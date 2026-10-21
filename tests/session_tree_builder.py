"""A synthetic multi-client session tree with known counts, plus sample row values.

Paths are always under a pytest `tmp_path`. Nothing here imports
`agentic_telemetry`, so `conftest.py` stays importable before the package exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claude_code_builders import (
    cc_assistant,
    cc_snapshot,
    cc_tool_result_block,
    cc_tool_use_block,
    cc_usage,
    cc_user,
)
from codex_builders import (
    codex_item_completed,
    codex_meta,
    codex_response_item,
    codex_rollout_name,
    codex_task_complete,
    codex_token_count,
    codex_token_count_without_info,
    codex_turn_context,
)
from session_table_builders import (
    ALPHA_CWD,
    MODEL,
    SENTINEL,
    pi_assistant,
    pi_compaction,
    pi_header,
    pi_session_file_name,
    pi_tool_result,
    pi_user,
    ts,
    write_jsonl,
    write_jsonl_zst,
)

LANDING_HOST = "laptop.example"
PI_SLUG = "--workspace-alpha-project--"
CC_SLUG = "-workspace-beta-project"


@dataclass(frozen=True)
class SessionTree:
    """Every client, a subagent each, a delegate Pi session, a stale copy and noise files."""

    root: Path
    expected_keys: frozenset[tuple[str, str]]
    sessions_per_client: dict[str, int]
    files_scanned: int
    unrecognized: int
    sessions_with_tokens: int
    duplicates_resolved: int
    live_pi_path: Path
    stale_pi_path: Path
    malformed_location: str
    non_utf8_location: str
    delegate_session_id: str
    delegate_run_id: str
    artifact_session_id: str


def make_session_tree(root: Path) -> SessionTree:
    """Write the tree under `root` and return its expected counts."""
    pi_dir = root / "pi-sessions" / PI_SLUG
    pi_main = [
        pi_header("pi-main-0001", timestamp=ts(0)),
        pi_user(timestamp=ts(0)),
        pi_assistant(stop_reason="toolUse", tool_calls=1, timestamp=ts(1)),
        pi_tool_result(timestamp=ts(1, 30)),
        pi_assistant(timestamp=ts(2)),
    ]
    live_tail = [
        '{"type": "message", "note": "' + SENTINEL + '"',
        pi_compaction(timestamp=ts(3)),
        pi_assistant(timestamp=ts(4)),
    ]
    live_pi = write_jsonl(pi_dir / pi_session_file_name("pi-main-0001"), pi_main + live_tail)
    stale_pi = write_jsonl(
        root / "landing" / LANDING_HOST / "pi-sessions" / PI_SLUG / live_pi.name, pi_main
    )
    write_jsonl(
        pi_dir / pi_session_file_name("pi-child-0001"),
        [
            pi_header("pi-child-0001", timestamp=ts(5), parent_session=str(live_pi)),
            pi_assistant(omit_usage=True, timestamp=ts(6)),
        ],
    )
    write_jsonl(
        pi_dir / "subagent-artifacts" / "pi-child-0001_transcript.jsonl",
        [pi_header("pi-artifact-0001"), pi_assistant()],
    )

    delegate = root / "delegate-project" / ".agentic" / "delegate"
    write_jsonl(delegate / "ledger.jsonl", [{"type": "run", "run": "run-delegate-0001"}])
    write_jsonl(
        delegate / "results" / "run-delegate-0001" / "session" / "pi-delegate.jsonl",
        [pi_header("pi-delegate-0001"), pi_assistant()],
    )

    cc_dir = root / "claude-projects" / CC_SLUG
    cc_main = write_jsonl(
        cc_dir / "cc-main-0001.jsonl",
        [
            cc_snapshot(),
            cc_user(timestamp=ts(0)),
            cc_assistant(message_id="msg-1", usage=cc_usage(), timestamp=ts(1)),
            cc_assistant(
                message_id="msg-1",
                block=cc_tool_use_block(),
                stop_reason="tool_use",
                timestamp=ts(1),
            ),
            cc_user([cc_tool_result_block()], timestamp=ts(1, 30)),
            b"\xff\xfe " + SENTINEL.encode() + b" \xc3",
            cc_assistant(message_id="msg-2", stop_reason="end_turn", timestamp=ts(2)),
        ],
    )
    write_jsonl(
        cc_dir / "cc-main-0001" / "subagents" / "agent-cc-agent-0001.jsonl",
        [
            cc_user(agent_id="cc-agent-0001", timestamp=ts(1)),
            cc_assistant(message_id="msg-a1", agent_id="cc-agent-0001", stop_reason="end_turn"),
        ],
    )

    codex_dir = root / "codex-sessions" / "2026" / "10" / "20"
    write_jsonl_zst(
        codex_dir / (codex_rollout_name("codex-main-0001") + ".zst"),
        [
            codex_meta("codex-main-0001"),
            codex_turn_context(),
            codex_item_completed("UserMessage"),
            codex_response_item("function_call", name="shell", arguments="{}"),
            codex_token_count(),
            codex_task_complete(),
        ],
    )
    write_jsonl(
        codex_dir / codex_rollout_name("codex-child-0001"),
        [
            codex_meta(
                "codex-child-0001",
                source={"subagent": {"thread_spawn": {"depth": 1}}},
                parent_thread_id="codex-main-0001",
            ),
            codex_turn_context(),
            codex_response_item("custom_tool_call", name="apply_patch", input=SENTINEL),
            codex_token_count_without_info(),
        ],
    )
    codex_home = root / "codex-home"
    write_jsonl(
        codex_home / "history.jsonl",
        [{"session_id": "codex-main-0001", "ts": 1760954400, "text": SENTINEL}],
    )
    write_jsonl(
        codex_home / "session_index.jsonl",
        [{"id": "codex-main-0001", "thread_name": SENTINEL, "updated_at": ts(2)}],
    )
    (codex_home / "auth.json").write_text(json.dumps({"token": SENTINEL}), encoding="utf-8")

    return SessionTree(
        root=root,
        expected_keys=frozenset(
            {
                ("pi", "pi-main-0001"),
                ("pi", "pi-child-0001"),
                ("pi", "pi-delegate-0001"),
                ("claude-code", "cc-main-0001"),
                ("claude-code", "cc-agent-0001"),
                ("codex", "codex-main-0001"),
                ("codex", "codex-child-0001"),
            }
        ),
        sessions_per_client={"pi": 3, "claude-code": 2, "codex": 2},
        files_scanned=11,
        unrecognized=3,
        sessions_with_tokens=5,
        duplicates_resolved=1,
        live_pi_path=live_pi,
        stale_pi_path=stale_pi,
        malformed_location=f"{live_pi}:6",
        non_utf8_location=f"{cc_main}:6",
        delegate_session_id="pi-delegate-0001",
        delegate_run_id="run-delegate-0001",
        artifact_session_id="pi-artifact-0001",
    )


def session_row_values(**overrides: object) -> dict[str, object]:
    """Return keyword values for one `SessionRow`, in the spec's column order."""
    values: dict[str, object] = {
        "client": "pi",
        "session_id": "pi-main-0001",
        "source_path": "sessions/pi/session.jsonl",
        "project": "alpha-project",
        "cwd": ALPHA_CWD,
        "client_version": None,
        "git_branch": None,
        "is_subagent": False,
        "parent_session_id": None,
        "run_id": None,
        "started_at": "2026-10-20T10:00:00+00:00",
        "ended_at": "2026-10-20T10:04:00+00:00",
        "span_seconds": 240.0,
        "provider": "example-provider",
        "models": (MODEL, "example-reasoner"),
        "primary_model": MODEL,
        "served_models": ("example-coder-q8", "example-reasoner-q4"),
        "primary_served_model": "example-coder-q8",
        "tokens_in": 300,
        "tokens_cache_read": 40,
        "tokens_cache_write": 5,
        "tokens_out": 60,
        "tokens_reasoning": 7,
        "assistant_turns": 3,
        "user_prompts": 1,
        "tool_calls": 2,
        "tool_errors": 1,
        "compactions": 1,
        "stop_reasons": '{"stop": 2, "toolUse": 1}',
    }
    values.update(overrides)
    return values
