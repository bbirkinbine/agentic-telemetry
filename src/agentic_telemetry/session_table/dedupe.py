"""Resolve two transcripts that hold the same (client, session ID).

A transcript grows while its session runs, so a shorter copy (a lagged landing
copy, or a transcript Claude Code set aside) is a stale prefix, not a
conflict. The longer file is kept, a warning names both paths, and the build
never stops for it.
"""

from collections.abc import Sequence

from agentic_telemetry.session_table.row import SessionRead, SessionRow


def merge_sessions(reads: Sequence[SessionRead]) -> tuple[list[SessionRow], int, list[str]]:
    """Keep one row per (client, session ID).

    The file with more physical lines wins; on a tie, the path that sorts first.

    Args:
        reads: Transcript reads that produced a row; reads without one are ignored.

    Returns:
        The kept rows sorted by (client, session ID), how many duplicates were
        dropped, and one warning per dropped duplicate.
    """
    kept: dict[tuple[str, str], SessionRead] = {}
    warnings: list[str] = []
    resolved = 0
    for read in reads:
        if read.row is None:
            continue
        key = (read.row.client, read.row.session_id)
        current = kept.get(key)
        if current is None:
            kept[key] = read
            continue
        winner, loser = _winner_and_loser(current, read)
        kept[key] = winner
        resolved += 1
        warnings.append(_duplicate_warning(key, winner, loser))
    rows = [read.row for _, read in sorted(kept.items()) if read.row is not None]
    return rows, resolved, warnings


def _winner_and_loser(first: SessionRead, second: SessionRead) -> tuple[SessionRead, SessionRead]:
    """Return the read to keep and the one to drop."""
    ranked = sorted((first, second), key=lambda read: (-read.line_count, _source_path(read)))
    return ranked[0], ranked[1]


def _source_path(read: SessionRead) -> str:
    """Return the transcript path behind a read."""
    return read.row.source_path if read.row is not None else ""


def _duplicate_warning(key: tuple[str, str], winner: SessionRead, loser: SessionRead) -> str:
    """Describe a resolved duplicate, naming both files."""
    client, session_id = key
    return (
        f"duplicate {client} session {session_id}: kept {_source_path(winner)} "
        f"({winner.line_count} lines) over {_source_path(loser)} ({loser.line_count} lines)"
    )
