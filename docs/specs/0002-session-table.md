# 0002 — Session Table

**Status:** shipped
**Last updated:** 2026-10-21

## Goal

Build the session half of roadmap pointer 2 in `0000-product.md`: a session
table with one row per client session, read from the native transcripts that
Pi, Claude Code and Codex CLI already write. A CLI command takes one or more
directories (the clients' session directories, project roots holding delegate
results, or collector inbox copies). It finds every transcript under them,
summarizes each into one row (client, project, served model, timings, token
totals, tool calls and tool errors, compactions), and writes a single Parquet
file keyed by client and session ID.

Spec 0001 covers delegate runs only. Most of the owner's agent work happens in
interactive sessions, including long local-model sessions with no delegate
ledger at all, and today none of it reaches a table. This spec needs nothing
from the scaffold's Phase 1 tool-call ledger: it reads what the clients record
on their own. The Phase 1 ledger reader and the transition failure matrix stay
in roadmap pointer 2 for a later spec.

### Input formats, as observed on 2026-10-21

Observed on the owner's machine: 187 Pi session files, 110 Claude Code session
files plus 142 Claude Code subagent files, and 186 Codex rollout files (6 plain
`.jsonl`, 180 `.jsonl.zst`). Only key
names and types were inspected, never content.

- **Pi** (`~/.pi/agent/sessions/<cwd-slug>/<ts>_<id>.jsonl`, and the delegate
  runner's `results/<run>/session/*.jsonl`): the first line is
  `{type: "session", version, id, timestamp, cwd, parentSession?}`, where
  `parentSession` is the parent session file's path. Each later line has `type`,
  `id`, `parentId` and `timestamp`.
  - `type: "message"` entries carry `message.role`:
    - `assistant` messages carry `model`, `responseModel?` (present when the
      answering model differs from `model`), `provider`, `stopReason` (`toolUse`,
      `stop`, `error`, `aborted`, `length`), `durationMs`, `usage{input, output,
      cacheRead, cacheWrite, reasoning?, totalTokens, cost}` and `content[]`
      items typed `text`, `thinking` and `toolCall`.
    - `toolResult` messages carry `toolName`, `toolCallId` and `isError`.
    - `user` messages carry `content[]`.
  - Other entry types: `compaction` (with `tokensBefore`), `model_change`,
    `thinking_level_change`, `session_info`, `custom`, `custom_message` and
    `context_edit`.
  - Pi's own docs say `parentSession` is written for `/fork`, `/clone` and
    `newSession({ parentSession })`. A fork or clone sits beside its parent.
    A subagent extension writes its children under
    `<parent-stem>/<id>/run-<n>/`. All 97 child sessions observed are of that
    kind, and they share no entry IDs with their parent.
- **Claude Code** (`~/.claude/projects/<cwd-slug>/<session-id>.jsonl`, with
  subagents at `<session-id>/subagents/agent-<id>.jsonl`): each line has `type`.
  - `user` and `assistant` lines carry `sessionId`, `cwd`, `version`,
    `gitBranch`, `timestamp`, `isSidechain` and `message`.
  - An assistant API response is split across several lines, one per content
    block (`text`, `thinking`, `tool_use`). Every line repeats the same
    `message.id` and identical `message.usage{input_tokens,
    cache_creation_input_tokens, cache_read_input_tokens, output_tokens, ...}`
    (6,815 repeats observed, 0 with differing usage).
  - `message.model` and `message.stop_reason` (`tool_use`, `end_turn`,
    `refusal`) are on the assistant lines.
  - Tool results are `tool_result` blocks in `user` lines, with `is_error`.
  - Subagent lines carry `isSidechain: true`, an `agentId`, and the parent's
    `sessionId`.
  - `system` lines carry `subtype`. `compact_boundary` marks a compaction.
  - Many other line types (`attachment`, `mode`, `ai-title`, `cost-state`,
    `file-history-snapshot`, `api-request`, ...) carry no per-turn usage.
- **Codex CLI** (`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl`): each
  line has `type`, `timestamp` and `payload`.
  - `session_meta.payload` carries `id`, `cwd`, `cli_version`,
    `model_provider`, `originator` and `source`. A subagent has `source:
    {subagent: ...}` and `parent_thread_id`.
  - `turn_context.payload` carries `model` and `effort`.
  - `event_msg` lines with `payload.type: "token_count"` carry
    `info.total_token_usage{input_tokens, cached_input_tokens,
    cache_write_input_tokens, output_tokens, reasoning_output_tokens,
    total_tokens}`, which is cumulative for the session. `input_tokens`
    includes `cached_input_tokens`.
  - `response_item` lines with `payload.type` `custom_tool_call` or
    `function_call` are tool calls.
  - `event_msg` lines with `payload.type: "item_completed"` carry
    `item{type, status}`, where `status: "failed"` marks a failed
    `CommandExecution`, `McpToolCall` or `FileChange`.
  - `event_msg` `task_complete` closes a turn.

## Success criteria

- `uv run agentic-telemetry session-table --out <file.parquet> [<dir>...]` writes
  one Parquet file and prints a summary to stdout. The summary gives: files
  scanned, sessions written per client, files skipped as unrecognized, sessions
  with token totals, duplicate session IDs resolved, and every warning. It
  never prints transcript content or `cwd`.
- **Default sources:** with no `<dir>`, the command reads each client's
  documented session location that exists, after applying its environment
  override (see `## External references`):
  - Pi: `$PI_CODING_AGENT_SESSION_DIR`, else `$PI_CODING_AGENT_DIR/sessions`,
    else `~/.pi/agent/sessions`.
  - Claude Code: `$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects`.
  - Codex: `$CODEX_HOME/sessions`, else `~/.codex/sessions`.

  The summary names each client as found or not found. Finding none of the
  three exits 1. With explicit `<dir>`s, only those are read, and a directory
  holding no recognized transcript exits 1 and names the directory. A Pi
  `--session-dir` passed for one run, or a `sessionDir` setting, cannot be
  detected; pass that directory explicitly.
- **Discovery:** each source is searched recursively for `*.jsonl` and
  `*.jsonl.zst` files (Codex compresses older rollouts: 180 of 186 observed),
  skipping `.git`, `node_modules`, `.venv`, `__pycache__` and Pi's
  `subagent-artifacts`. Each file is classified by its content, not its path:
  - **Pi:** the first parseable line has `type: "session"` and a `version`.
  - **Codex:** a `session_meta` line appears among the first 20 lines.
  - **Claude Code:** a `user` or `assistant` line with `sessionId` appears
    among the first 50 lines.
  - **Unrecognized:** anything else is counted and skipped, which covers Codex
    `history.jsonl` and `session_index.jsonl` and delegate ledgers.
- One row per distinct (`client`, `session_id`), with these columns:
  - **Identity:**
    - `client`: `pi`, `claude-code` or `codex`.
    - `session_id`: from the Pi header `id`, the Claude Code `sessionId` (for a
      subagent, whose lines carry `isSidechain: true`, its `agentId`), or the
      Codex `session_meta.id`.
    - `source_path`
    - `project`: the base name of the recorded `cwd`.
    - `cwd`
    - `client_version`: Claude Code's `version` (last recorded) or Codex's
      `cli_version`. Null for Pi, whose header `version` is the session-format
      version, not the CLI's.
    - `git_branch`: Claude Code only, the last recorded value.
  - **Lineage:**
    - `is_subagent`:
      - Pi: the file sits under a directory named after its parent's file stem.
        A fork or clone has a parent but is not a subagent.
      - Claude Code: the file's first turn line has `isSidechain: true` and an
        `agentId`. A stray sidechain line later in a main file does not re-key
        the session.
      - Codex: `session_meta.source` has a `subagent` key.
    - `parent_session_id`:
      - Pi: the parent file's stem after its last `_`.
      - Claude Code: a subagent's `sessionId`, which is its parent's (all 142
        observed subagent files match their parent directory).
      - Codex: `parent_thread_id`.
    - `run_id`: the delegate run ID when the file sits under
      `results/<run>/session/`, otherwise null. It joins to 0001's run table.
  - **Time:**
    - `started_at` and `ended_at`: the first and last entry timestamps, as
      ISO-8601 UTC strings.
    - `span_seconds`: the wall span between them, which includes idle time and
      resumes.
  - **Models:**
    - `provider`: Pi's `provider` on the first turn of `primary_model`, or
      Codex's `model_provider`. Null for Claude Code, which records none.
    - `models`: the distinct models used, in first-seen order.
    - `primary_model`: the model with the most assistant turns, ties broken by
      first seen. For a local server this is the requested profile alias.
    - `served_models`: the distinct models that answered, in first-seen order.
      - Pi: `responseModel`, which Pi records only when it differs from the
        requested `model`, else `model`. Observed: all 1,034 Pi turns with a
        `responseModel` name weights behind an alias.
      - Claude Code: `message.model`, the API response's own model field.
      - Codex: empty, because rollouts record only the requested model.
    - `primary_served_model`: the served model with the most assistant turns,
      ties broken by first seen. Null when none was recorded.
  - **Usage** (null when the client recorded none):
    - `tokens_in`: uncached input.
    - `tokens_cache_read`
    - `tokens_cache_write`
    - `tokens_out`
    - `tokens_reasoning`
    - `assistant_turns`
  - **Activity:**
    - `user_prompts`: user messages carrying human text, not tool results or
      injected context (per-client rules below).
    - `tool_calls`
    - `tool_errors`
    - `compactions`
    - `stop_reasons`: a JSON object mapping each stop reason to its count;
      null for Codex, which records none.
- **Per-client rules:**
  - **Pi:** sums `usage` over assistant messages. `tool_calls` counts
    `toolCall` content items, `tool_errors` counts `toolResult` with `isError:
    true`, and `compactions` counts `compaction` entries. `user_prompts`
    counts `user` messages whose content is a non-empty string or holds a
    `text` item. Pi's message types allow both.
  - **Claude Code:** dedupes assistant lines by `message.id` before summing
    usage and counting turns and stop reasons. Tool calls are `tool_use` blocks
    and tool errors are `tool_result` blocks with `is_error: true`, both counted
    per block, not deduped. `compactions` counts `system` lines with subtype
    `compact_boundary`. `user_prompts` counts `user` lines whose content is a
    string or holds a `text` block, excluding lines with `isMeta` or
    `isCompactSummary` (observed: 1,586 string and 57 text-block user lines,
    325 `isMeta`, 6 `isCompactSummary`). A main file holding more than one
    distinct `sessionId` takes the last one and adds a warning (none observed:
    all 110 main files hold exactly one).
  - **Codex:**
    - Usage comes from the last `token_count` whose `info` is non-null:
      - `tokens_in` is `input_tokens - cached_input_tokens`.
      - `tokens_cache_read` is `cached_input_tokens`.
      - `tokens_cache_write` is `cache_write_input_tokens`.
      - `tokens_out` is `output_tokens`.
      - `tokens_reasoning` is `reasoning_output_tokens`.
    - `assistant_turns` counts `token_count` events with usage `info` whose
      `total_token_usage` differs from the previous event's. Codex re-sends
      unchanged totals (observed: 236 of 8,673 events), and a repeat is not a
      new response. With no usage at all, `assistant_turns` is null.
    - `tool_calls` counts `response_item` lines typed `custom_tool_call` or
      `function_call`.
    - `tool_errors` counts `item_completed` items with `status: "failed"`.
    - `user_prompts` counts `item_completed` items typed `UserMessage`.
      `response_item` messages with role `user` also carry injected context, so
      they are not counted.
    - `compactions` counts top-level `compacted` lines (observed in a
      decompressed sample of the `.jsonl.zst` rollouts).
    - `parent_session_id` is `session_meta.payload.parent_thread_id` (present
      on both observed subagent files).
- **Duplicates:** when two files yield the same (`client`, `session_id`), for
  example a live file and a lagged inbox copy, the build keeps the file with
  more lines and adds a warning naming both paths. Equal line counts keep the
  first path in sorted order. This never exits non-zero: a transcript grows
  while a session runs, so a shorter copy is a stale prefix, not a conflict.
  This differs from 0001, where a differing copy of a run exits 1.
- **Robustness:**
  - A malformed JSON line, a non-object line, a non-UTF-8 line, or JSON that
    Python refuses (an over-long integer, or nesting past the recursion
    limit) produces a `file:line` warning and is skipped.
  - A file or directory that cannot be read, including a corrupt `.zst` or a
    symlink loop, is skipped with a warning naming it. It is not counted as
    unrecognized.
  - A single token count outside the BIGINT (int64) range is skipped like a
    mistyped value. A sum that leaves int64 makes the column null for that
    session.
  - A missing, mistyped or out-of-range field yields null in its column, never
    a crash.
  - Identical warnings print once.
- **Outputs:** the Parquet file has an explicit schema (BIGINT counts, DOUBLE
  seconds, `VARCHAR[]` lists) and is written atomically, as in 0001. The
  command exits 0 on success with warnings, 1 when no transcript is found (an
  explicit DIR without one, or default locations that hold none) or no row can
  be built from the transcripts found, and 2 on usage errors. When a DIR fails,
  the read warnings collected so far are printed with the error. A run that exits 1 writes nothing, so an earlier table is never
  replaced by an empty one.
- **Tests:** synthetic fixtures for every client and rule above. No fixture is
  copied from a real transcript.
- **Acceptance:** run manually against the owner's real session directories,
  with only counts recorded in tracked files.

## Non-goals

- The scaffold's Phase 1 tool-call ledger reader, per-tool-call rows, and the
  transition failure matrix. These stay in roadmap pointer 2 for a later spec.
- Per-tool breakdowns (calls per tool name). The session row carries totals
  only.
- Pi `subagent-artifacts/*_transcript.jsonl`, written by a subagent extension.
  They mirror child session files that are read directly.
- Codex `history.jsonl`, `session_index.jsonl` and the SQLite state files.
- Older Codex rollout formats not present on the owner's machine. Their files
  classify as Codex and their unknown lines yield nulls.
- Cost in dollars. Pi and Claude Code record it, but price tables are an
  external authority this spec does not take on.
- Reading `~/.codex/auth.json` or any credential file. Discovery reads only
  `*.jsonl`.
- Copying transcripts anywhere, shipping, or incremental updates. Each
  invocation rebuilds the whole file from read-only inputs.
- Subtracting forked history. Claude Code `--fork-session`, Pi `/fork` and Pi
  `/clone` copy earlier turns into a new file, so a forked session's totals
  include the copied turns. The `parent_session_id` column (with
  `is_subagent` false) lets analysis find and adjust for these. None were
  observed on the owner's machine.
- Codex `archived_sessions/`. It is empty on the owner's machine. Pass it as a
  DIR to include archived rollouts.

## Notes

- **Reuse:** this reuses 0001's DuckDB Parquet writer pattern and its
  `json_values` narrowing helpers (ADR 0001). The one new runtime dependency is
  `zstandard`.
- **Module layout:** a new `session_table/` package beside `run_table/`, one
  reader module per client. The run table's behavior is not changed. Its
  writer's docstring is corrected, since it is no longer the only module that
  imports duckdb.
- **Prompts on subagent rows:** a subagent's `user_prompts` counts the prompts
  its parent agent sent, not a human's. Filter on `NOT is_subagent` to count
  human prompts.
- **Normalization:** `tokens_in` is uncached input for every client. Codex
  reports `input_tokens` including cache reads, so it is reduced by
  `cached_input_tokens`. Claude Code and Pi report uncached input already.
  `tokens_reasoning` is taken as recorded. Whether it is part of `tokens_out`
  differs by client and is not reconciled here.
- **Privacy:** `cwd`, `source_path` and `git_branch` hold local paths and branch
  names. They go into the Parquet under ignored `data/`, never into tracked
  files or tests. Stdout names file paths only in warnings and never prints
  `cwd` or transcript content.
- **Compressed rollouts:** `.jsonl.zst` files are decompressed as a stream
  with the `zstandard` package (BSD-3-Clause, 0.25.0), the one new runtime
  dependency, chosen by the owner over shelling out to the zstd CLI or
  skipping compressed files.
- **Retention:** Claude Code deletes transcripts older than `cleanupPeriodDays`
  (default 30), so the table sees only what is still on disk. Raising it is a
  user-config change outside this repo.
- **Set-aside transcripts:** Claude Code may keep a previous transcript for a
  session as `<session>.orphaned-<ts>-<suffix>.jsonl`. It carries the same
  `sessionId`, so the duplicate rule keeps the longer file. None were observed.
- **Transcript formats** are the clients' own on-disk output, read from local
  evidence. They are undocumented and can change between client versions, so
  fields are checked before use and unknown shapes yield nulls.
- **Projects worked with several clients:**
  - Rows from Pi, Claude Code and Codex for one repository share the same
    `project` (the cwd base name), so `GROUP BY project, client` compares
    engines on one project.
  - Observed: every session whose cwd still exists ran at a repo root, except
    2 Claude Code sessions in a subdirectory. Each of those gets the
    subdirectory's name.
  - A session in a worktree named `<repo>-<branch>` gets that name. None were
    observed among interactive sessions.
  - Sessions are not linked across clients for the same task. That link
    needs the Phase 1 ledger's shared identifiers.
- **Home paths in code:** the default locations are built from
  `Path.home()` and the environment at run time, so no home-directory path is
  written into the code.

## External references

Default session locations and their overrides. All were retrieved 2026-10-21.

- **Pi:** the installed `@earendil-works/pi-coding-agent` 1.3.0 package docs
  (MIT):
  - `docs/environment-variables.md`: `PI_CODING_AGENT_DIR` (default
    `~/.pi/agent`) and `PI_CODING_AGENT_SESSION_DIR`.
  - `docs/session-format.md`:
    `~/.pi/agent/sessions/--<path>--/<timestamp>_<session-id>.jsonl`.
  - `docs/message-types.md`: "`responseModel` records a concrete provider
    response model when it differs from the requested model" (the
    `served_models` rule).
- **Claude Code:**
  - https://code.claude.com/docs/en/env-vars: `CLAUDE_CONFIG_DIR` "Override
    the configuration directory (default: `~/.claude`)".
  - https://code.claude.com/docs/en/claude-directory:
    - `projects/<project>/<session>.jsonl`
    - `projects/<project>/<session>/subagents/`
    - the orphaned and superseded transcript names
    - `cleanupPeriodDays` (default 30)
- **Codex:**
  - https://learn.chatgpt.com/docs/config-file/config-advanced: "Codex stores
    its local state under `CODEX_HOME` (defaults to `~/.codex`)".
  - The `sessions/YYYY/MM/DD/rollout-*.jsonl[.zst]` layout is not in the
    official docs. It is taken from the owner's machine, where it holds all
    186 rollouts.

## Standing decisions

- 2026-10-21: spec and plan approved by the owner:
  - The duplicate rule stays: the longer file wins and a warning is printed.
  - With no DIR, the documented default session locations are read.
  - `zstandard` is added to read Codex `.jsonl.zst` rollouts.
  - Multi-client projects group by `project`.
- 2026-10-21, after review: the owner added `served_models` and
  `primary_served_model`, to find models that underperform and to revisit a
  quant. The owner also asked for stale comments to be fixed, including the
  run table writer's docstring.

## Implementation notes

- **Code:** `src/agentic_telemetry/session_table/`:
  - `jsonl_lines`: a streaming plain or zstandard reader.
  - `classify`, `discovery` and `sources`.
  - `row` and `tally`.
  - One reader per client: `pi`, `claude_code`, `codex`.
  - `dedupe`, `summary`, `parquet_writer`, `build`.
  - The `session-table` subcommand in `cli.py`.

  `run_table/` behavior is unchanged (only its writer's docstring is corrected);
  `json_values` is imported from it.
- **Decisions made during implementation:**
  - **Existing but empty default locations:** with no DIR, one default
    location that exists but holds no transcript is not an error
    (`find_transcripts` takes `require_each=False`). The run still exits 1,
    writing nothing, when no default location exists or none of them holds a
    transcript. Explicit DIRs keep the per-directory exit 1.
  - **`~` in overrides:** a leading `~` in an override is expanded against the
    home directory, as Pi does. `default_sources` stays a pure function of the
    environment and the home directory.
  - **Codex turn counts:** a Codex session with no `token_count` usage gets
    `assistant_turns` null, not 0, as the plan states. Turns are known only
    from usage records.
  - **Empty stop reasons:** `stop_reasons` is null whenever no stop reason was
    recorded, for every client.
  - **Default-source status:** status lines print the path with the home
    directory written as `~`.
- **Acceptance, 2026-10-21, on the owner's machine** (counts only):
  - **Default run** (re-run after the review fixes): with no DIR, the command
    found all three default locations and wrote 629 sessions, with 0 warnings
    and 0 unrecognized files, in about 5 s.
    - By client: 187 Pi (97 subagents), 256 Claude Code (146 subagents), 186
      Codex (94 subagents; all 180 compressed rollouts read).
    - 610 sessions have token totals. No negative token or span values.
    - Codex has 8,437 assistant turns, matching the count of distinct usage
      totals.
  - **Served models** (after the owner added them):
    - Every Pi and Claude Code session with a turn has `served_models`; Codex
      has none, as specified.
    - 26 Pi sessions have a primary alias that differs from the primary served
      weights.
    - One set of weights was served under three different aliases across 14
      sessions, so a configuration can now be compared by weights.
  - **Long session:** the owner's longest local-model Pi session reads as
    342 assistant turns, 1,083,058 input tokens, 299,162 output tokens,
    6.9 hours and 1 compaction. This matches the hand count made before the
    spec.
  - **Multiple engines:** one repository shows sessions from all three
    clients (136 sessions), so `GROUP BY project, client` works across
    engines.
  - **Delegate cross-check:** against the two private projects with delegate
    runs, 5 delegate Pi sessions were read.
    - 4 join a run-table row by `run_id`, and all 4 agree on `tokens_in`,
      `tokens_out`, `tokens_cache_read` and `assistant_turns`.
    - The fifth belongs to a result directory the run table has no row for.

## Approved implementation plan

**Approved:** 2026-10-21

This is the planner's output, condensed by the orchestrator. Its headings are
demoted to nest under this section. It has been updated for the owner's
decisions on default sources and `zstandard`, and for the evidence checks run
before approval.

### Plan: 0002 Session Table

Spec `docs/specs/0002-session-table.md`; ADR `docs/adr/0001-duckdb-over-files.md`. Branch `spec-0002-session-table`. One new runtime dependency, `zstandard`, for `.jsonl.zst` Codex rollouts. It is added with `uv add`, which updates `[project.dependencies]` and `uv.lock`; `[tool.uv]` is untouched. No migration, no version bump. `data/` and `*.parquet` are already ignored.

#### Shared-helper decision

The smallest change, with **zero edits under `run_table/`**:

- **`json_values`: import in place** from `agentic_telemetry.run_table.json_values`.
  - It is a leaf module.
  - Moving it would touch 5 run_table files, and the spec says the run table is not changed.
  - Move it to `agentic_telemetry/json_values.py` when a third consumer arrives (the unit 2 ledger reader).
- **New `session_table/parquet_writer.py`.**
  - It repeats the ~25-line atomic DuckDB write pattern with its own schema.
  - `write_run_table` is typed to `RunRow`, so generalizing it would mean editing run_table.
- **Own skip-set constant** in session_table discovery: `{.git, node_modules, .venv, __pycache__, subagent-artifacts}`.

#### Files to touch

**Source** (`src/agentic_telemetry/`, all < 300 lines):

- `cli.py`:
  - Adds a `session-table` subparser that reuses `_input_dir` and `_parquet_out`. DIRs are optional; with none, it uses `default_sources`.
  - Its `--help` states intent and lists the default locations and their overrides.
  - `main` dispatches explicitly; anything else prints help and exits 2.
  - `NoTranscriptError` exits 1 on stderr before any write.
  - Prints the summary, then `wrote N rows to OUT`.
- `session_table/__init__.py`: docstring only.
- `session_table/sources.py`: `default_sources(environ, home) -> list[DefaultSource(client, path, found)]`.
  - Applies the spec's per-client environment overrides.
  - A pure function, so tests pass a fake environ and a `tmp_path` home.
  - The summary lists each client as found or not found. None found exits 1.
- `session_table/jsonl_lines.py`:
  - A streaming per-line byte decoder over plain `.jsonl` or `.jsonl.zst` (a `zstandard` stream reader).
  - A non-UTF-8, malformed or non-object line warns `path:line: <reason>`, never echoing the line text.
  - Blank lines are skipped.
  - Exposes `line_count`; the file is never held in memory.
- `session_table/classify.py`:
  - `classify_transcript(path) -> Client | None`, reading the head of the file only.
  - Pi: first parseable line is `type == "session"` with a `version`.
  - Codex: `session_meta` within lines 1-20.
  - Claude Code: a `user` or `assistant` line with `sessionId` within lines 1-50.
- `session_table/discovery.py`:
  - `find_transcripts(dirs) -> Discovery(transcripts, files_scanned, unrecognized)`.
  - Walks `os.path.abspath(dir)` with no symlink following, in sorted order, `*.jsonl` and `*.jsonl.zst`.
  - Dedupes resolved paths across overlapping dirs.
  - Raises `NoTranscriptError(dir)` per dir.
  - `delegate_run_id(path)` returns `<run>` for `.../results/<run>/session/<file>.jsonl`.
- `session_table/row.py`: frozen `SessionRow` in the spec's column order. `models` is `tuple[str, ...]`; `stop_reasons` is JSON text or None.
- `session_table/tally.py`: a mutable `SessionTally` shared by the readers.
  - Timestamps: min/max, normalized to UTC.
  - `span_seconds`.
  - Models in first-seen order, with turns per model; primary-model tie-break.
  - Per-field token sums, null when never recorded.
  - Counters and a `stop_reasons` Counter.
  - `to_row(identity)`. A mistyped value is skipped, never raised.
- `session_table/pi.py`: `read_pi(path) -> SessionRead(row | None, line_count, warnings)`, applying the spec's Pi rules.
- `session_table/claude_code.py`:
  - Dedupes by `message.id` (a line with no id is its own turn).
  - The stop reason is the last non-null value per id.
  - Tool use and tool errors are counted per block.
  - `compact_boundary` lines count as compactions.
  - A subagent uses `agentId` / `sessionId`.
  - `git_branch` and `version` are the last values recorded; `cwd` is the first.
  - `tokens_reasoning` is null.
  - User-prompt rule per spec. Takes the last `sessionId` and warns if a file holds more than one.
- `session_table/codex.py`:
  - Reads `session_meta` and `turn_context.model`.
  - Each `token_count` with non-null `info` is one turn, credited to the current model.
  - Usage comes from the last non-null `total_token_usage`. `tokens_in` is null unless both of its inputs are present.
  - Tool calls and failed items are counted.
  - `user_prompts` counts `UserMessage` items.
  - `compactions` counts top-level `compacted` lines.
  - `stop_reasons` is null.
- `session_table/dedupe.py`: `merge_sessions(reads) -> (rows, duplicates_resolved, warnings)`.
  - More lines wins; on a tie, the path that sorts first wins.
  - The warning names both paths. It never raises.
  - Output is sorted by `(client, session_id)`.
- `session_table/summary.py`: `SessionSummary` and `render_summary`.
  - Prints files scanned, sessions per client, unrecognized files, sessions with tokens, duplicates resolved, and each distinct warning once.
  - Prints no cwd, project, model or content.
- `session_table/parquet_writer.py`:
  - `SESSION_TABLE_SCHEMA`: BIGINT counts, DOUBLE `span_seconds`, BOOLEAN `is_subagent`, `VARCHAR[]` `models`, VARCHAR otherwise.
  - Atomic `write_session_table`.
- `session_table/build.py`:
  - `build_session_table(dirs) -> (rows, SessionSummary)`.
  - Order: discover, then read with an explicit `match` on client, then dedupe and summarize.
  - A file with no session ID, or an `OSError`, gets a warning and no row.

**Tests** (`tests/`, synthetic only):

- Fixture conventions:
  - cwd such as `/workspace/alpha-project`;
  - models such as `example-coder`;
  - a `TRANSCRIPT-CONTENT-SENTINEL` string to check that stdout never echoes content.
- `session_table_builders.py`: line builders per client shape, plus `write_jsonl`, which writes str items raw and bytes items as non-UTF-8 lines.
- `conftest.py`: adds a `session_tree` fixture covering:
  - all 3 clients, with a subagent each;
  - a delegate `results/<run>/session/` Pi file;
  - a stale-prefix duplicate;
  - `history.jsonl`, `session_index.jsonl`, a delegate ledger and `subagent-artifacts/`.

  The existing fixtures are untouched.
- Test files:
  - `test_session_jsonl_lines.py`
  - `test_session_classify.py`: covers the line 20/21 and 50/51 boundaries.
  - `test_session_discovery.py`: covers the skip dirs, an ignored `auth.json`, overlap, `NoTranscriptError` and `run_id`.
  - `test_session_tally.py`
  - `test_session_pi.py`
  - `test_session_claude_code.py`
  - `test_session_codex.py`
  - `test_session_dedupe.py`
  - `test_session_parquet_writer.py`
  - `test_session_sources.py`: each environment override, precedence, missing dirs.
  - `test_session_cli.py`:
    - a no-DIR default-source run with a fake home;
    - end-to-end counts;
    - cwd and the sentinel absent from stdout and stderr;
    - exit codes 1 and 2;
    - `--help` text;
    - `run-table` still works.

**Docs:**

- `AGENTS.md`: the Run line, and the current-state block (0002 shipped with counts; Next narrowed to the Phase 1 ledger reader plus the transition matrix).
- `README.md`: a session-table line in Quick start, using tilde paths.
- `docs/specs/0000-product.md`: one clause in pointer 2 saying the session half is spec 0002.
- Spec bookkeeping: the plan, `shipping`, then `shipped` with acceptance counts.

#### Order of operations

1. Evidence check. Done before approval and folded into the spec.
2. Persist the plan, set `shipping`.
3. `/test-first`: builders, the fixture, and 10 test files.
   - Expected red is `ModuleNotFoundError` for the planned `session_table.*` modules.
   - CLI tests fail by assertion.
   - run_table tests stay green.
4. Implement bottom-up:
   `jsonl_lines` → `classify` → `discovery` → `row` → `tally` → `pi` → `claude_code` → `codex` → `dedupe` → `summary` → `parquet_writer` → `build` → `cli`.
5. Docs, then mark `shipped`.
6. Checks:
   - the gate;
   - `git grep -nE '/(Users|home)/' -- src tests`;
   - a scan for non-`.example` hosts;
   - `closeout-check.sh`.
7. Manual acceptance against the real session dirs and the two delegate projects. Record counts only. Cross-check that a delegate Pi session's tokens, joined by `run_id`, agree with `data/run_table.parquet`.
8. `/review-check`, `/review`, `/review-adversarial`; stop for commit approval.

#### Risks / open questions (defaults taken)

- **Pi `parentSession`** may also mark forks, not only subagents. `is_subagent` follows the spec literally.
- **Timestamps:** min/max rather than file order. A naive timestamp is treated as UTC.
- **stdout paths:** warnings name file paths, and Claude Code paths carry the cwd slug. That is acceptable on a local terminal; stdout is never pasted into tracked files.
- **Per-line warnings** on a truncated transcript could be noisy; acceptance shows whether it matters.
- **Line-reader overlap:** `jsonl_lines` overlaps run_table's `ledger.py` decode loop. Unifying them is deferred to keep run_table untouched.

#### Out of scope

- `run_table/**` and its tests.
- `pyproject.toml` `[tool.uv]`.
- `collector/`, CI, `.agentic/toolchain.sh`, ADR 0001.
- The spec's non-goals.
- Real transcript lines in tests.
- Anything under `data/`.
