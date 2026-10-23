"""Which session made each captured call (spec 0008: Per-session view).

Rules, in order:

1. Header: the capture's first non-empty session header (`decode.SESSION_HEADERS`)
   names a session-table `session_id`; that session alone claims the call.
2. Time and model: a session claims a call when the call's span overlaps the
   session's span padded by `PAD` on each side and their model names meet.
   A call's names are llama-swap's `model` plus the request body's `model`; a
   session's come from 0005's `resolve_model`. Between a claimant and its
   ancestor, the descendant keeps the call only when the call overlaps the
   descendant's own, unpadded span, taking the latest start the call could have
   had (`TIMESTAMP_PRECISION`); in the pad the ancestor keeps it. Two or
   more unrelated claimants are all kept and flagged `ambiguous`.
3. Otherwise the call is unclaimed. A header that named no session adds
   `header_unmatched` to whatever rule 2 decided.

A session's parent is its `parent_session_id`, or for a delegate run's root
session, the orchestrating session (`orchestrators`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from agentic_telemetry.captures.activity import record_end, record_start
from agentic_telemetry.captures.decode import request_model, session_header
from agentic_telemetry.captures.session_windows import SessionWindow
from agentic_telemetry.server_metrics.resolve import resolve_model

# Allows for clock skew between the inference host and the client's machine.
PAD = timedelta(seconds=5)
# llama-swap v264's SQLite store (`store.path`) keeps a call's end in whole
# seconds (the in-memory store keeps nanoseconds), so a whole-second end may
# be up to this much early, and end minus duration starts the call too soon. A
# parent's next call after a child returns then starts inside the child's span.
TIMESTAMP_PRECISION = timedelta(seconds=1)
AMBIGUOUS = "ambiguous"
HEADER_UNMATCHED = "header_unmatched"


@dataclass(frozen=True)
class ClaimedCall:
    """One harvested record and the sessions that claim it (empty when unclaimed)."""

    record: Mapping[str, object]
    session_ids: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class _Call:
    """What the join needs from one record, parsed once."""

    start: datetime
    end: datetime
    latest_start: datetime
    models: frozenset[str]
    header: str | None


def _activity(record: Mapping[str, object]) -> dict[str, object]:
    """Return a record's activity object."""
    activity = record.get("activity")
    if not isinstance(activity, dict):
        raise ValueError("capture record has no activity object")
    return activity


def _parse_call(record: Mapping[str, object]) -> _Call:
    """Return a record's span, model names (llama-swap's and requested) and session header."""
    activity = _activity(record)
    capture = record.get("capture")
    names = {request_model(capture), activity.get("model")}
    header = session_header(capture.get("req_headers")) if isinstance(capture, dict) else None
    start, end = record_start(record), record_end(record)
    return _Call(
        start=start,
        end=end,
        latest_start=min(start + TIMESTAMP_PRECISION, end) if end.microsecond == 0 else start,
        models=frozenset(name for name in names if isinstance(name, str) and name),
        header=header,
    )


def _session_models(window: SessionWindow, known: frozenset[str]) -> frozenset[str]:
    """Return the names a session's calls can carry, resolved against `known`."""
    resolved, _source = resolve_model(window.models, window.served_models, None, known, ())
    if resolved is not None:
        return frozenset({resolved})
    return frozenset(name for name in window.models if name in known)


def orchestrators(sessions: Sequence[SessionWindow]) -> dict[str, str]:
    """Map each delegate run's root session to the one session that orchestrated it.

    The orchestrator has no `run_id`, its `project` is the checkout holding the
    run's results, and its span contains the run's start. Zero or several
    candidates link nothing.
    """
    links: dict[str, str] = {}
    for run in sessions:
        checkout = run.delegate_checkout()
        if checkout is None or run.is_subagent or run.start is None:
            continue
        begun = run.start
        candidates = [
            other.session_id
            for other in sessions
            if other.run_id is None
            and other.project == checkout
            and other.start is not None
            and other.end is not None
            and other.start - PAD <= begun <= other.end + PAD
        ]
        if len(candidates) == 1:
            links[run.session_id] = candidates[0]
    return links


def session_parents(sessions: Sequence[SessionWindow]) -> dict[str, str]:
    """Map each session to its parent: `parent_session_id`, else its orchestrator."""
    parents = {w.session_id: w.parent_session_id for w in sessions if w.parent_session_id}
    parents.update(orchestrators(sessions))
    return parents


def _ancestors(session_id: str, parents: Mapping[str, str]) -> set[str]:
    """Return the ids above `session_id` in its parent chain."""
    found: set[str] = set()
    parent = parents.get(session_id)
    while parent is not None and parent not in found:
        found.add(parent)
        parent = parents.get(parent)
    return found


def _overlaps(call: _Call, window: SessionWindow, pad: timedelta) -> bool:
    """True when the call's span meets the session's span widened by `pad`."""
    if window.start is None or window.end is None:
        return False
    return call.start <= window.end + pad and call.end >= window.start - pad


def _inside_unpadded(call: _Call, window: SessionWindow) -> bool:
    """True when the call, at its latest possible start, meets the session's own span."""
    if window.start is None or window.end is None:
        return False
    return call.latest_start <= window.end and call.end >= window.start


def _by_time_and_model(
    call: _Call,
    windows: Sequence[tuple[SessionWindow, frozenset[str]]],
    parents: Mapping[str, str],
) -> list[str]:
    """Return the sessions that keep the call by time window and model."""
    claimants = {
        window.session_id: window
        for window, models in windows
        if call.models & models and _overlaps(call, window, PAD)
    }
    dropped: set[str] = set()
    for session_id, window in claimants.items():
        above = _ancestors(session_id, parents) & claimants.keys()
        if not above:
            continue
        if _inside_unpadded(call, window):
            dropped |= above
        else:
            dropped.add(session_id)
    return sorted(claimants.keys() - dropped)


def assign_calls(
    records: Sequence[Mapping[str, object]], sessions: Sequence[SessionWindow]
) -> list[ClaimedCall]:
    """Return one `ClaimedCall` per record, in input order."""
    by_id = {window.session_id: window for window in sessions}
    parents = session_parents(sessions)
    calls = [_parse_call(record) for record in records]
    known = frozenset().union(*(call.models for call in calls))
    windows = [(window, _session_models(window, known)) for window in sessions]
    claimed: list[ClaimedCall] = []
    for record, call in zip(records, calls, strict=True):
        if call.header is not None and call.header in by_id:
            claimed.append(ClaimedCall(record=record, session_ids=(call.header,), flags=()))
            continue
        owners = _by_time_and_model(call, windows, parents)
        flags = [AMBIGUOUS] if len(owners) > 1 else []
        if call.header is not None:
            flags.append(HEADER_UNMATCHED)
        claimed.append(ClaimedCall(record=record, session_ids=tuple(owners), flags=tuple(flags)))
    return claimed
