"""Command line: `agentic-telemetry run-table --out PATH DIR...`."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.dedupe import ConflictingRunError
from agentic_telemetry.run_table.discovery import NoLedgerError
from agentic_telemetry.run_table.parquet_writer import write_run_table
from agentic_telemetry.run_table.summary import render_summary

RUN_TABLE_DESCRIPTION = (
    "Build the run table: one row per delegate run, read from every delegate ledger "
    "(.agentic/delegate/ledger.jsonl, or an inbox copy with a results/ directory) under "
    "each DIR, joined to its review verdict, result.json, state.json and session "
    "transcripts. Inputs are read-only evidence; the only file written is --out, which "
    "is rebuilt from scratch on every run. Prints a summary of counts and warnings."
)


def _input_dir(value: str) -> Path:
    """Argparse type: an existing directory to search for ledgers."""
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return its exit code.

    Exit codes: 0 on success (warnings included), 1 when the inputs cannot be
    built into a table, 2 for usage errors.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "run-table":
        parser.print_help(sys.stderr)
        return 2
    dirs: list[Path] = args.dirs
    out: Path = args.out
    return _run_table(dirs, out)
