# Telemetry VM: the source-agnostic collector

> Status: design draft, 2026-10-21. Configs assembled from vendor docs read today;
> nothing here has been run yet. See *Verification* at the end.

## What it is

One VM on the Proxmox cluster whose only job is to accept telemetry from anything
on the LAN or Tailnet, keep a raw copy forever, and feed long-retention stores that
Grafana can query. The sources change (Claude Code today, Codex, Pi, local model
servers, your own apps); the VM does not.

The rule that makes it source-agnostic: **everything enters as OpenTelemetry (OTLP)
or as a JSONL file dropped in a landing zone. Nothing else.** The Collector is the only
process that knows where data goes.

```
                 OTLP 4317/4318                          ┌──────────────────┐
 Claude Code ───────────────────────┐                    │ VictoriaMetrics  │ metrics, 5y
 Codex CLI  ────────────────────────┤                ┌──>│ :8428            │
 your Python apps (OTel SDK) ───────┤                │   └──────────────────┘
                                    │                │   ┌──────────────────┐
 ledger.jsonl / session JSONL       ▼                ├──>│ Loki :3100       │ logs, 2y
 (rsync from Macs) ──> landing/ ──> OTel Collector ────┤   └──────────────────┘
                       (filelog)   (one process)     │   ┌──────────────────┐
 llama.cpp / llama-swap /metrics ──> (prometheus     ├──>│ Tempo :3200      │ traces, 90d (optional)
                                     receiver)       │   └──────────────────┘
                                                     │   ┌──────────────────┐
                                                     └──>│ archive/*.jsonl  │ raw, forever (zstd)
                                                         └──────────────────┘
                                                                  ▲
                                            existing Grafana VM queries the three stores 
```

## VM

| Item | Value | Why |
|---|---|---|
| Name | `telemetry` | separate failure domain from the existing monitoring VM, which is too small to add three stores |
| OS | Ubuntu 24.04, Docker + compose | same as the rest of the lab |
| Size | 4 vCPU, 8 GB RAM, 250 GB thin disk | single-developer volume is tiny; disk is the only thing that grows |
| Network | LAN + Tailscale, no public exposure | same policy as the inference gateway in `ENDPOINTS.md` |
| Storage | `/srv/telemetry/{archive,landing,victoria,loki,tempo}` | archive can be an NFS mount from the NAS |
| Backup | PBS backs up the VM; archive is also rsynced to the NAS | the archive is the irreplaceable part |

## Services (compose.yaml)

| Service | Image | Port | Retention | Role |
|---|---|---|---|---|
| `otel-collector` | `otel/opentelemetry-collector-contrib` | 4317 gRPC, 4318 HTTP, 13133 health | n/a | the front door; redacts, archives, fans out |
| `victoriametrics` | `victoriametrics/victoria-metrics` | 8428 | `-retentionPeriod=5y` | metrics. Accepts OTLP natively at `/opentelemetry/v1/metrics`; Prometheus-compatible query API for Grafana |
| `loki` | `grafana/loki` | 3100 | 730 days via compactor | logs and events. Accepts OTLP natively at `/otlp` |
| `tempo` | `grafana/tempo` | 3200, 4317 internal | 90 days | traces. Optional; only Claude Code beta tracing and your own apps emit traces today |

Grafana is **not** run here. Add three data sources to the existing Grafana on
the monitoring VM: VictoriaMetrics (Prometheus type), Loki, Tempo.

## Pipelines (otel-collector.yaml)

Three pipelines, one per signal, all with the same processor chain:

1. `memory_limiter` first (Collector docs say so).
2. `resource` adds `deployment.environment=homelab` so dashboards can filter.
3. `redaction` masks API-key, AWS-key, PEM and email shapes in every attribute. This
   is the second redaction layer; the first is at the source (hook or SDK).
4. `transform` truncates any attribute over 16 KB so a pasted file can't bloat a span.
5. `batch`.

Every pipeline exports to **two places**: the long-term store for that signal, and
the `file` exporter writing zstd-compressed JSONL under `archive/`. The archive is
the source-agnostic record. If you ever replace Loki or VictoriaMetrics, replay the
archive into the new store.

## How each source gets in

| Source | Transport | Signals | Client-side setup |
|---|---|---|---|
| Claude Code | OTLP/HTTP to `:4318` | metrics (`claude_code.cost.usage`, `token.usage`, `lines_of_code.count`, `commit.count`, `session.count`, `active_time.total`), log events (`claude_code.tool_result`, `tool_decision`, `api_request`, ...), traces (beta) | `clients/claude-code-user-settings.json` merged into `~/.claude/settings.json`. Must be user-level; a repo's `.claude/settings.json` cannot turn export on |
| Codex CLI | OTLP/HTTP to `:4318` | log events (`codex.tool_decision`, `codex.tool_result`, ...) | `[otel]` table in `~/.codex/config.toml` with an `otlp-http` exporter. Exact sub-table syntax still unverified; see the vault plan |
| Pi (interactive) | none live | ledger + session JSONL | arrives via the landing zone |
| Delegate runner | none live | `ledger.jsonl`, `results/<run>/result.json`, session JSONL | arrives via the landing zone |
| Scaffold tool-call ledger (Phase 1, all clients) | landing zone | one JSON line per tool call | `scripts/ship-ledgers.sh` (to write) rsyncs `.agentic/telemetry/*.jsonl` and `.agentic/delegate/` from each project to `landing/<host>/<project>/` |
| llama-swap / llama.cpp server | Collector scrapes `/metrics` | llama-swap: system and GPU metrics. llama.cpp (`--metrics`): prompt/predicted token counters, tokens/s, `requests_deferred`, `n_tokens_max` | start each upstream with `--metrics`; add targets under the `prometheus` receiver (router mode needs `?model=<id>`). Per-request lines come from llama-swap `/logs/stream/http`, captured into the landing zone |
| Your own Python agents | OTLP from the OTel SDK | GenAI spans + metrics | `OTEL_EXPORTER_OTLP_ENDPOINT=http://telemetry:4318` |
| Anything else | drop JSONL in `landing/<source>/` | logs | the `filelog` receiver tails it; each line becomes one log record with `source` from the path |

The landing zone is the escape hatch that makes this "irregardless of source": a cron job on
any machine can rsync files there, and the Collector turns them into log records
with a checkpoint so nothing is ingested twice.

## Flow of records, including offline

Two paths. Both end in the same archive and the same stores.

| | Live path (OTLP) | Batch path (files) |
|---|---|---|
| Carries | Claude Code metrics and events, Codex events, your apps' spans | tool-call ledgers, delegate results, session transcripts |
| Mac side | `mac-agent/otel-agent.yaml`: a Collector on `localhost:4318` with a disk-backed queue and a local JSONL copy | `mac-agent/ship-ledgers.sh` every 15 min from launchd: rsync over SSH (LAN or Tailscale) into `landing/<host>/` |
| Offline | the queue fills on disk and drains when the VM is reachable; the local copy is written regardless | the script exits quietly; the next run catches up. Files never change after a session ends, so nothing is lost |
| Authoritative? | best effort: the queue has a cap and the clients drop on their own if no agent is listening | yes: the ledger and transcript files are the record of truth |

Rule: **clients point at localhost, never at the VM.** The Mac agent is the only
process that knows the VM's address. Sessions run with a local model and no network at
all (Pi on Ollama on the laptop) produce only files, so the batch path alone covers them.

Timeline for one session:

1. During the session: Claude Code pushes OTLP to the Mac agent; the scaffold hook
   appends to `.agentic/telemetry/<session>.jsonl`. Both are local writes.
2. Within minutes, when the VM is reachable: the agent drains its queue; the ship
   script rsyncs new ledger lines and transcripts into the landing zone.
3. On the VM, continuously: the Collector tails the landing zone and writes every record to
   the archive, VictoriaMetrics and Loki; the filelog checkpoint prevents re-ingest.
4. Nightly: the analysis package rebuilds the run table and matrices from the archive
   and landing zone into Parquet.

## Retention policy, in one place

| Layer | Keeps | Controlled by |
|---|---|---|
| Archive JSONL | forever | you; prune by hand or move to NAS cold storage |
| VictoriaMetrics | 5 years | `-retentionPeriod` flag |
| Loki | 2 years | `limits_config.retention_period` + compactor |
| Tempo | 90 days | `compactor.compaction.block_retention` |
| Landing-zone files | until processed + 30 days | the ship script deletes after the Collector checkpoint passes |

Derived stats (per-session and per-run summary rows, the per-model failure matrix)
are computed by the Python package in this repo from the archive and the landing zone, and
written to a DuckDB/Parquet set that is itself backed up. Those rows are what you
look at in a year; the raw records are what you go back to when a number looks wrong.

## Bring-up order

1. Create the VM, mount `/srv/telemetry`, install Docker.
2. `cp .env.example .env`, set the paths. `docker compose up -d`.
3. `curl -s localhost:13133` for Collector health; `otelcol-contrib validate --config otel-collector.yaml` before the first start.
4. Send a synthetic span with `telemetrygen` or point one Claude Code session at it with the client snippet; confirm a file appears under `archive/` and the metric shows in VictoriaMetrics' `/api/v1/query`.
5. Add the three Grafana data sources on the monitoring VM.
6. Add the VM and ports to `ENDPOINTS.md` in the vault.
7. Write `scripts/ship-ledgers.sh` and schedule it on each Mac.

## Verification

Read today (2026-10-21): Claude Code monitoring-usage docs (variable names, metric and
event names, traces beta); VictoriaMetrics OTLP ingest path; Loki OTLP endpoint and
`allow_structured_metadata`; llama.cpp server `--metrics` names and `/slots`;
llama-swap `/metrics` and `/logs/stream/http`.

Validated today with the pinned images, on the Mac, via Docker:

- `otel-collector.yaml`: `otelcol-contrib validate` passes on 0.163.1.
- `loki-config.yaml`: `loki -verify-config` reports "config is valid" on 3.7.9.
- `tempo.yaml`: parses on 2.10.10; the start failed only on the missing `/tempo`
  volume, which compose mounts.
- Re-validated 2026-10-21 after the `inbox` → `landing` rename: `otel-collector.yaml`
  passes `otelcol-contrib validate` on 0.163.1; `loki-config.yaml` reports "config
  is valid" on 3.7.9.

Not yet done: a live end-to-end run (synthetic span in, row out in each store); Codex
`[otel]` exporter syntax; which of the llama.cpp endpoints Strata and Project Maya
builds expose.
