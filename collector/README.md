# Telemetry stack: the source-agnostic collector

> Status, 2026-10-21: the stack runs on the Mac under OrbStack, a
> Docker-compatible runtime (spec 0003), and passed a live smoke test. Per-model server metrics come from an agent on
> the inference host (spec 0007, `inference-agent/`). The stack has not yet been
> deployed to the VM (spec 0006). See *Verification* at the end.

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
 ledgers (rsync from Macs)          ▼                ├──>│ Loki :3100       │ logs, 2y
 ──> landing/ ────────────────────> OTel Collector ────┤   └──────────────────┘
     (filelog: ledgers only)       (one process)     │   ┌──────────────────┐
 llama-swap gateway /metrics    ──> (prometheus     ├──>│ Tempo :3200      │ traces, 90d (optional)
                                     receiver)       │   └──────────────────┘
                                                     │   ┌──────────────────┐
                                                     └──>│ archive/*.zst    │ raw, forever (zstd)
                                                         └──────────────────┘
                                                                  ▲
                                            existing Grafana VM queries the three stores 
```

The model servers behind llama-swap are not in the diagram's scrape path. An
agent on the inference host scrapes each one on its own port and sends the
samples to the Collector as OTLP on 4318 (*Inference host agent*).

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
`docker buildx imagetools inspect <image>:<tag>`. Then update every place
that pins it:
- `compose.yaml`;
- for the Collector, `inference-agent/compose.yaml` too (a test keeps the two
  image strings identical), then re-copy it to the inference host;
- this table;
- the `SPEC_INDEX_DIGESTS` constant in `tests/test_collector_compose.py`;
- the spec that approved the upgrade.

Re-run the validators in *Verification*. If the Collector changes, also re-check
the `exporter.file.nativeCompression` gate.

## Inference server endpoints

What each server behind llama-swap exposes, from the inference host's sysadmin
agent on 2026-10-21 (llama-swap v264). Names, flags and paths only; addresses
live in the vault's `ENDPOINTS.md`. The stack's Collector never scrapes these
servers through llama-swap, for the reason given below. The inference host agent
(spec 0007) scrapes each one on its own port instead. None of these servers
attaches a model label, so the agent's targets file adds `model_id`.

| Build | llama-swap model ID | `/metrics` | `/slots` | Prometheus path on the server | `llamacpp:*` names |
|---|---|---|---|---|---|
| llama.cpp upstream b11552 | `qwen3.8-27b-ud-q8_k_xl` | Prometheus text, off by default; `--metrics` added 2026-10-21 (spec 0007 D5) | on by default in this build; `--slots` not set | `/metrics` (via llama-swap: `/upstream/qwen3.8-27b-ud-q8_k_xl/metrics`) | observed 2026-10-21 through the inference host agent (stored names): `prompt_tokens_total`, `prompt_tokens_cached_total`, `prompt_seconds_total`, `prompt_tokens_seconds`, `tokens_predicted_total`, `tokens_predicted_seconds_total`, `predicted_tokens_seconds`, `requests_processing`, `requests_deferred`, `n_tokens_max_total` (a counter upstream, so storage adds `_total`), `n_decode_total`, `n_busy_slots_per_decode` and four `spec_decode_*` counters, each with the `llamacpp_` prefix |
| Strata v0.1.45 | `qwen3.8-flash-next-unsloth-q8_0` | JSON; Prometheus text at `/metrics/prometheus`, built in | built in | `/metrics/prometheus` (via llama-swap: `/upstream/qwen3.8-flash-next-unsloth-q8_0/metrics/prometheus`) | `prompt_tokens_total`, `prompt_tokens_cached_total`, `prompt_seconds_total`, `prompt_tokens_seconds`, `tokens_predicted_total`, `tokens_predicted_seconds_total`, `predicted_tokens_seconds`, `requests_processing`, `requests_deferred`; also `strata:*`. No `n_tokens_max` |
| Project Maya v1.1.4 | `glm-5.3-flash-maya-l` | JSON only | built in | none; v1.1.5+ adds a partial Prometheus set, still without `n_tokens_max` | none |
| Sybil (SGLang 0.5.20, exllamav3 1.5.1) | `glm-5.3-flash` | no `llamacpp:*` route | not served | none; needs an exporter | none |

**Coverage** by the inference host agent (spec 0007):
- Covered: Strata (`/metrics/prometheus`) and llama.cpp (`/metrics`, once the
  host adds `--metrics`, spec 0007 D5).
- Left uncovered, not worked around: Project Maya v1.1.4, whose `/metrics` is
  JSON (v1.1.5+ adds Prometheus), and Sybil, which has no Prometheus route.

**One scraper per llama.cpp server.** Each `/metrics` call resets the server's
throughput buckets: `prompt_tokens_seconds`, `predicted_tokens_seconds` and
`n_busy_slots_per_decode` average over the window since the previous call
(llama.cpp `tools/server/server-context.cpp` at tag `b11552`, the deployed build,
commit `23b0202a189c44a54625aadcb37a946dd1d6278d`, MIT, retrieved 2026-10-21:
https://github.com/ggml-org/llama.cpp/blob/b11552/tools/server/server-context.cpp).
A second scraper, such as an ad-hoc `curl`, shortens the agent's next window.
The counters are unaffected.

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
- Label keys are `model_id` (added by the temporary capture's scrape job; the
  agent's targets file adds the same), `service_name` (the job name), `server_address`, `server_port`, `url_scheme`, `service_instance_id`,
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
metrics come from the inference host agent, which reads each server's own port,
so llama-swap never sees the requests and the TTL is untouched (spec 0007).
Per-model metrics exist only while a model is loaded; when it unloads, its port
is closed and the agent's `up` for it drops to 0.

## Run locally on the Mac

The same `compose.yaml` runs under OrbStack on the Mac (spec 0003) and on the
telemetry VM (spec 0006). Only the untracked `.env` differs.

1. Create `.env` from the tracked template, then set the three values:

   ```bash
   cp env.example .env
   ```

   - `TELEMETRY_ROOT`: the data root. On the Mac it must sit under a path OrbStack
     shares.
   - `LLAMA_SWAP_TARGET`: `host:port` of the llama-swap gateway, from the vault's
     `ENDPOINTS.md`.
   - `TELEMETRY_BIND_ADDR`: the address every published port binds to.
     - `0.0.0.0` on the home network, so the inference host agent can reach the
       Collector (spec 0007, D1). The macOS firewall, if on, must allow
       incoming connections for OrbStack, the Mac's Docker runtime.
     - `127.0.0.1` anywhere else, or stop the stack before leaving home: with
       `0.0.0.0` the stores and OTLP intake are open on any network the laptop
       joins, including VictoriaMetrics' unauthenticated admin API. A laptop
       that sleeps at home and wakes elsewhere resumes the stack as it was.
       The agent queues on disk until the Mac is back.
     - To see what is open away from home, `lsof -nP -iTCP:4318 -iTCP:8428
       -sTCP:LISTEN`: a `*:` address means all interfaces, so run
       `docker compose stop`.
     - The VM uses its LAN or Tailnet address (spec 0006).

   Compose stops with `set <NAME> in collector/.env` when one is missing.
2. Create the data root. Write `root` out as an absolute path. Compose expands
   `$HOME` inside `.env`, but a value pasted into the shell in single quotes is
   not expanded, so the folders would land in a literal `$HOME/` directory under
   the current one. The steps differ by host, because the Mac runtime and Docker on
   Linux treat bind-mount ownership differently:

   - **Mac (OrbStack).** Create the folders as yourself and change no
     owners. OrbStack's file sharing writes as your macOS user, so the
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

## Inference host agent (spec 0007)

`inference-agent/` holds a second, small Collector that runs on the inference
host, not with the stack. Every 30 s it scrapes each model server on that
server's own local port, labels the samples with the llama-swap `model_id`, and
sends them as OTLP to the stack's Collector. llama-swap never sees these
requests, so model TTLs are untouched. Its sending queue is on disk and retries
without a time limit, so samples taken while the stack is unreachable (the Mac
asleep or away) are delivered when it returns.

| File | On the inference host |
|---|---|
| `compose.yaml` | the install directory, as is. Same Collector image as the stack, host networking, no published ports |
| `otel-agent.yaml` | next to `compose.yaml`, as is |
| `env.example` | template for `.env` next to `compose.yaml` |
| `targets.example.json` | template for `targets.json` in the targets directory |

Prerequisites on the inference host, applied by its sysadmin:
- Docker Engine with the Compose v2 plugin (spec 0007 D4), from the same package
  source as the engine;
- `--metrics` on the llama.cpp server's command line (D5); Strata serves
  `/metrics/prometheus` without a flag;
- `127.0.0.1:13133` (health) and `127.0.0.1:8888` (the agent's own metrics)
  free, because host networking shares the host's ports.

Install:

1. Create an install directory on the inference host that the copying user
   owns, then copy the four files from `collector/inference-agent/` (not
   `collector/`, which has its own `compose.yaml` and `env.example`) straight
   into it:

   ```bash
   cd collector/inference-agent
   ssh -t gpu.example 'sudo mkdir -p /opt/agentic-telemetry/inference-agent &&
     sudo chown "$USER" /opt/agentic-telemetry/inference-agent'
   scp compose.yaml otel-agent.yaml env.example targets.example.json \
     gpu.example:/opt/agentic-telemetry/inference-agent/
   ```

   The repo is not cloned there, so any later change to a tracked file needs a
   re-copy. After re-copying `otel-agent.yaml`, run
   `docker compose up -d --force-recreate`: the Collector does not reload its
   config, compose does not see a changed mounted file, and a copy that
   replaces the file's inode is invisible to the running container's
   single-file mount.

2. In the install directory, `cp env.example .env` and set:
   - `TELEMETRY_OTLP_ENDPOINT`: the stack's OTLP/HTTP address. While the stack
     runs on the Mac, the Mac's current LAN address, from `ENDPOINTS.md`. Not
     its `.local` name: the agent is a static Go binary that cannot resolve
     mDNS, even where the host can. If the Mac's address changes, update this
     value and run `docker compose up -d`; the queue holds samples meanwhile.
     Spec 0006 repoints it to the VM.
   - `AGENT_QUEUE_DIR` and `AGENT_TARGETS_DIR`: keep the example paths unless the
     host needs others.
3. Create the queue and targets directories. The image runs as UID 10001:

   ```bash
   sudo mkdir -p /var/lib/agentic-telemetry/queue /etc/agentic-telemetry/targets
   sudo chown 10001:10001 /var/lib/agentic-telemetry/queue
   sudo chmod 0700 /var/lib/agentic-telemetry/queue
   sudo chmod 0755 /etc/agentic-telemetry/targets
   ```

4. Write `/etc/agentic-telemetry/targets/targets.json` from `targets.example.json`,
   readable by UID 10001 (`sudo chmod 0644`). An unreadable directory or file
   gives the agent zero targets with no error. Write one entry per
   Prometheus-capable model server, with its real local port, its llama-swap
   `model_id` and its `__metrics_path__`. The example's ports, 65001 and 65002,
   are placeholders; the real ones are in the vault's `ENDPOINTS.md`.
   - Every entry is a model server's own port: never llama-swap's port, and never
     a `__metrics_path__` containing `/upstream/`, which would reset that
     model's TTL on every scrape.
   - Never copy `targets.example.json` itself into the targets directory: the
     agent scrapes every `*.json` there.
   - Editing `targets.json` later needs no restart; the agent re-reads the
     directory.
5. Start it and check health. The health check should print 200:

   ```bash
   docker compose up -d
   curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:13133
   ```

6. Check delivery a minute later. Health only says the process is up; a wrong
   or unresolvable `TELEMETRY_OTLP_ENDPOINT` still reports 200 while the queue
   fills:

   ```bash
   curl -s http://127.0.0.1:8888/metrics |
     grep -E '^otelcol_exporter_(sent_metric_points|queue_size)'
   docker compose logs --since 2m otel-agent | grep -E 'no such host|Exporting failed'
   ```

   Delivering: `otelcol_exporter_sent_metric_points` is present and rising, and
   `otelcol_exporter_queue_size` stays near 0. Not delivering: no sent-points
   line, a queue size that grows each scrape, and `no such host` or `Exporting
   failed` in the log. Retries never stop, so no "failed points" counter
   appears. Names checked on 0.163.1, 2026-10-21.

Checks, with VictoriaMetrics on the stack. `nocache=1` keeps VictoriaMetrics'
rollup cache from hiding samples that arrived late from the queue:
- With a model loaded: `count by (__name__, model_id)({__name__=~"llamacpp_.*"})`
  lists that model's series, and `up{service_name="inference-models"}` is 1 for it.
- After the model's `ttl` passes idle: llama-swap unloads it as before, its `up`
  drops to 0, and nothing loads it again.
- llama-swap's access log shows no requests from the agent.

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
| Pi, Claude Code and Codex transcripts | landing zone, batch only | session JSONL (Codex also `.jsonl.zst`) | `mac-agent/ship-ledgers.sh` copies them to `landing/<host>/_sessions/{pi,claude-code,codex}/`; `session-table` reads them there. Never tailed (spec 0004) |
| Delegate runner | landing zone | `ledger.jsonl` (tailed); `results/<run>/{result.json,state.json,session/*.jsonl}` (batch) | `mac-agent/ship-ledgers.sh` copies these, and nothing else, to `landing/<host>/<project>/.agentic/delegate/`; `run-table` reads them there |
| Scaffold tool-call ledger (Phase 1, all clients) | landing zone | one JSON line per tool call | `mac-agent/ship-ledgers.sh` copies `.agentic/telemetry/*.jsonl` to `landing/<host>/<project>/.agentic/telemetry/`, where the Collector tails it |
| llama-swap gateway | Collector scrapes the gateway's own `/metrics` | system and GPU metrics | `performance.disabled: false` in the llama-swap config. Never scrape `/upstream/…` through it (see *Inference server endpoints*) |
| llama.cpp-style servers behind llama-swap | the inference host agent scrapes each server's own port and sends OTLP to `:4318` (spec 0007) | `llamacpp:*` prompt/predicted token counters, tokens/s, `requests_deferred`, `n_tokens_max` where the build has it | each server's Prometheus route on (llama.cpp: `--metrics`), and a line per model in the agent's `targets.json`. Per-request lines from llama-swap `/logs/stream/http` are not captured yet |
| Your own Python agents | OTLP from the OTel SDK | GenAI spans + metrics | `OTEL_EXPORTER_OTLP_ENDPOINT=http://telemetry.example:4318` |

The landing zone is where file-only sources meet: a job on any machine can rsync
files there. The Collector tails only the ledgers, `landing/*/*/.agentic/delegate/ledger.jsonl`
and `landing/*/*/.agentic/telemetry/*.jsonl`, with a checkpoint so nothing is
ingested twice. Transcripts carry captured tool content, so they stay files that
`run-table` and `session-table` read in batch rather than records in Loki (spec 0004).
A new source that only writes JSONL either matches those ledger paths or needs a
receiver change.

## Flow of records, including offline

Two paths. Both end in the same archive and the same stores.

| | Live path (OTLP) | Batch path (files) |
|---|---|---|
| Carries | Claude Code metrics and events, Codex events, your apps' spans | tool-call ledgers, delegate results, session transcripts |
| Mac side | `mac-agent/otel-agent.yaml`: a Collector on `localhost:4318` with a disk-backed queue and a local JSONL copy | `mac-agent/ship-ledgers.sh` (every 15 min from launchd, from 0006): rsync over SSH (LAN or Tailscale) into `landing/<host>/`, or `--dest DIR` into a local landing zone |
| Offline | the queue fills on disk and drains when the VM is reachable; the local copy is written regardless | the script prints one line and exits 0 (1 if discovery found a name collision or a symlinked `.agentic`); the next run catches up. Files never change after a session ends, so nothing is lost |
| Authoritative? | best effort: the queue has a cap and the clients drop on their own if no agent is listening | yes: the ledger and transcript files are the record of truth |

Rule: **clients point at localhost, never at the VM.** The Mac agent is the only
process that knows the VM's address. Sessions run with a local model and no network at
all (Pi on Ollama on the laptop) produce only files, so the batch path alone covers them.

Timeline for one session:

1. During the session: Claude Code pushes OTLP to the Mac agent; the scaffold hook
   appends to `.agentic/telemetry/<session>.jsonl`. Both are local writes.
2. Within minutes, when the VM is reachable: the agent drains its queue; the ship
   script rsyncs new ledger lines and transcripts into the landing zone.
3. On the VM, continuously: the Collector tails the landing zone's ledgers and writes
   every line to the archive and Loki, timestamped with the line's own `ts`; the
   filelog checkpoint prevents re-ingest. Each project is its own Loki stream
   (`landing_host` and `landing_project` are index labels), because Loki refuses
   a line far behind the newest one in its stream. A project's ledger files
   share that stream, so once Phase 1 writes one `telemetry/<session>.jsonl` per
   session, a backfill or an outage of more than an hour can still refuse an
   older session's lines; revisit the labels then (0004, D8). Loki also refuses lines older
   than a week (`reject_old_samples_max_age: 1w`, its default in 3.7.9), so a
   first backfill of older ledger lines reaches only the archive (0004, D1, D4).
4. Nightly: the analysis package rebuilds the run table and matrices from the archive
   and landing zone into Parquet.

## Retention policy, in one place

| Layer | Keeps | Controlled by |
|---|---|---|
| Archive (`<signal>.jsonl*.zst`) | forever | you; prune by hand or move to NAS cold storage |
| VictoriaMetrics | 5 years | `-retentionPeriod` flag |
| Loki | 2 years | `limits_config.retention_period` + compactor |
| Tempo | 90 days | `compactor.compaction.block_retention` |
| Landing-zone files | forever, for now | plan: until processed + 30 days. Nothing deletes landing files yet, and the ship script never passes `--delete` (spec 0004) |

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
7. Run `mac-agent/ship-ledgers.sh` on each Mac (`--help` lists its options;
   set `PROJECTS_ROOT` to where your projects live), then schedule it under
   launchd (0006).

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
    `amd64`. The Mac runtime's image store holds one platform per
    reference, so `--platform linux/amd64` on the index digest itself fails with
    "cannot overwrite digest".
  - `docker compose --env-file env.example config --quiet` passes. Without a
    `.env`, compose stops and names each missing variable.
- Live run on the Mac, 2026-10-21 (spec 0003), OrbStack on arm64. Spec 0003
  and earlier revisions of this README say Docker Desktop; the runtime was
  OrbStack throughout:
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

- Inference host agent, spec 0007, on the Mac, 2026-10-21:
  - `inference-agent/otel-agent.yaml` passes `otelcol-contrib validate` on the
    `linux/arm64` and `linux/amd64` entries of the 0.163.1 index (amd64 by its
    manifest digest, under emulation; the container reports `amd64`). A
    placeholder `TELEMETRY_OTLP_ENDPOINT` is passed in.
  - `docker compose -f inference-agent/compose.yaml --env-file
    inference-agent/env.example config --quiet` passes, including the log cap.
  - **E1, file-based discovery.** The tracked agent config ran against two fake
    local endpoints and the Mac stack's Collector. It used a scratch targets
    directory: one target with `__metrics_path__=/metrics/prometheus` (that
    endpoint answers 404 on `/metrics`), one with no path label, and one
    closed port.
    - VictoriaMetrics held both test series, each with its `model_id`.
    - `up` was 1, 1 and 0. The closed port logs one warning per scrape, and
      the agent keeps running.
    - No `__metrics_path__` label is stored.
    - A target added by editing `targets.json` in place was scraped 36 s later,
      and one added by an atomic rename 31 s later, both without a restart.
  - **E2, disk queue.** The stack's Collector was stopped for 5 minutes, and the
    agent was restarted once in the middle of it. Every 30 s sample from the
    outage reached VictoriaMetrics after the Collector came back, with no gap
    over 45 s. That includes the requests in flight at the restart.
    - The agent logs "Exporting failed. Dropping data." with `interrupted due to
      shutdown` for those requests, but the persistent queue keeps them and
      re-sends them.
    - The test series (`exp_*`) remain in the Mac stores and archive. They are
      throwaway data from this run.
- Re-validated 2026-10-21 after the spec 0007 review fixes (spec 0007 D6):
  - `inference-agent/otel-agent.yaml`, with the `/upstream/` drop rule and the
    comment fixes, passes `otelcol-contrib validate` on the arm64 and amd64
    entries of the 0.163.1 index.
  - Both compose files pass `config --quiet` with their `env.example`. The stack
    compose change is a header comment only.
  - **Drop rule, live.** An agent with one target on `/metrics/prometheus` and
    one with `__metrics_path__=/upstream/exp/metrics` scraped only the first.
    The fake server saw no request for the second, and it has no `up` series.
  - **Delivery metrics.** On 0.163.1 the agent's internal metrics on
    `127.0.0.1:8888` include `otelcol_exporter_sent_metric_points` and
    `otelcol_exporter_queue_size`. With an unresolvable endpoint, the log shows
    `no such host`, the queue size grows, and no sent-points line appears. With
    a working endpoint, sent points rise and the queue size stays at 0.

- Inference host agent live, 2026-10-21 (spec 0007):
  - **Install**, done by the inference host's sysadmin agent from the four
    copied files, with neither config edited:
    - Compose v2 plugin `docker-compose-v2` 2.40.3, from the same Ubuntu source
      as Docker Engine 29.1.3;
    - `--metrics` added to the llama.cpp unit and nothing else changed;
    - the container runs `amd64` from the pinned index digest, as UID 10001,
      with host networking;
    - its listeners are `127.0.0.1:13133` and `127.0.0.1:8888` only;
    - the queue directory is 0700 and owned by 10001, and `targets.json` is
      0644, the only JSON file in its directory, with two entries (Strata and
      llama.cpp) on each server's own port.
  - **Delivery.** Sent metric points rose (10, 20, then 140) with the queue at
    0 and no export errors. The Mac's VictoriaMetrics held
    `up{service_name="inference-models"}` for both `model_id`s, at 0 while both
    models were unloaded. Each closed port logs one warning per scrape.
  - **No side effects.** No model loaded from the agent's scrapes, and
    llama-swap's access log shows no agent requests.
  - **Loaded model.** The owner loaded `qwen3.8-27b-ud-q8_k_xl` with a normal
    request. Its `up` went from 0 to 1 on the first scrape after it was ready.
    VictoriaMetrics then held 16 `llamacpp_*` series with that `model_id` (names
    in *Inference server endpoints*), and `llamacpp_prompt_tokens_total` was
    above 0. Label keys: `model_id`, `service_name`, `server_port`, `url_scheme`,
    `service_instance_id`, `scope_name`, `scope_version`,
    `deployment_environment`.
  - **Idle unload.**
    - The inference host's watcher: after the last request, llama-swap unloaded
      the model 920 s later. That is the 900 s `ttl` within the watcher's
      polling tolerance.
    - The model stayed unloaded for the whole 1,813 s watch, and the watcher
      loaded nothing.
    - On the stack: the agent's `up` for that model dropped to 0 on the first
      scrape after the unload, and stayed 0 for all 62 scrapes of the next 31
      minutes. Its `llamacpp_*` series ended at the unload.

- Landing-zone shipping, spec 0004, on the Mac, 2026-10-22:
  - `otel-collector.yaml` (ledger-only `include`, `gotime` timestamp, host and
    project parsed into resource attributes) passes `otelcol-contrib validate` on
    the `linux/arm64` and `linux/amd64` entries of the 0.163.1 index (amd64 by its
    manifest digest, under emulation). The live Collector runs it.
  - **rsync.** The allowlist ships the same files under macOS openrsync
    (`/usr/bin/rsync`, with `/bin/bash` 3.2) and GNU rsync 3.2.7 (bash 5.2, in a
    Debian container). Neither copies a planted `pi-agent/auth.json`,
    `npm-cache/`, `segments/`, log, prompt or client credential file. After the
    review, the copy is `rsync -rpt` (no `-l`): both implementations skip a
    symlinked `result.json`, transcript or run directory, name it on stderr, exit
    0 and ship no target content.
  - **Timestamps.** A scratch Collector on the pinned image parsed ledger lines
    ending `:56Z`, `:56.123Z` and `+00:00` with their own `ts`, and did not read a
    `_sessions/` transcript.
  - **Same rows.** `ship-ledgers.sh --dest` into the local landing zone, then
    `run-table` and `session-table` on `landing/<host>/`: the same rows as from the
    project roots and client session directories (16 runs; 660 sessions), with the
    same `project` values. Counts only; the spec holds the breakdown.
  - **Loki streams.** With host and project as log attributes, every ledger
    shared one Loki stream, and Loki refused 23 of 28 backfilled lines as "entry
    too far behind" (the archive kept all 28). Parsed into resource attributes,
    they are index labels and each project is its own stream: a scratch Loki on
    `loki-config.yaml` then kept all 8 lines younger than a week and refused the
    20 older ones as "timestamp too old" (`reject_old_samples_max_age: 1w`, the 3.7.9
    default). A line with an old timestamp is only queryable after Loki flushes
    it to storage (`query_ingesters_within: 3h`).

Not yet done:
- the VM deployment (spec 0006);
- the Codex `[otel]` exporter syntax.
