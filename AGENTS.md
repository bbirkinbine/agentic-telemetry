# Project: agentic-telemetry

The private half of the agentic-scaffold telemetry plan (vault: `Projects/Agentic Scaffold Telemetry/`). The scaffold plants the sensors: a tool-call ledger in every bootstrapped project, run IDs on delegate handoffs. This repo collects and judges the readings: the homelab collector VM (`collector/`), a Python package that reads ledgers, delegate results, client session transcripts and local-LLM server metrics into one run table, the per-model failure matrices, and a small review app for labeling the first failure in a run. Purpose: find patterns that say whether to adjust the coding agent, the delegation packet, or the local model server, and rank served model configurations on this owner's own tasks.

Treat this repo (`github.com/bbirkinbine/agentic-telemetry`) as **public**
from commit #1, whether or not it is yet: it starts private and will flip to
public later, and its whole history flips with it. No credentials, tokens,
captured tool content, internal hostnames, IPs or home-directory paths in any
tracked file, commit message or branch name. Real endpoints live in untracked
`.env` files and in the vault's `ENDPOINTS.md`; tracked configs carry
placeholder hostnames.

Placeholders come from the reserved documentation namespaces: domains under RFC 2606 (`telemetry.example`,
`gpu.example`, `laptop.example`), IPv4 in RFC 5737 (`192.0.2.0/24`,
`198.51.100.0/24`, `203.0.113.0/24`), IPv6 in RFC 3849 (`2001:db8::/32`). They
are self-evidently fake and cannot collide with real infrastructure. Never use
angle-bracket or made-up names for an address.

`AGENTS.md` is the single client-neutral project contract. `CLAUDE.md` is a
Claude Code import containing only `@AGENTS.md`. In a bootstrapped project,
edit `AGENTS.md`, not the import. In the scaffold, edit `workflow/` sources
and render the client surfaces.

Some referenced workflows and docs require `--core` or `--full`; use
them only when installed.

Standing rules are included below. Put project-specific additions outside the
marked generated block; never duplicate shared policy in a client directory.

This contract writes workflows as `/name`. Claude Code and Pi use that project
prompt; Codex uses `$name` or `/skills`. Pi can also use `/skill:name`. All
surfaces render from the same source.

## Shared workflow protocols

These defaults are authoritative for every phase workflow.

### Active-spec resolution

Resolve the active feature spec in this order:

1. If the arguments name a spec, require exactly one existing feature markdown
   path under `docs/specs/`.
2. Otherwise derive the number from `spec-NNNN-<slug>` (local mode) or
   `<issue-number>-<slug>` (issue mode), zero-pad it, and require exactly one
   `docs/specs/NNNN-*.md` match.
3. Otherwise require exactly one feature spec with `**Status:** shipping`.

On zero, multiple, ambiguous, or nonexistent matches, stop, list candidates,
and request an explicit path. Never choose the highest number: numbers are
identities, not sequence. `0000-product.md` is context, never the active spec.

### Checkpoint report

End every deliberate stop with: what completed; why it stopped; what you need
from the human; the exact command or reply that continues. Omit empty fields.

### Approval matrix

The one approval policy; other files add only their own exceptions.

- **Runs without asking** once the plan (or Small spec) is approved:
  reversible in-plan work: planned edits; gates, tests, linters, formatters;
  installing plan-named dependencies (lockfile included); local reads; the
  feature branch; re-reviews; in-scope fixes.
- **Stop and ask:** spec approval, plan approval, scope expansion (a file,
  behavior, or dependency the plan does not name), and `[ask-user]` or
  `[DECISION NEEDED]` items.
- **Explicit approval:** commit; push; PR create or merge; destructive acts
  (deleting branches or data, history rewrite, force-push); repo, CI, or
  protection settings; network publishing; activating local delegation;
  handoff temporary commits (`/delegate`). "Commit and push" grants both; a
  commit grant excludes push. Each commit needs its own approval unless the
  human grants commits for the rest of one feature, recorded in its spec's
  `## Standing decisions`; no grant covers force-push or merge.

### Semantic change set

All semantic reviews (`/analyze`, both reviewers, and enabled specialists) use
the same complete change set:

1. Resolve the integration base: configured review/PR target, else the remote
   default branch's symbolic ref, else local `main`, then `master`. A feature
   branch's tracking ref is not its integration base. If no unique base
   resolves, stop and request one.
2. Compute the merge-base of that ref and `HEAD`.
3. Inspect `git diff --find-renames <merge-base> --`, including committed,
   staged, and unstaged tracked changes.
4. Inspect `git status --short` and the contents of every path from
   `git ls-files --others --exclude-standard`; report policy-denied reads.

Pass the spec path, base, merge-base, status, and untracked manifest to each
reviewer. An explicit `<base>..<head>` requests a committed-range review that
may omit working-tree changes; disclose that limitation. Never default to
`git diff main...HEAD` or a merge-base-to-`HEAD` diff because both omit current
work.

Keep personal preferences in ignored local overlays (`CLAUDE.local.md`,
`.claude/settings.local.json`, or user Codex configuration), not shared
`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.agents/`, or `.codex/` files.

## Stack

- Python 3.12 (managed by `uv`)
- DuckDB (`duckdb>=1.5.6,<2`) writes and queries Parquet/JSONL in place; files
  stay the record of truth, parsing stays in plain Python
  (`docs/adr/0001-duckdb-over-files.md`)
- Collector VM: OpenTelemetry Collector (contrib), VictoriaMetrics, Loki, Tempo,
  as Docker Compose under `collector/`; Grafana stays on the existing monitoring VM
- pytest + pytest-asyncio
- ruff (lint + format) + mypy (strict)

## How to run things

`.agentic/toolchain.sh` is the one place the gate's tools are named; the
hooks, `/review-check`, and CI call its subcommands. Use them too, so a
tool swap changes one file.

- Install: `.agentic/toolchain.sh install` (`uv sync`)
- Run table: `uv run agentic-telemetry run-table --out data/run_table.parquet DIR...`
  (DIR = project roots or landing-zone copies; `data/` is ignored, never committed).
  Add `--metrics-url http://127.0.0.1:8428` to join each run to the inference
  host's metrics in the local stack's VictoriaMetrics (spec 0005, read-only).
  Rerun after about 90 s for a run that just ended.
- Session table: `uv run agentic-telemetry session-table --out data/session_table.parquet [DIR...]`
  (no DIR = each client's default session directory; DIR = session directories,
  project roots or landing-zone copies)
- Ship evidence to a landing zone: `collector/mac-agent/ship-ledgers.sh --dest DIR`
  (or over SSH to `TELEMETRY_VM` without `--dest`; `--help` lists options and
  environment). Set `PROJECTS_ROOT` to the directory above your projects.
- Run tests: `.agentic/toolchain.sh test` (`uv run pytest`)
- Single test: `.agentic/toolchain.sh test path/to/test.py::test_name`
- Lint / format / type-check: `.agentic/toolchain.sh lint | format | typecheck`
  (ruff check, ruff format, mypy on `src/`)
- The whole gate: `.agentic/toolchain.sh gate`

## Your role: orchestrator

Hold the active spec and drive the loop. Delegate for independent review and
to keep wide searches or noisy output out of the main context.

| Situation | Route to |
| --- | --- |
| Medium or larger, the approach is unclear, or it needs an exploratory report | `/plan` (`planner`) |
| About to implement anything past trivial | `/test-first` before any implementation code |
| Implementation done and `/review-check` is green | `/review` (and `/review-adversarial` on meaningful features) |
| Need full test output, a wide survey, or doc fetches | A focused subagent |
| Implementation needs files outside the approved plan, or Trivial/Small work outgrows its size row | Stop and ask first; re-size (Medium runs `/plan`) |

Subagents run on the session's model: pass no model override to a subagent
or workflow unless the human names a model for that subagent.

Re-read the spec at phase boundaries and after context drift. Write a phase
handoff before changing sessions. Verify claims outside the gate (for example,
migrations or file comparisons) with a concrete check.

| Task size | The loop |
| --- | --- |
| Trivial: rename, typo, ≤ ~10 lines | Branch optional; skip spec and plan. |
| Small: one function/file | Branch; one-sentence spec; skip `/plan` unless the approach is unclear; use `/test-first`. |
| Medium: 3–10 files | Full loop. |
| Large: refactor/new subsystem | `/adr` first; full loop; split into medium tasks and sessions. |

A task between rows takes the larger one.

## Workflow expectations (Spec → Plan → Test-first → Implement → Verify)

`WORKFLOW.md` owns the walkthrough and `docs/workflow-diagram.md` the diagram.
Stop after the spec (Medium/Large), after any `/plan`, and after Verify for
commit approval. Once the plan is approved and persisted, autodrive
test-first → implement → docs → gate → review → fixes/re-review. Stop if
tests, gates, or review invalidate the spec.

Review findings are `[auto-fix]`, `[no-op]`, or `[ask-user]`. Apply
`[auto-fix]`, sync docs, rerun the complete gate, and obtain a fresh focused
review (full review for substantial fixes) until clear. Surface `[ask-user]`
verbatim and stop; with `/unattended` consent in the spec's `## Standing
decisions`, answer and log it as `/unattended` describes.

- **Spec:** Before non-trivial work, create `docs/specs/NNNN-<feature>.md`
  with goal, criteria, non-goals, and external references. Use `/scope-check`
  or `/clarify`; put product direction in `0000-product.md` and costly
  cross-cutting decisions in ADRs. When a roadmap unit or a spec splits into
  parts, give each part its own four-digit spec (0003, 0004, ...) with a
  `**Roadmap unit:** N` line and `**Depends on:**` for ordering. Do not use
  lettered sub-specs (`0003a`) or lettered sections as work units: the status,
  closeout and spec-resolution tooling match exactly four digits.
- **Plan:** For Medium/Large work, or when the approach is unclear, run
  `/plan` before tests or implementation. After approval, the orchestrator
  copies its file-by-file plan verbatim into `## Approved implementation
  plan`, updates the date, and marks the spec `shipping` before
  `/test-first`, compaction, client switch, or handoff. For Small work that
  skips `/plan`, mark the approved spec `shipping` before `/test-first`.
- **Test-first:** `/test-first` writes failing tests from the spec. Audit
  its file fingerprints, reject implementation edits, and rerun the focused
  test to confirm the expected failure. If installed, run `/analyze` before
  coding.
- **Implement:** Be on a feature branch; write the minimum passing code. For
  external-authority values, follow "External-reference provenance." If
  `/delegate` is installed, it says when this run delegates; otherwise
  implement yourself.
- **Docs:** Before review, correct affected docs. Change README only for pitch,
  installation, or user-facing changes. Add a doc only for standing guidance
  that fits no existing doc or docstring. Mark the spec `shipped` and
  regenerate its dashboard before final review.
- **Verify:** Run `/review-check`, then `/review`; add
  `/review-adversarial` for meaningful features. Add installed `/security` or
  `/performance` when their triggers match. Resolve and re-review fixes; then
  stop for commit authorization.
- **Bugs:** Reproduce first. The test must fail for the diagnosed cause before
  implementation and pass afterward.
- **Multi-day work:** Append `## Phase handoff` at each phase boundary and
  resume in a fresh session.

## Detailed workflow guidance

Load each phase's workflow for procedure. `WORKFLOW.md` owns the walkthrough
and hooks; `docs/project-types.md` the inventory; `docs/codex-cli.md` and
`docs/pi-agent.md` client startup and trust; and installed
`docs/parallel-agents.md` worktrees and unattended runs. Surface conflicts with this contract.

Hooks, permissions, pre-commit, and CI neither grant authorization nor prove
correctness. The rules, complete gate, and fresh semantic review still apply.

## Don't-touch list

- `pyproject.toml` `[tool.uv]` section — ask first
- `collector/*.yaml`, `collector/mac-agent/otel-agent.yaml` and
  `collector/inference-agent/otel-agent.yaml` are validated
  against pinned images (see `collector/README.md` → Verification); re-validate
  after any edit and record it there
- `collector/landing/` contents and anything under `data/` are evidence, never
  edited by hand and never committed

## Third-party code and licenses

This repo will be public and is MIT-licensed (`LICENSE`).
Rules for anything that comes from outside:

- **Run as a service, don't copy:** Grafana, Loki and Tempo are AGPL-3.0. They run
  unmodified from upstream images on the homelab; never vendor, patch, or copy
  their source or bundled dashboards into this repo.
- **Copy only MIT or Apache-2.0 material**, and credit it: keep the upstream
  license header or notice next to the copied file, and record the source, URL,
  retrieval date and license in `THIRD_PARTY_NOTICES.md` (create it on first
  copy). Apache-2.0 code also keeps its NOTICE text.
- **No license file, no copying.** A repo whose GitHub page shows no detected
  license grants nothing, whatever its README or package metadata says. Such a
  project may be installed and used as a tool; its code and config are not
  copied. Re-check before copying; licenses get added.
- **Elastic License 2.0 (Arize Phoenix) and source-available licenses:** fine
  to run privately, never a dependency of anything published here.
- **Langfuse:** MIT core only; nothing under its `ee/` directories.
- Research papers are cited, never pasted; private repos and the vault are
  never named as sources in tracked files.

## Open work / current state (updated 2026-10-22)

- Bootstrapped 2026-10-21 (full profile). `analysis/` is a design draft.
- Product spec `docs/specs/0000-product.md` exists. Spec 0001 (run table from
  delegate ledgers, review rows, `result.json`, `state.json` and Pi transcripts)
  is shipped: `src/agentic_telemetry/run_table/` plus the `run-table` CLI. Against
  the owner's two private projects with delegate runs it writes 15 rows, 11
  reviews, 4 runs with tokens and 1 orphaned result directory.
- Spec 0002 (session table from Pi, Claude Code and Codex transcripts, including
  Codex `.jsonl.zst`) is shipped: `src/agentic_telemetry/session_table/` plus the
  `session-table` CLI, which reads the clients' default session directories when
  given no DIR. On the owner's machine it writes 629 sessions (187 Pi, 256 Claude
  Code, 186 Codex) with no warnings; delegate sessions agree with the run table's
  token totals.
- Depends on the scaffold's Phase 1 tool-call ledger (separate work in the
  agentic-scaffold repo, vault prompt `Agentic Scaffold Telemetry - Agent Prompt`)
  only for the tool-call ledger reader (the rest of roadmap unit 2).
- Spec 0003 (local collector stack) is shipped:
  - `collector/` runs on the owner's Mac under OrbStack. Images are pinned
    by multi-arch digest, and host values come from the untracked `collector/.env`
    (template `collector/env.example`). Ports bind to `TELEMETRY_BIND_ADDR`.
  - The archive is native-zstd OTLP JSON (`archive/<signal>.jsonl*.zst`), behind
    an Alpha feature gate.
  - A telemetrygen smoke test passed in all three stores and the archive.
  - The Collector scrapes only llama-swap's own `/metrics` (`up == 1`), never
    `/upstream/`, because that resets model TTL. The endpoint findings and the
    stored `llamacpp_*` names are in `collector/README.md`. ADR 0002 records the
    choice of the Grafana-stack stores.
- Spec 0007 (inference host agent) is shipped:
  - `collector/inference-agent/` is a Collector that runs on the inference host
    with host networking. It scrapes each model server on its own port, using
    targets from an untracked `targets.json` that also sets `model_id`, and sends
    OTLP to the stack through a disk queue. llama-swap never sees these requests,
    and a relabel rule drops any `/upstream/` path.
  - Live since 2026-10-21. The owner copies the four files to the host, and its
    sysadmin agent installs them. Covered: Strata and llama.cpp (`--metrics`
    added). Uncovered: Project Maya v1.1.4 and Sybil.
  - A loaded llama.cpp model delivered 16 `llamacpp_*` series with `model_id`.
    It unloaded on its 900 s TTL, and nothing reloaded it.
  - While the stack is on the Mac, the Mac binds `0.0.0.0` on the home network
    (0007 D1). 0006 repoints the agent to the VM and returns the Mac to
    `127.0.0.1`.
  - Follow-ups:
    - an easier install (a one-command script, or Ansible together with 0006);
    - llama-swap issue #1135: if llama-swap's own `/metrics` gains per-model
      data, the agent can be retired.
- Spec 0005 (server-metrics join) is shipped:
  - `run-table --metrics-url URL` joins each delegate run to the inference host's
    per-model series in VictoriaMetrics, through `src/agentic_telemetry/server_metrics/`.
  - The model comes from the run's own transcript (served, then requested), with
    no alias table. Inference from moving counters happens only for a run with
    no recorded names, and only on an exact token match.
  - Throughput comes from counter increases. Each run's tokens are reconciled
    against the server's, and it gets a status: joined, joined_approx, overlap,
    uncovered, no_data or error.
  - Its implement phase was the first dogfooded handoff (`local-qwen38-flash-next`).
    That run joined `exact`, and the review verdict `model-defect` demoted the
    model for `phase:implement`. Delegation is active for this checkout
    (`policy = auto`).
- Spec 0004 (landing-zone shipping) is shipped:
  - `collector/mac-agent/ship-ledgers.sh` finds projects up to `--max-depth`
    below `PROJECTS_ROOT`. It copies only what `run-table` reads, keeping
    `.agentic` in the path, plus client transcripts to `_sessions/`. It copies
    over SSH, or with `--dest DIR` into a local landing zone. It never deletes
    and never copies a symlink. Two projects with one name are skipped (exit 1).
  - The Collector tails only ledgers, timestamps each line with its own `ts`,
    and gives each project its own Loki stream. Transcripts are read in batch,
    never tailed. Loki refuses lines older than a week; the archive keeps them.
  - `run-table` keeps the copy with more reviews when one is a stale copy of
    the other, with a warning. Any other difference still exits 1.
  - The local landing zone gives the same run and session rows as the project
    roots.
- Roadmap unit 3 continues in draft spec 0006, the VM deployment. It takes
  over launchd scheduling for the ship script, the easier inference-host install,
  and Loki's history window.
- Next: 0006, server columns for the session table (a follow-up 0005 names), or
  the rest of roadmap unit 2 once the scaffold's Phase 1 lands.

<!-- agentic-scaffold:standing-rules:start -->

## Standing rules

These rules are authoritative for every client. They live here once
rather than in duplicate client-specific policy files. Do not
hand-edit this marked block;
`bootstrap.sh --update` refreshes it while preserving content outside
the markers.

---


# Write code that agents (including you) can verify

This codebase is maintained primarily through agentic sessions. Code
that an agent can read, run, and check its own work against beats code
that is merely clever. When choosing between equivalent designs, prefer
the one a fresh session can verify without tribal knowledge.

- **Descriptive names over clever abstractions.** A function named for
  what it does is documentation that cannot drift. Avoid metaprogramming,
  dynamic dispatch tricks, and indirection layers that make "who calls
  this?" unanswerable by reading.
- **Simple control flow.** Early returns over nested conditionals;
  explicit branches over flag arguments threaded through call stacks.
- **Rich, observable output.** Log meaningful state transitions to
  stdout or the project logger so a session can run the code and *see* what happened
  rather than inferring it. Example: a dev-mode email sender that prints
  the message to stdout lets an agent verify a sign-up flow end-to-end
  with no mailbox access.
- **Make failure loud and specific.** Error messages should name the
  input and the expectation that failed — they are the feedback channel
  the next session debugs through.
- **Harden tools against misuse.** Internal scripts and CLI helpers get
  argument validation and a `--help` that states intent; assume a future
  agent will call them with plausible-but-wrong arguments.
- **Plain interfaces over frameworks** where the choice is free: a
  function that takes data and returns data is verifiable in one test;
  a framework hook is verifiable only inside the framework.

---

# Code / commit style

- No AI `Co-Authored-By`, generated-by-client, or other AI-attribution text in
  files, comments, commits, or PRs; the top-level README is the sole notice.
- Match the log: short imperative subject and a why-focused body when needed.
  Do not introduce conventional-commit prefixes unless the log uses them.
- Reference the spec under `docs/specs/` when applicable.
- Avoid emojis in repo files.
- Avoid the words *genuinely*, *straightforward*, *actually* in prose.
- Direct, technical tone.

## Mistakes feed back into the rules

Encode a human correction to a recurring agent mistake in `AGENTS.md` in the
same change. Review findings count: when `/review` or `/review-adversarial`
raises the same finding on a second feature, add the missing rule while fixing
it. One occurrence is a mistake; two indicate a missing rule. State the rule
as the correct action ("call the v2 client"), not only the prohibition
("don't call the v1 API"); a ban alone leaves the agent guessing what to do
instead.

---

# External-reference provenance (implement phase)

Any value or claim whose correctness depends on matching an external
authority — listed in the spec's `## External references` section — must
be populated by an in-session retrieval from the declared source URL,
with the retrieval date + license pinned in a header comment near where the value is
defined. Reconstructing such values from training is the fabrication
failure the spec template warns against — if the source isn't fetchable,
the spec's provenance is wrong; fix the spec, not the code.

Copyleft-licensed sources (GPL/AGPL/LGPL) are consult-only in a
permissive repo: do not copy their content verbatim and do not check the
project into `vendor/`. See `docs/specs/README.md` `## External
references` for the categories this covers and the license
compatibility rules.

---

# Git workflow

**Every change gets its own branch; never edit feature or fix code on `main`.**
Create it as soon as a spec or issue exists, without waiting to be asked.

## Branch naming

- Local spec mode: `spec-NNNN-<slug>`, where `NNNN` is the spec number.
- XS work without a spec: `<type>/<slug>`, where type is `feat`, `fix`,
  `chore`, `docs`, or `refactor`; never invent a spec number.
- Opt-in issue mode (record it in `AGENTS.md`): work beyond XS gets an issue,
  matching spec number, and `<issue-number>-<slug>` branch. Prefer
  `gh issue develop <N> --name <N>-<slug> --checkout` to preserve GitHub
  linkage.
- In either mode, the shared number links the work item, branch, and PR. It is
  an identity, not execution order; gaps are expected. See
  `docs/specs/README.md` → "Numbering".

One branch per spec / unit of work.

## One writing agent per checkout

A checkout has one session editing it. A second agent that will edit files
starts in its own worktree on its own branch (`git worktree add
../<repo>-<branch> -b <branch>`; see `docs/parallel-agents.md` where installed)
and removes it after the PR merges. If a session starts with uncommitted changes
it did not make, ask before editing; the SessionStart warning flags them.

## Before the Implement phase

Run `git branch --show-current`; on `main` or `master`, stop and branch. The
SessionStart warning and `no-commit-to-branch` hook are only backstops.

## Commits and pushes

Commit and push only as `AGENTS.md` → **Approval matrix** allows: make the
change, show `git status` and `git diff`, then wait.

## Pull requests

Run `/review`, then open with `gh pr create --fill --web`. In issue mode, put
`Closes #<issue-number>` in the PR body; omit it in local mode.

### Close-tasks ride in the PR they belong to

Finish bookkeeping on the feature branch before opening or merging its PR:
mark the spec `shipped`, regenerate `docs/specs/README.md`, update the
`AGENTS.md` current-state block, and tick related items. In an open PR,
`shipped` means "ships when merged."

Run `.agentic/hooks/closeout-check.sh` before opening the PR. CI rejects
numbered feature branches whose spec remains `draft`/`shipping` or dashboard
is stale; it does not verify the current-state prose.

Do not open a cleanup-only PR after merge. Fold missed bookkeeping into the
next related PR unless the cleanup adds independent standing value.

---

# Secrets and public-repo hygiene

Treat the repository as public from commit #1, even while private. These rules
cover files and diffs; commits and tags; branch names; PRs and reviews; issues,
Discussions, wikis, and releases; CI logs; and author/committer identities.
Commit identities must use the public GitHub identity, never a work address.

**Never commit:**

- Credentials: tokens, passwords, private/signing keys, OAuth secrets,
  cookies, or JWTs. Rotate immediately if committed; assume compromise.
- `.env*` except value-free `.env.*.example`; ignore `.env.*` and explicitly
  allow only the examples.
- Internal hostnames, IPs, subnets, or URLs; VPN endpoints; private chat links.
- Non-consenting coworker, manager, or customer names.
- Private tracker IDs or internal document links.
- Employer references, identity-leaking paths, or personal information.
- Unscrubbed tool output. Build, test, and tool logs print absolute paths, so
  they carry the local username and directory layout; commit a summary or
  scrub first (`git grep -nE '/(Users|home)/'` finds them).

Before making a private repo public, audit its entire history and all surfaces:

- Inspect `git log -p` and `git log --format='%an <%ae>'`.
- Inspect branches, tags, PRs, issues, and CI logs.
- Run `gitleaks detect`; confirm only value-free `.env.*.example` files are
  tracked.

Do not change visibility until clean. History rewrites change every SHA and
cannot retract copies already held by forks, caches, or clones.

## Secrets must not enter the context window either

Tool output enters the transcript. Client policy denies `.env`/`.env.*`,
`*.pem`, and `*.key` reads anywhere in the tree, including example env files.
Never bypass it with `cat`, `env`, `printenv`, sourcing, or echoing credential
variables. The human can paste a value-free example file when needed. If
output could expose a credential, ask the human to run the command outside
the session.

---


# Python code conventions

- **Files ≤ 300 lines.** Split aggressively; one concept per file. The
  `python-module-split` skill auto-invokes when a file approaches this.
- **Type hints required** on every function signature. `Any` requires a
  comment justifying it.
- **No bare `except:`**. Catch specific exceptions or `Exception` with a
  re-raise/log.
- **Docstrings:** Google-style. One-liner for trivial helpers; full
  args/returns/raises for public functions.
- **Imports:** absolute imports inside the package; relative only inside
  `__init__.py`.
- **Logging:** follow the project choice in `AGENTS.md` / `pyproject.toml`.
  `structlog` is a good default for services; stdlib `logging` is fine for
  small libraries and CLIs. Avoid `print` for non-CLI diagnostics.

## Test-first

- Runner: pytest, through `.agentic/toolchain.sh test [target]`; a focused
  target is `tests/test_x.py::test_name`.
- Shared fixtures live in `conftest.py` at the repository root and in
  `tests/conftest.py`; the test-first phase may edit those and `tests/`, nothing
  else.
- An expected red is `AttributeError` or `ImportError` on the missing symbol,
  `NotImplementedError` from a stub, or a behavior-level `AssertionError`.
  For a module the approved plan creates, `ModuleNotFoundError` naming that
  planned module is also an expected red, even though pytest reports it at
  collection: it fails for exactly the missing implementation, so do not
  create empty module shells to avoid it. A `SyntaxError`, a fixture error,
  or any other collection error (including an import of a module the plan
  does not create) is not a red; it is a broken test.

<!-- agentic-scaffold:standing-rules:end -->
