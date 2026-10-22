"""Static checks on the `captures-pull` harvester service (spec 0008).

Criteria "Harvester" (a long-running compose service built from a digest-pinned
Python base plus this package) and "Never in tracked files or Loki" (no ports,
files under the data root, no Collector tail, key only from `.env`). Split from
`test_collector_compose.py` to keep each file under 300 lines. No Docker and no
network: these read the tracked files only.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from collector_files import (
    COLLECTOR_DIR,
    DOCKERFILE,
    DOCKERIGNORE,
    ENV_EXAMPLE,
    HYGIENE_FILES,
    OTEL_CONFIG,
    REPO_ROOT,
    as_list,
    compose_service,
    load_yaml_mapping,
    parse_env_example,
    read_text,
    relative,
    service_environment,
    volume_mounts,
)

SERVICE = "captures-pull"
CAPTURE_PATHS = ("/captures/gpu/2026-10-22.jsonl.zst", "/landing/captures/gpu/2026-10-22.jsonl.zst")


def harvester() -> dict[str, object]:
    """Return the `captures-pull` compose service, asserting it exists."""
    return compose_service(SERVICE)


def command_text() -> str:
    """Return the service command as one string, list or string form."""
    command = harvester().get("command")
    return " ".join(str(part) for part in command) if isinstance(command, list) else str(command)


def test_harvester_is_built_from_the_repo_root_with_the_captures_dockerfile() -> None:
    service = harvester()
    assert "image" not in service, "captures-pull is built here, so it names no image"
    build = service.get("build")
    assert isinstance(build, dict), f"expected {SERVICE}.build to be a mapping with a dockerfile"
    context = (COLLECTOR_DIR / str(build.get("context", "."))).resolve()
    assert context == REPO_ROOT, f"expected the build context to be the repo root, got {context}"
    dockerfile = (context / str(build.get("dockerfile", "Dockerfile"))).resolve()
    assert dockerfile == DOCKERFILE, f"expected {relative(DOCKERFILE)}, got {dockerfile}"


def test_harvester_restarts_unless_stopped_with_init_and_publishes_no_ports() -> None:
    service = harvester()
    assert service.get("restart") == "unless-stopped"
    assert service.get("init") is True
    assert not as_list(service.get("ports")), f"{SERVICE} must publish no ports"


def test_harvester_mounts_only_the_captures_directory_under_the_required_root() -> None:
    mounts = volume_mounts(harvester())
    assert len(mounts) == 1, f"expected one {SERVICE} volume, got {mounts}"
    source, target, _ = mounts[0]
    assert source.startswith("${TELEMETRY_ROOT:?") and source.endswith("}/captures"), source
    assert target == "/captures"


def test_harvester_command_polls_the_required_target_into_the_host_label() -> None:
    text = command_text()
    assert "captures-pull" in text, f"expected the captures-pull subcommand, got {text!r}"
    assert "http://${LLAMA_SWAP_TARGET:?" in text, text
    # Compose interpolates profiled-out services too, so the label cannot be `:?`
    # (spec 0008 review resolutions); the harvester refuses an empty one.
    assert "--host-label=${CAPTURES_HOST_LABEL:-}" in text, text
    assert re.search(r"--out[ =]/captures\b", text), text
    assert "--once" not in text, "the service runs forever"


def test_harvester_runs_only_under_the_captures_profile() -> None:
    assert harvester().get("profiles") == ["captures"]
    assert parse_env_example().get("COMPOSE_PROFILES") == "captures"


def test_harvester_takes_the_api_key_only_from_an_optional_env_variable() -> None:
    environment = service_environment(harvester())
    assert environment.get("LLAMA_SWAP_API_KEY") == "${LLAMA_SWAP_API_KEY:-}"
    assert "LLAMA_SWAP_API_KEY" not in command_text()


def test_otel_collector_has_no_captures_mount() -> None:
    mounts = volume_mounts(compose_service("otel-collector"))
    hits = [m for m in mounts if "captures" in m[0] or "captures" in m[1]]
    assert not hits, f"the Collector must never see capture files: {hits}"


def test_no_filelog_receiver_tails_capture_files() -> None:
    receivers = load_yaml_mapping(OTEL_CONFIG).get("receivers")
    assert isinstance(receivers, dict), f"expected receivers in {relative(OTEL_CONFIG)}"
    globs = [
        str(pattern)
        for name, body in receivers.items()
        if str(name).split("/")[0] == "filelog" and isinstance(body, dict)
        for pattern in body.get("include") or []
    ]
    assert globs, "expected the filelog receivers' include globs"
    assert not [g for g in globs if "captures" in g], globs
    tailed = [p for p in CAPTURE_PATHS for g in globs if PurePosixPath(p).match(g)]
    assert not tailed, f"filelog include matches capture files: {tailed}"


def dockerignore_patterns() -> list[str]:
    """Return the non-comment, non-blank `.dockerignore` lines."""
    lines = read_text(DOCKERIGNORE).splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def test_dockerignore_allowlists_only_the_package_sources() -> None:
    patterns = dockerignore_patterns()
    assert patterns and patterns[0] == "*", f"expected {relative(DOCKERIGNORE)} to start with *"
    allowed = {p.removeprefix("!").removesuffix("/**").removesuffix("/") for p in patterns[1:]}
    assert all(p.startswith("!") for p in patterns[1:]), patterns
    assert allowed == {"pyproject.toml", "uv.lock", "src"}, allowed


def test_dockerignore_never_lets_an_env_file_into_the_context() -> None:
    reincluded = [p for p in dockerignore_patterns() if p.startswith("!") and ".env" in p]
    assert not reincluded, f"{relative(DOCKERIGNORE)} re-includes env files: {reincluded}"


def test_dockerfile_installs_the_locked_package_as_an_unprivileged_user() -> None:
    text = read_text(DOCKERFILE)
    run_lines = " ".join(line for line in text.splitlines() if "uv sync" in line)
    for flag in ("--locked", "--no-dev", "--no-editable"):
        assert flag in run_lines, f"expected `uv sync {flag}` in {relative(DOCKERFILE)}"
    assert re.search(r"^USER\s+10001\b", text, re.MULTILINE), "expected USER 10001"
    assert re.search(r"PYTHONUNBUFFERED[= ]1\b", text), "expected PYTHONUNBUFFERED=1"


def test_dockerfile_is_in_the_hygiene_scan() -> None:
    assert relative(DOCKERFILE) in HYGIENE_FILES


def test_env_example_declares_the_host_label_and_an_empty_api_key() -> None:
    values = parse_env_example()
    label = values.get("CAPTURES_HOST_LABEL", "")
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", label), (
        f"expected {relative(ENV_EXAMPLE)} to set a plain CAPTURES_HOST_LABEL, got {label!r}"
    )
    assert "LLAMA_SWAP_API_KEY" in values, f"expected {relative(ENV_EXAMPLE)} to declare it"
    assert values["LLAMA_SWAP_API_KEY"] == "", "the example key must stay empty"
