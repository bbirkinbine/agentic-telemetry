"""Parse a delegate `ledger.jsonl` into run rows and review rows.

A line that cannot be used is skipped with a warning naming `file:line`, so one
bad line never hides the rest of the ledger. Row types other than `run` and
`review` are counted and otherwise ignored.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from agentic_telemetry.run_table.json_values import JsonObject, as_object, opt_str


@dataclass(frozen=True)
class ReviewRecord:
    """One `type: "review"` ledger row."""

    run_id: str
    ts: str | None
    verdict: str | None
    line_no: int


@dataclass
class LedgerContents:
    """Everything usable in one ledger, with the problems found on the way."""

    runs: list[tuple[int, JsonObject]] = field(default_factory=list)
    reviews: list[ReviewRecord] = field(default_factory=list)
    unknown_type_counts: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)


def parse_ledger(path: Path) -> LedgerContents:
    """Read a ledger file line by line.

    Args:
        path: The `ledger.jsonl` file.

    Returns:
        Run rows with their 1-based line numbers, review rows, counts of unknown
        row types, and one warning per skipped line.

    Raises:
        OSError: The file cannot be read.
    """
    contents = LedgerContents()
    with path.open("rb") as handle:
        for line_no, raw in enumerate(handle, start=1):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                contents.warnings.append(f"{path}:{line_no}: skipped a line that is not UTF-8")
                continue
            if line.strip():
                _parse_line(path, line_no, line, contents)
    return contents


def _parse_line(path: Path, line_no: int, line: str, contents: LedgerContents) -> None:
    """Add one ledger line to `contents`, or a warning when it is unusable."""
    where = f"{path}:{line_no}"
    try:
        decoded: object = json.loads(line)
    except json.JSONDecodeError as error:
        contents.warnings.append(f"{where}: skipped malformed JSON ({error.msg})")
        return
    record = as_object(decoded)
    if record is None:
        contents.warnings.append(f"{where}: skipped a line that is not a JSON object")
        return
    row_type = opt_str(record.get("type"))
    if row_type is None:
        contents.warnings.append(f"{where}: skipped a line with no 'type' field")
        return
    if row_type not in ("run", "review"):
        contents.unknown_type_counts[row_type] += 1
        return
    run_id = opt_str(record.get("run"))
    if run_id is None:
        contents.warnings.append(f"{where}: skipped a '{row_type}' row with no 'run' field")
        return
    if row_type == "run":
        contents.runs.append((line_no, record))
        return
    contents.reviews.append(
        ReviewRecord(
            run_id=run_id,
            ts=opt_str(record.get("ts")),
            verdict=opt_str(record.get("verdict")),
            line_no=line_no,
        )
    )
