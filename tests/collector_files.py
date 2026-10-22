"""Read-only helpers for the static checks on tracked files under `collector/` (specs 0003, 0007).

Every loader asserts the file exists first, so a missing file is an `AssertionError`
naming the path and the expectation, never a `FileNotFoundError`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_DIR = REPO_ROOT / "collector"
COMPOSE = COLLECTOR_DIR / "compose.yaml"
OTEL_CONFIG = COLLECTOR_DIR / "otel-collector.yaml"
ENV_EXAMPLE = COLLECTOR_DIR / "env.example"
README = COLLECTOR_DIR / "README.md"
# Spec 0007: the scraper agent on the inference host. Paths only; nothing is read at import.
AGENT_DIR = COLLECTOR_DIR / "inference-agent"
AGENT_COMPOSE = AGENT_DIR / "compose.yaml"
AGENT_CONFIG = AGENT_DIR / "otel-agent.yaml"
AGENT_ENV_EXAMPLE = AGENT_DIR / "env.example"
AGENT_TARGETS_EXAMPLE = AGENT_DIR / "targets.example.json"
AGENT_SERVICE = "otel-agent"

# The tracked collector files the hygiene rules scan. Never `landing/` evidence.
HYGIENE_FILES = (
    "collector/compose.yaml",
    "collector/otel-collector.yaml",
    "collector/loki-config.yaml",
    "collector/tempo.yaml",
    "collector/env.example",
    "collector/README.md",
    "collector/landing/README.md",
    "collector/mac-agent/otel-agent.yaml",
    "collector/mac-agent/ship-ledgers.sh",
    "collector/clients/claude-code-user-settings.json",
    "collector/inference-agent/compose.yaml",
    "collector/inference-agent/otel-agent.yaml",
    "collector/inference-agent/env.example",
    "collector/inference-agent/targets.example.json",
)

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def relative(path: Path) -> str:
    """Return `path` relative to the repository root, for assertion messages."""
    return str(path.relative_to(REPO_ROOT))


def read_text(path: Path) -> str:
    """Return the text of `path`, asserting it exists as a regular file."""
    assert path.is_file(), f"expected tracked file {relative(path)} to exist"
    return path.read_text(encoding="utf-8")


def load_yaml_mapping(path: Path) -> dict[str, object]:
    """Parse `path` with `yaml.safe_load`, asserting it holds a mapping at the top."""
    data = yaml.safe_load(read_text(path))
    assert isinstance(data, dict), f"expected {relative(path)} to hold a YAML mapping"
    return data


def compose_services() -> dict[str, dict[str, object]]:
    """Return `services` from `collector/compose.yaml`, keyed by service name."""
    services = load_yaml_mapping(COMPOSE).get("services")
    assert isinstance(services, dict) and services, (
        f"expected {relative(COMPOSE)} to define a non-empty `services` mapping"
    )
    for name, body in services.items():
        assert isinstance(body, dict), f"expected compose service {name!r} to be a mapping"
    return services


def compose_service(name: str) -> dict[str, object]:
    """Return one compose service, asserting it is defined."""
    services = compose_services()
    assert name in services, f"expected compose service {name!r} in {relative(COMPOSE)}"
    return services[name]


def service_environment(service: dict[str, object]) -> dict[str, str]:
    """Normalise a compose `environment` (mapping or `KEY=VALUE` list) to a dict."""
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(key): "" if value is None else str(value) for key, value in raw.items()}
    assert isinstance(raw, list), "expected compose `environment` to be a mapping or a list"
    pairs = (str(item).partition("=") for item in raw)
    return {key: value for key, _, value in pairs}


def as_list(value: object) -> list[object]:
    """Return a compose list field (`ports`, `volumes`, `profiles`) as a list."""
    if value is None:
        return []
    assert isinstance(value, list), f"expected a YAML list, got {value!r}"
    return value


def split_outside_braces(entry: str) -> list[str]:
    """Split a compose short-syntax entry on `:`, ignoring colons inside `${...}`."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(entry):
        if entry.startswith("${", index):
            depth += 1
            current.append("${")
            index += 2
            continue
        char = entry[index]
        if char == "}" and depth:
            depth -= 1
        if char == ":" and not depth:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


def iter_strings(node: object) -> Iterator[str]:
    """Yield every string value (not mapping key) in a parsed YAML tree; comments are gone."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from iter_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_strings(item)


def parse_env_example(path: Path = ENV_EXAMPLE) -> dict[str, str]:
    """Parse an `env.example` into `{NAME: value}`, skipping comments and blanks."""
    values: dict[str, str] = {}
    for line_no, line in enumerate(read_text(path).splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, sep, value = stripped.removeprefix("export ").partition("=")
        assert sep, f"{relative(path)}:{line_no}: expected NAME=value, got {line!r}"
        value = value.strip()
        if value[:1] in {'"', "'"} and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[name.strip()] = value
    return values


def agent_services() -> dict[str, dict[str, object]]:
    """Return `services` from `collector/inference-agent/compose.yaml`, keyed by name."""
    services = load_yaml_mapping(AGENT_COMPOSE).get("services")
    assert isinstance(services, dict) and services, (
        f"expected {relative(AGENT_COMPOSE)} to define a non-empty `services` mapping"
    )
    for name, body in services.items():
        assert isinstance(body, dict), f"expected agent compose service {name!r} to be a mapping"
    return services


def agent_service() -> dict[str, object]:
    """Return the `otel-agent` service, asserting it is defined."""
    services = agent_services()
    assert AGENT_SERVICE in services, (
        f"expected compose service {AGENT_SERVICE!r} in {relative(AGENT_COMPOSE)}"
    )
    return services[AGENT_SERVICE]


def volume_mounts(service: dict[str, object]) -> list[tuple[str, str, str]]:
    """Return `(source, target, mode)` for each short or long-syntax compose volume."""
    mounts: list[tuple[str, str, str]] = []
    for entry in as_list(service.get("volumes")):
        if isinstance(entry, dict):
            mode = "ro" if entry.get("read_only") else ""
            mounts.append((str(entry.get("source") or ""), str(entry.get("target") or ""), mode))
            continue
        parts = split_outside_braces(str(entry))
        source = parts[0]
        target = parts[1] if len(parts) > 1 else ""
        mounts.append((source, target, parts[2] if len(parts) > 2 else ""))
    return mounts


def agent_mount_target(variable: str) -> str:
    """Return the container path the agent mounts from the required `${variable:?...}`."""
    prefix = "${" + variable + ":?"
    targets = [t for s, t, _ in volume_mounts(agent_service()) if s.startswith(prefix)]
    assert len(targets) == 1, (
        f"expected exactly one {AGENT_SERVICE} volume from {prefix}...}}, got {targets}"
    )
    return targets[0]


def _headings(lines: list[str]) -> Iterator[tuple[int, int, str]]:
    """Yield `(line index, level, title)` for each Markdown heading outside code fences."""
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        match = None if in_fence else _HEADING.match(line)
        if match:
            yield index, len(match.group(1)), match.group(2)


def markdown_section(text: str, title_pattern: str) -> str | None:
    """Return the body under the first heading whose title matches `title_pattern`.

    The match is case-insensitive. The body runs to the next heading of the same
    or a higher level. Returns None when no heading matches.
    """
    lines = text.splitlines()
    headings = list(_headings(lines))
    wanted = re.compile(title_pattern, re.IGNORECASE)
    for position, (start, level, title) in enumerate(headings):
        if not wanted.search(title):
            continue
        end = len(lines)
        for later_start, later_level, _ in headings[position + 1 :]:
            if later_level <= level:
                end = later_start
                break
        return "\n".join(lines[start + 1 : end])
    return None


def markdown_tables(text: str) -> list[list[list[str]]]:
    """Return each pipe table in `text` as rows of stripped cells, separator row dropped."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in [*text.splitlines(), ""]:
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                current.append(cells)
            continue
        if current:
            tables.append(current)
            current = []
    return tables
