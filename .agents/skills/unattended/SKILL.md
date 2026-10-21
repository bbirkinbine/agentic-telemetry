---
name: unattended
description: Prepare a feature to run unattended — overnight, or while you are away for the day (ask every foreseeable decision up front and record standing consent), or reconcile the decisions an unattended run made on the human's behalf.
---

In these shared instructions, `$ARGUMENTS` means the arguments supplied with this skill invocation. Any `/<name>` cross-reference names another workflow: invoke `$<name>` in Codex or the matching `/<name>` prompt (or `/skill:<name>`) in Pi.


Unattended mode lets a feature run to "ready to merge" with nobody watching,
for a night or a day away: the human answers everything foreseeable before
leaving, the orchestrator answers the rest on their behalf and logs each
answer, and the human reconciles the log on return. See `docs/parallel-agents.md` →
"One feature while you are away" when installed.

Resolve the active spec under `AGENTS.md` → **Shared workflow protocols** →
**Active-spec resolution** (or use the path in `$ARGUMENTS`). Ambiguity
stops.

## `start` — before the human leaves

Run after the plan is approved and persisted, before test-first.

1. Read the spec, its `## Approved implementation plan`, `AGENTS.md`, and
   any earlier `## Standing decisions`. List every decision the run is
   likely to hit, highest leverage first:
   - success criteria or plan steps that admit more than one reasonable
     reading, and behavior the tests will not pin;
   - choices the plan defers ("assume", "TBD", "if needed") or leaves to
     taste: names, layout, messages, defaults;
   - look and feel, for anything a person will see: what it should look
     like and how it should play or read, as measurably as the human can
     put it, since nobody will see it until the run ends;
   - scope edges: adjacent work the plan neither claims nor excludes;
   - baseline: which existing branch, config, or scaffold changes are
     already accepted and must not be redone or reviewed as new;
   - verification the run cannot do itself (manual or browser checks):
     defer it to the morning or skip it;
   - failure handling: if a step stays red or local delegation fails,
     fall back to `direct` (this session implements) or stop;
   - models: frontier work, subagents included, runs on the session's
     model, so a frontier choice means "start the session on it" and is
     recorded as the session model, never as an override. When local
     delegation is on, list the qualified models from
     `bash .agentic/delegate-run.sh status` with their phase records and
     ask which to use, or whether to let the runner select. Ask the
     delegation mode for this run: `direct` or `handoff` (implementation,
     or test-first plus implementation, to a local session; `/delegate` →
     "Delegation modes"). A verified `handoff` is preserved as a temporary
     commit on its isolated `handoff/<run>` branch, so ask separately
     whether those temporary commits are authorized; choosing the mode
     alone is not commit approval. If they decline, choose `direct`.
     Default `direct` when this session itself runs on a local model.
2. Ask them in one batch, each naming the spec or plan line it affects and
   offering your best-guess default so "defaults are fine" is a complete
   answer. Ask only what the spec, plan, and `AGENTS.md` do not already
   settle; say so if nothing needs asking.
3. Record the answers in the spec under `## Standing decisions`, one bullet
   each (`<decision>: <answer> (asked <date>)`), and add `## Unattended
   decisions` with the line `None yet.` The run treats standing decisions
   as the human's answers.
4. Ask for standing consent explicitly and record it under `## Standing
   decisions` as one line: unattended shipping authorized through
   "ready to merge" (branch green, review clean, uncommitted), or through
   commits on the feature branch if the human grants that. If `handoff` was
   selected, record its separate authorization for temporary commits on
   `handoff/<run>` branches; without that exact authorization, handoff is not
   available even when feature-branch commits are allowed. That
   authorization also covers resuming a paused handoff of the same run.
   Never push or merge.
5. Print the kickoff instruction the human can leave running, then stop
   with a checkpoint report (`AGENTS.md` → **Shared workflow protocols** → **Checkpoint report**).

## During the run — auto-answering

With standing consent recorded, an `[ask-user]` finding or question that a
standing decision does not settle no longer stops the run. The orchestrator:

1. picks the answer the human would most likely choose, judged from the
   spec, the plan, `AGENTS.md`, and the standing decisions, preferring the
   smallest change that keeps the approved plan intact;
2. appends an entry under `## Unattended decisions`: the question, the
   choice, the alternatives considered, why, the files it affected, and how
   to reverse it;
3. keeps that decision's changes separable — its own commit when commits
   were granted, otherwise a list of the exact hunks — and continues.

A handoff that pauses (`handoff-status` exit 11) is resumed without
asking, as often as it pauses, with `handoff-resume <run> --allow-commit`
under the recorded handoff authorization, and followed as in `/delegate`
(`handoff-status <run> --wait 540 --interval 60 --since <cursor>`). Log
each pause and resume under `## Unattended decisions` with its reason. A configured absolute ceiling is
not raised on the human's behalf: when resume refuses for a spent ceiling,
fall back as the failure-handling decision says.

A tool or command that fails on its own invocation (an unsupported
parameter, a wrong path or flag) is not a decision: correct the call from
the error and retry once, with the same intent and no new scope, and note
it in the run's report. A second failure, or a fix that would change what
the step does, stops as below.

It still stops, and leaves the run at its last green state, for anything
irreversible or outside the approved plan: deleting or rewriting history,
dependencies the plan does not name, changes to `.agentic/`, CI, or security-relevant
configuration, files outside the plan, or a spec the run has shown to be
wrong. Those wait for the human, behind a checkpoint report.

## Back early — the human returns mid-run

When the human says they are back before the run ends:

1. Stop auto-answering from that message on; `[ask-user]` findings go to
   the human again. Standing decisions still stand.
2. A live handoff keeps running in its own worktree; report its
   `handoff-status` and offer `handoff-stop` if the human wants it paused.
3. Report where the run is (last green step, next step) and list the
   `## Unattended decisions` logged so far, then offer to reconcile them
   now or at the end.
4. Continue the approved plan with the human present.

## `reconcile` — when the human is back

1. Read `## Unattended decisions`. For each entry, show the human the
   question, the choice, and its diff, and ask: keep or override (with the
   preferred answer).
2. Apply every override as a normal change: edit, gate, and a fresh focused
   review of the delta, then mark the entry `kept` or `overridden → <new
   answer>`. Move decisions worth keeping for future runs into
   `## Standing decisions`.
3. Report what changed, then stop for commit authorization with a
   checkpoint report (`AGENTS.md` → **Shared workflow protocols** → **Checkpoint report**).
