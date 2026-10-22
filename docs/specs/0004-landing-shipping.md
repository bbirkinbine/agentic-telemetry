# 0004 — Landing-Zone Shipping and Ledger Ingestion

**Status:** shipped
**Last updated:** 2026-10-22
**Depends on:** 0003
**Roadmap unit:** 3 (with 0003, 0005, 0006, 0007)

## Goal

Get file-based evidence from the Mac into the collector landing zone, in a layout the
run table and session table already read. Then have the Collector ingest the
ledgers, and only the ledgers, with their own timestamps. This settles what 0001
and 0002 left for unit 3:

- the landing-zone layout;
- the two run-table defaults that layout broke (a stale landing-zone copy exits 1, and
  a landing-zone ledger without `.agentic` gets `project = "delegate"`);
- the product spec's open question of whether transcripts reach the stores
  through Collector tailing or a batch load.

### Facts checked against local evidence on 2026-10-21

Only key names, timestamp shapes and arithmetic were inspected, never content.

- `mac-agent/ship-ledgers.sh` copies `.agentic/delegate/` to
  `landing/<host>/<project>/delegate/`, dropping `.agentic`. That layout triggers
  0001's `project = "delegate"` default.
- It globs one level under `PROJECTS_ROOT` (default `$HOME/src`). The owner's
  projects sit two levels down (`<root>/<owner>/<project>`), so it finds none.
- It ships `*.jsonl` only. 180 of the 186 Codex rollouts are `.jsonl.zst`,
  which 0002 reads, so they never reach the landing zone.
- Ledger `ts` values have whole seconds and no fraction (26 of 26). The
  Collector's `filelog/landing` layout `%Y-%m-%dT%H:%M:%S.%LZ` requires
  milliseconds and fails on every ledger line.
- `filelog/landing` tails `/landing/**/*.jsonl`, which includes the `_sessions/`
  transcripts. Those carry full captured tool content into Loki for two years,
  behind only the regex redaction layer.

## Success criteria

- **Help and argument errors.** `ship-ledgers.sh --help` states the script's
  intent and options and exits 0. An unknown option exits 2 with a message
  naming the option.
- **Project discovery.** The script ships every directory under
  `PROJECTS_ROOT` that holds `.agentic/delegate/` or `.agentic/telemetry/`, at
  any depth up to `--max-depth` (default 3). The search does not descend into a
  project it found, `.git`, `node_modules` or any hidden directory.
  `PROJECTS_ROOT` keeps its `$HOME/src` default; the owner sets it in the shell
  or launchd; a `PROJECTS_ROOT` that does not exist exits 2 naming it, and one
  with no projects prints one line saying so (D5). The project name is the
  directory's basename. Two projects with the same basename are both skipped,
  with one line naming both paths; every other project and the transcripts still
  ship, and the script exits 1 (D6, replacing "exit 1 before anything is
  copied").
- **What ships (reader allowlist).** From `.agentic/delegate/`, exactly what
  `run-table` reads: `ledger.jsonl`, `results/<run>/result.json`,
  `results/<run>/state.json` and `results/<run>/session/*.jsonl`. From
  `.agentic/telemetry/`, `*.jsonl`. Nothing else: not `pi-agent/` (a Pi home that
  can hold credentials), `npm-cache/`, `segments/`, `*.log`, `prompt.md` or
  `handoff.json`. A test plants `results/<run>/pi-agent/auth.json` and
  `npm-cache/` and checks that neither is copied. Symlinks are never copied,
  as links or as their targets (review fix).
- **Layout.** Project evidence lands at
  `landing/<host>/<project>/.agentic/{delegate,telemetry}/...`, keeping `.agentic`
  so 0001's `project` rule (parent of `.agentic`) holds unchanged. Transcripts
  land at `landing/<host>/_sessions/{claude-code,pi,codex}/...`, including Codex
  `.jsonl.zst`.
- **Destinations.** `--dest DIR` copies into a local directory, such as 0003's
  local landing zone. It is created when missing; a missing parent exits 2. The
  default (VM over SSH) is unchanged. A second run copies nothing new. An
  unreachable VM exits 0 and prints one line saying it skipped.
- **Copy failures.** A failed copy prints one line naming its source and the
  script continues with the rest; it exits 1 at the end if any copy failed. A
  client session directory that does not exist is skipped silently. No copy uses
  `--delete`: the landing zone keeps files the clients later prune.
- **Tests.** Tests drive the script with `--dest` into `tmp_path` against
  synthetic project trees. No real paths are committed.
- **Ingestion.** `filelog/landing` tails only ledgers:
  `landing/*/*/.agentic/delegate/ledger.jsonl` and
  `landing/*/*/.agentic/telemetry/*.jsonl`. It tails no transcript, neither
  `_sessions/` nor a delegate run's `results/<run>/session/*.jsonl`. Transcripts
  stay as files in the landing zone and are read in batch by `session-table` and
  `run-table`, which settles the product spec's tailing-vs-batch question for
  transcripts in favour of batch.
- **Timestamps.** The timestamp parser accepts both `...SSZ` and
  `...SS.fffZ`. A ledger line in Loki carries its own `ts`, not the ingest time,
  checked on 0003's local stack.
- **Validation.** The edited `otel-collector.yaml` passes `otelcol-contrib
  validate` on the pinned image, recorded in the README.
- **Stale landing-zone copy.** When two inputs carry the same run ID with
  identical run rows, and one input's review rows for that run are a subset of
  the other's, the superset wins. A warning names both ledger paths. Any other
  difference (a run field, or reviews that disagree) still exits 1, as in 0001.
- **`project` for landing-zone copies.** A test pins the rule: a ledger at
  `landing/<host>/<project>/.agentic/delegate/ledger.jsonl` yields
  `project = <project>`.
- **Same rows from the landing zone (manual check, counts only).** `run-table` and
  `session-table` pointed at the local `landing/<host>/` give the same rows and
  `project` values as when pointed at the Mac's project roots and session
  directories.

## Non-goals

- Scheduling the script under launchd, and shipping to the real VM (0006).
- Pruning the landing zone. `collector/README.md`'s "until processed + 30 days" stays a
  plan; nothing deletes landing-zone files.
- The scaffold's Phase 1 tool-call ledger reader (unit 2). The layout carries
  `.agentic/telemetry/` so those files arrive when Phase 1 lands.
- Tailing transcripts into Loki.

## Notes

- The script is shell because it wraps `rsync` and runs from launchd. It follows
  the standing rule on hardened tools: `--help`, argument validation, and loud,
  specific errors.
- **Don't-touch list.** `collector/*.yaml` edits are re-validated against the
  pinned images and recorded in `collector/README.md`.

## External references

- **OTel Collector contrib `filelog` receiver**: `include`/`exclude` globs and
  the stanza timestamp layouts. Authoritative source: the receiver's README in
  `opentelemetry-collector-contrib`, Apache-2.0. The URL is pinned at plan time
  by an in-session fetch, and the config is checked by `otelcol-contrib
  validate`.
- **OTel Collector contrib stanza timestamp parser**: `layout_type: gotime`.
  Authoritative source: `pkg/stanza/docs/types/timestamp.md` in
  `opentelemetry-collector-contrib` at the tag matching the pinned 0.163.1 image,
  Apache-2.0. Fetched in-session at implement time.
- **OTel Collector contrib stanza `regex_parser` and field types**: `parse_to:
  resource` (D4). Authoritative source:
  https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.163.0/pkg/stanza/docs/operators/regex_parser.md
  and `pkg/stanza/docs/types/field.md` at the same tag, Apache-2.0, retrieved
  2026-10-22.
- **Go `time.Parse`**: a fractional second after the seconds field is accepted
  even when the layout has none, so one `gotime` layout parses both `...SSZ` and
  `...SS.fffZ` (D3). Authoritative source: https://pkg.go.dev/time#Parse,
  BSD-3-Clause. Fetched in-session at implement time.

## Standing decisions

Answered by the owner on 2026-10-22, approving the plan with every recommended
default:

- **D1, Loki's old-sample limit.** `loki-config.yaml` is not changed in 0004.
  Loki's default rejects samples older than a week, so a first backfill of older
  ledger lines is refused by Loki (the archive keeps every line). The README
  states the limit, and the live check uses a fresh ledger line. Widening Loki's
  history window belongs with 0006.
- **D2, client session overrides.** The script resolves each client's session
  directory the same way `session_table/sources.py` does, honoring
  `PI_CODING_AGENT_SESSION_DIR`, `PI_CODING_AGENT_DIR`, `CLAUDE_CONFIG_DIR` and
  `CODEX_HOME`.
- **D3, Go `time.Parse` as an External reference.** Added above.
- **Delegation mode: `direct`.** This session implements; `local-qwen38-flash-next`
  is demoted for `phase:implement` after 0005.
- **D4, one Loki stream per project (owner, 2026-10-22, after the live check).**
  The `regex_parser` writes `landing_host` and `landing_project` to resource
  attributes (`parse_to: resource`), which `loki-config.yaml` already makes index
  labels. As log attributes they were only structured metadata, so every ledger
  shared one Loki stream, and Loki refused a line "too far behind" another
  project's newer one: on the live stack the backfill kept 5 of 28 lines, and the
  same loss can hit steady state whenever one project's lines reach Loki more
  than an hour after another's. This is a change to the approved plan's
  `otel-collector.yaml` edit, test-first in `tests/test_collector_landing_filelog.py`.
  `loki-config.yaml` is unchanged; D1 still holds.

Answered by the owner on 2026-10-22, after `/review` and `/review-adversarial`:

- **D5, project root.** A `PROJECTS_ROOT` that does not exist exits 2 naming it,
  before any copy. One that exists but yields no projects prints
  `ship-ledgers: no projects found under <root> (depth N)` and still ships the
  transcripts, exit 0: under launchd, macOS privacy settings can hide a
  protected directory's contents, and that line makes it visible.
- **D6, basename collision.** Both colliding projects are skipped with one line
  naming both paths; every other project and the transcripts still ship, and the
  script exits 1. One lasting collision no longer halts all shipping.
- **D7, 0006 scope.** 0006's draft records, for its clarify step, Loki's
  one-week window (D1) and an easier install for the inference-host agent.
- **D8, one stream per project, not per file.** A project's delegate ledger and
  its Phase 1 `telemetry/*.jsonl` files share that project's Loki stream, so a
  backfill or an outage of more than an hour could still refuse older session
  files' lines. Nothing ships to `telemetry/` yet; the limit is documented, the
  archive keeps every line, and the stream labels are revisited when Phase 1
  lands.

Answered by the owner on 2026-10-22, after the focused re-review:

- **D9, symlinked evidence directory.** A project whose `.agentic`,
  `.agentic/delegate` or `.agentic/telemetry` is a symlink is skipped with one
  line naming it, and the script exits 1; other projects ship. Copies skip
  symlinks, so following discovery's lead would have shipped nothing while
  printing "shipped".
- **D10, offline exit status.** An unreachable VM exits 0 for a plain offline
  run, and 1 when discovery already found a problem (a collision or a symlinked
  `.agentic`), so a launchd monitor sees it while the VM is down.

## Approved implementation plan

**Approved:** 2026-10-22

This is the planner's plan, copied verbatim. Headings are demoted so they nest
under this section, and the repo-absolute spec path is shortened to a
repo-relative one. The owner approved it with the recommended defaults for
D1–D3 (Standing decisions).

### Plan: 0004 Landing-zone shipping and ledger ingestion

Spec: `docs/specs/0004-landing-shipping.md`

#### Files to touch
- `collector/mac-agent/ship-ledgers.sh`: full rewrite (details below).
- `collector/otel-collector.yaml`: change `filelog/landing` `include`, change the timestamp layout, update the header and receiver comments. This file is on the don't-touch list, so it gets re-validated and the result recorded in the README.
- `src/agentic_telemetry/run_table/join.py`: `LedgerResult` gains `review_keys: dict[str, Counter[tuple[str | None, str | None]]]`, the multiset of each run's review `(ts, verdict)` pairs, filled from `contents.reviews`.
- `src/agentic_telemetry/run_table/dedupe.py`: add the stale-copy rule. `merge_runs` takes the review multisets keyed by `(ledger_path, run_id)` and returns rows plus warnings.
- `src/agentic_telemetry/run_table/build.py`: pass the review multisets to `merge_runs` and add its warnings to `summary.warnings`.
- `collector/README.md`: update the layout, the batch-vs-tail rule, the script name and path, the retention row, bring-up step 7, and the Verification entries.
- `collector/landing/README.md`: update the layout and say ingestion is ledgers only. It is the tracked exception in `.gitignore`: documentation, not evidence.
- `AGENTS.md`: current-state block (0004 shipped; next is 0006).
- `docs/specs/0004-landing-shipping.md`: status flips only, plus the External-references addition in D3. Non-goals stay as they are.
- `tests/ship_ledgers_helpers.py` (new): runs the script, builds synthetic project trees and HOME session directories, and writes the stub `bin/` directory.
- `tests/test_ship_ledgers_cli.py` (new)
- `tests/test_ship_ledgers_discovery.py` (new)
- `tests/test_ship_ledgers_allowlist.py` (new)
- `tests/test_ship_ledgers_sessions.py` (new)
- `tests/test_ship_ledgers_runs.py` (new)
- `tests/test_ship_ledgers_readers.py` (new)
- `tests/test_collector_landing_filelog.py` (new)
- `tests/test_dedupe.py`: replace `test_stale_copy_missing_a_review_is_a_conflict`, which pins the 0001 default this spec reverses.
- `tests/test_dedupe_stale_copy.py` (new)
- `tests/test_discovery.py`: add the end-to-end `project` rule test.

##### 1. `ship-ledgers.sh`
- **Shell compatibility.** It must run on bash 3.2, because macOS `/bin/bash` is 3.2 and launchd uses it. That rules out `declare -A`, `mapfile` and `${x,,}`, and with `set -u` an empty array must be guarded (`${arr[@]+"${arr[@]}"}`). Tests run it as `/bin/bash <script>`, which is 3.2 on the Mac gate and 5.x in CI.
- **Options.**
  - `--help` prints intent, options and environment variables (`PROJECTS_ROOT`, `TELEMETRY_VM`, `TELEMETRY_LANDING`, the client session overrides) and exits 0.
  - `--dest DIR` is the landing root; the script writes under `DIR/<host>/`, the same shape as the default `$TELEMETRY_LANDING/<host>`.
  - `--max-depth N` takes an integer of 1 or more; the default is 3.
  - Exit 2, with a message naming the problem, for: an unknown option, any positional argument, a missing option value, a `--max-depth` that is not a positive integer, a `--dest` whose parent is missing, and a `--dest` that names an existing file.
  - `--dest` is created with a single `mkdir`, not `mkdir -p`, so a missing parent stays an error.
- **Order of work.** Parse arguments, then discover projects, then check basename collisions (exit 1 naming both paths, before any copy), then check reachability (remote mode only), then copy.
- **Discovery.** A small recursive bash function, not `find`. It walks from depth 1 (children of `PROJECTS_ROOT`) up to `--max-depth`. A directory is a project if `.agentic/delegate/` or `.agentic/telemetry/` exists in it; a project is recorded and not descended into. The walk skips `.git`, `node_modules`, every hidden directory, and symlinked directories (to avoid loops). Collisions are found by comparing basenames with plain indexed arrays.
- **Allowlist.** One rsync per project, with the project root as source and `<dest>/<host>/<project>/` as destination. Only anchored include patterns and a final exclude:
  ```
  --include=/.agentic/ --include=/.agentic/delegate/ --include=/.agentic/delegate/ledger.jsonl
  --include=/.agentic/delegate/results/ --include=/.agentic/delegate/results/*/
  --include=/.agentic/delegate/results/*/result.json --include=/.agentic/delegate/results/*/state.json
  --include=/.agentic/delegate/results/*/session/ --include=/.agentic/delegate/results/*/session/*.jsonl
  --include=/.agentic/telemetry/ --include=/.agentic/telemetry/*.jsonl --exclude=*
  ```
  - The final `*` excludes `pi-agent/`, `npm-cache/` and `segments/` without rsync descending into them.
  - Because rsync runs from the project root, only `<dest>/<host>/` has to exist beforehand. That avoids `--mkpath`, which needs GNU rsync 3.2.3 or later, and avoids `-R` with `/./`.
  - Copy flags are `-a` only: no `--delete` and no `-m`.
- **Transcripts.** Copied to `<dest>/<host>/_sessions/{claude-code,pi,codex}/` with `--include=*/ --include=*.jsonl --exclude=*`; Codex also gets `--include=*.jsonl.zst`. A client directory that does not exist is skipped silently.
  - Source directories use the same resolution as `session_table/sources.py`: `PI_CODING_AGENT_SESSION_DIR`, then `PI_CODING_AGENT_DIR/sessions`, then `~/.pi/agent/sessions`; `CLAUDE_CONFIG_DIR/projects`; `CODEX_HOME/sessions`. See D2.
- **Remote mode.**
  - `nc -z -w 3 "$VM" 22`. On failure it prints one line (`ship-ledgers: <vm> unreachable, skipped`) and exits 0.
  - Then a single `ssh "$VM" mkdir -p -- "$LANDING/$HOST/_sessions"`.
  - rsync destinations take the form `"$VM:$LANDING/$HOST/<project>/"`.
  - `TELEMETRY_VM` (default `telemetry.example`) and `TELEMETRY_LANDING` behave as today.
- **Failures.**
  - Each rsync runs inside `if ! ...; then`. A failure prints `ship-ledgers: copy failed: <source>` to stderr, sets `failed=1` and moves on.
  - Exit 1 at the end if anything failed.
  - Each successful copy prints one line naming the project or client.
- **rsync implementations.**
  - macOS 15.4 and later ships `openrsync` as `/usr/bin/rsync`, with a smaller filter-rule implementation than GNU 3.x. The script relies only on `-a`, `--include`/`--exclude`, anchored `/` patterns, trailing `/` for directory-only matches, and `*`. No `**`, `--filter`, `--files-from`, `--mkpath`, `-m` or `-R`.
  - The implementer checks that subset against `man openrsync` on the Mac in-session.
  - The tests run whichever `rsync` is first on `PATH`: openrsync (or Homebrew rsync) in the local gate on the Mac, GNU rsync on the CI runner. The allowlist test therefore exercises both implementations whenever the local gate and CI both run.
  - CI: `ubuntu-latest` images include rsync. The implementer confirms this in the runner-images Ubuntu 24.04 readme. If it is missing, add an `apt-get install -y rsync` step to `.github/workflows/ci.yml`, which is scope expansion and needs approval. Tests fail with a named message if `rsync` is absent; they do not skip.
- **Synthetic HOME in tests.** Each subprocess gets an environment built from nothing: `PATH` (stub `bin/` first), `HOME=tmp_path/home`, `PROJECTS_ROOT=tmp_path/projects`, plus any per-test client overrides. The developer's real `HOME`, `CLAUDE_CONFIG_DIR` or `CODEX_HOME` never reach the script. The host directory is read from the one child of `--dest`, so `hostname -s` is never asserted and stays out of the repo.

##### 2. Collector `filelog/landing`
- `include: [/landing/*/*/.agentic/delegate/ledger.jsonl, /landing/*/*/.agentic/telemetry/*.jsonl]`. No `exclude` is needed because the globs are exact; an optional belt-and-braces `exclude: [/landing/*/_sessions/**]` is the implementer's call.
- Timestamp: `layout_type: gotime`, `layout: '2006-01-02T15:04:05Z07:00'`.
  - Go's `time.Parse` accepts a fractional-second field after the seconds even when the layout has none, so `...56Z` and `...56.123Z` both parse.
  - `Z07:00` also accepts `+00:00`, which review rows may carry.
  - strptime cannot express an optional fraction (`%L` requires one).
- URLs to pin. The implementer fetches each in-session and puts the retrieval date and license in a comment above the receiver:
  - `https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.163.0/receiver/filelogreceiver/README.md` (Apache-2.0). Use the tag that matches the 0.163.1 image if one exists; otherwise the newest tag at or below it, as `compose.yaml` does for telemetrygen.
  - `https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.163.0/pkg/stanza/docs/types/timestamp.md` (Apache-2.0), for `layout_type: gotime`.
  - `https://pkg.go.dev/time#Parse` (BSD-3-Clause), for the optional-fraction rule. See D3.
- The `regex_parser` (`^/landing/(?P<landing_host>[^/]+)/(?P<landing_project>[^/]+)/`) still matches `/landing/<host>/<project>/.agentic/...` with the same groups, so it is unchanged. A test pins this.
- Re-validate with the command in the yaml header, on the arm64 entry and on the amd64 manifest digest under emulation, as before. Record it in README Verification.
- Live check on 0003's stack (manual, at verify):
  1. `docker compose up -d --force-recreate otel-collector`.
  2. `ship-ledgers.sh --dest "$TELEMETRY_ROOT/landing"`.
  3. Query Loki for `{deployment_environment="homelab"} | log_file_path=~".*/ledger.jsonl"` and compare each entry's timestamp with its line's `ts` using `jq`, printing timestamps only.
  4. Confirm a count query on `log_file_path=~".*/(_sessions|session)/.*"` returns 0 for new entries.
  5. Record counts only in the README.

##### 3. Run-table stale-copy rule
- It lives in `dedupe.merge_runs`; `cli.py` already turns `ConflictingRunError` into exit 1.
- "Run rows identical" means every field except the location fields (`project`, `ledger_path`) and the review-derived fields (`review_verdict`, `review_ts`, `review_count`, `first_failure_phase`, `failure_cause`).
- Review identity is the multiset of `(ts, verdict)`; `line_no` is not part of it.
- When the review multisets are equal, keep the first row, as today, with no warning.
- When one multiset is strictly contained in the other (`Counter <=`), the superset's row replaces the kept row in place, so first-seen order is kept. The warning names the run, both ledger paths, and the review counts.
- Anything else raises `ConflictingRunError`. The message names "reviews" when only the reviews disagree.
- The server join runs after the merge, so the `server_*` columns are still defaults during this comparison.

##### 4. `project` rule and readers on `landing/<host>/`
- No code change is expected. `project_name` already returns the parent of `.agentic`. Run-table discovery walks hidden directories (only `.git`, `node_modules`, `.venv` and `__pycache__` are skipped) and walks into `_sessions/` harmlessly.
- Session-table classifies by content, reads `_sessions/**` and `results/<run>/session/*.jsonl` (where `delegate_run_id` works), and counts `ledger.jsonl` and `telemetry/*.jsonl` as unrecognized.
- New tests pin all of this. They are expected to pass on first run (see Risks).

##### 5. Docs
`collector/README.md`:
- the "How each source gets in" Delegate and Scaffold rows: the script path and the `.agentic` layout;
- the diagram caption and "Flow of records" step 3: the Collector tails ledgers only, and transcripts are read in batch;
- the retention row: "plan; nothing deletes landing files yet";
- bring-up step 7;
- new Verification entries.

`collector/landing/README.md`: layout and ingestion scope. `AGENTS.md`: current state.

#### Order of operations
1. The orchestrator settles the D-items, persists this plan in the spec, and marks it `shipping` (with the D3 External-references line).
2. `/test-first` writes the tests below. Expected reds:
   - the script tests fail on behavior (wrong layout, missing options, exit codes);
   - the filelog tests fail on the include globs and layout;
   - the stale-copy tests fail on `ConflictingRunError`, or on a `TypeError`/`AttributeError` from the new `merge_runs` signature;
   - the `project`-rule and readers-on-landing tests may already pass, as characterization pins.
3. Implement `dedupe.py`, `join.py` and `build.py`, then run the focused tests.
4. Rewrite `ship-ledgers.sh`. Run its tests locally on the Mac (openrsync or Homebrew rsync, `/bin/bash` 3.2).
5. Edit `otel-collector.yaml`: fetch the pinned URLs and add the provenance comment, then run `otelcol-contrib validate` on arm64 and amd64.
6. Docs: both READMEs and `AGENTS.md`. Mark the spec shipped.
7. `.agentic/toolchain.sh gate`, then `/review-check`, `/review` and `/review-adversarial` (the change touches a credential boundary: `pi-agent/`).
8. Manual verify steps:
   - the live Loki timestamp check;
   - the same-rows check (counts and `project` values only) for run-table and session-table, comparing project roots plus default session directories against `landing/<host>/`;
   - recording both in the README.
9. Stop for commit approval.

##### Tests (what each pins)
- `test_ship_ledgers_cli.py`:
  - `--help` exits 0 and names `--dest`, `--max-depth` and `PROJECTS_ROOT`;
  - an unknown option exits 2 and names the option;
  - a positional argument, a bad or zero `--max-depth`, and a missing `--dest` value exit 2;
  - a missing `--dest` parent exits 2 and creates nothing;
  - a missing `--dest` is created;
  - the script text contains no `--delete`.
- `test_ship_ledgers_discovery.py`:
  - a project at depth 2 is shipped;
  - depth beyond `--max-depth` is not;
  - projects under `.git`, `node_modules` or a hidden directory are not found;
  - a nested project inside a found project is not shipped separately;
  - a project with only `.agentic/telemetry/` is found;
  - a basename collision exits 1, names both paths, and leaves `--dest` empty of projects.
- `test_ship_ledgers_allowlist.py`:
  - the set of relative files under `<host>/<project>` equals exactly the allowlist;
  - the planted `results/<run>/pi-agent/auth.json`, `npm-cache/`, `segments/`, `*.log`, `prompt.md`, `handoff.json` and `last-diagnostics.txt` are absent;
  - `.agentic` is kept in the path.
- `test_ship_ledgers_sessions.py`:
  - `_sessions/{claude-code,pi,codex}` are populated, Codex `.jsonl.zst` included and non-JSONL files excluded;
  - a missing client directory gives no output line and exit 0;
  - the override variables are honored (D2).
- `test_ship_ledgers_runs.py`:
  - a second run leaves each destination file's inode unchanged, so nothing was re-copied;
  - a file deleted at the source survives in the destination;
  - a stub `rsync` wrapper that fails for one project and passes through to the real rsync otherwise gives one stderr line naming that source, ships the other project, and exits 1;
  - in remote mode a stub `nc` exiting 1 gives exit 0, exactly one output line, and no `rsync` or `ssh` call (the stubs record calls);
  - remote mode with stub `nc` 0, `ssh` and `rsync` sends to `telemetry.example:/srv/telemetry/landing/<host>/<project>/`.
- `test_ship_ledgers_readers.py`:
  - `build_run_table` on `dest/<host>` equals the result on the source projects, apart from `ledger_path`;
  - `project` is the project basename;
  - `build_session_table` on `dest/<host>` gives the same `(client, session_id)` keys as the synthetic HOME directories plus the project roots.
- `test_collector_landing_filelog.py`:
  - each include glob is checked with `PurePosixPath.match`: absolute patterns and no `**`, so 3.12 semantics match;
  - the include globs match the two ledger shapes and reject `_sessions/**`, `results/*/session/*.jsonl` and `result.json`;
  - `layout_type == "gotime"` and the layout is `2006-01-02T15:04:05Z07:00`;
  - the `regex_parser` regex (Python `re`) extracts host and project from a new-layout path;
  - `on_error: send_quiet` is kept.
- `test_dedupe_stale_copy.py`:
  - the superset wins in either input order, with a warning naming both ledger paths;
  - zero reviews against one review counts as a subset;
  - a duplicate review is checked as a multiset subset;
  - disagreeing verdicts raise;
  - a run field that differs while the reviews are a subset still raises.
- `test_discovery.py`: `find_ledgers([landing/<host>])` on `landing/<host>/<project>/.agentic/delegate/ledger.jsonl` returns `project == <project>`.

Every fixture is built in `tmp_path` with the existing builders (`run_table_builders`, `session_tree_builder`, `session_table_builders.write_jsonl_zst`). No committed JSON fixtures.

#### Risks / open questions
- **[DECISION NEEDED] D1: Loki rejects old samples.** Loki defaults to `reject_old_samples: true` with a maximum age of one week. The implementer confirms this for 3.7.9; `loki-config.yaml` does not set it. Once ledgers carry their own `ts`, the first backfill of lines older than a week gets a 400 from Loki, and otlphttp drops the whole batch, which can include fresh lines. The archive still gets every line.
  - **Recommended default:** leave `loki-config.yaml` alone in 0004, write the limit up in the README, and run the live check on a fresh line.
  - Alternative: raise `reject_old_samples_max_age`. That is scope expansion, and the file is on the don't-touch list, so it would need re-validation.
- **[DECISION NEEDED] D2: client session overrides in the script.** The spec says only "client session directory".
  - **Recommended default:** honor the same overrides as `session_table/sources.py`, so "same rows" holds and tests stay synthetic. Otherwise the script reads only `$HOME/...` and tests set `HOME`.
- **[DECISION NEEDED] D3: Go `time.Parse` as an External reference.** The optional-fraction behavior comes from Go's `time.Parse` docs, not the filelog README.
  - **Recommended default:** add `https://pkg.go.dev/time#Parse` (BSD-3-Clause) to the spec's `## External references` when the plan is persisted.
- **openrsync filter behavior is not verified yet.** If the Mac's openrsync handles the anchored rules differently, the allowlist test goes red on the Mac. In that case use GNU rsync (`brew install rsync`, first on `PATH`) and record it in the README, rather than widening the patterns.
- **Characterization tests pass on first run.** The `project`-rule and readers-on-landing tests pin behavior that already works. The orchestrator's red audit should accept them as pins, not reject them.
- **Codex twins.** Without `--delete`, a Codex `.jsonl` and its later `.jsonl.zst` can both sit in the landing zone. Session-table already resolves the pair with a "duplicate" warning. The manual same-rows check may show extra warnings but the same rows.
- **Loki labels may miss the landing attributes.** `loki-config.yaml` lists `landing_host` and `landing_project` as resource attributes for index labels, but the `regex_parser` writes them to log attributes, so they arrive as structured metadata. This is pre-existing and out of scope; it is mentioned so the live-check query filters on metadata, not stream labels.
- **Ingestion checkpoints.** Anything already ingested from the local landing zone under the old `**/*.jsonl` glob stays in Loki. The checkpoint prevents re-ingest, so ledgers ingested earlier keep their ingest-time timestamps. The verify step counts any `_sessions` lines already in Loki and reports them; nothing is deleted.
- **`_sessions` name collision.** A project named `_sessions` would collide with the transcript directory. This is an edge case and is only noted.

#### Out of scope (won't touch)
- launchd scheduling, shipping to the real VM, and VM landing permissions (0006).
- Pruning the landing zone; the `--delete` flag.
- The tool-call ledger reader (unit 2).
- Tailing transcripts into Loki.
- `loki-config.yaml`, unless D1 chooses otherwise.
- `collector/mac-agent/otel-agent.yaml`, `compose.yaml` and `pyproject.toml`.
- Removing run-table support for the old flattened landing layout.
- The resource-attribute mismatch for the Loki landing labels.

## Phase handoff

### Test-first (2026-10-22)

- The test-first subagent wrote the plan's test files; the scope audit by content
  fingerprint found changes only under `tests/`.
- The orchestrator's own red run: 71 failed, 45 passed across the new and changed
  files. The 71 are 63 behavior-level `AssertionError`s (the old script ignores
  its arguments and finds no projects; the old filelog glob and `strptime`
  layout), 6 `ConflictingRunError`s (the 0001 stale-copy rule this spec reverses)
  and 2 `AttributeError`s on the planned `LedgerResult.review_keys`. The 45 passing
  include the characterization pins the plan names (`project` rule, the unchanged
  `regex_parser`, no `--delete`, the probe on port 22).
- Rejected-value search in `tests/` for `landing/**`, `%LZ`, `strptime`,
  `ship-ledgers`, `ship_ledgers`, `_sessions`, `mac-agent`, `"delegate"`:
  `test_stale_copy_missing_a_review_is_a_conflict` was the only pin of a replaced
  default and was replaced. Kept: `tests/collector_files.py` `HYGIENE_FILES` lists
  `collector/mac-agent/ship-ledgers.sh`; the path is unchanged.

### Implement and verify (2026-10-22)

- Direct implementation: `dedupe.py`, `join.py`, `build.py`, the rewritten
  `ship-ledgers.sh`, and the `filelog/landing` receiver. Full suite green.
- **rsync implementations.** The allowlist ships the same 7 files from a
  synthetic tree (with `pi-agent/auth.json`, `npm-cache/`, `segments/`, logs,
  `prompt.md` and client credentials planted) under macOS openrsync with
  `/bin/bash` 3.2 and under GNU rsync 3.2.7 with bash 5.2 in a Debian container.
  All 60 script tests pass on the Mac (openrsync).
- **Timestamps.** A scratch Collector on the pinned 0.163.1 image parsed
  `...:56Z`, `...:56.123Z` and `+00:00` lines with their own `ts` and skipped a
  `_sessions/` transcript.
- **Live landing zone.** `ship-ledgers.sh --dest` into the local stack's
  landing zone, `PROJECTS_ROOT` two levels above the projects: 3 projects, 12
  `result.json`, 3 `state.json`, 6 run transcripts, 736 client transcripts; no
  `pi-agent/`, `npm-cache/`, `segments/`, log, prompt, handoff or credential
  file. A second run exits 0.
- **Same rows (counts only).** `run-table`: 16 rows each way, identical apart
  from `ledger_path`, the same `project` values (1, 13, 2), 12 reviews joined, 5
  runs with tokens, 1 orphan; roots plus landing together still give 16 rows.
  `session-table`: 660 sessions each way (279 Claude Code, 186 Codex, 195 Pi), the
  same keys both ways, 6 delegate run IDs each way, no warnings.
- **Loki.** On the live stack with one shared stream, Loki kept 5 of 28 ledger
  lines, each at its own `ts`, and refused the rest as "entry too far behind";
  no transcript line reached Loki, and the archive holds all 28. That finding
  is D4. With per-project streams, a scratch Loki on the repo's
  `loki-config.yaml` kept all 8 lines younger than a week and refused the 20
  older ones as "timestamp too old" (D1). Old-timestamped lines are only
  queryable after the ingester flushes (`query_ingesters_within: 3h`).
- `otel-collector.yaml` re-validated on the arm64 and amd64 entries of the
  0.163.1 index after the final edit; the live Collector runs it. Its filelog
  checkpoints were kept, so lines already ingested are not re-sent.

### Review and fixes (2026-10-22)

- `/review` and `/review-adversarial` on the full change set. Fixed, test-first
  where code changed:
  - remote mode: an ssh failure after a good probe exited 255 with no
    `ship-ledgers:` line; it now exits 1 naming the VM;
  - symlinks: `rsync -a` copied a symlinked `result.json` or transcript as a
    link, putting the target's absolute path in the landing zone. The copy is
    now `-rpt`, so links are skipped and rsync names each one. Same result under
    openrsync and GNU rsync 3.2.7: exit 0, the same files, no target content;
  - a `--dest` value starting with `-` exits 2;
  - D5 and D6 above;
  - new pins: `merge_runs` without multisets still raises on review
    differences; symlinked project directories are not followed; paths with
    spaces ship.
- Focused re-review: all seven resolved. Then D9 and D10, test-first, and a
  `PROJECTS_ROOT` that is a file now says "is not a directory" (exit 2).
- Doc sync: the product spec's tailing-versus-batch question is marked resolved;
  this spec's External references gain the `regex_parser` source;
  `docs/agent-handoff.md` mentions `--dest`; 0006's draft records D7; the README
  documents D8.
- Not changed, as accepted limits: a file that vanishes during a copy (rsync
  exit 24) counts as a failed copy and clears on the next run; a ledger line with
  no parseable `ts` takes the ingest time; remote mode is only exercised with
  stubs until 0006; the archive holds landing records from before D4 with host and
  project as log attributes, and later ones as resource attributes.
