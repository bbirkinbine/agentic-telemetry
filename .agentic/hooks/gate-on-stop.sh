#!/usr/bin/env bash
# Stop hook — refuse to end the turn while the local quality gate is red.
#
# Fires when the main session tries to finish responding. If the source
# tree has pending changes and the toolchain gate is red, it returns
# decision:block so the client continues the turn instead of declaring
# done. The tools themselves are named only in .agentic/toolchain.sh. This makes the review-check discipline automatic: the session
# cannot stop on a broken build without a human seeing green first.
#
# See AGENTS.md -> "Workflow expectations" (Verify).
#
# Stop hooks have no matcher and fire on every turn end, so two guards keep
# this from nagging in normal conversation or looping forever:
#   1. loop guard   — if we are already continuing because of a prior block
#                     (stop_hook_active), step aside with a warning so a gate
#                     that genuinely can't pass surfaces to the human rather
#                     than looping.
#   2. change guard — do nothing if src/ has no pending changes this turn.
#
# decision control: a Stop hook reports its decision via JSON on stdout,
# processed only on exit 0. Both clients accept the same top-level form
# ({"decision":"block","reason":...}), so that is the only shape emitted.
# The Codex wiring passes --codex; the flag is accepted and ignored so
# both wirings can share this script. Emit nothing to allow the stop.
#
# NOT an unbounded guarantee: clients cap or guard repeated continuation.
# This gate is one rung of the
# completion ladder — in-prompt checks below it and a fresh verification
# subagent above it. See WORKFLOW.md -> "The completion ladder".

set -uo pipefail

# Run from the project root so the source guard, git, and the toolchain
# runner all resolve regardless of the invoking client's CWD. Resolve it from this script's
# location (.agentic/hooks/ is two levels below the root); if that fails,
# allow the stop rather than blocking on hook infrastructure.
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 0

INPUT="$(cat)"

# 1. loop guard
if printf '%s' "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
  echo "gate-on-stop: gate still red after a retry; leaving it for you to resolve." >&2
  exit 0
fi

TOOLCHAIN=.agentic/toolchain.sh
# Exit 3 is deliberately uninitialized; any other readiness failure is a
# broken gate, not permission to skip validation. Check syntax separately
# so a malformed runner cannot masquerade as the uninitialized status.
status=0
if bash -n "$TOOLCHAIN"; then
  bash "$TOOLCHAIN" ready || status=$?
else
  status=2
fi
[[ "$status" == 3 ]] && exit 0
if [[ "$status" != 0 ]]; then
  printf '%s\n' '{"decision":"block","reason":"Quality gate configuration is broken. Repair .agentic/toolchain.sh and its configured directories, then re-run validation."}'
  exit 0
fi

# 2. change guard — modified OR untracked files under the source dirs both
# count. The runner says which dirs those are (src/ for Python; Go would
# say cmd/ and internal/).
# shellcheck disable=SC2046
if [ -z "$(git status --porcelain -- $(bash "$TOOLCHAIN" source-dirs) 2>/dev/null)" ]; then
  exit 0
fi

# gate --quiet prints nothing on green and "failed: <steps>" on red.
fails="$(bash "$TOOLCHAIN" gate --quiet 2>/dev/null)"

if [ -n "$fails" ]; then
  reason="Quality gate is red (${fails}). Per AGENTS.md Verify phase, do not finish: fix the failures, or write the missing failing tests first, then re-run. Use the review-check workflow for verbose output."
  printf '{"decision":"block","reason":"%s"}\n' "$reason"
fi

exit 0
