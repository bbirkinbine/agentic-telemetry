# 0005 — Server-Metrics Join

**Status:** shipped
**Last updated:** 2026-10-22
**Depends on:** 0003, 0007
**Roadmap unit:** 3 (with 0003, 0004, 0006, 0007)

## Goal

Add server-side columns to the run table by joining each delegate run to the
inference host's per-model metrics in VictoriaMetrics. This is the step
`analysis/README.md` names as missing: nothing today joins agent-side records to
server-side records. Without it, a slow or failed local run cannot be told apart
from a slow, cold or busy server.

No request carries a run ID through llama-swap, so the join key is model plus
time window. The model is resolved without a hand-kept mapping:
- names the run's own transcript recorded, kept only if VictoriaMetrics knows them;
- failing that, which model's counters moved during the run;
- either way, token counts on both sides say how good the match is.

### Facts checked against local evidence on 2026-10-21

Only key names, model names, timestamps and counts were inspected, never content.

- **Run window.** A ledger `run` row's `ts` is the run's **end** time. The run ID
  ends in the start stamp (`...-YYYYMMDDTHHMMSSZ-<pid>`), and `ts − wall_seconds`
  equals that stamp to the second on every handoff run checked (6 of 6).
- **Names in transcripts.** Each assistant message in a Pi transcript carries the
  requested `model`. Pi adds the server's `responseModel` only when it differs
  from the requested name (Pi 1.3.0 `docs/message-types.md`, as cited in
  `session_table/pi.py`; corrected at plan time), so a run that requests the
  llama-swap ID directly records no served name and resolves by rule 2.
  - Behind llama-swap, llama.cpp and Strata answer with the llama-swap model ID.
    For example, alias `local-qwen38-27b` was served as `qwen3.8-27b-ud-q8_k_xl`,
    and `local-qwen38-flash-next-q8` as `qwen3.8-flash-next-unsloth-q8_0`.
  - The GLM builds answer with their own name. Maya and Sybil both report
    `glm-5.3-flash`, which is also Sybil's llama-swap ID. So a served name is
    trusted only when it is a known `model_id` and the requested name does not
    contradict it.
  - The requested name is sometimes the profile alias (`local-glm53-maya-l`)
    and sometimes the llama-swap ID itself (`glm-5.3-flash-maya-s24`).
  - Two interactive sessions echoed the alias as `responseModel`, probably
    after failed requests.
  - Aliases drift. On 2026-10-13, `local-fast` was served as
    `qwen3.8-flash-next-unsloth-ud-iq4_xs`, and quants changed later. A static
    alias table would need date ranges; the transcript records the truth for
    each run.
- **Clocks and tokens agree** (one interactive Pi session, 2026-10-22 UTC,
  against the 0007 agent's series):
  - The session's input and output tokens (17,784 and 10,714) equal the
    increases of `llamacpp_prompt_tokens_total` and
    `llamacpp_tokens_predicted_total` over its window, exactly.
  - The session ended at 02:50:17.978Z by the Mac's clock. The inference host
    logged the last request at 02:50:18Z. The two clocks agree to within about
    a second, so the 30 s scrape interval, not clock skew, limits resolution.
  - The session started at 02:44:48Z. The model's `up` turned 1 at the 02:45:24Z
    scrape, and the first tokens appeared by 02:45:54Z: a cold load visible
    from `up`.
- **Existing delegate runs** in the owner's two private projects all used
  Project Maya, which has no Prometheus metrics (0007, uncovered). They all
  predate the 0007 agent, so none can join. Verification needs a new delegate run
  on Strata or llama.cpp.

## Success criteria

- **Flag.** `run-table` takes an optional `--metrics-url URL` (the VictoriaMetrics
  base URL). Without it, behaviour and output match 0001 exactly: the `server_*`
  columns are null and `server_status = "not_queried"`. The transcript columns
  (`requested_models`, `served_models`, `peak_turn_prompt_tokens`) need no
  network and are always filled (owner decision at plan approval, 2026-10-22).
- **Transcript names.** The run table's transcript reader also records each run's
  model names, as new columns:
  - `requested_models` and `served_models`: the distinct `model` and
    `responseModel` values over the run's assistant messages, in first-seen
    order;
  - null when the run has no transcript.
- **Known models.** With the flag, the command reads the set of `model_id` label
  values from VictoriaMetrics once per invocation.
- **Model resolution.** For each run, `server_model_id` and `server_model_source`
  are set by the first rule that applies:
  1. `served`: exactly one served name is a known `model_id`, and no requested
     name, nor the ledger's `model_id`, is a different known `model_id` (D8);
  2. `requested`: exactly one requested name, or the ledger's `model_id`, is a
     known `model_id`;
  3. `inferred`: the transcript recorded no model names, and exactly one known
     `model_id` has a nonzero increase in `llamacpp_prompt_tokens_total` or
     `llamacpp_tokens_predicted_total` over the run's lookback. The inference is
     kept only when the run's tokens reconcile `exact`; otherwise the run is
     `uncovered` with source `none` (D8);
  4. otherwise, `server_model_id` is null and `server_model_source` is `none`.
- **Window.**
  - The run's window is `[ts − wall_seconds, ts]`. When `wall_seconds` is absent,
    `agent_seconds` is used instead, and when the run ID carries a start stamp
    that stamp wins.
  - Ranged queries look back over the run plus one scrape interval (30 s),
    evaluated one scrape past the run's end, so the lookback starts at the run's
    own start. There is no start-side widening (D8): `increase_pure` already
    takes the last sample before its window as the base, and reaching one
    scrape further back would count more of a preceding run's tokens.
- **Columns**, filled for the resolved model over that lookback:
  - `server_prompt_tokens` and `server_predicted_tokens`: the increase of the
    two `_total` counters;
  - `server_prefill_tps`: increase of `llamacpp_prompt_tokens_total` divided by
    the increase of `llamacpp_prompt_seconds_total`;
  - `server_decode_tps`: increase of `llamacpp_tokens_predicted_total` divided by
    the increase of `llamacpp_tokens_predicted_seconds_total`;
  - in both rates, a zero denominator gives null. The per-scrape `*_seconds`
    gauges are not used: llama.cpp resets their buckets on every `/metrics`
    call (0007);
  - `server_deferred_max`: max of `llamacpp_requests_deferred`;
  - `server_ctx_high_water`: `llamacpp_n_tokens_max_total` at the window's end.
    It is the largest context since the model loaded, not this run's own; the
    column docstring and the README say so;
  - `server_cold_start`: true when the model's `up` was 0 (or absent) at the
    run's start and 1 at any point through one scrape past its end. It means the
    run waited for a model load, measured to scrape resolution. Null when no `up`
    sample exists after the start at all (D8).
- **Peak context.** `peak_turn_prompt_tokens` is the largest
  `input + cacheRead + cacheWrite` of any one assistant turn in the run's
  transcripts: the run's own peak context, null when there is no transcript
  (owner decision at plan approval, 2026-10-22).
- **Token reconciliation.** `server_token_match` compares the run's transcript
  tokens with the server's increases, prompt and predicted separately:
  - `exact`: both equal;
  - `server_more`: the server processed more than the run sent, so other
    traffic shared the window;
  - `server_less`: the server saw less, for example because the model unloaded
    mid-run or a scrape was missed;
  - `not_compared`: no transcript tokens, or no resolved model.
- **Status.** `server_status`, one of:
  - `joined`: a resolved model with series in the window, and
    `server_token_match` is `exact` or `not_compared`;
  - `joined_approx`: a resolved model with series, but `server_token_match` is
    `server_less`;
  - `overlap`: `server_token_match` is `server_more`, or another run in the
    table shares the resolved model and an overlapping window. The columns
    stay filled for inspection, but nothing is apportioned between runs;
  - `uncovered`: the requested or served names match no known `model_id`
    and nothing was inferred. Typical for builds without metrics, such as Maya
    and Sybil;
  - `no_data`: a resolved model with no series in the window, including every
    run older than the 0007 agent;
  - `error`: a query failed. A warning names the run and the URL path.
- **Missing metrics.** Where a build lacks a metric, that column is null and the
  status is unchanged. For example, Strata serves no `n_tokens_max`.
- **Summary and exit codes.**
  - The summary line counts runs per `server_status` and per
    `server_model_source`.
  - An unreachable `--metrics-url` exits 1 before writing, naming the URL.
  - A malformed URL exits 2.
- **No new dependency.** Queries use the standard library HTTP client.
- **Tests.**
  - The HTTP layer is stubbed with response-shaped JSON built in `tests/`.
  - Synthetic transcripts cover every resolution rule and every status,
    including the Maya case (served `glm-5.3-flash` with a different known
    requested ID) and an echoed alias.
  - No live endpoint, hostname or real metric value is committed.
- **Live check** (manual, counts only). With the 0007 agent running, the owner
  makes one delegate run on Strata or llama.cpp, then runs `run-table
  --metrics-url ...`. That run's row has:
  - `server_status = "joined"`;
  - `server_model_source` `served` or `requested`;
  - non-null throughput columns;
  - `server_token_match = "exact"`, or a recorded reason it is not.

## Non-goals

- Exact swap-wait durations, and the sampling parameters the server applied.
  These need `/slots` polling and llama-swap log capture. `server_cold_start`
  only flags that a load happened.
- Server columns for the session table. The same join would work for
  interactive sessions (see Facts), but it belongs in a follow-up spec.
- Aggregates across runs (per-fingerprint throughput, the failure matrix).
- Apportioning metrics between overlapping runs.
- A hand-kept alias-to-ID mapping file.

## Notes

- **`server_ctx_high_water` limits.** The metric is a high-water mark since the
  server started, so it can come from an earlier run on the same loaded model.
  A run's own peak context is `peak_turn_prompt_tokens`, read from its
  transcript (D2).
- **Prompt caching and token matching.** Resolved by the live check
  (2026-10-22): the handoff run had cache-read tokens, and its prompt and output
  tokens still reconciled `exact` against Strata's counters. So Pi's input
  count excludes cached prefix tokens, as llama.cpp's processed-prompt counter
  does. `server_less` and `server_more` still surface any build that differs.
- **`increase_pure` and series that appear mid-life** (from the adversarial
  review). If a counter series' first sample is not a true zero, `increase_pure`
  counts the counter's whole lifetime value. Examples: a target newly added to
  `targets.json` for a server that was already running, or a gap longer than
  VictoriaMetrics' previous-sample allowance. The run then shows a huge
  `server_prompt_tokens` and `overlap`, visible rather than hidden. Add a target
  while its model is unloaded to avoid it.
- **Runs less than one scrape apart.** The base sample is the last scrape at
  or before a run's start, and the end lookback runs one scrape past its end.
  So two same-model runs less than about 30 s apart (a pipeline's implement
  then review, for example) each pick up a little of the other's tokens and
  both read `server_more` and `overlap`. That is inherent to 30 s scrapes and is
  visible rather than hidden. Read such an `overlap` against the runs' own
  timing before treating it as concurrent traffic.
- **Concurrent runs.** One run at a time on the server keeps windows
  unambiguous (`analysis/README.md`). Token reconciliation detects a violation
  even when the other traffic is not in the run table, such as an interactive
  session.
- **Old runs.** Runs before the 0007 agent existed get `no_data` or
  `uncovered`. The join only pays off on new runs.

## Standing decisions

Answered by the owner on 2026-10-22, approving the plan with every recommendation:

- **D1, transcript columns.** `requested_models`, `served_models` and
  `peak_turn_prompt_tokens` are filled with or without `--metrics-url`; only the
  `server_*` columns depend on the flag.
- **D2, peak context column.** Added as `peak_turn_prompt_tokens` (criterion
  above).
- **D3, interpretations.** A mixed token result (server higher on one side,
  lower on the other) counts as `server_more`. Overlap compares unwidened
  windows. A run with no usable window is `no_data` with a warning. Rule 2's
  candidates are the requested names plus the ledger's `model_id`. Server token
  columns are BIGINT, rounded.
- **D4, implementation route: dogfood.** The orchestrator writes the failing
  tests. Then it qualifies `local-qwen38-flash-next` and, after the owner's
  explicit yes to activation, hands the implement phase to it through
  `.agentic/delegate-run.sh handoff`. The orchestrator reviews and fixes the
  handoff branch. That run is the live check's delegate run.

- **D5, `increase_pure` for the counters** (owner, 2026-10-22, after the plan's
  provenance step). The four token and seconds counter queries use
  `increase_pure`, not `increase`, wherever the plan says `increase`. The
  MetricsQL docs say `increase` "ignores the first value in a series if it is
  too big", while `increase_pure` "assumes that counters always start from 0".
  - llama.cpp counters start at 0 when the server starts, and their series exist
    only while the model is loaded, so a cold-start run's series begins inside
    its window.
  - On the local stack, both functions gave 17,784 and 10,714 over the
    2026-10-22 session window (series starting at 0), and 9,298 over a window
    inside a running series.
- **D6, delegation activation** (owner, 2026-10-22, explicit yes). The
  orchestrator may qualify `local-qwen38-flash-next`, add this checkout's
  `[[project]]` entry to the machine-wide delegate profile, and activate it
  (`policy = "auto"`), showing the profile change. Deactivation is the owner's
  call at any time.

- **D7, test-first lint and commits** (owner, 2026-10-22, "yes to all").
  - `pyproject.toml` gains `[tool.ruff.lint.isort] known-first-party =
    ["agentic_telemetry"]`. This is a scope expansion beyond the plan's "no
    `pyproject.toml` edit". Without it, ruff classed the not-yet-written
    `server_metrics` imports as third-party (9 × I001), and the pre-commit
    hook would block the commit.
  - The owner approved a work-in-progress commit of the spec and tests on this
    branch (the handoff worktree starts from `HEAD`), and the handoff's
    temporary commit on `handoff/<run>`.
- **D8, review adjudication** (owner, 2026-10-22, answering the `[ask-user]`
  items from `/review` and `/review-adversarial` on the handoff). These supersede
  the plan where they differ:
  - **No start-side widening.** Every ranged query looks back `duration + 30 s`,
    evaluated at `end + 30 s`, instead of the plan's `duration + 60 s`.
  - **Strict inference.** Rule 3 applies only when the transcript recorded no
    names, and is kept only on an `exact` token match.
  - **Cold start.** `up` later is read over the same lookback at `end + 30 s`.
    `server_cold_start` is null when no `up` sample exists after the start.
  - **Mid-pass transport failures.** A refused or timed-out connection after the
    known-models read aborts the whole command (exit 1, nothing written), while
    an HTTP error status on one query stays that run's `error`. A test pins it.
  - **Rule 1's guard.** The orchestrator, on both reviewers' finding, also counts
    the ledger's `model_id` as a contradicting name. It only narrows rule 1.
  - **Windowless runs.** A run with no usable window stays `no_data` with
    source `none`: it is never read, so it is not resolved.

## External references

- **VictoriaMetrics HTTP API and MetricsQL** (VictoriaMetrics, Apache-2.0),
  retrieved 2026-10-22:
  - "API examples", https://docs.victoriametrics.com/victoriametrics/url-examples/ :
    `/api/v1/query`, and `/api/v1/label/…/values` "returns label values seen
    during the last day, starting at 00:00 UTC", with `start` and `end` to
    widen it. `match[]` on label values is not on that page. It was confirmed
    live on the local stack (2026-10-22) and is part of Prometheus' label-values
    API (below).
  - "MetricsQL", https://docs.victoriametrics.com/victoriametrics/metricsql/ :
    `increase` "takes into account the last sample before the given lookbehind
    window"; MetricsQL "doesn't extrapolate rate and increase"; `increase_pure`
    "assumes that counters always start from 0"; and `max_over_time` and
    `last_over_time` operate on raw samples in the window.
  - "Single-node VictoriaMetrics",
    https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/ :
    samples close to the current time may be hidden (`-search.latencyOffset`).
    The `nocache=1` argument is not on the pages read. The plan's use of it
    rests on the local stack's behaviour, recorded in 0007's E2.
- **Prometheus HTTP API**, https://prometheus.io/docs/prometheus/latest/querying/api/
  (Apache-2.0): the independent source for the instant-vector response shape
  that the test stub copies. Fetched at test-first.
- **Metric names**: empirical, from the stored names recorded in
  `collector/README.md` (0003 for Strata, 0007 for llama.cpp).
- **llama.cpp gauge bucket reset**: llama.cpp `tools/server/server-context.cpp`
  at tag `b11552`, as cited in 0007's External references.


## Files to touch

The implement phase's scope, as full paths, taken from the approved plan below
(D4). Docs come afterwards and are the orchestrator's.

- `src/agentic_telemetry/server_metrics/__init__.py`
- `src/agentic_telemetry/server_metrics/http_fetch.py`
- `src/agentic_telemetry/server_metrics/victoria_client.py`
- `src/agentic_telemetry/server_metrics/window.py`
- `src/agentic_telemetry/server_metrics/readings.py`
- `src/agentic_telemetry/server_metrics/resolve.py`
- `src/agentic_telemetry/server_metrics/reconcile.py`
- `src/agentic_telemetry/server_metrics/columns.py`
- `src/agentic_telemetry/server_metrics/join.py`
- `src/agentic_telemetry/run_table/session_usage.py`
- `src/agentic_telemetry/run_table/row.py`
- `src/agentic_telemetry/run_table/parquet_writer.py`
- `src/agentic_telemetry/run_table/summary.py`
- `src/agentic_telemetry/cli.py`

## Approved implementation plan

**Approved:** 2026-10-22

This is the planner's plan, copied verbatim. Headings are demoted so they nest
under this section, and repo-absolute paths are shortened to repo-relative
ones. The owner approved it with every recommendation (Standing decisions
D1–D4):
- the transcript columns are always filled;
- `peak_turn_prompt_tokens` is added;
- the five interpretations are accepted;
- the implement phase goes to `local-qwen38-flash-next` through a delegate
  handoff, after the owner's explicit yes to activating delegation.

The planner's fact 1 also corrected the spec's `responseModel` claim (see Facts).

### Plan: 0005 Server-Metrics Join

Spec: `docs/specs/0005-server-metrics-join.md`

- **What changes outside `src/`:** no schema migration, no dependency, no `pyproject.toml` edit and no version bump. `collector/` is not touched, so nothing needs re-validating.
- **Parquet schema:** new columns are appended after `retries`. The file is rebuilt on every run, so there is nothing to migrate.
- **New code:** a subpackage, `src/agentic_telemetry/server_metrics/`, made of plain functions. The network seam is `Fetch = Callable[[str], bytes]`, passed in as an argument. Nothing monkeypatches urllib.

#### Facts found while planning (they change the design)

1. **Pi often leaves `responseModel` out.** Pi writes it only when the answering model differs from the requested one. The source is `session_table/pi.py:80-83`, citing Pi 1.3.0 `docs/message-types.md`. The spec says every assistant message carries it.
   - The plan follows the spec literally: `served_models` holds only the `responseModel` values that were recorded. When a transcript has none, it is `[]`.
   - In that case rule 2 (`requested`) resolves the run instead of rule 1 (`served`). Delegate runs request an alias, which differs from the served ID, so Pi records the served name there.
2. **The label-values endpoint looks back one day by default.** VictoriaMetrics' `/api/v1/label/<name>/values` covers only the last day from 00:00 UTC unless `start` and `end` are passed, and it rounds them to whole days. Without them, older runs would come out `uncovered` instead of `no_data`. This came from a search of VictoriaMetrics doc mirrors, not the official site. Step 2's fetch must confirm it.
3. **The label-values call must cover old runs.** It passes `start` = the earliest run start in the table minus 30 s, `end` = now, and `match[]=up{service_name="inference-models"}` (the agent's job name, from `collector/inference-agent/otel-agent.yaml`). The known set is then every model the agent has ever targeted, so old runs become `no_data`.
   - That set also contains the throwaway `exp-*` IDs from 0007's E1 experiment. They are harmless: they have no `llamacpp_*` series, so they can never be inferred.
4. **Every query gets the same series filter.** All `llamacpp_*` and `up` selectors carry `{service_name="inference-models"}`. That keeps out 0003's temporary-capture series, which also carry `model_id`.

#### Files to touch

**New: `src/agentic_telemetry/server_metrics/`.** Estimated lines are in brackets; every file stays under 300.

- `__init__.py` [~3]: docstring only.
- `http_fetch.py` [~80]
  - Contents: the `Fetch` alias, `MetricsUnreachableError` and `MetricsQueryError`.
  - `urlopen_fetch(url: str, timeout: float = 10.0) -> bytes`.
    - It checks again that the scheme is http or https, then calls `urlopen` with a `# noqa: S310` and a justification.
    - It turns `HTTPError`, `URLError`, `TimeoutError` and `OSError` into the errors above. Each error message names the URL **path** only.
  - Header comment: VictoriaMetrics docs URL, retrieval date and Apache-2.0, from step 2.
- `victoria_client.py` [~150]
  - Constants: `LABEL_VALUES_PATH = "/api/v1/label/model_id/values"` and `QUERY_PATH = "/api/v1/query"`. `query_range` is **not used**; say so in the module docstring.
  - `known_model_ids(base_url, fetch, *, start, end) -> frozenset[str]` sends `match[]`, `start` and `end` (unix seconds).
  - `instant_by_model(base_url, fetch, query, at) -> dict[str, float]` sends `query`, `time=<unix seconds>` and `nocache=1`.
  - Response parsing:
    - it expects `{"status":"success","data":{"resultType":"vector","result":[{"metric":{"model_id":…},"value":[ts,"<str>"]}]}}`;
    - `"NaN"` or `"±Inf"` means the model is absent;
    - any other status, non-JSON or a wrong shape raises `MetricsQueryError(path, detail)`;
    - it parses through `run_table/json_values.py` helpers, so no `Any` reaches mypy.
  - Header comment: same provenance as `http_fetch.py`.
- `window.py` [~90]
  - `RunWindow(start: datetime, end: datetime)`.
  - `start_stamp(run_id) -> datetime | None`, using the pattern `-(\d{8}T\d{6}Z)-\d+$`.
  - `run_window(run_id, ts, wall_seconds, agent_seconds) -> RunWindow | None`. The end is `ts`, parsed with `fromisoformat`; a naive time is taken as UTC. The start is the run-ID stamp if present, else `ts − wall_seconds`, else `ts − agent_seconds`. It returns None when there is no `ts`, no way to get a start, or the start falls after the end.
  - `widen(window, seconds)` and `overlaps(a, b)`. `overlaps` is strict, `a.start < b.end and b.start < a.end`, so back-to-back runs do not overlap.
  - `SCRAPE_INTERVAL_SECONDS = 30` and `SERVICE_NAME = "inference-models"`, with a comment that they mirror the agent config.
- `readings.py` [~130]
  - `WindowReadings`: one `dict[model_id, float]` each for prompt, predicted, prompt_seconds and predicted_seconds increases, deferred max, ctx high water, up at start, and up max later.
  - `read_window(base_url, fetch, window) -> WindowReadings` sends **8 instant queries per run**. Each query is grouped by model, so one query covers every model and also serves rule 3. Here `W` = window duration + 60 s, rounded up, evaluated at widened end `E` = end + 30 s:

    | Reading | Query | Evaluated at |
    |---|---|---|
    | prompt, predicted, prompt seconds, predicted seconds | `sum by (model_id) (increase(llamacpp_<name>{service_name="inference-models"}[Ws]))`, once for each of the four `_total` counters | `E` |
    | deferred max | `max by (model_id) (max_over_time(llamacpp_requests_deferred{…}[Ws]))` | `E` |
    | ctx high water | `max by (model_id) (last_over_time(llamacpp_n_tokens_max_total{…}[Ws]))` | `E` |
    | up at start | `max by (model_id) (last_over_time(up{…}[60s]))` | unwidened start |
    | up max later | `max by (model_id) (max_over_time(up{…}[Ds]))`, D = unwidened duration (at least 1 s) | unwidened end |

  - Query strings are module constants. Model IDs are never put into a query, so there is no escaping problem.
- `resolve.py` [~90]: `resolve_model(requested, served, ledger_model_id, known, moved) -> tuple[str | None, str]`. It is pure and applies rules 1 to 4:
  1. **`served`:** exactly one known served name, and no requested name is a *different* known ID.
  2. **`requested`:** exactly one distinct known ID in requested ∪ {ledger `model_id`}.
  3. **`inferred`:** exactly one known ID with a nonzero prompt or predicted increase.
  4. **`none`:** otherwise.
- `reconcile.py` [~50]: `token_match(tokens_in, tokens_out, server_prompt, server_predicted) -> str`.
  - `not_compared` when any input is None.
  - Otherwise `server_more` if the server is higher on **either** side, then `server_less` if lower on either side, else `exact`. A mixed result therefore counts as `server_more`.
- `columns.py` [~120]: the `ServerColumns` dataclass and `server_columns(readings, model_id, source, tokens_in, tokens_out) -> ServerColumns`.
  - Token columns are rounded to int.
  - Throughput is null when the denominator is 0 or absent.
  - Deferred max and ctx high water are BIGINT, null when the build lacks the metric.
  - `server_cold_start` = (up at start is 0 or absent) and (up max later = 1). It is null when the status is not joined, joined_approx or overlap.
  - The status before the overlap pass is decided in this order: `uncovered` (source `none`), then `no_data` (the resolved model is in neither increase vector), then `overlap` (`server_more`), then `joined_approx` (`server_less`), else `joined`.
- `join.py` [~140]: `join_server_metrics(rows, base_url, fetch, now) -> tuple[list[RunRow], ServerJoinReport]`.
  - It calls `known_model_ids` **once**. An unreachable URL, or a failure of that call, raises `MetricsUnreachableError` up to the CLI.
  - Per run, it computes the window and `read_window`, resolves and fills the columns.
  - A `MetricsQueryError` gives status `error`, null columns, and the warning `run <id>: query failed at <path>: <detail>`.
  - A run with no window gets `no_data` and a warning.
  - If the widened end is later than now − 60 s, it warns that VictoriaMetrics' `-search.latencyOffset` (30 s) plus one scrape may hide the last samples, and to rerun later.
  - Then the overlap pass: two joined, joined_approx or overlap runs that share the resolved model and have overlapping **unwidened** windows both become `overlap`.
  - It applies the results with `dataclasses.replace`.

**Edited source**
- `run_table/session_usage.py` (100 → ~140)
  - `SessionUsage` gains `requested_models` and `served_models` (`tuple[str, ...]`, distinct, first-seen across sorted files), plus the optional peak column (see Risks).
  - `_assistant_usage` is refactored to return the assistant message, so `model` and `responseModel` can be read from it.
- `run_table/row.py` (156 → ~185)
  - New fields after `retries`, in this order: `requested_models`, `served_models`, `peak_turn_prompt_tokens` (if accepted), `server_status`, `server_model_id`, `server_model_source`, `server_token_match`, `server_prompt_tokens`, `server_predicted_tokens`, `server_prefill_tps`, `server_decode_tps`, `server_deferred_max`, `server_ctx_high_water`, `server_cold_start`.
  - Server fields default to `server_status="not_queried"` and None for the rest, so `build_row` stays the same apart from the transcript fields.
  - The `server_ctx_high_water` comment says it is the largest context since the model loaded, not this run's.
- `run_table/parquet_writer.py`
  - Add `requested_models` and `served_models` to `LIST_COLUMNS`.
  - BIGINT: the token columns, deferred max, ctx and peak. DOUBLE: both tps columns. BOOLEAN: `server_cold_start`.
- `run_table/summary.py`
  - Add `server_status_counts` and `server_source_counts` (Counter).
  - Render them **only when queried**, one line each over the full vocabulary with zeros included: `server_status <status>: N` and `server_model_source <source>: N`.
  - Without the flag, stdout is the same as 0001.
- `cli.py` (186 → ~225)
  - Add `--metrics-url URL` with an argparse type `_metrics_url`. It rejects, with exit 2: a scheme other than http or https, a missing host, an unparsable port, and any query or fragment. It strips a trailing `/`.
  - The help text and `RUN_TABLE_DESCRIPTION` say the flag queries VictoriaMetrics read-only.
  - `main(argv=None, fetch: Fetch | None = None)`; it defaults to `urlopen_fetch`.
  - `_run_table`: build, then join if `--metrics-url` is set, then write. `MetricsUnreachableError` exits 1 before writing and names the URL.

**Tests** (only `tests/`, all synthetic; hosts are `telemetry.example`, `127.0.0.1` or RFC 5737)
- `tests/run_table_builders.py` (250 → ~260): `assistant_message(..., model=None, response_model=None)`. Keys are left out when None, so existing tests are unchanged.
- New `tests/vm_stub.py` [~130]
  - `FakeVictoria`: a callable `Fetch` that records the URLs it gets.
  - It dispatches on the path, and for `/api/v1/query` on the metric name inside `query`.
  - Builders: `vector_response({model_id: value})`, `label_values_response([...])` and `error_response()`. A raising mode simulates a failed query.
- New `tests/test_server_window.py`
  - The run-ID stamp wins over `ts − wall`.
  - `agent_seconds` is the fallback; a naive `ts` is UTC.
  - A missing or unusable `ts` gives None.
  - Widening is ±30 s, and the overlap check is strict.
  - Consistency: `SCRAPE_INTERVAL_SECONDS` and `SERVICE_NAME` match the agent config's `scrape_interval` and `job_name`, through `collector_files.AGENT_CONFIG`.
- New `tests/test_server_resolve.py` (parametrized)
  - One case for each of rules 1 to 4.
  - **The Maya case:** served `glm-5.3-flash`, requested `glm-5.3-flash-maya-s24`, both known, resolves to `requested` / maya-s24.
  - **The echoed alias:** served = requested = an alias. It falls to the ledger `model_id` (`requested`), else to `inferred`.
  - Two known served names fall to rule 2; two models moving gives `none`; a known ledger `model_id` alone gives `requested`.
- New `tests/test_server_reconcile.py`: `exact`, `server_more`, `server_less`, mixed (gives `server_more`), and `not_compared` for None inputs.
- New `tests/test_server_client.py`
  - The exact paths, plus `match[]`, `start`, `end`, `time` and `nocache=1`.
  - The 8 MetricsQL strings are pinned once.
  - NaN means absent; `status:"error"`, non-JSON and a wrong shape each raise `MetricsQueryError` naming the path.
- New `tests/test_server_http_fetch.py`
  - A loopback `http.server` on an ephemeral port returns the bytes.
  - A port that is bound and then closed raises `MetricsUnreachableError`.
  - A non-http scheme is refused.
- New `tests/test_server_join_status.py` (split as needed to stay under 300 lines). It covers every status:
  - `joined` (exact tokens);
  - `joined_approx`;
  - `overlap`, both through `server_more` and through two table runs on the same model;
  - `uncovered`;
  - `no_data`, an old run;
  - `error`, where the warning names the run and the path and not the host;
  - `not_queried`.
  - Missing ctx gives a null column with an unchanged status (Strata).
  - Zero seconds give null tps.
  - Cold start true, false, and null.
  - The label-values call happens once per invocation; window-less and recent-run warnings appear.
- New `tests/test_cli_metrics.py`
  - Malformed URLs exit 2: no scheme, `ftp://`, `http://`, a bad port.
  - Unreachable exits 1 and names the URL, and nothing is written.
  - With a stub `fetch`, the Parquet file has the server columns and the summary has the per-status and per-source lines.
  - **Without the flag:** every row is `not_queried`, all server columns are null, and stdout has no `server_` lines.
- Edit `tests/test_session_usage.py`: names in first-seen order across files, deduplicated; `[]` when names are absent; None when there is no transcript; the peak, if accepted.
- Edit `tests/test_parquet_writer.py`: extend `EXPECTED_COLUMNS`, `sample_row` and the type assertions. This is the **rejected-value search result**: the 40-column `EXPECTED_COLUMNS` list is the only test that pins 0001's exact output. The 0001 columns stay as an unchanged prefix. No other test pins the full row; `test_cli.summary_has` and `test_result_dir` check single fields.

**Docs**
- `AGENTS.md`
  - How to run things: add `uv run agentic-telemetry run-table --metrics-url http://127.0.0.1:8428 --out data/run_table.parquet DIR...`. The address is loopback to the local stack.
  - At closeout, update the current-state block.
- `README.md`: one usage line for the flag, plus a short note on the server columns, including the `server_ctx_high_water` caveat the spec asks for.
- `analysis/README.md`, the join section (lines 31–35): replace the stale "Collector scrapes each llama.cpp server's /metrics (router mode…)" with the 0007 agent and the 0005 columns and statuses.
- `docs/specs/0005-server-metrics-join.md`
  - At approval: the plan, `shipping`, External references pinned (URL, date, Apache-2.0), and the accepted interpretations below.
  - At closeout: the live-check counts, then `shipped`, and regenerate the `docs/specs/README.md` dashboard.

#### Order of operations
1. **Orchestrator.** Settle the `[ask-user]` items, persist the plan and set the spec to `shipping`. Confirm the branch is `spec-0005-server-metrics-join`.
2. **Provenance, before test-first.** Fetch the official VictoriaMetrics docs from https://docs.victoriametrics.com/ in this session:
   - the HTTP API and URL-examples page (`/api/v1/query`, `/api/v1/label/…/values`, with `match[]`, `start`, `end` and the 1-day default);
   - the MetricsQL page (`increase`, which uses the sample before the window and does not extrapolate; `max_over_time`; `last_over_time`; `sum by`);
   - the single-node page (`-search.latencyOffset` and per-query `latency_offset`; `nocache=1`).
   - Pin the URLs, the date and Apache-2.0 in the spec and the module headers. For the independent fixture rule, also fetch Prometheus' HTTP API page (https://prometheus.io/docs/prometheus/latest/querying/api/, Apache-2.0) for the vector response shape that `vm_stub.py` copies.
   - If a doc contradicts this plan (for example, `increase` ignores the first sample of a series that starts mid-window), stop and re-plan the affected columns.
3. **`/test-first`** (delegate) on the test list above.
   - Expected red: `ModuleNotFoundError` for `agentic_telemetry.server_metrics.*`, a `TypeError` or `AssertionError` on the new `SessionUsage`/`RunRow` fields, and an `AssertionError` on the CLI flag.
   - Audit the fingerprints (only `tests/` changed), then rerun the focused tests.
4. **Implement, bottom-up**, running each focused test as you go: `http_fetch` → `victoria_client` → `window` → `readings` → `resolve` → `reconcile` → `columns` → `join` → `session_usage` → `row` → `parquet_writer` → `summary` → `cli`.
5. **Docs** (above).
6. **Gate:** `.agentic/toolchain.sh gate`, then `git grep -nE '/(Users|home)/' -- src tests` and a scan of `tests/` for hostnames that are not `.example`.
7. **Live check, owner.**
   - With the 0007 agent up, make one delegate run on Strata or llama.cpp. Then run `uv run agentic-telemetry run-table --metrics-url http://127.0.0.1:8428 --out data/run_table.parquet <project>` at least 60 s after the run ends.
   - The orchestrator records only: the counts per status and per source, that run's `server_status`, `server_model_source` and `server_token_match`, and whether its throughput columns are null or non-null. No values.
   - Also run once without the flag and confirm stdout has no `server_` lines.
8. **Closeout:**
   - mark the spec `shipped`, regenerate the dashboard, and update the AGENTS.md current-state block;
   - run `.agentic/hooks/closeout-check.sh`;
   - `/review-check`, then `/review` and `/review-adversarial` (network input and a new HTTP surface);
   - stop for commit approval.

#### Risks / open questions
- **[ask-user] Transcript columns without the flag.** The spec says "without the flag ... the new columns are null". `requested_models` and `served_models` need no network. *Recommendation:* always fill them, null only when there is no transcript, and keep the null-without-flag rule for the `server_*` columns only. The alternative is gating them on the flag, which is literal but loses free data.
- **[ask-user] Peak context column.** *Recommendation: yes*, as `peak_turn_prompt_tokens` = max over assistant turns of `input + cacheRead + cacheWrite`.
  - It comes free from the same transcript pass, needs no network, and gives the run's own context, which `server_ctx_high_water` cannot.
  - Pi's `input` appears to exclude cached prefix tokens: it matched llama.cpp's processed-prompt counter exactly in the spec's Facts. So `max(input)` alone, the spec Note's literal wording, would understate context under caching.
  - It needs a spec criterion line at approval. If declined, drop it from `SessionUsage`, `RunRow` and the tests.
- **[ask-user] Interpretations to record in the spec at approval** (the defaults are above):
  - a mixed token result counts as `server_more`;
  - overlap uses unwidened windows;
  - a run with no window is `no_data` with a warning;
  - rule 2's candidate set is requested ∪ ledger `model_id`;
  - server token columns are BIGINT, rounded.
- **Pi's `responseModel` is sparse** (fact 1). `server_model_source` will often be `requested` where the spec's facts imply `served`. The live check shows which.
- **Recent runs.** VictoriaMetrics' latency offset hides about the last 30 s. The disk-queued agent backfills late after the Mac sleeps. Both can give `server_less` or `no_data` until a rerun. The warning plus the README note cover this.
- **Prefix caching and reasoning tokens.** Whether Pi's `output` includes reasoning, and whether `input` matches `prompt_tokens_total` under caching, is unverified beyond one session. Reconciliation shows it; it does not fix it.
- **`increase` across an unload or reload.** The series resets to 0 on reload, which VictoriaMetrics treats as a counter reset. That works if the docs confirm it (step 2), and the spec's cold-load fact matched exactly.
- **Query count.** 8 instant queries per run over loopback is negligible at this table's size (tens of runs). Combining them with `union` was rejected for readability.

#### Out of scope (won't touch)
- `collector/` (no config edits, so no re-validation), `pyproject.toml`, `uv.lock`, CI.
- Server columns for the session table, `/slots` polling, llama-swap log capture, and splitting metrics between overlapping runs. These are spec non-goals.
- A `query_range`-based swap-wait measurement, and window start and end columns. Both are useful, but not in the spec; raise them separately if wanted.
- `THIRD_PARTY_NOTICES.md`: nothing is copied. The query strings are original and the docs are cited only.

## Phase handoff

### Test-first (2026-10-22)

- **Files.**
  - New: `tests/vm_stub.py` (its response shape follows Prometheus' HTTP API
    docs), `tests/server_join_helpers.py`, and `tests/test_server_{window,
    resolve,reconcile,client,readings,http_fetch,join_status,join_columns,
    join_calls}.py`, `tests/test_cli_metrics.py` and
    `tests/test_run_table_model_names.py`.
  - Edited: `tests/run_table_builders.py`, `tests/test_session_usage.py` and
    `tests/test_parquet_writer.py`.
- **Scope audit.** Clean. Against the pre-phase fingerprints, only `tests/`
  changed, apart from the orchestrator's own spec edit (the `## Files to touch`
  section).
- **Red, re-run by the orchestrator:** 34 failed, 9 collection errors and 418
  passed.
  - The 9 collection errors are `ModuleNotFoundError: agentic_telemetry.server_metrics`.
  - The other failures are `TypeError` (`main()` has no `fetch`; `RunRow` lacks
    the new fields), `AttributeError` (the new `RunRow` and `SessionUsage`
    fields), one `KeyError` (a Parquet column) and `AssertionError`s (column
    order, help text).
  - Every untouched test passes.
- **Oracle check.** The test-first agent's throwaway reference implementation
  passed all 575 tests, and 11 of 12 deliberate mutations failed, including
  `increase` instead of `increase_pure`. The twelfth was a gap in the experiment,
  not the tests. The reference was deleted before the handoff.
- **Rejected-value search.**
  - The full column list is pinned only by `tests/test_parquet_writer.py`
    `EXPECTED_COLUMNS`. It was extended with an appended suffix, so 0001's columns
    are an unchanged prefix.
  - `increase(` appears only in the new guard asserting its absence.
  - `tests/test_session_parquet_writer.py` is the session table's and is
    unchanged.
- **API the tests pin beyond the plan's text:**
  - `ServerJoinReport.warnings`;
  - `MetricsUnreachableError(message)`;
  - aware datetimes for `start`, `end` and `at`, sent as unix seconds;
  - `moved` as a collection of IDs;
  - summary lines `server_status <s>: N` and `server_model_source <s>: N`, zeros
    included;
  - a malformed URL exits 2 naming `--metrics-url`, with no request made.

### After handoff (implement, 2026-10-22)

- **Ran through the delegate handoff on `local-qwen38-flash-next`** (run
  `handoff-0005-server-metrics-join-20261022T042448Z-52656`). Per D4, that run
  is the live check's delegate run. The runner committed the verified result on
  `handoff/<run>`.
  - *Corrected by the orchestrator.* The session first wrote that it had not
    run through a delegate handoff and that nothing was committed. It misread
    D4 in its own brief.
- **Gate green** through `.agentic/toolchain.sh`: lint, format-check, typecheck
  (38 files), 575 tests. No test file was edited. No file outside the spec's
  `## Files to touch` was changed; the plan's Docs entries (AGENTS.md,
  README.md, `analysis/README.md`) are the orchestrator's and were left alone.
- **Files written.** New subpackage `src/agentic_telemetry/server_metrics/`
  (`http_fetch`, `victoria_client`, `window`, `readings`, `resolve`,
  `reconcile`, `columns`, `join`; largest file 226 lines), plus the planned
  edits to `run_table/session_usage.py`, `run_table/row.py`,
  `run_table/parquet_writer.py`, `run_table/summary.py` and `cli.py` (263
  lines, against the plan's estimate of 225; under the 300-line rule).
- **Choices made where the spec left room** (none changes the approved plan):
  - rule 3's moved-model set is `WindowReadings.moved_models()`, a method on the
    readings rather than the plan's sketched free function; `resolve_model` still
    takes it as a plain collection of IDs.
  - `MetricsQueryError` carries `path` and `detail` separately, so the join's
    warning reads `run <id>: query failed at /api/v1/query: <detail>` and names
    the path but never the host. `_without_host` in `http_fetch.py` also strips
    the netloc, host and port out of transport-error text.
  - a transport failure (`MetricsUnreachableError`) during a run's own queries
    stops the whole command, like a failed known-models read: exit 1, nothing
    written. A per-query `MetricsQueryError` stays per-run and gives that run
    status `error`. The plan pinned only the known-models case.
  - a run with no usable window gets `no_data` and resolves no model, so its
    source is `none`: with no window nothing can be read, and naming a model
    that nothing could confirm would suggest a join that did not happen.
  - `columns.unread(status, model_id, source)` fills `not_compared` plus nulls
    for `uncovered`, `no_data`, `error` and the windowless case, so the plan's
    "null columns, status unchanged" rule is one function rather than four.
  - `SessionUsage` accumulation moved into a private mutable `_Tally`: one pass
    over the transcripts now fills totals, names and the peak turn context. The
    plan asked only that the assistant message be returned rather than its usage
    object; the malformed-line warning text is unchanged.
  - `Summary` gained `server_queried` beside the two counters and renders the
    `server_` lines only when it is set, so stdout without the flag is what 0001
    printed.
  - join warnings are deduplicated in first-seen order, as build.py already does.
  - `_metrics_url` checks scheme, then host, then port, then query and fragment,
    and strips any trailing `/`.
- **Left for the orchestrator:** the plan's Docs edits, the live check, then
  `shipped` and the dashboard. No test looked wrong to implement against.


### Implement, review and live check (2026-10-22)

- **Handoff.** `local-qwen38-flash-next` implemented the phase in about 16
  minutes, with exit 0. The runner's independent full gate was green (575
  tests), the plan-scope check passed, and no test was edited. The
  orchestrator squash-merged it onto this branch.
- **Reviews.**
  - `/review` recommended `pass`.
  - `/review-adversarial` found the start-side double count and the
    neighbour-inference flaw, both gaps in the spec and plan, which the stub's
    time-blind answers could not expose. The owner settled them as D8.
  - The session's own defects, all fixed by the orchestrator:
    - a made-up `cacheWrite` rationale in a comment;
    - `http.client.HTTPException` and `ValueError` escaping `urlopen_fetch`;
    - `_without_host` mis-parsing IPv6 and userinfo;
    - credentials accepted in `--metrics-url`;
    - loose wording in the rerun warning;
    - the false provenance bullet above.
  - The orchestrator added tests for D8, credentials (including that no secret
    is echoed) and a non-HTTP port; each was red first. It also pinned
    behaviour the handoff already had: HTTP 500 is `MetricsQueryError`,
    `file://` is `MetricsUnreachableError`, and a mid-pass transport failure
    aborts the command.
  - **Verdict:** `record-review ... model-defect`, because the findings
    included defects in the session's own code. The runner then demoted
    `local-qwen38-flash-next` for `phase:implement` (0/1 passed). Undoing that
    is the owner's call (`delegate-run.sh reset local-qwen38-flash-next
    phase:implement`).
- **Gate:** green, 585 tests.
- **Live check** (counts only).
  - `run-table --metrics-url http://127.0.0.1:8428` over this checkout gave one
    row: the handoff run, `joined`, source `served` (requested alias, served
    llama-swap ID), `server_token_match = exact`. Both throughput columns were
    filled, `server_ctx_high_water` was null (Strata has no `n_tokens_max`),
    `server_cold_start` was false (qualification had loaded the model), and the
    peak column was filled.
  - Over the owner's two private projects plus this checkout: 16 rows, 1
    `joined` and 15 `uncovered` (Maya runs; Maya has no metrics). The one
    warning is 0001's existing orphaned-result-directory warning.
  - Without the flag, stdout has no `server_` lines.
