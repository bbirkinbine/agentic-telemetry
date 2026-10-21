---
name: review-adversarial
description: Invoke the reviewer-adversarial subagent on the complete current semantic change set. Argues against the change rather than for it. Pair with /review for A/B comparison.
---

In these shared instructions, `$ARGUMENTS` means the arguments supplied with this skill invocation. Any `/<name>` cross-reference names another workflow: invoke `$<name>` in Codex or the matching `/<name>` prompt (or `/skill:<name>`) in Pi.


Invoke the `reviewer-adversarial` subagent.

Spec selection:

- Resolve the active spec using `AGENTS.md` → **Shared workflow protocols** →
  **Active-spec resolution**: explicit spec path, then branch-number match,
  then the unique `shipping` spec. Ambiguity stops; never guess from the
  highest spec number.

Change-set selection:

- By default, build the complete pre-commit semantic change set from
  `AGENTS.md` → **Shared workflow protocols** → **Semantic change set**. Pass
  the reviewer the resolved spec path, base ref, merge-base, `git status`
  output, tracked working-tree diff, and untracked-path manifest. The reviewer
  must inspect every untracked path as well as the patch.
- If `$ARGUMENTS` explicitly contains `<base>..<head>`, use that committed
  historical range instead and state that current working-tree changes were
  not reviewed.

The adversarial reviewer is fresh and read-only — it has not seen the
implementation reasoning. Its job is to find reasons the complete change set
should NOT merge: spec deviation, weak tests, missing edge cases, hidden side
effects, don't-touch violations, simpler alternatives that were skipped, and
docs drift.

Surface its findings verbatim. Do NOT argue with or rationalize away its calls.
The orchestrator may apply `[auto-fix]` findings; `[ask-user]` stops for human
adjudication, with a checkpoint report
(`AGENTS.md` → **Shared workflow protocols** → **Checkpoint report**). The point of running this alongside `/review` is to surface
failure modes a collaborative review misses; if the adversarial reviewer's
concerns are real, fixing them is cheaper now than after merge.

Intended workflow: run `/review` and `/review-adversarial` on the same
complete semantic change set, read both, and adjudicate. They use the same
section structure so side-by-side comparison is direct.

After fixes, synchronize any affected docs and run another complete gate. Then
give a fresh reviewer the prior findings and the new complete semantic change
set for a focused resolution check. It must verify the claimed fixes and
inspect the fix delta; a substantial change gets both full reviews again.
