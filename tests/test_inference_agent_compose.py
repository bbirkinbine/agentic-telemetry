"""Static checks on the inference host agent's compose file and env.example (spec 0007).

Standing decision D2 (Compose, host networking, the stack's pinned Collector image)
and the value-free template the host's untracked `.env` starts from. No Docker and
no network: these read the tracked files only.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from collector_files import (
    AGENT_COMPOSE,
    AGENT_CONFIG,
    AGENT_ENV_EXAMPLE,
    AGENT_SERVICE,
    agent_service,
    agent_services,
    as_list,
    compose_service,
    iter_strings,
    load_yaml_mapping,
    parse_env_example,
    relative,
    service_environment,
    volume_mounts,
)

COMPOSE_VARIABLE = re.compile(r"(?<!\$)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))")
COLLECTOR_ENV_REF = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
REQUIRED_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?([^}]*)\}")
DOC_NETS = tuple(
    ipaddress.ip_network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
AGENT_ENV_KEYS = {"TELEMETRY_OTLP_ENDPOINT", "AGENT_QUEUE_DIR", "AGENT_TARGETS_DIR"}
QUEUE_DIR_ON_HOST = "/var/lib/agentic-telemetry/queue"  # host facts in the spec's Notes
TARGETS_MOUNT = "/etc/otelcol/targets"  # the approved plan's container path


def required_source(source: str, variable: str) -> bool:
    """True when `source` is exactly one required `${variable:?message}` and nothing else."""
    match = REQUIRED_VARIABLE.fullmatch(source)
    return match is not None and match.group(1) == variable


def test_agent_compose_defines_exactly_the_otel_agent_service() -> None:
    services = agent_services()
    assert list(services) == [AGENT_SERVICE], (
        f"expected {relative(AGENT_COMPOSE)} to define only {AGENT_SERVICE!r}, got {list(services)}"
    )


def test_agent_image_is_byte_identical_to_the_stack_collector_image() -> None:
    stack_image = str(compose_service("otel-collector").get("image", ""))
    agent_image = str(agent_service().get("image", ""))
    assert stack_image, "expected the stack's otel-collector to pin an image"
    assert agent_image == stack_image, (
        f"expected {AGENT_SERVICE} to run the stack's pinned Collector image {stack_image!r}, "
        f"got {agent_image!r}"
    )


def test_agent_uses_host_networking_and_publishes_no_ports() -> None:
    service = agent_service()
    assert service.get("network_mode") == "host", (
        f"expected network_mode: host (D2), got {service.get('network_mode')!r}"
    )
    assert not as_list(service.get("ports")), (
        f"expected no ports: under host networking, got {service.get('ports')!r}"
    )


def test_agent_restarts_unless_stopped() -> None:
    restart = agent_service().get("restart")
    assert restart == "unless-stopped", f"expected restart: unless-stopped, got {restart!r}"


def test_every_agent_bind_source_is_relative_or_one_required_variable() -> None:
    mounts = volume_mounts(agent_service())
    assert mounts, f"expected bind mounts in {relative(AGENT_COMPOSE)}"
    bad = [
        source
        for source, _, _ in mounts
        if not (source.startswith("./") or REQUIRED_VARIABLE.fullmatch(source))
    ]
    assert not bad, f"bind sources must start with ./ or be one required ${{X:?...}}: {bad}"


def test_required_variable_messages_carry_no_colon() -> None:
    compose = load_yaml_mapping(AGENT_COMPOSE)
    messages = [m for text in iter_strings(compose) for m in REQUIRED_VARIABLE.findall(text)]
    assert messages, f"expected required ${{X:?message}} variables in {relative(AGENT_COMPOSE)}"
    with_colon = [f"{name}: {message!r}" for name, message in messages if ":" in message]
    assert not with_colon, f"keep colons out of the :? messages: {with_colon}"


def test_agent_config_is_mounted_read_only_where_the_command_points() -> None:
    service = agent_service()
    config_mounts = [(t, m) for s, t, m in volume_mounts(service) if s == "./otel-agent.yaml"]
    assert len(config_mounts) == 1, (
        f"expected one mount of ./otel-agent.yaml, got {volume_mounts(service)}"
    )
    target, mode = config_mounts[0]
    assert mode == "ro", f"expected ./otel-agent.yaml mounted :ro, got mode {mode!r}"
    command = " ".join(str(part) for part in as_list(service.get("command")))
    assert f"--config={target}" in command, (
        f"expected the {AGENT_SERVICE} command to pass --config={target}, got {command!r}"
    )


def test_targets_directory_is_mounted_read_only_as_a_directory() -> None:
    """A single-file bind mount pins the inode, so an atomic rename would never arrive."""
    mounts = [(s, t, m) for s, t, m in volume_mounts(agent_service()) if t == TARGETS_MOUNT]
    assert len(mounts) == 1, f"expected one volume mounted at {TARGETS_MOUNT}, got {mounts}"
    source, _, mode = mounts[0]
    assert required_source(source, "AGENT_TARGETS_DIR"), (
        f"expected the whole ${{AGENT_TARGETS_DIR:?...}} directory as the source, got {source!r}"
    )
    assert mode == "ro", f"expected the targets directory mounted :ro, got mode {mode!r}"


def test_queue_directory_is_mounted_writable_from_the_required_variable() -> None:
    mounts = [
        (s, t, m)
        for s, t, m in volume_mounts(agent_service())
        if s.startswith("${AGENT_QUEUE_DIR:?")
    ]
    assert len(mounts) == 1, f"expected one volume from ${{AGENT_QUEUE_DIR:?...}}, got {mounts}"
    source, target, mode = mounts[0]
    assert required_source(source, "AGENT_QUEUE_DIR"), (
        f"expected the source to be exactly ${{AGENT_QUEUE_DIR:?...}}, got {source!r}"
    )
    assert target.startswith("/"), f"expected an absolute container path, got {target!r}"
    assert mode != "ro", "expected the queue directory to be writable"


def test_every_agent_compose_variable_is_declared_in_agent_env_example() -> None:
    compose = load_yaml_mapping(AGENT_COMPOSE)
    used = {
        braced or bare
        for text in iter_strings(compose)
        for braced, bare in COMPOSE_VARIABLE.findall(text)
    }
    assert used, f"expected ${{...}} variables in {relative(AGENT_COMPOSE)}"
    missing = sorted(used - set(parse_env_example(AGENT_ENV_EXAMPLE)))
    assert not missing, f"{relative(AGENT_ENV_EXAMPLE)} does not declare {missing}"


def test_every_agent_config_env_reference_is_passed_as_a_required_variable() -> None:
    config = load_yaml_mapping(AGENT_CONFIG)
    referenced = {m for s in iter_strings(config) for m in COLLECTOR_ENV_REF.findall(s)}
    assert "TELEMETRY_OTLP_ENDPOINT" in referenced, (
        f"expected ${{env:TELEMETRY_OTLP_ENDPOINT}} in {relative(AGENT_CONFIG)}"
    )
    environment = service_environment(agent_service())
    missing = sorted(referenced - set(environment))
    assert not missing, f"{AGENT_SERVICE}.environment does not pass {missing}"
    optional = sorted(
        name for name in referenced if not required_source(environment[name].strip(), name)
    )
    assert not optional, f"expected each passed value to be ${{NAME:?...}}: {optional}"


def test_agent_env_example_declares_exactly_the_three_agent_keys() -> None:
    keys = set(parse_env_example(AGENT_ENV_EXAMPLE))
    assert keys == AGENT_ENV_KEYS, (
        f"expected {relative(AGENT_ENV_EXAMPLE)} to declare {sorted(AGENT_ENV_KEYS)}, "
        f"got {sorted(keys)}"
    )


def test_agent_env_example_endpoint_is_a_placeholder_otlp_http_url() -> None:
    raw = parse_env_example(AGENT_ENV_EXAMPLE).get("TELEMETRY_OTLP_ENDPOINT", "")
    url = urlsplit(raw)
    assert url.scheme in {"http", "https"}, f"expected an http(s) URL, got {raw!r}"
    host = url.hostname or ""
    try:
        documentation = any(ipaddress.ip_address(host) in net for net in DOC_NETS)
    except ValueError:
        documentation = host.endswith(".example")
    assert documentation, f"expected a *.example or RFC 5737 host, got {raw!r}"
    assert url.port == 4318, f"expected the stack's OTLP HTTP port 4318, got {raw!r}"


def test_agent_env_example_directories_are_absolute_and_not_home_paths() -> None:
    values = parse_env_example(AGENT_ENV_EXAMPLE)
    for name in ("AGENT_QUEUE_DIR", "AGENT_TARGETS_DIR"):
        path = values.get(name, "")
        assert path.startswith("/"), f"expected {name} to be an absolute path, got {path!r}"
        assert "/Users/" not in path and "/home/" not in path, f"{name} home path: {path!r}"
        assert "$HOME" not in path and "~" not in path, f"{name} home path: {path!r}"


def test_agent_env_example_queue_dir_is_the_host_root_filesystem_path() -> None:
    queue = parse_env_example(AGENT_ENV_EXAMPLE).get("AGENT_QUEUE_DIR")
    assert queue == QUEUE_DIR_ON_HOST, (
        f"expected AGENT_QUEUE_DIR={QUEUE_DIR_ON_HOST}, got {queue!r}"
    )
