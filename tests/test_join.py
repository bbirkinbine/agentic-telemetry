"""Per-ledger assembly: review join, column flattening and derived columns."""

from __future__ import annotations

import json
from pathlib import Path

from run_table_builders import (
    PLACEHOLDER_ENDPOINT,
    assistant_message,
    make_project,
    model_record,
    review_record,
    run_record,
    session_header,
    write_ledger,
    write_result_dir,
    write_session,
    write_state,
)

from agentic_telemetry.run_table.discovery import find_ledgers
from agentic_telemetry.run_table.join import assemble_ledger


def _assemble(root: Path):
    found = find_ledgers([root])
    assert len(found) == 1
    return assemble_ledger(found[0])


def _only_row(root: Path):
    result = _assemble(root)
    assert len(result.rows) == 1
    return result.rows[0]


def test_latest_review_by_timestamp_wins_and_all_reviews_are_counted(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(
        delegate,
        [
            run_record("run-a"),
            review_record("run-a", "model-defect", ts="2026-10-20T10:00:00+00:00"),
            # 08:00-05:00 is 13:00 UTC: later in time, though it sorts earlier as text.
            review_record("run-a", "pass", ts="2026-10-20T08:00:00-05:00"),
            review_record("run-a", "packet-defect", ts="2026-10-20T11:00:00+00:00"),
        ],
    )

    row = _only_row(tmp_path)

    assert row.review_verdict == "pass"
    assert row.review_ts == "2026-10-20T08:00:00-05:00"
    assert row.review_count == 3


def test_later_line_breaks_a_review_timestamp_tie(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    same_ts = "2026-10-20T12:00:00+00:00"
    write_ledger(
        delegate,
        [
            run_record("run-a"),
            review_record("run-a", "pass", ts=same_ts),
            review_record("run-a", "packet-defect", ts=same_ts),
        ],
    )

    row = _only_row(tmp_path)

    assert row.review_verdict == "packet-defect"
    assert row.review_count == 2


def test_run_without_review_has_null_verdict_and_zero_count(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "proj"), [run_record("run-a")])

    row = _only_row(tmp_path)

    assert row.review_verdict is None
    assert row.review_ts is None
    assert row.review_count == 0


def test_reviews_joined_counts_runs_that_received_a_review(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [
            run_record("run-a"),
            run_record("run-b"),
            run_record("run-c"),
            review_record("run-a", "pass"),
            review_record("run-c", "model-defect"),
        ],
    )

    result = _assemble(tmp_path)

    assert result.reviews_joined == 2


def test_review_for_unknown_run_is_a_warning_and_not_a_row(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [run_record("run-a"), review_record("run-ghost", "pass")],
    )

    result = _assemble(tmp_path)

    assert [row.run_id for row in result.rows] == ["run-a"]
    assert any("run-ghost" in warning for warning in result.warnings)


def test_identity_and_served_configuration_columns_come_from_the_run_row(tmp_path: Path) -> None:
    ledger = write_ledger(make_project(tmp_path, "local-game"), [run_record("run-a")])

    row = _only_row(tmp_path)

    assert row.run_id == "run-a"
    assert row.project == "local-game"
    assert Path(row.ledger_path).resolve() == ledger.resolve()
    assert row.ts == "2026-10-20T10:00:00+00:00"
    assert row.harness == "delegate-run-v1"
    assert row.mode == "per-file"
    assert row.bucket == "small"
    assert row.target == "src/example_module.py"
    assert row.packet == "packets/example-packet.md"
    assert row.packet_sha256 == "a" * 64
    assert row.snapshot_sha256 == "b" * 64
    assert row.fingerprint == "fp-0a1b2c3d4e5f"
    assert row.qualified is True
    assert row.model_name == "example-coder"
    assert row.model_id == "example-coder-7b-instruct"
    assert row.quant == "q4_k_m"
    assert row.runtime == "example-runtime"
    assert row.endpoint == PLACEHOLDER_ENDPOINT
    assert row.outcome == "green"
    assert row.reason == "reason-text-not-for-stdout"
    assert row.gate == "pass"
    assert list(row.unverified_steps) == ["manual smoke check"]


def test_request_params_keeps_a_recorded_json_string_verbatim(tmp_path: Path) -> None:
    recorded = '{"temperature":0.2, "top_p": 0.95}'
    write_ledger(
        make_project(tmp_path, "proj"),
        [run_record("run-a", model=model_record(request=recorded))],
    )

    assert _only_row(tmp_path).request_params == recorded


def test_request_params_object_becomes_sorted_key_json_text(tmp_path: Path) -> None:
    request = {"top_p": 0.95, "temperature": 0.2, "max_tokens": 4096}
    write_ledger(
        make_project(tmp_path, "proj"),
        [run_record("run-a", model=model_record(request=request))],
    )

    assert _only_row(tmp_path).request_params == json.dumps(request, sort_keys=True)


def test_attempts_become_count_outcome_list_and_summed_seconds(tmp_path: Path) -> None:
    attempts = [
        {"attempt": 1, "outcome": "gate-fail", "detail": "red", "seconds": 30},
        {"attempt": 2, "outcome": "integrity", "detail": "edited tests", "seconds": 45},
        {"attempt": 3, "outcome": "green", "detail": "ok", "seconds": 12},
    ]
    write_ledger(make_project(tmp_path, "proj"), [run_record("run-a", attempts=attempts)])

    row = _only_row(tmp_path)

    assert row.attempts == 3
    assert list(row.attempt_outcomes) == ["gate-fail", "integrity", "green"]
    assert row.agent_seconds == 87


def test_no_attempts_gives_zero_count_and_null_agent_seconds(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "proj"), [run_record("run-a", attempts=[])])

    row = _only_row(tmp_path)

    assert row.attempts == 0
    assert list(row.attempt_outcomes) == []
    assert row.agent_seconds is None


def test_wall_seconds_is_null_when_the_key_is_absent(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "proj"), [run_record("run-early", drop=("wall_seconds",))])

    assert _only_row(tmp_path).wall_seconds is None


def test_wall_seconds_is_kept_when_recorded(tmp_path: Path) -> None:
    write_ledger(make_project(tmp_path, "proj"), [run_record("run-a", wall_seconds=611)])

    assert _only_row(tmp_path).wall_seconds == 611


def test_handoff_run_gets_session_tokens_and_state_counters(tmp_path: Path) -> None:
    delegate = make_project(tmp_path, "proj")
    write_ledger(
        delegate,
        [run_record("run-h", harness="delegate-run-v2", bucket="phase:implement")],
    )
    run_dir = write_result_dir(delegate, "run-h")
    write_state(run_dir, phase="implement", segments=[(1, 2), (1, 0)])
    write_session(
        run_dir,
        [
            session_header(),
            assistant_message(tokens_in=500, tokens_out=50, cache_read=25, reasoning=5),
            assistant_message(tokens_in=700, tokens_out=70, cache_read=35, reasoning=6),
        ],
    )

    row = _only_row(tmp_path)

    assert row.tokens_in == 1200
    assert row.tokens_out == 120
    assert row.tokens_cache_read == 60
    assert row.tokens_reasoning == 11
    assert row.assistant_turns == 2
    assert row.compactions == 2
    assert row.retries == 2


def test_malformed_ledger_line_warning_reaches_the_ledger_result(tmp_path: Path) -> None:
    ledger = write_ledger(make_project(tmp_path, "proj"), [run_record("run-a"), "{oops"])

    result = _assemble(tmp_path)

    assert any(f"{ledger}:2" in warning for warning in result.warnings)


def test_unparsable_review_timestamp_warns_and_sorts_first(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [
            run_record("run-a"),
            review_record("run-a", "pass", ts="2026-10-20T09:00:00Z"),
            review_record("run-a", "model-defect", ts="yesterday afternoon"),
        ],
    )

    result = _assemble(tmp_path)

    (row,) = result.rows
    assert row.review_verdict == "pass"
    assert any("run-a" in w and "yesterday afternoon" in w for w in result.warnings)


def test_zulu_naive_and_offset_review_timestamps_compare_as_utc(tmp_path: Path) -> None:
    write_ledger(
        make_project(tmp_path, "proj"),
        [
            run_record("run-a"),
            review_record("run-a", "packet-defect", ts="2026-10-20T14:00:00"),
            review_record("run-a", "pass", ts="2026-10-20T15:00:00Z"),
            # 09:30-05:00 is 14:30 UTC: later than the naive 14:00, earlier than 15:00Z.
            review_record("run-a", "model-defect", ts="2026-10-20T09:30:00-05:00"),
        ],
    )

    row = _only_row(tmp_path)

    assert row.review_verdict == "pass"
    assert row.review_count == 3
