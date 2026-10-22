"""Model resolution rules 1 to 4 (spec 0005, Model resolution; D3 rule-2 candidates)."""

from __future__ import annotations

import pytest
from server_join_helpers import (
    ALIAS_MAYA,
    ALIAS_QWEN,
    ALIAS_UNKNOWN,
    FLASH,
    GLM,
    KNOWN,
    MAYA,
    QWEN,
)

from agentic_telemetry.server_metrics.resolve import resolve_model

NOTHING_MOVED: frozenset[str] = frozenset()

CASES = [
    pytest.param(
        (ALIAS_QWEN,), (QWEN,), ALIAS_QWEN, NOTHING_MOVED, (QWEN, "served"), id="rule1-served"
    ),
    pytest.param(
        (QWEN,), (QWEN,), ALIAS_QWEN, NOTHING_MOVED, (QWEN, "served"), id="rule1-same-known-name"
    ),
    pytest.param(
        (MAYA,), (GLM,), ALIAS_MAYA, NOTHING_MOVED, (MAYA, "requested"), id="maya-requested-wins"
    ),
    pytest.param(
        (ALIAS_MAYA, MAYA),
        (GLM,),
        ALIAS_MAYA,
        NOTHING_MOVED,
        (MAYA, "requested"),
        id="maya-alias-and-id-requested",
    ),
    pytest.param(
        (QWEN,), (), ALIAS_QWEN, NOTHING_MOVED, (QWEN, "requested"), id="rule2-requested-id"
    ),
    pytest.param(
        (ALIAS_QWEN,), (QWEN, FLASH), QWEN, NOTHING_MOVED, (QWEN, "requested"), id="two-served"
    ),
    pytest.param((), (), QWEN, NOTHING_MOVED, (QWEN, "requested"), id="ledger-model-id-alone"),
    pytest.param(
        (ALIAS_QWEN,),
        (ALIAS_QWEN,),
        QWEN,
        NOTHING_MOVED,
        (QWEN, "requested"),
        id="echoed-alias-falls-to-ledger-id",
    ),
    pytest.param(
        (ALIAS_QWEN,),
        (ALIAS_QWEN,),
        ALIAS_QWEN,
        frozenset({FLASH}),
        (None, "none"),
        id="echoed-alias-is-not-inferred",
    ),
    pytest.param(
        (), (), ALIAS_QWEN, frozenset({QWEN}), (QWEN, "inferred"), id="rule3-inferred-no-names"
    ),
    pytest.param(
        (ALIAS_QWEN,), (), ALIAS_QWEN, frozenset({QWEN}), (None, "none"), id="names-block-rule3"
    ),
    pytest.param(
        (QWEN, FLASH),
        (),
        ALIAS_QWEN,
        frozenset({FLASH}),
        (None, "none"),
        id="two-requested-known-is-not-inferred",
    ),
    pytest.param(
        (QWEN,),
        (),
        FLASH,
        frozenset({FLASH}),
        (None, "none"),
        id="requested-and-ledger-disagree-is-not-inferred",
    ),
    pytest.param((), (), ALIAS_QWEN, frozenset({QWEN, FLASH}), (None, "none"), id="two-moved"),
    pytest.param(
        (ALIAS_UNKNOWN,), (), ALIAS_UNKNOWN, NOTHING_MOVED, (None, "none"), id="rule4-none"
    ),
    pytest.param((), (), None, NOTHING_MOVED, (None, "none"), id="no-names-at-all"),
]


@pytest.mark.parametrize(("requested", "served", "ledger_id", "moved", "expected"), CASES)
def test_first_applicable_rule_sets_model_and_source(
    requested: tuple[str, ...],
    served: tuple[str, ...],
    ledger_id: str | None,
    moved: frozenset[str],
    expected: tuple[str | None, str],
) -> None:
    assert resolve_model(requested, served, ledger_id, frozenset(KNOWN), moved) == expected


def test_served_name_is_trusted_only_when_known() -> None:
    known = frozenset({QWEN})

    result = resolve_model((ALIAS_QWEN,), (GLM,), ALIAS_QWEN, known, NOTHING_MOVED)

    assert result == (None, "none")


def test_unknown_requested_name_does_not_block_a_known_served_name() -> None:
    """Rule 1 is blocked only by a requested name that is a *different known* ID."""
    known = frozenset({QWEN, GLM})

    result = resolve_model((MAYA,), (GLM,), ALIAS_MAYA, known, NOTHING_MOVED)

    assert result == (GLM, "served")


def test_names_in_transcript_beat_counters_that_moved() -> None:
    result = resolve_model((ALIAS_QWEN,), (QWEN,), ALIAS_QWEN, frozenset(KNOWN), frozenset({FLASH}))

    assert result == (QWEN, "served")


def test_ledger_model_id_also_contradicts_a_served_name() -> None:
    """D8: an alias request, a known Maya ID in the ledger and a GLM answer is Maya."""
    known = frozenset({MAYA, GLM})

    result = resolve_model((ALIAS_MAYA,), (GLM,), MAYA, known, NOTHING_MOVED)

    assert result == (MAYA, "requested")
