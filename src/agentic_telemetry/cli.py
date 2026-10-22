"""Command line: `agentic-telemetry run-table --out PATH DIR... [--metrics-url URL]` and
`agentic-telemetry session-table --out PATH [DIR...]`."""

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.dedupe import ConflictingRunError
from agentic_telemetry.run_table.discovery import NoLedgerError
from agentic_telemetry.run_table.parquet_writer import write_run_table
from agentic_telemetry.run_table.summary import render_summary
from agentic_telemetry.server_metrics.http_fetch import (
    Fetch,
    MetricsUnreachableError,
    urlopen_fetch,
)
from agentic_telemetry.server_metrics.join import join_server_metrics
from agentic_telemetry.session_table.build import build_session_table
from agentic_telemetry.session_table.discovery import NoTranscriptError
from agentic_telemetry.session_table.parquet_writer import write_session_table
from agentic_telemetry.session_table.sources import default_sources
from agentic_telemetry.session_table.summary import render_source_line
from agentic_telemetry.session_table.summary import render_summary as render_session_summary

RUN_TABLE_DESCRIPTION = (
    "Build the run table: one row per delegate run, read from every delegate ledger "
    "(.agentic/delegate/ledger.jsonl, or a landing-zone copy with a results/ directory) under "
    "each DIR, joined to its review verdict, result.json, state.json and session "
    "transcripts. Inputs are read-only evidence; the only file written is --out, which "
    "is rebuilt from scratch on every run. With --metrics-url, each run is also joined to "
    "the inference host's per-model server metrics, read read-only from that VictoriaMetrics "
    "base URL; without it the server_ columns stay null and no request is made. Prints a "
    "summary of counts and warnings."
)

SESSION_TABLE_DESCRIPTION = (
    "Build the session table: one row per Pi, Claude Code or Codex session, read from "
    "the clients' own transcripts (*.jsonl, and Codex's compressed *.jsonl.zst) under "
    "each DIR. With no DIR, reads each client's default session directory that exists: "
    "Pi at $PI_CODING_AGENT_SESSION_DIR, else $PI_CODING_AGENT_DIR/sessions, else "
    "~/.pi/agent/sessions; Claude Code at $CLAUDE_CONFIG_DIR/projects, else "
    "~/.claude/projects; Codex at $CODEX_HOME/sessions, else ~/.codex/sessions. "
    "Inputs are read-only; the only file written is --out, rebuilt from scratch on "
    "every run. Prints counts and warnings, never transcript content."
)


def _input_dir(value: str) -> Path:
    """Argparse type: an existing directory to search for ledgers or session transcripts."""
    path = Path(value)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"input directory does not exist: {value}")
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"input is not a directory: {value}")
    return path


def _parquet_out(value: str) -> Path:
    """Argparse type: a `.parquet` file path whose parent directory exists."""
    path = Path(value)
    if path.suffix != ".parquet":
        raise argparse.ArgumentTypeError(f"--out must end in .parquet, got: {value}")
    if path.is_dir():
        raise argparse.ArgumentTypeError(f"--out names an existing directory: {value}")
    if not path.parent.is_dir():
        raise argparse.ArgumentTypeError(
            f"--out parent directory does not exist: {path.parent} (create it with mkdir -p)"
        )
    return path


def _metrics_url(value: str) -> str:
    """Argparse type: a VictoriaMetrics base URL, with any trailing slash removed.

    Args:
        value: The `--metrics-url` argument.

    Returns:
        The URL, ready to have an API path appended.

    Raises:
        argparse.ArgumentTypeError: The value carries credentials, is not an
            http(s) URL, names no host, has a port that is not a number, or
            carries a query or fragment.
            A query would be dropped by the appended parameters anyway.
    """
    parts = urlsplit(value)
    # Credentials first, and never echoed: every later message quotes the value.
    if parts.username or parts.password:
        raise argparse.ArgumentTypeError("--metrics-url must not carry credentials")
    if parts.scheme not in ("http", "https"):
        raise argparse.ArgumentTypeError(
            f"--metrics-url must start with http:// or https://, got: {value}"
        )
    if not parts.hostname:
        raise argparse.ArgumentTypeError(f"--metrics-url names no host: {value}")
    try:
        _ = parts.port  # reading the port is the check: a non-numeric one raises
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--metrics-url has an unusable port: {value}") from error
    if parts.query or parts.fragment:
        # Not echoed: a query string may carry a token.
        raise argparse.ArgumentTypeError("--metrics-url must carry no query or fragment")
    return value.rstrip("/")


def _parser() -> argparse.ArgumentParser:
    """Return the top-level parser with its subcommands."""
    parser = argparse.ArgumentParser(
        prog="agentic-telemetry",
        description="Read coding-agent telemetry evidence into analysis tables.",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    run_table = commands.add_parser(
        "run-table",
        help="build the run table from delegate ledgers",
        description=RUN_TABLE_DESCRIPTION,
    )
    run_table.add_argument(
        "--out",
        required=True,
        type=_parquet_out,
        metavar="PATH",
        help="Parquet file to write, e.g. data/run_table.parquet (parent must exist)",
    )
    run_table.add_argument(
        "--metrics-url",
        type=_metrics_url,
        metavar="URL",
        help="VictoriaMetrics base URL to read the inference host's per-model metrics from "
        "(read-only), e.g. http://127.0.0.1:8428; omit it to leave the server_ columns null",
    )
    run_table.add_argument(
        "dirs",
        nargs="+",
        type=_input_dir,
        metavar="DIR",
        help="project root or collector landing-zone directory to search for ledgers",
    )
    session_table = commands.add_parser(
        "session-table",
        help="build the session table from Pi, Claude Code and Codex transcripts",
        description=SESSION_TABLE_DESCRIPTION,
    )
    session_table.add_argument(
        "--out",
        required=True,
        type=_parquet_out,
        metavar="PATH",
        help="Parquet file to write, e.g. data/session_table.parquet (parent must exist)",
    )
    session_table.add_argument(
        "dirs",
        nargs="*",
        type=_input_dir,
        metavar="DIR",
        help="session directory, project root or landing-zone copy; omit to use the defaults",
    )
    return parser


def _run_table(dirs: Sequence[Path], out: Path, metrics_url: str | None, fetch: Fetch) -> int:
    """Build the run table, join the server metrics when asked, and write it.

    Args:
        dirs: The evidence directories.
        out: The Parquet file to write.
        metrics_url: The VictoriaMetrics base URL, or None to leave the server
            columns null and make no request.
        fetch: The HTTP seam.

    Returns:
        The process exit code: 0 on success, 1 when the evidence cannot be built
        or the metrics URL cannot be read. Nothing is written on a failure.
    """
    try:
        rows, summary = build_run_table(dirs)
    except (NoLedgerError, ConflictingRunError) as error:
        print(f"agentic-telemetry run-table: error: {error}", file=sys.stderr)
        return 1
    if metrics_url is not None:
        try:
            rows, report = join_server_metrics(rows, metrics_url, fetch, datetime.now(UTC))
        except MetricsUnreachableError as error:
            print(
                f"agentic-telemetry run-table: error: --metrics-url {metrics_url} "
                f"could not be read: {error}",
                file=sys.stderr,
            )
            return 1
        summary.warnings.extend(report.warnings)
        summary.server_queried = True
        summary.server_status_counts = report.status_counts
        summary.server_source_counts = report.source_counts
    write_run_table(rows, out)
    print(render_summary(summary))
    print(f"wrote {len(rows)} rows to {out}")
    return 0


def _session_table(dirs: Sequence[Path], out: Path) -> int:
    """Build and write the session table; return the process exit code."""
    home = Path.home()
    source_lines: list[str] = []
    require_each = bool(dirs)
    if not dirs:
        sources = default_sources(os.environ, home)
        source_lines = [render_source_line(source, home) for source in sources]
        dirs = [source.path for source in sources if source.found]
        if not dirs:
            print("\n".join(source_lines))
            print(
                "agentic-telemetry session-table: error: no default session directory "
                "exists for pi, claude-code or codex; pass DIR explicitly",
                file=sys.stderr,
            )
            return 1
    try:
        rows, summary = build_session_table(dirs, require_each=require_each)
    except NoTranscriptError as error:
        for warning in dict.fromkeys(error.warnings):
            print(f"warning: {warning}", file=sys.stderr)
        print(f"agentic-telemetry session-table: error: {error}", file=sys.stderr)
        return 1
    if not rows:
        # Never replace an earlier table with an empty one.
        where = "the given directories" if require_each else "the default session directories"
        print("\n".join([*source_lines, render_session_summary(summary)]))
        print(
            f"agentic-telemetry session-table: error: no session row could be built from "
            f"{where}; nothing written",
            file=sys.stderr,
        )
        return 1
    write_session_table(rows, out)
    print("\n".join([*source_lines, render_session_summary(summary)]))
    print(f"wrote {len(rows)} rows to {out}")
    return 0


def main(argv: Sequence[str] | None = None, fetch: Fetch | None = None) -> int:
    """Run the command line and return its exit code.

    Exit codes: 0 on success (warnings included), 1 when the inputs cannot be
    built into a table, 2 for usage errors.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.
        fetch: The HTTP seam for `--metrics-url`; defaults to the stdlib GET.
            Tests pass a stub, so no test needs a listening port.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    fetcher: Fetch = fetch if fetch is not None else urlopen_fetch
    dirs: list[Path] = getattr(args, "dirs", [])
    out: Path | None = getattr(args, "out", None)
    metrics_url: str | None = getattr(args, "metrics_url", None)
    if args.command == "run-table" and out is not None:
        return _run_table(dirs, out, metrics_url, fetcher)
    if args.command == "session-table" and out is not None:
        return _session_table(dirs, out)
    parser.print_help(sys.stderr)
    return 2
