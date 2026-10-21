"""Where each client keeps its session transcripts when no directory is given.

Locations and overrides were retrieved on 2026-10-21 from each client's docs
(see spec 0002, External references):
- Pi 1.3.0 (MIT), `docs/environment-variables.md`, `docs/session-format.md`:
  `PI_CODING_AGENT_SESSION_DIR`, else `$PI_CODING_AGENT_DIR/sessions`, default
  agent directory `~/.pi/agent`.
- Claude Code, https://code.claude.com/docs/en/env-vars and
  https://code.claude.com/docs/en/claude-directory: `CLAUDE_CONFIG_DIR`, default
  `~/.claude`, transcripts under `projects/`.
- Codex, https://learn.chatgpt.com/docs/config-file/config-advanced: `CODEX_HOME`,
  default `~/.codex`. The `sessions/` subdirectory is observed on disk, not
  documented there.

Pi's docs are MIT-licensed. The Claude Code and Codex pages are proprietary
documentation; only facts (environment variable names and default paths) are
used here, and no text is copied.

Paths are built from the arguments at run time, so no home directory is written
into the code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentic_telemetry.session_table.classify import Client


@dataclass(frozen=True)
class DefaultSource:
    """One client's default session directory and whether it exists."""

    client: Client
    path: Path
    found: bool


def default_sources(environ: Mapping[str, str], home: Path) -> list[DefaultSource]:
    """Return each client's session directory after applying its environment override.

    Args:
        environ: The environment to read overrides from, e.g. `os.environ`.
        home: The user's home directory, e.g. `Path.home()`.

    Returns:
        One source per client (Pi, Claude Code, Codex). `found` is True only for
        an existing directory.
    """
    locations: list[tuple[Client, Path]] = [
        ("pi", _pi_sessions(environ, home)),
        (
            "claude-code",
            _override(environ, "CLAUDE_CONFIG_DIR", home / ".claude", home) / "projects",
        ),
        ("codex", _override(environ, "CODEX_HOME", home / ".codex", home) / "sessions"),
    ]
    return [DefaultSource(client, path, path.is_dir()) for client, path in locations]


def _pi_sessions(environ: Mapping[str, str], home: Path) -> Path:
    """Return Pi's session directory: the session override, else the agent directory's."""
    session_dir = environ.get("PI_CODING_AGENT_SESSION_DIR")
    if session_dir:
        return _expand_home(session_dir, home)
    return _override(environ, "PI_CODING_AGENT_DIR", home / ".pi" / "agent", home) / "sessions"


def _override(environ: Mapping[str, str], name: str, default: Path, home: Path) -> Path:
    """Return the directory named by `environ[name]`, or `default` when unset or empty."""
    value = environ.get(name)
    return _expand_home(value, home) if value else default


def _expand_home(value: str, home: Path) -> Path:
    """Expand a leading `~` against `home`, as Pi does for its overrides.

    A `~` left in place would name a directory under the working directory.
    """
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value)
