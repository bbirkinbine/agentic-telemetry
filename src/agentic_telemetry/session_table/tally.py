"""`SessionTally`: the running totals every client reader feeds while it reads a transcript.

Readers pass raw JSON values; anything of the wrong type is skipped, never
raised, so one odd line cannot stop a session from being summarized.
"""

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import PurePosixPath

from agentic_telemetry.run_table.json_values import opt_int, opt_str
from agentic_telemetry.session_table.row import SessionIdentity, SessionRow

# Token columns are BIGINT; a count or sum outside int64 is treated as unrecorded.
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1
TOKEN_COLUMNS = (
    "tokens_in",
    "tokens_cache_read",
    "tokens_cache_write",
    "tokens_out",
    "tokens_reasoning",
)


class SessionTally:
    """Accumulate times, models, token totals and activity counts for one session."""

    def __init__(self) -> None:
        """Start with no timestamps, no turns and every token column unrecorded."""
        self._earliest: datetime | None = None
        self._latest: datetime | None = None
        self._turns_per_model: dict[str, int] = {}
        self._turns_per_served_model: dict[str, int] = {}
        self._first_provider: dict[str, str] = {}
        self._tokens: dict[str, int] = {}
        self._overflowed: set[str] = set()
        self.assistant_turns = 0
        self.user_prompts = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.compactions = 0
        self.stop_reasons: Counter[str] = Counter()

    def observe_timestamp(self, value: object) -> None:
        """Widen the session's time range with an ISO-8601 timestamp; a naive one is UTC."""
        text = opt_str(value)
        if not text:
            return
        try:
            instant = datetime.fromisoformat(text)
            instant = (
                instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant.astimezone(UTC)
            )
        except (ValueError, OverflowError):
            # Unparseable, or out of range once moved to UTC (e.g. year 1 with an offset).
            return
        if self._earliest is None or instant < self._earliest:
            self._earliest = instant
        if self._latest is None or instant > self._latest:
            self._latest = instant

    def count_assistant_turn(
        self, model: object, provider: object = None, served_model: object = None
    ) -> None:
        """Count one assistant turn, credited to `model` when it is a string.

        `served_model` is counted on its own, even when `model` is missing.

        Args:
            model: The model the client asked for (for a local server, its profile alias).
            provider: The provider the client recorded for this turn.
            served_model: The model that answered, when the client records it.
        """
        self.assistant_turns += 1
        served_name = opt_str(served_model)
        if served_name:
            served_turns = self._turns_per_served_model.get(served_name, 0) + 1
            self._turns_per_served_model[served_name] = served_turns
        name = opt_str(model)
        if not name:
            return
        self._turns_per_model[name] = self._turns_per_model.get(name, 0) + 1
        provider_name = opt_str(provider)
        if provider_name and name not in self._first_provider:
            self._first_provider[name] = provider_name

    def add_usage(
        self,
        *,
        tokens_in: object = None,
        tokens_cache_read: object = None,
        tokens_cache_write: object = None,
        tokens_out: object = None,
        tokens_reasoning: object = None,
    ) -> None:
        """Add token counts; each column stays null until an integer is recorded for it.

        A single count outside int64 is skipped like any mistyped value. A sum that
        leaves int64 makes the column null for the session: unknown, not an undercount.
        """
        given = (tokens_in, tokens_cache_read, tokens_cache_write, tokens_out, tokens_reasoning)
        for column, value in zip(TOKEN_COLUMNS, given, strict=True):
            count = opt_int(value)
            if count is None or not INT64_MIN <= count <= INT64_MAX:
                continue
            total = self._tokens.get(column, 0) + count
            if INT64_MIN <= total <= INT64_MAX:
                self._tokens[column] = total
            else:
                self._overflowed.add(column)

    def _token_total(self, column: str) -> int | None:
        """Return a token column's total, or None when unrecorded or overflowed."""
        return None if column in self._overflowed else self._tokens.get(column)

    def count_stop_reason(self, value: object) -> None:
        """Count one assistant stop reason when it is a string."""
        reason = opt_str(value)
        if reason:
            self.stop_reasons[reason] += 1

    @property
    def started_at(self) -> str | None:
        """The earliest timestamp, as ISO-8601 UTC."""
        return _iso(self._earliest)

    @property
    def ended_at(self) -> str | None:
        """The latest timestamp, as ISO-8601 UTC."""
        return _iso(self._latest)

    @property
    def span_seconds(self) -> float | None:
        """Wall seconds from the earliest to the latest timestamp, idle time included."""
        if self._earliest is None or self._latest is None:
            return None
        return (self._latest - self._earliest).total_seconds()

    @property
    def models(self) -> tuple[str, ...]:
        """Distinct models in first-seen order."""
        return tuple(self._turns_per_model)

    @property
    def primary_model(self) -> str | None:
        """The model with the most turns; a tie goes to the first seen."""
        return _most_turns(self._turns_per_model)

    @property
    def served_models(self) -> tuple[str, ...]:
        """Distinct served models in first-seen order."""
        return tuple(self._turns_per_served_model)

    @property
    def primary_served_model(self) -> str | None:
        """The served model with the most turns; a tie goes to the first seen."""
        return _most_turns(self._turns_per_served_model)

    @property
    def primary_provider(self) -> str | None:
        """The provider recorded on the primary model's first turn."""
        primary = self.primary_model
        return self._first_provider.get(primary) if primary else None

    @property
    def tokens_in(self) -> int | None:
        """Uncached input tokens."""
        return self._token_total("tokens_in")

    @property
    def tokens_cache_read(self) -> int | None:
        """Input tokens read from the prompt cache."""
        return self._token_total("tokens_cache_read")

    @property
    def tokens_cache_write(self) -> int | None:
        """Input tokens written to the prompt cache."""
        return self._token_total("tokens_cache_write")

    @property
    def tokens_out(self) -> int | None:
        """Output tokens, as the client reports them."""
        return self._token_total("tokens_out")

    @property
    def tokens_reasoning(self) -> int | None:
        """Reasoning tokens, as the client reports them."""
        return self._token_total("tokens_reasoning")

    def to_row(self, identity: SessionIdentity) -> SessionRow:
        """Return the session row for `identity` with this tally's totals."""
        return SessionRow(
            client=identity.client,
            session_id=identity.session_id,
            source_path=identity.source_path,
            project=project_name(identity.cwd),
            cwd=identity.cwd,
            client_version=identity.client_version,
            git_branch=identity.git_branch,
            is_subagent=identity.is_subagent,
            parent_session_id=identity.parent_session_id,
            run_id=identity.run_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            span_seconds=self.span_seconds,
            provider=identity.provider,
            models=self.models,
            primary_model=self.primary_model,
            served_models=self.served_models,
            primary_served_model=self.primary_served_model,
            tokens_in=self.tokens_in,
            tokens_cache_read=self.tokens_cache_read,
            tokens_cache_write=self.tokens_cache_write,
            tokens_out=self.tokens_out,
            tokens_reasoning=self.tokens_reasoning,
            assistant_turns=self.assistant_turns,
            user_prompts=self.user_prompts,
            tool_calls=self.tool_calls,
            tool_errors=self.tool_errors,
            compactions=self.compactions,
            stop_reasons=_stop_reasons_json(self.stop_reasons),
        )


def _most_turns(turns: dict[str, int]) -> str | None:
    """Return the key with the most turns; max() keeps the first seen on a tie."""
    if not turns:
        return None
    return max(turns, key=lambda name: turns[name])


def project_name(cwd: str | None) -> str | None:
    """Return the base name of a recorded working directory, or None."""
    if not cwd:
        return None
    return PurePosixPath(cwd).name or None


def _iso(instant: datetime | None) -> str | None:
    """Format a UTC instant as ISO-8601 with milliseconds and a `+00:00` offset."""
    return instant.isoformat(timespec="milliseconds") if instant else None


def _stop_reasons_json(counts: Counter[str]) -> str | None:
    """Return stop-reason counts as sorted JSON text, or None when none were recorded."""
    return json.dumps(dict(counts), sort_keys=True) if counts else None
