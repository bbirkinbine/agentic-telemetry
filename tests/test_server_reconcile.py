"""Token reconciliation between a run's transcript and the server (spec 0005; D3 mixed)."""

from __future__ import annotations

import pytest

from agentic_telemetry.server_metrics.reconcile import token_match


@pytest.mark.parametrize(
    ("tokens_in", "tokens_out", "server_prompt", "server_predicted", "expected"),
    [
        pytest.param(1000, 200, 1000, 200, "exact", id="both-equal"),
        pytest.param(1000, 200, 1500, 200, "server_more", id="more-prompt"),
        pytest.param(1000, 200, 1000, 260, "server_more", id="more-predicted"),
        pytest.param(1000, 200, 900, 200, "server_less", id="less-prompt"),
        pytest.param(1000, 200, 1000, 150, "server_less", id="less-predicted"),
        pytest.param(1000, 200, 900, 150, "server_less", id="less-both"),
        pytest.param(1000, 200, 1500, 150, "server_more", id="mixed-more-prompt-less-out"),
        pytest.param(1000, 200, 900, 260, "server_more", id="mixed-less-prompt-more-out"),
    ],
)
def test_prompt_and_predicted_are_compared_separately(
    tokens_in: int, tokens_out: int, server_prompt: int, server_predicted: int, expected: str
) -> None:
    assert token_match(tokens_in, tokens_out, server_prompt, server_predicted) == expected


@pytest.mark.parametrize(
    "inputs",
    [
        pytest.param((None, 200, 1000, 200), id="no-transcript-in"),
        pytest.param((1000, None, 1000, 200), id="no-transcript-out"),
        pytest.param((1000, 200, None, 200), id="no-server-prompt"),
        pytest.param((1000, 200, 1000, None), id="no-server-predicted"),
        pytest.param((None, None, None, None), id="nothing"),
    ],
)
def test_any_missing_side_is_not_compared(
    inputs: tuple[int | None, int | None, int | None, int | None],
) -> None:
    assert token_match(*inputs) == "not_compared"
