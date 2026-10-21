# Workflow — how to use this scaffolding

Companion to the shared `AGENTS.md` project contract. Codex and Pi read it
directly; Claude Code loads it through the `@AGENTS.md` import in
`CLAUDE.md`. This file is the human's step-by-step — what to run, in order,
with a one-line reason for each step.

The whole thing is five phases: **Spec → Plan → Test-first → Implement →
Verify.** Each workflow entry runs one phase. That ends a command, not
your permission: you approve the spec, the plan, scope changes, and the
commit, and the agent drives from plan approval through clean review
(`AGENTS.md` → **Approval matrix**). Claude Code and Pi use `/<name>` project prompts; Codex uses
`$<name>` repository skills (or `/skills`). Pi can also use the same portable
skills as `/skill:<name>`. New to the terms? A *spec* is a short
design note; a *subagent* is a fresh agent with its own clean context.

## The short version

Day zero, once: the founding session ran the bootstrap and drafted
`AGENTS.md` from your description. Read that file and cut what is wrong,
run `.agentic/toolchain.sh install`, install the commit guard with the
`pre-commit install` line the bootstrap printed, restart the agent so its
hooks load, and make the one scaffolding commit on `main`.

Every feature, in order:

1. `/spec <name>`, then read and edit what it wrote.
2. Branch: `git switch -c spec-NNNN-<slug>`. Never build on `main`.
3. `/plan`, approve or correct it. Skip for a one-file change.
4. `/test-first`, confirm the new tests fail for the right reason.
5. Implement the minimum that passes.
6. `/review-check` (the gate), then `/review` (a fresh agent reads the diff
   against the spec). Fix what they find and rerun.
7. Commit with your own message; open the PR; CI runs the same gate.

The Stop hook will not let the agent end a turn on a red gate, and the
agent never commits without your approval. Everything below is the same loop with the
reasons, the options, and the edge cases.

## Day zero (once per project)

1. **Run bootstrap.** Normally the founding session did this after choosing
   the stack from your description (scaffold `README.md` → "Start here").
   By hand: `bash path/to/agentic-scaffold/<stack>/bootstrap.sh`
   (wherever you cloned this repo; `python`, `typescript`, or `custom`) — drops the
   default `--core` scaffolding into your repo. Use `--minimal` for a thinner starter and
   `--full` for the author's full workflow bundle. The Stop gate (blocks
   ending a turn on a red gate) is on by default — `--no-stop-gate`
   removes it; add `--strict-hooks` if you also want edit hooks to run
   lint/type checks after every edit. Restart the client after setup so
   its settings and hooks load. In Codex, review the project trust prompt
   and run `/hooks` to trust the checked-in hook definitions; see
   `docs/codex-cli.md`. In Pi, approve project trust and restart or `/reload`
   so settings, prompts, the local extension, and `pi-subagents` load; see
   `docs/pi-agent.md`.
2. **Fill the placeholders.** `rg '\{\{' .`, then replace every `{{...}}`
   — the agent reads the project contract, so a leftover placeholder
   misleads it. One placeholder is the starter package directory
   `src/agentic_telemetry/` — rename it to your package name. Own the
   canonical `AGENTS.md` content (description, don't-touch list,
   conventions); leave `CLAUDE.md` as the `@AGENTS.md` compatibility
   import. Draft the contract by hand or with the agent, your call, but
   read and cut every line rather than committing a generated file unread.
   The study measured agent-generated context files at 0.5–2% below no
   context file and developer-written ones at 2.4% above; neither
   difference was statistically significant. It did find 20–23% higher
   inference cost for agent-generated files and up to 19% for
   developer-written files. Keep the contract short for that recurring
   cost and Codex's finite combined instruction budget, not because the
   study found short files more successful — file length itself had no
   significant effect on success. The rule is ownership, not authorship
   (see the scaffold's `python/README.md` → "Don't" for the study and the
   numbers). Mechanical fills like the project name in the manifest are fine
   to delegate.
3. **Set up git identity and GitHub.** `git config user.email` must be
   your GitHub noreply address — it is baked into the first commit
   forever. Then add the README AI-acknowledgement line and fill the
   GitHub "About" sidebar. As soon as the GitHub repo exists (first
   push), run `gh repo edit --delete-branch-on-merge` so merged PR
   branches auto-delete instead of piling up — or run
   `bash path/to/agentic-scaffold/scripts/protect-main.sh`, which
   enables it alongside branch protection. Skip both on a repo with no
   GitHub remote. (A fuller checklist, including the private→public
   hygiene scrub, lives in the agentic-scaffold repo as
   `new-project-checklist.md` — it is not copied into your project.)
4. **Decide opt-in reviewers now.** Security and performance reviewers
   are off by default; turn them on if the project needs them (see the
   scaffold's `python/README.md` → "Opt-in subagents").
5. **Install the dev tools.** `.agentic/toolchain.sh install`, then wire
   the commit guard with the `pre-commit install` line the bootstrap printed
   — dependencies plus the guard that keeps work off `main`. On the custom
   stack the runner is a template: skip the install, wire the commit guard,
   and fill `.agentic/toolchain.sh` (its header says how) in the first spec
   that adds code to check, then set `TC_CONFIGURED=1` to activate it.
   Once active, configuration errors fail validation. Until then the Stop
   gate and CI's quality job stay quiet and `/review` is the verification.
6. **Issue mode only — create the GitHub issue labels** the issue forms
   use: `feature`, `bug`, `spec-needed`, `triage` (e.g.
   `gh label create spec-needed`). In the default local mode, skip
   this; specs use the next local number and no issues are involved
   (see `docs/specs/README.md` → "Numbering").
7. **Optional — `/product-spec` if installed.** Interviews you and writes
   `docs/specs/0000-product.md`, the product-level "what is this, and who
   is it for." Skip it for a small project. See "Authoring the product
   spec (PRD): the interview" below for when and how.

## Every feature (the loop)

Run these in order. Steps marked *optional* are skippable when the answer
is already obvious.

1. **Number the work.** Default local mode: nothing to do — `/spec`
   takes the next local `docs/specs/NNNN-*.md` number. In issue mode
   (the repo tracks its backlog as GitHub issues; recorded in
   `AGENTS.md`), create the issue first — an issue is a work item, like
   a Jira or Linear ticket, not just a bug report — and its number names
   the spec, the branch, and the PR.
2. **`/spec <name>`** — write a short spec (goal, success criteria,
   non-goals), or — if you have already discussed the feature in this
   session — have `/spec` draft it from that discussion. Either way you
   review and edit it before moving on; this is the source of truth every
   later step checks against.
   - *Optional, if installed:* `/scope-check` before (fuzzy goal),
     `/clarify` after (open questions) to sharpen it.
   - See "Authoring a spec: three styles" below for the write-it-yourself,
     discuss-then-draft, and let-the-agent-interview-you flows.
3. **Make a branch** named `spec-NNNN-<slug>` (or `<type>/<slug>` for
   tiny untracked work) — `<issue#>-<slug>` in issue mode. Never build on
   `main`.
4. **`/plan`** — the agent lists the files to touch and the order.
   Review it before any code; a wrong approach is cheap to fix here. When
   you approve it, the orchestrator copies the approved plan into the spec
   under `## Approved implementation plan`, updates `Last updated`, and marks
   the spec `shipping`. This happens before `/test-first` and before any
   `/clear` or client switch, so the approved plan survives the chat. Small
   work that explicitly skips `/plan` still moves the approved spec to
   `shipping` immediately before `/test-first`.
5. **`/test-first`** — a test-first subagent writes failing tests from the
   spec. The orchestrator records the workspace before the subagent, audits
   afterward that only tests changed, and independently reruns the focused
   tests. Any implementation edit is a hard phase failure; a red result counts
   only when it is caused by the missing behavior. Tests written *after* the
   code just rubber-stamp whatever you built.
   - *Optional, if installed:* `/analyze` invokes a fresh read-only analyzer
     to confirm every success criterion has a meaningful test.
6. **Implement.** Write the minimum code to make the tests pass.
7. **Sync the docs.** Sweep `docs/` for statements the implementation made
   false — commands, file lists, described behavior, architecture notes — and
   fix them before review so the reviewer sees the final documentation too.
   `README.md` remains high-level: update it only when the pitch, install
   steps, or user-facing surface changed. The agent may create a durable doc
   when the feature needs one, but extending an existing doc beats adding a
   maintenance surface. Flip the active spec's `**Status:**` to `shipped` and
   regenerate its dashboard here, on the feature branch.
8. **`/review-check`** — runs the gate through `.agentic/toolchain.sh`
   (lint, format, typecheck, tests; the stack's tools behind it, listed in
   `AGENTS.md` → "Stack"). It
   must be green, but green is not the final checkpoint; semantic review
   still follows.
9. **`/review`** (and `/review-adversarial` on meaningful changes, if
   installed) — a fresh read-only agent reviews the active spec against the
   complete pre-commit change set: committed branch delta, staged and unstaged
   tracked changes, and every untracked file. This catches what the gate can't.
   - *Optional:* `/security` and `/performance` if you installed them.
10. **Close review findings.** The orchestrator applies `[auto-fix]`
    findings, resyncs any affected docs, reruns the complete gate, and asks a
    fresh reviewer to verify the fixes and inspect their delta. A substantial
    fix gets a full review again. Repeat until no must-fix finding remains;
    `[ask-user]` is a hard stop for your decision, except in an unattended
    run with standing consent, where `/unattended` answers and logs it for
    you to reconcile when you are back. A rerun of lint/tests alone
    does not close a semantic finding.
11. **Final checkpoint, then commit and open the PR.** The agent reports the
    spec, complete changed-file set, red → green evidence, latest gate, review
    disposition, and unresolved decisions, then stops for commit approval. You
    write or approve the commit message; "commit and push" approves both. In
    issue mode, the PR body says `Closes #<issue>` so merging closes
    the issue; the default local mode omits the closing keyword. A
    change's own close-tasks (the spec `Status` flip above, dashboard regen,
    todo ticks) already ride in this reviewed change, not a follow-up. The
    `closeout` CI job enforces the machine-checkable half on every PR — it
    fails while the spec still says `draft`/`shipping` or the dashboard is
    stale. Run
    `.agentic/hooks/closeout-check.sh` before opening the PR rather than
    finding out from CI.

## The planning artifacts, broad to narrow

Three documents plan the work, and they nest — broadest to narrowest:

1. **Product spec (PRD)** — `docs/specs/0000-product.md`. The product
   itself: who it is for, what success looks like, what would kill it.
   Standing context that everything below links up to.
2. **ADR** — `docs/adr/NNNN-*.md`. A cross-cutting technical decision that
   several features inherit and is costly to reverse. Sits between the
   product and the feature work that lives under the decision.
3. **Spec** — `docs/specs/NNNN-*.md`. One unit of work. Links up to the
   product spec, and to any ADR its approach depends on.

This is a *hierarchy*, not a mandatory pipeline. Most features are just
"product spec as standing context → spec → build" with **no ADR at all** —
the product spec is written once (day zero, or when the backlog outgrows
your head) and refreshed rarely, ADRs appear only on Large cross-cutting
work, and specs are the everyday artifact. The map form of this nesting is
in [`docs/workflow-diagram.md`](docs/workflow-diagram.md). The three
authoring guides below run in that broad-to-narrow order.

### Authoring the product spec (PRD): the interview

**When to reach for it.** The product spec (`docs/specs/0000-product.md`)
is the PRD-level layer — the problem and who it is *for* and *not for*,
success metrics, kill criteria, product non-goals, constraints. Feature
specs link up to it instead of restating product rationale. Write it when
the backlog outgrows your head, or before any multi-spec autonomous run; a
small project can lean on the README purpose paragraph until then. One
file, reserved number `0000`, status `evergreen` — revised in place, never
shipped or closed.

**The flow is an interview, by design.** Unlike `/spec` and `/adr` (below),
`/product-spec` does not draft from inference — a blank PRD template gets
ignored and an AI-guessed one is worse than none, so it asks and you
answer:

```text
/product-spec        # create mode: 7 questions, one at a time — you answer
                     # → writes docs/specs/0000-product.md from your answers
...
/product-spec        # later: refresh mode — asks only what has gone stale (max 3),
                     #        or says "product spec is current" and stops
```

Two modes: **create** (file absent — the seven questions) and **refresh**
(file exists — it compares against the open issues and asks only about
sections that are missing, vague, or contradicted). You can hand-write the
file instead, but the interview exists because that is what reliably gets
it written in practice. `/product-spec` installs with `--core` and `--full`.

### Authoring an ADR: when, and two styles

**When to reach for an ADR.** An ADR (`docs/adr/NNNN-<slug>.md`) records a
*cross-cutting technical decision* — one that several features inherit and
that is costly to reverse (a storage engine, an async/sync boundary, a
public API shape, an auth model, a serialization format). If a decision
affects only the feature in front of you, it belongs in that feature
spec's `## Sketch`, not an ADR. Mostly **Large** work. ADRs are numbered
by their own sequence (the next ADR number, not a spec number). The full
spec-vs-ADR decision table is in
[`docs/adr/README.md`](docs/adr/README.md).

`/adr` is one-shot and stops for your review — there is no separate
interview command, because the design discussion *is* the interview. Two
styles:

**1. Write it yourself.** Run `/adr "<decision title>"`, get the Context /
Decision / Consequences / Alternatives skeleton, fill it in.

**2. Discuss, then draft.** Weigh the options with the agent — constraints,
trade-offs, what argues against each — then run `/adr`. It drafts the four
sections from that discussion and marks anything it guessed with
`<!-- assumption -->`.

```text
You:  Postgres or SQLite for the event store? ... (weigh durability,
      concurrency, ops cost)
...   (back-and-forth)
You:  /adr "event store engine"
AI:   writes docs/adr/00NN-event-store-engine.md from the discussion, then stops
```

Either way you review and edit the rationale before the decision is acted
on. An accepted ADR changes later by being *superseded*, not edited — a
new ADR that links back to it (see [`docs/adr/README.md`](docs/adr/README.md)).

### Authoring a spec: three styles

The spec is the one artifact worth getting right — every later step checks
against it. `/spec` is one-shot (it writes the file and stops for your
review); it is not itself an interview. Pick the style that matches how
much you have already thought the feature through:

**1. You write it.** You already know what you want. Run `/spec <name>`,
get the skeleton, and fill `## Goal` / `## Success criteria` /
`## Non-goals` by hand.

**2. Discuss, then draft.** Talk the feature through with the agent first
— this is ordinary conversation, not a command — then run `/spec`. It
drafts the spec from that discussion and marks anything it had to guess
with `<!-- assumption -->`.

```text
You:  Add per-account user prefs — theme + default timezone.
      Skip cross-device sync for now.
...   (back-and-forth as needed)
You:  /spec add-user-prefs
AI:   writes docs/specs/00NN-add-user-prefs.md from the discussion, then stops
```

**3. Let the agent interview you.** The goal is fuzzy and you want the
questions driven for you. `/scope-check` and `/clarify` are the turn-by-turn
Q&A passes (one question at a time); `/spec` sits between them:

```text
/scope-check add-user-prefs   # AI asks 5 forcing questions, you answer
/spec add-user-prefs          # AI drafts the spec from those answers
/clarify                      # AI asks up to 5 more, folds each answer back in
/plan
```

(`/scope-check` and `/clarify` install with `--core` and `--full`,
not `--minimal`.) Whichever style you use, `/spec` stops after writing —
you review and edit before `/plan`. The spec stays human-owned; drafting
just gets you to a reviewable first pass faster.

## Scale to the task

Don't run the full loop on tiny work.

| Task | Do |
| --- | --- |
| Trivial — rename, typo, ≤10 lines | Just do it. Skip spec/plan; branch optional. |
| Small — one function | Branch + one-sentence spec; `/test-first`; skip `/plan` unless the approach is unclear. |
| Medium — 3–10 files | The full loop above. |
| Large — new subsystem | Capture the cross-cutting technical decision in an ADR (`/adr`) first, then split into medium pieces, one spec each. |
| Research, measurement, or docs — no code to test | Spec whose success criteria name the evidence each will be checked against; `/plan` if it is more than one sitting; skip `/test-first` and the gate; `/review` checks the evidence against the spec. |

A task between rows takes the larger one.

A throwaway script needs none of this — just write the code.

## Phase handoff (multi-day features)

A feature that spans sessions reviews badly when one session carries all
the spec, plan, test, and implementation context. At a phase boundary,
append a `## Phase handoff` block to the spec, run `/clear`, and resume
fresh. Boundaries worth a reset: after `/plan` is approved, and after
docs are synced and `/review-check` is green. Before the first reset, persist
the approved plan under `## Approved implementation plan` and mark the spec
`shipping`; a chat-only plan is not a handoff. Section shapes:
`docs/specs/README.md`.

## Session hygiene

Context is the resource the whole loop runs on; treat it deliberately.

- **Audit what loads by default.** Once per project (and after adding
  any MCP server or skill), open a fresh empty session and inspect the
  client's context/configuration view (`/context` in Claude Code;
  `/skills`, `/hooks`, and the startup status in Codex). A stale MCP
  server or rarely-used skill is pure overhead; scope it to projects
  that use it.
- **Watch the percentage, not the limit.** The status line shows context
  usage every turn. Output quality degrades well before the hard limit
  — practitioners commonly report a soft zone around 40–50% — so treat
  a rising number as the cue to reach a phase boundary, not as budget
  still available.
- **Compact deliberately, and not forever.** Prefer a manual `/compact`
  at a natural boundary over waiting for automatic compaction at the
  limit. But repeated compaction of one long thread accumulates drift:
  after a compact or two, write the `## Phase handoff` block into the
  spec and `/clear` instead. The spec is the durable memory; the
  conversation is not.
- **Audit client memory occasionally.** Where the active client exposes
  durable memory controls, inspect them. A stale entry steers future
  sessions; prune it like a wrong line in `AGENTS.md`.

## The completion ladder

"The agent said done, but it wasn't" has layered fixes — use more the
longer nobody is watching:

1. Success criteria written as a runnable command.
2. A durable completion goal checked independently from the implementation.
3. The Stop hook (on by default) — blocks ending a turn on a red gate.
4. A fresh-context `/review` — the only rung that catches "gate green but
   feature wrong."

Detail and the client-specific autonomy controls: `docs/parallel-agents.md`
(installed with `--full` / `--advanced-docs`).

## Good to know

- **The project contract is durable state.** Edit `AGENTS.md` when a standing
  rule changes; leave `CLAUDE.md` as the thin `@AGENTS.md` import.
- **Subagents don't see your chat.** Put anything the reviewer needs in
  the spec, not in a message.
- **Specs are permanent** — they are the design log, not deleted after a
  feature ships.
- **CI is the gate you can't skip.** Local hooks can be bypassed; a red
  PR is not done.

## Going deeper

Some files below depend on the bootstrap profile. `--minimal` installs the
core loop; `--core` adds ADR/status/workflow docs; `--full` or
`--advanced-docs` adds the advanced doctrine docs.

- [`docs/project-types.md`](docs/project-types.md) — the orientation map:
  flavors, profiles, the capability matrix, and when to reach for each
  agent, skill, and command. Start here if you are new to the scaffolding.
- [`docs/workflow-diagram.md`](docs/workflow-diagram.md) — the same loop as
  a visual map (Mermaid diagrams).
- [`docs/codex-cli.md`](docs/codex-cli.md) — Codex trust, workflow mapping,
  client switching, non-interactive use, and guardrail differences.
- [`docs/pi-agent.md`](docs/pi-agent.md) — Pi trust/reload, prompt and
  subagent mapping, provider-neutral model use, and guardrail limits.
- `docs/specs/README.md` — spec numbering, the opt-in issue mode, the product spec, section shapes.
- `docs/adr/README.md` — architecture decision records: when a choice is
  cross-cutting and costly to reverse, log the decision and its rationale
  (spec-vs-ADR table, numbering, template). Mostly for Large work.
- `docs/parallel-agents.md` — autonomy tiers, worktrees, unattended runs.
- `docs/local-executor.md` — handing the implement phase to a weaker or
  local model: the one rule that makes it safe, what to delegate and what
  never to, and why the failures are serialization rather than reasoning.
- `docs/agent-handoff.md` — operational runbook: risks, rollback, "when X breaks."
- `AGENTS.md` — the single contract every client follows; `CLAUDE.md` imports
  it for Claude Code.
