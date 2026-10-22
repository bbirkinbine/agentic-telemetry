"""Session-header claims ahead of time and model (spec 0008: Per-session view, D2).

The header value is the first non-empty one of `x-session-id`,
`x-session-affinity`, `x-client-request-id` in the capture's `req_headers`,
names matched in any case. A value equal to a session-table `session_id` claims
the call for that session alone, with no `ambiguous` flag and no window check.
A value that names no session falls back to time and model and adds
`header_unmatched`. Interfaces: `session_header(req_headers) -> str | None` from
`captures.decode`, and the join helpers in `capture_join_helpers`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from capture_builders import QWEN, at, session_row
from capture_join_helpers import call, claims, owners

from agentic_telemetry.captures.decode import session_header
from agentic_telemetry.session_table.row import SessionRow

HEADER_SPELLINGS = [
    "X-Session-Id",
    "x-session-id",
    "X-Session-Affinity",
    "x-session-affinity",
    "X-Client-Request-Id",
    "X-CLIENT-REQUEST-ID",
]


def overlapping() -> list[SessionRow]:
    """Return two unrelated sessions on the same model, both covering 10:10-10:30."""
    return [
        session_row("pi-a", at(10), at(10, 30)),
        session_row("pi-b", at(10, 10), at(10, 40)),
    ]


@pytest.mark.parametrize("name", HEADER_SPELLINGS)
def test_session_header_reads_each_of_the_three_names_in_any_case(name: str) -> None:
    assert session_header({"Content-Type": "application/json", name: "pi-b"}) == "pi-b"


def test_session_header_is_none_without_any_of_the_three() -> None:
    assert session_header({"Content-Type": "application/json", "X-Request-Id": "r-1"}) is None


def test_session_header_takes_the_first_non_empty_in_the_fixed_order() -> None:
    headers = {
        "X-Client-Request-Id": "pi-c",
        "X-Session-Affinity": "pi-b",
        "X-Session-Id": "pi-a",
    }

    assert session_header(headers) == "pi-a"
    assert session_header({**headers, "X-Session-Id": ""}) == "pi-b"
    assert session_header({"X-Session-Id": "", "X-Client-Request-Id": "pi-c"}) == "pi-c"


@pytest.mark.parametrize("name", HEADER_SPELLINGS)
def test_header_claim_beats_an_overlapping_session_without_ambiguous(
    tmp_path: Path, name: str
) -> None:
    result = claims(tmp_path, overlapping(), [call(1, at(10, 20), headers={name: "pi-b"})])

    assert owners(result[1]) == {"pi-b"}
    assert not set(result[1].flags)


def test_header_claim_holds_outside_the_session_window(tmp_path: Path) -> None:
    result = claims(tmp_path, overlapping(), [call(1, at(12), headers={"X-Session-Id": "pi-a"})])

    assert owners(result[1]) == {"pi-a"}
    assert not set(result[1].flags)


def test_header_claim_ignores_the_model_rule(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30), models=("local-other",))]

    result = claims(tmp_path, sessions, [call(1, at(10, 5), headers={"X-Session-Id": "pi-a"})])

    assert owners(result[1]) == {"pi-a"}


def test_disagreeing_headers_follow_the_fixed_order(tmp_path: Path) -> None:
    headers = {"X-Session-Affinity": "pi-b", "X-Session-Id": "pi-a"}

    result = claims(tmp_path, overlapping(), [call(1, at(10, 20), headers=headers)])

    assert owners(result[1]) == {"pi-a"}


def test_header_naming_no_session_falls_back_with_header_unmatched(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30))]
    record = call(1, at(10, 15), headers={"X-Client-Request-Id": "req-7f3a"})

    result = claims(tmp_path, sessions, [record])

    assert owners(result[1]) == {"pi-a"}
    assert set(result[1].flags) == {"header_unmatched"}


def test_unmatched_header_with_overlapping_sessions_is_ambiguous_too(tmp_path: Path) -> None:
    record = call(1, at(10, 20), headers={"X-Session-Id": "pi-gone"})

    result = claims(tmp_path, overlapping(), [record])

    assert owners(result[1]) == {"pi-a", "pi-b"}
    assert set(result[1].flags) == {"ambiguous", "header_unmatched"}


def test_unmatched_header_and_no_window_is_unclaimed_but_flagged(tmp_path: Path) -> None:
    record = call(1, at(13), headers={"X-Session-Id": "pi-gone"})

    result = claims(tmp_path, overlapping(), [record])

    assert list(result[1].session_ids) == []
    assert set(result[1].flags) == {"header_unmatched"}


def test_first_non_empty_header_alone_decides_even_when_a_later_one_matches(
    tmp_path: Path,
) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30))]
    headers = {"X-Session-Id": "pi-gone", "X-Session-Affinity": "pi-a"}

    result = claims(tmp_path, sessions, [call(1, at(10, 15), headers=headers)])

    assert owners(result[1]) == {"pi-a"}
    assert set(result[1].flags) == {"header_unmatched"}, "pi-a claims by time and model"


def test_no_header_falls_back_to_time_and_model_silently(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30))]

    result = claims(tmp_path, sessions, [call(1, at(10, 15))])

    assert owners(result[1]) == {"pi-a"}
    assert not set(result[1].flags)


def test_evicted_call_without_capture_uses_time_and_model(tmp_path: Path) -> None:
    sessions = [session_row("pi-a", at(10), at(10, 30), models=(QWEN,))]
    record = call(1, at(10, 15))
    record["capture"] = None
    record["capture_status"] = "evicted"

    result = claims(tmp_path, sessions, [record])

    assert owners(result[1]) == {"pi-a"}, "activity.model alone still meets the session"
    assert not set(result[1].flags)
