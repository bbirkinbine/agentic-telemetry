# Delegating implementation to a local model

Advanced doc, installed with `--full` / `--advanced-docs`. Companion to
[`parallel-agents.md`](parallel-agents.md): that doc covers running *more*
agents of the same class, this one covers running a *weaker* agent on a
subset of the loop and being honest about which subset.

Everything here is optional. Nothing in the loop depends on it.

## The rule

**Delegate to a local model only where a machine — not a human — can tell
whether it succeeded.**

That is not a general principle about model quality. It follows from three
facts, in order:

1. Local models fail agentic tasks often enough that failure must be
   planned for. As of 2026-08 the comparison this doc started from put
   open-weight models that fit in 72 GB of VRAM at roughly 25% on the
   Terminal-Bench 2.0 agentic coding benchmark against roughly 85% for
   frontier models; those figures were never pinned to a source and are
   kept only as dated history. By 2026-10 a 27B open-weight model's vendor
   reported about 59% on the same benchmark — self-reported, at full
   precision, with long context, generous token budgets, and multi-hour
   timeouts, not a quantized workstation setup. Published scores are a
   prior about what to try, not evidence about your setup; your own
   recorded runs are.
2. A high failure rate is only affordable when detection is automatic and
   free.
3. This scaffolding already owns automatic detection. `/test-first` writes
   the oracle *before* implementation exists, and `/review-check` runs it.

Point 3 is the whole reason this is worth attempting here. Most public
reports of local-model delegation converge on "bounded chores" precisely
because the people writing them have no mechanical oracle and are
eyeballing the diff.

## Two tiers, two verdicts

"Can a local model code" is two questions here.

| Tier | The job | Model class | Verdict |
| --- | --- | --- | --- |
| **1 — inline autocomplete (FIM)** | Complete the line you are typing | 1.5B–7B **base** model | Works. Unrelated to this loop; wire it up and forget it |
| **2 — one phase in a worktree** | Implement an approved plan against failing tests, with repository access and tools, in an isolated git worktree | instruct with reliable tool calling, typically 27B and up | What this scaffold wires, behind an independent gate and scope audit. Unmeasured on real local runs; your recorded handoffs are the evidence |

Tier 1 notes worth carrying: use a **base** model, not an Instruct
variant — chat tuning suppresses fill-in-the-middle, and model vendors say
so directly. Serve it on its own endpoint; the autocomplete model and the
implementation model should never be the same process.

Tier 2 is a bet that a model with the repository and its tools does better
work than one that sees a fixed excerpt and returns text. That is a stated
expectation, not a measurement; the record per phase (`status`) and the
review verdicts are how you find out on your own tasks.

## Phase-by-phase

| Phase | Local? | Why |
| --- | --- | --- |
| `/spec`, `/product-spec`, `/adr` | No | No machine oracle, and specification defects are the largest single category of multi-agent failure |
| `/plan` | No | A weaker planner measurably degrades a stronger executor; the reverse hazard is real |
| `/test-first` | No, by default | The tests *are* the oracle. `--phase test-first+implement` hands it over too; review must then check the tests against the spec |
| Implement | **Yes, conditionally** | The only phase with mechanical pass/fail |
| `/review-check` | n/a | Deterministic already — the stack's gate through `.agentic/toolchain.sh` |
| `/review`, `/review-adversarial` | No | This is the rung that catches "gate green, feature wrong." The model that wrote the code, or one weaker than the orchestrator, is the wrong judge |

## Conditions on the implement phase

All five, or don't delegate:

1. **An approved plan bounds the scope.** The session may change only the
   files under the plan's `## Files to touch`, plus the spec's own handoff
   note. The runner checks this after tool calls and again before and after
   the gate; anything outside the list, a test edit in the implement phase,
   or a commit stops the run.
2. **Tests for the phase exist and fail before the session.** No oracle, no
   delegation. In the implement phase the tests are read-only to the
   session.
3. **The gate is run by the runner, not trusted from the session.** The
   session runs tests as it works; the runner then runs the full gate
   itself in the worktree. Red ends the run as `needs_frontier`.
4. **One session, then escalate.** A red or empty result goes back to the
   orchestrating session, which implements or hands off once more with a
   short hint in the spec. A paused session resumes; it does not restart.
5. **Pin the quadruple** — model, quant, runtime version, harness version —
   plus the request settings (reasoning mode or thinking level, sampling).
   The same weights with reasoning on and off are different executors.
   Local tool-calling breaks on serialization mismatches between model,
   runtime and harness far more often than on reasoning, and every one of
   those layers ships breaking changes. The runner fingerprints the setup
   with the Pi version, and a change starts a fresh record.

A local executor does what the tests and the spec demand and little more:
where the spec leaves a choice open, it tends to pick the smallest thing
the tests accept. In a trial on a Pac-Man-inspired game, a frontier model
given the same spec built a 15×11 maze in arcade colors; the local model
built a 7×7 loop with white corridors, and both passed. So pin intent where
the executor can't miss it: in tests where it is measurable (sizes, counts,
constants), and as concrete decisions in the spec where it is not. Files
that are mostly presentation (rendering, styles, copy) are where a weaker
model's judgment shows most; keep them with the frontier model unless tests
pin their look.

## Running it: `.agentic/delegate-run.sh`

`--full` installs a runner that does the loop above. It needs `curl`, `jq`,
`perl` (the watchdog that bounds each verifier command), and Pi for the
session itself:

1. **Profile.** Describe your inference servers and models in
   `~/.config/agentic/local-models.toml`, outside the repository.
   `bash .agentic/delegate-run.sh init <base-url> ...` prints a draft from
   what each server lists; review it and save it yourself
   (`--help` shows the format). Set `max_tokens` in a model's `request`
   high enough for its longest response, including any reasoning, and
   `timeout_seconds` long enough for one qualify call to swap and load the
   model, think, and reply. A server key is named by `credential_env`,
   never written into the file. `discover` lists what each server serves.
2. **Qualify.** `qualify <model>` is a plumbing check: a first response,
   then one known-answer edit of a generated file of about 270 lines,
   graded exactly. It needs no project fixture. A handoff uses only a
   qualified model, and any change to the model's setup (weights,
   quantization, runtime, request settings, server) needs a new pass.
3. **Activate.** `activate` sets `policy = "auto"` and
   `activation_approved = true` in this checkout's project entry (it
   refuses without a qualified candidate); `deactivate` sets it back. An
   agent runs either only on the human's explicit yes. Then `policy`
   prints `auto`. The human then picks a delegation mode per run
   (`direct` or `handoff`; `/delegate` → "Delegation modes"), and the
   Implement step follows it.
4. **Hand off.** After the human explicitly authorizes its temporary
   handoff-branch commit, `handoff <spec> --allow-commit --phase implement`
   gives the whole implement phase (or `test-first+implement`) to a local
   agent session in a git worktree, verifies the gate and the plan's file
   scope, and commits the result on `handoff/<run>` for review and a squash
   merge. Choosing `handoff` mode alone is not commit authorization; the
   runner refuses without the acknowledgement flag. Without `--model`, it
   selects from the project's `[[project]]` candidates.
   `handoff-clean` removes it afterwards. Supervision, pause and resume,
   and where the logs are: [Supervising a handoff](#supervising-a-handoff).
   The runner generates an isolated Pi provider from the selected machine
   profile; no matching global Pi provider is needed. It passes only the
   credential variable the profile names and stores that variable's name in
   the generated configuration. Scope and test-file checks compare against
   the starting commit before and after the gate; session commits and branch
   changes fail the run.
5. **Review and record.** After `/review`, `record-review <run>
   pass|model-defect|spec-defect` scores the model for that phase
   (`spec-defect`: the spec or plan was at fault). Its record decides
   selection and demotion; `status` shows each model's phase records for
   the installed harness, and `reset <model> phase:implement` (or
   `phase:test-first+implement`) clears a phase demotion.

Results and the ledger stay in the gitignored `.agentic/delegate/`. The
local session works in its own worktree with the repository and tools; it
never sees this conversation.

### Which clients can do what

| Role | Claude Code | Codex CLI | Pi |
|---|---|---|---|
| Orchestrate with a frontier model and delegate locally (`handoff`) | yes | yes | yes |
| `handoff` session (the local agent itself) | runs Pi | runs Pi | yes |
| A local model orchestrating the whole loop | not wired by this scaffold | not wired by this scaffold | yes, with a model that handles long tool use |

So local delegation works under any of the three clients, as long as Pi is
installed for the handoff session.

### Supervising a handoff

The two modes, for context: `direct` keeps implementation with the
orchestrating session, and `handoff` gives a whole phase to a Pi session. When each applies and what
the agent does around it is `/delegate` policy ("Delegation modes", "Phase
handoff"); this section covers what a human watching a handoff needs.

**Launch and follow.** `handoff` launches detached by default, prints the
run id, and returns. Pi runs in RPC mode; nothing streams into the
conversation. Follow it with `handoff-status`:

```bash
bash .agentic/delegate-run.sh handoff-status <run> --wait 540 --interval 60 [--since <cursor>]
```

It reads local files only (no model tokens), polls until the run reaches a
final or paused state or the wait ends, and prints a snapshot: state and
reason, spec and phase, model, worktree and branch, segment count and active
time, the ceiling, the live segment's last event, active tool, retries,
compactions, and seconds until the lease expires, the number of changed
paths in the worktree, and the last five progress lines from `runner.log`.
With `--interval`, it also prints one line (at most 200 characters) per
interval while it waits: whether the runner and Pi are alive, seconds since
the last activity and the last event, the running tool and how long it has
run, lease seconds left, and tool calls (by tool name), retries,
compactions, and changed paths since the previous line. These say the
processes are alive and emitting; they are not a claim of progress, and
they never include messages, commands, or tool output. The snapshot ends
with `cursor: <segment>:<offset>`; pass it back as `--since` and the next
snapshot counts only the events after it (across a resume too) in place of
the raw tool lines. A cursor that does not fit the run is reported and
ignored; it never changes the exit status. Whether a client shows the
interim lines before the command returns depends on the client.
Its exit status is the handoff's:

| Exit | Meaning |
|---|---|
| 0 | Verified and committed on `handoff/<run>`; review, then squash-merge |
| 10 | The session ended with the gate red or changed nothing, or no candidate was selectable |
| 11 | Paused or orphaned; resumable (no gate, no commit) |
| 4 | Out-of-plan or test-file changes |
| 3 | Harness, RPC, install, or worktree failure |
| 2 | Refused, or no such run |
| 12 | Still running when the wait ended; run it again |

`--foreground` on `handoff` or `handoff-resume` runs attached and exits
with the same codes (12 aside).

**Progress.** A detached run writes `summary` progress to `runner.log`:
a heartbeat every 15 seconds (`DELEGATE_PROGRESS_INTERVAL` changes it) with
elapsed time, seconds since the last activity, and the running tool, plus
bounded lines for tool start/end, retries, and compactions. Lines are
truncated and obvious credentials redacted; reasoning, generated code, and
full tool output are not printed there.

**The lease.** There is no fixed time limit. The session holds a renewable
inactivity lease; all values are whole seconds, set in the environment that
launches the run:

| Variable | Default | Meaning |
|---|---|---|
| `DELEGATE_HANDOFF_LEASE_SECONDS` | 3600 | Inactivity lease, renewed by model or tool activity; at expiry, worktree content not seen before renews it |
| `DELEGATE_HANDOFF_RECOVERY_SECONDS` | 900 | One extension while Pi is in retry or compaction recovery |
| `DELEGATE_HANDOFF_GRACE_SECONDS` | 600 | How long a checkpoint may take before the runner aborts the session |
| `DELEGATE_HANDOFF_ABORT_SECONDS` | 60 | How long abort and shutdown may take before signals |
| `DELEGATE_HANDOFF_MAX_TOTAL_SECONDS` | unset | Optional ceiling on active time across segments |
| `DELEGATE_HANDOFF_SCOPE_CHECK_SECONDS` | 5 | Least time between in-flight scope checks (see Scope stops) |

When the lease runs out with nothing new, or the ceiling is reached, or an
operator stops the run, the runner asks the session to checkpoint: finish
the operation in progress, append a `### Paused handoff` note under
`## Phase handoff` in the spec, and stop. It then audits scope and exits 11
without running the gate or committing. A session that does not settle
within the grace is aborted and recorded as a forced pause.

**Scope stops.** The runner does not wait for the session to finish to
check scope. After tool calls it applies the same rules as the final
audit; a test edit in the implement phase, a path outside the plan, or a
commit aborts the session at once and ends the run as an integrity stop
(exit 4), with the worktree kept for inspection. That run cannot be
resumed. After each context compaction the runner restates the scope
rules to the session, since Pi's compaction summary may drop them.

**Controls.**

- `handoff-stop <run>` asks a live run to checkpoint and pause.
- `handoff-resume <run> --allow-commit` continues a paused run in the same
  worktree and Pi session after checking the spec, plan, snapshot, branch,
  model fingerprint, and scope. `--max-total <seconds>` raises the ceiling.
- An **orphaned** run is one whose state is not final but whose runner and
  Pi are both gone (a reboot, a killed terminal). `handoff-status` reports
  it with exit 11 and names any tool process groups still alive;
  `handoff-resume` treats it as a forced pause and kills those leftovers
  first.
- `handoff-clean` lists handoff worktrees; `handoff-clean <run>` (or
  `--all`) removes the worktree and its `handoff/` branch, skipping a live
  run.

**Where the logs are.** Per run, under the ignored
`.agentic/delegate/results/<run>/`:

| Path | Contents |
|---|---|
| `runner.log` | The detached runner's output and progress lines |
| `state.json` | The logical run: state, worktree, branch, segments, live status |
| `prompt.md` | The handoff prompt sent to Pi |
| `install.log`, `gate.log` | Worktree install and final gate output |
| `segments/NNNN/events.jsonl` | Raw Pi RPC events for segment `NNNN` (0001, 0002, ...) |
| `segments/NNNN/harness.log` | Pi's stderr for that segment |
| `segments/NNNN/status.json` | How the segment ended |
| `session/` | Pi's session JSONL; `state.json` names the file as `session_file` |

The worktree itself is at `${DELEGATE_HANDOFF_DIR:-$TMPDIR/agentic-handoff}/<run>`
(`/tmp` when `TMPDIR` is unset); `handoff-status` prints it.

```bash
r=.agentic/delegate/results/<run>
tail -f "$r/runner.log"                                   # bounded progress
tail -f "$r/segments/0001/harness.log"                    # Pi stderr
tail -f "$r/segments/0001/events.jsonl" | jq -c '{type}'  # event types only
```

`runner.log` is bounded but still names files and commands; the rest is
raw. `events.jsonl`, `harness.log`, the session JSONL, and `prompt.md` can
hold prompts, source code, model
reasoning, tool arguments and results, and local paths. Keep
`.agentic/delegate/` ignored, do not paste these files into issues or
commits, and delete runs you no longer need. They are the client side's
record of the session, not an HTTP capture of the traffic to the model and
not the inference server's logs; what the server saw is visible only
through the server's own logging, if it has any.

## What breaks, and it is not what you expect

The dominant local-executor failure is **serialization, not reasoning**:
the model emits a tool call in a shape the harness does not parse, and the
harness either silently no-ops or loops forever re-emitting it. Documented
instances span Cline, Roo Code, OpenCode, Qwen's own first-party CLI,
llama.cpp's tool parser, Ollama's Anthropic-compatibility layer, and Claude
Code's schema validation. These are fixed and re-broken continuously.

Practical consequences:

- Budget context for the harness itself. An agentic harness system prompt
  plus tool schemas can consume 6–10K tokens before your task starts;
  32K context is a floor and 64K is comfortable.
- Test the plumbing on an *already-solved* spec first. You want the first
  failure to be a parser bug you can see, not a subtle wrong implementation.
- When a model works, write down the exact versions. When you upgrade, re-run
  the plumbing test before trusting a real task to it.

## Honest expectations

Implementing an approved, tested plan is among the cheaper things a
frontier model does, so moving it local saves less than it seems. Expect the win to show up as
latency and privacy rather than capability or cost, and expect a meaningful
fraction of delegated tasks to escalate back.

The good reason to build this is to learn where the envelope actually is.
That is a real reason. It is not an efficiency argument, and presenting it
as one sets up a disappointment.

## What would change this doc

- A controlled comparison — same tasks, frontier-only versus frontier-plan
  plus local-execute, with quality and token numbers. No such measurement is
  published as of 2026-08. Running one on your own repo would be novel.
- Your own recorded handoffs. Published agentic scores are vendor-reported
  numbers on setups unlike a local one; review verdicts per phase on your
  own tasks are the evidence that would confirm or retire Tier 2. If the
  sessions' work on small, well-tested leaf modules keeps failing review,
  that is the signal to reconsider narrower delegation.
- Harness-side convergence on one tool-call serialization, which would
  retire most of the failure list above.
