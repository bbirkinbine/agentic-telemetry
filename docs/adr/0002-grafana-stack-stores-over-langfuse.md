# 0002 — Grafana-Stack Stores on a Separate Telemetry Host Instead of Langfuse

**Status:** accepted
**Last updated:** 2026-10-21

## Context

The vault's telemetry plan (written 2026-10-20) sends the coding clients' OTel
output through one OTel Collector, with traces going to a self-hosted Langfuse
and every signal to a JSONL archive. Roadmap unit 3 (specs 0003–0006) is where
that backend is deployed, and every later unit inherits the choice:

- the server-metrics join (0005);
- the transition failure matrix (unit 2);
- the review app (unit 4).

Changing the backend after data accumulates means migrating stores and
rewriting queries.

What the telemetry carries decides the fit:

- **The clients' tool detail is logs, not traces.** Claude Code reports tool
  activity as `claude_code.tool_result` and `claude_code.tool_decision` log
  events, and its traces are beta. Codex reports tool activity as
  `codex.tool_decision` and `codex.tool_result` log events, plus counters and
  histograms, and its docs describe no trace export. Langfuse's OpenTelemetry
  integration documents traces only, received at `/api/public/otel` over HTTP
  (JSON or protobuf; no gRPC). Pointed straight at Langfuse, Codex shows nothing
  and Claude Code shows only its beta spans. Sources, re-read 2026-10-21:
  - Claude Code, "Monitoring usage": https://code.claude.com/docs/en/monitoring-usage
  - Codex, "Advanced Configuration" (`[otel]`):
    https://learn.chatgpt.com/docs/config-file/config-advanced
  - Langfuse, "OpenTelemetry integration":
    https://langfuse.com/integrations/native/opentelemetry
- **The server-side half is Prometheus metrics.** llama-swap's and llama.cpp's
  `/metrics` (throughput, context high-watermark, deferred requests) are what
  0005 joins to run windows, and they need a time-series store.
- **Files are the record of truth.** Ledgers and transcripts reach the stores
  through the landing zone, and the analysis package reads files
  (`docs/adr/0001-duckdb-over-files.md`). Any store is therefore a query and view
  layer, never the only copy.
- **Grafana already runs** on the homelab's monitoring VM (with Prometheus,
  2 vCPU / 4 GB). That VM is too small to also host three stores.
- **Licenses.** This repo is MIT and will be public. Grafana, Loki and Tempo are
  AGPL-3.0, Langfuse's core is MIT (its `ee/` directories are not), and Arize
  Phoenix is Elastic License 2.0.

Against the choice reached below:

- Langfuse is one application with an LLM-specific UI for browsing generations
  and spans. The Grafana stack is three stores plus a Collector to run, upgrade
  and back up, on a VM that did not exist before. That upkeep counts against the
  product spec's kill criterion on collection cost.
- Grafana's trace view is generic and does not render LLM spans the way
  Langfuse does.
- The product spec notes Langfuse is also worth knowing for employability.

## Decision

We will store telemetry in VictoriaMetrics (metrics), Loki (logs and events) and
Tempo (traces, optional), run by Docker Compose on a dedicated `telemetry` host
behind one OTel Collector:

- Every signal is written twice: to its store, and as zstd-compressed JSONL to an
  archive that is kept forever.
- The existing Grafana queries the three stores as data sources. No Grafana runs
  on the telemetry host.
- The stores are rebuildable from the archive; the archive and the landing zone
  are the record.
- During development the same Compose stack runs on the owner's Mac (0003). The
  homelab VM (0006) is its permanent home.
- Grafana, Loki and Tempo run only as unmodified upstream images. Their source,
  config templates and dashboards are never copied into this repo.

Langfuse is not part of this stack. It remains a possible read-only viewer, fed
from the archive by an importer (roadmap unit 4).

## Consequences

Easier:

- Every signal the clients and servers emit lands somewhere queryable: logs,
  metrics and traces alike. Nothing depends on a backend accepting only one
  signal type.
- 0005's join is a VictoriaMetrics window query on the same store that scrapes
  the inference host.
- Swapping a store later means replaying the archive into the replacement, not
  migrating a database.
- One Grafana covers homelab infrastructure and agent telemetry.

Harder:

- Four services to operate (the Collector and three stores), with their own
  retention settings, upgrades and pinned-image re-validation (`AGENTS.md`
  don't-touch list).
- A new VM to provision and back up (0006).
- No LLM-aware trace browser. Reading a run means Grafana's log and trace views,
  the run and session tables, or the unit 4 review app.
- The AGPL components constrain what may enter this repo: configs written here,
  nothing copied from upstream.

Follow-on work:

- 0003–0006 implement this decision.
- A Langfuse importer, if one is ever wanted, is unit 4 work and reads the
  archive, not the stores.

## Alternatives considered

- **Langfuse as the primary backend (the vault plan's design).** Its OTLP
  endpoint accepts traces only, so Codex events and Claude Code's tool events
  would be lost or need a second path. It also stores no Prometheus metrics, so
  the server-metrics join needs another store regardless.
- **Langfuse alongside the Grafana stack from the start.** It is a fifth service
  to run for a view that "browsing traces is not the bottleneck"
  (`analysis/README.md`) does not yet justify. Deferred to unit 4 as an optional
  viewer.
- **Adding the stores to the existing monitoring VM.** At 2 vCPU / 4 GB it is too
  small for three more stores, and a separate host keeps homelab monitoring out
  of this project's failure domain.
- **Arize Phoenix.** Its Elastic License 2.0 allows private use but rules it out
  as a dependency of anything published here.
  <!-- assumption: Phoenix was weighed on license grounds only; no feature comparison was made -->
