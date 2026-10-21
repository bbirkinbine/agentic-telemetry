"""Command line: `agentic-telemetry run-table --out PATH DIR...` and
`agentic-telemetry session-table --out PATH [DIR...]`."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.dedupe import ConflictingRunError
from agentic_telemetry.run_table.discovery import NoLedgerError
from agentic_telemetry.run_table.parquet_writer import write_run_table
from agentic_telemetry.run_table.summary import render_summary
from agentic_telemetry.session_table.build import build_session_table
from agentic_telemetry.session_table.discovery import NoTranscriptError
from agentic_telemetry.session_table.parquet_writer import write_session_table
from agentic_telemetry.session_table.sources import default_sources
from agentic_telemetry.session_table.summary import render_source_line
from agentic_telemetry.session_table.summary import render_summary as render_session_summary

RUN_TABLE_DESCRIPTION = (
    "Build the run table: one row per delegate run, read from every delegate ledger "
    "(.agentic/delegate/ledger.jsonl, or an inbox copy with a results/ directory) under "
    "each DIR, joined to its review verdict, result.json, state.json and session "
    "transcripts. Inputs are read-only evidence; the only file written is --out, which "
    "is rebuilt from scratch on every run. Prints a summary of counts and warnings."
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
        "dirs",
        nargs="+",
        type=_input_dir,
        metavar="DIR",
        help="project root or collector inbox directory to search for ledgers",
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
        help="session directory, project root or inbox copy; omit to use the defaults",
    )
    return parser


def _run_table(dirs: Sequence[Path], out: Path) -> int:
    """Build and write the run table; return the process exit code."""
    try:
        rows, summary = build_run_table(dirs)
    except (NoLedgerError, ConflictingRunError) as error:
        print(f"agentic-telemetry run-table: error: {error}", file=sys.stderr)
        return 1
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return its exit code.

    Exit codes: 0 on success (warnings included), 1 when the inputs cannot be
    built into a table, 2 for usage errors.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    dirs: list[Path] = getattr(args, "dirs", [])
    out: Path | None = getattr(args, "out", None)
    if args.command == "run-table" and out is not None:
        return _run_table(dirs, out)
    if args.command == "session-table" and out is not None:
        return _session_table(dirs, out)
    parser.print_help(sys.stderr)
    return 2
