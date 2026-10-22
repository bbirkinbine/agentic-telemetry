# 0005 — Server-Metrics Join

**Status:** draft
**Last updated:** 2026-10-21
**Depends on:** 0003, 0007
**Roadmap unit:** 3 (with 0003, 0004, 0006, 0007)

## Goal

Add server-side columns to the run table by joining each delegate run's time
window to the inference host's metrics in VictoriaMetrics. This is the step
`analysis/README.md` names as missing: nothing today joins agent-side records to
server-side records. Without it, a slow or failed local run cannot be told apart
from a slow or overloaded server. No request carries a run ID through
llama-swap, so the join key is model plus time window.

### Facts checked against local evidence on 2026-10-21

- A ledger `run` row's `ts` is the run's **end** time. The run ID ends in the
  start stamp (`...-YYYYMMDDTHHMMSSZ-<pid>`), and `ts − wall_seconds` equals that
  stamp to the second on every handoff run checked (6 of 6).

## Success criteria

- `run-table` takes an optional `--metrics-url URL` (VictoriaMetrics base URL).
  Without it, behaviour and output match 0001 exactly, the new columns are null,
  and `server_status = "not_queried"`.
- With it, each run's window is `[ts − wall_seconds, ts]`, falling back to
  `agent_seconds` when `wall_seconds` is absent. The command queries
  VictoriaMetrics for that window and that run's `model_id`, and fills:
  - `server_decode_tps`: mean of `llamacpp_predicted_tokens_seconds`.
  - `server_prefill_tps`: mean of `llamacpp_prompt_tokens_seconds`.
  - `server_ctx_high_water`: max of `llamacpp_n_tokens_max_total` (stored with
    `_total` because llama.cpp declares it a counter; observed 2026-10-21, 0007).
    Open question: it is the largest `n_tokens` since the server started, so a
    window max can include earlier runs on the same loaded model.
  - `server_prompt_tokens`, `server_predicted_tokens`: increase of the two
    `_total` counters over the window.
  - `server_deferred_max`: max of `llamacpp_requests_deferred`.
  - `server_status`, one of:
    - `joined`
    - `no_data`: no series for that model in the window, including every run
      older than the stack.
    - `overlap`: another run in the table shares the model and an overlapping
      window. Columns stay null; the window is flagged, never apportioned.
    - `error`: the query failed. A warning names the run and the URL path.
- Metric names are the stored names 0003 recorded for the owner's builds, and
  the model label is the `model_id` that 0007's scraper attaches. Where a build
  lacks a metric, that column is null for its runs and the status stays
  `joined`. For example, Strata serves no `n_tokens_max`.
- The summary line counts runs per `server_status`. An unreachable
  `--metrics-url` exits 1 before writing, naming the URL. A malformed URL exits 2.
- Queries use the standard library HTTP client; no new dependency.
  <!-- assumption: stdlib urllib over adding httpx/requests -->
- Tests stub the HTTP layer with response-shaped JSON built in `tests/`. No live
  endpoint, hostname or real metric value is committed.
- With 0007's scraper running, the owner runs one delegate run, then `run-table
  --metrics-url ...`. That run's row has `server_status = "joined"` and non-null
  throughput and context columns (manual, counts only).

## Non-goals

- Swap wait and the sampling params the server applied. These need `/slots`
  polling and llama-swap log capture, which come in a later spec.
- Session-table server columns. Interactive sessions have no single model window
  yet.
- Aggregates across runs (per-fingerprint throughput, the failure matrix).
- Apportioning metrics between overlapping runs.

## Notes

- **Open question: `model_id` vs metric label.** 0001 left open whether the
  ledger's `endpoint` alias or `model_id` is the stable key. The join uses
  `model_id`, which must match the `model_id` label 0007 attaches, exactly or
  through a recorded mapping. As of 2026-10-21 they do not match. Ledgers record
  profile aliases such as `local-qwen38-flash-next`, while llama-swap model IDs
  look like `qwen3.8-flash-next-unsloth-q8_0`, and quants have changed since
  those runs. So the mapping is required, and this spec must say where it lives.
  The label's values come from the inference host agent's untracked
  `targets.json` (0007), one `model_id` per model server port.
- **Open question, throughput gauges are per scrape window.** The criteria above
  average the gauges; reconcile that with this note before `/plan`. llama.cpp resets its
  `prompt_tokens_seconds`, `predicted_tokens_seconds` and
  `n_busy_slots_per_decode` buckets on every `/metrics` call (0007, recorded in
  `collector/README.md`). Each sample averages the 30 s since the agent's
  previous scrape, so a run's throughput should come from the counters
  (`llamacpp_prompt_tokens_total` over `llamacpp_prompt_seconds_total`, and
  `llamacpp_tokens_predicted_total` over
  `llamacpp_tokens_predicted_seconds_total`), not from averaging the gauges.
- **Risk: concurrent runs.** One run at a time on the server keeps windows
  unambiguous (`analysis/README.md`). `overlap` makes a violation visible rather
  than averaged.
- Runs before 0003's stack existed get `no_data`. The join only pays off on new
  runs.

## External references

- **VictoriaMetrics HTTP query API** (`/api/v1/query`, `/api/v1/query_range`,
  MetricsQL functions used, response shape). Authoritative source: the
  VictoriaMetrics docs, Apache-2.0. The URL and retrieval date are pinned at plan
  time by an in-session fetch.
- **Metric names**: empirical, from 0003's recorded findings.
