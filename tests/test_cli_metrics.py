"""`run-table --metrics-url` end to end through `main(argv, fetch)` (spec 0005).

The fetch is the `vm_stub` fake, except where a test needs the real default
fetch against a closed loopback port.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest
from server_join_helpers import (
    ALIAS_QWEN,
    KNOWN,
    PROJECT_JOINED_WINDOW,
    QWEN,
    SERVER_NULL_COLUMNS,
    model_readings,
    write_server_project,
)
from vm_stub import FakeVictoria

from agentic_telemetry.cli import main

METRICS_URL = "http://telemetry.example:8428"
STATUSES = ("joined", "joined_approx", "overlap", "uncovered", "no_data", "error")
SOURCES = ("served", "requested", "inferred", "none")


def run_main(argv: Sequence[str], fetch: FakeVictoria | None = None) -> int:
    """Return the exit code whether `main` returns it or argparse raises SystemExit."""
    try:
        return main(list(argv)) if fetch is None else main(list(argv), fetch=fetch)
    except SystemExit as exit_signal:
        code = exit_signal.code
        return code if isinstance(code, int) else 1


def fake_victoria() -> FakeVictoria:
    """Series for the joined run's window only; the second run sees nothing move."""
    start, end = PROJECT_JOINED_WINDOW
    return FakeVictoria(
        known=KNOWN,
        readings=model_readings(QWEN, up_at_start=None),
        active_between=(start - timedelta(minutes=5), end + timedelta(minutes=5)),
    )


def read_rows(out: Path) -> dict[str, dict[str, object]]:
    relation = duckdb.read_parquet(str(out))
    columns = list(relation.columns)
    records = [dict(zip(columns, values, strict=True)) for values in relation.fetchall()]
    return {str(record["run_id"]): record for record in records}


def column_types(out: Path) -> dict[str, str]:
    relation = duckdb.read_parquet(str(out))
    return {name: str(kind) for name, kind in zip(relation.columns, relation.types, strict=True)}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "evidence"
    write_server_project(root)
    return root


@pytest.fixture
def out(tmp_path: Path) -> Path:
    out_dir = tmp_path / "data"
    out_dir.mkdir()
    return out_dir / "run_table.parquet"


def test_metrics_url_fills_server_columns_in_the_parquet_file(project: Path, out: Path) -> None:
    fake = fake_victoria()

    code = run_main(
        ["run-table", "--metrics-url", METRICS_URL, "--out", str(out), str(project)], fake
    )

    assert code == 0
    rows = read_rows(out)
    joined = rows["alpha-run-joined"]
    assert joined["server_status"] == "joined"
    assert joined["server_model_id"] == QWEN
    assert joined["server_model_source"] == "served"
    assert [joined["server_token_match"]] == ["exact"]
    assert (joined["server_prompt_tokens"], joined["server_predicted_tokens"]) == (1000, 200)
    assert joined["server_prefill_tps"] == pytest.approx(250.0)
    assert joined["server_decode_tps"] == pytest.approx(20.0)
    assert (joined["server_deferred_max"], joined["server_ctx_high_water"]) == (3, 8192)
    assert joined["server_cold_start"] is True
    assert rows["alpha-run-sybil"]["server_status"] == "uncovered"


def test_metrics_url_summary_counts_every_status_and_source_with_zeros(
    project: Path, out: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = fake_victoria()

    code = run_main(
        ["run-table", "--metrics-url", METRICS_URL, "--out", str(out), str(project)], fake
    )

    stdout = capsys.readouterr().out
    assert code == 0
    expected_status = {"joined": 1, "uncovered": 1}
    for status in STATUSES:
        count = expected_status.get(status, 0)
        assert re.search(rf"^\s*server_status {status}: {count}\s*$", stdout, re.M), status
    expected_source = {"served": 1, "none": 1}
    for source in SOURCES:
        count = expected_source.get(source, 0)
        assert re.search(rf"^\s*server_model_source {source}: {count}\s*$", stdout, re.M), source
    assert "telemetry.example" not in stdout


def test_metrics_url_reads_known_models_once_and_strips_a_trailing_slash(
    project: Path, out: Path
) -> None:
    fake = fake_victoria()

    code = run_main(
        ["run-table", "--metrics-url", f"{METRICS_URL}/", "--out", str(out), str(project)], fake
    )

    assert code == 0
    assert len(fake.label_calls()) == 1
    assert all(url.startswith(f"{METRICS_URL}/api/v1/") for url in fake.urls), fake.urls


@pytest.mark.parametrize(
    "bad_url",
    [
        pytest.param("telemetry.example:8428", id="no-scheme"),
        pytest.param("ftp://telemetry.example:8428", id="ftp"),
        pytest.param("http://", id="no-host"),
        pytest.param("http://telemetry.example:notaport", id="bad-port"),
        pytest.param("http://telemetry.example:8428/?nocache=1", id="query"),
        pytest.param("http://telemetry.example:8428#top", id="fragment"),
        pytest.param("http://user:secret@telemetry.example:8428", id="credentials"),
        pytest.param("ftp://user:secret@telemetry.example:8428", id="ftp-credentials"),
        pytest.param("http://telemetry.example:8428/?token=secret", id="query-secret"),
    ],
)
def test_malformed_metrics_url_is_a_usage_error_before_any_request(
    project: Path, out: Path, bad_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = fake_victoria()

    code = run_main(["run-table", "--metrics-url", bad_url, "--out", str(out), str(project)], fake)

    err = capsys.readouterr().err
    assert code == 2
    assert "--metrics-url" in err
    assert "unrecognized arguments" not in err
    assert "secret" not in err
    assert fake.urls == []
    assert not out.exists()


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_unreachable_metrics_url_exits_1_naming_it_and_writes_nothing(
    project: Path, out: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"http://127.0.0.1:{_closed_port()}"

    code = run_main(["run-table", "--metrics-url", url, "--out", str(out), str(project)])

    assert code == 1
    assert url in capsys.readouterr().err
    assert list(out.parent.iterdir()) == []


def test_failed_known_models_call_exits_1_and_writes_nothing(
    project: Path, out: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeVictoria(known=KNOWN, label_error=True)

    code = run_main(
        ["run-table", "--metrics-url", METRICS_URL, "--out", str(out), str(project)], fake
    )

    assert code == 1
    assert METRICS_URL in capsys.readouterr().err
    assert list(out.parent.iterdir()) == []


def test_without_the_flag_server_columns_are_null_and_not_queried(project: Path, out: Path) -> None:
    fake = fake_victoria()

    code = run_main(["run-table", "--out", str(out), str(project)], fake)

    assert code == 0
    assert fake.urls == [], "no request without --metrics-url"
    for run_id, record in read_rows(out).items():
        assert record["server_status"] == "not_queried", run_id
        for name in SERVER_NULL_COLUMNS:
            assert record[name] is None, (run_id, name)


def test_without_the_flag_transcript_columns_are_still_filled(project: Path, out: Path) -> None:
    code = run_main(["run-table", "--out", str(out), str(project)])

    assert code == 0
    rows = read_rows(out)
    joined = rows["alpha-run-joined"]
    assert joined["requested_models"] == [ALIAS_QWEN]
    assert joined["served_models"] == [QWEN]
    assert joined["peak_turn_prompt_tokens"] == 700
    sybil = rows["alpha-run-sybil"]
    assert (sybil["requested_models"], sybil["served_models"]) == (None, None)
    assert sybil["peak_turn_prompt_tokens"] is None


def test_without_the_flag_stdout_has_no_server_lines(
    project: Path, out: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_main(["run-table", "--out", str(out), str(project)])

    stdout = capsys.readouterr().out
    assert code == 0
    assert "server_" not in stdout


def test_new_columns_have_their_parquet_types(project: Path, out: Path) -> None:
    run_main(
        ["run-table", "--metrics-url", METRICS_URL, "--out", str(out), str(project)],
        fake_victoria(),
    )

    types = column_types(out)
    assert types["requested_models"] == types["served_models"] == "VARCHAR[]"
    for name in (
        "peak_turn_prompt_tokens",
        "server_prompt_tokens",
        "server_predicted_tokens",
        "server_deferred_max",
        "server_ctx_high_water",
    ):
        assert types[name] == "BIGINT", name
    assert types["server_prefill_tps"] == types["server_decode_tps"] == "DOUBLE"
    assert types["server_cold_start"] == "BOOLEAN"


def test_run_table_help_describes_the_read_only_metrics_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_main(["run-table", "--help"])

    help_text = " ".join(capsys.readouterr().out.lower().split())
    assert code == 0
    assert "--metrics-url" in help_text
    assert "victoriametrics" in help_text
    assert "read-only" in help_text
