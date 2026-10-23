"""Joining calls to sessions by time window and model (spec 0008: Per-session view).

A session claims a call when the call's `[timestamp - duration_ms, timestamp]`
overlaps the session's `[started_at - 5 s, ended_at + 5 s]` and their model sets
meet. A call's models are `activity.model` plus the request body's `model`; a
session's set comes from 0005's `resolve_model`, with `known` = every call model.
Ancestors of another claimant are dropped (the innermost session wins); two
unrelated claimants are both kept and flagged `ambiguous`. Interfaces: see
`capture_join_helpers`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from capture_builders import ALIAS_QWEN, FLASH, QWEN, at, session_row, write_sessions
from capture_join_helpers import call, claims, owners, seconds

from agentic_telemetry.captures.session_windows import read_session_windows


def test_call_inside_window_on_the_session_model_is_claimed(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30), models=(QWEN,))]

    result = claims(tmp_path, sessions, [call(1, at(10, 15), requested=QWEN)])

    assert owners(result[1]) == {"pi-a"}
    assert not set(result[1].flags)


def test_alias_session_claims_through_the_request_body_model(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30), models=(ALIAS_QWEN,))]

    result = claims(tmp_path, sessions, [call(1, at(10, 15), model=QWEN, requested=ALIAS_QWEN)])

    assert owners(result[1]) == {"pi-a"}


def test_served_model_name_lets_a_session_claim_its_calls(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30), models=("local-x",), served=(QWEN,))]

    result = claims(tmp_path, sessions, [call(1, at(10, 15), model=QWEN, requested=QWEN)])

    assert owners(result[1]) == {"pi-a"}


def test_five_second_pad_claims_a_call_just_before_the_session_starts(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30))]
    near = call(1, at(10) - seconds(4), duration_ms=1000)
    near_end = call(2, at(10, 30) + seconds(4) + seconds(1), duration_ms=1500)
    far = call(3, at(10) - seconds(6), duration_ms=1000)

    result = claims(tmp_path, sessions, [near, near_end, far])

    assert owners(result[1]) == {"pi-a"}
    assert owners(result[2]) == {"pi-a"}, "starts 3.5 s after ended_at"
    assert owners(result[3]) == set(), "ends 6 s before started_at"


def test_call_spanning_a_short_session_is_claimed_through_its_duration(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10, 10), at(10, 10, 20))]

    result = claims(tmp_path, sessions, [call(1, at(10, 11), duration_ms=120_000)])

    assert owners(result[1]) == {"pi-a"}, "the call ran 10:09:00-10:11:00, ending after the pad"


def test_child_session_wins_over_its_ancestors(tmp_path: Path) -> None:
    sessions = [
        session_row("pi-parent", at(10), at(10, 40)),
        session_row("pi-child", at(10, 10), at(10, 30), parent="pi-parent"),
        session_row("pi-grandchild", at(10, 15), at(10, 20), parent="pi-child"),
    ]
    records = [call(1, at(10, 5)), call(2, at(10, 12)), call(3, at(10, 17)), call(4, at(10, 35))]

    result = claims(tmp_path, sessions, records)

    assert owners(result[1]) == {"pi-parent"}
    assert owners(result[2]) == {"pi-child"}
    assert owners(result[3]) == {"pi-grandchild"}
    assert owners(result[4]) == {"pi-parent"}
    assert not [item for item in result.values() if set(item.flags)]


def test_the_parents_next_call_after_a_child_ends_stays_with_the_parent(tmp_path: Path) -> None:
    # Found on a real session: llama-swap stores the call end in whole seconds,
    # so end minus duration can land up to 1 s before the call's true start.
    sessions = [
        session_row("pi-parent", at(10), at(10, 40)),
        session_row("pi-child", at(10, 10), at(10, 20, 0, 400_000), parent="pi-parent"),
    ]
    child_last = call(1, at(10, 20), duration_ms=30_000)  # true end 10:20:00.4
    parent_next = call(2, at(10, 20, 6), duration_ms=6_200)  # true run 10:20:00.7-10:20:06.9

    result = claims(tmp_path, sessions, [child_last, parent_next])

    assert owners(result[1]) == {"pi-child"}
    assert owners(result[2]) == {"pi-parent"}, "the computed start 10:19:59.8 is truncation"


@pytest.mark.parametrize(
    ("end", "duration_ms", "child_end"),
    [
        (at(10, 20, 6, 300_000), 5_800, at(10, 20, 0, 600_000)),
        (at(10, 20), 300, at(10, 20, 0, 400_000)),
    ],
    ids=["sub-second-end-is-exact", "short-call-start-capped-at-its-end"],
)
def test_a_childs_own_last_call_stays_with_the_child(
    tmp_path: Path, end: datetime, duration_ms: int, child_end: datetime
) -> None:
    sessions = [
        session_row("pi-parent", at(10), at(10, 40)),
        session_row("pi-child", at(10, 10), child_end, parent="pi-parent"),
    ]

    result = claims(tmp_path, sessions, [call(1, end, duration_ms=duration_ms)])

    assert owners(result[1]) == {"pi-child"}


def test_overlapping_unrelated_sessions_on_one_model_are_both_listed_as_ambiguous(
    tmp_path: Path,
) -> None:
    sessions = [
        session_row("pi-a", at(10), at(10, 30)),
        session_row("pi-b", at(10, 10), at(10, 40)),
    ]

    result = claims(tmp_path, sessions, [call(1, at(10, 20)), call(2, at(10, 35))])

    assert owners(result[1]) == {"pi-a", "pi-b"}
    assert set(result[1].flags) == {"ambiguous"}
    assert owners(result[2]) == {"pi-b"}
    assert not set(result[2].flags)


def test_overlapping_sessions_on_different_models_do_not_collide(tmp_path: Path) -> None:
    sessions = [
        session_row("pi-qwen", at(10), at(10, 30), models=(QWEN,)),
        session_row("pi-flash", at(10), at(10, 30), models=(FLASH,)),
    ]
    records = [
        call(1, at(10, 10), model=QWEN, requested=QWEN),
        call(2, at(10, 11), model=FLASH, requested=FLASH),
    ]

    result = claims(tmp_path, sessions, records)

    assert owners(result[1]) == {"pi-qwen"}
    assert owners(result[2]) == {"pi-flash"}
    assert not set(result[1].flags) | set(result[2].flags)


def test_call_no_session_claims_is_still_listed_unclaimed(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30))]

    result = claims(tmp_path, sessions, [call(1, at(13))])

    assert list(result[1].session_ids) == []
    assert not set(result[1].flags)


def test_session_whose_models_meet_no_call_never_claims(tmp_path: Path) -> None:
    sessions = [
        session_row(
            "cc-frontier", at(10), at(10, 30), models=("claude-example-1",), client="claude-code"
        )
    ]

    result = claims(tmp_path, sessions, [call(1, at(10, 15))])

    assert owners(result[1]) == set()


def test_session_windows_are_read_from_the_session_table_parquet(tmp_path: Path) -> None:
    path = write_sessions(
        tmp_path / "session_table.parquet",
        [
            session_row("pi-a", at(10), at(10, 30), run_id="run-0001"),
            session_row("pi-b", at(11), at(11, 5), parent="pi-a"),
        ],
    )

    windows = read_session_windows(path)

    assert sorted(window.session_id for window in windows) == ["pi-a", "pi-b"]
