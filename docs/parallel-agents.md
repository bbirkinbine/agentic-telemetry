# Parallel agents and unattended runs

The default for this scaffolding is one session driving the loop with
the human at two checkpoints. This doc covers the patterns past that
default: several agents at once, and runs with nobody watching. Both
are additive — the loop itself (Spec → Plan → Test-first → Implement →
Verify) does not change; what changes is how many copies of it run and
who is watching the checkpoints.

## Degrees of autonomy

The loop is identical at every tier; what changes is what substitutes
for your attention. Pick the tier deliberately. The tier decides which
rungs of the completion ladder you activate, not which phases you run.

> **`/goal`, `/loop`, and `/sandbox` are Claude Code built-ins**, not
> slash commands this scaffold ships — there is no file for them under
> `.claude/commands/`. They come with the Claude Code CLI itself
> (version-gated where the version tag is noted below, e.g. `v2.1.139+`),
> so availability tracks your CLI version, not this template. Codex does
> not expose those names as scaffold workflows. Use the repository
> contract, its Stop hook, Codex sandbox/permission controls, and a
> runnable completion condition in the kickoff prompt.

| Tier | Your role | Claude Code | Codex |
| --- | --- | --- | --- |
| **1 · Attended** (default) | Both checkpoints, adjudicate `[ask-user]` findings live | Use the scaffold as shipped | Use the scaffold as shipped |
| **2 · Long autodrive** (one feature, you're nearby) | Same checkpoints; the implement-to-green stretch runs long without you | Set `/goal` from the spec's runnable success criteria after approving the plan | State the same completion condition in the kickoff prompt; keep the Stop hook and fresh review enabled |
| **3 · Unattended** ("just ship it" / overnight / away for the day) | Standing consent given up front; checkpoints collapse into the spec (`/unattended start` and `reconcile`) | `/goal` + `/sandbox` + scoped permissions | `codex --sandbox workspace-write` or the project permission profile + a tight spec + Stop hook; grant commit authority separately if intended |
| **4 · Babysitting / recurring** (no feature in flight) | None per iteration | `/loop <interval> <prompt>`; cap iterations or cost | Use an external scheduler around a bounded `codex exec` invocation; recurring scheduling is not a workflow shipped by this repository |

**The unattended ceiling is "ready-to-merge," not "merged."** Standing
consent covers resolving `[ask-user]` findings, but the git-workflow
rule still requires an explicit commit instruction — so a tier-3 run
ends at "branch green, review clean, awaiting commit" unless the
kickoff instruction explicitly granted the commit too. The last
irreversible act stays human by default.

## When to parallelize (and when not to)

Parallel agents buy *volume*, not quality. The published numbers cut
both ways: high-adoption multi-agent workflows correlate with far more
merged PRs, but also with much larger PRs and much longer review times.
Parallelism converts to real throughput only with the same discipline
the single-session loop enforces — small PRs, real review, partitioned
ownership.

Parallelize when:

- Two or more features are independent at the *file* level — disjoint
  modules, disjoint specs.
- One stream is long and unattended-safe (a migration sweep, a
  documentation pass) and would otherwise block interactive work.
- You want independent attempts at the same problem to compare (write
  two, merge the survivor).

Don't parallelize when the tasks share files (merge conflicts eat the
gain), when the work is exploratory (you'd be reviewing two wrong
directions instead of one), or when you can't give each stream its own
spec.

## Worktrees: one agent, one branch, one directory

Git worktrees give each agent an isolated checkout of its own branch —
no stepping on each other's working tree, no shared dirty state:

```bash
git worktree add ../myproj-42-user-prefs 42-add-user-prefs
cd ../myproj-42-user-prefs
claude  # or: codex
```

Conventions that keep this sane:

- **One worktree per spec/branch**, named after the branch. Remove it
  when the PR merges (`git worktree remove ../myproj-42-user-prefs`).
- **One writing agent per checkout, including the main one.** The
  SessionStart hook warns when a session opens on uncommitted changes it
  did not make; treat that as a second writer and move to a worktree.
- **Partition file ownership in the specs.** Each spec's `## Non-goals`
  should exclude the files the other stream owns. Two agents editing
  one module is a merge conflict you scheduled on purpose.
- **Each worktree gets the full scaffolding for free** — `.claude/`,
  `.codex/`, `.agents/`, `.agentic/`, and pre-commit travel with the
  checkout. Run `.agentic/toolchain.sh install` per worktree; the
  environment (`.venv/`, `node_modules/`) is per-directory.
- A terminal multiplexer (tmux pane per worktree) keeps the sessions
  glanceable; that's an ergonomic choice, not a requirement.

Do not use client conversation memory as cross-worktree state. Put durable
decisions and the latest `## Phase handoff` in the spec so either client can
resume from disk.

## Built-in multi-agent controls

Codex has built-in subagents and discovers the custom roles under
`.codex/agents/` after project trust. Ask Codex to delegate explicitly or
invoke a workflow that calls for the planner, test-first, or reviewer role.
Use `/agent` to inspect and switch among active threads. Review roles are
read-only by default; `test-first` is workspace-write so it can author tests.
Parent-session permission overrides can supersede an agent's default, so use
a read-only parent when testing a review boundary.

Claude Code's experimental agent teams put a lead session and several
teammates on a shared task list with inter-agent messaging. Enable them with
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` where that client version supports
the feature.

What maps directly onto this scaffolding:

- **The generated role adapters map the same role into each client.**
  Claude reads `.claude/agents/`; Codex reads `.codex/agents/`.
- Start with research/review topologies (parallel reviewers over one
  diff, parallel investigators over one codebase) before parallel
  *implementation* — review parallelizes safely because it doesn't
  write.
- Keep teams small (3–5 teammates) and partition file ownership exactly
  as with worktrees.
- Quality gates extend to task granularity via the `TaskCompleted` /
  `TeammateIdle` hook events (exit 2 blocks a task from completing) —
  the same gate-on-stop idea, per task instead of per turn.

## The completion ladder

"How do I stop the agent from declaring victory early?" has a layered
answer; each rung catches what the one below it misses:

1. **In-prompt check** — the spec's success criteria phrased as a
   runnable check ("done when `.agentic/toolchain.sh test tests/test_x.py` passes").
   Cheapest; easiest for a long session to drift past.
2. **Client completion condition** — Claude Code can use `/goal` where
   available. In Codex, put the runnable condition in the kickoff prompt
   and spec; the durable artifact matters more than client syntax.
3. **Stop hook** (on by default; removed by bootstrap's
   `--no-stop-gate`) —
   `gate-on-stop.sh` mechanically blocks ending a turn on a red gate.
   The shared hook retries once and then surfaces a still-red gate to the
   human, so this is a strong nudge, not an unbounded guarantee.
4. **Fresh-context verification** — `/review` + `/review-adversarial`
   in Claude or `$review` + `$review-adversarial` in Codex (or a
   verification teammate): a context that has not seen the implementation
   reasoning judges the result. This is the only rung that catches "the
   gate is green but the feature is wrong."

Use the ladder top-down when configuring an unattended run: the longer
nobody is watching, the more rungs you want active.

## Unattended runs

These are the tier 3–4 mechanics from the table above. For long
autonomous work — a product requirements document (PRD) with many
items, which in this scaffolding is `docs/specs/0000-product.md` plus
the issue backlog it points at, or a repo-wide sweep — the
working pattern is a *loop with externalized state*: progress
accumulates in files and git (specs, `## Phase handoff` sections,
commits on a branch), never only in the conversation, so each iteration
can start with a fresh context and pick up from disk.

- **Claude Code `/loop`** re-runs a prompt or command on an interval —
  it fits babysitting jobs (re-check CI, retry a flaky migration step).
- **Codex recurring work** should use an external scheduler around a
  bounded `codex exec` command. Keep the iteration cap and durable state
  explicit.
- **The Ralph-loop pattern** (official `ralph-wiggum` plugin) re-feeds
  one prompt until a completion sentinel or max-iterations — fits
  "work through this PRD item by item" (here: the `0000-product.md`
  roadmap pointers, one issue → spec → branch per item). Known
  failure modes: drift
  without a tight spec, and uncapped cost — set max-iterations.
- **A client completion condition + autodrive** covers the common middle:
  one feature, end to end, with the spec holding the finish line.

### One feature while you are away

One feature, run to "ready to merge" while nobody watches: overnight, a
day at a conference, a long meeting. Three steps:

1. **Before leaving: `/unattended start`.** After the plan is approved, the
   orchestrator asks every decision it can foresee in one batch (ambiguous
   criteria, deferred choices, scope edges, the accepted baseline, checks it
   cannot run itself, fallback on failure), each with a default. Answers and
   the standing consent go in the spec's `## Standing decisions`.
2. **During the run.** An `[ask-user]` that the standing decisions do not
   settle is answered with the human's most likely choice and logged in
   `## Unattended decisions` with its alternatives, reason, files, and how to
   reverse it. Irreversible steps and anything outside the approved plan
   still stop the run. Local delegation keeps running: a paused handoff is
   resumed, and a red one falls back to the orchestrating session.
3. **When you are back: `/unattended reconcile`.** Keep or override each logged
   decision; overrides go through the normal gate and a focused review.

The run never pushes or merges. Unless standing consent grants commits on
the feature branch, it ends uncommitted at "ready to merge".

Safety posture for unattended runs is different from interactive ones:
prefer the client's sandbox and a scoped permission mode over bypass flags;
the `block-destructive.sh` hook remains as a narrow backstop, not the primary
containment. Git and the phase handoff are the portable recovery points;
client-specific rewind features are conveniences, not workflow state.

## A weaker executor is a different question

Everything above scales the *number* of agents at one capability level.
Running a **weaker** model — a local open-weight model on your own hardware
— on part of the loop is a separate decision with a much narrower answer,
because the failure rate is high enough that only mechanically-detected
failure is affordable. The rule, the phase-by-phase table, and the
preconditions are in [`local-executor.md`](local-executor.md); the short
version is that the implement phase is the only delegable one, because it
is the only one `/review-check` can grade.

## What this scaffolding deliberately does not do

No orchestration framework, no agent-to-agent message bus, no custom
runner. Practitioner experience is consistent that elaborate
multi-agent setups underperform simple ones — plain worktrees, plain
specs, plain hooks — until the simple version is saturated. Saturate it
first.
