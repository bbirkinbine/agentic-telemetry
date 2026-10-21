"""Ledger parsing: run and review rows, and the lines that are skipped with a warning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from run_table_builders import review_record, run_record, write_ledger

from agentic_telemetry.run_table.ledger import parse_ledger


def test_run_rows_are_returned_with_their_line_numbers(tmp_path: Path) -> None:
    first = run_record("run-one")
    second = run_record("run-two", outcome="needs_frontier")
    ledger = write_ledger(tmp_path / "delegate", [first, review_record("run-one"), second])

    contents = parse_ledger(ledger)

    assert list(contents.runs) == [(1, first), (3, second)]
    assert list(contents.warnings) == []


def test_review_rows_carry_run_ts_and_verdict(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "delegate",
        [
            run_record("run-one"),
            review_record("run-one", "model-defect", ts="2026-10-20T13:00:00+00:00"),
        ],
    )

    contents = parse_ledger(ledger)

    assert len(contents.reviews) == 1
    review = contents.reviews[0]
    assert review.run_id == "run-one"
    assert review.verdict == "model-defect"
    assert review.ts == "2026-10-20T13:00:00+00:00"
    assert review.line_no == 2


@pytest.mark.parametrize(
    "bad_line",
    [
        pytest.param("{not json at all", id="malformed-json"),
        pytest.param(json.dumps(["a", "list", "not", "an", "object"]), id="non-object"),
        pytest.param(json.dumps({"ts": "2026-10-20T10:00:00+00:00", "run": "r"}), id="no-type"),
        pytest.param(json.dumps({"type": "run", "outcome": "green"}), id="run-without-run"),
        pytest.param(json.dumps({"type": "review", "verdict": "pass"}), id="review-without-run"),
    ],
)
def test_bad_line_is_skipped_with_a_warning_naming_file_and_line(
    tmp_path: Path, bad_line: str
) -> None:
    good_after = run_record("run-after")
    ledger = write_ledger(tmp_path / "delegate", [run_record("run-before"), bad_line, good_after])

    contents = parse_ledger(ledger)

    assert [run_id for _, run_id in _run_ids(contents.runs)] == ["run-before", "run-after"]
    assert len(contents.warnings) == 1
    assert f"{ledger}:2" in contents.warnings[0]


def test_each_bad_line_gets_its_own_warning(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "delegate",
        ["{broken", run_record("run-ok"), json.dumps({"run": "no-type-here"})],
    )

    contents = parse_ledger(ledger)

    assert len(contents.warnings) == 2
    assert f"{ledger}:1" in contents.warnings[0]
    assert f"{ledger}:3" in contents.warnings[1]


def test_unknown_type_is_counted_without_a_warning(tmp_path: Path) -> None:
    ledger = write_ledger(
        tmp_path / "delegate",
        [
            {"type": "heartbeat", "run": "run-one", "ts": "2026-10-20T10:00:00+00:00"},
            run_record("run-one"),
            {"type": "heartbeat", "run": "run-one", "ts": "2026-10-20T10:05:00+00:00"},
            {"type": "annotation", "run": "run-one"},
        ],
    )

    contents = parse_ledger(ledger)

    assert dict(contents.unknown_type_counts) == {"heartbeat": 2, "annotation": 1}
    assert list(contents.warnings) == []
    assert len(contents.runs) == 1
    assert list(contents.reviews) == []


def _run_ids(runs: list[tuple[int, dict[str, object]]]) -> list[tuple[int, object]]:
    return [(line_no, record["run"]) for line_no, record in runs]


def test_line_that_is_not_utf8_is_skipped_with_a_warning(tmp_path: Path) -> None:
    ledger = write_ledger(tmp_path / "delegate", [run_record("run-before")])
    with ledger.open("ab") as handle:
        handle.write(b'{"type": "run", "run": "cut-\xe2\x82\n')
        handle.write((json.dumps(run_record("run-after")) + "\n").encode())

    contents = parse_ledger(ledger)

    assert [record["run"] for _, record in contents.runs] == ["run-before", "run-after"]
    assert len(contents.warnings) == 1
    assert f"{ledger}:2" in contents.warnings[0]
