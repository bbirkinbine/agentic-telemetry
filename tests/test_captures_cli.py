"""`agentic-telemetry captures show` and `captures-pull` through `main(argv)` (spec 0008: CLI).

`captures show --captures DIR --session-table PATH (--session ID | RUN_ID |
--date YYYY-MM-DD) [--call N]` lists calls (time as HH:MM:SS UTC, model, prompt
and output tokens, duration, capture status, `src`, flags), or prints one call's
request and reassembled response. Owner decision 2026-10-22: `show` prints `src`
in the list and the detail. `--call N` counts from 1 within the session's own
calls; a child session gets one summary line naming it. Errors exit 1 naming the
input; usage errors exit 2.

`captures-pull --url URL --host-label LABEL --out DIR [--interval S] [--once]`
writes `DIR/LABEL/<date>.jsonl.zst`; the key comes only from LLAMA_SWAP_API_KEY.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from capture_builders import API_KEY, BASE_URL, QWEN, SENTINEL, SRC, activity_row, at, capture
from capture_builders import read_capture_lines as read_lines
from capture_cli_helpers import (
    FIRST_DURATION_MS,
    FIRST_TOKENS,
    SECOND_TOKENS,
    CaptureEvidence,
    header_value,
    lines_with,
    make_capture_evidence,
    run_cli,
    show_args,
)
from capture_fake_api import FakeLlamaSwap


@pytest.fixture
def evidence(tmp_path: Path) -> CaptureEvidence:
    return make_capture_evidence(tmp_path / "data")


def has_number(line: str, number: int) -> bool:
    """True when `number` stands alone in `line`."""
    return re.search(rf"(?<![\d.]){number}(?![\d])", line) is not None


def test_session_list_shows_each_call_with_its_columns_in_order(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "--session", "pi-main"))

    out = capsys.readouterr().out
    assert code == 0
    [first] = lines_with(out, "10:05:00")
    [second] = lines_with(out, "10:25:00")
    assert out.index(first) < out.index(second)
    assert QWEN in first
    assert has_number(first, FIRST_TOKENS[0]) and has_number(first, FIRST_TOKENS[1])
    assert re.search(rf"{FIRST_DURATION_MS}|6\.5", first), f"duration missing: {first!r}"
    assert "saved" in first
    assert SRC in first
    assert has_number(second, SECOND_TOKENS[0]) and has_number(second, SECOND_TOKENS[1])


def test_session_list_gives_a_child_session_one_summary_line(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "--session", "pi-main"))

    out = capsys.readouterr().out
    assert code == 0
    assert len(lines_with(out, "pi-sub")) == 1


def test_call_detail_prints_request_and_reassembled_response_and_src(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "--session", "pi-main", "--call", "2"))

    out = capsys.readouterr().out
    assert code == 0
    assert SENTINEL in out, "the request's system prompt"
    assert '"temperature"' in out, "the request's sampling settings"
    assert "Second answer" in out, "the streamed pieces, reassembled"
    assert "Hello world" not in out, "only call 2 is printed"
    assert SRC in out


def test_run_id_resolves_to_its_root_session(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "run-0001"))

    out = capsys.readouterr().out
    assert code == 0
    assert len(lines_with(out, "11:05:00")) == 1
    assert len(lines_with(out, "pi-run-sub")) == 1


def test_run_id_shared_by_two_root_sessions_is_an_error_listing_both(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "run-dup"))

    err = capsys.readouterr().err
    assert code == 1
    assert "pi-dup-1" in err and "pi-dup-2" in err


def test_date_list_shows_unclaimed_and_ambiguous_calls_with_src(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "--date", "2026-10-22"))

    out = capsys.readouterr().out
    assert code == 0
    ambiguous = lines_with(out, "12:20:00")
    assert ambiguous and all("ambiguous" in line for line in ambiguous), ambiguous
    [unclaimed] = lines_with(out, "13:00:00")
    assert "unclaimed" in unclaimed
    assert SRC in unclaimed
    assert lines_with(out, "10:05:00"), "claimed calls are listed by date too"


def test_unknown_session_exits_1_naming_it(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "--session", "pi-nope-0000"))

    assert code == 1
    assert "pi-nope-0000" in capsys.readouterr().err


def test_call_number_past_the_session_exits_1_naming_it(
    evidence: CaptureEvidence, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_cli(show_args(evidence, "--session", "pi-main", "--call", "9"))

    assert code == 1
    assert has_number(capsys.readouterr().err, 9)


@pytest.mark.parametrize(
    "selector",
    [
        [],
        ["--session", "pi-main", "--date", "2026-10-22"],
        ["--session", "pi-main", "run-0001"],
        ["--date", "2026-13-45"],
    ],
    ids=["no-selector", "session-and-date", "session-and-run", "bad-date"],
)
def test_show_usage_errors_exit_2(evidence: CaptureEvidence, selector: list[str]) -> None:
    assert run_cli(show_args(evidence, *selector)) == 2


def test_show_without_captures_dir_exits_2(evidence: CaptureEvidence) -> None:
    argv = [
        "captures",
        "show",
        "--session-table",
        str(evidence.session_table),
        "--date",
        "2026-10-22",
    ]

    assert run_cli(argv) == 2


def pull_args(out: Path, url: str = BASE_URL, *extra: str) -> list[str]:
    """Return `captures-pull --once` argv."""
    return [
        "captures-pull",
        "--url",
        url,
        "--host-label",
        "gpu",
        "--out",
        str(out),
        "--once",
        *extra,
    ]


def test_pull_once_writes_the_daily_file_and_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLAMA_SWAP_API_KEY", raising=False)
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, at(10)), capture(1))
    out = tmp_path / "captures"
    out.mkdir()

    code = run_cli(pull_args(out), api_fetch=fake.fetch)

    assert code == 0
    [line] = read_lines(out / "gpu" / "2026-10-22.jsonl.zst")
    assert line["capture_status"] == "saved"
    assert (out / "gpu" / "cursor.json").is_file()
    assert all(header_value(headers, "authorization") is None for _, headers in fake.calls)


def test_pull_sends_the_key_from_the_environment_and_never_prints_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLAMA_SWAP_API_KEY", API_KEY)
    fake = FakeLlamaSwap()
    fake.add(activity_row(1, at(10)), capture(1))

    code = run_cli(pull_args(tmp_path), api_fetch=fake.fetch)

    captured = capsys.readouterr()
    assert code == 0
    assert fake.calls
    assert all(header_value(h, "authorization") == f"Bearer {API_KEY}" for _, h in fake.calls)
    assert API_KEY not in captured.out + captured.err


def test_pull_has_no_api_key_option(tmp_path: Path) -> None:
    assert run_cli(pull_args(tmp_path, BASE_URL, "--api-key", API_KEY)) == 2


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("http://harvester:hunter2-secret@gpu.example:8080", "hunter2-secret"),
        ("http://gpu.example:8080/?token=query-secret-77", "query-secret-77"),
        ("http://gpu.example:8080/#frag-secret-88", "frag-secret-88"),
    ],
    ids=["credentials", "query", "fragment"],
)
def test_pull_rejects_a_url_with_secrets_without_echoing_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], url: str, secret: str
) -> None:
    fake = FakeLlamaSwap()

    code = run_cli(pull_args(tmp_path, url), api_fetch=fake.fetch)

    captured = capsys.readouterr()
    assert code == 2
    assert secret not in captured.out + captured.err
    assert fake.calls == [], "nothing is fetched"


@pytest.mark.parametrize("argv", [["captures-pull", "--help"], ["captures", "show", "--help"]])
def test_help_states_what_the_command_reads(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(argv) == 0
    assert "llama-swap" in capsys.readouterr().out
