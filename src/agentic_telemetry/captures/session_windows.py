"""Session-table sessions as time windows that captured calls can be joined to."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

COLUMNS = (
    "session_id",
    "parent_session_id",
    "run_id",
    "is_subagent",
    "client",
    "started_at",
    "ended_at",
    "models",
    "served_models",
    "project",
    "source_path",
)


class SessionTableError(ValueError):
    """The session table cannot be read, or lacks a column the join needs."""


@dataclass(frozen=True)
class SessionWindow:
    """One session's identity, family, time span and model names."""

    session_id: str
    parent_session_id: str | None
    run_id: str | None
    is_subagent: bool
    client: str
    start: datetime | None
    end: datetime | None
    models: tuple[str, ...]
    served_models: tuple[str, ...]
    project: str | None = None
    source_path: str | None = None

    def delegate_checkout(self) -> str | None:
        """Return the name of the checkout holding this delegate run's results, or None.

        A delegate transcript sits at `<checkout>/.agentic/delegate/results/<run>/session/`
        (or a landing-zone copy of that path); the orchestrating session's `project` is
        that checkout's directory name.
        """
        if self.run_id is None or self.source_path is None:
            return None
        parts = self.source_path.replace("\\", "/").split("/")
        for index in range(1, len(parts) - 2):
            if parts[index : index + 3] == [".agentic", "delegate", "results"]:
                return parts[index - 1] or None
        return None


def _instant(value: object) -> datetime | None:
    """Return a stored session timestamp as aware UTC, or None when absent or unreadable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _names(value: object) -> tuple[str, ...]:
    """Return a VARCHAR[] column as a tuple of names."""
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def read_session_windows(path: Path) -> list[SessionWindow]:
    """Return every session of a session-table Parquet file as a window.

    Raises:
        SessionTableError: The file is missing, unreadable, or lacks a needed column.
    """
    if not path.is_file():
        raise SessionTableError(f"session table not found: {path}")
    try:
        with duckdb.connect() as connection:
            relation = connection.read_parquet(str(path))
            missing = [column for column in COLUMNS if column not in relation.columns]
            if missing:
                raise SessionTableError(f"{path}: missing columns {', '.join(missing)}")
            rows = relation.select(*COLUMNS).fetchall()
    except duckdb.Error as error:
        raise SessionTableError(f"{path}: cannot be read as Parquet ({error})") from error
    return [
        SessionWindow(
            session_id=str(row[0]),
            parent_session_id=row[1],
            run_id=row[2],
            is_subagent=bool(row[3]),
            client=str(row[4]),
            start=_instant(row[5]),
            end=_instant(row[6]),
            models=_names(row[7]),
            served_models=_names(row[8]),
            project=row[9],
            source_path=row[10],
        )
        for row in rows
    ]
