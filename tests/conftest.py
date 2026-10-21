"""Shared fixtures: thin wrappers over `run_table_builders` and `session_tree_builder`."""

from __future__ import annotations

from pathlib import Path

import pytest
from run_table_builders import (
    EvidenceTree,
    assistant_message,
    make_project,
    model_record,
    review_record,
    run_record,
    session_header,
    write_ledger,
    write_result_dir,
    write_session,
    write_state,
)
from session_tree_builder import SessionTree, make_session_tree


@pytest.fixture
def evidence_tree(tmp_path: Path) -> EvidenceTree:
    root = tmp_path / "evidence"

    alpha = make_project(root, "alpha", layout="agentic")
    alpha_ledger = write_ledger(
        alpha,
        [
            run_record("alpha-run-green"),
            review_record("alpha-run-green", "pass"),
            run_record("alpha-run-defect"),
            "{this line is not json",
            review_record("alpha-run-defect", "model-defect"),
            run_record("alpha-run-frontier", outcome="needs_frontier", drop=("wall_seconds",)),
            review_record("ghost-run", "pass"),
        ],
    )
    write_result_dir(
        alpha,
        "alpha-run-from-result",
        result=run_record("alpha-run-from-result", outcome="integrity"),
    )
    orphan_dir = write_result_dir(
        alpha, "alpha-orphan", extra_files=("prompt.md", "harness.log", "install.log")
    )
    write_session(orphan_dir, [session_header(), assistant_message(tokens_in=7, tokens_out=3)])

    beta = make_project(root, "beta", layout="landing")
    beta_ledger = write_ledger(
        beta,
        [
            run_record(
                "beta-run-handoff",
                mode="handoff",
                harness="delegate-run-v2",
                bucket="phase:implement",
                outcome="integrity",
                model=model_record(name="example-planner"),
            ),
            review_record("beta-run-handoff", "packet-defect"),
        ],
    )
    beta_run = write_result_dir(beta, "beta-run-handoff")
    write_state(beta_run, phase="implement", segments=[(1, 1), (0, 2)])
    write_session(
        beta_run,
        [session_header(), assistant_message(tokens_in=1000, tokens_out=200, cache_read=50)],
    )

    return EvidenceTree(
        root=root,
        alpha_ledger=alpha_ledger,
        beta_ledger=beta_ledger,
        malformed_line_no=4,
        alpha_run_ids=(
            "alpha-run-green",
            "alpha-run-defect",
            "alpha-run-frontier",
            "alpha-run-from-result",
        ),
        beta_run_ids=("beta-run-handoff",),
        orphan_run_id="alpha-orphan",
        ghost_review_run_id="ghost-run",
        reviews_joined=3,
        runs_with_tokens=1,
    )


@pytest.fixture
def session_tree(tmp_path: Path) -> SessionTree:
    return make_session_tree(tmp_path / "sessions")
