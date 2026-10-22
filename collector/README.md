# Telemetry stack: the source-agnostic collector

> Status, 2026-10-22: the stack runs on the telemetry VM (spec 0006) and passed
> its bring-up checks on the LAN. Per-model server metrics come from an agent on the inference
> host (spec 0007, `inference-agent/`). The Mac's copy under OrbStack (spec 0003)
> is stopped and kept as a test bed on `127.0.0.1`. The `captures-pull` service
> keeps every local-model request and response llama-swap captures (spec 0008,
> *Local-model captures*). See *Verification* at the end.

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
| OS | Ubuntu 24.04 LTS (`noble`), amd64; Docker Engine (`docker-ce`) and `docker-compose-plugin` from Docker's apt repository, with the default containerd image store (at bring-up: 24.04.4, Engine 29.9.2, Compose v5.6.1) | same as the rest of the lab (0006, D3) |
| Size | 4 vCPU, 8 GB RAM, 250 GB thin disk on node-local NVMe | single-developer volume is tiny; disk is the only thing that grows |
| Provisioning | the owner's homelab repository: an OpenTofu role clones the VM from the lab's Ubuntu template; an Ansible role installs Docker, puts the admin user in the `docker` group, creates `/srv/telemetry` with the ownership in *Run locally on the Mac*, and caps container logs (`json-file`, 3 × 50 MB) | provisioning code stays out of this repository (0006, Non-goals); `compose.yaml` sets no logging limits and the data root shares `/` with the OS |
| Network | ports published on the VM's LAN IP only (DHCP reservation); no Tailscale on the VM: Tailnet clients reach it through the existing subnet router; no public exposure | same policy as the inference gateway in `ENDPOINTS.md`; one address avoids a Tailnet IP failing to bind before `tailscaled` starts (0006) |
| Storage | `/srv/telemetry/{archive,landing,collector-state,victoria,loki,tempo,captures}`, a directory on the root filesystem | archive can be an NFS mount from the NAS |
| Backup | Proxmox Backup Server backs up the VM daily, managed from the homelab repository, not here; a copy of `archive/` to the NAS (shown here as `nas.example`) is still planned | the archive and `captures/` are the irreplaceable parts |

## Services (compose.yaml)

| Service | Image | Port | Retention | Role |
|---|---|---|---|---|
| `otel-collector` | `otel/opentelemetry-collector-contrib` | 4317 gRPC, 4318 HTTP, 13133 health | n/a | the front door; redacts, archives, fans out |
| `victoriametrics` | `victoriametrics/victoria-metrics` | 8428 | `-retentionPeriod=5y` | metrics. Accepts OTLP natively at `/opentelemetry/v1/metrics`; Prometheus-compatible query API for Grafana |
| `loki` | `grafana/loki` | 3100 | 730 days via compactor | logs and events. Accepts OTLP natively at `/otlp` |
| `tempo` | `grafana/tempo` | 3200, 4317 internal | 90 days | traces. Optional; only Claude Code beta tracing and your own apps emit traces today |
| `captures-pull` | built from `captures/Dockerfile` | none | forever, for now | copies llama-swap's per-call request/response captures into `captures/<label>/` (spec 0008, *Local-model captures*) |

Grafana is **not** run here. Add three data sources to the existing Grafana on
the monitoring VM: VictoriaMetrics (Prometheus type), Loki, Tempo.

## Images

Every image in `compose.yaml`, and each base image `captures/Dockerfile` pulls,
is pinned as `tag@sha256:<index digest>`. An index
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
| `python` (base of `captures/Dockerfile`, 2026-10-22) | `3.12-slim-bookworm` | `sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70` | `linux/amd64`, `linux/arm64` | 10001 (the Dockerfile's `USER`; the base runs as root) |
| `ghcr.io/astral-sh/uv` (build stage of `captures/Dockerfile`, 2026-10-22; 0.11.x matches `uv_build>=0.11,<0.12`) | `0.11.33` | `sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7` | `linux/amd64`, `linux/arm64` | none at run time: only its `uv` binary is copied in |

To upgrade one, read the new index digest and its users with
`docker buildx imagetools inspect <image>:<tag>`. Then update every place
that pins it:
- `compose.yaml`;
- for the Collector, `inference-agent/compose.yaml` too (a test keeps the two
  image strings identical), then re-copy it to the inference host;
- this table;
- the `SPEC_INDEX_DIGESTS` constant in `tests/test_collector_compose.py` (for the
  harvester's bases, `captures/Dockerfile` and `DOCKERFILE_IMAGES` there);
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
     - On the Mac, `127.0.0.1`. The inference host agent sends to the VM, so
       nothing outside the Mac needs to reach this stack. While the Mac hosted
       the stack it bound `0.0.0.0` on the home network for that agent
       (spec 0007, D1); with `0.0.0.0` the stores and OTLP intake are open on any
       network the laptop joins, including VictoriaMetrics' unauthenticated admin
       API. `lsof -nP -iTCP:4318 -iTCP:8428 -sTCP:LISTEN` shows what is open: a
       `*:` address means all interfaces.
     - The VM uses its LAN address; the Tailnet reaches it through the subnet
       router (spec 0006).
   - `COMPOSE_PROFILES` and `CAPTURES_HOST_LABEL` (spec 0008): on the Mac
     test bed, delete both lines. With the `captures` profile on, `up -d`
     would start a second capture harvester against the same llama-swap. The
     VM keeps them (*Local-model captures* → Deploy).

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
     mkdir -p "$root"/{archive,landing,collector-state,victoria,loki,tempo,captures}
     ```

   - **Linux (the telemetry VM, spec 0006).** The Collector, Loki and Tempo run
     as UID 10001 and must own their folders. VictoriaMetrics runs as root.
     `landing/` belongs to the user who ships files, and UID 10001 reads it.
     Spec 0006 runs these steps for real:

     ```bash
     root=/path/from/TELEMETRY_ROOT
     sudo mkdir -p "$root"/{archive,landing,collector-state,victoria,loki,tempo,captures}
     sudo chown 10001:10001 "$root"/archive "$root"/collector-state "$root"/loki "$root"/tempo "$root"/captures
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
   - `TELEMETRY_OTLP_ENDPOINT`: the stack's OTLP/HTTP address, the telemetry
     VM's LAN address from `ENDPOINTS.md` (spec 0006; while the stack ran on the
     Mac, it was the Mac's LAN address). Use an address, not a `.local` name:
     the agent is a static Go binary that cannot resolve mDNS, even where the
     host can. If the stack's address changes, update this value and run
     `docker compose up -d`; the queue holds samples meanwhile.
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

## Local-model captures (spec 0008)

llama-swap already keeps a request/response capture of each call it proxies, but
only in a size-bounded in-memory cache that evicts the oldest first. The
`captures-pull` service copies every call into files before that happens, so a
local-model session can be read turn by turn as the model saw it: the system
prompt and tools the client sent, the history it re-sent, the sampling settings,
and the raw response. Facts below are from llama-swap v264 (MIT), read 2026-10-22
(spec 0008, *Step 0 results*).

**llama-swap settings** (an owner change on the inference host, through its
sysadmin agent; restart llama-swap while idle, since a restart unloads models):

- `captureBuffer: 1024` (MB). The harvester drains the cache every 15 s, so the
  buffer only has to cover a harvester outage (a VM reboot, a day of
  maintenance). Resize it from the measured `evicted` count.
- `store.path`: a file on persistent local disk. Without it the activity list
  keeps only the newest `metricsMaxInMemory` rows (default 1000), so a long
  outage would drop rows whose captures were still cached, and a restart resets
  row ids. With it, every row and its id survive a restart. The file grows
  without bound on the inference host; pruning it is decided with capture
  retention.

**What is and is not captured.** Every call through llama-swap's own port, for
every backend behind it. A client that calls a model server's own port bypasses
llama-swap and is not captured. A failed (non-200) call keeps everything but its
response body; a client disconnect or an empty response keeps no capture at all.
llama-swap itself replaces the values of `authorization`, `proxy-authorization`,
`cookie`, `set-cookie` and `x-api-key` with `[REDACTED]`; v264 has no setting to
turn that off. Nothing else is redacted: bodies, other headers and `src` (the
client address) are kept as served.

**Files.** One JSON line per call in `captures/<label>/<YYYY-MM-DD>.jsonl.zst`
under `TELEMETRY_ROOT`, named by the UTC date the call ended:
`{"activity", "capture", "capture_status", "harvested_at", "epoch"}` (plus
`estimated_end` on a malformed row), with the
activity row and the capture exactly as llama-swap served them (bodies stay
base64). Every 100 rows, and at the end of each poll, the harvester appends one
checksummed zstd frame per daily file and fsyncs it before
`captures/<label>/cursor.json` moves, so a crash repeats rows (readers drop them)
and never skips one. The Collector neither mounts nor tails these files, so they
never reach Loki; PBS's daily VM backup covers them. A failed append is
truncated back; a frame torn anyway (a hard kill, a copy taken mid-append) is
skipped by `captures show` with a warning naming the file.

**Counts.** Each poll that saved or lost anything logs
`poll: rows=N saved=S evicted=E no_capture=K id_reset=0|1`, plus `error=N` when a
capture failed (not with a 404) on 3 polls in a row and was saved without it
(`capture_status: "error"`), or an activity row would not parse on 3 polls and
was saved as served with an `estimated_end` from its neighbours. An
unreachable llama-swap never counts toward that. The 3 polls are counted within
one harvester process: a restart, or each `--once` run, starts the count over,
so a stuck capture or a malformed row never passes under `--once`. A quiet harvester logs a heartbeat every 40
polls. A poll writes and moves the cursor every 100 rows, so a backlog after an
outage never sits in memory whole. llama-swap lists a call a moment before it
stores the capture, so a recent clean call listed without one is fetched anyway. `evicted` is a call whose capture was listed but
gone by the time it was fetched (404). `no_capture` is a call llama-swap listed
without a capture: evicted before listing, a disconnect or empty body, or a
capture lost when llama-swap restarted (the cache is in memory even with
`store.path`). `id_reset=1` means row ids went backwards (the store was wiped,
moved, or llama-swap ran without `store.path`); the harvester opens a new epoch
instead of skipping rows. An unreachable llama-swap is logged and retried; the
service never exits on it. Log lines carry counts and API paths only.

**Reading captures.** Copy `captures/` to the Mac under the ignored `data/`, build
the session table, then:

```sh
rsync -a 192.0.2.10:/srv/telemetry/captures/ data/captures/
uv run agentic-telemetry session-table --out data/session_table.parquet
uv run agentic-telemetry captures show --captures data/captures \
  --session-table data/session_table.parquet --session SESSION_ID [--call N]
```

`RUN_ID` in place of `--session` shows a delegate run's root session;
`--date YYYY-MM-DD` lists every call of one UTC day with the sessions that claim
it, or `unclaimed`. It reads only the daily files from the day before the
selection to the day after, so its cost does not grow with retention; a session
header naming a session from further away is not seen. A call goes to the session named by its first non-empty
`x-session-id`, `x-session-affinity` or `x-client-request-id` request header when
that names a session; otherwise to every session whose time window (padded 5 s)
and model match, the innermost session of a parent and its subagent winning, and
flagged `ambiguous` when unrelated sessions overlap. A subagent keeps only
calls inside its own unpadded window; in the pad its parent keeps them. A
delegate run's session counts as a child of the session that orchestrated it
(the one session of the run's checkout, with no run id, running when the run
started). A header that names no session is flagged `header_unmatched`.
Without the header, a background subagent on its parent's model, and an
orchestrator calling while its delegate runs, lose their concurrent calls to
the child. Pi sends those headers only when a local
model's entry in Pi's model settings sets `compat.sendSessionAffinityHeaders:
true` (with `sessionAffinityFormat: "openrouter"` it sends `x-session-id`); that
is optional, and a client setting, not a scaffold change.

**Deploy** (the first time this stack runs code from this repository):

1. Apply the llama-swap settings above.
2. In the VM's `.env`, add `COMPOSE_PROFILES=captures` and
   `CAPTURES_HOST_LABEL` (a plain label such as `gpu`, never a hostname), and
   `LLAMA_SWAP_API_KEY` only if llama-swap has `apiKeys` set.
   `LLAMA_SWAP_TARGET` is already the llama-swap `host:port`. Leave the Mac
   test bed's `.env` without the profile: it never runs the harvester.
3. Copy `collector/compose.yaml`, `collector/env.example` and
   `collector/captures/Dockerfile`, and from the repository root
   `pyproject.toml`, `uv.lock`, `.dockerignore` and `src/`, so that
   `/opt/telemetry` mirrors the repository layout (`/opt/telemetry/collector/`,
   `/opt/telemetry/src/`, ...). The build context is the repository root, and
   `.dockerignore` narrows it to the package.
4. `sudo mkdir -p /srv/telemetry/captures && sudo chown 10001:10001 /srv/telemetry/captures`
   (before the first start: Docker would otherwise create it owned by root).
5. `docker compose build captures-pull && docker compose up -d captures-pull`,
   then `docker compose logs captures-pull`: the first poll backfills every row
   llama-swap still lists (old rows without a capture), then poll lines follow.

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
| Local-model requests and responses through llama-swap | the `captures-pull` service reads llama-swap's `/api/metrics/activity` and `/api/captures/{id}` every 15 s (spec 0008) | one record per call: the activity row and its capture, written to `captures/<label>/<YYYY-MM-DD>.jsonl.zst` | `captureBuffer` and `store.path` in the llama-swap config (*Local-model captures*). Files only: never tailed, never in Loki |

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
   older session's lines; revisit the labels then (0004, D8). Loki accepts lines
   as old as its 730-day retention (`reject_old_samples_max_age: 17520h`, spec
   0006; the 3.7.9 default is one week; Loki 3.7.x request-validation docs,
   AGPL-3.0, cited not copied, retrieved 2026-10-22:
   https://grafana.com/docs/loki/v3.7.x/operations/request-validation-rate-limits/),
   so a fresh VM's first ship cycle backfills every ledger line into Loki as well
   as the archive.
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
| Capture files (`captures/<label>/*.jsonl.zst`) | forever, for now | decided once a day's and a week's sizes are measured (spec 0008) |
| llama-swap's activity store (`store.path`, on the inference host) | forever, for now | grows without bound; pruning is decided with capture retention (spec 0008) |

Derived stats (per-session and per-run summary rows, the per-model failure matrix)
are computed by the Python package in this repo from the archive and the landing zone, and
written to a DuckDB/Parquet set that is itself backed up. Those rows are what you
look at in a year; the raw records are what you go back to when a number looks wrong.

## Bring-up order

1. Provision the VM (Ubuntu 24.04 LTS, amd64, sized as in *VM*) and install
   Docker Engine and the Compose plugin from Docker's apt repository. The owner
   does both from their homelab repository (*VM* → Provisioning); a hand-built VM
   works too. Give its LAN address a DHCP reservation. `/srv/telemetry` is a
   directory on the root filesystem.
2. Copy exactly `compose.yaml env.example otel-collector.yaml loki-config.yaml
   tempo.yaml` from the Mac's `collector/` with `scp` into the stack directory
   (`/opt/telemetry/collector` on the owner's VM; never the Mac's `.env`; no
   repository credentials on the VM). There, `cp env.example .env`, `chmod 0600
   .env`, and set the three variables, with `TELEMETRY_BIND_ADDR` = the VM's LAN
   address and `TELEMETRY_ROOT=/srv/telemetry`. Create the data root as in *Run
   locally on the Mac* (the Linux ownership steps) unless provisioning did; either
   way `stat -c '%u:%g %a %n' /srv/telemetry/*` shows `10001:10001` on `archive`,
   `collector-state`, `loki` and `tempo`. `docker compose up -d`. The capture
   harvester needs more files and its own directory; see *Local-model captures*
   → Deploy.
3. Check Collector health on the bind address, for example
   `curl -fsS http://192.0.2.10:13133`, replacing `192.0.2.10` with the
   `TELEMETRY_BIND_ADDR` value from `.env`. Validate `otel-collector.yaml` before the first
   start, as in its header comment.
4. Send a synthetic span with `telemetrygen` or point one Claude Code session at it with the client snippet; confirm a file appears under `archive/` and the metric shows in VictoriaMetrics' `/api/v1/query`.
5. Optionally, add the three data sources to Grafana on the monitoring VM
   (outside spec 0006).
6. Add the VM and ports to `ENDPOINTS.md` in the vault.
7. On each Mac, ship evidence to the VM and schedule it (spec 0006):
   1. Give the VM's address a `Host 192.0.2.10` stanza with your `User` in
      `~/.ssh/config`, then accept its host key once (`ssh 192.0.2.10 true`).
      `TELEMETRY_VM` must be the address, not an ssh alias: the script probes it
      with `nc`. The script runs ssh with `BatchMode=yes`, so
      `ssh -o BatchMode=yes 192.0.2.10 true` must succeed without a prompt.
   2. Run `mac-agent/ship-ledgers.sh` once by hand with `TELEMETRY_VM` and
      `PROJECTS_ROOT` set (`--help` lists its options); it prints one `shipped`
      line per project and client. Over ssh, project directory names must use
      only letters, digits, `.`, `_` and `-`; others are skipped (exit 1), since
      the VM's shell would split them.
   3. Copy `mac-agent/com.example.ship-ledgers.plist` to
      `~/Library/LaunchAgents/<label>.plist`, where `<label>` is your own
      reverse-DNS label replacing the template's `com.example.ship-ledgers`
      (the file is named after it). Replace its placeholder paths and VM address
      too (point the log paths at a directory that already exists, written as the
      absolute path of `~/Library/Logs/`: launchd neither expands `~` nor
      creates a missing directory, and the job would not start), then `plutil -lint` it, `launchctl bootstrap gui/$(id -u) <plist>` and
      `launchctl kickstart gui/$(id -u)/<label>`. After editing a
      loaded plist, `launchctl bootout gui/$(id -u)/<label>`
      first. If the log stays empty, `launchctl print
      gui/$(id -u)/<label>` shows the `last exit code`.
   4. Read the log file the plist names. A `no projects found` or `Operation
      not permitted` line means macOS privacy hid the projects folder from the
      job: grant Full Disk Access to `/bin/bash` (every launchd-run bash job then
      has it) and kickstart again (0006, D5).
8. Once the inference host agent sends to the VM, set the Mac's
   `TELEMETRY_BIND_ADDR` back to `127.0.0.1` and `docker compose stop` its stack;
   its data root stays as a test bed, started on `127.0.0.1` for config trials
   (0006, D6). `restart: unless-stopped` already keeps a stopped container down
   across restarts; `docker update --restart=no` on each container is a second
   guard, lost when a later `up` recreates them.

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

- Telemetry VM, spec 0006, repository changes on the Mac, 2026-10-22:
  - `loki-config.yaml` (`reject_old_samples_max_age: 17520h`) reports "config
    is valid" on the `linux/arm64` and `linux/amd64` entries of the 3.7.9 index
    (amd64 by its manifest digest, under emulation). `-print-config-stderr` shows
    `reject_old_samples_max_age: 2y`.
  - `mac-agent/com.example.ship-ledgers.plist`: `plutil -lint` OK.
  - `docker compose --env-file env.example config --quiet` passes after the
    comment-only change to `compose.yaml`.

- Telemetry VM bring-up, spec 0006 Phase B, 2026-10-22 (step numbers are the
  spec's runbook):
  - **VM (B1).** Ubuntu 24.04.4 LTS, amd64, 4 vCPU, 8 GB, a 246 GB root
    filesystem. Docker Engine 29.9.2 and Compose v5.6.1 from
    `download.docker.com` (`noble stable`), containerd image store. Before the
    stack, `ufw` allowed only 22/tcp and only sshd and systemd-resolved listened.
  - **Files and data root (B2).** The five files on the VM have the same SHA-256
    as the commit. Owners: `10001:10001` on `archive`, `collector-state`, `loki`
    and `tempo`; root on `victoria`; the admin user on `landing` (`0755`).
    `otelcol-contrib validate` (by digest, with the archive feature gate) and
    `docker compose config --quiet` pass on the VM.
  - **Start and images (B3, B4).** All four services run and log no
    `permission denied`. `docker compose images` on the VM shows the pinned index
    digests, the same as the Mac's `RepoDigests`.
  - **Smoke test and config (B5–B8).** Health returns 200. One telemetrygen trace,
    metric and log landed in Tempo, VictoriaMetrics and Loki, and once in each
    archive file. `up{service_name="llama-swap"}` is 1. Loki's running config
    shows `reject_old_samples: true` and `reject_old_samples_max_age: 2y`.
  - **Listeners and reboot (B9).** The six ports listen only on the LAN address,
    through `docker-proxy`, before and after a reboot. The services were back
    within about 20 s with the smoke data and the scrape intact.
  - **Shipping (B11–B16).**
    - With a `Host` stanza, `ssh -o BatchMode=yes` succeeds. A manual ship took
      22 s, exit 0: three projects and three clients.
    - The first launchd cycle exited 126 with `Operation not permitted`: the
      script sits in a privacy-protected folder. With Full Disk Access for
      `/bin/bash` (D5), the next cycle exited 0 with the same six lines.
    - The VM's `landing/<host>/` and a fresh `--dest` copy both hold 770 files;
      only the 2 transcripts still being written differ.
    - An unreachable address gave one `unreachable, skipped` line, exit 0, in
      5 s; the next cycle shipped the transcript written in between.
  - **File modes.** UID 10001 reads all 3 landed ledgers. The amended check
    still prints a `Permission denied` for `landing/<host>/_sessions/claude-code`:
    Claude Code keeps its transcript folders `0700`, the copy keeps that mode, and
    the Collector never reads transcripts. The check has no `-prune`, so `find`
    descends into `_sessions/` and reports its `0700` folders; read only the ledger
    lines of its output.
  - **Loki window (B17).** After `POST /flush`, Loki and the landing zone both
    hold 28 ledger lines, equal per project. No `loki_discarded_samples_total`
    series exists. One ledger line's `ts` equals its Loki timestamp.
  - **Inference agent (B18).** The host agent changed only
    `TELEMETRY_OTLP_ENDPOINT`. Sent points rose, the queue stayed at 0, and no
    export errors were logged. `up{service_name="inference-models"}` on the VM
    returns both `model_id`s (0 while the models are unloaded).
  - **Mac (B19).** `TELEMETRY_BIND_ADDR=127.0.0.1`; the stack is stopped with
    restart policy `no`, and nothing on the Mac listens on the six ports.
  - **Run join (B24).** A throwaway Flash Next handoff (test-first+implement,
    452 s, never pushed), joined against the VM: the served model resolved from
    the transcript, and the server's counters read 81,627 prompt and 16,889
    generated tokens, exactly the session's total. The status was `overlap`
    (`server_more`), not `joined`. The session's test-first subagent ran on the
    same model, Pi records the subagent's usage on its `subagent` tool result,
    and `run-table` sums only assistant messages, so the run's own totals were
    20,705 and 7,291 tokens short. A follow-up spec counts subagent usage.
    Spec 0009 now counts it, and the same run joins `joined` with `exact` tokens.

- **Spec 0008, phase A (2026-10-22, on the Mac; no host touched).**
  - `docker compose --env-file env.example config --quiet` passes with the
    `captures-pull` service.
  - `docker compose build captures-pull` builds on linux/arm64, and
    `docker buildx build --platform linux/amd64` builds the same Dockerfile;
    the amd64 image runs `captures show --help` under emulation. After review
    the image copies uv 0.11.33: it accepts the repository's `uv.lock` with
    `--locked`, builds the package with its own backend, and the build
    downloads only the hash-pinned `duckdb` and `zstandard`. The image runs as UID
    10001, and `/app` holds only `pyproject.toml`, `uv.lock` and `src/`.
  - Against a throwaway fake llama-swap on 127.0.0.1 serving three synthetic
    calls (one captured, one listed with a capture that answers 404, one
    without), `captures-pull --once` printed
    `poll: rows=3 saved=1 evicted=1 no_capture=1 id_reset=0`, wrote one daily
    file and `cursor.json` (`last_id` 3), and a second `--once` saved 0 rows.
    The built image, run with host networking, gave the same line, and refused
    an empty `--host-label` with a usage error.
  - With `COMPOSE_PROFILES=captures` (as in `env.example`), `config --services`
    lists `captures-pull`; with neither the profile nor the label set (the Mac
    test bed), `config --quiet` passes and the service is not listed.
  - Phase B (the llama-swap settings, the VM deploy, the TTL, restart and
    coverage checks, and a day's and a week's sizes) is the owner's, in spec
    0008's owner checklist.

Not yet done:
- B10, reachability over the Tailnet and from outside both networks (deferred);
- a NAS copy of `archive/` and Grafana data sources, outside spec 0006 (PBS
  backs up the VM daily, managed from the homelab repository);
- the Codex `[otel]` exporter syntax;
- spec 0008 phase B: llama-swap `captureBuffer` and `store.path`, the
  `captures-pull` deploy on the VM, and its checks on the inference host.
