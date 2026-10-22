"""Static checks on the Collector's `filelog/landing` receiver (spec 0004).

The receiver tails ledgers only: `landing/*/*/.agentic/delegate/ledger.jsonl` and
`landing/*/*/.agentic/telemetry/*.jsonl`. Transcripts stay files, read in batch.
Each ledger line keeps its own `ts`, with or without fractional seconds, through
one Go `time.Parse` layout. Globs are checked with `PurePosixPath.match`: with
absolute patterns and no `**`, Python 3.12 matches the whole path one segment per
`*`, as the receiver's glob does.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import pytest
from collector_files import COLLECTOR_DIR, OTEL_CONFIG, load_yaml_mapping, relative
from run_table_builders import LANDING_HOST

RECEIVER = "filelog/landing"
GOTIME_LAYOUT = "2006-01-02T15:04:05Z07:00"
HOST_ROOT = f"/landing/{LANDING_HOST}"

LEDGER_PATHS = [
    f"{HOST_ROOT}/alpha/.agentic/delegate/ledger.jsonl",
    f"{HOST_ROOT}/beta-project/.agentic/telemetry/tool-calls.jsonl",
]
NOT_LEDGER_PATHS = [
    f"{HOST_ROOT}/_sessions/claude-code/-workspace-beta-project/cc-0001.jsonl",
    f"{HOST_ROOT}/_sessions/claude-code/-workspace-beta-project/cc-0001/subagents/agent-a.jsonl",
    f"{HOST_ROOT}/_sessions/pi/--workspace-alpha-project--/2026-10-20T10-00-00-000Z_pi.jsonl",
    f"{HOST_ROOT}/_sessions/codex/2026/10/20/rollout-2026-10-20T10-00-00-codex.jsonl",
    f"{HOST_ROOT}/alpha/.agentic/delegate/results/run-0001/session/pi-run-0001.jsonl",
    f"{HOST_ROOT}/alpha/.agentic/delegate/results/run-0001/result.json",
    f"{HOST_ROOT}/alpha/.agentic/delegate/results/run-0001/state.json",
    f"{HOST_ROOT}/alpha/.agentic/telemetry/nested/tool-calls.jsonl",
]


def landing_receiver() -> dict[str, object]:
    """Return the `filelog/landing` receiver, asserting it exists."""
    receivers = load_yaml_mapping(OTEL_CONFIG).get("receivers")
    assert isinstance(receivers, dict) and RECEIVER in receivers, (
        f"expected receiver {RECEIVER!r} in {relative(OTEL_CONFIG)}"
    )
    receiver = receivers[RECEIVER]
    assert isinstance(receiver, dict), f"expected {RECEIVER} to be a mapping"
    return receiver


def include_globs() -> list[str]:
    """Return the receiver's `include` list."""
    include = landing_receiver().get("include")
    assert isinstance(include, list) and include, f"expected a non-empty {RECEIVER}.include"
    return [str(pattern) for pattern in include]


def operator(kind: str) -> dict[str, object]:
    """Return the receiver's one operator of type `kind`."""
    operators = landing_receiver().get("operators")
    assert isinstance(operators, list), f"expected {RECEIVER}.operators to be a list"
    found = [op for op in operators if isinstance(op, dict) and op.get("type") == kind]
    assert len(found) == 1, f"expected one {kind} operator in {RECEIVER}, got {len(found)}"
    return found[0]


def tailed(path: str) -> bool:
    """True when some include glob matches `path` and no exclude glob does."""
    receiver = landing_receiver()
    excludes = [str(pattern) for pattern in receiver.get("exclude") or []]
    included = any(PurePosixPath(path).match(pattern) for pattern in include_globs())
    excluded = any(
        PurePosixPath(path).match(pattern) for pattern in excludes if "**" not in pattern
    )
    return included and not excluded


def test_include_globs_are_absolute_and_never_recursive() -> None:
    for pattern in include_globs():
        assert pattern.startswith("/landing/"), f"expected an absolute /landing/ glob: {pattern}"
        assert "**" not in pattern, f"expected one segment per `*`, no `**`: {pattern}"


def test_include_globs_are_exactly_the_two_ledger_shapes() -> None:
    assert sorted(include_globs()) == sorted(
        [
            "/landing/*/*/.agentic/delegate/ledger.jsonl",
            "/landing/*/*/.agentic/telemetry/*.jsonl",
        ]
    )


@pytest.mark.parametrize("path", LEDGER_PATHS)
def test_ledger_paths_are_tailed(path: str) -> None:
    assert tailed(path), f"expected {RECEIVER} to tail {path}"


@pytest.mark.parametrize("path", NOT_LEDGER_PATHS)
def test_transcripts_and_result_files_are_not_tailed(path: str) -> None:
    assert not tailed(path), f"expected {RECEIVER} never to tail {path}"


def test_timestamp_uses_one_gotime_layout_for_whole_and_fractional_seconds() -> None:
    timestamp = operator("json_parser").get("timestamp")
    assert isinstance(timestamp, dict), "expected json_parser.timestamp to be a mapping"

    assert timestamp.get("parse_from") == "attributes.ts"
    assert timestamp.get("layout_type") == "gotime"
    assert timestamp.get("layout") == GOTIME_LAYOUT


def test_regex_parser_extracts_host_and_project_from_the_dot_agentic_layout() -> None:
    parser = operator("regex_parser")
    assert parser.get("parse_from") == 'attributes["log.file.path"]'
    pattern = re.compile(str(parser.get("regex")))

    match = pattern.match(LEDGER_PATHS[0])

    assert match is not None, f"expected {pattern.pattern!r} to match {LEDGER_PATHS[0]}"
    assert match.group("landing_host") == LANDING_HOST
    assert match.group("landing_project") == "alpha"


def test_host_and_project_become_loki_stream_labels() -> None:
    """D4: one Loki stream per project, so one project's newer line cannot push another's out.

    Loki refuses a line too far behind the newest in its stream. Written as log
    attributes, host and project are only structured metadata and every ledger
    shares one stream; as resource attributes they are the index labels that
    `loki-config.yaml` names.
    """
    assert operator("regex_parser").get("parse_to") == "resource"
    loki = load_yaml_mapping(COLLECTOR_DIR / "loki-config.yaml")
    limits = loki.get("limits_config")
    assert isinstance(limits, dict)
    otlp = limits.get("otlp_config")
    assert isinstance(otlp, dict)
    resource = otlp.get("resource_attributes")
    assert isinstance(resource, dict)
    index_labels = [
        label
        for rule in resource.get("attributes_config") or []
        if isinstance(rule, dict) and rule.get("action") == "index_label"
        for label in rule.get("attributes") or []
    ]
    assert {"landing_host", "landing_project"} <= set(index_labels), index_labels


@pytest.mark.parametrize("kind", ["json_parser", "regex_parser"])
def test_parsers_keep_malformed_lines_quietly(kind: str) -> None:
    assert operator(kind).get("on_error") == "send_quiet"


def test_receiver_keeps_file_paths_and_checkpoints() -> None:
    receiver = landing_receiver()

    assert receiver.get("include_file_path") is True
    assert receiver.get("storage") == "file_storage"
