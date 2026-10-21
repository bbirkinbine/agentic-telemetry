"""Find delegate ledgers under the directories given on the command line.

A ledger counts when it is named `ledger.jsonl` and either sits in a directory
named `delegate` (a bootstrapped project's `.agentic/delegate/`) or next to a
`results/` directory (a collector landing copy that flattens that path).
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

LEDGER_NAME = "ledger.jsonl"

# Directories that never hold a delegate ledger but can be large in a project tree.
SKIPPED_DIR_NAMES = frozenset({".git", "node_modules", ".venv", "__pycache__"})


class NoLedgerError(Exception):
    """An input directory holds no delegate ledger."""

    def __init__(self, directory: Path) -> None:
        """Record the directory that had no ledger."""
        self.directory = directory
        super().__init__(
            f"no delegate ledger under {directory}: expected a {LEDGER_NAME} inside a "
            "'delegate' directory or next to a 'results/' directory"
        )


@dataclass(frozen=True)
class DiscoveredLedger:
    """One delegate ledger and where its per-run result directories live."""

    path: Path
    project: str
    results_dir: Path | None


def project_name(ledger_path: Path) -> str:
    """Return the project a ledger belongs to.

    Args:
        ledger_path: Path of a `ledger.jsonl` file.

    Returns:
        The name of the directory that contains the nearest `.agentic` ancestor,
        or the ledger's parent directory name when there is no `.agentic` ancestor.
    """
    for ancestor in ledger_path.parents:
        if ancestor.name == ".agentic":
            return ancestor.parent.name
    return ledger_path.parent.name


def find_ledgers(dirs: Sequence[Path]) -> list[DiscoveredLedger]:
    """Find every delegate ledger under `dirs`, in argument order.

    A ledger reached through overlapping directories is returned once.

    Args:
        dirs: Directories to search recursively.

    Returns:
        The ledgers found, in the order of `dirs` and then of a sorted walk.

    Raises:
        NoLedgerError: A directory in `dirs` holds no delegate ledger.
    """
    found: list[DiscoveredLedger] = []
    seen: set[Path] = set()
    for directory in dirs:
        ledgers = _ledgers_under(directory)
        if not ledgers:
            raise NoLedgerError(directory)
        for ledger in ledgers:
            resolved = ledger.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            results_dir = ledger.parent / "results"
            found.append(
                DiscoveredLedger(
                    path=ledger,
                    project=project_name(ledger),
                    results_dir=results_dir if results_dir.is_dir() else None,
                )
            )
    return found


def _ledgers_under(directory: Path) -> list[Path]:
    """Return the accepted ledgers under one directory, without descending into them."""
    ledgers: list[Path] = []
    # abspath collapses `.` and `..` textually so they name the project; unlike
    # resolve() it keeps symlinked names as given.
    walk_root = os.path.abspath(directory)  # noqa: PTH100 - resolve() would follow symlinks
    for dirpath, dirnames, filenames in os.walk(walk_root, followlinks=False):
        here = Path(dirpath)
        if LEDGER_NAME in filenames and _is_delegate_dir(here):
            ledgers.append(here / LEDGER_NAME)
            # A delegate directory's children are results and caches, never ledgers.
            dirnames.clear()
            continue
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIR_NAMES)
    return ledgers


def _is_delegate_dir(directory: Path) -> bool:
    """True when a directory holding `ledger.jsonl` looks like a delegate directory."""
    return directory.name == "delegate" or (directory / "results").is_dir()
