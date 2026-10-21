"""Find client session transcripts under the directories to read.

Every `*.jsonl` and `*.jsonl.zst` file is a candidate; `classify_transcript`
decides by content whether it is a Pi, Claude Code or Codex session. Anything
else (Codex `history.jsonl`, delegate ledgers) is counted as unrecognized. A
file or directory that cannot be read is skipped with a warning naming it.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import zstandard

from agentic_telemetry.session_table.classify import Client, classify_transcript

TRANSCRIPT_SUFFIXES = (".jsonl", ".jsonl.zst")
# Large trees that hold no session transcript, plus a Pi subagent extension's
# mirror copies of child sessions that are read from their own files.
SKIPPED_DIR_NAMES = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", "subagent-artifacts"}
)


class NoTranscriptError(Exception):
    """An input directory holds no recognized session transcript."""

    def __init__(self, directory: Path, warnings: Sequence[str] = ()) -> None:
        """Record the directory that had no transcript and any read warnings so far."""
        self.directory = directory
        self.warnings = list(warnings)
        super().__init__(
            f"no session transcript under {directory}: expected Pi, Claude Code or Codex "
            "*.jsonl / *.jsonl.zst files"
        )


@dataclass(frozen=True)
class DiscoveredTranscript:
    """One transcript file and the client that wrote it."""

    path: Path
    client: Client


@dataclass(frozen=True)
class Discovery:
    """What a search found: transcripts, and how many candidate files it looked at."""

    transcripts: list[DiscoveredTranscript]
    files_scanned: int
    unrecognized: int
    warnings: list[str] = field(default_factory=list)


def find_transcripts(dirs: Sequence[Path], *, require_each: bool = True) -> Discovery:
    """Find every session transcript under `dirs`.

    A file reached through overlapping directories is counted and returned once.

    Args:
        dirs: Directories to search recursively; symlinked directories are not followed.
        require_each: Raise when a directory holds no transcript. Default client
            locations pass False: an existing but empty session directory is not
            an error.

    Returns:
        Transcripts in the order of `dirs`, then sorted by path, with absolute paths.

    Raises:
        NoTranscriptError: `require_each` is set and a directory in `dirs` holds no
            recognized transcript.
    """
    transcripts: list[DiscoveredTranscript] = []
    warnings: list[str] = []
    clients: dict[Path, Client | None] = {}
    unrecognized = 0
    for directory in dirs:
        found_here = False
        for candidate in _candidates_under(directory, warnings):
            # realpath, unlike resolve() on Python 3.12, never raises on a symlink loop.
            resolved = Path(os.path.realpath(candidate))
            if resolved not in clients:
                try:
                    client = classify_transcript(candidate)
                except (OSError, zstandard.ZstdError) as error:
                    warnings.append(_unreadable_warning(candidate, error))
                    client = None
                else:
                    if client is None:
                        unrecognized += 1
                    else:
                        transcripts.append(DiscoveredTranscript(candidate, client))
                clients[resolved] = client
            found_here = found_here or clients[resolved] is not None
        if require_each and not found_here:
            raise NoTranscriptError(directory, warnings)
    return Discovery(transcripts, len(clients), unrecognized, warnings)


def _unreadable_warning(path: Path, error: Exception) -> str:
    """Describe a file that could not be read, by error type only."""
    return f"{path}: skipped, could not be read ({type(error).__name__})"


def delegate_run_id(path: Path) -> str | None:
    """Return the delegate run ID for a transcript at `.../results/<run>/session/<file>`.

    Returns:
        The `<run>` directory name, or None for any other location.
    """
    session_dir = path.parent
    run_dir = session_dir.parent
    if session_dir.name == "session" and run_dir.parent.name == "results" and run_dir.name:
        return run_dir.name
    return None


def _candidates_under(directory: Path, warnings: list[str]) -> list[Path]:
    """Return the `*.jsonl` and `*.jsonl.zst` files under one directory, sorted.

    A subdirectory that cannot be listed adds a warning to `warnings`.
    """
    candidates: list[Path] = []

    def warn_unlistable(error: OSError) -> None:
        warnings.append(_unreadable_warning(Path(error.filename or directory), error))

    # abspath collapses `.` and `..` textually; resolve() would follow symlinks.
    walk_root = os.path.abspath(directory)  # noqa: PTH100
    for dirpath, dirnames, filenames in os.walk(
        walk_root, onerror=warn_unlistable, followlinks=False
    ):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIR_NAMES)
        candidates.extend(
            Path(dirpath) / name for name in filenames if name.endswith(TRANSCRIPT_SUFFIXES)
        )
    return sorted(candidates)
