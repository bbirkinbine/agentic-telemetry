# 0007 — Host-Local Model Metrics Scraper

**Status:** draft
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
  upstream on its own local port, never through llama-swap.
  It is an OTel Collector (contrib) in agent mode, pinned by digest like the
  stack's images (owner decision, 2026-10-21, from the 0003 review).
- Every forwarded series carries `model_id=<llama-swap model ID>`.
- The scraper sends OTLP to the stack's Collector, never remote-write straight
  to VictoriaMetrics. That keeps the stored names 0003 recorded (the colon
  becomes an underscore: `llamacpp_prompt_tokens_total`), and every sample also
  lands in the archive. It sends to the Collector's OTLP endpoint at
  `TELEMETRY_BIND_ADDR` (the Mac during 0003, the VM after 0006). It keeps a
  disk-backed buffer so a sleeping Mac loses no samples.
- With a model loaded, VictoriaMetrics holds that model's `llamacpp:*` series
  labelled with its `model_id`. This criterion moved here from 0003.
- With the model idle, llama-swap unloads it after its `ttl` as before; the
  sysadmin agent checks this once. The scraper's own `up` for that model drops to
  0 and nothing reloads it.
- llama-swap's access log shows no scraper requests.
- Builds without a Prometheus route are recorded as uncovered, not worked around:
  Project Maya before v1.1.5, and the Sybil SGLang/exllamav3 build.
- Tracked config carries placeholders only. Host addresses and ports live in
  untracked env files and the vault's `ENDPOINTS.md`.

## Non-goals

- Exporters or adapters for builds without Prometheus metrics (Sybil), and
  upgrading Project Maya.
- `/slots` polling and llama-swap request-log capture.
- Changing llama-swap's TTL or residency policy.
- The run-table join (0005).

## Notes

- **Out-of-repo actions need the owner.** The scraper runs on the inference host
  and is installed through that host's sysadmin agent. This repo holds its config
  template and README steps.
- **Upstream ports and identity** (inference host's sysadmin agent,
  2026-10-21). Each model has its own fixed port, so the port identifies the
  model. No model uses llama-swap's `${PORT}` macro. The port numbers, and which
  ports are bound to localhost versus all interfaces, are in the vault's
  `ENDPOINTS.md`, not in tracked files (owner decision from the 0003 re-review).
  The scraper's config reads them from an untracked env file.

  - While a model is unloaded, its port has no listener.
  - llama-swap's `GET /running` and `GET /v1/models` do not reset any model's
    TTL. They are gateway handlers and never reach a model process.
  - A loaded server reports its own identity:
    - Strata, llama.cpp and Maya: `GET /props` gives `model_alias`, and
      `GET /v1/models` gives `data[].id`;
    - Sybil: `GET /v1/models` gives `data[].id`, and it has no `/props`.

  So a scraper on the host can label each port with a fixed `model_id`,
  because the port identifies the model.
- **llama.cpp needs `--metrics`** (and `--slots`) on `qwen3.8-27b-ud-q8_k_xl`. Strata
  serves Prometheus text at `/metrics/prometheus` with no flag.
- **0005 depends on this spec.**
