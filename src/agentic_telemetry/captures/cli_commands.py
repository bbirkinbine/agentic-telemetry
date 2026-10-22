"""The `captures-pull` and `captures show` subcommands (spec 0008)."""

import argparse
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from agentic_telemetry.captures.capture_file import read_capture_tree
from agentic_telemetry.captures.cursor import CursorFileError, load_cursor
from agentic_telemetry.captures.harvest import CURSOR_FILE, Failures, PollReport, poll_once
from agentic_telemetry.captures.join import ClaimedCall, assign_calls, session_parents
from agentic_telemetry.captures.llama_swap_api import (
    ApiFetch,
    LlamaSwapApi,
    LlamaSwapQueryError,
    LlamaSwapUnreachableError,
)
from agentic_telemetry.captures.service import report_line, run_forever
from agentic_telemetry.captures.session_windows import (
    SessionTableError,
    SessionWindow,
    read_session_windows,
)
from agentic_telemetry.captures.show import call_detail, date_view, ended, session_view

COMMANDS = frozenset({"captures-pull", "captures"})
API_KEY_ENV = "LLAMA_SWAP_API_KEY"
# Spec 0008: llama-swap is polled every 15 s, well inside its capture cache's reach.
DEFAULT_INTERVAL_SECONDS = 15.0

PULL_DESCRIPTION = (
    "Copy every call llama-swap records, with its request/response capture, into "
    "OUT/LABEL/<YYYY-MM-DD>.jsonl.zst before llama-swap's in-memory capture cache evicts "
    "it. Reads llama-swap's API (GET /api/metrics/activity and /api/captures/{id}) "
    "read-only; never touches /upstream/, so no model's TTL is reset. The API key, when "
    f"llama-swap has apiKeys set, comes only from ${API_KEY_ENV}. Runs every --interval "
    "seconds until stopped, or once with --once. Logs counts only."
)
SHOW_DESCRIPTION = (
    "List captured llama-swap calls for one session (--session ID), one delegate run "
    "(RUN_ID, its root session) or one UTC day (--date), joined to the session table by "
    "session header, else by time window and model; --call N prints that call's request "
    "and reassembled response in full. Reads --captures (a copy of the harvester's "
    "captures/ tree) and --session-table (from `agentic-telemetry session-table`)."
)
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _llama_swap_url(value: str) -> str:
    """Argparse type: an http(s) llama-swap base URL; same rules as --metrics-url."""
    parts = urlsplit(value)
    # Credentials and queries are refused without echoing the value: either may hold a secret.
    if parts.username or parts.password:
        raise argparse.ArgumentTypeError("--url must not carry credentials")
    if parts.query or parts.fragment:
        raise argparse.ArgumentTypeError("--url must carry no query or fragment")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise argparse.ArgumentTypeError(f"--url must be http(s)://HOST[:PORT], got: {value}")
    try:
        _ = parts.port  # reading the port is the check: a non-numeric one raises
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--url has an unusable port: {value}") from error
    return value.rstrip("/")


def _label(value: str) -> str:
    """Argparse type: a host label usable as one directory name."""
    if not _LABEL.match(value):
        raise argparse.ArgumentTypeError(f"--host-label must match {_LABEL.pattern}: {value}")
    return value


def _existing_dir(value: str) -> Path:
    """Argparse type: an existing directory."""
    path = Path(value)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"not an existing directory: {value}")
    return path


def _interval(value: str) -> float:
    """Argparse type: a positive number of seconds."""
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--interval must be a number: {value}") from error
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"--interval must be positive: {value}")
    return seconds


def _day(value: str) -> str:
    """Argparse type: a YYYY-MM-DD date."""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--date must be YYYY-MM-DD: {value}") from error


def add_capture_parsers(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register `captures-pull` and `captures show` on the top-level subparsers."""
    pull = commands.add_parser(
        "captures-pull",
        help="harvest llama-swap captures into daily files",
        description=PULL_DESCRIPTION,
    )
    pull.add_argument("--url", required=True, type=_llama_swap_url, help="llama-swap base URL")
    pull.add_argument(
        "--host-label", required=True, type=_label, help="directory name for the host"
    )
    pull.add_argument("--out", required=True, type=_existing_dir, help="captures root directory")
    pull.add_argument(
        "--interval", type=_interval, default=DEFAULT_INTERVAL_SECONDS, help="seconds between polls"
    )
    pull.add_argument("--once", action="store_true", help="poll once and exit")
    captures = commands.add_parser("captures", help="read harvested llama-swap captures")
    actions = captures.add_subparsers(dest="captures_command", metavar="ACTION")
    show = actions.add_parser(
        "show", help="list or print captured calls", description=SHOW_DESCRIPTION
    )
    show.add_argument("--captures", required=True, metavar="DIR", help="captures root")
    show.add_argument("--session-table", required=True, metavar="PATH", help="session Parquet")
    selector = show.add_mutually_exclusive_group(required=True)
    selector.add_argument("--session", metavar="ID", help="a session-table session_id")
    selector.add_argument("--date", type=_day, help="a UTC day, YYYY-MM-DD")
    selector.add_argument("run_id", nargs="?", metavar="RUN_ID", help="a delegate run id")
    show.add_argument("--call", type=int, metavar="N", help="print call N (from 1) in full")


def _pull(args: argparse.Namespace, fetch: ApiFetch) -> int:
    """Run the harvester once or forever; return the exit code."""
    api = LlamaSwapApi(args.url, fetch, api_key=os.environ.get(API_KEY_ENV) or None)
    host_dir: Path = args.out / args.host_label

    failures: Failures = {}  # consecutive failures by call id, kept across polls

    def poll() -> PollReport:
        return poll_once(api, load_cursor(host_dir / CURSOR_FILE), host_dir, failures)

    try:
        if args.once:
            print(report_line(poll()))
            return 0
        print(f"captures-pull: polling every {args.interval:g} s into {host_dir}", flush=True)
        run_forever(
            poll=poll,
            interval=args.interval,
            sleep=time.sleep,
            log=lambda line: print(line, flush=True),
        )
    except (LlamaSwapUnreachableError, LlamaSwapQueryError, CursorFileError) as error:
        print(f"agentic-telemetry captures-pull: error: {error}", file=sys.stderr)
        return 1
    return 0


def _family(session_id: str, windows: list[SessionWindow]) -> dict[str, str]:
    """Map every descendant of `session_id` to its child directly below `session_id`."""
    children: dict[str, str | None] = dict.fromkeys(w.session_id for w in windows)
    children.update(session_parents(windows))
    family: dict[str, str] = {}
    for candidate in children:
        chain, parent = [candidate], children.get(candidate)
        while parent is not None and parent != session_id and parent not in chain:
            chain.append(parent)
            parent = children.get(parent)
        if parent == session_id:
            family[candidate] = chain[-1]
    return family


def _select_session(args: argparse.Namespace, windows: list[SessionWindow]) -> SessionWindow:
    """Return the session named by --session or RUN_ID, or raise naming the input."""
    if args.session is not None:
        for window in windows:
            if window.session_id == args.session:
                return window
        raise LookupError(f"no session {args.session} in the session table")
    roots = [w for w in windows if w.run_id == args.run_id and not w.is_subagent]
    if len(roots) != 1:
        found = ", ".join(sorted(w.session_id for w in roots)) or "none"
        raise LookupError(f"run {args.run_id} needs exactly one root session, found: {found}")
    return roots[0]


def _session_calls(
    window: SessionWindow, windows: list[SessionWindow], calls: list[ClaimedCall]
) -> tuple[list[ClaimedCall], str]:
    """Return a session's own calls and its view, with one count per child session."""
    own = [call for call in calls if window.session_id in call.session_ids]
    family = _family(window.session_id, windows)
    children = dict.fromkeys(set(family.values()), 0)
    for call in calls:
        for owner in call.session_ids:
            if owner in family:
                children[family[owner]] += 1
    return own, session_view(window, own, children)


def _days_around(first: date, last: date) -> set[str]:
    """Return the `YYYY-MM-DD` days from the day before `first` to the day after `last`."""
    day, days = first - timedelta(days=1), set()
    while day <= last + timedelta(days=1):
        days.add(day.isoformat())
        day += timedelta(days=1)
    return days


def _show(args: argparse.Namespace) -> int:
    """Join captures to sessions and print the selected view; return the exit code.

    Only the capture files of the days around the selection are read (the
    `--date` day, or the session's span, each widened by a day), so a query's
    cost does not grow with months of retained captures.
    """
    root = Path(args.captures)
    try:
        if not root.is_dir():
            raise LookupError(f"--captures is not a directory: {root}")
        windows = read_session_windows(Path(args.session_table))
        window = None if args.date is not None else _select_session(args, windows)
        days: set[str] | None = None
        if args.date is not None:
            days = _days_around(date.fromisoformat(args.date), date.fromisoformat(args.date))
        elif window is not None and window.start is not None and window.end is not None:
            days = _days_around(window.start.date(), window.end.date())
        warnings: list[str] = []
        records = read_capture_tree(root, warnings, days)
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        calls = sorted(assign_calls(records, windows), key=ended)
        if window is None:
            chosen = [call for call in calls if ended(call).date().isoformat() == args.date]
            view = date_view(args.date, chosen)
        else:
            chosen, view = _session_calls(window, windows, calls)
        if args.call is not None:
            if not 1 <= args.call <= len(chosen):
                raise LookupError(f"--call {args.call}: the selection has {len(chosen)} calls")
            view = call_detail(args.call, chosen[args.call - 1])
    except (LookupError, SessionTableError, ValueError, OSError) as error:
        print(f"agentic-telemetry captures show: error: {error}", file=sys.stderr)
        return 1
    print(view)
    return 0


def run_capture_command(args: argparse.Namespace, fetch: ApiFetch) -> int | None:
    """Run a capture subcommand; return its exit code, or None when it needs help."""
    if args.command == "captures-pull":
        return _pull(args, fetch)
    if getattr(args, "captures_command", None) == "show":
        return _show(args)
    return None
