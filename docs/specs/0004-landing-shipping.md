# 0004 — Landing-Zone Shipping and Ledger Ingestion

**Status:** draft
**Last updated:** 2026-10-21
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
  any depth up to `--max-depth` (default 3). <!-- assumption: depth search rather than a list file of projects -->
- **Layout.** Project evidence lands at
  `landing/<host>/<project>/.agentic/{delegate,telemetry}/...`, keeping `.agentic`
  so 0001's `project` rule (parent of `.agentic`) holds unchanged. Transcripts
  land at `landing/<host>/_sessions/{claude-code,pi,codex}/...`, including Codex
  `.jsonl.zst`.
- **Destinations.** `--dest DIR` copies into a local directory, such as 0003's
  local landing zone. The default (VM over SSH) is unchanged. A second run copies
  nothing new. An unreachable VM exits 0 and prints one line saying it skipped.
- **Tests.** Tests drive the script with `--dest` into `tmp_path` against
  synthetic project trees. No real paths are committed.
- **Ingestion.** `filelog/landing` includes `landing/*/*/.agentic/**/*.jsonl` and
  excludes `_sessions/`. Transcripts stay as files in the landing zone and are read in
  batch by `session-table`. <!-- assumption: settles tailing-vs-batch in favour of batch for transcripts -->
- **Timestamps.** The timestamp parser accepts both `...SSZ` and
  `...SS.fffZ`. A ledger line in Loki carries its own `ts`, not the ingest time,
  checked on 0003's local stack.
- **Validation.** The edited `otel-collector.yaml` passes `otelcol-contrib
  validate` on the pinned image, recorded in the README.
- **Stale landing-zone copy.** When two inputs carry the same run ID and the only
  differences are review columns, the row with the later `review_ts` (or the
  only review) wins. A warning names both ledger paths. Any other difference
  still exits 1, as in 0001. <!-- assumption: a stale copy differs only by missing later reviews; anything else is a real conflict -->
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
