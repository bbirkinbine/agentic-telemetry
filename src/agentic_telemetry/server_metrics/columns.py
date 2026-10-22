"""The `server_*` columns one run gets from its resolved model's readings (spec 0005).

Where a model server lacks a metric, its column is null and the status is
unchanged: Strata serves no `n_tokens_max`, for example, which costs the run its
context column and nothing else.
"""

from dataclasses import dataclass

from agentic_telemetry.server_metrics.readings import WindowReadings
from agentic_telemetry.server_metrics.reconcile import (
    MATCH_EXACT,
    MATCH_NOT_COMPARED,
    MATCH_SERVER_LESS,
    MATCH_SERVER_MORE,
    token_match,
)
from agentic_telemetry.server_metrics.resolve import SOURCE_INFERRED, SOURCE_NONE

# The full vocabulary, so a summary can print every status and source with zeros.
# `not_queried` is deliberately absent: it is what a row holds when the join never
# ran, and a queried table never contains it.
SERVER_STATUSES = ("joined", "joined_approx", "overlap", "uncovered", "no_data", "error")
SERVER_MODEL_SOURCES = ("served", "requested", "inferred", "none")
STATUS_NOT_QUERIED = "not_queried"
STATUS_JOINED = "joined"
STATUS_JOINED_APPROX = "joined_approx"
STATUS_OVERLAP = "overlap"
STATUS_UNCOVERED = "uncovered"
STATUS_NO_DATA = "no_data"
STATUS_ERROR = "error"

# Statuses whose columns are the run's own server picture, so the overlap pass may
# rewrite them and a cold start means something.
JOIN_STATUSES = (STATUS_JOINED, STATUS_JOINED_APPROX, STATUS_OVERLAP)


@dataclass(frozen=True)
class ServerColumns:
    """The server-side columns of one run, ready to `dataclasses.replace` onto a row."""

    server_status: str
    server_model_id: str | None
    server_model_source: str
    server_token_match: str
    server_prompt_tokens: int | None
    server_predicted_tokens: int | None
    server_prefill_tps: float | None
    server_decode_tps: float | None
    server_deferred_max: int | None
    server_ctx_high_water: int | None
    server_cold_start: bool | None


def server_columns(
    readings: WindowReadings,
    model_id: str | None,
    source: str,
    tokens_in: int | None,
    tokens_out: int | None,
) -> ServerColumns:
    """Fill the server columns for one run from its window's readings.

    Args:
        readings: What the eight queries answered for the run's lookback (D8).
        model_id: The resolved model, or None when no rule resolved one.
        source: Which rule resolved it (`served`, `requested`, `inferred`, `none`).
        tokens_in: The run's transcript input tokens, or None with no transcript.
        tokens_out: The run's transcript output tokens, or None.

    Returns:
        The columns. Status is `uncovered` with no resolved model, `no_data` when
        the resolved model has no series in the window, and otherwise follows the
        token comparison: `overlap` for `server_more`, `joined_approx` for
        `server_less`, `joined` for an exact or uncomparable match. An `inferred`
        model whose tokens do not reconcile exactly is dropped: `uncovered`.
    """
    if model_id is None:
        return unread(STATUS_UNCOVERED, None, SOURCE_NONE)
    prompt = readings.prompt.get(model_id)
    predicted = readings.predicted.get(model_id)
    if prompt is None and predicted is None:
        return unread(STATUS_NO_DATA, model_id, source)
    prompt_tokens = _whole(prompt)
    predicted_tokens = _whole(predicted)
    match = token_match(tokens_in, tokens_out, prompt_tokens, predicted_tokens)
    if source == SOURCE_INFERRED and match != MATCH_EXACT:
        # An inference from moving counters is kept only when the run's own tokens
        # confirm it (D8); otherwise the run is as uncovered as if nothing moved.
        return unread(STATUS_UNCOVERED, None, SOURCE_NONE)
    status = {
        MATCH_SERVER_MORE: STATUS_OVERLAP,
        MATCH_SERVER_LESS: STATUS_JOINED_APPROX,
    }.get(match, STATUS_JOINED)
    return ServerColumns(
        server_status=status,
        server_model_id=model_id,
        server_model_source=source,
        server_token_match=match,
        server_prompt_tokens=prompt_tokens,
        server_predicted_tokens=predicted_tokens,
        server_prefill_tps=_per_second(prompt, readings.prompt_seconds.get(model_id)),
        server_decode_tps=_per_second(predicted, readings.predicted_seconds.get(model_id)),
        server_deferred_max=_whole(readings.deferred.get(model_id)),
        server_ctx_high_water=_whole(readings.ctx.get(model_id)),
        server_cold_start=_cold_start(readings, model_id),
    )


def unread(status: str, model_id: str | None, source: str) -> ServerColumns:
    """The server columns of a run whose server side was never read.

    Used for `uncovered`, `no_data`, `error` and a run with no usable window:
    the run keeps its resolution when it has one, and every value is null.

    Args:
        status: The status to record.
        model_id: The resolved model, when one was resolved.
        source: The rule that resolved it.

    Returns:
        Columns with `not_compared` and null values.
    """
    return ServerColumns(
        server_status=status,
        server_model_id=model_id,
        server_model_source=source,
        server_token_match=MATCH_NOT_COMPARED,
        server_prompt_tokens=None,
        server_predicted_tokens=None,
        server_prefill_tps=None,
        server_decode_tps=None,
        server_deferred_max=None,
        server_ctx_high_water=None,
        server_cold_start=None,
    )


def _whole(value: float | None) -> int | None:
    """Round a token or sample count to a whole number, keeping a missing value null."""
    return None if value is None else round(value)


def _per_second(tokens: float | None, seconds: float | None) -> float | None:
    """Tokens per second over the window, or null when either side is missing or zero.

    A zero denominator means the counter rose without any measured time, which
    says nothing about throughput.
    """
    if tokens is None or seconds is None or seconds <= 0:
        return None
    return tokens / seconds


def _cold_start(readings: WindowReadings, model_id: str) -> bool | None:
    """True when the model was down (or unseen) at the run's start and up later.

    Measured to scrape resolution: `up` answers once a scrape interval, so this
    says a load happened, not how long the run waited for it. Null when no `up`
    sample exists after the start at all (D8): nothing says whether it loaded.
    """
    up_later = readings.up_later.get(model_id)
    if up_later is None:
        return None
    started_down = readings.up_at_start.get(model_id) in (None, 0.0)
    return started_down and up_later == 1.0
