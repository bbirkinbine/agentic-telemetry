"""Token totals, model names and assistant turns from a run's Pi session transcripts."""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from agentic_telemetry.run_table.json_values import JsonObject, as_object, opt_int, opt_str

# The usage keys summed into their own columns. `cacheWrite` and `totalTokens`
# are not summed into any column (spec 0001).
_SUMMED_USAGE_KEYS = ("input", "output", "cacheRead", "reasoning")

# The usage keys that make up one turn's context window.
_CONTEXT_KEYS = ("input", "cacheRead", "cacheWrite")


@dataclass(frozen=True)
class SessionUsage:
    """Usage summed over every assistant message in a run's transcripts.

    `requested_models` and `served_models` are the distinct `model` and
    `responseModel` names of those messages, in first-seen order across the
    transcripts sorted by name. Pi writes `responseModel` only when the server
    answered with a name other than the one requested, so an empty
    `served_models` means the run recorded no served name — not that the server
    answered wrongly.

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
    warnings: tuple[str, ...] = ()


def read_session_usage(session_dir: Path) -> SessionUsage | None:
    """Read the assistant messages in every `*.jsonl` under `session_dir`.

    Args:
        session_dir: A run's `session/` directory.

    Returns:
        The totals, model names and peak turn context, or None when the directory
        is missing or holds no transcript.
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
    warnings: list[str] = field(default_factory=list)

    def add_turn(self, message: JsonObject) -> None:
        """Count one assistant message: its usage, its model names, its context."""
        usage = as_object(message.get("usage")) or {}
        for key in self.totals:
            self.totals[key] += opt_int(usage.get(key)) or 0
        self.turns += 1
        self._record_name(self.requested_models, message, "model")
        self._record_name(self.served_models, message, "responseModel")
        context = sum(opt_int(usage.get(key)) or 0 for key in _CONTEXT_KEYS)
        if self.peak_turn_prompt_tokens is None or context > self.peak_turn_prompt_tokens:
            self.peak_turn_prompt_tokens = context

    def usage(self) -> SessionUsage:
        """Return the accumulated columns."""
        return SessionUsage(
            tokens_in=self.totals["input"],
            tokens_out=self.totals["output"],
            tokens_cache_read=self.totals["cacheRead"],
            tokens_reasoning=self.totals["reasoning"],
            assistant_turns=self.turns,
            requested_models=tuple(self.requested_models),
            served_models=tuple(self.served_models),
            peak_turn_prompt_tokens=self.peak_turn_prompt_tokens,
            warnings=tuple(self.warnings),
        )

    def _record_name(self, seen: dict[str, None], message: JsonObject, key: str) -> None:
        """Add `message`'s value at `key` to `seen`, keeping first-seen order."""
        name = opt_str(message.get(key))
        if name is not None:
            seen.setdefault(name, None)


def _add_transcript(transcript: Path, tally: _Tally) -> None:
    """Add one transcript's assistant messages to `tally`.

    Malformed lines are skipped; the file gets one warning with the first bad line
    number and the count, since a truncated transcript can have many.
    """
    bad_lines = 0
    first_bad_line = 0
    for line_no, message in _assistant_messages(transcript):
        if message is None:
            bad_lines += 1
            first_bad_line = first_bad_line or line_no
            continue
        tally.add_turn(message)
    if bad_lines:
        tally.warnings.append(
            f"{transcript}:{first_bad_line}: skipped {bad_lines} malformed transcript line(s)"
        )


def _assistant_messages(transcript: Path) -> Iterator[tuple[int, JsonObject | None]]:
    """Yield `(line, assistant message)` for each line, with None for a bad line.

    Lines that are neither an assistant message nor malformed are not yielded at
    all: a transcript carries session headers, model changes and tool results, and
    none of them is a turn.
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
            message = _assistant_message(decoded)
            if message is not None:
                yield line_no, message


def _assistant_message(decoded: object) -> JsonObject | None:
    """Return the message of an assistant message line, or None for any other line.

    An assistant message without a usage object still counts as a turn, with no
    tokens and no context.
    """
    record = as_object(decoded)
    if record is None or opt_str(record.get("type")) != "message":
        return None
    message = as_object(record.get("message"))
    if message is None or opt_str(message.get("role")) != "assistant":
        return None
    return message
