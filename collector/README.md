# Telemetry stack: the source-agnostic collector

> Status, 2026-10-21: the stack runs on the Mac under Docker Desktop (spec 0003)
> and passed a live smoke test. It has not yet been deployed to the VM (spec
> 0006). See *Verification* at the end.

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
 llama-swap gateway /metrics    ──> (prometheus     ├──>│ Tempo :3200      │ traces, 90d (optional)
                                     receiver)       │   └──────────────────┘
                                                     │   ┌──────────────────┐
                                                     └──>│ archive/*.zst    │ raw, forever (zstd)
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
| Storage | `/srv/telemetry/{archive,landing,collector-state,victoria,loki,tempo}` | archive can be an NFS mount from the NAS |
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

## Images

Every image in `compose.yaml` is pinned as `tag@sha256:<index digest>`. An index
digest names one multi-arch manifest: the Mac pulls its `linux/arm64` entry, an x86
Ubuntu host pulls its `linux/amd64` entry, and a re-pushed tag cannot change
either. Checked 2026-10-21 with `docker buildx imagetools inspect`.

| Image | Tag | Index digest | Platforms used | User |
|---|---|---|---|---|
| `otel/opentelemetry-collector-contrib` | `0.163.1` | `sha256:1175b03003053962012f5a231f5567f66a3e8fdb960c7835c0e48f7aa1555806` | `linux/amd64`, `linux/arm64` | 10001 |
| `victoriametrics/victoria-metrics` | `v1.154.1` | `sha256:c7884f32920dd45a9d86542a717227e63af03e060e53bbf8031e6e6fd0e1e05d` | `linux/amd64`, `linux/arm64` | root |
| `grafana/loki` | `3.7.9` | `sha256:0db216b630ae1f348a1584c67f3cead8b4c1fc839169a39501db664744158e28` | `linux/amd64`, `linux/arm64` | 10001 |
| `grafana/tempo` | `2.10.10` | `sha256:12b0820f13ffa10ce956b6e9acc3b03f3cffded99109be6cc09d106fe56e5d38` | `linux/amd64`, `linux/arm64` | 10001 |
| `ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen` | `v0.163.0` | `sha256:569b79a39f8313db839a727e8eac161c66b8a6c2e633f2da4ad352ce7b383c23` | `linux/amd64`, `linux/arm64` | 10001 |

To upgrade one, read the new index digest and its users with
`docker buildx imagetools inspect <image>:<tag>`. Then update all four places
that pin it:
- `compose.yaml`;
- this table;
- the `SPEC_INDEX_DIGESTS` constant in `tests/test_collector_compose.py`;
- the spec that approved the upgrade.

Re-run the validators in *Verification*. If the Collector changes, also re-check
the `exporter.file.nativeCompression` gate.

## Inference server endpoints

What each server behind llama-swap exposes, from the inference host's sysadmin
agent on 2026-10-21 (llama-swap v264). Names, flags and paths only; addresses
live in the vault's `ENDPOINTS.md`. Today the stack does not scrape these servers: per-model
metrics come from a scraper on the inference host (spec 0007), for the reason
given below. None of these servers attaches a model label, so that scraper must
add `model_id` itself.

| Build | llama-swap model ID | `/metrics` | `/slots` | Prometheus path on the server | `llamacpp:*` names |
|---|---|---|---|---|---|
| llama.cpp upstream b11552 | `qwen3.8-27b-ud-q8_k_xl` | Prometheus text, off by default; needs `--metrics` | on by default in this build; `--slots` added | `/metrics` (via llama-swap: `/upstream/qwen3.8-27b-ud-q8_k_xl/metrics`) | not observed on this build yet (needs `--metrics`). Upstream master documents `prompt_tokens_total`, `prompt_seconds_total`, `prompt_tokens_seconds`, `tokens_predicted_total`, `tokens_predicted_seconds_total`, `predicted_tokens_seconds`, `requests_processing`, `requests_deferred`, `n_tokens_max`, `n_decode_total`, `n_busy_slots_per_decode` and four `spec_decode_*` counters (llama.cpp `tools/server/README.md`, MIT, retrieved 2026-10-21) |
| Strata v0.1.45 | `qwen3.8-flash-next-unsloth-q8_0` | JSON; Prometheus text at `/metrics/prometheus`, built in | built in | `/metrics/prometheus` (via llama-swap: `/upstream/qwen3.8-flash-next-unsloth-q8_0/metrics/prometheus`) | `prompt_tokens_total`, `prompt_tokens_cached_total`, `prompt_seconds_total`, `prompt_tokens_seconds`, `tokens_predicted_total`, `tokens_predicted_seconds_total`, `predicted_tokens_seconds`, `requests_processing`, `requests_deferred`; also `strata:*`. No `n_tokens_max` |
| Project Maya v1.1.4 | `glm-5.3-flash-maya-l` | JSON only | built in | none; v1.1.5+ adds a partial Prometheus set, still without `n_tokens_max` | none |
| Sybil (SGLang 0.5.20, exllamav3 1.5.1) | `glm-5.3-flash` | no `llamacpp:*` route | not served | none; needs an exporter | none |

llama-swap's own `/metrics` serves system and GPU telemetry only, never
`llamacpp:*`. It returns 503 while `performance.disabled: true`; the host set it to
`false` on 2026-10-21. Names, with label keys in parentheses:
- CPU and load: `llamaswap_cpu_util_percent` (`core`) and
  `llamaswap_load_average` (`interval`).
- Memory and swap, with no label: `llamaswap_memory_{free,total,used}_bytes`,
  `llamaswap_swap_total_bytes` and `llamaswap_swap_used_bytes`.
- Network: `llamaswap_network_bytes_total` (`interface`, `direction`).
- GPU, each labelled `id`, `name` and `uuid`:
  - `llamaswap_gpu_util_percent`;
  - `llamaswap_gpu_memory_{used,total}_bytes` and
    `llamaswap_gpu_memory_util_percent`;
  - `llamaswap_gpu_power_draw_watts`;
  - `llamaswap_gpu_temperature_celsius` and
    `llamaswap_gpu_vram_temperature_celsius`;
  - `llamaswap_gpu_fan_speed_percent`.

**Names as stored in VictoriaMetrics.** These come from one supervised 75 s
capture of the loaded Strata model on 2026-10-21, made by a temporary Collector
with the same receiver and OTLP path as the stack:
- The colon becomes an underscore: `llamacpp:prompt_tokens_total` is stored as
  `llamacpp_prompt_tokens_total`, and `strata:requests_total` as
  `strata_requests_total`.
- Counters keep `_total`.
- The stored names for Strata are `llamacpp_prompt_tokens_total`,
  `llamacpp_prompt_tokens_cached_total`, `llamacpp_prompt_seconds_total`,
  `llamacpp_prompt_tokens_seconds`, `llamacpp_tokens_predicted_total`,
  `llamacpp_tokens_predicted_seconds_total`, `llamacpp_predicted_tokens_seconds`,
  `llamacpp_requests_processing` and `llamacpp_requests_deferred`.
- Label keys are `model_id` (added by the temporary capture's scrape job; 0007's
  scraper must add the same), `service_name` (the job name), `server_address`, `server_port`, `url_scheme`, `service_instance_id`,
  `scope_name` and `scope_version`. The stack's `resource` processor also adds
  `deployment_environment`.
- `server_address` holds the real host. Keep it out of anything tracked.

**Why the Collector does not scrape `/upstream/`** (spec 0003, Standing decision D6):
- A request to `/upstream/<model-id>/…` for an unloaded model makes llama-swap
  load it. `upstream.ignorePaths` can turn that into a 409 instead.
- For a model that is already loaded, llama-swap v264 still counts the request
  as use and resets the 900 s idle timer (`ttl: 900`), even on an ignored path.
  A 30 s scrape would keep the model loaded indefinitely. That suppresses the
  swap and load waits this stack exists to observe.

So the Collector scrapes only llama-swap's own `/metrics`. Per-model `llamacpp:*`
metrics come from a scraper on the inference host that reads each server's own
port, which llama-swap never sees (spec 0007). Per-model metrics exist only while
a model is loaded; when it unloads, its port is closed.

## Run locally on the Mac

The same `compose.yaml` runs under Docker Desktop on the Mac (spec 0003) and on the
telemetry VM (spec 0006). Only the untracked `.env` differs.

1. Create `.env` from the tracked template, then set the three values:

   ```bash
   cp env.example .env
   ```

   - `TELEMETRY_ROOT`: the data root. On the Mac it must sit under a path Docker
     Desktop shares.
   - `LLAMA_SWAP_TARGET`: `host:port` of the llama-swap gateway, from the vault's
     `ENDPOINTS.md`.
   - `TELEMETRY_BIND_ADDR`: `127.0.0.1` on the Mac, so nothing is published on
     whatever network the laptop joins. The VM uses its LAN or Tailnet address.

   Compose stops with `set <NAME> in collector/.env` when one is missing.
2. Create the data root. Write `root` out as an absolute path. Compose expands
   `$HOME` inside `.env`, but a value pasted into the shell in single quotes is
   not expanded, so the folders would land in a literal `$HOME/` directory under
   the current one. The steps differ by host, because Docker Desktop and Docker on
   Linux treat bind-mount ownership differently:

   - **Mac (Docker Desktop).** Create the folders as yourself and change no
     owners. Docker Desktop's file sharing writes as your macOS user, so the
     containers can write to folders you own. This is how the live run's data
     root was made. A folder chowned to UID 10001 may become unwritable through
     file sharing.

     ```bash
     root=/path/from/TELEMETRY_ROOT
     mkdir -p "$root"/{archive,landing,collector-state,victoria,loki,tempo}
     ```

   - **Linux (the telemetry VM, spec 0006).** The Collector, Loki and Tempo run
     as UID 10001 and must own their folders. VictoriaMetrics runs as root.
     `landing/` belongs to the user who ships files, and UID 10001 reads it.
     Spec 0006 runs these steps for real:

     ```bash
     root=/path/from/TELEMETRY_ROOT
     sudo mkdir -p "$root"/{archive,landing,collector-state,victoria,loki,tempo}
     sudo chown 10001:10001 "$root"/archive "$root"/collector-state "$root"/loki "$root"/tempo
     sudo chown "$USER" "$root"/landing && sudo chmod 0755 "$root"/landing
     ```

3. Start the stack and check the Collector's health endpoint. It should print 200:

   ```bash
   docker compose up -d
   curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:13133
   ```

4. Smoke test: send one trace, metric and log through the Collector with
   telemetrygen, then find each one in its store and in the archive. The
   `telemetrygen` service runs only under the `smoke` profile:

   ```bash
   docker compose --profile smoke run --rm telemetrygen      # one trace
   docker compose --profile smoke run --rm telemetrygen metrics \
     --otlp-endpoint=otel-collector:4317 --otlp-insecure --metrics=1
   docker compose --profile smoke run --rm telemetrygen logs \
     --otlp-endpoint=otel-collector:4317 --otlp-insecure --logs=1

   curl -s http://127.0.0.1:8428/api/v1/query \
     --data-urlencode 'query=last_over_time(gen{service_name="telemetrygen"}[1h])'
   curl -s -G http://127.0.0.1:3100/loki/api/v1/query_range \
     --data-urlencode 'query={service_name="telemetrygen"}'
   curl -s -G http://127.0.0.1:3200/api/search \
     --data-urlencode 'tags=service.name=telemetrygen'
   root=/path/from/TELEMETRY_ROOT    # restate it in a new shell
   zstd -dc "$root"/archive/logs.jsonl.zst | grep -c telemetrygen
   ```

   macOS has no `zstd` command by default: `brew install zstd`.

   VictoriaMetrics hides samples newer than 30 seconds (`-search.latencyOffset`),
   so query it half a minute after sending.
5. Teardown stops the containers and keeps every byte under the data root:

   ```bash
   docker compose down
   ```

   Never delete the data root to "reset" the stack. The archive under it is the
   record.

## Pipelines (otel-collector.yaml)

Three pipelines, one per signal, all with the same processor chain:

1. `memory_limiter` first (Collector docs say so).
2. `resource` adds `deployment.environment=homelab` so dashboards can filter.
3. `redaction` masks API-key, AWS-key, PEM and email shapes in every attribute. This
   is the second redaction layer; the first is at the source (hook or SDK).
4. `transform` truncates any attribute over 16 KB so a pasted file can't bloat a span.
5. `batch`.

Every pipeline exports to **two places**: the long-term store for that signal, and
the `file` exporter writing `archive/<signal>.jsonl.zst`. Each file is OTLP JSON,
one line per batch, inside a standard zstd stream, readable with `zstd -dc`. When a
file reaches 256 MB it rotates. Rotated files keep the `<signal>.jsonl` prefix
and the `.zst` suffix with a timestamp between them, so readers glob
`<signal>.jsonl*.zst`, not `*.jsonl.zst`. No rotation has been observed yet; check
the exact name when the first one happens. A reader should also tolerate a final
line with no newline and skip blank lines, in case a rotation falls between a
batch and its newline. This
needs the Collector feature gate `exporter.file.nativeCompression`, which
`compose.yaml` sets. Without the gate, the exporter writes length-prefixed,
separately compressed batches that `zstd -d` cannot read. The gate is Alpha in
0.163.1; re-check it whenever the Collector is upgraded. The archive is
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
| llama-swap gateway | Collector scrapes the gateway's own `/metrics` | system and GPU metrics | `performance.disabled: false` in the llama-swap config. Never scrape `/upstream/…` through it (see *Inference server endpoints*) |
| llama.cpp-style servers behind llama-swap | a scraper on the inference host (spec 0007) | `llamacpp:*` prompt/predicted token counters, tokens/s, `requests_deferred`, `n_tokens_max` where the build has it | start each upstream with `--metrics`. Per-request lines from llama-swap `/logs/stream/http` are not captured yet |
| Your own Python agents | OTLP from the OTel SDK | GenAI spans + metrics | `OTEL_EXPORTER_OTLP_ENDPOINT=http://telemetry.example:4318` |
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
| Archive (`<signal>.jsonl*.zst`) | forever | you; prune by hand or move to NAS cold storage |
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
2. `cp env.example .env` and set the three variables, then create the data root, as
   in *Run locally on the Mac*. `docker compose up -d`.
3. Check Collector health on the bind address, for example
   `curl -fsS http://192.0.2.10:13133`, replacing `192.0.2.10` with the
   `TELEMETRY_BIND_ADDR` value from `.env`. Validate `otel-collector.yaml` before the first
   start, as in its header comment.
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
- `tempo.yaml`: parses on 2.10.10. The note first written here, "the start failed
  only on the missing `/tempo` volume, which compose mounts", was wrong: see the
  2026-10-21 live run below.
- Re-validated 2026-10-21 after the `inbox` → `landing` rename: `otel-collector.yaml`
  passes `otelcol-contrib validate` on 0.163.1; `loki-config.yaml` reports "config
  is valid" on 3.7.9.
- Validated 2026-10-21 for spec 0003, by index digest, after `LLAMA_SWAP_TARGET`
  moved to `.env`:
  - `otel-collector.yaml` passes `otelcol-contrib validate` on the `linux/arm64` and
    `linux/amd64` entries of the 0.163.1 index. A placeholder
    `LLAMA_SWAP_TARGET=gpu.example:9292` is passed in.
  - `loki-config.yaml` reports "config is valid" on both entries of the 3.7.9 index.
  - The amd64 runs use the index's amd64 manifest digest (`docker buildx
    imagetools inspect <index> --raw`) under emulation, and the container reports
    `amd64`. Docker Desktop's classic image store holds one platform per
    reference, so `--platform linux/amd64` on the index digest itself fails with
    "cannot overwrite digest".
  - `docker compose --env-file env.example config --quiet` passes. Without a
    `.env`, compose stops and names each missing variable.
- Live run on the Mac, 2026-10-21 (spec 0003), Docker Desktop on arm64:
  - **Tempo mount.** Tempo would not start: in the image, `/tempo` is the tempo
    binary, so the data directory cannot be mounted there. It now mounts at
    `/var/tempo`, and `tempo.yaml` stores blocks and WAL under `/var/tempo`. After
    that fix Tempo started and served a search.
  - **Stack.** All four services run from their `linux/arm64` images (`docker
    inspect`), and the Collector health endpoint returns 200 on `127.0.0.1:13133`.
  - **Smoke test.** One telemetrygen trace, metric and log each landed in its
    store and in the archive:
    - VictoriaMetrics: series `gen` with `service_name=telemetrygen`;
    - Loki: 1 line for `{service_name="telemetrygen"}`;
    - Tempo: 1 trace for `service.name=telemetrygen`;
    - archive: one telemetrygen record in each of the three `*.jsonl.zst` files,
      read with `zstd -dc`.
  - **Archive format.** The archive first came out in the exporter's default
    framed format. It was switched to native zstd (the Pipelines section above),
    and `otel-collector.yaml` re-validated with the gate on both the `linux/arm64`
    and `linux/amd64` entries of the 0.163.1 index.
  - **OTLP labels.** Resource attributes arrive as labels `service_name`,
    `deployment_environment`, `scope_name` and `scope_version`. The scrape job
    appears as `service_name`, not `job`.
  - **Gateway scrape.** At first the `llama-swap` job got 503, because
    `performance.disabled: true` was set on the host. After the host set it to
    `false` (llama-swap restarted, `ttl: 900` unchanged), `up{service_name="llama-swap"}`
    is 1. All 16 `llamaswap_*` names are stored unchanged, and there are 4 GPU
    series (labels `id`, `name`, `uuid`).
  - **Strata capture.** One supervised capture recorded the stored `llamacpp_*`
    names (see *Inference server endpoints*). The Strata model was already
    loaded, and the temporary Collector was removed afterwards.
- Re-validated 2026-10-21 after the review fixes, on the final files:
  - `tempo.yaml` with `/var/tempo` starts on the `linux/amd64` entry of the 2.10.10
    index (amd64 manifest digest, under emulation): "Tempo started". On arm64 it
    starts in the live stack.
  - `otel-collector.yaml` (gateway-only scrape, archive gate) passes `validate`
    on both the arm64 and amd64 entries of the 0.163.1 index.
  - `docker compose --env-file env.example config --quiet` passes on the final
    `compose.yaml` (Tempo mount, feature gate).

Not yet done:
- per-model `llamacpp:*` scraping (spec 0007);
- the VM deployment (spec 0006);
- the Codex `[otel]` exporter syntax.
