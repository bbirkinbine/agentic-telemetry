"""Token totals, model names and turns from a run's Pi session transcripts."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from agentic_telemetry.run_table.json_values import JsonObject, as_object, opt_int, opt_str
from agentic_telemetry.run_table.subagent_usage import (
    SubagentCall,
    read_subagent_call,
    strip_provider,
)

# The usage keys summed into their own columns. `cacheWrite` and `totalTokens`
# are not summed into any column (spec 0001).
_SUMMED_USAGE_KEYS = ("input", "output", "cacheRead", "reasoning")

# The usage keys that make up one turn's context window.
_CONTEXT_KEYS = ("input", "cacheRead", "cacheWrite")


@dataclass(frozen=True)
class SessionUsage:
    """Usage summed over a run's assistant messages and `subagent` tool results.

    A `subagent` tool result's `usage` covers every turn its child runs took, so
    `tokens_in`, `tokens_out` and `tokens_cache_read` include the subagent's work
    (spec 0009). `tokens_reasoning` does too only if the tool result reports
    `reasoning`, which Pi's does not. `assistant_turns` and
    `peak_turn_prompt_tokens` stay the parent session's own. `subagent_runs`
    counts the child runs (`details.results` entries) and `subagent_turns` sums
    their `totalChildUsage.turns`, None when any call lacks an integer one.

    `requested_models` and `served_models` are the distinct `model` and
    `responseModel` names of the assistant messages, in first-seen order across
    the transcripts sorted by name: the parent's own, which the server-metrics
    join resolves. `subagent_models` holds the children's distinct
    `details.results[].model` names, without a `<provider>/` prefix that matches
    one of the run's assistant messages' `provider`. Pi writes
    `responseModel` only when the server answered with a name other than the one
    requested, so an empty `served_models` means the run recorded no served name —
    not that the server answered wrongly.

    `peak_turn_prompt_tokens` is this run's own largest one-turn context. The
    server's `llamacpp_n_tokens_max_total` is a high-water mark since the model
    loaded and can belong to an earlier run; this cannot.
    """

    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    tokens_reasoning: int
    assistant_turns: int
    requested_models: tuple[str, ...]
    served_models: tuple[str, ...]
    peak_turn_prompt_tokens: int | None
    subagent_runs: int
    subagent_turns: int | None
    subagent_models: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def read_session_usage(session_dir: Path) -> SessionUsage | None:
    """Read the assistant messages and subagent calls in each `*.jsonl` in `session_dir`.

    Only the directory's own files are read. The subdirectories that mirror a
    subagent's transcripts are skipped: its `subagent` tool result already counts
    those turns.

    Args:
        session_dir: A run's `session/` directory.

    Returns:
        The totals, model names, peak turn context and subagent runs, turns and
        models, or None when the directory is missing or holds no transcript.
    """
    if not session_dir.is_dir():
        return None
    transcripts = sorted(path for path in session_dir.glob("*.jsonl") if path.is_file())
    if not transcripts:
        return None
    tally = _Tally()
    for transcript in transcripts:
        _add_transcript(transcript, tally)
    return tally.usage()


@dataclass
class _Tally:
    """What one pass over a run's transcripts accumulates."""

    totals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_SUMMED_USAGE_KEYS, 0))
    turns: int = 0
    requested_models: dict[str, None] = field(default_factory=dict)
    served_models: dict[str, None] = field(default_factory=dict)
    peak_turn_prompt_tokens: int | None = None
    providers: dict[str, None] = field(default_factory=dict)
    subagent_models: dict[str, None] = field(default_factory=dict)
    subagent_runs: int = 0
    subagent_turns: int | None = 0
    warnings: list[str] = field(default_factory=list)

    def add_turn(self, message: JsonObject) -> None:
        """Count one assistant message: its usage, its model names, its context."""
        usage = as_object(message.get("usage")) or {}
        self._add_usage(usage)
        self.turns += 1
        self._record_name(self.requested_models, message, "model")
        self._record_name(self.served_models, message, "responseModel")
        self._record_name(self.providers, message, "provider")
        context = sum(opt_int(usage.get(key)) or 0 for key in _CONTEXT_KEYS)
        if self.peak_turn_prompt_tokens is None or context > self.peak_turn_prompt_tokens:
            self.peak_turn_prompt_tokens = context

    def add_subagent_call(self, call: SubagentCall) -> None:
        """Count one subagent call's tokens, child runs, turns and model names.

        Its usage is a total over many turns, so it is not a turn here and has no
        one-turn context.
        """
        self._add_usage(call.usage)
        self.subagent_runs += call.runs
        if self.subagent_turns is None or call.turns is None:
            self.subagent_turns = None
        else:
            self.subagent_turns += call.turns
        for name in call.models:
            self.subagent_models.setdefault(name, None)

    def usage(self) -> SessionUsage:
        """Return the accumulated columns."""
        children = (strip_provider(name, self.providers) for name in self.subagent_models)
        return SessionUsage(
            tokens_in=self.totals["input"],
            tokens_out=self.totals["output"],
            tokens_cache_read=self.totals["cacheRead"],
            tokens_reasoning=self.totals["reasoning"],
            assistant_turns=self.turns,
            requested_models=tuple(self.requested_models),
            served_models=tuple(self.served_models),
            peak_turn_prompt_tokens=self.peak_turn_prompt_tokens,
            subagent_runs=self.subagent_runs,
            subagent_turns=self.subagent_turns,
            subagent_models=tuple(dict.fromkeys(children)),
            warnings=tuple(self.warnings),
        )

    def _add_usage(self, usage: JsonObject) -> None:
        """Add a usage object's summed keys; a missing or non-integer value adds 0."""
        for key in self.totals:
            self.totals[key] += opt_int(usage.get(key)) or 0

    def _record_name(self, seen: dict[str, None], message: JsonObject, key: str) -> None:
        """Add `message`'s value at `key` to `seen`, keeping first-seen order."""
        name = opt_str(message.get(key))
        if name is not None:
            seen.setdefault(name, None)


def _add_transcript(transcript: Path, tally: _Tally) -> None:
    """Add one transcript's assistant messages and subagent calls to `tally`.

    Malformed lines are skipped; the file gets one warning with the first bad line
    number and the count, since a truncated transcript can have many.
    """
    bad_lines = 0
    first_bad_line = 0
    for line_no, message in _counted_messages(transcript):
        if message is None:
            bad_lines += 1
            first_bad_line = first_bad_line or line_no
            continue
        if opt_str(message.get("role")) == "assistant":
            tally.add_turn(message)
            continue
        call = read_subagent_call(message)
        if call is not None:
            tally.add_subagent_call(call)
    if bad_lines:
        tally.warnings.append(
            f"{transcript}:{first_bad_line}: skipped {bad_lines} malformed transcript line(s)"
        )


def _counted_messages(transcript: Path) -> Iterator[tuple[int, JsonObject | None]]:
    """Yield `(line, message)` for each counted message, with None for a bad line.

    Counted messages are assistant messages and `subagent` tool results. Other
    lines are not yielded at all: session headers, model changes and other tool
    results add no usage.
    """
    # A transcript cut off mid-write can end inside a multibyte character. Only numeric
    # usage fields and model names are read, so replacing undecodable bytes changes no column.
    with transcript.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                decoded: object = json.loads(line)
            except json.JSONDecodeError:
                yield line_no, None
                continue
            message = _counted_message(decoded)
            if message is not None:
                yield line_no, message


def _counted_message(decoded: object) -> JsonObject | None:
    """Return the message of an assistant or `subagent` tool-result line, else None.

    An assistant message without a usage object still counts as a turn, with no
    tokens and no context.
    """
    record = as_object(decoded)
    if record is None or opt_str(record.get("type")) != "message":
        return None
    message = as_object(record.get("message"))
    if message is None:
        return None
    role = opt_str(message.get("role"))
    if role == "assistant":
        return message
    if role == "toolResult" and opt_str(message.get("toolName")) == "subagent":
        return message
    return None
