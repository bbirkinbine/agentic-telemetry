#!/usr/bin/env bash
# Shared post-compaction reminder, wired as SessionStart with matcher
# "compact". Neither Claude Code nor Codex passes PreCompact output to the
# model or the summarizer; both add SessionStart's plain stdout to the
# model's context, so the reminder runs after compaction and points the
# model at durable state instead of the summary.

set -euo pipefail

cat <<'EOF'
Context was just compacted. Before continuing, re-establish state from durable sources, not the summary: the current branch (`git branch --show-current`) and changed files (`git status --short`); the active task or spec, if any, including its recorded phase handoff and standing decisions; and validation state, by re-running the repository's validation before reporting it. Confirm with the human any unresolved decision that appears only in the summary.
EOF
