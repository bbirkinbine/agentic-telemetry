# Agent handoff — agentic-telemetry

> **Purpose.** Operational state for the agent (or future-you) opening
> this repo cold. `AGENTS.md` covers the *rules*; this file covers the
> *situation* — what's risky right now, what's safe to run unsupervised,
> and how to roll back if a change goes wrong.
>
> Update this file when those facts change. A stale handoff is worse
> than no handoff. Date the "Current state" section every edit.

---

## Current state

The canonical "what's in progress / what's blocked" lives in
`AGENTS.md` → **Open work / current state**. Don't duplicate it here —
that's two places to drift out of sync. This section holds only the
*operational* detail that doesn't belong in the project contract:

- Branches in flight and their open PRs.
- Anything deployed, recently rolled back, or mid-migration.
- Transient blockers (a flaky external dependency, a pinned-back
  version waiting on an upstream fix).

If there's nothing operational in flight, say so in one line and move
on — an empty section beats a stale one.

---

## Known risks

Things that have bitten in the past, or are fragile right now and need
extra care. Each entry: *what to watch*, *why it bites*, *how to avoid*.

- Secrets in captured content — ledgers and transcripts carry whatever a tool
  printed — never enable content capture on day-job repos; redact at the hook
  and again at the Collector; never commit `collector/inbox/` or `data/`
- Eval contamination — earlier runs' ledgers and transcripts are an answer key
  for a later agent — keep the inbox and archive out of any eval sandbox
- Config drift — the collector YAMLs were validated against pinned images — after
  editing, re-run the validation commands in `collector/README.md`

Delete this section if the repo is greenfield with no landmines yet.
Revisit after the first incident.

---

## Accepted commands (safe to run unsupervised)

This list names commands, not authority. When to ask follows `AGENTS.md` →
**Approval matrix**: reversible in-plan work runs without asking after plan
approval; scope expansion stops for the human; irreversible or publishing
actions need explicit approval.

**Read-only:** `git status`, `git diff`, `git log`, `git branch --show-current`,
`rg`, `ls`, `cat`, `find` (non-destructive flags), `.agentic/toolchain.sh
test | lint | typecheck | gate`, `docker run ... validate`, `duckdb` queries over files under `data/`.

**Mutating but local-only:** `.agentic/toolchain.sh install | format`,
adding a dependency the approved plan names with the stack's package
manager, lockfile change included (subject to the `dependency-hygiene` skill
where installed; an unplanned dependency is scope expansion),
`git switch -c <branch>`, `git switch <existing>`, `git stash`,
`git restore <path>`, writing derived files under `data/` (gitignored).

**Never on this list** (always require explicit approval):
`git commit`, `git push`, and the other irreversible or publishing actions in
`AGENTS.md` → **Approval matrix**. Anything matched by
`.agentic/hooks/block-destructive.sh`. And the project-specific
high-blast-radius commands: `docker compose up|down` against the telemetry VM,
any `rsync` or `ssh` to the VM or to the inference host, and any delete under
`collector/inbox/` or the VM's archive.

---

## Rollback playbook

Concrete commands, not principles.

**Local working copy in a bad state:**

```bash
git stash                  # save changes, return to HEAD
git restore <file>         # discard changes in a single file
git restore .              # discard ALL uncommitted changes
```

**Bad commit on a feature branch (not pushed):**

```bash
git reset --soft HEAD~1    # undo last commit, keep changes staged
```

**Bad commit already pushed (any branch):**

```bash
git revert <sha>           # safe — creates a new commit that undoes it
git push                   # (requires explicit approval — AGENTS.md → Approval matrix)
```

`git reset --hard` is recoverable via the reflog but still blunt — prefer
`git reset --soft` above. Force-push needs explicit approval
(`AGENTS.md` → **Approval matrix**); never reach for it to "clean up" a branch.

**Bad merge to main:** open a revert PR with `gh pr create`, or use GitHub's
"Revert" button on the merged PR. If a collector config change was deployed
to the VM, redeploy the previous tagged config and re-run its validation.

**Bad dependency change:**

```bash
git restore <manifest> <lockfile>   # pyproject.toml + uv.lock, or package.json + package-lock.json
.agentic/toolchain.sh install
```

**Out-of-repo side effects:** the Mac agent writes under
`~/.local/state/otel-agent/` (queue and local archive); the ship script writes
to the VM's `inbox/`; the VM's Collector writes `archive/` and the three stores
under `/srv/telemetry/`. Clean the Mac state by stopping the agent and deleting
the directory; never delete the VM archive without a verified backup.

---

## When X breaks (first thing to try)

- **CI red on a PR** — read the failing job; reproduce locally with the
  `review-check` workflow (`/review-check` in Claude, `$review-check` in
  Codex). Flaky twice → file an issue, don't keep re-running.
- **`.agentic/toolchain.sh install` fails** — delete the environment
  (`.venv/` or `node_modules/`) and retry. Still failing → check whether
  the manifest was edited without regenerating the lockfile.
- **`pre-commit` blocks every commit** — `git branch --show-current`;
  `no-commit-to-branch` blocks `main`. Branch first.
- **PostToolUse hook fights the editor** — the editor's formatter settings
  differ from the project's tool config. Align them — the project config wins.
- **Session won't end its turn** — the Stop hook (`gate-on-stop.sh`) is
  holding it: the source tree has pending changes and
  `.agentic/toolchain.sh gate` is red. Run `/review-check`, fix the failures. A gate that genuinely
  can't pass is surfaced on the second attempt (with a warning on stderr).
- **PreToolUse deny-list blocks a command you actually need** — confirm
  it's truly unrecoverable; if not, the deny-list pattern is wrong (fix
  it). If it is, run outside the agent session.
- **Collector config rejected at start** — run `otelcol-contrib validate` via the
  pinned image; the error names the component and line

---

## Out-of-repo dependencies

External systems this repo touches that aren't obvious from the code.
Changes here may need coordinated changes out there.

- Telemetry VM (Proxmox): the collector stack; address in the vault's
  `ENDPOINTS.md`, not here; only the human deploys or restarts it
- The local inference host: llama-swap gateway and llama.cpp upstreams whose
  `/metrics` the Collector scrapes; LAN/Tailnet only, no credentials
- agentic-scaffold (public repo): produces the ledger format this repo reads;
  format changes there need a reader change here
- Obsidian vault `Projects/Agentic Scaffold Telemetry/`: the plan of record

Delete this section for a self-contained library / utility repo.
