---
description: Invoke the planner subagent against a spec. Read-only — produces a markdown plan, never writes code.
argument-hint: "[path to spec, or blank to resolve the active spec]"
---

Invoke the `planner` subagent.

Spec selection:

- Resolve the active spec using `AGENTS.md` → **Shared workflow protocols** →
  **Active-spec resolution**: explicit spec path, then branch-number match,
  then the unique `shipping` spec. Ambiguity stops; never guess from the
  highest spec number.

The planner is read-only. It reads the spec, surveys the codebase, and produces a markdown plan covering: files-to-touch, order-of-operations, risks / open questions, and out-of-scope. The plan should be reviewable in under five minutes.

Surface the planner's output verbatim. Do NOT proceed to test-writing or
implementation. The human reviews and approves the plan before the next phase.
End with a checkpoint report (`AGENTS.md` → **Shared workflow protocols** →
**Checkpoint report**) whose continue line names both paths: on approval the
plan is recorded and the run autodrives to a clean review, stopping on
`[ask-user]`; a human who will be away and wants those decisions answered and
logged on their behalf runs `/unattended start` (if installed) after approving,
before test-first. Approving local delegation does not imply unattended
consent.

Approval is a durable state transition, not a chat-only acknowledgment. When
the human approves the plan, the orchestrator — not the read-only planner —
must, before `/test-first`, compaction, a client switch, or a phase handoff:

1. Append the approved plan verbatim to the active spec under
   `## Approved implementation plan`, using the section shape in
   `docs/specs/README.md`. Replace an earlier proposed/approved plan only when
   the human explicitly approved the replacement; retain the prior approval
   in the spec's history or note why it changed.
2. Change `**Status:** draft` to `**Status:** shipping` and update
   `**Last updated:**`.
3. Show the resulting spec diff, then continue to `/test-first` (or write the
   documented phase handoff before switching sessions; or run
   `/unattended start` first when the human chose it).

If the plan contains `[DECISION NEEDED]`, it is not approvable yet. Resolve the
decision in the spec or an ADR, rerun `/plan`, and obtain approval for the
replacement.
