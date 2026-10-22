"""Static checks on `collector/inference-agent/targets.example.json` (spec 0007, D3).

The tracked example is a placeholder of the untracked file_sd file on the inference
host: loopback targets on placeholder ports, each labelled with its llama-swap
`model_id` and the server's own Prometheus path.
"""

from __future__ import annotations

import json
import re

from collector_files import AGENT_TARGETS_EXAMPLE, read_text, relative

LOOPBACK_TARGET = re.compile(r"127\.0\.0\.1:(\d{1,5})")
PLACEHOLDER_PORTS = range(65000, 65536)
METRICS_PATHS = {"/metrics", "/metrics/prometheus"}
# The approved plan's two entries: Strata serves /metrics/prometheus, llama.cpp
# (with --metrics, D5) serves /metrics. Both IDs are already in collector/README.md.
EXPECTED_PATHS = {
    "qwen3.8-flash-next-unsloth-q8_0": "/metrics/prometheus",
    "qwen3.8-27b-ud-q8_k_xl": "/metrics",
}


def target_groups() -> list[dict[str, object]]:
    """Parse the example, asserting it is a non-empty JSON list of objects."""
    data = json.loads(read_text(AGENT_TARGETS_EXAMPLE))
    assert isinstance(data, list) and data, (
        f"expected {relative(AGENT_TARGETS_EXAMPLE)} to hold a non-empty JSON list"
    )
    for group in data:
        assert isinstance(group, dict), f"expected each file_sd entry to be an object: {group!r}"
    return data


def labels_of(group: dict[str, object]) -> dict[str, object]:
    """Return a group's `labels` object, asserting it is one."""
    labels = group.get("labels")
    assert isinstance(labels, dict), f"expected a `labels` object in {group!r}"
    return labels


def port_of(group: dict[str, object]) -> int:
    """Return the port of a group's single `127.0.0.1:<port>` target."""
    targets = group.get("targets")
    assert isinstance(targets, list) and len(targets) == 1, (
        f"expected exactly one target per entry, got {targets!r}"
    )
    match = LOOPBACK_TARGET.fullmatch(str(targets[0]))
    assert match, f"expected a 127.0.0.1:<port> target, got {targets[0]!r}"
    return int(match.group(1))


def test_targets_example_entries_carry_only_targets_and_labels() -> None:
    for group in target_groups():
        assert set(group) == {"targets", "labels"}, (
            f"expected file_sd keys targets and labels only, got {sorted(group)}"
        )


def test_each_entry_has_one_loopback_target_on_a_placeholder_port() -> None:
    for group in target_groups():
        port = port_of(group)
        assert port in PLACEHOLDER_PORTS, f"expected a port in 65000-65535, got {port}"


def test_each_entry_labels_model_id_and_a_known_metrics_path() -> None:
    for group in target_groups():
        labels = labels_of(group)
        assert isinstance(labels.get("model_id"), str) and labels["model_id"], (
            f"expected a non-empty string model_id label, got {labels!r}"
        )
        path = labels.get("__metrics_path__")
        assert path in METRICS_PATHS, (
            f"expected __metrics_path__ in {sorted(METRICS_PATHS)}, got {path!r}"
        )
        not_strings = {k: v for k, v in labels.items() if not isinstance(v, str)}
        assert not not_strings, f"file_sd label values must be strings: {not_strings}"


def test_ports_and_model_ids_are_each_distinct() -> None:
    groups = target_groups()
    ports = [port_of(group) for group in groups]
    model_ids = [labels_of(group).get("model_id") for group in groups]
    assert len(set(ports)) == len(ports), f"expected distinct ports, got {ports}"
    assert len(set(model_ids)) == len(model_ids), f"expected distinct model_ids, got {model_ids}"


def test_both_metrics_paths_appear() -> None:
    paths = {labels_of(group).get("__metrics_path__") for group in target_groups()}
    assert paths == METRICS_PATHS, f"expected both {sorted(METRICS_PATHS)}, got {paths}"


def test_example_maps_strata_and_llama_cpp_models_to_their_own_paths() -> None:
    mapping = {
        labels_of(group).get("model_id"): labels_of(group).get("__metrics_path__")
        for group in target_groups()
    }
    assert mapping == EXPECTED_PATHS, f"expected {EXPECTED_PATHS}, got {mapping}"
