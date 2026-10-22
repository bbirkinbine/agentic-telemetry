"""Spec 0008 cases the pre-implementation analysis found uncovered.

The 15 s poll interval (Harvester), the README's statement that direct-port
clients are not captured (Coverage), the join's fallback for a session that used
two local models, the `.gitignore` guard that keeps captures out of git without
hiding the `captures` package, and `--call` on a run and on an evicted call.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from capture_builders import (
    FLASH,
    QWEN,
    SRC,
    activity_row,
    at,
    record,
    session_row,
    write_capture_file,
    write_sessions,
)
from capture_cli_helpers import CaptureEvidence, make_capture_evidence, run_cli, show_args
from capture_join_helpers import call, claims, owners
from collector_files import (
    COMPOSE,
    README,
    REPO_ROOT,
    compose_service,
    markdown_section,
    read_text,
)

from agentic_telemetry.captures.cli_commands import DEFAULT_INTERVAL_SECONDS, add_capture_parsers


def test_pull_polls_every_fifteen_seconds_by_default() -> None:
    parser = argparse.ArgumentParser()
    add_capture_parsers(parser.add_subparsers(dest="command"))

    args = parser.parse_args(
        ["captures-pull", "--url", "http://gpu.example:8080", "--host-label", "gpu", "--out", "."]
    )

    assert DEFAULT_INTERVAL_SECONDS == 15
    assert args.interval == 15


def test_compose_service_polls_every_fifteen_seconds() -> None:
    command = compose_service("captures-pull").get("command")
    text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command)
    match = re.search(r"--interval[ =](\S+)", text)
    assert match is None or float(match.group(1)) == 15, f"{COMPOSE.name}: {text}"


def test_readme_says_direct_port_clients_are_not_captured() -> None:
    body = markdown_section(read_text(README), r"\blocal-model captures\b")
    assert body is not None, "expected a Local-model captures section"
    flat = " ".join(body.split())
    assert "own port bypasses llama-swap and is not captured" in flat


def test_session_that_used_two_local_models_claims_calls_on_either(tmp_path: Path) -> None:
    sessions = [session_row("pi-switch", at(10), at(10, 30), models=(QWEN, FLASH))]
    records = [
        call(1, at(10, 5), model=QWEN, requested=QWEN),
        call(2, at(10, 20), model=FLASH, requested=FLASH),
    ]

    result = claims(tmp_path, sessions, records)

    assert owners(result[1]) == {"pi-switch"}
    assert owners(result[2]) == {"pi-switch"}


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("captures/gpu/2026-10-22.jsonl.zst", True),
        ("src/agentic_telemetry/captures/harvest.py", False),
        ("collector/captures/Dockerfile", False),
    ],
)
def test_gitignore_keeps_capture_files_out_but_not_the_package(path: str, ignored: bool) -> None:
    git = shutil.which("git") or "git"
    result = subprocess.run(  # noqa: S603 - fixed arguments, the repo's own git
        [git, "check-ignore", "--quiet", "--no-index", path],
        cwd=REPO_ROOT,
        check=False,
    )
    assert (result.returncode == 0) is ignored, f"{path}: check-ignore exit {result.returncode}"


@pytest.fixture
def evidence(tmp_path: Path) -> CaptureEvidence:
    return make_capture_evidence(tmp_path / "data")


def test_run_id_call_prints_that_runs_call(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "run-0001", "--call", "1"))

    out = capsys.readouterr().out
    assert code == 0
    assert "11:05:00" in out
    assert SRC in out


def test_call_detail_of_an_evicted_call_has_no_request_or_response(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lost = record(activity_row(1, at(10, 5), has_capture=True), None, status="evicted")
    captures = tmp_path / "captures"
    write_capture_file(captures / "gpu" / "2026-10-22.jsonl.zst", [lost])
    table = write_sessions(
        tmp_path / "sessions.parquet", [session_row("pi-a", at(10), at(10, 30), models=(QWEN,))]
    )
    evidence = CaptureEvidence(captures=captures, session_table=table)

    code = run_cli(show_args(evidence, "--session", "pi-a", "--call", "1"))

    out = capsys.readouterr().out
    assert code == 0
    assert "evicted" in out
    assert "request headers" not in out
    assert "content:" not in out
