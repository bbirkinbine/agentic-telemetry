# 0008 — Local-Model Request and Response Capture

**Status:** shipping
**Last updated:** 2026-10-22
**Depends on:** 0006
**Roadmap unit:** 5

## Goal

Keep the exact request and response of every call my agents make to my local
models, so I can see what a model actually received on each turn and what it
returned. Local models are what I benchmark: I tune the harness (agentic-scaffold)
against them and decide which model is better for which task. A transcript shows
the conversation; it does not show:

- the system prompt and tool definitions the client sent;
- the history the client re-sent after compaction or truncation;
- the sampling settings;
- the raw response stream.

Those are where packet, harness and template problems show up.

Capture covers local models only. Frontier models (Claude, Codex) stay on
transcripts: their requests can be captured only on the client side, and the
provider's side (added content, full reasoning, the rendered prompt) stays
hidden anyway.

### Facts checked on 2026-10-22 (llama-swap v264, MIT, released 2026-10-12)

Read from the source and docs at tag `v264`; no capture has been taken yet.

- `captureBuffer` (MB, default 10, `0` disables) makes llama-swap store a
  request/response capture for each proxied call. A capture holds the request
  path, request headers, request body, response headers and response body, with
  sensitive headers replaced by `[REDACTED]`. Chat and completion routes capture
  all four by default. A failed (non-200) request keeps everything except the
  response body; a client disconnect or an empty response keeps no capture.
  Bodies are bytes, so the capture's JSON carries them base64-encoded; headers
  keep one value each under Go-canonical keys.
- Captures live in an in-memory, size-bounded cache, compressed (zstd over
  CBOR), and the oldest are evicted first. The optional SQLite `store.path`
  persists the activity rows but not the captures. Anything not fetched before
  eviction is lost.
- `GET /api/metrics/activity` lists activity rows: `id`, `timestamp`, `src`,
  `model`, `req_path`, `resp_status_code`, nested `tokens`, `duration_ms`,
  `has_capture`, `metadata` (`timestamp` is the call's end). It is paged
  (`data`, `page`, `limit`, `total`, `total_pages`). Without `store.path` the
  in-memory list keeps only the newest `metricsMaxInMemory` rows (default 1000). `GET /api/captures/{id}` returns one capture. Both are in the API
  group, which requires a key when llama-swap's `apiKeys` is set.
- `ui.activity.session_id` names request headers (default `X-Session-ID`) that
  identify a session in the in-flight view only; the value does not reach the
  activity row's `metadata`. The capture's request headers do keep it.
- Pi 1.3.0 sends a session ID header to a model only when that model's
  `compat.sendSessionAffinityHeaders` is true (default: OpenRouter only):
  `x-session-id` with `sessionAffinityFormat: "openrouter"`, otherwise
  `x-client-request-id` and `x-session-affinity`.
- Only `/upstream/...` requests reset a model's TTL (spec 0003, D6), so reading
  `/api/...` should not keep a model loaded. This is to be confirmed on the host.

## Success criteria

- **Capture on.** llama-swap on the inference host runs with `captureBuffer`
  sized so that a normal day loses no capture to eviction. It starts at 1 GB, so
  the buffer also covers a long harvester outage (a VM reboot or a day of
  maintenance), and is resized from the measured eviction rate. llama-swap also
  gets `store.path` (SQLite), so the activity list is not cut to the newest 1000
  rows during an outage and row ids survive a llama-swap restart. That file grows
  without bound on the inference host; pruning it is decided with capture
  retention. The settings are
  an owner action done through the inference host's sysadmin agent; the
  repository records the value and the reason in `collector/README.md`.
- **Harvester.** A process on the always-on side fetches every new capture before
  it is evicted. It keeps a cursor of the last activity `id` saved, reads new
  rows from `/api/metrics/activity`, fetches `/api/captures/{id}` for each row
  with `has_capture`, and appends one record per call to a daily file
  `captures/<host>/<YYYY-MM-DD>.jsonl.zst`. Each record holds the activity row and
  the capture as llama-swap returned it, nothing rewritten. The harvester is a
  long-running `agentic-telemetry captures-pull` service in the telemetry VM's
  compose stack, polling every 15 s with restart `unless-stopped`. Its image is
  built from a digest-pinned Python base plus this package, so deploying it adds
  the package source to what is copied to the VM.
- **No silent loss.**
  - The harvester reports per poll how many rows it saved, and how many it
    could not fetch because they had been evicted (rows with `has_capture` whose
    capture returned 404).
  - If row ids go backwards (the activity store was wiped, moved, or llama-swap
    ran without `store.path`), the harvester detects it (an `id` at or below its
    cursor with a newer timestamp) and starts a new cursor instead of skipping
    rows.
  - An unreachable llama-swap is logged and retried. It never crashes the
    service.
- **Coverage.** Captures arrive for every backend the owner uses behind
  llama-swap: llama.cpp, Strata and Project Maya, including Maya, whose
  `/metrics` answers JSON rather than Prometheus text (0007 left it uncovered). The Sybil build is out of scope: the
  owner does not use it (2026-10-22). Clients that call a model server's port directly
  bypass llama-swap and are not captured; `collector/README.md` says so.
- **Streaming.** A streamed (SSE) response is kept as the raw event stream. A
  reader reassembles it into the final message, tool calls and usage, and a test
  pins that reassembly on synthetic streams.
- **Per-session view.** Calls join to sessions, not only to delegate runs, so a
  session where the local model is itself the orchestrator is covered.
  - Any session-table session that used a local model qualifies, whatever the
    client: an orchestrator, a delegate run or a subagent. Calls are joined by
    time window and model, using 0005's model resolution.
  - For a session made after deployment, `agentic-telemetry captures show
    --session ID [--call N]` lists its model calls in order (time, model,
    prompt and output tokens, duration) and prints any one call's full request
    and reassembled response. `captures show RUN_ID [--call N]` finds a delegate
    run's session and shows the same.
  - A call inside a child (subagent) session's window goes to the child, the
    innermost session: a Pi parent waits on its `subagent` tool call and makes
    no model call meanwhile.
  - When two sessions that are not parent and child overlap on the same model,
    the call is listed under each, flagged `ambiguous`.
  - A call that no session claims is still listed by time.
  - When the first non-empty of a capture's `x-session-id`,
    `x-session-affinity` and `x-client-request-id` request headers (in that
    order, any case) matches a session-table `session_id`, that session claims
    the call exactly, ahead of
    the time-and-model rule and without an `ambiguous` flag. Without the
    header, time and model decide. No scaffold change: the owner may turn on
    Pi's `compat.sendSessionAffinityHeaders` for local models in Pi's own model
    settings.
- **Never in tracked files or Loki.**
  - Capture files stay under the stack's data root and are never committed.
  - The Collector does not tail them.
  - Tests use synthetic captures only.
  - No API key appears in a tracked file; the harvester reads it from the
    untracked `.env`.
- **Check on the host.** One supervised session confirms that reading the API
  does not reset a model's TTL (as 0007 did for scrapes), and records counts only
  in `collector/README.md` → Verification.

## Non-goals

- Request or response capture for frontier models (Claude Code, Codex, Pi
  against a hosted API). Transcripts cover them.
- The rendered prompt (the chat template applied by llama.cpp). It is the next
  layer down and the one that shows template bugs. It needs a server-side source
  such as llama.cpp's `/apply-template`, and it belongs in a later spec.
- A browsing UI. The review app (roadmap unit 4) can read these files later.
- Redacting request or response bodies. They hold code and whatever reached a
  prompt. They stay on the owner's homelab storage, outside Loki and git.
- Changes to the scaffold, such as a run-ID header (see Notes).
- Pruning capture files. Retention is decided once real sizes are known.

## Notes

- **Why this is cheap:** llama-swap already captures. The work is fetching
  captures before eviction, storing them as files, and joining them to runs. No
  proxy is added in front of the models.
- **Join precision:** time window plus model is exact when one session uses a
  model at a time. With concurrent sessions on one model it is ambiguous, and the
  call is flagged. A session ID header in the capture makes it exact (see
  Per-session view). Whether Pi's header value equals the transcript's session
  `id` is checked on the host after Pi's setting is turned on; a run-ID header
  from the scaffold's delegate runner is a later change.
- **Size:** every turn re-sends the whole conversation, so capture volume grows
  with history length. Measure a day's compressed size before choosing retention.
- **Spec numbering:** 0006's clarify pass called the inference-host installer
  "spec 0008" before this spec existed. The installer takes the next number when
  it is drafted.

## External references

- **llama-swap** v264 (MIT): `docs/config.example.yaml` (`captureBuffer`,
  `store`, `ui.activity.session_id`), `internal/server/captures.go`,
  `internal/server/metrics.go`, `internal/server/server.go` (API routes) and
  `internal/store/activity.go` (activity row fields), at
  https://github.com/mostlygeek/llama-swap/tree/v264. Read in-session
  2026-10-22. Field names are re-checked against the deployed version at plan
  time.

## Approved implementation plan

**Approved:** 2026-10-22

The planner's first draft was revised before approval, once the owner chose
`store.path` (D1) and the session-header match (D2); only this revision was
approved.

Amended 2026-10-22 by the owner, before test-first: `captures show` prints
`src` (the client address) in the call list and the call detail; captures stay
in the lab, so nothing in them is hidden from the owner. Where the plan below
says `captures show` never prints `src`, this amendment governs. The harvester's
log lines still carry counts only.

Also added at the owner's request (2026-10-22): an `## Upstream dependencies`
section in `docs/specs/0000-product.md`, a file the plan below does not list.

### Plan: 0008 Local-model request and response capture (revised for D1 = `store.path`, D2 = header match)

Both decisions are folded in and nothing is left open; one spec bullet still needs rewording (first item below).

#### Changes from the first plan

- **Spec wording to fix.** "No silent loss", second bullet, still says "A llama-swap restart resets `id`". With `store.path` it does not.
  - Proposed wording: "If row ids go backwards (the activity store was wiped, moved, or llama-swap ran without `store.path`), the harvester detects it (an `id` at or below its cursor with a newer timestamp) and starts a new cursor instead of skipping rows."
  - The code does not change: the rule stays as a safety net.
- **The restart rule is now a safety net.** With SQLite, ids survive a llama-swap restart, so in normal operation the rule never fires. It is kept, unchanged, for a wiped or moved store or a fall-back to in-memory.
  - The epoch is kept in the cursor and in each record. It costs one integer, and it tells apart two id sequences if ids are ever reused.
  - Duplicates are still dropped by `(activity.id, activity.timestamp)`, so dropping them does not depend on the epoch.
- **A restart still loses captures.** `store.path` keeps the activity rows, but the capture cache is in memory, so a restart empties it. Unfetched rows from before the restart come back with `has_capture: false`.
  - They are counted as `no_capture`, not `evicted`. The README says that `no_capture` includes rows lost to a llama-swap restart.
- **First run backfills the whole store.** With no cursor file, the first poll walks every page of the SQLite store and saves each row. Old rows are saved with `capture: null` and `capture_status: none`.
  - That happens once. Afterwards, a poll reads only the newest page or two.
- **Pagination stays a descending walk.** Order is descending by id, and the walk continues until a page reaches `id <= last_id`.
  - With a live SQLite store, a row inserted while the harvester is paging shifts later pages down by one. In descending order that only repeats a row (removed by id within the poll); it never skips one.
  - Ascending paging from `min_id` would be stable too. It is not used, because step 0 has not yet confirmed that `min_id` is a query parameter, and the descending walk works either way.
- **The header match (D2) is now in scope, ahead of the time-and-model rule.**
  - The header value is the first non-empty one of `x-session-id`, `x-session-affinity`, `x-client-request-id`, matched without regard to case.
  - It is taken from the capture's `req_headers`. A match claims that session exactly, with no `ambiguous` flag.
  - A header present that matches no session falls back to time and model, and the call is flagged `header_unmatched`. That flag makes the spec's host check (does Pi's header value equal the transcript `id`?) visible.
- **Owner checklist.** `store.path` replaces `metricsMaxInMemory`, and both settings are applied before the harvester is deployed. The store's growth is recorded, and backing up or pruning the store stays out of scope.
- **`captures show` never prints `src`.** It holds client IPs; a list now persists them in SQLite and in the capture files.

#### Where the v264 source disagrees with the spec (still open)

The owner's edit already corrected most of the Facts section. What remains:

1. **The handlers are not in the fetched files.** `handleAPIActivity`, `handleAPICapture`, `CreateAuthMiddleware` and request-header redaction are defined in none of the five. Still unpinned:
   - the query parameter names and the largest `limit`;
   - the status for a missing capture;
   - whether the capture is JSON-encoded;
   - which header carries the auth key.

   Step 0 retrieves the file(s) that define them.
2. **The SQLite store's behavior is unread.** Its id assignment (AUTOINCREMENT or plain rowid) and how it stores timestamps are not in the fetched files. Neither matters while nothing deletes rows, and with `store.path` nothing prunes. Step 0 notes the answer if the repository file is in reach.
3. **`/upstream/...` requests are captured too**, because the upstream chain includes the metrics middleware (`server.go:385-391`). This is harmless.
4. **No `apiKeys` on the host today, as far as the evidence shows:** the Collector scrapes `/metrics`, which sits behind the same auth middleware, without a key. So the key stays optional, used only when `LLAMA_SWAP_API_KEY` is set.

#### Decisions made (details)

- **Cursor.** The cursor is `{epoch, last_id, last_ts}` in `captures/<host>/cursor.json`, written atomically (temp file, then rename).
  - A row is new when its `id > last_id`. An id cursor, because concurrent requests can insert slightly out of timestamp order.
  - **Safety-net rule:** if any listed row has `id <= last_id` and `timestamp > last_ts`, the id sequence was reset. The harvester sets `epoch += 1`, `last_id = 0`, and logs `id reset detected`.
- **Records** (see Review resolutions: Stuck capture, Malformed row, for the `error` status and `estimated_end`). One JSON line per activity row, including rows without a capture: `{"activity": <verbatim>, "capture": <verbatim | null>, "capture_status": "saved"|"evicted"|"none", "harvested_at", "epoch"}`.
  - The file is `captures/<host>/<UTC date of activity.timestamp>.jsonl.zst`.
  - Each poll appends one zstd frame per file and fsyncs before the cursor is written.
  - Readers open with `read_across_frames=True`. `session_table/jsonl_lines.py` does not, so it is not reused.
  - Readers drop duplicates by `(activity.id, activity.timestamp)`.
- **Polls.**
  - 404 on a capture fetch: counted as `evicted`, and the row is saved.
  - `has_capture: false`: counted as `no_capture` (eviction before listing, a llama-swap restart, a client disconnect or an empty body).
  - Unreachable host or 5xx: one log line with the host scrubbed, then sleep and retry; the service never exits.
  - A failure partway through a poll: the cursor advances only past rows that were fully handled.
  - One line per poll that saved or lost anything: `poll: rows=N saved=S evicted=E no_capture=K id_reset=0|1`, plus a heartbeat every 40 quiet polls. No content, no host, no `src`.
- **Join** (pure). The rules apply in order:
  1. **Header match.** The capture's `req_headers` hold one of the three session headers, and its value matches a session-table `session_id`: that session claims the call. No `ambiguous` flag, no window check.
  2. **Time and model.**
     - A call's model names are `activity.model` plus the request body's `model`.
     - A session's model set is `resolve_model(models, served_models, None, known=all call models, moved=())`, from `server_metrics/resolve.py`. If that resolves nothing, the set is the session's requested names that are in `known`.
     - A session claims a call when the call's `[timestamp − duration_ms, timestamp]` overlaps the session's `[started_at − 5 s, ended_at + 5 s]` and the model sets meet.
     - Claimants that are ancestors of another claimant (`parent_session_id` chain) are dropped, so the child wins.
     - More than one claimant left means the call is listed under each, flagged `ambiguous`.
  3. **No claim.** The call is listed as unclaimed by `--date`.

  A header present that matched no session in rule 1 adds `header_unmatched` to whatever rule 2 decides.
- **`captures show` options.** It runs on the owner's Mac against a copy of `captures/` under the ignored `data/`.
  - `--captures DIR` and `--session-table PATH` are required.
  - The selector is one of `--session ID`, a positional `RUN_ID`, or `--date YYYY-MM-DD`. `--call N` picks one call.
  - The list columns: time, model, prompt and output tokens, duration, status, flags. Each child session gets one summary line.
  - `RUN_ID` resolves to the session with that `run_id` and `is_subagent = false`. Several matches is an error that lists them.
- **SSE reassembly** targets OpenAI chat-completions:
  - events split on blank lines, multi-line `data:`, CRLF, `[DONE]`;
  - `delta.content` and `reasoning_content`/`reasoning`;
  - `tool_calls` merged by `index`;
  - `finish_reason`, `usage` and `timings`;
  - a stream without an end is flagged `incomplete`;
  - non-stream JSON is passed through, and other routes are shown raw.
- **Harvester CLI.** `agentic-telemetry captures-pull --url URL --host-label LABEL --out DIR [--interval 15] [--once]`.
  - The key comes only from the `LLAMA_SWAP_API_KEY` environment variable.
  - The URL validator rejects credentials, queries and fragments.
- **Image.** `collector/captures/Dockerfile`:
  - `FROM python:3.12-slim-<debian>@sha256:<index>`, plus `COPY --from=ghcr.io/astral-sh/uv:<tag>@sha256:<index>`. Both digests are read with `docker buildx imagetools inspect` at implement time.
  - It runs `uv sync --locked --no-dev --no-editable`, `USER 10001` and `PYTHONUNBUFFERED=1`.
  - The build context is the repo root. A root `.dockerignore` allowlists only `pyproject.toml`, `uv.lock` and `src/`.
- **Compose service** `captures-pull`:
  - `build:` with no `image:`; `restart: unless-stopped`; `init: true`; no ports.
  - Volume `${TELEMETRY_ROOT:?…}/captures:/captures`.
  - Command uses `http://${LLAMA_SWAP_TARGET:?…}` and `${CAPTURES_HOST_LABEL:?…}`.
  - Environment: `LLAMA_SWAP_API_KEY: ${LLAMA_SWAP_API_KEY:-}`.
  - `otel-collector` gets no captures mount.
- **No new dependency**, so `uv.lock` does not change. No schema migration, no version bump; the session and run tables do not change.

#### Files to touch

New package `src/agentic_telemetry/captures/` (each file under 300 lines):
- `__init__.py`: the package docstring.
- `llama_swap_api.py`: the HTTP seam `ApiFetch = Callable[[str, Mapping[str, str]], bytes]`.
  - URL builders, with names pinned in step 0.
  - Errors `LlamaSwapUnreachableError`, `CaptureNotFoundError` and `LlamaSwapQueryError`. Messages carry the path only.
- `activity.py`: `ActivityRow`, keeping the raw dict. Timestamps become aware UTC, with nanosecond fractions trimmed.
- `decode.py`: base64 body to bytes or JSON; case-insensitive header lookup; the request `model`; `session_header(req_headers) -> str | None` (first non-empty of the three, in the order above).
- `sse.py`: `reassemble(body, content_type) -> Reassembled`.
- `cursor.py`: `Cursor`; atomic `load_cursor` and `save_cursor`; pure `detect_id_reset(rows, cursor)` and `new_rows(rows, cursor)`.
- `capture_file.py`: `append_records` (one frame per file per batch, fsync) and `read_records` (across frames, duplicates dropped).
- `harvest.py`: `poll_once(api, cursor, out) -> PollReport` (descending walk, id-reset check, capture fetches, write, cursor).
- `service.py`: `run_forever(poll, interval, sleep, log, max_polls=None)` and the report and heartbeat lines.
- `session_windows.py`: reads the session-table Parquet with duckdb into `SessionWindow`.
- `join.py`: `assign_calls(calls, sessions) -> list[ClaimedCall]` (header rule, then time and model with child wins and `ambiguous`, then unclaimed; `header_unmatched`).
- `show.py`: renders the list, one call's detail and the date list. Never prints `src`.
- `cli_commands.py`: `add_capture_parsers` and `run_capture_command`, with `--help` that states intent.

Edited:
- `src/agentic_telemetry/cli.py` (now 269 lines): register the two subcommands and dispatch them (about 10 lines); update the docstring.
- `collector/compose.yaml`: add the `captures-pull` service. Re-validate and record (don't-touch rule).
- `collector/env.example`: add `CAPTURES_HOST_LABEL=gpu` and an empty `LLAMA_SWAP_API_KEY=`, each with a comment.
- `collector/README.md`: new section "Local-model captures (spec 0008)" covering:
  - the llama-swap settings: `captureBuffer: 1024` (a normal day plus a long harvester outage; resized from the measured `evicted` count) and `store.path` (keeps every activity row through an outage, and ids through a restart; grows without bound on the inference host; pruning is decided with capture retention; no backup in this spec);
  - that a llama-swap restart still empties the capture cache, so unfetched calls show as `no_capture`;
  - the file layout and record shape, and that the Collector never tails the files;
  - that clients calling a model server's own port are not captured;
  - the session-header join and Pi's optional `compat.sendSessionAffinityHeaders`;
  - the deploy steps, and copying captures to the Mac.

  Also: two Images rows (python, uv); a line in the "How each source gets in" table; a Retention-table line for both the capture files and the inference host's SQLite store ("forever, for now"); and Verification entries.
- `tests/test_collector_compose.py`: services with `build:` are skipped by the image-pin tests, and their Dockerfile `FROM` and `COPY --from` lines must be pinned. New `captures-pull` tests.
- `tests/test_collector_readme.py`: `image_rows` skips `build:` services and adds the Dockerfile's images.
- `tests/collector_files.py`: add `DOCKERFILE` and `DOCKERIGNORE`; add `collector/captures/Dockerfile` to `HYGIENE_FILES`.
- `.gitignore`: add `captures/` as a guard.
- `AGENTS.md`: "How to run things" and the current-state block, at the docs step.
- `docs/specs/0008-local-llm-capture.md`: the approved plan, the id-reset wording, status; then regenerate `docs/specs/README.md`.

New, outside `src/`: `collector/captures/Dockerfile` and `.dockerignore` (repo root).

New tests (synthetic only):
- `tests/capture_builders.py`: rows, captures with base64 bodies and optional session headers, SSE streams, and a fake paged API. The fake can insert rows between page fetches, and scripts 404 and unreachable responses.
- `tests/test_captures_poll.py`, `tests/test_captures_id_reset.py`, `tests/test_captures_service.py`, `tests/test_captures_file.py`, `tests/test_captures_sse.py`, `tests/test_captures_join.py`, `tests/test_captures_join_header.py`, `tests/test_captures_cli.py`.

#### Order of operations

0. **Orchestrator, before test-first (unchanged):**
   - Add the id-reset wording to the spec, copy the plan in, mark the spec `shipping`.
   - Retrieve in-session, at tag v264 (MIT), the file(s) defining `handleAPIActivity`, `handleAPICapture`, `CreateAuthMiddleware` and request-header redaction. Pin the query parameter names and the largest `limit`, the status for a missing capture, the JSON encoding, and the auth header.
   - Read the python and uv index digests.
   - Record dates and licenses in the module headers.
1. **`/test-first`**: all the test files above, plus the collector test edits. The expected red is `ModuleNotFoundError` for `agentic_telemetry.captures.*`, and assertion failures for compose, README and Dockerfile.
2. Implement `activity`, `decode`, `sse`, `cursor`, `capture_file`.
3. Implement `llama_swap_api`, `harvest`, `service`.
4. Implement `session_windows`, `join` (header rule included) and `show`.
5. Implement `cli_commands` and wire them into `cli.py`.
6. Add the Dockerfile, `.dockerignore`, compose service and env.example lines. Then, on the Mac:
   - run `docker compose --env-file env.example config --quiet`;
   - build `captures-pull` on arm64, and on amd64 under emulation;
   - run `captures-pull --once` against a fake server on 127.0.0.1;
   - record all three in README → Verification.
7. Docs, then the gate (`.agentic/toolchain.sh gate`), `/review` and `/review-adversarial`.
8. **Phase B (owner checklist below).** Record the host counts in Verification, mark the spec `shipped`, and stop for commit approval.

#### Test plan by success criterion

| Criterion | Tests |
|---|---|
| Harvester | `test_captures_poll.py`: rows saved in id order; the descending walk stops at the cursor; a row inserted mid-walk gives no duplicate and no skip; a first run with no cursor backfills every page, saving old rows with `capture: null` and `capture_status: none`; records hold `activity` and `capture` byte-for-byte as served; the cursor advances; a quiet poll writes nothing. `test_captures_file.py`: daily path by UTC date; a batch spanning midnight splits; multi-frame read; duplicates dropped. |
| No silent loss: counts | `test_captures_poll.py`: `has_capture` with a 404 gives `evicted`, and the row is saved; `has_capture: false` gives `no_capture`; the report line has exact counts and no host, content or `src`. |
| No silent loss: id reset | `test_captures_id_reset.py`: ids continuing after a llama-swap restart (the SQLite case) is not a reset, and the cursor simply advances; a lower id with a newer timestamp is a reset, giving a new epoch and every new row saved; a reset whose new ids have already passed the old cursor is still detected; same id and same timestamp is not a reset; the cursor resumes after a harvester restart without duplicates. |
| No silent loss: unreachable | `test_captures_service.py`: logged with the host scrubbed and retried, the loop continues (fake `sleep`, `max_polls`), nothing raised; a mid-poll failure leaves the cursor before the failed row; the key is sent in the pinned header only when set, and never logged. |
| Streaming | `test_captures_sse.py`: content; reasoning; interleaved tool calls with split arguments; a usage chunk; `[DONE]`; multi-line `data:`; CRLF; `incomplete`; non-stream JSON; a `null` body; a non-chat route left raw. |
| Per-session view: time and model | `test_captures_join.py`: a window and model claim; an alias match through the request `model`; the 5 s pad; child wins; overlapping unrelated sessions are `ambiguous`; different models do not collide; unclaimed calls; a session with no local model never claims. |
| Per-session view: header | `test_captures_join_header.py`: each of the three headers matches in any case; the header claim beats an overlapping session on the same model, with no `ambiguous` flag; a header claim works even outside the session's window; precedence when two headers disagree; a header naming no session falls back to time and model with `header_unmatched`; no header falls back silently. |
| CLI | `test_captures_cli.py`: `--session ID` list columns; `--call N` detail (request JSON and reassembled response, no `src`); `RUN_ID` resolves to its root session; `--date` shows unclaimed calls and flags; errors exit 1 naming the input; usage errors exit 2; `captures-pull --once` writes a file; a credential URL is rejected without echoing it. |
| Never tracked or in Loki | compose tests: `captures-pull` has no ports, restart `unless-stopped`, its mount is under `${TELEMETRY_ROOT:?`; `otel-collector` has no captures mount; no filelog include matches `captures`; `.dockerignore` excludes `.env`; the Dockerfile is in the hygiene scan; every compose variable is in env.example. |
| Capture on, Coverage, Check on the host | Owner actions; counts go in Verification. |

#### Risks and open questions

- **The SQLite store grows without bound** on the inference host. Activity rows are small and hold no bodies, but they do hold `src` (client addresses). Size is measured after a week; pruning and backup are outside this spec.
- **The first harvester run walks the whole store**, once. That is fine early on. If `store.path` has been running for months before the harvester starts, the first poll is long but still bounded.
- **A llama-swap restart still loses captures not yet fetched**, shown as `no_capture`. With a 15 s poll, the window is at most one interval. Restart while idle.
- **Unconfirmed until step 0:** that a missing capture answers 404, and the names of the page and limit parameters. Pagination and the `evicted` count depend on both.
- **Host check:** whether Pi's session header value equals the transcript `id`. `header_unmatched` makes a mismatch visible; if they differ, the time-and-model rule still decides the call.
- **`x-client-request-id` from another client** may be a per-request ID. It then matches no session and falls back; it never makes a wrong exact claim.
- **Clock skew:** the 5 s pad assumes NTP on the inference host and the owner's Mac. Confirm during the coverage check.
- **Project Maya's stream format is unknown.** If it is not OpenAI SSE, its calls show raw.
- **Size:** capture volume grows with history length. Measure a day before deciding retention.
- **Deploy layout:** build context `..` assumes `/opt/telemetry/{collector/, src/, pyproject.toml, uv.lock, .dockerignore}` on the VM.

#### Out of scope (won't touch)

- `collector/otel-collector.yaml`, `loki-config.yaml`, `tempo.yaml`, `inference-agent/*` and `mac-agent/*`.
- Session-table and run-table schemas; scaffold changes (including a run-ID header).
- The rendered prompt; a UI; body redaction; pruning or backup of capture files or of the inference host's SQLite store.
- Frontier-model capture.
- Automated copying of captures to the Mac.

#### Owner-action checklist (Phase B)

1. **Through the inference host's sysadmin agent, before the harvester is deployed:**
   - Set `captureBuffer: 1024`.
   - Set `store.path` to a file on persistent local disk that llama-swap's user can write, outside any directory that gets cleaned. If llama-swap runs in a container, put it on a mounted volume. The real path stays in the vault's `ENDPOINTS.md`.
   - Confirm whether `apiKeys` is empty.
   - Restart llama-swap while idle, keeping `ttl: 900`.
   - Confirm the SQLite file exists and grows after one call.
2. **On the telemetry VM:**
   - Copy `collector/{compose.yaml, env.example, captures/Dockerfile}` and repo-root `{pyproject.toml, uv.lock, .dockerignore, src/}` into `/opt/telemetry`, mirroring the repository layout.
   - `sudo mkdir -p /srv/telemetry/captures && sudo chown 10001:10001 /srv/telemetry/captures`.
   - Add `CAPTURES_HOST_LABEL` (and `LLAMA_SWAP_API_KEY` only if keys are set) to `.env`, which stays `0600`.
   - `docker compose build captures-pull && docker compose up -d captures-pull`.
   - In `docker compose logs captures-pull`, the first poll's backfill line appears, then poll lines.
3. **Supervised TTL check:** with one model loaded and idle and the harvester polling, confirm it unloads at about 900 s. Record counts only.
4. **Restart check:** restart llama-swap once while idle. Confirm that ids continue, that the harvester logs no `id reset`, and that no rows are skipped. Record counts.
5. **Coverage check:** one call each through llama-swap to llama.cpp, Strata and Project Maya, each giving a record with `capture_status: saved`. Record counts per model.
6. **Optional:** set Pi's `compat.sendSessionAffinityHeaders: true` (with `sessionAffinityFormat: "openrouter"`) for the local models in Pi's model settings. Then confirm that one session's calls show neither `header_unmatched` nor `ambiguous`; that is the check that Pi's header value equals the transcript `id`.
7. **After one normal day and after one week:**
   - record the `evicted` total and the day's compressed capture size, and resize `captureBuffer` if needed;
   - record the SQLite file's size and row count growth.
8. **On the Mac:**
   - `rsync` the VM's `captures/` into `data/captures/` and run `session-table`;
   - run `captures show --session ID` for one post-deploy session and `captures show RUN_ID` for one delegate run;
   - record that they worked, counts only.
9. **Vault:** add the harvester and the SQLite path to `ENDPOINTS.md` (no tracked change).

## Phase handoff

### Step 0 results (2026-10-22)

Retrieved in-session from llama-swap tag v264 (MIT),
`internal/server/apigroup.go`, `internal/server/auth.go`,
`internal/server/captures.go` and `internal/store/activity.go` at
https://github.com/mostlygeek/llama-swap/tree/v264:

- `GET /api/metrics/activity` query parameters: `page` (>= 1), `limit`
  (1 to 999, default 25), `sort` (includes `id`, default `id`), `order`
  (`asc` or `desc`, default `desc`), `min_id` and `max_id` (inclusive, >= 1),
  `start` and `end` (RFC 3339), repeated `model`, `src_prefix`. A bad parameter
  answers 400. The response is the `ActivityPage` JSON
  (`data`, `page`, `limit`, `total`, `total_pages`), with `has_capture`
  overlaid from the capture cache.
- `GET /api/captures/{id}`: 400 for a non-integer id, **404 "capture not
  found"** when the cache has no capture, otherwise the `ReqRespCapture`
  marshalled with `encoding/json` (`id`, `req_path`, `req_headers`, `req_body`,
  `resp_headers`, `resp_body`; byte fields as base64 strings or `null`).
- Auth (`CreateAuthMiddleware`): a pass-through when no keys are configured;
  otherwise the key is accepted as `Authorization: Bearer`, `Authorization:
  Basic` (password) or `x-api-key`, and a missing or wrong key answers 401. The
  harvester sends `Authorization: Bearer <key>` only when the key is set.
- Redaction (`captures.go` `redactHeaders`): `authorization`,
  `proxy-authorization`, `cookie`, `set-cookie` and `x-api-key` values become
  `[REDACTED]`, case-insensitively. Header maps keep the first value under the
  Go-canonical key.
- Corrected at review: with `store.path`, the SQLite store keeps
  `timestamp` as Unix seconds and serves it in the host's zone, so served
  timestamps are whole seconds with a local offset (the in-memory store keeps
  nanoseconds). Ids are SQLite AUTOINCREMENT, monotonic and never reused.
- `min_id` exists, but the harvester keeps the plan's descending walk: the
  id-reset safety net needs to see rows at or below the cursor, which a
  `min_id` filter hides. Pages use `sort=id&order=desc&limit=200`.
- Base images (index digests read with `docker buildx imagetools inspect`):
  `python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70`
  (linux amd64 and arm64 among others) and
  `ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a`
  (superseded: see Review resolutions → Build pin, uv 0.11.33)
  (released 2026-10-08; the newest 0.13.x releases are under two weeks old).

### Test-first result (2026-10-22)

- Scope audit against the pre-phase fingerprints: only `tests/` changed.
- Orchestrator re-run (`pytest --continue-on-collection-errors`): 749 passed,
  18 failed, 8 errors. The 8 errors are `ModuleNotFoundError:
  agentic_telemetry.captures` (the planned package), one per
  `tests/test_captures_*.py`; the 18 failures are behavior-level assertions on
  the missing compose service, Dockerfile, `.dockerignore`, env.example lines
  and README sections.
- Rejected-value search: `metricsMaxInMemory`, "restart resets" and any
  assertion that `src` is absent, across `src/`, `tests/` and `collector/`: no
  matches outside this spec's own history.
- Test helpers beyond the plan's list, to keep files under 300 lines:
  `tests/capture_fake_api.py`, `tests/capture_join_helpers.py`,
  `tests/capture_cli_helpers.py`, and the compose checks in
  `tests/test_collector_captures_service.py`.

### Implement result, phase A (2026-10-22)

- Implemented directly (delegation is off for this worktree: no `[[project]]`
  entry). Gate green: 885 tests, lint, format, mypy.
- `/analyze` before coding: one `[ask-user]` (the spec bullet allowed any of the
  three session headers to match, the approved plan and tests use the first
  non-empty one). The approved plan governs; the bullet was reworded to match.
  Its `[auto-fix]` findings were applied: a loopback test of the real fetch
  (`tests/test_captures_http_fetch.py`, which also caught an uncaught
  `http.client.HTTPException`), and `tests/test_captures_extra_cases.py` (15 s
  default, the README's direct-port statement, the two-model join fallback, the
  `.gitignore` guard, `RUN_ID --call`, `--call` on an evicted call); the
  Upstream dependencies rows; Maya's JSON `/metrics`; a test renamed to what it
  checks.
- `.gitignore` uses `/captures/`: a bare `captures/` also ignored
  `src/agentic_telemetry/captures/` and `collector/captures/`, which the new
  guard test now pins.
- Step 6 checks are recorded in `collector/README.md` → Verification (compose
  config, arm64 and amd64 builds, `--once` against a fake llama-swap).
- Phase B (owner checklist) is outstanding; the spec stays `shipping` until its
  counts are recorded.

### Review resolutions (2026-10-22, owner answers)

Where the approved plan says otherwise, these govern.

- **Insert race.** llama-swap v264 inserts the activity row before it stores the
  capture. A row listed with `has_capture: false`, HTTP 200, no `error_msg`,
  that ended within 60 s of the newest listed row, is still fetched; a 404
  then means `none`.
- **Chunked commits.** A poll appends and saves the cursor every 100 rows (and
  at its end), so an outage backlog never sits in memory whole.
- **Stuck capture.** A capture that fails with anything but a 404 on 3
  consecutive polls of one harvester process is saved with `capture_status:
  "error"`, counted as `error=N` in the poll line, and passed. An unreachable
  llama-swap still retries forever.
- **Torn frames.** A failed append truncates the file back to its old size; a
  reader skips an unreadable frame with a warning naming the file.
- **Child window.** A descendant session beats its ancestor only for a call
  that overlaps the descendant's own (unpadded) window; in the 5 s pad the
  ancestor keeps it.
- **Build pin.** The image copies uv 0.11.33 (index digest
  `sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7`,
  released 2026-07-28), inside `pyproject.toml`'s `uv_build>=0.11,<0.12`, so
  the build downloads no backend.
- **Compose profile.** `captures-pull` sits behind the `captures` profile; the
  VM's `.env` sets `COMPOSE_PROFILES=captures`. Compose interpolates every
  service whatever its profile, so the label is `${CAPTURES_HOST_LABEL:-}` and
  an empty label makes the harvester exit with a usage error.
- **Delegate runs.** A delegate run's root session is linked as a child of the
  orchestrating session: the one session with no `run_id` whose project is the
  checkout holding the run's `.agentic/delegate/results/` and whose window
  contains the run's start. Zero or several candidates link nothing.
  Background subagents on the parent's model, and an orchestrator's own calls
  while its delegate runs, still go to the child; a session header resolves
  both exactly.
- Fixed directly: a missing `total_pages` stops the poll; `captures show` names
  an unreadable file instead of a traceback; reasoning sent under both keys
  counts once; an SSE body with no chat-completions chunk is shown raw, and raw
  bodies are printed as text; request bodies are parsed once per call; served
  timestamps from the SQLite store are whole seconds in the host's zone; README
  deploy order and data-root steps.
- Second round (re-review, owner answers 2026-10-22):
  - **Show reads nearby days.** `captures show` reads only the daily files
    from the day before its selection (the `--date` day, or the session's span)
    to the day after. A session header naming a session from further away is
    not seen there.
  - **Malformed row.** An activity row that will not parse on 3 consecutive
    polls of one harvester process is saved as served with `capture_status:
    "error"` and a top-level `estimated_end` (its nearest neighbour's end), and
    passed. It never moves `last_ts` or triggers an id reset. A row with no
    integer id still stops the poll. A malformed row at or below the cursor
    (already handled, perhaps by an earlier process) is never counted or
    raised again; an id reset clears the failure counts and saves the new
    epoch's cursor before walking it, so a malformed row in the new sequence
    passes after 3 polls like any other (a failure in that walk names the
    reset).
  - Fixed directly: an append is unbuffered and cut back with `ftruncate` when
    the write or the sync fails; frames carry a checksum; the frame magic
    comes from `zstandard.FRAME_HEADER`; a line that is not a capture record
    names its file and line, and an unreadable file exits 1; non-chat stream
    events (a mid-stream error) are printed under "other events"; the Mac
    setup step deletes `COMPOSE_PROFILES` and `CAPTURES_HOST_LABEL`.
- Owner checklist additions: at the coverage check (step 5) and on the Mac
  (step 8), confirm `captures show` prints real token counts (not `-`) and a
  streamed call reassembles into non-empty content, since every test fixture
  was built from the same step 0 reading; and watch whether `no_capture`
  appears for calls that completed normally.

### Review result, phase A (2026-10-22)

`/review` and `/review-adversarial`, a full re-review, and three focused
re-reviews. The last one, on the id-reset fix, found no issues and called the
change set clear for commit approval. Gate green: 915 tests, ruff lint and
format, mypy strict. Phase B (owner checklist) is next; the spec turns `shipped`
once its counts are recorded.
