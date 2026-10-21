"""The session table's row: one client session, summarized from its transcript."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SessionRow:
    """One session-table row. Field order is the Parquet column order (spec 0002)."""

    # Identity
    client: str
    session_id: str
    source_path: str
    project: str | None
    cwd: str | None
    client_version: str | None
    git_branch: str | None
    # Lineage
    is_subagent: bool
    parent_session_id: str | None
    run_id: str | None
    # Time
    started_at: str | None
    ended_at: str | None
    span_seconds: float | None
    # Models
    provider: str | None
    models: tuple[str, ...]
    primary_model: str | None
    # The model that answered, where the client records it: for a local server,
    # the weights behind the profile alias in `models`. Empty for Codex.
    served_models: tuple[str, ...]
    primary_served_model: str | None
    # Usage: null per column when the client recorded none
    tokens_in: int | None
    tokens_cache_read: int | None
    tokens_cache_write: int | None
    tokens_out: int | None
    tokens_reasoning: int | None
    assistant_turns: int | None
    # Activity
    user_prompts: int | None
    tool_calls: int | None
    tool_errors: int | None
    compactions: int | None
    stop_reasons: str | None


@dataclass(frozen=True)
class SessionIdentity:
    """The per-session fields a reader takes from headers and metadata lines."""

    client: str
    session_id: str
    source_path: str
    cwd: str | None
    client_version: str | None = None
    git_branch: str | None = None
    is_subagent: bool = False
    parent_session_id: str | None = None
    run_id: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class SessionRead:
    """What reading one transcript produced.

    `row` is None when the transcript has no session ID. `line_count` counts
    physical lines, so the longer of two copies of a session can be kept.
    """

    row: SessionRow | None
    line_count: int
    warnings: list[str] = field(default_factory=list)
