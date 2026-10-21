"""What a session-table build found, rendered for stdout.

The summary prints counts, file locations and default-source status. It never
prints a session's cwd, project, model or any transcript content.
"""

from dataclasses import dataclass, field
from pathlib import Path

from agentic_telemetry.session_table.classify import CLIENTS
from agentic_telemetry.session_table.sources import DefaultSource


@dataclass
class SessionSummary:
    """Counts and warnings from one session-table build."""

    files_scanned: int = 0
    sessions_per_client: dict[str, int] = field(default_factory=dict)
    unrecognized: int = 0
    sessions_with_tokens: int = 0
    duplicates_resolved: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def rows_written(self) -> int:
        """Total sessions across clients."""
        return sum(self.sessions_per_client.values())


def render_summary(summary: SessionSummary) -> str:
    """Return the multi-line stdout summary of a build."""
    lines = [f"files scanned: {summary.files_scanned}"]
    lines.append(f"sessions written: {summary.rows_written}")
    lines.extend(f"  {client}: {summary.sessions_per_client.get(client, 0)}" for client in CLIENTS)
    lines.append(f"unrecognized files skipped: {summary.unrecognized}")
    lines.append(f"sessions with token totals: {summary.sessions_with_tokens}")
    lines.append(f"duplicate sessions resolved: {summary.duplicates_resolved}")
    warnings = list(dict.fromkeys(summary.warnings))
    lines.append(f"warnings: {len(warnings)}")
    lines.extend(f"  - {warning}" for warning in warnings)
    return "\n".join(lines)


def render_source_line(source: DefaultSource, home: Path | None = None) -> str:
    """Return one default-source status line, e.g. `default source pi: found ~/.pi/...`."""
    status = "found" if source.found else "not found"
    return f"default source {source.client}: {status} {_tilde(source.path, home)}"


def _tilde(path: Path, home: Path | None) -> str:
    """Show `path` with the home directory written as `~`."""
    if home is not None and path.is_relative_to(home):
        return str(Path("~") / path.relative_to(home))
    return str(path)
