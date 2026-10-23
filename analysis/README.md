# Analysis plan: finding what to fix, agent side or server side

> Status: design draft, 2026-10-21. Answers the question "do I review telemetry, run
> public benchmarks, or write my own evals?" The answer is all three, each with one job.
> Since built: the run table and server-metrics join (specs 0001, 0005, 0009), the
> session table (0002) and local-model captures (0008). Not built: the transition
> matrix, task candidates and the review app.

## Three instruments, three jobs

| Instrument | Job | Decides | Cost | Run when |
|---|---|---|---|---|
| **Telemetry review** (ledger + server metrics) | Diagnose. Find *where* and *why* runs break, and whether the cause is the agent, the packet, the model, or the server | nothing by itself; produces hypotheses and task ideas | low, continuous | after every batch of real work |
| **Public benchmarks** (Terminal-Bench sample via Harbor) | Sanity-check a served configuration. Catch a quant, runtime or sampling change that broke tool calling before it costs you a day | whether a new server-side setup is admissible | medium, hours | on every runtime, quant or sampling change |
| **Private evals** (replay fixtures + tasks mined from your PR history) | Decide. Rank configurations on your own code, under the scaffold's rules | which model/config gets delegation for which bucket | high per run; repetitions needed | per new candidate; on scaffold changes to delegation |

Private notes already cover the second and third: a Harbor runbook, a replay plan
(Fixture A boundary test, Fixture B repair test), and the evals-FAQ takeaways
(first-failure labels, transition matrix, pass^k). The gap was the first: agent-side
records joined to server-side ones. Spec 0005 closed it for delegate runs
(`run-table --metrics-url`); interactive sessions are not joined yet.

**Order of operations:** review telemetry first and let it choose the tasks. The evals
FAQ's rule is that an eval is written for a failure you have seen fire, not one you
imagine. Public benchmarks are a filter on configurations, never the ranking.

## The join: agent run ↔ served configuration ↔ server metrics

No request carries a run ID through llama-swap, so the join is by **configuration +
time window**:

- The delegate runner already records per run: `run_id`, `phase`, bucket, model entry,
  the fingerprint (model_id, quant, runtime, request settings, harness version), start
  and end times, attempts, outcome, and `record-review` verdict.
- The stack's Collector scrapes llama-swap's own `/metrics`. An agent on the
  inference host scrapes each model server on its own port every 30 s, labelled
  with the llama-swap `model_id` (spec 0007).
- `run-table --metrics-url` joins each run's window to that model's series (spec
  0005):
  - the model comes from the run's own transcript, with no hand-kept alias table;
  - throughput comes from counter increases, and the run's tokens are reconciled
    against the server's (`exact`, `server_more`, `server_less`);
  - each run gets a status: `joined`, `joined_approx`, `overlap`, `uncovered`,
    `no_data` or `error`. Overlapping or shared traffic is flagged, never apportioned.

Server metrics that carry signal (llama.cpp `--metrics`, names verified 2026-10-21):

| Metric | Pattern it reveals |
|---|---|
| `llamacpp:n_tokens_max` | context high-watermark; a run that approaches `n_ctx` is a truncation or compaction suspect |
| `llamacpp:prompt_tokens_total` vs `tokens_predicted_total` per run | prompt re-processing per turn; a high prompt:output ratio means the prompt cache is missing (unstable system prompt, tool schema order, timestamps) |
| `llamacpp:prompt_tokens_seconds`, `predicted_tokens_seconds` | prefill and decode throughput; decode collapse at long context is a quant/offload problem, not a model problem |
| `llamacpp:requests_deferred`, `requests_processing` | queueing; a deferred request looks like a slow model from the agent side |
| `llamacpp:n_busy_slots_per_decode` | batching; relevant once more than one worker runs |
| `/slots` (not Prometheus; not polled yet) | per-slot `n_ctx`, sampling params in effect; the ground truth for "what did the server really use" |
| llama-swap `/api/metrics/activity` + `/api/captures/{id}` (spec 0008) | every call's full request and response: the system prompt, tool schemas, re-sent history and sampling settings the client sent, and the raw stream; `captures show` joins calls to sessions |
| llama-swap model swap events (upstream log) | first-request load time; shows up on the agent side as a timeout or a 600 s "thinking" |

Strata and Project Maya are llama.cpp-derived. Which of these endpoints each build
exposes is recorded in `collector/README.md` → Inference server endpoints: Strata
serves Prometheus text and is scraped (spec 0007); Project Maya v1.1.4 serves JSON
only and is uncovered. Stored names replace `:` with `_` (`llamacpp_prompt_tokens_total`).

## Pattern catalog: what to look for, and which side to fix

| Symptom in the ledger | Server-side check | If server confirms | If server clears |
|---|---|---|---|
| Malformed or fenced tool output; lost tool results | `/slots` sampling params vs profile `request`; chat template / `--jinja` in the upstream log | fix the serving config; requalify | model limitation; narrow bucket or swap model |
| Run times out at `timeout_seconds` | swap/load time in llama-swap log; `requests_deferred` > 0 | raise timeout or pin the model loaded (`ttl`) | model loops; cap turns, lower reasoning effort |
| Quality drops late in a run | `n_tokens_max` near `n_ctx`; `prompt_tokens_total` per turn climbing | raise context or improve cache reuse | packet too large; split by file |
| Slow turns, correct output | decode tokens/s per run; GPU split across cards | quant or offload layout; this is a hardware/runtime choice | packet asks for too much output; use `expected_lines` |
| Edited immutable tests / out-of-scope writes | none (server cannot cause this) | n/a | agent/contract problem; this is Fixture A's job |
| Gate red, model "fixed" the test instead of the code | none | n/a | agent/prompt problem; counts in the transition matrix |
| Frontier redo after local pass (`record-review` fail) | none | n/a | model defect vs packet defect; the ledger's cause field decides |
| Same task passes 1/3 | tokens/s and context stable across the three | model stochastic at this bucket; use pass^k, not best-of | sampling settings differ by thinking level; pin them |

Two questions settle most cases: *did the server deliver what the profile asked for*
(`/slots` and the upstream log), and *did the agent receive what the server sent*
(ledger `output_bytes`, parse errors, retries). When both are yes, the model is the
variable. When either is no, fix the plumbing before touching the model.

## Outputs this repo will produce

1. **Run table** (built: specs 0001, 0005, 0009). One row per delegate run:
   fingerprint, bucket, outcome, first-failure phase and cause, agent and wall
   seconds, review verdict, transcript tokens (subagents included) and, with
   `--metrics-url`, the server's tokens, prefill and decode tokens/s, context
   high-water mark, deferred requests and a cold-start flag. Source:
   `ledger.jsonl`, `result.json`, `state.json`, Pi transcripts and
   VictoriaMetrics window queries. Stored as Parquet. Not built: eval-run rows,
   a task column, grading seconds.
2. **Transition failure matrix** per fingerprint: rows = last phase that succeeded,
   columns = first phase that failed (spec, plan, test-first, implement, gate, review).
3. **Server health during runs**: throughput and context per run, so a model
   comparison can be rejected as "measured a different server state" instead of
   trusted.
4. **Task candidates**: recurring failure clusters from (2), each one a seed for a
   private task (parent-commit rollback, terse prompt + long-context variant).

Also built: the session table (spec 0002), one row per Pi, Claude Code or Codex
session, and local-model captures (spec 0008), each llama-swap call's request
and response joined to its session.

## Not now

- An LLM judge. Nothing above needs one; the gate, the review verdict and the scope
  audit are deterministic. Add a judge only for a task family where "done" is
  ambiguous, and validate it against your own labels first.
- Langfuse. Browsing traces is not the bottleneck; labeled runs and joined metrics are.
