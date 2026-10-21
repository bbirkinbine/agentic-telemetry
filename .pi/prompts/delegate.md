---
description: Hand one phase of an approved spec to a local model session (Pi) in its own git worktree, verify it, and bring the result back for review. Also sets up local delegation and records review verdicts on delegated work.
argument-hint: "[spec path, or blank for the active spec]"
---

Hand the implement phase (or test-first plus implement) of an approved spec
to a local agent session. The session reads the repository, edits, and runs
the tests itself in its own git worktree; `.agentic/delegate-run.sh` then
verifies the full gate and the plan's file scope independently and commits
the result on a temporary `handoff/<run>` branch for this session to review
and merge.

Read `docs/local-executor.md` first if it is installed; this command
implements the rule it states. Refuse the task rather than bending it — a
handoff that violates the preconditions is worse than none, because the
failure will look like a model problem.

## Setting up local delegation

Until the machine profile has a `[[project]]` entry for this checkout,
`bash .agentic/delegate-run.sh policy` prints `off`. Setup, each step
stopping for the human (detail in `docs/project-types.md` → "Local
delegation setup"):

1. Ask which local inference endpoints to use (base URLs; for a server that
   needs a key, the name of the environment variable holding it, never the
   key).
2. Run `bash .agentic/delegate-run.sh init <base-url> ...` and show the
   draft profile. It writes nothing.
3. Save it to `~/.config/agentic/local-models.toml` only after the human
   approves it.
4. Run `qualify <name>` on each candidate and report the results.
5. Ask separately whether to activate. Only on an explicit yes run
   `bash .agentic/delegate-run.sh activate`; never activate on your own
   judgment.

## Delegation modes

Run `bash .agentic/delegate-run.sh policy`. Exit 1 (`off: <why>`) means no
delegation for this checkout: implement yourself and do not ask about a
mode. Exit 2 means delegation is set up but the runner cannot start (a
missing dependency such as `jq`): tell the human what it printed and
implement yourself; do not install anything. `policy` printing `auto`
means the human approved local delegation for this checkout. Whether to
use it is chosen per run, as a standing decision from `/unattended start` or
the human's direct answer. Each mode says who writes the code relative to
this orchestrating session, whatever model the session runs on:

- `direct`: this session implements. A recorded `none` (the earlier name)
  means `direct`.
- `handoff`: "Phase handoff" below, a whole phase to a local session.

A recorded `files` mode is no longer available. Treat it
as no choice recorded: ask once whether to use `direct` or `handoff`, and
record the answer where the old value was.

Delegation was designed around a stronger orchestrator doing the judgment
and review. When this session itself runs on a local model, review is no
stronger than the delegated work unless the human switches to a different
model for review, so `direct` is the sensible default rather than
delegating to an equivalent local model.

With no choice recorded, ask once at the implement step; never assume one
from the policy alone.

## Preconditions — check all of them, and stop if any fails

1. An active spec resolves under `AGENTS.md` → **Shared workflow protocols**
   → **Active-spec resolution**, and it has a `## Success criteria` section.
   Ambiguity stops; never guess from the highest spec number.
2. The spec is `shipping` and carries an `## Approved implementation plan`
   whose `## Files to touch` lists every file the phase may change. The
   session may change nothing else except the spec's own handoff note.
3. The working tree is committed: the worktree starts from `HEAD`.
4. For `--phase implement`, tests for the plan exist and **currently
   fail**. Run the gate and show the failing output. If they pass or do not
   exist, stop and say `/test-first` has not run, or use
   `--phase test-first+implement`. If the spec replaced a value, name, or
   behavior, the `/test-first` rejected-value search must be recorded: an
   implement-only session cannot edit tests, so one stale assertion makes
   the phase unsatisfiable.
5. **No open choices are left to the session.** Where the spec and plan
   leave a choice open that a person will see or feel (size, layout, count,
   palette, wording, defaults), decide it from the spec's goal, the product
   spec, and the project's intent, and write the decision into the spec
   concretely before launching (precondition 3 then needs that edit
   committed): "a symmetric maze about 19×21 with blue
   walls `#1d4ed8` on black", not "an original maze". A local executor
   fills an open choice with the smallest thing the tests accept. List
   these decisions in your report; in an unattended run, log them under
   `## Unattended decisions`. If a choice is product direction rather than
   detail, ask the human instead.
6. Pi is installed (`pi --version`), and `policy` printed `auto`.

State which preconditions passed. If any failed, stop there with a
checkpoint report (`AGENTS.md` → **Shared workflow protocols** →
**Checkpoint report**).

## Phase handoff

The session runs in Pi whichever client orchestrates. Use it with
delegation `handoff` and every precondition above met.

1. Explain that a verified handoff is stored as a temporary commit on a
   `handoff/<run>` branch, separate from the feature branch, and ask for
   explicit current-chat authorization for that commit. Choosing delegation
   mode `handoff` by itself is not commit authorization. If the human does
   not authorize it, use `direct` instead. Once authorized, launch
   it:
   `bash .agentic/delegate-run.sh handoff <spec> --allow-commit --phase implement`
   (or `--phase test-first+implement`), with `--model <name>` when the
   human named one. The runner refuses without `--allow-commit`; pass it only
   for the authorization just obtained or recorded by `/unattended start`.
   The launch returns at once (exit 2 if refused) and prints the run id; the
   session runs detached for as long as it keeps working. Do not edit the
   repository while it runs.
2. Wait with
   `bash .agentic/delegate-run.sh handoff-status <run> --wait 540 --interval 60`.
   It reads local files only and costs no tokens. Repeat it while it exits 12
   (still running), adding `--since <cursor>` with the `cursor:` value the
   previous call printed, so each snapshot summarizes only what happened
   since; 540 seconds stays under every client's tool time limit. The
   interim lines show liveness, not progress: do not report them as work done.
3. Act on the exit status:
   - 0: verified (full gate green, only plan files changed, no test file in
     an implement-only phase) and committed on `handoff/<run>`. Read the
     spec's `### After handoff` note in the worktree, then run `/review`
     (and `/review-adversarial` on meaningful features) on
     `git diff HEAD...handoff/<run>`. Fix findings yourself or hand off
     again; then `git merge --squash handoff/<run>` into the feature branch
     and continue with closeout.
   - 11 (paused): the session went quiet past its inactivity lease, hit an
     optional total ceiling, or was stopped, and was checkpointed; or its
     runner died (state `orphaned`). Nothing was gated or committed. Read
     the spec's `### Paused handoff` note in the worktree, then resume the
     same session and worktree with
     `bash .agentic/delegate-run.sh handoff-resume <run> --allow-commit`
     (add `--max-total <seconds>` to raise a spent ceiling) and wait again
     as in step 2. Resuming needs the same commit authorization as the
     launch. Or abandon it with `handoff-clean <run>`.
   - 10 (`needs_frontier`): the session ended with the gate red or changed
     nothing. Read its handoff note in the kept worktree, then either
     implement yourself from the branch's starting point or hand off once
     more with a short hint in the spec. When no candidate model was
     selectable, no session ran: implement yourself.
   - 4 (integrity): the session changed files outside the plan, or a test
     in an implement-only phase. Report to the human; do not merge.
   - 3 (infrastructure) or 2 (refused): report and implement yourself.
4. After merging or abandoning a handoff, remove its worktree and branch:
   `bash .agentic/delegate-run.sh handoff-clean <run>`
   (`handoff-clean` alone lists what is left with each run's state; `--all`
   removes every one that is not live). Record the review verdict with
   `record-review <run>` first ("Review of delegated work" below); a
   handoff is scored per phase, once per logical run however many times it
   paused.

To stop a live handoff deliberately, `handoff-stop <run>` asks it to
checkpoint and pause (exit 11 from `handoff-status`).

## Review of delegated work

If `.agentic/delegate/ledger.jsonl` has `green` handoff runs with no
`review` record yet, pass the reviewer that list — run ID, spec, and phase
for each — and ask it to tag every finding with the file it concerns and,
for delegated work, whether the cause is the session's own code or
something the spec or plan left out or contradicted. The reviewer still
reviews the whole change set against the spec; the list only makes
attribution explicit, so a gap in work that was never delegated is not read
as a model failure.

Once the final disposition is settled, the orchestrator — not the
read-only reviewer — records each handoff's outcome:
`bash .agentic/delegate-run.sh record-review <run> <verdict>`. Use
`model-defect` when a finding was a defect in the session's own
implementation, even if the orchestrator then fixed it; `spec-defect`
when the cause was something the spec or plan failed to state or
contradicted; otherwise `pass`, including when findings concerned other
files or docs. Tell the human the verdict and the record line the runner
prints. Older ledgers may also hold runs from a delegation mode that no
longer exists; those are history, and `record-review` refuses them.

## Codex sandbox

Under Codex, the default workspace sandbox blocks network access, including
the local model endpoint (and the npm registry when the checkout has not
installed its Pi packages). Ask the human to approve running the handoff
commands outside the sandbox or with a network-enabled permission profile,
or to run them in a terminal. Do not change the project's Codex sandbox
configuration.
