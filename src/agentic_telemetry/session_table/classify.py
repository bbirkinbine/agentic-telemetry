"""Decide which client wrote a transcript by reading the head of the file.

Classification is by content, not path, so inbox copies and renamed folders
classify the same way as the clients' own session directories.
"""

from contextlib import closing
from pathlib import Path
from typing import Literal

from agentic_telemetry.run_table.json_values import JsonObject, opt_str
from agentic_telemetry.session_table.jsonl_lines import JsonlLines

Client = Literal["pi", "claude-code", "codex"]
CLIENTS: tuple[Client, ...] = ("pi", "claude-code", "codex")

# A Codex rollout opens with `session_meta`; a Claude Code transcript can open
# with dozens of bookkeeping lines (snapshots, modes, titles) before a turn.
CODEX_HEAD_LINES = 20
CLAUDE_CODE_HEAD_LINES = 50


def classify_transcript(path: Path) -> Client | None:
    """Return the client that wrote `path`, or None when it is not a session transcript.

    Rules, checked in this order:
    - Pi: the first parseable line is `type: "session"` with a `version`.
    - Codex: a `session_meta` line within the first 20 lines.
    - Claude Code: a `user` or `assistant` line with a `sessionId` within the
      first 50 lines.

    Raises:
        OSError: The file cannot be read.
        zstandard.ZstdError: A `.zst` file is not valid zstandard data.
    """
    first_object = True
    # closing() shuts the file as soon as the head has been read.
    with closing(JsonlLines(path).__iter__()) as lines:
        for line_no, record in lines:
            if line_no > CLAUDE_CODE_HEAD_LINES:
                return None
            if first_object and _is_pi_header(record):
                return "pi"
            first_object = False
            line_type = opt_str(record.get("type"))
            if line_type == "session_meta" and line_no <= CODEX_HEAD_LINES:
                return "codex"
            if line_type in ("user", "assistant") and opt_str(record.get("sessionId")):
                return "claude-code"
    return None


def _is_pi_header(record: JsonObject) -> bool:
    """Return True for a Pi session header line."""
    return record.get("type") == "session" and "version" in record
