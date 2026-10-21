# 0003 — Local Collector Stack

**Status:** shipped
**Last updated:** 2026-10-21
**Roadmap unit:** 3 (with 0004, 0005, 0006, 0007)

## Goal

Run the validated stack under `collector/` (OTel Collector, VictoriaMetrics,
Loki, Tempo) on the owner's Mac under Docker Desktop. Prove the stack end to end
before any homelab VM exists, and use it to answer the question roadmap unit 3
depends on: which metrics the inference host serves, and under which labels.
This is the first of five specs for roadmap pointer 3 in `0000-product.md`:

- 0003: the stack runs locally.
- 0004: files reach the landing zone and ledgers are ingested.
- 0005: the run table joins server metrics.
- 0006: the same stack moves to the `telemetry` VM.
- 0007: a scraper on the inference host collects per-model metrics (added by
  Standing decision D6).

The Mac can run the stack: Docker Desktop on arm64, 12 CPUs, about 16 GB for the
Docker VM, and the pinned Collector, Loki and Tempo images are already pulled
(checked 2026-10-21). Delegate runs start from the Mac, so the Mac is awake and
scraping during every run window that 0005 needs.

## Success criteria

- `collector/compose.yaml` and `otel-collector.yaml` take every host-specific
  value from the untracked `collector/.env`:
  - the data root (`TELEMETRY_ROOT`);
  - the llama-swap scrape target;
  - the address the published ports bind to (`TELEMETRY_BIND_ADDR`).

  Tracked files keep placeholders (`gpu.example`). A tracked, value-free
  `collector/env.example` lists every variable.
- `collector/README.md` gains a "Run locally on the Mac" section: the `.env`
  variables to set, `docker compose up -d`, the health check, and teardown.
  Teardown stops the stack and keeps the data root.
- With the stack up on the Mac, the Collector health endpoint answers 200.
- A synthetic metric, log and span sent with `telemetrygen` (pinned image
  version) each appear in `archive/` and in their store: VictoriaMetrics
  `/api/v1/query`, Loki `query_range`, Tempo search.
- VictoriaMetrics shows `up == 1` for the llama-swap gateway scrape job. The
  `llamacpp:*` series criterion moved to spec 0007 (Standing decision D6). 0003
  records the stored `llamacpp:*` names from one supervised capture.
- For each served build (llama.cpp upstream, Strata, Project Maya), the README
  records:
  - whether it exposes `/metrics` and `/slots`;
  - which `llamacpp:*` names it emits;
  - which label carries the model ID behind llama-swap.

  This answers the product spec's open question and fixes 0005's input.
- Every edited `collector/*.yaml` passes its validator on the pinned image
  (`otelcol-contrib validate`, `loki -verify-config`), recorded in the README's
  Verification section with the date.
- **Linux parity.** The stack on the Mac is the one that will run on Ubuntu
  (0006), down to the image bytes:
  - Every `image:` in `compose.yaml` is pinned as `tag@sha256:<index digest>`.
    The index digest names one multi-arch manifest, so the Mac pulls its
    `linux/arm64` entry and an x86 Ubuntu host pulls the `linux/amd64` entry of
    the same release. A re-pushed tag cannot change either.
  - The README records, for each image, that its index carries both
    `linux/amd64` and `linux/arm64` and which user it runs as.
  - Each config also passes its validator on the `linux/amd64` variant, run
    under emulation on the Mac (`--platform linux/amd64`).
  - The README's run steps create the data root with the ownership the
    containers need on a Linux host, where Docker Desktop's relaxed bind-mount
    ownership does not apply: `archive/`, `collector-state/`, `loki/` and
    `tempo/` writable by UID 10001, and `landing/` readable by it. Per D7 the
    steps are split by host: on the Mac the folders are created as the user,
    because Docker Desktop's file sharing writes as the macOS user. The Linux
    `chown 10001` steps are run for real in 0006.
- No real hostname, IP or home path enters a tracked file. Captured metric
  values are summarised (names and label keys), never pasted.

## Non-goals

- Shipping files into the landing zone, and changes to `filelog/landing` (0004).
- The run-table join (0005).
- The homelab VM, its backups, and the Grafana data sources on the monitoring VM
  (0006).
- A Grafana container on the Mac. Checks use the stores' query APIs.
  <!-- assumption: no local Grafana; add an unmodified upstream container only if the API checks prove too clumsy -->
- The live OTLP path from the Mac: `mac-agent/otel-agent.yaml`, and user-level
  Claude Code and Codex OTel settings. This is vault plan Phase 2 and gets its
  own spec. <!-- assumption: deferred to keep unit 3 to the batch path and the join -->
- Polling `/slots`, or capturing llama-swap `/logs/stream/http` and swap events.
  This spec only records whether they exist.
- Bearer-token auth on the Collector.

## Notes

- **ADR first (done).** The vault plan routes traces to Langfuse. This repo chose
  VictoriaMetrics, Loki and Tempo instead. The choice is recorded in
  `docs/adr/0002-grafana-stack-stores-over-langfuse.md`, accepted 2026-10-21.
- **Ports.** The stack binds 4317, 4318, 8428, 3100, 3200 and 13133 on the Mac.
  The planned Mac agent also wants `localhost:4318`. If both ever run on one Mac,
  the stack's ports move; that is not this spec's problem while the live path is
  deferred.
- **Sleep.** Scrapes stop while the Mac sleeps. That is acceptable for testing
  and for 0005's windows, and is the reason 0006 exists.
- **Env example.** Agent policy denies reading `.env*` files, and the existing
  `collector/.env.example` is ignored by the `.env.*` rule, so it was never
  tracked. See Standing decisions D1.
- **Don't-touch list.** `collector/*.yaml` edits are re-validated against the
  pinned images and recorded in `collector/README.md`.

- **Images checked 2026-10-21** (`docker buildx imagetools inspect`):
  - Every pinned tag publishes `linux/amd64` and `linux/arm64`.
  - Collector contrib 0.163.1, Loki 3.7.9 and Tempo 2.10.10 run as UID 10001.
    VictoriaMetrics v1.154.1 runs as root.
- **Docker Desktop vs Docker Engine.** On Linux, published ports bypass `ufw`.
  `host.docker.internal` does not exist without an `extra_hosts` entry; the
  stack does not use it, since the scrape target is a remote host.

## Standing decisions

Answered by the owner on 2026-10-21, from the first `/plan` run:

- **D1, env example.** The tracked, value-free example is a new
  `collector/env.example`. It has no leading dot, so neither the agent's `.env*`
  read policy nor the `.env.*` ignore rule applies to it. Tests check it against
  `compose.yaml`, and the README says `cp env.example .env`. The owner deletes
  the old untracked `collector/.env.example`; the agent never reads it.
- **D2, port binding.** Every `ports:` entry binds to a required
  `TELEMETRY_BIND_ADDR` from `.env`: `127.0.0.1` on the Mac, and the VM's LAN or
  Tailnet address in 0006. Unauthenticated OTLP and the stores are not published
  on every network the laptop joins.
- **D4, archive format** (owner, 2026-10-21, during step 6). Found on the live run:
  with `compression: zstd`, the file exporter by default writes length-prefixed
  batches, each compressed on its own, which `zstd -d` cannot read. The owner
  chose the `exporter.file.nativeCompression` feature gate instead:
  - archive files are OTLP JSON lines inside a standard zstd stream, named
    `*.jsonl.zst`, and readable with `zstd -dc`;
  - the gate is Alpha in 0.163.1, so every Collector upgrade re-checks it;
  - the three smoke-test files written in the old format were moved, not deleted,
    to `archive/pre-native-2026-10-21/` in the local data root.
- **D5, scrape shape** (owner, 2026-10-21; the plan's step 7 `[ask-user]`). The
  inference host's sysadmin agent found that llama-swap's own `/metrics` serves
  only system and GPU telemetry, and is switched off (`performance.disabled: true`,
  hence the 503). Per-model `llamacpp:*` metrics exist only behind
  `/upstream/<model-id>/…`, on a path that differs by build, and none carries a
  model label. Scraping an unloaded model's path makes llama-swap load it. The
  owner chose:
  - **Tracked jobs.** One `prometheus/servers` job per Prometheus-capable model
    in `otel-collector.yaml`: target `${env:LLAMA_SWAP_TARGET}`, `metrics_path`
    `/upstream/<llama-swap model ID>/…`, and a static `model_id` label. Plus a
    `llama-swap` job on the gateway's own `/metrics`. Model IDs are names, not
    addresses, so they may be tracked.
  - **Host changes.** The owner applies these through the host's sysadmin agent:
    - `upstream.ignorePaths` for metrics, slots and props, so a scrape of an
      unloaded model returns 409 instead of loading it;
    - `performance.disabled: false`;
    - `--metrics --slots` on the llama.cpp model.

    Per-model jobs must not run against the host before `ignorePaths` is in
    place.
  - **Criterion wording.** Delegate ledgers record profile aliases (for example
    `local-qwen38-flash-next`), not llama-swap model IDs. So 0003 requires a
    `llamacpp:*` series labelled `model_id=<llama-swap model ID>` for a loaded,
    Prometheus-capable model. Mapping aliases to IDs is 0005's problem.
  - **No job yet for two builds.** Project Maya v1.1.4 (JSON-only metrics) and
    the Sybil SGLang/exllamav3 build (no Prometheus route) get none. The README
    records why.
- **D6, observe without perturbing** (owner, 2026-10-21). This supersedes D5's
  per-model jobs, D5's host changes, and D5's criterion wording. Before applying
  `ignorePaths`, the sysadmin agent found that in llama-swap v264 a request to an
  already-loaded model's `/upstream/…` path updates `lastUse`, even on an ignored
  path. A 30-second scrape would therefore keep the scraped model loaded
  indefinitely. That would suppress the swap and load waits the analysis needs to
  observe. The owner chose:
  - **Gateway job only.** `otel-collector.yaml` keeps the `llama-swap` job on the
    gateway's own `/metrics`. No scrape job goes through `/upstream/`, and a test
    guards that.
  - **One host change**, made by the owner through the sysadmin agent:
    `performance.disabled: false`. No `ignorePaths` change is needed.
  - **Per-model metrics move to spec 0007.** A scraper on the inference host
    reads each upstream server's own port directly, so llama-swap never sees the
    request. When a model is unloaded its port is closed and nothing loads. 0005
    depends on 0007.
  - **Criterion.** 0003's `llamacpp:*` series criterion moves to 0007. 0003
    requires `up == 1` for the gateway job.
  - **Stored names.** For 0005's queries, 0003 records the names
    VictoriaMetrics stores for `llamacpp:*`. They come from one short,
    supervised capture of an already-loaded model, made by a temporary
    Collector outside the tracked config. That capture resets that model's
    idle timer only while it runs.
- **D7, review adjudication** (owner, 2026-10-21, answering the `[ask-user]`
  findings from `/review` and `/review-adversarial`):
  - **Data-root steps split by host.** On the Mac, create the folders as the
    user; Docker Desktop's file sharing writes as the macOS user, and the live
    root was made that way. The Linux `sudo mkdir`, `chown 10001` and
    `chown $USER landing` steps are run for real in 0006. This replaces "the Mac
    uses the same steps" in criterion 8.
  - **Ship after the gateway check.** 0003 waited for `up == 1` on the gateway
    job, which the host change made true the same day.
  - **0007 transport.** The host scraper is an OTel Collector in agent mode that
    sends OTLP through the stack's Collector, so 0005 queries the underscore
    names 0003 recorded.
  - **0006 and 0005.** 0006 keeps `Depends on: 0003, 0004`. Its join check runs
    only if 0005 has shipped. The bind-address question (one IP, two networks)
    is an open question in 0006.
  - **Digest test kept.** The README's upgrade steps name all four places that
    pin a digest, including the test constant.
  - **Confirmations.** "Sybil" is a build codename. The `AGENTS.md` numbering
    rule records the owner's correction from this session (one four-digit spec
    per part, no lettered sub-specs). D3 was the plan's conditional scrape-shape
    question; it was answered as D5, so nothing was dropped.

## External references

- **llama.cpp server metric names** (`llamacpp:*`). Empirical: captured from the
  owner's running builds' `/metrics` and recorded in the README, because Strata
  and Project Maya may differ from upstream. Upstream llama.cpp (MIT) is the
  cross-check.
- **llama-swap `/metrics`** and how it exposes upstream metrics. Empirical,
  captured from the running gateway.
- **`telemetrygen` usage.** Authoritative source: its README in
  `opentelemetry-collector-contrib`, Apache-2.0,
  https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/cmd/telemetrygen/README.md.
  The implement phase fetches it in-session and records the retrieval date next
  to the pinned service.

## Approved implementation plan

**Approved:** 2026-10-21

This is the planner's replacement plan, copied verbatim, with three changes:

- Headings are demoted two levels so they nest under this section.
- The planner's preamble asked the orchestrator to fix two stale spec lines. It
  is removed, and both lines are fixed in this same edit.
- Step 5's last bullet carries a done note: the owner deleted the old untracked
  `collector/.env.example` on 2026-10-21.

The first plan's two decisions are answered under Standing decisions (D1, D2).

The plan abbreviates the image digests. These are the full index digests,
checked 2026-10-21 with `docker buildx imagetools inspect`:

- `otel/opentelemetry-collector-contrib:0.163.1`:
  `sha256:1175b03003053962012f5a231f5567f66a3e8fdb960c7835c0e48f7aa1555806`
- `victoriametrics/victoria-metrics:v1.154.1`:
  `sha256:c7884f32920dd45a9d86542a717227e63af03e060e53bbf8031e6e6fd0e1e05d`
- `grafana/loki:3.7.9`:
  `sha256:0db216b630ae1f348a1584c67f3cead8b4c1fc839169a39501db664744158e28`
- `grafana/tempo:2.10.10`:
  `sha256:12b0820f13ffa10ce956b6e9acc3b03f3cffded99109be6cc09d106fe56e5d38`

### Plan: 0003 Local Collector Stack

Spec: `docs/specs/0003-local-collector-stack.md`, with Standing decisions D1 (a tracked `collector/env.example`) and D2 (a required `TELEMETRY_BIND_ADDR` on every published port). ADR 0002 is accepted. Nothing changes in `src/`. There are no schema changes, migrations or version bumps. The only dependency change is one dev-group entry.

#### Files to touch
- `collector/compose.yaml`
  - Pin every `image:` as `tag@sha256:<index digest>`:
    - `otel/opentelemetry-collector-contrib:0.163.1@sha256:1175b030...5806`
    - `victoriametrics/victoria-metrics:v1.154.1@sha256:c7884f32...e05d`
    - `grafana/loki:3.7.9@sha256:0db216b6...8e28`
    - `grafana/tempo:2.10.10@sha256:12b0820f...5d38`
    - telemetrygen (chosen in step 3).
  - Every bind source uses `${TELEMETRY_ROOT:?set TELEMETRY_ROOT in collector/.env}`.
  - Every `ports:` entry reads `"${TELEMETRY_BIND_ADDR:?set TELEMETRY_BIND_ADDR in collector/.env}:<host>:<container>"`. Keep the `:?` message free of colons.
  - `otel-collector.environment` gets `LLAMA_SWAP_TARGET: ${LLAMA_SWAP_TARGET:?set LLAMA_SWAP_TARGET in collector/.env}`.
  - Add a `telemetrygen` service:
    - under `profiles: [smoke]`, so `up -d` never starts it;
    - no `ports:`;
    - sends to `otel-collector:4317` over the compose network, which is the same on Linux and needs no `host.docker.internal`;
    - run as `docker compose --profile smoke run --rm telemetrygen <metrics|logs|traces> ...`.
  - Rewrite the header comment to cover both the Mac and the VM.
- `collector/otel-collector.yaml`
  - `prometheus/servers` target becomes `["${env:LLAMA_SWAP_TARGET}"]`.
  - Update the header's validate command.
  - Nothing else unless the step 7 [ask-user] answer adds scrape jobs. `filelog/landing` belongs to 0004.
- `collector/env.example` (new, tracked, value-free). One commented line per variable:
  - `TELEMETRY_ROOT=/srv/telemetry`
  - `LLAMA_SWAP_TARGET=gpu.example:9292`
  - `TELEMETRY_BIND_ADDR=127.0.0.1`, with a comment that the VM uses its LAN or Tailnet address, e.g. `192.0.2.10`.
- `collector/README.md`
  - Update the status line at the top.
  - New **Run locally on the Mac** section:
    - `cp env.example .env` and the three variables. `TELEMETRY_ROOT` must sit under a path Docker Desktop shares;
    - create the data root:
      - `mkdir -p {archive,landing,collector-state,victoria,loki,tempo}`;
      - `sudo chown 10001:10001` on archive, collector-state, loki and tempo;
      - landing stays owned by the shipping user at mode 0755;
      - victoria is left alone because VictoriaMetrics runs as root.

      These steps are identical on Linux;
    - `docker compose up -d`;
    - the health check: `curl -fsS -o /dev/null -w '%{http_code}' "http://$TELEMETRY_BIND_ADDR:13133"`. That is `127.0.0.1` on the Mac; the VM uses its bind address, not `localhost`;
    - the telemetrygen smoke commands and the store queries: VM `/api/v1/query`, Loki `query_range` on `{service_name="telemetrygen"}`, Tempo `/api/search`;
    - an archive check: `zstd -dc`, then grep `telemetrygen`;
    - teardown with `docker compose down`. Never remove the data root.
  - New **Images** table, for all five images: tag, index digest, amd64 and arm64 both present, runtime user.
  - New **Inference server endpoints** table, one row per build (llama.cpp upstream, Strata, Project Maya):
    - whether it serves `/metrics` and `/slots`;
    - the `llamacpp:*` names it emits;
    - the model-ID label behind llama-swap;
    - the names and label keys as stored in VictoriaMetrics.

    Names and keys only, never values.
  - Bring-up step 2 becomes `cp env.example .env`.
  - Dated Verification entries:
    - arm64 and amd64 validator runs;
    - `docker compose config`;
    - the live checks;
    - the telemetrygen README URL, retrieval date and Apache-2.0.
  - Trim the "Not yet done" list.
- `pyproject.toml`: add `"pyyaml>=6.0.3,<7"` to `[dependency-groups].dev` and refresh `uv.lock`. PyYAML is already locked through pre-commit; naming it stops the tests depending on that. This is not the `[tool.uv]` section. mypy covers only `src/`, so no stubs are needed.
- `tests/collector_files.py` (new helper): repo-root paths, `yaml.safe_load` loaders, and an `env.example` parser.
- `tests/test_collector_compose.py`, `tests/test_collector_hygiene.py`, `tests/test_collector_readme.py` (new; coverage below).
- Orchestrator bookkeeping:
  - in spec 0003: the approved plan, the two stale lines above, and the status flips;
  - regenerate `docs/specs/README.md`;
  - update the current-state block in `AGENTS.md`;
  - at closeout, record that the owner deleted the old untracked `collector/.env.example` (the agent never reads it).
- `collector/loki-config.yaml`, `collector/tempo.yaml`: no planned edits. If the live run forces one, re-validate on both arches and record it (loki `-verify-config`; tempo by a start-parse, since there is no verify flag).

#### Order of operations
1. Add `pyyaml` to the dev group; run `.agentic/toolchain.sh install`.
2. Test-first (delegate). It writes only under `tests/`; coverage is below. Rerun the focused tests and confirm `AssertionError` reds.
3. Implement `compose.yaml`, `otel-collector.yaml` and `collector/env.example`. Pin telemetrygen:
   - fetch `https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/cmd/telemetrygen/README.md` in-session for flags and the image name (`ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen`);
   - prefer the tag that matches collector 0.163.x;
   - read its index digest, platforms and user with `docker buildx imagetools inspect`;
   - put a provenance comment (URL, date, Apache-2.0) next to the service.
4. Validate on the pinned digests, arm64 first and then with `--platform linux/amd64`:
   - `otelcol-contrib validate` with `-e LLAMA_SWAP_TARGET=gpu.example:9292`. Always pass it: an unset `${env:}` expands to empty, and an empty target may fail;
   - `loki -verify-config`. The file is unchanged; this is the amd64 parity run;
   - `docker compose --env-file env.example config --quiet`.
5. Owner, outside the session:
   - create `collector/.env` from `env.example`, with the real target from `ENDPOINTS.md` and `TELEMETRY_BIND_ADDR=127.0.0.1`;
   - create the data root using the README steps (needs `sudo`);
   - delete the old `collector/.env.example`. (Done 2026-10-21.)
6. `docker compose up -d`. Confirm each running container is arm64 with `docker inspect --format '{{.Architecture}}'`. Then:
   - health check returns 200 on `127.0.0.1:13133`;
   - send a telemetrygen metric, log and span;
   - confirm each lands in `archive/` and in VM, Loki and Tempo.
7. Probe through the local VM API. The agent never reads `.env` and never contacts the target directly.
   - The owner first sends one request to a model a delegate profile names, so it is loaded.
   - The agent queries `up` for the scrape job, `/api/v1/label/__name__/values` filtered to `llamacpp`, and `/api/v1/labels`.
   - For `/slots` and per-build `/metrics` via `/upstream/<model>/...`, the agent prepares a command that prints only HTTP status codes, metric names and label keys. The owner runs it outside the session and pastes the result.
   - **[ask-user]** If no `llamacpp:*` series sits behind llama-swap's `/metrics`, stop and ask the owner for the scrape shape:
     - one job per delegate-profile model on `/upstream/<model-id>/metrics` with a `model` label;
     - and where those model IDs live (tracked config, or `.env` / a file_sd file under `TELEMETRY_ROOT`).

     Resume once the answer is recorded under Standing decisions; then re-run steps 4 and 6.
8. Write the README sections and the dated Verification entries.
9. Docs and bookkeeping, then the gate (`.agentic/toolchain.sh gate`), `/review-check`, `/review` and `/review-adversarial` (because of the network-exposure and parity surface). Stop for commit approval.

#### Test-first coverage (pytest, static checks on files only, no Docker in the gate)
Each test asserts the file exists first, so a missing `env.example` or README section is an `AssertionError`, not a `FileNotFoundError`.

**Criterion 1 (host values from `.env`)**
- Every compose bind source starts with `./` or `${TELEMETRY_ROOT:?`.
- `otel-collector.environment` carries `LLAMA_SWAP_TARGET`.
- The `prometheus/servers` targets are only `${env:...}` references.
- Every `${env:X}` in the collector config is passed by compose.
- Every `${X}` in compose is declared in `collector/env.example`.
- `LLAMA_SWAP_TARGET` in the example is a `*.example` host.
- `TELEMETRY_ROOT` in the example is not under `/Users/` or `/home/`.

**Criterion 1 / D2 (bind address)**
- Every entry in every service's `ports:` starts with `${TELEMETRY_BIND_ADDR:?`.
- `env.example` declares `TELEMETRY_BIND_ADDR` with a loopback value (`127.0.0.0/8`) or an RFC 5737 value (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`).
- No port is published bare or on `0.0.0.0`.

**Criterion 8 (parity)**
- Every `image:` matches `name:tag@sha256:[0-9a-f]{64}`.
- `telemetrygen` is profile-gated and has no `ports:`.
- Every compose image reference appears in the README Images table with `linux/amd64`, `linux/arm64` and a user.
- The run steps mention `10001` and each of `archive`, `collector-state`, `loki` and `tempo`.

**Criterion 9 (hygiene)**, over an explicit list of tracked collector files, never `landing/` contents:
- no `/Users/` or `/home/`;
- no IPv4 outside RFC 5737, loopback and `0.0.0.0`;
- every `host:port` literal is `*.example`, a compose service name, `localhost` or a `${...}` reference.

**Criteria 2 and 6 (structure only)**
- The "Run locally on the Mac" heading exists and contains `cp env.example .env`, `docker compose up -d`, `13133` and `docker compose down`.
- The endpoints table has a row for each of the three builds.

**Manual checks**, recorded in the README's Verification section:
- health 200;
- telemetrygen in the archive and all three stores;
- `up == 1` and a `llamacpp:*` series for a delegate-profile model;
- the endpoint and label findings;
- the arm64 and amd64 validator runs and `compose config`;
- running containers are arm64.

#### Risks / open questions
- **Stored metric names may differ.** The OTel prometheus receiver, then OTLP, then VM with `usePrometheusNaming` may rename `llamacpp:*` (colon or suffix handling) and may expose `job` as `service_name`. Query with `{__name__=~"llamacpp.*"}` and record the stored names and label keys exactly; 0005 queries those.
- **Emulated validation can re-point the local tag.** Running `--platform linux/amd64` without the containerd image store may leave compose running the amd64 image under emulation. Validate by digest and check `Architecture` after `up` (step 6).
- **The probe swaps models.** Requests through `/upstream/<model>` make llama-swap load a model on the live GPU host. Run the probe when no delegate run is active.
- **The real host lands in local data.** The `instance` label carries the real host:port in VM and the archive. That is untracked, but the agent avoids printing `instance` values and the README records label keys only.
- **Mac file ownership.** After `chown 10001`, the owner may lose write access to `archive/`; reads still work. File modes for Linux-side readers are 0006's concern.
- **0004 interaction.** Under D2, the 0004 ship script and the 0006 Grafana data sources must reach the stack at `TELEMETRY_BIND_ADDR`, not at `localhost`. Note this for 0004 and 0006; nothing changes here.
- **Mislabelled Mac data.** `resource` still stamps `deployment.environment=homelab` on Mac data. That is left as is (not one of the three variables); the Mac data is throwaway, and 0006 starts empty.

#### Out of scope (won't touch)
- `filelog/landing` include/exclude rules, timestamp parsing, `ship-ledgers.sh` and `collector/landing/README.md` (0004).
- The run-table join and everything under `src/` (0005).
- The VM, backups, the Grafana data sources and `ENDPOINTS.md` (0006).
- `collector/mac-agent/otel-agent.yaml`, the live OTLP path and client OTel settings.
- Polling `/slots` or capturing `/logs/stream/http`.
- `bearertokenauth`.
- A local Grafana container.

Planner sources:
- [OpenTelemetry Collector quick start (telemetrygen usage)](https://opentelemetry.io/docs/collector/quick-start/index.md)
- [oneuptime: telemetrygen pipeline validation (image name)](https://oneuptime.com/blog/post/2026-02-06-telemetrygen-test-traces-pipeline-validation/view)
- [oneuptime: run the Collector locally in Docker](https://oneuptime.com/blog/post/2026-02-06-run-collector-locally-docker-quick-testing/markdown)

## Phase handoff

### Test-first (2026-10-21)

- **Files.** Four new test files: `tests/collector_files.py` (helper),
  `tests/test_collector_compose.py`, `tests/test_collector_hygiene.py` and
  `tests/test_collector_readme.py`.
- **Scope audit.** Clean. Against the pre-phase fingerprints, only those four
  paths changed, and the index did not change.
- **Red, re-run by the orchestrator** with `--maxfail` (the toolchain passes
  `-x`): 24 failed and 26 passed. All 24 failures are `AssertionError`s for
  missing behaviour. The 26 passing cases are hygiene guards over existing
  collector files.
- **Beyond the coverage list.** Four checks are kept because each pins a value
  the spec states:
  - the exact index digests;
  - the runtime users (10001, and root for VictoriaMetrics);
  - `cp env.example` in place of `cp .env.example`;
  - `/metrics` and `/slots` recorded in the endpoints section.
- **Rejected-value search.** Terms searched:
  - `.env.example`;
  - unprefixed `"N:N"` port entries;
  - image tags without a digest;
  - `gpu.example:9292` as a literal scrape target;
  - `localhost:13133`.

  Every match is in a file the plan edits (`collector/README.md`,
  `collector/compose.yaml`, `collector/otel-collector.yaml`). One is kept on
  purpose: `0.0.0.0:13133` in `otel-collector.yaml` is the Collector's in-container
  listen address, not a host publish. No existing test asserts an old value.

### Implement (2026-10-21)

- **D5 test, written red-first by the orchestrator.**
  `test_scrape_jobs_cover_gateway_and_label_each_upstream_model` (one labelled
  job per upstream model) went red before its scrape jobs existed.
- **Replaced under D6.** It became
  `test_scrape_jobs_cover_the_gateway_and_never_go_through_upstream`. The new test
  was shown red against a copy of the config holding an `/upstream/` job, and
  green against the real config.
- **Fixes the live run forced:**
  - Tempo's data mount moved from `/tempo` (the binary) to `/var/tempo`;
  - the archive moved to native zstd (D4).
- **Live checks, recorded in `collector/README.md` → Verification:**
  - arm64 containers, health 200, and the telemetrygen smoke test in all three
    stores and the archive;
  - one supervised Strata capture recorded the stored `llamacpp_*` names.
- **Gate green:** 364 tests.
- **Review.** `/review` and `/review-adversarial` both ran. Every `[auto-fix]`
  was applied:
  - README drift from D5 to D6;
  - the amd64 Tempo start and final `compose config` runs, recorded;
  - a D4 archive-format test and a broadened D6 guard, each shown red on a
    broken copy;
  - vault citations replaced in the Collector header and ADR 0002;
  - README commands fixed so they work when followed literally.

  The `[ask-user]` items were answered by the owner as D7.
- **Closed.** After the host set `performance.disabled: false`, the gateway job
  showed `up == 1` on 2026-10-21. Spec marked `shipped`.
