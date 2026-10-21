# Agentic workflow — visual map

> **Purpose.** The visual / systems companion to the methodology.
> `../AGENTS.md` is the canonical *rules* contract; `../CLAUDE.md` imports
> it for Claude Code;
> [`../WORKFLOW.md`](../WORKFLOW.md) is the prose *walkthrough* — what to
> run at each step, in order, and why. This file is the *map*: how the
> spec, branch, slash commands, subagents, hooks, and CI fit together, in
> diagrams. Read WORKFLOW.md for the *steps*; read this to see the
> *shape*.
>
> Diagrams are [Mermaid](https://mermaid.js.org/) — they render natively on
> GitHub and in Obsidian (VS Code needs the "Markdown Preview Mermaid
> Support" extension), and degrade to readable source everywhere else. This
> doc is generic scaffolding; nothing here is project-specific.
>
> New here? Start with [`project-types.md`](project-types.md) to choose a
> flavor and profile and see which agents, skills, and commands you get.
> This file is the loop those pieces run inside.

---

## Three actors

The loop has three actors, and most of the design is about keeping them in
their lanes:

- **You** — drive the loop, own the two checkpoints, write the spec and
  the commit message. The agent never commits without your approval
  (`AGENTS.md` → **Approval matrix**).
- **The agent** (main session) — the *orchestrator*: holds the spec, runs
  each phase, delegates focused work to subagents to keep its own context
  clean.
- **Automation** — hooks and CI that fire on their own (on edit, on
  turn-end, on commit, on PR) so discipline doesn't depend on memory.

The diagrams below colour these where it helps: **★ checkpoints** are
yours, **gates** are automated, **subagents** run in fresh context.

---

## The planning artifacts (how the three docs nest)

Three documents plan the work, broadest to narrowest. This is a
*hierarchy*, not a per-feature pipeline: the product spec is standing
context, ADRs appear only on Large cross-cutting work, and most features
go straight from product context to a spec (the dashed path).

```mermaid
flowchart TD
    P["Product spec (PRD)<br/>docs/specs/0000-product.md<br/>who it is for · success · kill criteria<br/>written once · evergreen"]
    P --> A["ADR<br/>docs/adr/NNNN-*.md<br/>a cross-cutting technical decision<br/>(Large work only)"]
    A --> S1["Spec<br/>docs/specs/NNNN-*.md<br/>one unit of work"]
    A --> S2["Spec<br/>another unit of work"]
    P -.->|"most features: no ADR"| S3["Spec<br/>one unit of work"]

    classDef prd fill:#fde68a,stroke:#b45309,color:#111;
    classDef adr fill:#ddd6fe,stroke:#6d28d9,color:#111;
    class P prd;
    class A adr;
```

Each narrower artifact links *up* to the broader one: a feature spec links
to the product spec instead of restating product rationale, and to any ADR
its approach depends on. Authoring flows for all three — in this same
broad-to-narrow order — are in [`../WORKFLOW.md`](../WORKFLOW.md) → "The
planning artifacts, broad to narrow."

---

## Day zero (once per project)

```mermaid
flowchart TD
    A0["Choose stack + profile<br/>(project-types.md):<br/>--minimal · --core · --full<br/>+ --strict-hooks? --no-stop-gate?"] --> A["git init + &lt;stack&gt;/bootstrap.sh --&lt;profile&gt;"]
    A --> A1["Codex: trust project layer + hooks<br/>Claude: restart for settings"]
    A1 --> B["Fill template placeholders"]
    B --> C["new-project checklist (in the agentic-scaffold repo,<br/>not copied here): README ack · GitHub About · identity (git user.email)"]
    C --> D{"Opt-in for THIS repo?"}
    D -->|"network / auth / untrusted input / secrets"| D1["copy security-reviewer"]
    D -->|"hot path / async / under load"| D2["copy performance-reviewer"]
    D -->|"large / long-lived repo"| D3["enable serena MCP<br/>(serena-setup.md)"]
    D -->|"small fresh repo"| E
    D1 --> E
    D2 --> E
    D3 --> E
    E[".agentic/toolchain.sh install · pre-commit install"] --> F["scaffolding commit on main<br/>— the one commit allowed there —<br/>then branch everything after"]
    F --> G["/product-spec — optional<br/>interview → docs/specs/0000-product.md<br/>problem · success metrics · kill criteria · non-goals"]

    classDef optional fill:#f3f4f6,stroke:#6b7280,color:#111,stroke-dasharray: 5 5;
    class G optional;
```

The profile decides how much scaffolding lands — see
[`project-types.md`](project-types.md) for the choice and the full list of
what each profile installs. The identity check is load-bearing:
`git config user.email` is baked into the first commit forever and leaks
once the repo flips public. Opt-ins are decided *now*, not retroactively —
see [`../WORKFLOW.md`](../WORKFLOW.md) "Day zero."

`/product-spec` is the product-level layer — the job a PRD does on a
team. It interviews you (seven questions, one at a time) and writes
`docs/specs/0000-product.md` from the answers; feature specs link up to
it instead of restating product rationale. Optional on day one — a
README purpose paragraph covers a small project — but write it before
the backlog outgrows your head or before any multi-spec autonomous run.
Canonical description in [`specs/README.md`](specs/README.md) → "The
product spec."

---

## The per-feature loop

Set up once, then loop. The top is linear — issue, spec, plan, branch,
first tests. Then **implement → check → review repeats until every
success criterion in the spec passes.** Then you ship.

```mermaid
flowchart TD
    ISS["Number the work: next spec number<br/>(issue mode: create the issue — its number<br/>names the spec · branch · PR)"]
    ISS --> SPEC["spec workflow<br/>Claude /spec · Codex $spec<br/>goal · success criteria · non-goals"]
    SPEC --> PLAN["plan workflow<br/>agent lists the files to touch + the order"]
    PLAN --> OK1{"★ Plan look right?"}
    OK1 -->|"no — fix the spec"| SPEC
    OK1 -->|yes| BR["Branch: spec-NNNN-slug<br/>(issue mode: issue#-slug — never on main)"]
    BR --> TF["test-first workflow<br/>failing tests from the spec"]
    TF --> IMPL

    subgraph LOOP["Build loop — repeat until the spec is finished"]
      IMPL["Implement<br/>minimum code to pass the tests"]
      IMPL --> CHK["review-check workflow<br/>toolchain.sh lint · format · typecheck · test — must be green"]
      CHK --> REV["review workflow<br/>fresh agent checks the diff against the spec"]
      REV --> DONE{"Every success<br/>criterion met?"}
      DONE -->|"no — fix and go again"| IMPL
    end

    DONE -->|"yes ★"| DOCS["Sync docs/ to the change<br/>README only if the surface changed"]
    DOCS --> SHIP["Commit + open PR<br/>(issue mode: Closes #N) — you write the message"]
    SHIP --> MERGE["CI green → merge → next feature"]

    classDef checkpoint fill:#fde68a,stroke:#b45309,color:#111;
    classDef gate fill:#bbf7d0,stroke:#15803d,color:#111;
    classDef subagent fill:#ddd6fe,stroke:#6d28d9,color:#111;
    class OK1,DONE checkpoint;
    class CHK gate;
    class PLAN,TF,REV subagent;
```

**You stop at two ★ checkpoints; the agent drives the loop between
them.** Checkpoint one: approve the plan (a wrong plan is a one-paragraph
spec fix — much cheaper than catching it later). Checkpoint two: decide
the spec is finished before you commit.

**The number comes first** — in the default local mode it is simply the
next spec number; in issue mode the issue is created first (an issue is
a work item, like a Jira or Linear ticket, not just a bug report) and
its number is the shared id for the spec, the branch, and the PR's
`Closes #N`. The number is identity, not order; see
[`specs/README.md`](specs/README.md) → "Numbering".

**Optional sharpening passes** slot in where each helps and are left off
the diagram to keep it clean: `/scope-check` (fuzzy goal) and `/clarify`
(open questions) before building, `/analyze` (tests cover the spec?)
after `/test-first`, and `/security` and `/performance` during review. See
[`../WORKFLOW.md`](../WORKFLOW.md).

---

## The automation layer (fires on its own)

The linear loop above hides the guardrails firing around it. These need no
slash command — they trigger on lifecycle events so "I forgot to run the
gate" stops being a failure mode.

```mermaid
flowchart LR
    W["your work<br/>(edit · run · commit)"]
    SS["Session start"] --> SSb["shared branch-check.sh<br/>warn if on main or tree already dirty"] --> W
    RL["reading src/** or tests/**"] --> RLb["AGENTS.md standing rules<br/>+ Claude path-scoped adapters"] --> W
    W --> PRE["before each Bash<br/>(PreToolUse)"] --> PREb["block-destructive.sh<br/>deny rm -rf /, git clean -fd, …"]
    W --> POST["after each edit tool<br/>(Claude Edit/Write · Codex apply_patch)"] --> POSTb["toolchain.sh format<br/>(+ lint · typecheck with --strict-hooks)"]
    W --> CMP["after compaction<br/>(SessionStart compact)"] --> CMPb["rebuild spec · branch ·<br/>modified files · gate state from the repo"]
    W --> STOP["turn end<br/>(Stop — default; --no-stop-gate removes)"] --> STOPb["shared gate-on-stop.sh<br/>block if src/ dirty & gate red<br/>(one automatic retry)"]
    W --> PC["git commit<br/>(pre-commit)"] --> PCb["no-commit-to-branch · gitleaks · detect-private-key"]
    W --> CIp["pull request<br/>(GitHub Actions)"] --> CIb["CI: the four toolchain.sh steps + dependency audit — non-skippable<br/>+ claude-review.yml if enabled"]

    classDef auto fill:#bfdbfe,stroke:#1e40af,color:#111;
    class SSb,RLb,PREb,POSTb,CMPb,STOPb,PCb,CIb auto;
```

Behaviour, edge cases, and how to bypass each (e.g. the Stop hook,
`--no-verify` for the day-zero commit) live in
`../AGENTS.md` → **Hooks and guardrails**. The line `block-destructive`
draws is *unrecoverable* — things the reflog or a re-clone can't bring
back; merely risky-but-recoverable commands stay off it. OS-level
sandboxing and permission modes sit above all of these for
unattended runs.

---

## Scale the loop to the task

Heavyweight process on trivial work is its own failure mode. Pick the path
by size:

```mermaid
flowchart TD
    Q{"How big is the task?"}
    Q -->|"trivial — rename, typo, ≤10 lines"| TRIV["Just do it.<br/>branch optional · skip spec & plan"]
    Q -->|"small — one function, one file"| SMALL["branch · spec = 1 sentence<br/>skip /plan unless approach is unclear<br/>/test-first still required"]
    Q -->|"medium — 3–10 files"| MED["Full loop. (Where it shines.)"]
    Q -->|"large — refactor / new subsystem"| LARGE["Split into medium tasks first<br/>one spec + branch each"]
    LARGE --> SEQ["dependent pieces:<br/>loop × N, sequential<br/>phase handoff between sessions"]
    LARGE --> PAR["independent pieces:<br/>one worktree + agent per spec<br/>file ownership partitioned in non-goals"]
```

Implementation that needs **files outside the approved plan**, or
Trivial/Small work that outgrows its size, is a stop-and-ask and a re-size,
not a proceed-anyway — see `../AGENTS.md` "Your role: orchestrator." A
complex program is not a bigger loop — it is the same medium-sized loop run
*N* times over a split backlog, sequentially when the pieces depend on each
other, in parallel worktrees when they don't
([`parallel-agents.md`](parallel-agents.md)).

---

## When to use what

The loop is fixed; everything else is opt-in. One row per decision —
the *Skip when* column is as load-bearing as the *Reach for* column.

| Situation | Reach for | Skip when |
| --- | --- | --- |
| Backlog outgrows your head, or a multi-spec autonomous run is coming | `/product-spec` — interview → `docs/specs/0000-product.md` | Small project where the README purpose paragraph still covers it |
| Goal itself is fuzzy ("we should do something about X") | `/scope-check` before `/spec` | The goal is already one concrete sentence |
| A spec can't start until other specs ship | `**Depends on:** NNNN` in its header; `/specs-status` shows `(blocked)` | No cross-spec ordering — most specs |
| Spec draft has real unknowns (data shapes, failure behavior) | `/clarify` after editing the spec | The spec is tight; don't invent questions |
| Want proof tests cover the spec before implementing | `/analyze` after `/test-first` | Trivial/small tasks; one-criterion specs |
| Network surface, auth, untrusted input, secrets | `security-reviewer` (opt-in, decide at day zero) | Pure-local tooling with no trust boundary |
| Hot path, DB on user-sized data, async, latency SLO | `performance-reviewer` (opt-in) | Nothing runs under load |
| Agent burns turns re-mapping a large, long-lived repo | `serena` MCP ([`serena-setup.md`](serena-setup.md)) | Fresh or small repo — grep is enough |
| Two+ features independent at the file level | Worktrees, one agent each ([`parallel-agents.md`](parallel-agents.md)) | Tasks share files, or work is exploratory |
| Long run with nobody watching | Completion ladder rungs 2–4 + scoped client permissions | You're at the keyboard — checkpoints suffice |
| Feature spans sessions | `## Phase handoff` + `/clear` | Single-session features — pure overhead |
| Recurring agent mistake | A line in `AGENTS.md`, kept identical in `CLAUDE.md` | One-off slip — correcting in-session is enough |
| Want another review without a human reviewer handy | `$review` / `codex review` locally, or `claude-review.yml.example` for Claude CI (rename; bills an API key) | The normal fresh-context review before the PR already covers it |
| Many projects consuming this scaffolding | Plugin packaging ([`plugin-packaging.md`](plugin-packaging.md)) | `bootstrap.sh --update` still takes seconds |

---

## Multi-day: phase handoff

Single-session features run the loop end-to-end. When a feature spans
sessions, running it all in one context degrades review quality (the
U-curve). Reset at a phase boundary instead:

```mermaid
flowchart LR
    P1["Phase N done<br/>(plan accepted, or review-check green)"] --> H["append ## Phase handoff<br/>to the spec"]
    H --> CL["start a fresh client session<br/>(Claude: /clear)"]
    CL --> P2["Phase N+1: fresh session<br/>re-reads contract + spec + diff"]
```

The two boundaries worth a fresh session: after `plan` is accepted (before
`test-first`), and after `review-check` passes (before `review`). Claude can
use `/clear`; either client can exit and restart from the durable handoff.
Section shapes are in [`specs/README.md`](specs/README.md).

---

## Go deeper

- [`project-types.md`](project-types.md) — choose a flavor and profile,
  and the matrix of which agents, skills, and commands each one installs.
- [`../WORKFLOW.md`](../WORKFLOW.md) — the step-by-step walkthrough:
  day-zero setup and the per-feature loop, one line of why per step.
- `../AGENTS.md` — the canonical rules every client reads (Claude through
  `../CLAUDE.md`): delegation, git workflow, hooks, public-repo hygiene.
- [`codex-cli.md`](codex-cli.md) — Codex trust, invocation, switching,
  and non-interactive use.
- [`specs/README.md`](specs/README.md) — spec numbering (identity, not
  order), status vocabulary, the `0000-product.md` product spec,
  `## External references`, `## Phase handoff`, `## Implementation Notes`.
- [`parallel-agents.md`](parallel-agents.md) — degrees of autonomy, the
  completion ladder, worktree parallelism, agent teams, unattended runs.
- [`agent-handoff.md`](agent-handoff.md) — the operational runbook:
  current state, known risks, accepted commands, rollback playbook.
- [`plugin-packaging.md`](plugin-packaging.md) — the (not-yet-adopted)
  plugin/marketplace distribution path.
- [`serena-setup.md`](serena-setup.md) — the optional symbol-navigation MCP.
