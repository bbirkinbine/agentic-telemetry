"""Static checks on the inference host agent's Collector config (spec 0007).

Targets come only from the file_sd directory (D3); samples go over OTLP to the
stack's Collector through a disk-backed queue that never gives up; nothing listens
off loopback under host networking (D2); nothing goes through llama-swap's upstream
route (0003 D6). Comments are gone after parsing, so only live values are checked.
"""

from __future__ import annotations

import json
import re

from collector_files import (
    AGENT_COMPOSE,
    AGENT_CONFIG,
    AGENT_TARGETS_EXAMPLE,
    OTEL_CONFIG,
    agent_mount_target,
    iter_strings,
    load_yaml_mapping,
    read_text,
    relative,
)

STACK_JOB_RECEIVER = "prometheus/servers"
AGENT_JOB_NAME = "inference-models"  # the service_name the plan's live checks query
ALL_INTERFACES = {"0.0.0.0", "::", "[::]"}  # noqa: S104 (the values this test rejects)


def section(name: str) -> dict[str, object]:
    """Return a top-level mapping of the agent config, asserting it exists."""
    value = load_yaml_mapping(AGENT_CONFIG).get(name)
    assert isinstance(value, dict) and value, (
        f"expected a non-empty `{name}` mapping in {relative(AGENT_CONFIG)}"
    )
    return value


def components_of_type(name: str, kind: str) -> dict[str, dict[str, object]]:
    """Return `{id: config}` for the components in `name` whose type (before `/`) is `kind`."""
    return {
        str(key): value if isinstance(value, dict) else {}
        for key, value in section(name).items()
        if str(key).split("/")[0] == kind
    }


def scrape_jobs() -> list[dict[str, object]]:
    """Return every scrape config across the agent's prometheus receivers."""
    receivers = components_of_type("receivers", "prometheus")
    assert receivers, f"expected a prometheus receiver in {relative(AGENT_CONFIG)}"
    jobs: list[dict[str, object]] = []
    for name, receiver in receivers.items():
        config = receiver.get("config")
        assert isinstance(config, dict), f"expected {name}.config to be a mapping"
        found = config.get("scrape_configs")
        assert isinstance(found, list) and found, f"expected {name} to define scrape_configs"
        jobs.extend(job for job in found if isinstance(job, dict))
    return jobs


def the_exporter() -> tuple[str, dict[str, object]]:
    """Return the agent's single exporter, asserting there is exactly one."""
    exporters = section("exporters")
    assert len(exporters) == 1, f"expected exactly one exporter, got {sorted(exporters)}"
    name, config = next(iter(exporters.items()))
    assert isinstance(config, dict), f"expected exporter {name!r} to be a mapping"
    return str(name), config


def iter_keys(node: object) -> list[str]:
    """Return every mapping key in a parsed tree."""
    if isinstance(node, dict):
        return [str(k) for k in node] + [k for v in node.values() for k in iter_keys(v)]
    if isinstance(node, list):
        return [k for item in node for k in iter_keys(item)]
    return []


def binds_all_interfaces(text: str) -> bool:
    """True for `0.0.0.0`, `::` or a bare `:port`, alone or as the host of an endpoint."""
    value = text.strip()
    if value in ALL_INTERFACES:
        return True
    return value.startswith(("0.0.0.0:", "[::]:", ":"))


def test_scrape_jobs_take_targets_only_from_the_mounted_file_sd_directory() -> None:
    targets_mount = agent_mount_target("AGENT_TARGETS_DIR").rstrip("/")
    for job in scrape_jobs():
        discovery = sorted(k for k in job if k.endswith("_sd_configs") or k == "static_configs")
        assert discovery == ["file_sd_configs"], (
            f"expected job {job.get('job_name')!r} to use only file_sd_configs, got {discovery}"
        )
        files = [str(path) for sd in job["file_sd_configs"] for path in (sd.get("files") or [])]
        assert files, f"expected file_sd files in job {job.get('job_name')!r}"
        outside = [path for path in files if not path.startswith(f"{targets_mount}/")]
        assert not outside, f"expected file_sd globs under {targets_mount}/, got {outside}"


def test_scrape_jobs_leave_the_metrics_path_to_each_target() -> None:
    """D3: `__metrics_path__` in the targets file picks /metrics or /metrics/prometheus."""
    overriding = [job.get("job_name") for job in scrape_jobs() if "metrics_path" in job]
    assert not overriding, f"expected no metrics_path on the agent's jobs, got {overriding}"


def test_scrape_job_is_named_inference_models_and_matches_the_stack_interval() -> None:
    jobs = scrape_jobs()
    assert [job.get("job_name") for job in jobs] == [AGENT_JOB_NAME], (
        f"expected one job named {AGENT_JOB_NAME!r}, got {[j.get('job_name') for j in jobs]}"
    )
    stack_receivers = load_yaml_mapping(OTEL_CONFIG)["receivers"]
    stack_jobs = stack_receivers[STACK_JOB_RECEIVER]["config"]["scrape_configs"]
    stack_interval = stack_jobs[0].get("scrape_interval")
    assert jobs[0].get("scrape_interval") == stack_interval, (
        f"expected scrape_interval {stack_interval!r} like the stack, "
        f"got {jobs[0].get('scrape_interval')!r}"
    )


def test_the_only_exporter_is_otlphttp_to_the_endpoint_variable() -> None:
    name, config = the_exporter()
    assert name.split("/")[0] == "otlphttp", f"expected an otlphttp exporter, got {name!r}"
    endpoint = config.get("endpoint")
    assert endpoint == "${env:TELEMETRY_OTLP_ENDPOINT}", (
        f"expected endpoint ${{env:TELEMETRY_OTLP_ENDPOINT}}, got {endpoint!r}"
    )


def test_no_remote_write_exporter_anywhere_in_the_config() -> None:
    config = load_yaml_mapping(AGENT_CONFIG)
    hits = [k for k in iter_keys(config) if "prometheusremotewrite" in k]
    hits += [s for s in iter_strings(config) if "prometheusremotewrite" in s]
    assert not hits, f"expected OTLP to the stack, never remote-write, found {hits}"


def test_sending_queue_is_persisted_in_the_mounted_queue_directory() -> None:
    _, config = the_exporter()
    queue = config.get("sending_queue")
    assert isinstance(queue, dict), "expected a sending_queue on the exporter"
    assert queue.get("enabled") is True, f"expected sending_queue.enabled: true, got {queue}"
    storage = str(queue.get("storage", ""))
    file_storages = components_of_type("extensions", "file_storage")
    assert storage in file_storages, (
        f"expected sending_queue.storage to name a file_storage extension in "
        f"{sorted(file_storages)}, got {storage!r}"
    )
    enabled = section("service").get("extensions") or []
    assert storage in enabled, f"expected {storage!r} in service.extensions, got {enabled}"
    directory = str(file_storages[storage].get("directory", "")).rstrip("/")
    queue_mount = agent_mount_target("AGENT_QUEUE_DIR").rstrip("/")
    assert directory == queue_mount, (
        f"expected {storage}.directory to be the queue mount {queue_mount!r}, got {directory!r}"
    )


def test_queue_compaction_stays_inside_the_mounted_queue_directory() -> None:
    """The image has no writable /tmp, the default compaction directory."""
    storage = str(the_exporter()[1].get("sending_queue", {}).get("storage", ""))
    extension = components_of_type("extensions", "file_storage").get(storage) or {}
    compaction = extension.get("compaction")
    assert isinstance(compaction, dict), f"expected {storage}.compaction to be set"
    directory = str(compaction.get("directory", "")).rstrip("/")
    queue_mount = agent_mount_target("AGENT_QUEUE_DIR").rstrip("/")
    assert directory == queue_mount or directory.startswith(queue_mount + "/"), (
        f"expected {storage}.compaction.directory under {queue_mount!r}, got {directory!r}"
    )


def test_retry_on_failure_never_gives_up() -> None:
    _, config = the_exporter()
    retry = config.get("retry_on_failure")
    assert isinstance(retry, dict), "expected retry_on_failure on the exporter"
    assert retry.get("enabled", True) is not False, "expected retry_on_failure enabled"
    assert retry.get("max_elapsed_time") in {0, "0", "0s"}, (
        f"expected max_elapsed_time: 0 (no time limit), got {retry.get('max_elapsed_time')!r}"
    )


def test_service_runs_exactly_one_metrics_pipeline_from_scrape_to_otlp() -> None:
    pipelines = section("service").get("pipelines")
    assert isinstance(pipelines, dict), "expected service.pipelines to be a mapping"
    assert list(pipelines) == ["metrics"], (
        f"expected only a metrics pipeline, got {list(pipelines)}"
    )
    pipeline = pipelines["metrics"]
    exporter_name, _ = the_exporter()
    assert pipeline.get("exporters") == [exporter_name], (
        f"expected the metrics pipeline to export to [{exporter_name}], got {pipeline}"
    )
    receivers = set(components_of_type("receivers", "prometheus"))
    assert receivers and receivers <= set(pipeline.get("receivers") or []), (
        f"expected the metrics pipeline to receive from {sorted(receivers)}, got {pipeline}"
    )


def test_health_check_listens_on_loopback_only() -> None:
    checks = components_of_type("extensions", "health_check")
    assert len(checks) == 1, f"expected one health_check extension, got {sorted(checks)}"
    name, config = next(iter(checks.items()))
    endpoint = str(config.get("endpoint", ""))
    assert endpoint.startswith("127.0.0.1:"), f"expected 127.0.0.1:<port>, got {endpoint!r}"
    enabled = section("service").get("extensions") or []
    assert name in enabled, f"expected {name!r} in service.extensions, got {enabled}"


def test_internal_telemetry_is_set_explicitly_on_loopback() -> None:
    telemetry = section("service").get("telemetry")
    assert isinstance(telemetry, dict) and isinstance(telemetry.get("metrics"), dict), (
        "expected service.telemetry.metrics to be set explicitly (host networking)"
    )
    strings = list(iter_strings(telemetry["metrics"]))
    assert any("127.0.0.1" in text for text in strings), (
        f"expected service.telemetry.metrics to bind 127.0.0.1, got {strings}"
    )


def test_no_parsed_value_binds_all_interfaces() -> None:
    bad = [s for s in iter_strings(load_yaml_mapping(AGENT_CONFIG)) if binds_all_interfaces(s)]
    assert not bad, f"under host networking nothing may listen on all interfaces: {bad}"


def config_without_drop_rules() -> dict[str, object]:
    """Return the agent config with `action: drop` relabel rules removed.

    The drop rule that guards against the upstream route names that route in its
    regex; every other string in the config must still never contain it.
    """
    config = load_yaml_mapping(AGENT_CONFIG)
    pending: list[object] = [config]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            rules = node.get("relabel_configs")
            if isinstance(rules, list):
                node["relabel_configs"] = [
                    r for r in rules if not (isinstance(r, dict) and r.get("action") == "drop")
                ]
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return config


def test_scrape_job_drops_any_target_on_the_upstream_route() -> None:
    """A hand-edited targets.json must not reset a model's TTL (0003 D6)."""
    for job in scrape_jobs():
        drops = [
            rule
            for rule in job.get("relabel_configs") or []
            if rule.get("action") == "drop" and rule.get("source_labels") == ["__metrics_path__"]
        ]
        assert len(drops) == 1, f"expected one __metrics_path__ drop rule in {job.get('job_name')}"
        pattern = re.compile(str(drops[0].get("regex")))  # Prometheus anchors relabel regexes
        assert pattern.fullmatch("/upstream/some-model/metrics"), "expected upstream paths dropped"
        for kept in ("/metrics", "/metrics/prometheus"):
            assert not pattern.fullmatch(kept), f"expected {kept} to be kept, not dropped"


def test_no_agent_file_goes_through_the_llama_swap_upstream_route() -> None:
    """0003 D6: any request on `/upstream/` resets the model's idle TTL."""
    targets = json.loads(read_text(AGENT_TARGETS_EXAMPLE))
    trees = {
        relative(AGENT_CONFIG): config_without_drop_rules(),
        relative(AGENT_COMPOSE): load_yaml_mapping(AGENT_COMPOSE),
        relative(AGENT_TARGETS_EXAMPLE): targets,
    }
    upstream = sorted(
        f"{path}: {text}"
        for path, tree in trees.items()
        for text in iter_strings(tree)
        if "upstream/" in text
    )
    assert not upstream, f"agent files must never go through llama-swap /upstream/: {upstream}"
