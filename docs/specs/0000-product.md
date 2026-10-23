# 0000 — agentic-telemetry

**Status:** evergreen
**Last updated:** 2026-10-22

## Problem

I run a coding workflow (agentic-scaffold) across a frontier model and several
local models served from a homelab inference host. When a run goes wrong today I cannot tell whether
the agent, the delegation packet, the model, or the server was at fault, and I
cannot compare served configurations on my own tasks: nothing records the
trajectory, and nothing joins agent-side evidence to server-side evidence. Without
this I re-read raw transcripts by hand, keep comparison notes by hand, and judge a
model from one run.

The user is me, as the sole operator of the scaffold and the homelab.

Not for teams or other developers. Not for monitoring production apps or the
homelab's infrastructure; Prometheus and Grafana already cover that. Not competing
with hosted LLM-observability products (LangSmith, Braintrust) or with public
leaderboards; this only has to answer questions about my own setup.

## Success metrics

- When a delegate run fails, I can name the cause (agent, packet, model, or
  server) from the run table and the joined server metrics without re-reading raw
  transcripts.
- I stop keeping comparison notes by hand because every run lands in the archive
  automatically, including runs from offline sessions.
- A new local model or quant is admitted to or rejected from a bucket from numbers
  across repeated runs, not from one good-looking run.
- When a local-model run goes wrong, I can read exactly what the model received
  and returned on each turn (system prompt, tools, re-sent history, sampling,
  raw response), so a harness or packet change is chosen from evidence.

## Kill criteria

- The ledger and run table exist but I never open them: the review gates and the
  delegate runner's own verdicts turned out to be enough.
- Collection cost (hook latency, shipping, VM upkeep) exceeds what the analysis
  changes in practice.
- The local-model track is abandoned because frontier delegation covers everything,
  which removes the main comparison question.

## Product non-goals

- Langfuse (or any LLM-observability backend) as the system of record. The archive
  and the Grafana-stack stores are the record of truth. Langfuse may be added later
  as an optional viewer fed from the archive by an importer; see Roadmap pointers.
- An LLM-as-judge, until a task family needs one and it has been validated against
  my own labels.
- Live viewing of Pi sessions.
- A benchmark harness of my own; Harbor and the public task sets are reused as-is.
- Anything multi-user or hosted.
- Exact request and response capture for frontier models. Their transcripts are
  enough for this project's questions, and the provider's side (added content,
  full reasoning, the rendered prompt) cannot be seen anyway. Full capture is for
  local models only (spec 0008).
- Changes to how the scaffold's gate, review, or delegation decide pass or fail;
  those live in the scaffold repo.

## Constraints and assumptions

- Solo-maintained, private repository; Python 3.12 with uv; the scaffold's gate and
  loop.
- Runs on the homelab: a new Proxmox VM, LAN and Tailnet only, nothing exposed
  publicly.
- The Mac must keep working offline: files are the record of truth and shipping to
  the VM is lagged, never required during a session.
- The public scaffold cannot carry any hostname or endpoint; everything with a real
  address lives here (untracked) or in the vault's `ENDPOINTS.md`.
- No secrets and no captured tool content in tracked files or history.
- Evidence from earlier runs stays out of any eval sandbox.
- Local models are served from the homelab inference host through llama-swap; server-side metrics
  depend on what those builds (llama.cpp, Strata, Project Maya) expose.
- The ledger record format is set by the scaffold's telemetry plan (vault,
  `Projects/Agentic Scaffold Telemetry/`); this repo reads it and adapts when real
  payloads differ.

## Upstream dependencies

Formats and APIs this repo reads but does not own. When one changes upstream,
the specs listed against it are the ones to re-check.

| Source | What is read | Pinned at | Read by |
| --- | --- | --- | --- |
| agentic-scaffold delegate runner | `.agentic/delegate/ledger.jsonl` (records and phase names), `results/<run>/result.json` and `state.json` | the record shapes in spec 0001 | 0001, 0004 (copies them), 0005, 0009 |
| agentic-scaffold delegate runner | the `results/<run>/session/` layout, which gives a transcript its `run_id` | `session_table/discovery.py` `delegate_run_id`; `run_table` reads `session/*.jsonl` | 0001, 0002, 0004 (copies it), 0008 (`captures show RUN_ID`), 0009 |
| agentic-scaffold Phase 1 | the tool-call ledger | not started upstream | the rest of roadmap unit 2 (blocked) |
| agentic-scaffold (later, optional) | a run-ID request header from the delegate runner | not planned | would make 0008's join exact for delegate runs |
| agentic-scaffold as tooling | `.agentic/` hooks and gate, `/delegate`, the standing-rules block in `AGENTS.md` | refreshed by `bootstrap.sh --update` | every spec's workflow, no data |
| Pi | session transcripts, `subagent` tool results, the `compat.sendSessionAffinityHeaders` session header | Pi 1.3.0 | 0001, 0002, 0004 (copies them), 0005, 0008, 0009 |
| Claude Code, Codex | session transcripts (Codex also `.jsonl.zst`) | the formats in spec 0002 | 0002, 0004 (copies them) |
| llama-swap | its own `/metrics`; `/api/metrics/activity` and `/api/captures/{id}`, the capture JSON and its redacted headers | v264 | 0003, 0007 (avoids `/upstream/`, which resets a model's TTL), 0008 |
| llama.cpp, Strata, Project Maya | per-model metrics endpoints and their series names (Maya's `/metrics` is JSON, so it is uncovered); their chat-completions response streams | the builds recorded in `collector/README.md` | 0003, 0005, 0007, 0008 (stream reassembly) |
| Collector stack images | OpenTelemetry Collector, VictoriaMetrics, Loki, Tempo, and the capture harvester's Python and uv bases | digests in `collector/compose.yaml`, `collector/inference-agent/compose.yaml`, `collector/captures/Dockerfile` and the README's Images table | 0003, 0006, 0007, 0008 |

## Roadmap pointers

1. Run table from delegate evidence: given a list of scaffold-bootstrapped project
   roots (or the VM landing-zone layout), read every `ledger.jsonl` and `result.json` into
   one Parquet table keyed by run ID and served-configuration fingerprint, with
   outcome, first-failure phase, timings and tokens. Proven on whatever projects
   exist at the time. Spec: `docs/specs/0001-*.md` (next).
2. Tool-call ledger reader and session summaries: parse the scaffold's Phase 1
   ledger and the three clients' session transcripts into per-session rows, then
   the transition failure matrix per fingerprint. The session-transcript half is
   spec 0002 (shipped); the ledger reader and matrix depend on the scaffold's Phase 1
   landing.
3. Collector VM bring-up and server-metrics join: deploy the validated stack under
   `collector/`, ship ledgers from the Mac, scrape the inference host, and join each run's time
   window to the server metrics. Specs: 0003 (local stack on the Mac), 0004
   (landing-zone shipping and ledger ingestion), 0005 (server-metrics join), 0006
   (telemetry VM deployment), 0007 (host-local model metrics scraper).
4. Later: the review and labeling app; the Langfuse importer and optional viewer
   (useful for employability as well as browsing; see `analysis/README.md`).
5. Local-model request and response capture: keep llama-swap's per-call captures
   of every local-model request and response as files, and join them to sessions
   and runs, so a session can be read turn by turn as the model saw it. Spec: 0008. The rendered
   chat-template prompt is a later layer.

## Open questions

- Resolved 2026-10-21 (spec 0003): which llama.cpp endpoints (`/metrics`,
  `/slots`) the Strata and Project Maya builds expose. See `collector/README.md`
  → Inference server endpoints. Per-model scraping moved to spec 0007.
- Resolved 2026-10-22 (spec 0004): session transcripts are read in batch from the
  landing zone by `run-table` and `session-table`; the Collector tails only
  ledgers, so captured tool content never reaches Loki.
