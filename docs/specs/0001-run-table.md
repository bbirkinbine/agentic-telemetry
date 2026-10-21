# 0001 — Run Table

**Status:** shipped
**Last updated:** 2026-10-21

## Goal

Build the first output in `analysis/README.md`: a run table with one row per
delegate run. A CLI command takes one or more directories (scaffold-bootstrapped
project roots, or the collector inbox's `inbox/<host>/<project>/` copies). It
finds every delegate ledger under them, joins each run to its review verdict and
its per-run result directory, and writes a single Parquet file keyed by run ID
and served-configuration fingerprint. This is roadmap pointer 1 in
`0000-product.md`: it lets the owner name the cause of a failed delegate run
without re-reading transcripts. It also gives units 2-4 a table to extend, with
server metrics and tool-call sessions. It runs on the evidence that exists today
(the owner's two private projects with delegate runs) and needs nothing from the scaffold's Phase 1.

### Input formats, as observed on 2026-10-21

- `.agentic/delegate/ledger.jsonl`: one JSON object per line, with a `type`
  field.
  - `type: "run"` rows carry `ts`, `run`, `mode`, `selection`, `fingerprint`,
    `qualified`, `packet`, `packet_sha256`, `target`, `bucket`,
    `snapshot_sha256`, `harness`, `model{name, model_id, quant, runtime,
    endpoint, request}`, `attempts[{attempt, outcome, detail, seconds}]`,
    `outcome`, `reason`, `gate`, `unverified_steps`, `review` and
    `wall_seconds`. Early rows have no `wall_seconds`.
  - `type: "review"` rows carry `ts`, `run`, `verdict` and `review_changes`.
- `.agentic/delegate/results/<run>/result.json` holds the same object as that
  run's ledger `run` row; all 11 observed pairs match field for field. Older
  runs have no result directory.
- `results/<run>/session/*.jsonl` is the Pi session transcript (handoff runs
  only). Assistant `message` entries carry `usage{input, output, cacheRead,
  cacheWrite, reasoning, totalTokens}`.
- `results/<run>/state.json` (delegate-run-v2 only) carries `phase`,
  `harness_version` and `segments[{retries, compactions, end, seconds}]`.

## Success criteria

- `uv run agentic-telemetry run-table --out <file.parquet> <dir>...` writes one
  Parquet file and prints a summary to stdout. The summary gives ledgers found,
  run rows written per project, reviews joined, runs with token totals, and
  every warning.
- Discovery: each `<dir>` is searched recursively for `ledger.jsonl` files whose
  parent directory is named `delegate` or contains a `results/` directory. A
  directory with no ledger exits non-zero and names the directory.
- One row per distinct `run` ID, with these columns:
  - Identity: `run_id`, `project`, `ledger_path`, `ts`, `harness`, `mode`,
    `bucket`, `target`, `packet`, `packet_sha256`, `snapshot_sha256`.
  - Served configuration: `fingerprint`, `qualified`, `model_name`, `model_id`,
    `quant`, `runtime`, `endpoint`, `request_params` (JSON string, as
    recorded).
  - Outcome: `outcome`, `reason`, `gate`, `unverified_steps` (list), `attempts`
    (count), `attempt_outcomes` (list), `agent_seconds` (sum of attempt
    seconds), `wall_seconds` (null when absent).
  - Review: `review_verdict`, `review_ts`, `review_count`.
  - Derived failure: `delegated_phase`, `first_failure_phase`,
    `failure_cause`.
  - Session usage (null when there is no transcript): `tokens_in`,
    `tokens_out`, `tokens_cache_read`, `tokens_reasoning`, `assistant_turns`,
    `compactions`, `retries`.
- Joins:
  - Review rows join to runs by `run` ID. With several reviews for one run, the
    latest `ts` wins and `review_count` records how many there were. A review
    whose run ID has no `run` row is a warning.
  - `result.json` fills in a run missing from the ledger. The result
    directory's name is that run's ID. A `run` field that disagrees with the
    directory name is a warning. When both a ledger row and a `result.json`
    exist and differ, the ledger row wins and a warning names the run and the
    differing keys.
  - A result directory with neither a ledger row nor a `result.json` (an
    interrupted run) is not a row; the summary lists it as orphaned.
- The same run ID reached through two inputs (for example a project root and
  its inbox copy) appears once if the two rows are identical. If they differ,
  the command exits non-zero and names both ledger paths.
- A malformed ledger line, or a line missing `type` or `run`, is skipped with a
  warning that names `file:line`. An unknown `type` is counted, not fatal.
- `first_failure_phase` uses the phase names of the transition matrix (spec,
  plan, test-first, implement, gate, review):
  - `green` with review `pass` or no review: null.
  - `green` whose latest review has no verdict: `unknown`, cause =
    `review-without-verdict`, with a warning.
  - `green` with any other verdict: `review`, cause = the verdict.
  - `needs_frontier`: `gate`, cause = `needs_frontier`.
  - `integrity`: `delegated_phase`, cause = `integrity`; `unknown` when the
    delegated phase cannot be told.
  - Any other outcome: `unknown`, with a warning.
  The evidence has no failure-phase field, so the phase is derived from outcome
  plus verdict. Per-file runs (harness v1, bucket small/medium) count as
  `delegated_phase=implement`. Handoff runs take the phase from `state.json`, or
  from the `phase:` bucket prefix.
- Run against the two private projects with delegate runs as they stand on
  2026-10-21, the command writes 15 rows (13 + 2) with 11 joined reviews,
  4 runs with token totals, 1 orphaned result directory, and no warnings
  beyond that orphan. This check is manual. It prints only counts and is not
  committed.
- Tests use synthetic fixtures under `tests/`, with placeholder endpoints and
  paths. No real ledger lines, transcripts, hostnames or home paths are copied
  into the repo.

## Non-goals

- Server-side columns (decode tokens/s, context high-watermark, swap wait). They
  need VictoriaMetrics and arrive with unit 3.
- The scaffold's Phase 1 tool-call ledger and interactive-session transcripts
  (unit 2).
- The transition failure matrix, pass^k, or any aggregation. This spec produces
  the table only.
- Eval-run rows (Harbor and similar). Delegate runs only for now.
- Shipping or syncing files from the Mac. Inputs are local directories.
- Incremental or append-only updates. Each invocation rebuilds the whole file.
- A labeling UI, or edits to the ledgers. Inputs are read-only evidence.

## Notes

- Dependency: DuckDB (`duckdb>=1.5.6,<2`) writes the Parquet file and is the only
  new runtime dependency. Parsing and joining stay in plain Python. See
  `docs/adr/0001-duckdb-over-files.md`.
- `project` is the name of the directory that contains `.agentic/`, or the
  ledger's parent directory when the inbox copy flattens that path. The ship
  script's inbox layout is not written yet (unit 3). Discovery does not depend
  on exact paths, so it works with either layout.
- `reason` and `state.json` hold absolute local paths (worktree locations). They
  go into the Parquet as-is. That file lives under ignored `data/` and is never
  committed. The stdout summary prints run IDs and counts, not reasons.
- External references: none. The input formats are the scaffold delegate
  runner's own output, read from local evidence, not an external authority.
- Open question for unit 2: whether `endpoint` (a profile alias today) stays
  stable enough to serve as a fingerprint component, or whether the scaffold's
  fingerprint already covers it. The fingerprint is taken as recorded here.

## Standing decisions

- 2026-10-21: spec approved by the owner, with the three assumptions accepted:
  the failure-phase mapping, DuckDB writing Parquet (ADR 0001) and the `project`
  column rule.

## Implementation notes

- Test-first (2026-10-21):
  - The scope audit was clean: only `tests/` changed, and `test_smoke.py` was
    deleted.
  - Red: all 9 test files failed at collection with `ModuleNotFoundError`
    naming the planned `agentic_telemetry.cli` and
    `agentic_telemetry.run_table.*` modules.
  - Rejected-value search: not applicable, because the spec replaces no
    existing value. The only earlier test (`test_smoke.py`) is deleted.
- Departures from the approved plan, none of which change behavior:
  - `COPY ... TO ?` takes a bound parameter, so the path is never quoted into
    SQL. The one `S608` noqa sits on the INSERT, whose column list is fixed.
  - `agent_seconds` and `wall_seconds` are DOUBLE, so a fractional value from a
    future harness version still fits.
  - `ResultDir` carries the warnings for an unreadable `result.json` or
    `state.json`, and `LedgerResult` carries unknown row-type counts for the
    summary.
  - `row.py` adds two small value types, `ReviewSummary` and `FailureColumns`,
    to keep `build_row` readable.
  - `tests/test_join.py` was split into `tests/test_join_failure_columns.py`
    to stay under 300 lines.
  - Discovery does not descend into an accepted delegate directory and skips
    `.git`, `node_modules`, `.venv` and `__pycache__`, so large result caches
    are never walked.
- Fixes from the first review round (2026-10-21; both reviewers):
  - A relative DIR such as `.` is walked as an absolute path, so `project` and
    `ledger_path` stay correct.
  - A ledger line that is not UTF-8 is skipped with a `file:line` warning.
  - Transcripts are decoded with replacement characters, since only numeric
    usage fields are read.
  - An undecodable `result.json` or `state.json` warns, naming the file.
  - An orphan reached through two inputs is listed once, and identical warnings
    are printed once.
  - `agent_seconds` and `wall_seconds` are asserted as DOUBLE.
  - Added tests: discovery pruning, the unreadable result and state file
    warnings, and review timestamp parsing.
- Focused re-review fix (2026-10-21): discovery walks `os.path.abspath(DIR)`, so
  `..` also names the project, still without following symlinks.
- Owner decisions on the review's `[ask-user]` items (2026-10-21):
  1. A result directory's name is the run ID of a result-only run; a
     disagreeing `run` field warns.
  2. A green run whose latest review has no verdict fails as `unknown`.
  3. An `integrity` run with an underivable phase fails as `unknown`.
  4. Run IDs stay globally unique; a cross-project collision exits 1.
  5. The delegate-evidence projects are private, so tracked files describe them
     and do not name them. The names already in the day-zero commit stay in
     history; nothing is rewritten.
- Manual acceptance (2026-10-21), against the two private projects with delegate runs:
  - 2 ledgers, 15 rows (13 + 2), 11 reviews joined, 4 runs with token totals,
    1 orphaned result directory, and no other warnings. Exit 0.
  - Only these counts are recorded here.

## Approved implementation plan

**Approved:** 2026-10-21

Copied verbatim from the planner's output, with two changes: headings are demoted
two levels so they nest under this section, and the planner's absolute spec and
ADR paths are written as repo-relative paths. Private project names were later
replaced with a description, by owner decision.

The owner accepted the plan's defaults for the open risks:
- `ts` is stored as recorded text.
- A conflicting duplicate run (including a stale inbox copy) exits non-zero; unit
  3 revisits this.
- The README "Private" line is fixed in a separate change.
- `analysis/README.md` is skipped.

The orchestrator confirmed the field types against the evidence:
- `qualified` is a boolean.
- `gate`, `ts`, `outcome`, `bucket` and the `model.*` fields are strings.
- `unverified_steps` items are strings.
- `attempts[].seconds` and `wall_seconds` are integers. `wall_seconds` is absent
  from early rows rather than null.

### Plan: 0001 Run Table

Spec: `docs/specs/0001-run-table.md`. ADR: `docs/adr/0001-duckdb-over-files.md`. The repo has no code yet: `src/agentic_telemetry/__init__.py` holds a docstring and `tests/test_smoke.py` is a placeholder. `data/` and `*.parquet` are already in `.gitignore`. `docs/specs/README.md` defines the `## Approved implementation plan` shape as `**Approved:** YYYY-MM-DD` followed by this plan verbatim, and in the same edit you set `**Status:** shipping` and bump `**Last updated:**`.

#### Dependency check: duckdb >=1.5.6,<2 (dependency-hygiene; approving this plan approves the add)

- **Last release:** 1.5.6 is the floor. 2.0.0 came out 2026-10-21 and the `<2` cap excludes it (ADR 0001).
- **Maintainers:** DuckDB Labs / DuckDB Foundation, many contributors. No bus-factor risk.
- **License:** MIT. Compatible with this MIT repo.
- **Stdlib alternative:** none for Parquet. `sqlite3` and JSONL were rejected in the ADR.
- **Advisories:** I did not check, because the skill forbids the planner fetching PyPI. Run `uv run --with pip-audit pip-audit` after `uv add` (CI's `audit` job repeats it).
- **Transitives:** none expected. Confirm `uv.lock` adds only `duckdb`.
- **Recommendation:** add.
- **Type stubs:** I believe the duckdb wheel ships its own stubs (there is no `types-duckdb` package to add), but this is unverified. Check it at step 3 with `.agentic/toolchain.sh typecheck` once the writer imports duckdb.
  - If mypy strict reports `import-untyped` or an `Any` leak, keep the problem inside `parquet_writer.py`: annotate locals, and add a scoped `[[tool.mypy.overrides]] module = ["duckdb", "duckdb.*"]` block (`ignore_missing_imports = true`) only if that is still needed.
  - Do not use a blanket `type: ignore`. The override goes in `[tool.mypy]` territory, not `[tool.uv]`.

#### Files to touch

**Config**
- `pyproject.toml`
  - Add `dependencies = ["duckdb>=1.5.6,<2"]` via `uv add 'duckdb>=1.5.6,<2'`.
  - Add `[project.scripts] agentic-telemetry = "agentic_telemetry.cli:main"`.
  - Add the mypy override only if needed.
  - `[tool.uv]` / `[tool.uv.build-backend]` stay untouched.
- `uv.lock`: regenerated by `uv add`.

**Source**, under `src/agentic_telemetry/` (estimated lines in brackets, all well under 300):
- `cli.py` [~130]
  - `main(argv: list[str] | None = None) -> int`, using argparse with a `run-table` subparser: `--out PATH` (required) and `DIR...` (nargs="+").
  - `--help` states intent: build the run table from delegate ledgers; inputs are read-only.
  - Validation (argparse error, exit 2):
    - every DIR exists and is a directory;
    - `--out` ends in `.parquet`, is not an existing directory, and its parent exists (refuse with a message suggesting `mkdir -p`; do not auto-create).
  - Runtime failures exit 1 with a one-line message on stderr, before any file is written:
    - a DIR with no ledger (names the DIR);
    - a conflicting duplicate run (names both ledger paths).
  - Success exits 0, warnings included. The summary goes to stdout.
- `run_table/__init__.py` [~5]: docstring only.
- `run_table/json_values.py` [~80]
  - `JsonObject = dict[str, object]`.
  - Narrowing helpers (`as_object`, `opt_str`, `opt_float`, `opt_bool`, `str_list`, `as_json_text`) so mypy strict never sees `Any` from `json.loads`.
  - `as_json_text` keeps a string as recorded; anything else becomes `json.dumps(sort_keys=True)`.
- `run_table/discovery.py` [~90]
  - `find_ledgers(dirs) -> list[DiscoveredLedger(path, project, results_dir)]`.
  - Walks with `os.walk(followlinks=False)` and keeps only `ledger.jsonl` files whose parent is named `delegate` or contains `results/`.
  - Dedupes on the resolved path, so overlapping DIR arguments do not double-count.
  - Raises `NoLedgerError(dir)`.
  - `project_name(ledger_path)`: the parent of the nearest `.agentic` ancestor, otherwise the ledger's parent directory name.
- `run_table/ledger.py` [~120]
  - `parse_ledger(path) -> LedgerContents(runs: list[(line_no, JsonObject)], reviews: list[ReviewRecord], unknown_type_counts, warnings)`.
  - Malformed JSON, a non-object line, or a missing `type`/`run` produces a warning naming `path:line`.
  - An unknown `type` is counted, not warned.
- `run_table/result_dir.py` [~110]
  - `scan_results(results_dir) -> dict[run_id, ResultDir(result_json, state)]`, where `state` is `StateSummary(phase, retries, compactions)` summed over `segments`.
  - `orphaned_result_dirs(...)`: directories with no ledger row and no `result.json`.
  - `diff_keys(ledger_row, result_json) -> list[str]`.
- `run_table/session_usage.py` [~90]
  - `read_session_usage(session_dir) -> SessionUsage | None`. Sums `usage.input/output/cacheRead/reasoning` over every `*.jsonl` where `type=="message"` and `message.role=="assistant"`, and counts assistant turns.
  - Returns `None` when there is no transcript.
  - Malformed transcript lines give one warning per file (count plus first line number), not one per line.
- `run_table/failure_phase.py` [~80]
  - `delegated_phase(bucket, state_phase)`, in this order:
    1. `state.json` phase;
    2. the `phase:` bucket prefix;
    3. bucket `small`/`medium` gives `implement`;
    4. otherwise null with a warning.
  - `first_failure(outcome, verdict, delegated_phase) -> (phase, cause, warning|None)`, following the spec's table exactly. For an unknown outcome the cause is the outcome itself.
  - A phase outside {spec, plan, test-first, implement, gate, review} gives a warning, and the value is kept as recorded.
- `run_table/row.py` [~90]
  - `RunRow` frozen dataclass whose field order is the column order in the spec.
  - `build_row(project, ledger_path, run, review, usage, state, phase) -> RunRow`.
  - Flattening rules:
    - `model.*` fields become columns;
    - `request_params` comes from `as_json_text`;
    - `attempts` is the count, `attempt_outcomes` the list, and `agent_seconds` the sum of attempt seconds (null when there are no attempts);
    - `wall_seconds` is null when absent.
- `run_table/join.py` [~120]
  - `assemble_ledger(discovered) -> LedgerResult(rows, reviews_joined, orphans, warnings)`.
  - The ledger row wins over `result.json`, with a warning naming the run and the differing keys.
  - `result.json` fills in a run that has no ledger row.
  - Reviews:
    - the latest `ts` wins, compared after `datetime.fromisoformat` with the later line breaking ties;
    - `review_count` records how many there were;
    - a review whose run ID has no run row gives a warning.
  - Each orphaned result directory gives a warning.
- `run_table/dedupe.py` [~60]
  - `merge_runs(results) -> list[RunRow]`.
  - Rows with the same `run_id` collapse to one when they are equal on every column except `project` and `ledger_path`; the first discovered row is kept.
  - Otherwise it raises `ConflictingRunError(run_id, path_a, path_b)`. The same rule applies to duplicates inside one ledger.
- `run_table/build.py` [~60]: `build_run_table(dirs) -> (rows, Summary)`. It wires discover, parse, join and dedupe. It is pure apart from file reads and does no writing.
- `run_table/summary.py` [~80]
  - `Summary` dataclass plus `render_summary(summary) -> str`.
  - Contents: ledgers found, run rows written per project, reviews joined, runs with token totals, orphaned result directories (project/run_id), and every warning.
  - It prints run IDs and counts, never `reason` text.
- `run_table/parquet_writer.py` [~80]: the only module that imports duckdb.
  - It uses an in-memory connection and creates a table with an explicit DDL schema, with `VARCHAR[]` for the list columns and `DOUBLE`/`BIGINT`/`BOOLEAN` as appropriate. The explicit schema keeps the column types fixed even when a column is all null.
  - Rows go in through `executemany` INSERT and out through `COPY ... TO '<tmp>' (FORMAT parquet)`. The output is written to a sibling `.tmp` file and then moved into place with `Path.replace`, so the write is atomic.
  - `COPY TO` probably cannot take a bound parameter. If so, single-quote-escape the path and add a justified `# noqa: S608`.
  - It also exports `RUN_TABLE_SCHEMA` so a test can assert it matches `RunRow`'s fields.

**Tests**, under `tests/`, all synthetic, with endpoint `gpu.example`, `tmp_path` paths, and no home paths:
- `run_table_builders.py`: plain functions `write_ledger(dir, lines)`, `run_record(**overrides)`, `review_record(...)`, `write_result_dir(...)`, `write_session(...)`, `write_state(...)`, `make_project(tmp_path, layout="agentic"|"inbox")`.
- `conftest.py`: thin fixtures over the builders, e.g. a two-project tree with one orphan.
- `test_smoke.py`: delete it. Its docstring asks to be replaced once real tests exist.
- Test files, mapped to spec criteria:

| Test file | Covers (spec criterion) |
|---|---|
| `test_discovery.py` | Discovery rule (`delegate` parent / `results/` sibling; ignores other `ledger.jsonl`); `project` for both layouts; overlapping DIRs; `NoLedgerError` names the dir |
| `test_ledger.py` | run/review parsing; malformed line, missing `type`, missing `run` each warn with `file:line`; unknown type counted, no warning |
| `test_result_dir.py` | `result.json` fill-in; differing keys named; `state.json` phase/retries/compactions; orphan detection; older run with no result dir |
| `test_session_usage.py` | token sums across several `*.jsonl`; first `session` line and non-assistant lines ignored; no transcript gives `None` |
| `test_failure_phase.py` | Parametrized table: green+pass, green+no review, green+fail, needs_frontier, integrity, unknown (+warning); delegated phase from state.json, `phase:` prefix, small/medium |
| `test_join.py` | Latest review wins, `review_count`; orphan review warning; column flattening (`request_params` verbatim string, attempts, `agent_seconds`, null `wall_seconds`) |
| `test_dedupe.py` | Identical across two inputs gives one row; differing raises an error naming both paths |
| `test_parquet_writer.py` | Round-trip read back through `duckdb.sql(...)`: exact column names and order, list columns are lists, null `wall_seconds`, row count, overwrite of an existing file, empty table keeps the schema; schema matches `RunRow` fields |
| `test_cli.py` | `main([...])` end to end: file written, summary lines (ledgers, per-project rows, reviews joined, token runs, orphan, warnings); exit 2 for nonexistent DIR / bad `--out`; exit 1 for no-ledger DIR and conflicting duplicate, with no file written; `--help` exit 0 with the intent text; `agentic-telemetry` console-script entry point registered (`importlib.metadata.entry_points`) |

**Docs**
- `AGENTS.md`
  - "How to run things": replace the "Run app" line with `uv run agentic-telemetry run-table --out data/run_table.parquet DIR...`.
  - Stack line: DuckDB is settled (ADR 0001).
  - "Open work / current state": 0001 shipped, next is unit 2/3.
- `README.md`: replace the Quick start line "no entry point yet" with the run-table command. That is a user-facing run change.
- `analysis/README.md`: one line under Outputs item 1 saying the delegate-run columns ship in 0001 and the server columns arrive with unit 3. This is optional; skip it if you would rather not touch it.
- `docs/specs/0001-run-table.md`:
  - at approval, the plan section plus the `shipping` status;
  - at docs time, `shipped` and a regenerated dashboard.

#### Order of operations

1. Orchestrator: persist this plan in the spec and set `shipping`. Confirm you are on branch `spec-0001-run-table`.
2. `uv add 'duckdb>=1.5.6,<2'`, add `[project.scripts]`, then run `uv run --with pip-audit pip-audit`.
3. Typecheck spike: run `.agentic/toolchain.sh typecheck` against a trivial `import duckdb` in `parquet_writer.py`. If needed, decide on the mypy override now, before tests.
   - This adds a stub module before `/test-first`. If that breaks your audit rule, fold the check into step 5 instead.
4. `/test-first`: write the builders, conftest and the nine test files above. Delete `test_smoke.py`. The expected red is `ModuleNotFoundError` for the planned `agentic_telemetry.run_table.*` and `agentic_telemetry.cli` modules.
5. Implement bottom-up, running each focused test as you go:
   - `json_values` → `discovery` → `ledger` → `result_dir` → `session_usage` → `failure_phase` → `row` → `join` → `dedupe` → `summary` → `parquet_writer` → `build` → `cli`.
6. Docs edits (above). Mark the spec `shipped` and regenerate the dashboard.
7. Gate: `.agentic/toolchain.sh gate`. Then:
   - run `git grep -nE '/(Users|home)/' -- src tests` and a scan for non-`.example` hostnames in `tests/`;
   - run `.agentic/hooks/closeout-check.sh`.
8. Manual acceptance (orchestrator only, nothing committed): run the CLI against the two private projects with delegate runs with `--out data/run_table.parquet`.
   - Expect 15 rows (13 + 2), 11 reviews joined, 4 runs with token totals, 1 orphan, and no other warnings.
   - Record only the counts.
9. `/review-check`, then `/review` and `/review-adversarial`. Stop for commit approval.

#### Risks / open questions

- **JSON types of some run-row fields are not in the spec:** `qualified`, `gate`, the `unverified_steps` items, `attempts[].seconds`, `wall_seconds` and `ts`. The DuckDB schema needs them.
  - Defaults: `qualified` BOOLEAN, `gate` VARCHAR (JSON text if not a string), `unverified_steps` VARCHAR[] (non-string items as JSON text), seconds DOUBLE.
  - The orchestrator should confirm the types (not the values) against the evidence before `/test-first`, so the synthetic fixtures match.
- **`ts` / `review_ts` are stored as recorded strings (VARCHAR)**, not TIMESTAMPTZ, so nothing is lost. Unit 3 can cast. This is cheap to change now if you prefer typed timestamps.
- **`compactions` / `retries` come only from `state.json` (v2).** A handoff run that has a transcript but no `state.json` gets token totals and null compactions/retries. The spec groups them under "null when there is no transcript", which this only partly satisfies.
- **Duplicate run rows inside one ledger** use the cross-input rule: identical rows collapse, differing rows exit 1. The spec covers only cross-input duplicates. If the runner ever rewrites a run row on retry, step 8 will expose it.
- **Stale inbox copies fail the run.** "Identical" compares every column except `project` and `ledger_path`, so an inbox copy that is missing a later review makes the run exit non-zero. That follows the spec's wording but will bite once unit 3 ships stale copies.
- **The `project` rule for an inbox path ending in `.../delegate/ledger.jsonl` without `.agentic`** yields `delegate`. The spec's literal rule allows it; refine it in unit 3 when the inbox layout exists.
- **The run row's own `review` field** has no column in the spec, so it is ignored.
- **The `COPY TO` path is likely a SQL literal, not a parameter.** It needs quote escaping and a justified `S608` noqa.
- **Dependabot may propose widening `duckdb` past `<2`.** Decline until the 2.x evaluation.
- **Scope note (not in the spec):** `README.md` line 6 says "Private: it names homelab hosts", which conflicts with the treat-as-public rule. I would not touch it here; raise it separately.
- No `[DECISION NEEDED]` items. Every choice above is a default you can override at approval.

#### Out of scope (won't touch)

- Server-metric columns, the Phase 1 tool-call ledger, interactive transcripts, aggregation, pass^k, eval-run rows, incremental updates, any labeling UI (spec non-goals).
- `pyproject.toml` `[tool.uv]` and `[tool.uv.build-backend]`; `collector/`; `.agentic/toolchain.sh`; CI workflows; `THIRD_PARTY_NOTICES.md` (nothing is copied).
- Any edit to ledgers or evidence. No real evidence lines in tests. Nothing under `data/` is committed.
