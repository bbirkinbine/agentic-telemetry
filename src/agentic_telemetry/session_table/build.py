"""Build the session table: discover transcripts, read each one, resolve duplicates."""

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import zstandard

from agentic_telemetry.session_table.claude_code import read_claude_code
from agentic_telemetry.session_table.codex import read_codex
from agentic_telemetry.session_table.dedupe import merge_sessions
from agentic_telemetry.session_table.discovery import DiscoveredTranscript, find_transcripts
from agentic_telemetry.session_table.pi import read_pi
from agentic_telemetry.session_table.row import SessionRead, SessionRow
from agentic_telemetry.session_table.summary import SessionSummary


def build_session_table(
    dirs: Sequence[Path], *, require_each: bool = True
) -> tuple[list[SessionRow], SessionSummary]:
    """Read every session transcript under `dirs` into session-table rows.

    Args:
        dirs: Directories to search: client session directories, project roots
            or collector landing copies.
        require_each: Raise when a directory holds no transcript (see
            `find_transcripts`).

    Returns:
        One row per (client, session ID), sorted, and the build's summary.

    Raises:
        NoTranscriptError: `require_each` is set and a directory holds no transcript.
    """
    discovery = find_transcripts(dirs, require_each=require_each)
    reads: list[SessionRead] = []
    warnings: list[str] = list(discovery.warnings)
    for transcript in discovery.transcripts:
        read = _read_transcript(transcript, warnings)
        if read is None:
            continue
        warnings.extend(read.warnings)
        if read.row is None:
            warnings.append(f"{transcript.path}: no session ID found; no row written")
            continue
        reads.append(read)
    rows, duplicates_resolved, duplicate_warnings = merge_sessions(reads)
    warnings.extend(duplicate_warnings)
    summary = SessionSummary(
        files_scanned=discovery.files_scanned,
        sessions_per_client=dict(Counter(row.client for row in rows)),
        unrecognized=discovery.unrecognized,
        sessions_with_tokens=sum(1 for row in rows if _has_tokens(row)),
        duplicates_resolved=duplicates_resolved,
        warnings=warnings,
    )
    return rows, summary


def _read_transcript(transcript: DiscoveredTranscript, warnings: list[str]) -> SessionRead | None:
    """Read one transcript with its client's reader; a read failure becomes a warning."""
    try:
        match transcript.client:
            case "pi":
                return read_pi(transcript.path)
            case "claude-code":
                return read_claude_code(transcript.path)
            case "codex":
                return read_codex(transcript.path)
    except (OSError, zstandard.ZstdError) as error:
        warnings.append(f"{transcript.path}: skipped, could not be read ({type(error).__name__})")
        return None


def _has_tokens(row: SessionRow) -> bool:
    """True when any token column was recorded for the session."""
    tokens = (
        row.tokens_in,
        row.tokens_cache_read,
        row.tokens_cache_write,
        row.tokens_out,
        row.tokens_reasoning,
    )
    return any(value is not None for value in tokens)
