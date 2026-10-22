# 0009 — Count Subagent Usage in Pi Transcripts

**Status:** shipped
**Last updated:** 2026-10-22
**Depends on:** 0001, 0005

## Goal

Make the run table count the tokens a delegate run's Pi session spent in its
subagents. A delegate handoff that starts a subagent (for example a test-first
subagent) runs it on the same local model, inside the same run. Today the run
table sums only `assistant` messages, so the subagent's tokens are missing from
the run's totals. The server-metrics join (0005) then sees the inference
server process more tokens than the run recorded, and marks a correctly joined
run `overlap` (`server_more`).

### Found on 2026-10-22 (spec 0006, step B24)

A Flash Next handoff (test-first+implement) started one test-first subagent.

- The parent transcript records the subagent's usage on a `message` entry with
  `role: toolResult` and `toolName: subagent`. Its `usage` object has the same
  keys as an assistant message's (`input`, `output`, `cacheRead`, `cacheWrite`,
  `totalTokens`, `cost`). It has no `model` or `responseModel`. Its `details`
  carry `totalChildUsage`, `runId` and `artifacts`.
- The subagent's own turns are also written twice under the run's `session/`
  directory: `subagent-artifacts/<id>_<agent>_<n>_transcript.jsonl` and
  `<parent-session>/<id>/session.jsonl`. Both hold the same 9 assistant turns.
- Assistant messages: 60,922 input and 9,598 output tokens. The subagent tool
  result: 20,705 and 7,291. Together: 81,627 and 16,889, which equals exactly
  what the inference server counted for the run's window.
- `run-table` reads only `session/*.jsonl` (not subdirectories) and sums only
  assistant messages, so it reported 60,922 and 9,598.
- `session-table` is not short. Its discovery skips only the `subagent-artifacts/`
  mirror, so it reads `<parent-session>/<id>/run-<n>/session.jsonl` as its own
  Pi row, with `is_subagent` set and `parent_session_id` naming the parent
  (spec 0002). Adding the tool result's usage to the parent row would count
  those tokens twice across the table (checked 2026-10-22, clarify).

## Success criteria

- **Run table.** A run's `tokens_in`, `tokens_out` and `tokens_cache_read`
  include every `subagent` tool result's usage in its transcripts.
  `tokens_reasoning` stays the parent's: Pi's tool result has no `reasoning`
  key, though one would be added if it appeared (review, 2026-10-22). The tool result's `usage` is the only source: the copies under
  `subagent-artifacts/` and the nested `<parent-session>/<id>/` directory are
  never read, so each subagent turn is counted once. A `subagent` tool result
  without `usage` (the B24 run's `management` call) adds nothing. One marked
  `isError: true` still counts: the server processed those tokens. A non-numeric
  usage value counts as 0, as it does for assistant messages (clarify). This reads only
  the parent transcripts that ship-ledgers already copies, so landing-zone copies
  give the same rows as project roots with no ship-ledgers change (clarify).
- **Model names.** Each entry of a usage-bearing tool result's
  `details.results[].model` adds a name to a new list column `subagent_models`
  (distinct, first-seen order); `requested_models` stays the parent's own
  names, so the server-metrics join resolves the parent's model as before
  (review, 2026-10-22: a child name there could make rule 2 resolve the child's
  model). Pi writes it as
  `<provider>/<model>`; when the prefix up to the first `/` equals the `provider`
  of one of the run's own assistant messages, that prefix is removed, otherwise
  the name is kept whole. `served_models` is unchanged: the tool result carries
  no served name (clarify).
- **Turns.** `assistant_turns` stays the parent's assistant turns. Two new
  integer columns (clarify):
  - `subagent_runs`: the number of `details.results` entries across the run's
    usage-bearing `subagent` tool results, so a parallel or chain call with three
    children counts 3. The B24 run: 1.
  - `subagent_turns`: the sum of `details.totalChildUsage.turns` over those tool
    results. The B24 run: 9. Null when any of them lacks an integer `turns`.
  - A run with transcripts and no subagent gets 0 and 0; a run with no transcript
    gets null in both, like the other session columns.
- **Join.** On the B24 run's transcripts (kept locally, never committed), the
  run-table join reads `joined` with `server_token_match = exact`. A synthetic
  fixture with a parent, a subagent tool result and both nested copies pins the
  totals in a test.
- **Old runs.** Runs without a subagent keep identical values in every existing
  column, and the three new columns read 0, 0 and an empty list (null with no
  transcript); a regression test compares the 0001 fixtures before and after.

## Non-goals

- Apportioning server metrics between parent and subagent turns.
- Any session-table change. Every client's subagent session, Pi's included, is
  already its own row (`is_subagent`, `parent_session_id`); a parent's total
  with its children is a query over those rows.
- Subagent usage for clients other than Pi.
- Changing Pi or the scaffold's handoff runner.

## Notes

- Peak per-turn context (`peak_turn_prompt_tokens`) stays the parent's: the
  tool result's `usage` is a total over the subagent's turns, not one turn
  (clarify).
- Known under-counts (review, 2026-10-22; read from the installed pi-subagents
  package, a newer version than the one the scaffold pins):
  - A background (async) subagent's tool result has empty `results` and no
    `usage`, so its tokens never reach the parent transcript; such a run still
    reads `overlap`.
  - A child that starts its own subagent: the tool result's `usage` sums only the
    direct children's turns, so a grandchild's tokens are missing. The
    scaffold's child agents have no `subagent` tool today.
- A run whose subagent ran a different model than the parent reads
  `joined_approx`: the run resolves to the parent's model, and the summed
  tokens exceed that model's counters. Apportioning is a non-goal.

## External references

- None outside this repository. The transcript shapes come from the run's own
  files (read 2026-10-22). Fixtures are synthetic, never copied transcripts.

## Standing decisions

- 2026-10-22: the owner approved the plan with "run the loop, dont stop until
  you need me". Delegation mode for the implement phase: `direct` (no handoff).
  No commit grant: stop before commit as usual.

## Approved implementation plan

**Approved:** 2026-10-22

Amended after review, with the owner's approval: child model names go to
`subagent_models`, not `requested_models`. Where the plan below says otherwise,
`### Review resolutions` under `## Phase handoff` governs.

The planner's plan, copied verbatim. Its later headings are demoted one level;
`## Files to touch` stays level 2 because `.agentic/delegate-run.sh` reads the
plan's file list from it.

## Files to touch

- `src/agentic_telemetry/run_table/subagent_usage.py` — new module, about 80 lines:
  - A frozen `SubagentCall` with `usage: JsonObject`, `runs: int` (the number of `details.results` object entries), `turns: int | None` (`opt_int` of `details.totalChildUsage.turns`) and `models: tuple[str, ...]` (the string `details.results[].model` values, unchanged).
  - `read_subagent_call(message: JsonObject) -> SubagentCall | None` returns None unless `role == "toolResult"`, `toolName == "subagent"` and `usage` is an object. A management call has no `usage`, so it gives None. `isError` is ignored, so an error still counts.
  - `strip_provider(name: str, providers: Collection[str]) -> str` removes the prefix up to the first `/` only when that prefix is in `providers`.
- `src/agentic_telemetry/run_table/session_usage.py`:
  - `SessionUsage` gains `subagent_runs: int` and `subagent_turns: int | None`, placed before `warnings`.
  - `_assistant_messages` and `_assistant_message` become `_counted_messages` and `_counted_message`. They also yield `toolResult` messages whose `toolName` is `subagent`.
  - `_Tally`:
    - shares one `_add_usage` helper for summing (`_SUMMED_USAGE_KEYS`, `opt_int` or 0);
    - records each assistant message's `provider`;
    - gets `add_subagent_call`: adds the usage, `runs` and `turns` (turns goes null once any call has None) and keeps the raw model names. It changes neither turns nor the peak.
  - `usage()` builds `requested_models` as the parent's names in first-seen order, then the subagent names after `strip_provider` against every provider seen in the run's transcripts, keeping each name once.
  - The glob stays `session/*.jsonl` (not recursive).
  - Update the docstrings. The file should stay at 210 lines or fewer.
- `src/agentic_telemetry/run_table/row.py` — `RunRow` gains `subagent_runs: int | None` and `subagent_turns: int | None` right after `peak_turn_prompt_tokens`, with no default and a comment. `build_row` fills both from `usage`, or None when there is no usage.
- `src/agentic_telemetry/run_table/parquet_writer.py` — add both columns to `BIGINT_COLUMNS`.
- `tests/subagent_builders.py` — new synthetic builders, with placeholder provider `gpu-example` and invented model names:
  - `subagent_tool_result(...)` takes tokens, `models`, `turns` (None leaves it out), `is_error`, `mode` and `results` count;
  - `management_tool_result()` has no `usage`;
  - `write_nested_subagent_copies(run_dir, parent_session, call_id, agent, lines)` writes `session/subagent-artifacts/<id>_<agent>_1_transcript.jsonl`, `session/<parent-session>/<id>/session.jsonl` and `session/<parent-session>/<id>/run-1/session.jsonl`.
- `tests/run_table_builders.py` — `assistant_message` gains `provider: str | None = None`.
- `tests/test_run_table_subagent_usage.py` — new reader-level tests (listed below).
- `tests/test_run_table_subagent_rows.py` — new row, build and join tests (listed below).
- `tests/test_run_table_old_runs.py` — new regression test over the `evidence_tree` fixture.
- `tests/test_parquet_writer.py` — add the two columns to `EXPECTED_COLUMNS` (after `peak_turn_prompt_tokens`), to `INTEGER_COLUMNS`, to `sample_row` and to the all-null test.
- `tests/server_join_helpers.py` — `server_row` gains `subagent_runs: 0` and `subagent_turns: 0`.
- `AGENTS.md` — current-state block:
  - the 0006 "Finding" bullet ends "Spec 0009 fixes it.";
  - add a 0009 shipped bullet with the two columns, the requested-name rule and the B24 result;
  - remove 0009 from "Next".
- `docs/specs/0009-subagent-usage.md` — status, plan, phase handoffs and the B24 verification result (as a summary, with no hostnames or paths).
- `docs/specs/README.md` — regenerate the dashboard at close.

### Order of operations

1. On branch `spec-0009-subagent-usage`, `/test-first` writes the tests below.
   - Expected red:
     - `ModuleNotFoundError` for `agentic_telemetry.run_table.subagent_usage`;
     - `AttributeError` or `AssertionError` on `subagent_runs`/`subagent_turns` and on the token totals;
     - `TypeError` (unexpected keyword) from `RunRow(**values)` in `server_join_helpers.server_row` and `test_parquet_writer.sample_row`, which reds the server-join and parquet tests until `RunRow` has the fields.
   - The old-runs test must pass on every pre-0009 column now and fail only on the new columns.
2. Implement `subagent_usage.py`.
3. Implement the `session_usage.py` changes.
4. Make the `row.py` and `parquet_writer.py` changes.
5. Run `.agentic/toolchain.sh gate`.
6. Verify B24 by hand. This is not a committed test:
   - On the owner's machine, run `uv run agentic-telemetry run-table --metrics-url <telemetry VM VictoriaMetrics URL from the untracked env> --out data/run_table.parquet <project root holding the B24 run>`.
   - Expect: `tokens_in` 81627, `tokens_out` 16889, `subagent_runs` 1, `subagent_turns` 9, `server_status` `joined`, `server_token_match` `exact`.
   - Also run the same command against the landing-zone copy and confirm the row is the same.
   - Record a summary in the spec.
7. Docs: update `AGENTS.md`, set the spec to shipped and regenerate the dashboard. Then `/review-check`, `/review` and `/review-adversarial`.

### Tests to write (one behavior each)

`tests/test_run_table_subagent_usage.py` (calls `read_session_usage`):
- A subagent tool result's `input`/`output`/`cacheRead`/`reasoning` are added to the token totals. `assistant_turns` stays the parent's count. `cacheWrite`/`totalTokens` are still not summed.
- With both nested copies written as well, the totals are exactly parent plus tool result. The B24-shaped numbers 60922+20705 and 9598+7291 give 81627 and 16889.
- A management call (no `usage`) adds no tokens, no runs and no model names.
- An `isError: true` subagent result still counts its usage and its runs.
- A non-numeric usage value (string, null) counts as 0.
- A `toolResult` with another `toolName` that carries `usage` is ignored.
- `subagent_runs` counts `details.results` entries across calls: one call with 3 children plus one with 1 gives 4.
- `subagent_turns` sums `totalChildUsage.turns`. It is null when any usage-bearing call lacks a numeric `turns`. A management call without `turns` does not make it null.
- A transcript with no subagent call gives `subagent_runs == 0` and `subagent_turns == 0`.
- Provider prefix:
  - `gpu-example/local-flash-next` becomes `local-flash-next` when a parent assistant message has `provider: gpu-example`;
  - `other-example/x` is kept whole;
  - a name with no `/` is kept whole.
- Requested names keep the parent's first, then the subagent's. A name equal to the parent's appears once. `served_models` is unchanged.
- The provider match works across transcript files: the tool result is in `0001.jsonl` and the assistant message with `provider` is in `0002.jsonl`.
- `peak_turn_prompt_tokens` stays the parent's largest turn even when the tool result's usage is larger.

`tests/test_run_table_subagent_rows.py`:
- A `build_run_table` row for a run with one subagent call has the summed tokens, `subagent_runs == 1` and `subagent_turns == 9`.
- A run with no transcript has null `subagent_runs` and `subagent_turns`.
- The same evidence in the `agentic` and `landing` layouts gives identical rows, apart from location fields.
- Join:
  - Build the row with the parent requesting `ALIAS_QWEN` and served `QWEN`, and the subagent model `gpu-example/<ALIAS_QWEN>`.
  - Run `join_server_metrics` with a `FakeVictoria` whose prompt and predicted counts equal the summed totals.
  - Expect `server_status == "joined"` and `server_token_match == "exact"`.

`tests/test_run_table_old_runs.py`:
- The `evidence_tree` rows (all five run IDs) match a pinned golden of every pre-0009 column. The golden is captured from the current code at test-first time, with `ledger_path` relative to the root.
- In the same fixture, `beta-run-handoff` has 0 and 0, and the alpha runs without a transcript have None and None.

### Risks / open questions

- Decided from the spec, no human input needed:
  - **Column position.** The new columns go after `peak_turn_prompt_tokens`, as required fields. They must come before the defaulted `server_*` fields, and the 0001 columns stay an unchanged prefix. Readers select by name (DuckDB), so the server columns moving does not affect them.
  - **`turns` must be a JSON integer** (`opt_int`). A float or a string makes `subagent_turns` null. Pi writes integers.
  - **Order of requested names.** Subagent names go after the parent's names, not interleaved by position in the file. This is simpler and deterministic.
- **Resolve side effect (accepted, no code change).** If a subagent ran a different *known* model than the parent, `requested_models` now holds two known IDs. `resolve_model` then gives `none`, so the run is uncovered, or the served rule is contradicted. That is correct for a run that used two models. Neither `join.py` nor `server_metrics/` changes.
- **Edge case.** A parent transcript with no model names but a named subagent call loses rule-3 inference (`not requested`). This is rare and accepted.
- **No `provider` on old transcripts.** If the parent's assistant messages have no `provider`, subagent names stay whole (`gpu-example/...`). They are unknown to VictoriaMetrics, so they do not change resolution.
- The B24 check needs the owner's local transcripts and the VM's VictoriaMetrics URL. The orchestrator cannot do it without the owner. Record only counts and statuses, never the URL, host or path.

### Out of scope (won't touch)

- `src/agentic_telemetry/run_table/join.py`, `src/agentic_telemetry/server_metrics/*`, `run_table/summary.py` and `cli.py`: they handle the new fields generically, or have no column list.
- `src/agentic_telemetry/session_table/*`, because the spec rules out session-table changes.
- `collector/mac-agent/ship-ledgers.sh`, because it already copies the parent transcripts.
- Clients other than Pi, apportioning server metrics between parent and subagent, and any change to Pi or the scaffold runner.
- Reading the nested subagent transcripts. The tool result's `usage` is the only source.
- Shipped specs 0001 and 0005: their column lists are historical records and are not edited.

## Phase handoff

### Test-first (2026-10-22)

- Tests written: `tests/subagent_builders.py`, `tests/test_run_table_subagent_usage.py`,
  `tests/test_run_table_subagent_rows.py`, `tests/test_run_table_old_runs.py`; changed
  `tests/run_table_builders.py` (`provider`), `tests/server_join_helpers.py` and
  `tests/test_parquet_writer.py` (the two new columns).
- Scope audit: only those paths changed (fingerprints compared before and after).
- Red, rerun by the orchestrator: 81 failed, 663 passed. 55 `TypeError` (unexpected
  keyword `subagent_runs` on `RunRow`), 10 `AttributeError` on the new
  `SessionUsage`/`RunRow` fields, 16 behavior-level `AssertionError` (token totals,
  requested names, column order, `overlap` instead of `joined`). No collection error.
- The old-runs golden (every pre-0009 column of the five `evidence_tree` runs) passes
  before implementation.
- Rejected-value search: `assistant message`, `assistant_turns`,
  `peak_turn_prompt_tokens`, `requested_models`, `tokens_cache_read`, `sums only`
  over `src/`, `docs/`, `collector/`, `AGENTS.md` and `README.md`. Kept: the B24
  paragraph in `collector/README.md` (a dated record of what happened). Open: that
  file's "Not yet done" bullet on spec 0009 is outside the plan's file list; asked at
  the commit stop.

### Implement and verify (2026-10-22)

- Implemented directly (no handoff) as planned: `run_table/subagent_usage.py` (new),
  `session_usage.py`, `row.py`, `parquet_writer.py`. `session_usage.py` came out at
  224 lines, over the plan's estimate of 210 and under the 300-line limit.
- Gate: lint, format, mypy and 744 tests green.
- B24, re-run with this branch's `run-table --metrics-url` against the telemetry
  VM's VictoriaMetrics: `tokens_in` 81,627, `tokens_out` 16,889, `assistant_turns`
  16, `subagent_runs` 1, `subagent_turns` 9, `server_status` `joined`,
  `server_token_match` `exact` (server: 81,627 and 16,889). A fresh landing-zone
  copy made with `ship-ledgers.sh --dest`, which carries only the parent
  transcript, gives an identical row apart from `ledger_path`.
- Seen on the same run, not changed here: `run-table` warns that the delegated
  phase `test-first+implement` is not one of its known phases, so
  `first_failure_phase` cannot be derived for a combined-phase handoff.

### Review resolutions (2026-10-22)

- `/review` and `/review-adversarial` raised four `[ask-user]` items; the owner chose:
  - Child model names move to a new `subagent_models` column (`VARCHAR[]`, after
    `subagent_turns`); `requested_models` stays the parent's. This replaces the plan's
    risk note "resolve side effect (accepted)", which was wrong: with an unknown
    parent alias, a known child name made rule 2 resolve the child's model. A join
    test pins that a child on another known model leaves the parent's served model.
  - `tokens_reasoning` wording corrected (it stays the parent's); the test builder
    no longer writes a `reasoning` key Pi never writes, and one test keeps the
    stray-key case.
  - `collector/README.md`: the "Not yet done" bullet on spec 0009 is removed and the
    B24 record gains one sentence (scope addition approved).
  - The two under-counts above go in Notes.
  - With child names out of `requested_models`, the plan's other risk ("a parent
    transcript with no model names but a named subagent call loses rule-3
    inference") no longer applies: rule 3 sees only the parent's names.
- Auto-fixes: the `read_session_usage` docstring; a test for malformed `details`.
- B24 re-checked after these fixes: 81,627 and 16,889 tokens, `tokens_reasoning` 0,
  `subagent_runs` 1, `subagent_turns` 9, one `subagent_models` name equal to the
  parent's requested alias once its provider prefix is removed, `joined` with
  `exact` tokens, model source `served`.
- A fresh full review then found only wording fixes (this record, the Old-runs
  criterion, a `row.py` comment), applied.
- Files added beyond the plan's list, with the owner's approval of the above:
  `tests/test_run_table_subagent_models.py` (the model-name tests, moved out of the
  usage tests to keep both under 300 lines) and `collector/README.md`.
