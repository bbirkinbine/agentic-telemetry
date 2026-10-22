"""Static checks on `collector/compose.yaml`, `otel-collector.yaml` and `env.example` (spec 0003).

Criteria 1 (host values from `.env`), D2 (bind address) and 8 (Linux parity).
No Docker and no network: these read the tracked files only.
"""

from __future__ import annotations

import ipaddress
import re

from collector_files import (
    COMPOSE,
    DOCKERFILE,
    ENV_EXAMPLE,
    OTEL_CONFIG,
    as_list,
    compose_service,
    compose_services,
    dockerfile_image_references,
    image_services,
    iter_strings,
    load_yaml_mapping,
    parse_env_example,
    relative,
    service_environment,
    split_outside_braces,
)

DOC_AND_LOOPBACK_NETS = tuple(
    ipaddress.ip_network(net)
    for net in ("127.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
PINNED_IMAGE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9._/-]*):(?P<tag>[A-Za-z0-9_][A-Za-z0-9._-]*)"
    r"@sha256:(?P<digest>[0-9a-f]{64})$"
)
# Full index digests from the spec's approved plan, checked 2026-10-21.
SPEC_INDEX_DIGESTS = {
    "otel/opentelemetry-collector-contrib:0.163.1": (
        "1175b03003053962012f5a231f5567f66a3e8fdb960c7835c0e48f7aa1555806"
    ),
    "victoriametrics/victoria-metrics:v1.154.1": (
        "c7884f32920dd45a9d86542a717227e63af03e060e53bbf8031e6e6fd0e1e05d"
    ),
    "grafana/loki:3.7.9": "0db216b630ae1f348a1584c67f3cead8b4c1fc839169a39501db664744158e28",
    "grafana/tempo:2.10.10": "12b0820f13ffa10ce956b6e9acc3b03f3cffded99109be6cc09d106fe56e5d38",
}
# Spec 0008: harvester base images, index digests read 2026-10-22 (uv per the review resolutions).
DOCKERFILE_IMAGES = (
    "python:3.12-slim-bookworm@sha256:"
    "6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70",
    "ghcr.io/astral-sh/uv:0.11.33@sha256:"
    "77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7",
)
COMPOSE_VARIABLE = re.compile(r"(?<!\$)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))")
COLLECTOR_ENV_REF = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def all_port_entries() -> list[tuple[str, object]]:
    """Return `(service, entry)` for every `ports:` entry in compose."""
    return [
        (name, entry)
        for name, service in compose_services().items()
        for entry in as_list(service.get("ports"))
    ]


def port_host_ip(entry: object) -> str:
    """Return the host-IP part of a short or long-syntax port entry ('' when bare)."""
    if isinstance(entry, dict):
        return str(entry.get("host_ip") or "")
    parts = split_outside_braces(str(entry))
    return parts[0] if len(parts) >= 3 else ""


def test_every_bind_source_is_relative_or_under_required_telemetry_root() -> None:
    sources: list[tuple[str, str]] = []
    for name, service in compose_services().items():
        for entry in as_list(service.get("volumes")):
            if isinstance(entry, dict):
                sources.append((name, str(entry.get("source") or "")))
            else:
                sources.append((name, split_outside_braces(str(entry))[0]))
    assert sources, f"expected bind mounts in {relative(COMPOSE)}"
    bad = [
        (name, src)
        for name, src in sources
        if not (src.startswith("./") or src.startswith("${TELEMETRY_ROOT:?"))
    ]
    assert not bad, f"bind sources must start with ./ or ${{TELEMETRY_ROOT:?: {bad}"
    assert any(src.startswith("${TELEMETRY_ROOT:?") for _, src in sources), (
        "expected at least one bind source under the required ${TELEMETRY_ROOT:?...}"
    )


def test_otel_collector_environment_passes_required_llama_swap_target() -> None:
    environment = service_environment(compose_service("otel-collector"))
    assert "LLAMA_SWAP_TARGET" in environment, (
        "expected otel-collector.environment to carry LLAMA_SWAP_TARGET"
    )
    assert environment["LLAMA_SWAP_TARGET"].startswith("${LLAMA_SWAP_TARGET:?"), (
        "expected LLAMA_SWAP_TARGET to come from a required ${LLAMA_SWAP_TARGET:?...}, "
        f"got {environment['LLAMA_SWAP_TARGET']!r}"
    )


def test_prometheus_servers_targets_are_only_env_references() -> None:
    receivers = load_yaml_mapping(OTEL_CONFIG).get("receivers")
    assert isinstance(receivers, dict) and "prometheus/servers" in receivers, (
        f"expected a prometheus/servers receiver in {relative(OTEL_CONFIG)}"
    )
    scrape_configs = receivers["prometheus/servers"]["config"]["scrape_configs"]
    targets = [
        str(target)
        for job in scrape_configs
        for static in job.get("static_configs") or []
        for target in static.get("targets") or []
    ]
    assert targets, "expected at least one static scrape target in prometheus/servers"
    literal = [t for t in targets if not re.fullmatch(r"\$\{env:[A-Za-z_][A-Za-z0-9_]*\}", t)]
    assert not literal, f"scrape targets must be ${{env:...}} references only, got {literal}"


def test_every_collector_env_reference_is_passed_by_compose() -> None:
    config = load_yaml_mapping(OTEL_CONFIG)
    referenced = {m for s in iter_strings(config) for m in COLLECTOR_ENV_REF.findall(s)}
    assert referenced, f"expected ${{env:...}} references in {relative(OTEL_CONFIG)}"
    passed = set(service_environment(compose_service("otel-collector")))
    missing = sorted(referenced - passed)
    assert not missing, f"otel-collector.environment does not pass {missing}"


def test_every_compose_variable_is_declared_in_env_example() -> None:
    compose = load_yaml_mapping(COMPOSE)
    used = {
        braced or bare
        for text in iter_strings(compose)
        for braced, bare in COMPOSE_VARIABLE.findall(text)
    }
    assert used, f"expected ${{...}} variables in {relative(COMPOSE)}"
    declared = set(parse_env_example())
    missing = sorted(used - declared)
    assert not missing, f"{relative(ENV_EXAMPLE)} does not declare {missing}"


def test_env_example_llama_swap_target_is_a_documentation_host() -> None:
    values = parse_env_example()
    assert "LLAMA_SWAP_TARGET" in values, f"expected {relative(ENV_EXAMPLE)} to declare it"
    host, sep, port = values["LLAMA_SWAP_TARGET"].rpartition(":")
    host = host if sep else port
    assert host.endswith(".example"), (
        f"expected LLAMA_SWAP_TARGET to be a *.example host, got {values['LLAMA_SWAP_TARGET']!r}"
    )
    assert not sep or port.isdigit(), f"expected a numeric port, got {port!r}"


def test_env_example_telemetry_root_is_not_a_home_path() -> None:
    values = parse_env_example()
    root = values.get("TELEMETRY_ROOT", "")
    assert root, f"expected {relative(ENV_EXAMPLE)} to declare a non-empty TELEMETRY_ROOT"
    assert "/Users/" not in root and "/home/" not in root, f"home path: {root!r}"
    assert not root.startswith("~") and "$HOME" not in root, f"home path: {root!r}"


def test_every_published_port_binds_to_required_telemetry_bind_addr() -> None:
    entries = all_port_entries()
    assert entries, f"expected published ports in {relative(COMPOSE)}"
    bad = [
        (name, entry)
        for name, entry in entries
        if not port_host_ip(entry).startswith("${TELEMETRY_BIND_ADDR:?")
    ]
    assert not bad, f"ports must start with ${{TELEMETRY_BIND_ADDR:?: {bad}"


def test_no_port_is_published_bare_or_on_all_interfaces() -> None:
    entries = all_port_entries()
    assert entries, f"expected published ports in {relative(COMPOSE)}"
    bad = [
        (name, entry)
        for name, entry in entries
        if port_host_ip(entry) in {"", "0.0.0.0", "::", "[::]"}  # noqa: S104
    ]
    assert not bad, f"ports published bare or on all interfaces: {bad}"


def test_env_example_bind_addr_is_loopback_or_documentation_address() -> None:
    values = parse_env_example()
    assert "TELEMETRY_BIND_ADDR" in values, f"expected {relative(ENV_EXAMPLE)} to declare it"
    raw = values["TELEMETRY_BIND_ADDR"]
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as error:
        raise AssertionError(f"TELEMETRY_BIND_ADDR is not an IP address: {raw!r}") from error
    assert any(address in net for net in DOC_AND_LOOPBACK_NETS), (
        f"TELEMETRY_BIND_ADDR must be loopback or RFC 5737, got {raw!r}"
    )


def test_every_image_is_pinned_by_tag_and_index_digest() -> None:
    images = {name: str(svc.get("image", "")) for name, svc in image_services().items()}
    unpinned = {name: image for name, image in images.items() if not PINNED_IMAGE.match(image)}
    assert not unpinned, f"images must be name:tag@sha256:<64 hex>: {unpinned}"


def test_stack_images_pin_the_index_digests_named_in_the_spec() -> None:
    pinned = {
        f"{m['name']}:{m['tag']}": m["digest"]
        for svc in compose_services().values()
        if (m := PINNED_IMAGE.match(str(svc.get("image", ""))))
    }
    for reference, digest in SPEC_INDEX_DIGESTS.items():
        assert pinned.get(reference) == digest, (
            f"expected {reference}@sha256:{digest} in {relative(COMPOSE)}, "
            f"got {pinned.get(reference)!r}"
        )


def test_built_service_dockerfile_pins_the_spec_base_images() -> None:
    built = sorted(set(compose_services()) - set(image_services()))
    assert built == ["captures-pull"], f"expected only captures-pull built here, got {built}"
    references = dockerfile_image_references()
    unpinned = [ref for ref in references if not PINNED_IMAGE.match(ref)]
    assert not unpinned, f"{relative(DOCKERFILE)} images must be name:tag@sha256: {unpinned}"
    assert sorted(references) == sorted(DOCKERFILE_IMAGES), (
        f"expected FROM and COPY --from to pull exactly {DOCKERFILE_IMAGES}, got {references}"
    )


def test_telemetrygen_service_is_smoke_profile_gated_without_ports() -> None:
    service = compose_service("telemetrygen")
    image = str(service.get("image", ""))
    assert image.split("@")[0].rsplit(":", 1)[0].endswith("/telemetrygen"), (
        f"expected the telemetrygen image, got {image!r}"
    )
    assert "smoke" in as_list(service.get("profiles")), (
        "expected telemetrygen under profiles: [smoke] so `up -d` never starts it"
    )
    assert not as_list(service.get("ports")), "expected telemetrygen to publish no ports"


def test_scrape_jobs_cover_the_gateway_and_never_go_through_upstream() -> None:
    """Standing decision D6: scrape the gateway's own /metrics, never /upstream/.

    In llama-swap v264 any request to a loaded model's /upstream/ path resets its
    idle TTL, so scraping there would keep the model loaded. The check covers every
    prometheus receiver and every string under it (metrics_path, relabel rules,
    params), not just metrics_path.
    """
    receivers = load_yaml_mapping(OTEL_CONFIG).get("receivers")
    assert isinstance(receivers, dict) and "prometheus/servers" in receivers, (
        f"expected a prometheus/servers receiver in {relative(OTEL_CONFIG)}"
    )
    jobs = receivers["prometheus/servers"]["config"]["scrape_configs"]
    paths = [str(job.get("metrics_path", "/metrics")) for job in jobs]
    assert "/metrics" in paths, f"expected a job scraping the gateway's own /metrics, got {paths}"
    prometheus = {k: v for k, v in receivers.items() if str(k).startswith("prometheus")}
    upstream = sorted(
        {
            f"{name}: {text}"
            for name, receiver in prometheus.items()
            for text in iter_strings(receiver)
            if "upstream/" in text
        }
    )
    assert not upstream, (
        f"prometheus receivers must not go through llama-swap /upstream/: {upstream}"
    )


def test_compressed_archive_exporters_use_native_zstd_with_rotation() -> None:
    """Standing decision D4: compressed archive files must stay readable with `zstd -dc`.

    Without the feature gate the file exporter writes length-prefixed batches that
    `zstd -d` cannot read; without rotation the native writer is one open stream.
    """
    exporters = load_yaml_mapping(OTEL_CONFIG).get("exporters")
    assert isinstance(exporters, dict), f"expected exporters in {relative(OTEL_CONFIG)}"
    compressed = {
        str(name): config
        for name, config in exporters.items()
        if str(name).split("/")[0] == "file"
        and isinstance(config, dict)
        and config.get("compression")
    }
    assert compressed, "expected compressed file exporters for the archive"
    without_rotation = sorted(
        name for name, config in compressed.items() if not config.get("rotation")
    )
    assert not without_rotation, f"compressed file exporters need rotation: {without_rotation}"
    command = " ".join(
        str(part) for part in as_list(compose_service("otel-collector").get("command"))
    )
    gates = re.findall(r"--feature-gates=(\S+)", command)
    enabled = {gate for value in gates for gate in value.split(",")}
    assert "exporter.file.nativeCompression" in enabled, (
        f"otel-collector command must enable exporter.file.nativeCompression, got {command!r}"
    )
