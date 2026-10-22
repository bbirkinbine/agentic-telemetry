# 0007 — Host-Local Model Metrics Scraper

**Status:** shipped
**Last updated:** 2026-10-21
**Depends on:** 0003
**Roadmap unit:** 3 (with 0003, 0004, 0005, 0006)

## Goal

Get each inference server's per-model `llamacpp:*` metrics into VictoriaMetrics
without changing how llama-swap loads and unloads models. Spec 0003 found that the
only route from the telemetry stack to those metrics, llama-swap's
`/upstream/<model-id>/…`, resets a loaded model's idle timer on every request
(llama-swap v264, Standing decision D6 in 0003). A 30-second scrape through it
would keep the model loaded and hide the swap and load waits the analysis needs.
This spec puts a small scraper on the inference host itself. It reads each
upstream server's own port, labels every series with the llama-swap model ID, and
forwards the series to the telemetry stack. When a model is unloaded its port is
closed, the scrape fails quietly, and nothing loads.

## Success criteria

- A scraper runs on the inference host and reads each Prometheus-capable
  upstream on its own local port, never through llama-swap. It is an OTel
  Collector (contrib) in agent mode, run by Docker Compose with host networking
  and pinned by multi-arch digest like the stack's images (D2). Its tracked files
  live in `collector/inference-agent/`: a compose file, the agent config, a
  value-free `env.example` and a placeholder targets example.
- The scraper's targets come from one untracked file_sd file on the inference
  host (D3). Each entry gives a port, its `model_id` and its metrics path.
  Adding or retiring a model edits only that file; the running agent picks the
  change up without a restart.
- Every forwarded series carries `model_id=<llama-swap model ID>`.
- The scraper sends OTLP to the stack's Collector, never remote-write straight
  to VictoriaMetrics. That keeps the stored names 0003 recorded (the colon
  becomes an underscore: `llamacpp_prompt_tokens_total`), and every sample also
  lands in the archive. Its sending queue is disk-backed and retries without a
  time limit, so samples taken while the Mac sleeps or is away are delivered
  when it returns.
- While the stack runs on the Mac, the Mac's untracked `collector/.env` binds the
  stack to an address the inference host can reach (D1). `env.example` and the
  collector README say when to use loopback and when to use a LAN address.
- With a model loaded, VictoriaMetrics holds that model's `llamacpp_*` series
  labelled with its `model_id`. This criterion moved here from 0003.
- With the model idle, llama-swap unloads it after its `ttl` as before; the
  sysadmin agent checks this once. The scraper's own `up` for that model drops to
  0 and nothing reloads it.
- llama-swap's access log shows no scraper requests.
- Builds without a Prometheus route are recorded as uncovered, not worked around:
  Project Maya before v1.1.5 (v1.1.4 is deployed, and its `/metrics` is JSON),
  and the Sybil SGLang/exllamav3 build.
- Tracked config carries placeholders only. Host addresses, ports and the
  port-to-model mapping live in untracked files on each host and in the vault's
  `ENDPOINTS.md`.
- The agent config is validated against its pinned image, recorded in
  `collector/README.md` → Verification, and added to the `AGENTS.md` don't-touch
  list next to the other Collector configs.

## Non-goals

- Exporters or adapters for builds without Prometheus metrics (Sybil), and
  upgrading Project Maya.
- `/slots` polling and llama-swap request-log capture.
- Changing llama-swap's TTL or residency policy.
- The run-table join (0005).
- Moving the stack to the VM (0006). The agent's target address is one env value,
  so 0006 changes it without a config edit.

## Standing decisions

Answered by the owner on 2026-10-21, while reviewing this draft:

- **D1, reaching the Mac stack.** The inference host forwards to the stack on
  the Mac, so the Mac's `TELEMETRY_BIND_ADDR` becomes a LAN address the host can
  reach instead of `127.0.0.1`. This matches the owner's decision to trust the
  local network (vault `ENDPOINTS.md`, 2026-10-21). The change is to the
  untracked `.env` only; compose already reads the variable. The alternatives,
  deploying the VM first or publishing only the OTLP port, were declined.
  Amended by the owner on 2026-10-21, after the plan's `[ask-user]` on address
  stability: on the home network everything listens on all interfaces, so the
  Mac's `TELEMETRY_BIND_ADDR` is `0.0.0.0`. No DHCP reservation and no fixed
  Wi-Fi address are needed for the stack to start. This replaces 0003 D2's
  `127.0.0.1` default for the Mac while 0007's agent forwards to it. The cost
  is that on any other network the laptop joins, the stores and OTLP intake are
  open too. The README says to stop the stack or switch back to `127.0.0.1`
  away from home. The inference host's agent still needs an address for the
  Mac: the Mac's current LAN address, which is updated when it changes (D6
  replaced an mDNS `.local` option).
- **D2, runtime.** Docker Compose on the inference host, `network_mode: host`, so
  the agent reaches upstream ports bound to loopback as well as those on all
  interfaces. The image is the Collector contrib image the stack pins, by index
  digest, so the inference host pulls its own architecture. A systemd unit
  running the release binary was declined.
- **D3, targets.** One untracked file_sd JSON on the inference host maps each
  upstream port to its `model_id` and sets its metrics path through the
  `__metrics_path__` label (Strata serves `/metrics/prometheus`, llama.cpp
  `/metrics`). One job per server with env-var ports in tracked config was
  declined, because a new server would need a tracked edit and re-validation.
- **D4, Compose on the inference host** (owner, 2026-10-21, after the host
  report). The inference host has Docker Engine but no Compose plugin. Its
  sysadmin agent installs the Compose v2 plugin from the same package source as
  the engine, and the agent runs from the tracked compose file, the same way the
  stack runs. Tracking a plain `docker run` script was declined.
- **D5, llama.cpp metrics** (owner, 2026-10-21). The owner approves the
  sysadmin agent adding `--metrics` to the llama.cpp container's command. It
  takes effect at the next model load. Coverage at ship time is then Strata and
  llama.cpp; Maya (v1.1.4) and Sybil are recorded as uncovered.
- **D6, review adjudication** (owner, 2026-10-21, answering the `[ask-user]`
  items from `/review` and `/review-adversarial`):
  - **Mac address for the agent.** `TELEMETRY_OTLP_ENDPOINT` uses the Mac's
    current LAN address. The `.local` option is dropped: the Collector is a
    static Go binary whose resolver does not do mDNS, so a check on the host
    could pass while the agent fails with "no such host". A stale address shows
    in the README's delivery check (step 6), and the queue holds samples until
    it is fixed.
  - **Upstream guard in config.** The scrape job drops any target whose
    `__metrics_path__` matches `.*/upstream/.*`. A hand-edited `targets.json`
    then cannot reset a model's TTL (0003 D6). It is the plan's one relabel
    rule. Future servers with other paths still need no tracked edit.
  - **Away from home with `0.0.0.0`.** Accepted as decided in D1. The README
    adds a one-line `lsof` check for what is listening on all interfaces.
  - **Stack compose header.** The comment in `collector/compose.yaml` that said
    the Mac binds `127.0.0.1` is corrected in this change (comment only), and
    compose config re-checked.

## Notes

- **Out-of-repo actions need the owner.** The scraper runs on the inference host
  and is installed through that host's sysadmin agent. This repo holds its config
  template and README steps.
- **Host facts** (inference host's sysadmin agent, 2026-10-21, read-only):
  - Docker Engine 29.1.3 on x86_64 (amd64); no Compose plugin (see D4). Host
    networking shares the host's namespace, so the container's `127.0.0.1`
    reaches loopback-bound upstreams.
  - Project Maya is v1.1.4. Its `/metrics` returns JSON, not Prometheus text,
    so Maya stays uncovered until v1.1.5 or later.
  - The llama.cpp container (build b11552) runs without `--metrics`, so it serves
    no Prometheus metrics yet (see D5). That build enables the slots endpoint by
    default, without `--slots`.
  - A request straight to an upstream's own port does not reset llama-swap's
    900-second TTL. llama-swap updates `lastUse` only for requests it proxies.
    The agent confirmed this from the deployed v264 code and config, without
    loading a model.
  - An unloaded upstream's port refuses the connection (curl exit 7, no body).
  - The queue directory is `/var/lib/agentic-telemetry/queue` on the root
    filesystem, which has ample free space. The installer creates it and gives
    it to UID 10001. The models filesystem is nearly full and is not used.
  - The host firewall is inactive and outbound traffic is allowed. Nothing
    local blocks TCP 4318 out, and nothing on the LAN listens there yet (D1).
- **Mac address stability.** Binding `0.0.0.0` (D1) means an address change never
  stops the stack from starting. Only the agent's target can go stale, and the
  disk queue holds samples until it is corrected. The macOS firewall may need
  to allow incoming connections to Docker.
- **Upstream ports and identity** (inference host's sysadmin agent,
  2026-10-21). Each model has its own fixed port, so the port identifies the
  model. No model uses llama-swap's `${PORT}` macro. The port numbers, and which
  ports are bound to localhost versus all interfaces, are in the vault's
  `ENDPOINTS.md`, not in tracked files (owner decision from the 0003 re-review).

  - While a model is unloaded, its port has no listener.
  - llama-swap's `GET /running` and `GET /v1/models` do not reset any model's
    TTL. They are gateway handlers and never reach a model process.
  - A loaded server reports its own identity:
    - Strata, llama.cpp and Maya: `GET /props` gives `model_alias`, and
      `GET /v1/models` gives `data[].id`;
    - Sybil: `GET /v1/models` gives `data[].id`, and it has no `/props`.

  So the targets file can label each port with a fixed `model_id`, because the
  port identifies the model.
- **Metrics paths.** Strata serves Prometheus text at `/metrics/prometheus` with
  no flag. llama.cpp (`qwen3.8-27b-ud-q8_k_xl`) serves `/metrics` once `--metrics`
  is set (D5).
- **`$` in the agent config.** The Collector's Prometheus receiver reads `$` as an
  environment variable, so a literal `$` is written `$$`.
- **0005 depends on this spec.**

## External references

- Prometheus, "Configuration" (`file_sd_config`; the `__metrics_path__` and
  `__scheme__` target labels):
  https://prometheus.io/docs/prometheus/latest/configuration/configuration/ ,
  retrieved 2026-10-21, license: Apache-2.0 (docs). Quoted there: file_sd
  changes "are detected via disk watches and applied immediately", with
  `refresh_interval` (default 5m) as the fallback; `__scheme__` and
  `__metrics_path__` are "set to the scheme and metrics path of the target",
  and setting them per target overrides the scrape configuration.
- OpenTelemetry Collector contrib, Prometheus receiver README:
  https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/receiver/prometheusreceiver/README.md ,
  retrieved 2026-10-21, license: Apache-2.0. Quoted there: "Service discovery
  and relabeling behave like Prometheus", and `$` must be escaped as `$$`. The
  README does not name `file_sd_configs` explicitly, so the plan validates a
  file_sd job against the pinned image before relying on it.
- llama.cpp server, `tools/server/server-context.cpp` at tag `b11552` (the
  deployed build), commit `23b0202a189c44a54625aadcb37a946dd1d6278d`:
  https://github.com/ggml-org/llama.cpp/blob/b11552/tools/server/server-context.cpp ,
  retrieved 2026-10-21, license: MIT. The `/metrics` handler posts a metrics task
  with `metrics_reset_bucket = true`, commented "the gauges are averaged over
  the window between two scrapes". This backs the README's one-scraper rule and
  0005's advice to compute throughput from counters.

## Approved implementation plan

**Approved:** 2026-10-21

This is the planner's plan, copied verbatim. Headings are demoted so they nest
under this section, and repo-absolute paths are shortened to repo-relative ones.
The owner approved it with these amendments, which take precedence over the
plan text they name:

- **First `[ask-user]`, Mac address stability.** Answered by the D1 amendment:
  the Mac binds `0.0.0.0` on the home network, so there is no DHCP reservation
  and no Private Wi-Fi Address step. As a result:
  - the `collector/env.example` comment says `0.0.0.0` on the home network and
    `127.0.0.1` elsewhere, not "never `0.0.0.0`". The example value stays
    `127.0.0.1`;
  - the README's Run-locally step 1 drops the reservation guidance and says to
    stop the stack or switch back to `127.0.0.1` away from home. The step 3 and
    4 curls keep `127.0.0.1`, which `0.0.0.0` includes, so the `addr=`
    rewrite is dropped;
  - step 7 becomes: the owner sets `TELEMETRY_BIND_ADDR=0.0.0.0`, runs
    `docker compose up -d`, and allows Docker in the macOS firewall;
  - in step 9c, the agent's `TELEMETRY_OTLP_ENDPOINT` uses the Mac's current
    LAN address (D6 dropped an mDNS `.local` option that was here);
  - the Risks bullet on this question is superseded.
- **Second `[ask-user]`, how the files reach the inference host.** The owner
  copies the tracked files from the Mac to the inference host. The sysadmin
  agent installs them from there. No clone of the repo on the inference host.
  The README states the copy step, and that a config change needs a re-copy.
- **Review adjudication (D6), 2026-10-21.** It supersedes these plan lines:
  - "No `static_configs`, no `metrics_path` override, no relabel rules, so no
    `$$` is needed": the job now has one relabel rule, which drops
    `/upstream/` metrics paths. It needs no `$$` either;
  - the upstream-route guard ("no parsed string ... contains `upstream/`"):
    the test now skips `action: drop` rules, and a new test pins that drop rule;
  - Out of scope, "`collector/compose.yaml` ... no edits": its header comment
    is corrected (comment only), and compose config was re-checked;
  - step 9c's "the three tracked files": four files are copied, including
    `targets.example.json` as the template for `targets.json`.

### Plan: 0007 Host-Local Model Metrics Scraper

Spec: `docs/specs/0007-host-local-model-metrics.md` (D1–D5). Nothing changes in `src/`. There are no schema changes, migrations, dependency changes or version bumps. The agent reuses the stack's Collector image, `otel/opentelemetry-collector-contrib:0.163.1@sha256:1175b03003053962012f5a231f5567f66a3e8fdb960c7835c0e48f7aa1555806`.

#### Files to touch

**New, tracked, under `collector/inference-agent/`**
- `compose.yaml`: one service, `otel-agent`.
  - `image:` is byte-identical to the stack's `otel-collector` image. `restart: unless-stopped`. `network_mode: host` (D2). No `ports:`.
  - `environment:` sets `TELEMETRY_OTLP_ENDPOINT: ${TELEMETRY_OTLP_ENDPOINT:?set TELEMETRY_OTLP_ENDPOINT in collector/inference-agent/.env}`. Keep colons out of the `:?` messages.
  - Volumes:
    - `./otel-agent.yaml:/etc/otelcol/config.yaml:ro`
    - `${AGENT_TARGETS_DIR:?...}:/etc/otelcol/targets:ro`. Mount the **directory**, not the file. A single-file bind mount pins the inode, so an editor's atomic rename would never reach the container.
    - `${AGENT_QUEUE_DIR:?...}:/var/lib/otelcol/queue`
  - The header comment says the inference host pulls amd64 from the same index digest, and gives the validate command.
- `otel-agent.yaml` (agent config):
  - `receivers.prometheus/models`: one job, `inference-models`, `scrape_interval: 30s` (the same as the stack). `file_sd_configs: [{files: [/etc/otelcol/targets/*.json], refresh_interval: 30s}]`. No `static_configs`, no `metrics_path` override, no relabel rules, so no `$$` is needed.
  - `processors`: `memory_limiter` (small) and `batch`.
  - `extensions`:
    - `file_storage` at `/var/lib/otelcol/queue`, with `create_directory: true` and compaction (`on_start` and `on_rebound`, `directory` set to the same path). Compaction gives disk back after a long drain.
    - `health_check` on `127.0.0.1:13133`. Loopback only, because with host networking `0.0.0.0` would publish it on the LAN.
  - `exporters.otlphttp/stack`:
    - `endpoint: ${env:TELEMETRY_OTLP_ENDPOINT}`;
    - `sending_queue: {enabled: true, storage: file_storage, queue_size: 100000}`. At about 4 requests a minute, that covers roughly two weeks away;
    - `retry_on_failure: {enabled: true, initial_interval: 10s, max_interval: 1m, max_elapsed_time: 0}`.
    - No `prometheusremotewrite` exporter.
  - `service.telemetry.metrics` pulls from `127.0.0.1:8888`. Set it explicitly: the default port could collide on a host-networked box. The syntax is checked against the 0.163 internal-telemetry docs in-session.
  - One pipeline, `metrics`.
  - The header says why the agent never goes through llama-swap's upstream route. It cites 0003 D6 and does not spell out the literal path.
- `env.example` (value-free; no leading dot, as in 0003 D1):
  - `TELEMETRY_OTLP_ENDPOINT=http://telemetry.example:4318`. The comment says that while the stack is on the Mac this is the Mac's reserved LAN address, for example `http://192.0.2.20:4318`, and that 0006 repoints it to the VM.
  - `AGENT_QUEUE_DIR=/var/lib/agentic-telemetry/queue`. The comment says to chown it to 10001.
  - `AGENT_TARGETS_DIR=/etc/agentic-telemetry/targets`.
- `targets.example.json`: two entries.
  - Each has `"targets": ["127.0.0.1:6500N"]` and the labels `model_id` and `__metrics_path__`:
    - `qwen3.8-flash-next-unsloth-q8_0` with `/metrics/prometheus` (Strata);
    - `qwen3.8-27b-ud-q8_k_xl` with `/metrics` (llama.cpp).
  - Both model IDs already appear in the tracked `collector/README.md`.
  - JSON cannot carry comments, so the README says the ports are placeholders (65001 and 65002) and that the real ones are in `ENDPOINTS.md`.

**Edited**
- `collector/env.example`: rewrite only the `TELEMETRY_BIND_ADDR` comment, and keep the value `127.0.0.1` (an existing test needs loopback or RFC 5737):
  - use `127.0.0.1` when nothing off the Mac sends to the stack;
  - use the Mac's DHCP-reserved LAN address while the inference agent forwards to it, for example `192.0.2.20`;
  - never `0.0.0.0` (0003 D2).
- `collector/README.md`:
  - status line, diagram (add the agent), "Not yet done", and the "How each source gets in" row;
  - *Inference server endpoints*: replace "Today the stack does not scrape these servers" with the agent, and add a **Coverage** list. Covered: Strata and llama.cpp (with `--metrics`, D5). Uncovered: Project Maya v1.1.4 (JSON `/metrics`) and Sybil (no Prometheus route);
  - *Run locally on the Mac*:
    - the step 1 `TELEMETRY_BIND_ADDR` bullet says when to use loopback and when the reserved LAN address. It covers the macOS firewall allowing Docker, and macOS "Private Wi-Fi Address" set to Fixed (or Off) for the home network, so the DHCP reservation holds;
    - the step 3 and 4 curls stop hard-coding `127.0.0.1` and use an `addr=` variable. Today they break as soon as D1 is applied;
  - new section **Inference host agent (spec 0007)**:
    - prerequisites: the Compose v2 plugin (D4), and `--metrics` on llama.cpp (D5);
    - installing the files: copy the three tracked files to the host;
    - `.env`, the queue dir (`mkdir`, then `chown 10001:10001`), the targets dir and file format (port, `model_id`, `__metrics_path__`);
    - editing targets with no restart;
    - `docker compose up -d`;
    - a loopback health check;
    - the checks listed under Verification below;
  - *Images*: the upgrade list goes from four places to five, adding `collector/inference-agent/compose.yaml`;
  - *Verification*: dated entries for the validator runs, experiments E1 and E2, and the live checks.
- `.gitignore`: add `collector/inference-agent/targets*.json` and `!collector/inference-agent/targets.example.json`. This is a backstop in case a real targets file is dropped next to the template. `.env` is already ignored everywhere.
- `AGENTS.md`: add `collector/inference-agent/otel-agent.yaml` to the Don't-touch line, and update the current-state block at closeout.
- `docs/specs/0006-telemetry-vm-deploy.md`: add one criterion: repoint the agent's `TELEMETRY_OTLP_ENDPOINT` to the VM, and return the Mac `.env` to `127.0.0.1`. 0007's non-goal already hands 0006 this job; this edit only makes it explicit.
- `docs/specs/0005-server-metrics-join.md`: add a one-line note that `model_id` comes from the agent's targets file. No change to criteria.
- `docs/specs/0000-product.md`: no change expected. Recheck the line-97 wording at closeout.
- Tests, all under `tests/`:
  - `tests/collector_files.py` (+~25 lines):
    - constants for the four agent files;
    - `parse_env_example(path=ENV_EXAMPLE)`, made path-generic;
    - the four agent files appended to `HYGIENE_FILES`.
  - New `tests/test_inference_agent_compose.py`, `tests/test_inference_agent_config.py`, `tests/test_inference_agent_targets.py` and `tests/test_inference_agent_docs.py`.
  - `tests/test_collector_compose.py` (278 lines) is not grown.
- Spec 0007 bookkeeping (orchestrator):
  - the approved plan;
  - D1's open assumption resolved as a recorded decision;
  - `shipping`, then `shipped`;
  - regenerate `docs/specs/README.md`.

#### Test-first coverage (static, no Docker, no network)
- **Compose (D2, parity):**
  - exactly one service;
  - its `image` equals `compose.yaml`'s `otel-collector` image (the digest-match guard);
  - `network_mode: host` and no `ports:`;
  - every bind source is `./` or a required `${X:?`;
  - every `${X}` is declared in the agent `env.example`;
  - every `${env:X}` in `otel-agent.yaml` is passed in `environment`.
- **env.example:**
  - the endpoint host is `*.example` or RFC 5737;
  - the queue and targets dirs are not home paths;
  - `AGENT_QUEUE_DIR == /var/lib/agentic-telemetry/queue`.
- **Config:**
  - every prometheus receiver uses only `file_sd_configs` (no `static_configs`), and its files glob sits under the targets mount's container path;
  - the only exporter is otlphttp, and its endpoint is exactly `${env:TELEMETRY_OTLP_ENDPOINT}`;
  - `sending_queue.enabled` is set and its `storage` names a `file_storage` extension, whose `directory` equals the queue mount's container path;
  - `retry_on_failure.max_elapsed_time == 0`;
  - the metrics pipeline only;
  - no `0.0.0.0` or `::` in any parsed string;
  - **the upstream-route guard:** no parsed string in `otel-agent.yaml`, `compose.yaml` or `targets.example.json` contains `upstream/`. Comments are excluded, as in 0003's test.
- **Targets example:**
  - a list of objects, each with exactly one target matching `127.0.0.1:<port>`, with the port in 65000–65535;
  - labels `model_id` and `__metrics_path__` are present, and the path is in {`/metrics`, `/metrics/prometheus`};
  - ports and `model_id`s are each distinct;
  - both paths appear.
- **Docs:**
  - the README agent section has `docker compose up -d`, `10001`, `/var/lib/agentic-telemetry/queue`, `--metrics` and `ENDPOINTS.md`;
  - the Coverage text names Project Maya and Sybil as uncovered;
  - the README says the example ports are placeholders;
  - the Run-locally section mentions both loopback and LAN for `TELEMETRY_BIND_ADDR`;
  - the README upgrade list names `inference-agent/compose.yaml`;
  - `AGENTS.md`'s Don't-touch section names `collector/inference-agent/otel-agent.yaml`.
- **Hygiene:** the existing parametrized tests now also cover the four new files.

Expected red is an `AssertionError` from `read_text` on the missing files, plus the docs, gitignore and Don't-touch assertions.

#### Order of operations
1. The orchestrator persists this plan verbatim and marks the spec `shipping`.
2. Test-first (delegate). It writes only under `tests/`. Audit its fingerprints, then rerun `.agentic/toolchain.sh test tests/test_inference_agent_*.py` and confirm the reds are `AssertionError`s.
3. Implement the four `collector/inference-agent/` files, the `.gitignore` lines and the `env.example` comment.
4. **Validate on the Mac.** Pass `-e TELEMETRY_OTLP_ENDPOINT=http://telemetry.example:4318`.
   - `otelcol-contrib validate --config=/c.yaml` on the arm64 index digest.
   - The same on the **amd64 manifest digest**: find it with `docker buildx imagetools inspect <index> --raw`, run it under emulation, and confirm `amd64` with `docker inspect`. Do not use `--platform linux/amd64` on the index digest; 0003 hit "cannot overwrite digest".
   - `docker compose -f collector/inference-agent/compose.yaml --env-file collector/inference-agent/env.example config --quiet`.
5. **E1, the `file_sd` and `__metrics_path__` proof**, on the Mac against the pinned image. All throwaway files go in the scratchpad, never the repo.
   - Fake upstreams: a scratchpad Python script serves two local ports. Port A serves Prometheus text (`Content-Type: text/plain; version=0.0.4`) only at `/metrics/prometheus`, with `exp_a_total 1`, and returns 404 on `/metrics`. Port B serves `/metrics` only, with `exp_b_total 1`.
   - Targets file (scratchpad) with three entries:
     - A, with `__metrics_path__=/metrics/prometheus` and `model_id=exp-a`;
     - B, with no path label (the default `/metrics`) and `model_id=exp-b`;
     - a closed port, with `model_id=exp-closed`.
     - All use `host.docker.internal:<port>`.
   - Run the **tracked** `otel-agent.yaml` with `docker run --network collector_default`, the targets dir mounted read-only, a scratchpad queue dir, and `TELEMETRY_OTLP_ENDPOINT=http://otel-collector:4318`.
   - Pass when VictoriaMetrics (query with `nocache=1`) holds all of:
     - `exp_a_total{model_id="exp-a"}` and `exp_b_total{model_id="exp-b"}`;
     - `up` = 1 for A and B, and `up` = 0 for `exp-closed`;
     - no `__metrics_path__` label;
     - in the agent log, a quiet scrape failure for the closed port and no crash.
   - Hot reload: add a fourth entry, once by editing in place and once by atomic rename (`mv`). Confirm each is scraped within `refresh_interval` with no restart. If the Docker Desktop bind mount drops inotify events, the 30 s refresh still picks the change up. Record which path fired.
   - **If any check fails, stop with `[DECISION NEEDED]`.** The fallback is two tracked jobs, one per `metrics_path`, each with its own file_sd file. That changes D3.
6. **E2, the disk queue**, with E1's agent still running.
   - `docker compose stop otel-collector` (the stack's) for about 5 minutes. In that window, restart the agent container once and confirm the queue dir is non-empty.
   - Start the stack Collector again. Confirm the outage window is backfilled: `count_over_time(exp_a_total[10m])` with `nocache=1`, which avoids VictoriaMetrics' rollup cache.
   - Then remove the experiment containers.
7. **D1 on the Mac (owner).**
   - Create the DHCP reservation and set Private Wi-Fi Address to Fixed (or Off).
   - Set `TELEMETRY_BIND_ADDR` in `collector/.env` to the reserved address, then `docker compose up -d`. Allow Docker in the macOS firewall.
   - The orchestrator confirms health on that address. It reads the address from the owner or from `docker compose ps`, and never reads `.env`.
8. Docs: the README sections, the AGENTS.md Don't-touch line, and the 0006 and 0005 notes.
9. **Inference host (sysadmin agent; the orchestrator drafts each prompt, the owner relays it).**
   - (a) Install the Compose v2 plugin from the engine's package source and show `docker compose version`. Confirm `127.0.0.1:13133` and `:8888` are free (`ss -ltn`).
   - (b) Add `--metrics` to the llama.cpp container (D5).
   - (c) Copy the three tracked files to an install dir. Create the queue dir: `mkdir`, `chown 10001:10001`, mode 0700. Create the targets dir (0755) and `targets.json` (0644) from `ENDPOINTS.md`, for Strata and llama.cpp only. Write `.env` with the Mac's reserved address.
   - (d) `curl` the Mac's health port, then `docker compose up -d`. Confirm the container's architecture is `amd64`, its image digest matches, and `ss -ltnp` shows the agent listening on loopback only.
10. **Live checks** (spec criteria; record names and label keys only, never values):
    - **Loaded model.** The owner loads a model with a normal request. VictoriaMetrics shows `count by (__name__, model_id)({__name__=~"llamacpp_.*"})` with that model's `model_id`, and `up{service_name="inference-models"}` = 1 for it.
    - **Idle (sysadmin, once).** After `ttl` 900 s, llama-swap `GET /running` shows the model unloaded; agent `up` = 0 for it; no reload event follows for about 30 minutes.
    - **Access log (sysadmin).** llama-swap's log shows no requests from the agent: none from loopback, and none on any upstream path.
    - Optional: let the Mac sleep for 10 minutes or more with a model loaded, then confirm the backfill.
11. Closeout:
    - mark the spec `shipped`; regenerate the dashboard; update the AGENTS.md current-state block;
    - run `.agentic/hooks/closeout-check.sh` and `.agentic/toolchain.sh gate`;
    - `/review-check`, `/review`, and `/review-adversarial` (network exposure and an install on a remote host);
    - stop for commit approval.

#### Risks / open questions
- **[ask-user] Mac address stability, with a recommendation.** Use a DHCP reservation and bind that fixed address. Do not use `0.0.0.0`: 0003 D2 rejected publishing unauthenticated OTLP and the stores on every network the laptop joins.
  - The cost: away from home the address does not exist, so the stack fails to bind (fail-closed). The owner then switches `.env` to `127.0.0.1` or stops the stack, and the agent queues in the meantime.
  - Owner to confirm: the reservation exists for the interface in use (Wi-Fi and Ethernet have different MACs), and Private Wi-Fi Address is Fixed or Off for that network.
  - This does not block steps 1–6.
- **[ask-user] How the files reach the inference host.** The repo is private, and a clone needs credentials there. Recommendation: the sysadmin agent copies the three tracked files to the host. Config edits then need a re-copy, and the README says so. Not blocking.
- **llama.cpp `/metrics` may reset its rate buckets on each scrape.** The `*_tokens_seconds` gauges may be averages since the previous scrape. If so, a second scraper (such as an ad-hoc capture) skews the agent's readings. Check the upstream server source (MIT) during implementation and note the result for 0005. One scraper per upstream.
- **Host-network port collisions.** The agent binds `127.0.0.1:13133` and `:8888`. Step 9a checks they are free; a clash makes the agent fail at start, which is visible rather than silent.
- **Queue cap.** 100000 requests, roughly 1–2 GB worst case on the root filesystem. Once it is full, new samples are dropped. Recheck `queue_size` and `sizer` against the 0.163 exporterhelper docs; `validate` catches a bad key.
- **VictoriaMetrics rollup cache.** It can hide backfilled samples in recent range queries. Verification queries use `nocache=1`.
- **Experiment data lands in the Mac stores.** `exp_*` series go into the Mac stack's stores and archive. That data is throwaway (0006 starts empty); say so in Verification.
- **Content-type strictness.** The Prometheus 3.x scraper inside 0.163 may reject a response without a Prometheus content type. E1's fake sets one, and the real builds send `text/plain`. If Strata does not, `fallback_scrape_protocol` is the fix and needs re-validation.
- **E1 is the gate.** If `__metrics_path__` is not honored, the plan stops at step 5.

#### Out of scope (won't touch)
- `collector/compose.yaml` and `collector/otel-collector.yaml`: no edits, so no re-validation of the stack.
- `collector/mac-agent/*`, the live OTLP path from the Mac, and client OTel settings.
- Maya and Sybil exporters, a Maya upgrade, `/slots` polling, llama-swap log capture, and TTL or residency changes.
- The 0005 join and anything under `src/`. Moving to the VM and its bind-address question (0006).
- Collector auth, and a local file archive on the agent.
- `ENDPOINTS.md`, the host's `.env` and `targets.json`, and the Mac's `.env`: owner and sysadmin only; the agent never reads them.

## Phase handoff

### Test-first (2026-10-21)

- **Files.** `tests/collector_files.py` (agent path constants, path-generic
  `parse_env_example`, compose helpers; the four agent files added to the
  hygiene list), plus new `tests/test_inference_agent_compose.py`,
  `tests/test_inference_agent_config.py`, `tests/test_inference_agent_targets.py`
  and `tests/test_inference_agent_docs.py`.
- **Scope audit.** Clean: against the pre-phase fingerprints only those five
  paths changed, and the index did not.
- **Red, re-run by the orchestrator:** 59 failed and 365 passed. All 59 are
  `AssertionError`s: missing agent files (46, including 12 hygiene cases) and
  missing README, `env.example`, `AGENTS.md` and `.gitignore` text (13). There
  were no collection errors.
- **Rejected-value search.** Terms: `four places`, `does not scrape`, `0.0.0.0`,
  `reserv`, `inference-agent`. No existing test pinned a rejected value.
  Kept on purpose: the stack compose test that forbids a hard-coded all-interfaces
  port (the `0.0.0.0` value lives only in the untracked `.env`), the hygiene
  allowance for `0.0.0.0`, and the `env.example` test that keeps the example
  value `127.0.0.1`.

### Implement, verify and review (2026-10-21)

- **Done on the Mac.** Plan steps 1–6 and 8:
  - the four agent files and the `.gitignore` lines;
  - validation on arm64 and amd64;
  - E1 (file_sd, per-target `__metrics_path__`, hot reload both ways) and
    E2 (no gaps across a 5-minute outage with an agent restart);
  - docs, and the 0005, 0006 and `docs/agent-handoff.md` notes.
- **Additions beyond the plan:** a json-file log cap in the agent compose, and
  the llama.cpp bucket-reset note (sourced at tag `b11552`).
- **Reviews.**
  - `/review` and `/review-adversarial` raised `[auto-fix]` items, all applied,
    and five `[ask-user]` items, answered by the owner as D6.
  - A focused re-review verified all 15 resolutions. Its last three mechanical
    items are applied: the copy step's directory, `ssh -t`, and the
    plan-amendment bullet for D6.
- **Gate:** green, 426 passed.
- **Remaining, owner and inference host:**
  - step 7: the Mac's `.env` gets `TELEMETRY_BIND_ADDR=0.0.0.0`, and the macOS
    firewall allows Docker;
  - step 9: the sysadmin agent installs the Compose plugin, adds `--metrics`,
    and installs the agent from the four copied files;
  - step 10: the live checks (a loaded model, an idle unload, the access log).
- **Then closeout (step 11):** record the live results in Verification, mark
  the spec `shipped`, regenerate the dashboard, update the `AGENTS.md`
  current-state block, run the closeout check and a final focused review, and
  stop for commit approval.

### Live checks and closeout (2026-10-21)

- **Step 7 (Mac).** The owner set `TELEMETRY_BIND_ADDR=0.0.0.0`, because the
  agent policy blocked editing `.env`. All six stack ports listen on all
  interfaces, and the macOS firewall allows OrbStack. The Mac's runtime is
  OrbStack, not Docker Desktop, and the docs are corrected.
- **Step 9 (inference host).** The sysadmin agent installed the Compose plugin,
  added `--metrics` and installed the agent from the four copied files. The
  report is recorded in `collector/README.md` → Verification.
- **Step 10 (live).**
  - **Loaded model:** 16 `llamacpp_*` series with `model_id` for
    `qwen3.8-27b-ud-q8_k_xl`.
  - **Idle unload:** at 920 s against the 900 s TTL, then no reload for 1,813 s.
    The stack's `up` stayed 0 throughout.
  - llama-swap shows no agent requests.
  - Found in the live data: the stored context high-water name is
    `llamacpp_n_tokens_max_total`. 0005 is corrected.
- **Status:** `shipped`.
