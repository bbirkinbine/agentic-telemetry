"""Token totals and assistant turns from a run's Pi session transcripts."""

import json
from dataclasses import dataclass
from pathlib import Path

from agentic_telemetry.run_table.json_values import as_object, opt_int, opt_str


@dataclass(frozen=True)
class SessionUsage:
    """Usage summed over every assistant message in a run's transcripts."""

    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    tokens_reasoning: int
    assistant_turns: int
    warnings: tuple[str, ...] = ()


def read_session_usage(session_dir: Path) -> SessionUsage | None:
    """Sum `usage` over the assistant messages in every `*.jsonl` under `session_dir`.

    Input, output, cache-read and reasoning tokens are summed separately.
    `cacheWrite` and `totalTokens` are not added to any column.

    Args:
        session_dir: A run's `session/` directory.

    Returns:
        The totals, or None when the directory is missing or holds no transcript.
    """
    if not session_dir.is_dir():
        return None
    transcripts = sorted(path for path in session_dir.glob("*.jsonl") if path.is_file())
    if not transcripts:
        return None
    totals = {"input": 0, "output": 0, "cacheRead": 0, "reasoning": 0}
    turns = 0
    warnings: list[str] = []
    for transcript in transcripts:
        turns += _add_transcript(transcript, totals, warnings)
    return SessionUsage(
        tokens_in=totals["input"],
        tokens_out=totals["output"],
        tokens_cache_read=totals["cacheRead"],
        tokens_reasoning=totals["reasoning"],
        assistant_turns=turns,
        warnings=tuple(warnings),
    )


def _add_transcript(transcript: Path, totals: dict[str, int], warnings: list[str]) -> int:
    """Add one transcript's assistant usage to `totals` and return its assistant turns.

    Malformed lines are skipped; the file gets one warning with the first bad line
    number and the count, since a truncated transcript can have many.
    """
    turns = 0
    bad_lines = 0
    first_bad_line = 0
    # A transcript cut off mid-write can end inside a multibyte character. Only numeric
    # usage fields are read, so replacing undecodable bytes changes no column.
    with transcript.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                decoded: object = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                first_bad_line = first_bad_line or line_no
                continue
            usage = _assistant_usage(decoded)
            if usage is None:
                continue
            turns += 1
            for key in totals:
                totals[key] += opt_int(usage.get(key)) or 0
    if bad_lines:
        warnings.append(
            f"{transcript}:{first_bad_line}: skipped {bad_lines} malformed transcript line(s)"
        )
    return turns


def _assistant_usage(decoded: object) -> dict[str, object] | None:
    """Return the usage object of an assistant message line, or None for any other line.

    An assistant message without a usage object still counts as a turn, with no tokens.
    """
    record = as_object(decoded)
    if record is None or opt_str(record.get("type")) != "message":
        return None
    message = as_object(record.get("message"))
    if message is None or opt_str(message.get("role")) != "assistant":
        return None
    return as_object(message.get("usage")) or {}
