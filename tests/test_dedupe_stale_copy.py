"""A stale landing-zone copy: its reviews are a subset of the live ledger's (spec 0004).

When two inputs carry the same run with identical run rows and one input's review
multiset `(ts, verdict)` is contained in the other's, the superset's row wins in
the first-seen position and a warning names both ledger paths. Any other
difference still raises `ConflictingRunError`.
"""

from __future__ import annotations

import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from run_table_builders import LANDING_HOST, make_project, review_record, run_record, write_ledger

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.dedupe import ConflictingRunError, merge_runs
from agentic_telemetry.run_table.discovery import find_ledgers
from agentic_telemetry.run_table.join import assemble_ledger

RUN = "run-shared"
T1 = "2026-10-20T12:00:00+00:00"
T2 = "2026-10-21T09:30:00+00:00"


def _live_and_landing(
    tmp_path: Path,
    live: Sequence[Mapping[str, object]],
    landing: Sequence[Mapping[str, object]],
) -> tuple[Path, Path, Path, Path]:
    """Write a live project and a `.agentic`-layout landing copy with the given ledgers.

    Returns:
        The live input dir, its ledger, the landing input dir and its ledger.
    """
    work = tmp_path / "work"
    live_ledger = write_ledger(make_project(work, "local-game"), live)
    landing_root = tmp_path / "collector" / "landing" / LANDING_HOST
    shutil.copytree(work / "local-game" / ".agentic", landing_root / "local-game" / ".agentic")
    landing_ledger = landing_root / "local-game" / ".agentic" / "delegate" / "ledger.jsonl"
    write_ledger(landing_ledger.parent, landing)
    return work, live_ledger, landing_root, landing_ledger


def _stale_warnings(warnings: Sequence[str], first: Path, second: Path) -> list[str]:
    """Return the warnings that name the run and both ledger paths."""
    return [w for w in warnings if RUN in w and str(first) in w and str(second) in w]


@pytest.mark.parametrize("live_first", [True, False], ids=["live-first", "landing-first"])
def test_superset_of_reviews_wins_in_either_order_with_a_warning(
    tmp_path: Path, live_first: bool
) -> None:
    work, live_ledger, landing, landing_ledger = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1), review_record(RUN, "pass", T2)],
        landing=[run_record(RUN), review_record(RUN, "pass", T1)],
    )

    rows, summary = build_run_table([work, landing] if live_first else [landing, work])

    assert [row.run_id for row in rows] == [RUN]
    assert rows[0].review_count == 2
    assert rows[0].review_ts == T2
    assert Path(rows[0].ledger_path) == live_ledger
    assert len(_stale_warnings(summary.warnings, live_ledger, landing_ledger)) == 1, (
        summary.warnings
    )


def test_superset_row_takes_the_first_seen_position(tmp_path: Path) -> None:
    work, live_ledger, landing, _ = _live_and_landing(
        tmp_path,
        live=[run_record("run-early"), run_record(RUN), review_record(RUN, "model-defect", T1)],
        landing=[run_record("run-early"), run_record(RUN)],
    )

    rows, _ = build_run_table([landing, work])

    assert [row.run_id for row in rows] == ["run-early", RUN]
    assert rows[1].review_verdict == "model-defect"
    assert Path(rows[1].ledger_path) == live_ledger


def test_zero_reviews_against_one_review_is_a_subset(tmp_path: Path) -> None:
    work, live_ledger, landing, landing_ledger = _live_and_landing(
        tmp_path,
        live=[run_record(RUN)],
        landing=[run_record(RUN), review_record(RUN, "packet-defect", T1)],
    )

    rows, summary = build_run_table([work, landing])

    assert len(rows) == 1
    assert rows[0].review_count == 1
    assert rows[0].review_verdict == "packet-defect"
    assert Path(rows[0].ledger_path) == landing_ledger
    assert _stale_warnings(summary.warnings, live_ledger, landing_ledger), summary.warnings


def test_duplicate_review_is_a_multiset_subset(tmp_path: Path) -> None:
    work, live_ledger, landing, landing_ledger = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1), review_record(RUN, "pass", T1)],
        landing=[run_record(RUN), review_record(RUN, "pass", T1)],
    )

    rows, summary = build_run_table([landing, work])

    assert rows[0].review_count == 2
    assert Path(rows[0].ledger_path) == live_ledger
    assert _stale_warnings(summary.warnings, live_ledger, landing_ledger), summary.warnings


def test_set_subset_that_is_not_a_multiset_subset_raises(tmp_path: Path) -> None:
    work, _, landing, _ = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1), review_record(RUN, "pass", T2)],
        landing=[run_record(RUN), review_record(RUN, "pass", T1), review_record(RUN, "pass", T1)],
    )

    with pytest.raises(ConflictingRunError):
        build_run_table([work, landing])


def test_disagreeing_verdicts_raise_naming_reviews_and_both_ledgers(tmp_path: Path) -> None:
    work, live_ledger, landing, landing_ledger = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1)],
        landing=[run_record(RUN), review_record(RUN, "model-defect", T1)],
    )

    with pytest.raises(ConflictingRunError) as excinfo:
        build_run_table([work, landing])

    message = str(excinfo.value)
    assert str(live_ledger) in message
    assert str(landing_ledger) in message
    without_paths = message.replace(str(live_ledger), "").replace(str(landing_ledger), "")
    assert RUN in without_paths
    assert "reviews" in without_paths


def test_differing_run_field_raises_even_when_reviews_are_a_subset(tmp_path: Path) -> None:
    work, live_ledger, landing, landing_ledger = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1)],
        landing=[run_record(RUN, outcome="integrity")],
    )

    with pytest.raises(ConflictingRunError) as excinfo:
        build_run_table([work, landing])

    message = str(excinfo.value)
    assert str(live_ledger) in message
    assert str(landing_ledger) in message
    without_paths = message.replace(str(live_ledger), "").replace(str(landing_ledger), "")
    assert "outcome" in without_paths


def test_equal_review_multisets_keep_the_first_row_without_a_warning(tmp_path: Path) -> None:
    work, live_ledger, landing, landing_ledger = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1)],
        landing=[run_record(RUN), review_record(RUN, "pass", T1)],
    )

    rows, summary = build_run_table([landing, work])

    assert Path(rows[0].ledger_path) == landing_ledger
    assert _stale_warnings(summary.warnings, live_ledger, landing_ledger) == []


def test_ledger_result_carries_each_runs_review_multiset(tmp_path: Path) -> None:
    ledger = write_ledger(
        make_project(tmp_path / "work", "local-game"),
        [
            run_record(RUN),
            review_record(RUN, "pass", T1),
            review_record(RUN, "pass", T1),
            review_record(RUN, "model-defect", T2),
            run_record("run-unreviewed"),
        ],
    )
    (discovered,) = find_ledgers([ledger.parent])

    result = assemble_ledger(discovered)

    assert result.review_keys[RUN] == Counter({(T1, "pass"): 2, (T2, "model-defect"): 1})
    assert not result.review_keys.get("run-unreviewed")


def test_merge_runs_takes_review_multisets_by_ledger_and_returns_warnings(
    tmp_path: Path,
) -> None:
    work, live_ledger, landing, landing_ledger = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1), review_record(RUN, "pass", T2)],
        landing=[run_record(RUN), review_record(RUN, "pass", T1)],
    )
    rows = []
    reviews: dict[tuple[str, str], Counter[tuple[str | None, str | None]]] = {}
    for discovered in find_ledgers([landing, work]):
        assembled = assemble_ledger(discovered)
        rows.extend(assembled.rows)
        for run_id, multiset in assembled.review_keys.items():
            reviews[(str(discovered.path), run_id)] = multiset

    merged, warnings = merge_runs(rows, reviews)

    assert [row.run_id for row in merged] == [RUN]
    assert Path(merged[0].ledger_path) == live_ledger
    assert _stale_warnings(warnings, live_ledger, landing_ledger), warnings


def test_merge_runs_without_review_multisets_treats_review_differences_as_a_conflict(
    tmp_path: Path,
) -> None:
    """With no multisets nothing says which copy is stale, so 0001's conflict holds."""
    work, _, landing, _ = _live_and_landing(
        tmp_path,
        live=[run_record(RUN), review_record(RUN, "pass", T1)],
        landing=[run_record(RUN)],
    )
    rows = [row for d in find_ledgers([landing, work]) for row in assemble_ledger(d).rows]

    with pytest.raises(ConflictingRunError) as excinfo:
        merge_runs(rows)

    assert "reviews" in str(excinfo.value)
