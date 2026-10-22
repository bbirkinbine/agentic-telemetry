"""Run-table rows and the server-metrics join with a subagent's usage counted (spec 0009)."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path
from typing import Literal

from run_table_builders import (
    assistant_message,
    make_project,
    model_record,
    run_record,
    session_header,
    write_ledger,
    write_result_dir,
    write_session,
)
from server_join_helpers import (
    ALIAS_QWEN,
    BASE_URL,
    FLASH,
    KNOWN,
    NOW,
    PROJECT_JOINED_WINDOW,
    QWEN,
    model_readings,
)
from subagent_builders import PROVIDER, subagent_tool_result, write_nested_subagent_copies
from vm_stub import FakeVictoria

from agentic_telemetry.run_table.build import build_run_table
from agentic_telemetry.run_table.row import RunRow
from agentic_telemetry.server_metrics.join import join_server_metrics

PARENT_SESSION = "2026-10-20T10-00-00-000Z_parent-0001"
# Parent turns 600+400 in and 120+80 out; the subagent 500 in and 150 out.
SUMMED_IN = 1500
SUMMED_OUT = 350


def parent_turn(
    *, tokens_in: int, tokens_out: int, message_id: str, cache_read: int = 0
) -> dict[str, object]:
    """Return a parent assistant turn on the Qwen alias through provider `gpu-example`."""
    return assistant_message(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read=cache_read,
        message_id=message_id,
        model=ALIAS_QWEN,
        response_model=QWEN,
        provider=PROVIDER,
    )


def write_subagent_project(
    root: Path,
    layout: Literal["agentic", "landing"] = "agentic",
    *,
    nested_copies: bool = True,
    child_model: str = ALIAS_QWEN,
) -> None:
    """Write one project with a subagent run and a run with no transcript.

    `alpha-run-subagent` (window `PROJECT_JOINED_WINDOW`): two parent turns that
    request the Qwen alias through provider `gpu-example` and record the Qwen ID as
    `responseModel`, and one subagent call on `gpu-example/<child_model>` with 9
    turns.
    `nested_copies` also writes the subagent's own transcript where Pi mirrors it.
    `alpha-run-bare`: no transcript.
    """
    delegate_dir = make_project(root, "alpha", layout=layout)
    write_ledger(
        delegate_dir,
        [
            run_record(
                "alpha-run-subagent",
                ts="2026-10-20T10:01:40+00:00",
                wall_seconds=100,
                mode="handoff",
                model=model_record(model_id=ALIAS_QWEN),
            ),
            run_record("alpha-run-bare", ts="2026-10-20T11:00:00+00:00", wall_seconds=50),
        ],
    )
    run_dir = write_result_dir(delegate_dir, "alpha-run-subagent")
    write_session(
        run_dir,
        [
            session_header("parent-0001"),
            parent_turn(tokens_in=600, tokens_out=120, message_id="m-1"),
            subagent_tool_result(
                tokens_in=500, tokens_out=150, models=(f"{PROVIDER}/{child_model}",), turns=9
            ),
            parent_turn(tokens_in=400, tokens_out=80, cache_read=300, message_id="m-3"),
        ],
        name=f"{PARENT_SESSION}.jsonl",
    )
    if nested_copies:
        child = assistant_message(tokens_in=500, tokens_out=150, model=ALIAS_QWEN)
        write_nested_subagent_copies(
            run_dir, PARENT_SESSION, "sub-0001", "test-first", [session_header("child"), child]
        )


def built_rows(root: Path) -> dict[str, RunRow]:
    """Build the run table under `root` and key its rows by run ID."""
    rows, _ = build_run_table([root])
    return {row.run_id: row for row in rows}


def test_subagent_run_row_has_summed_tokens_and_subagent_columns(tmp_path: Path) -> None:
    write_subagent_project(tmp_path / "evidence")

    row = built_rows(tmp_path / "evidence")["alpha-run-subagent"]

    assert (row.tokens_in, row.tokens_out) == (SUMMED_IN, SUMMED_OUT)
    assert row.assistant_turns == 2
    assert row.subagent_runs == 1
    assert row.subagent_turns == 9


def test_subagent_run_row_keeps_child_names_in_their_own_column(tmp_path: Path) -> None:
    write_subagent_project(tmp_path / "evidence", child_model="local-helper-coder")

    row = built_rows(tmp_path / "evidence")["alpha-run-subagent"]

    assert tuple(row.requested_models or ()) == (ALIAS_QWEN,)
    assert tuple(row.served_models or ()) == (QWEN,)
    assert tuple(row.subagent_models or ()) == ("local-helper-coder",)


def test_run_without_a_transcript_has_null_subagent_columns(tmp_path: Path) -> None:
    write_subagent_project(tmp_path / "evidence")

    row = built_rows(tmp_path / "evidence")["alpha-run-bare"]

    assert row.subagent_runs is None
    assert row.subagent_turns is None
    assert row.subagent_models is None


def test_landing_copy_gives_the_same_row_as_the_project_root(tmp_path: Path) -> None:
    # A landing zone holds only the parent transcripts that ship-ledgers copies.
    write_subagent_project(tmp_path / "projects", "agentic", nested_copies=True)
    write_subagent_project(tmp_path / "landing-zone", "landing", nested_copies=False)

    project_rows = built_rows(tmp_path / "projects")
    landing_rows = built_rows(tmp_path / "landing-zone")

    assert sorted(project_rows) == sorted(landing_rows)
    for run_id, row in project_rows.items():
        landing = landing_rows[run_id]
        assert dataclasses.replace(landing, ledger_path="") == dataclasses.replace(
            row, ledger_path=""
        ), run_id
    assert landing_rows["alpha-run-subagent"].tokens_in == SUMMED_IN


def test_server_counts_equal_to_the_summed_tokens_join_exactly(tmp_path: Path) -> None:
    write_subagent_project(tmp_path / "evidence")
    row = built_rows(tmp_path / "evidence")["alpha-run-subagent"]
    start, end = PROJECT_JOINED_WINDOW
    fake = FakeVictoria(
        known=KNOWN,
        readings=model_readings(QWEN, prompt=float(SUMMED_IN), predicted=float(SUMMED_OUT)),
        active_between=(start - timedelta(minutes=5), end + timedelta(minutes=5)),
    )

    (joined,), _ = join_server_metrics([row], BASE_URL, fake, NOW)

    assert joined.server_model_id == QWEN
    assert joined.server_status == "joined"
    assert [joined.server_token_match] == ["exact"]


def test_a_child_on_another_known_model_does_not_change_the_resolved_model(
    tmp_path: Path,
) -> None:
    # Were the child's name a requested name, rule 2 would resolve FLASH instead.
    write_subagent_project(tmp_path / "evidence", child_model=FLASH)
    row = built_rows(tmp_path / "evidence")["alpha-run-subagent"]
    start, end = PROJECT_JOINED_WINDOW
    fake = FakeVictoria(
        known=KNOWN,
        readings=model_readings(QWEN, prompt=float(SUMMED_IN), predicted=float(SUMMED_OUT)),
        active_between=(start - timedelta(minutes=5), end + timedelta(minutes=5)),
    )

    (joined,), _ = join_server_metrics([row], BASE_URL, fake, NOW)

    assert tuple(row.subagent_models or ()) == (FLASH,)
    assert (joined.server_model_id, joined.server_model_source) == (QWEN, "served")
