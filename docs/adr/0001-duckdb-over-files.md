# 0001 — DuckDB as the Parquet Writer and Query Engine Over File-Based Evidence

**Status:** accepted
**Last updated:** 2026-10-21

> The collector `inbox/` was renamed `landing/` (landing zone) on 2026-10-21.
> This record keeps the name it was written with.

## Context

The product spec makes files the record of truth: ledgers, delegate result
directories, session transcripts, and the collector inbox and archive. The Mac has
to keep working offline, so nothing may depend on a running database. Every
analysis output (the run table in spec 0001, the transition failure matrix and
session summaries in unit 2, the server-metrics join in unit 3) is a derived file
that can be rebuilt from that evidence.

Spec 0001 needs a way to write its run table as Parquet, the format
`analysis/README.md` names. The units after it need more than a writer:

- Unit 2 groups and pivots per fingerprint, and scans session transcripts. One run's
  Pi event log alone is about 100k lines.
- Unit 3 joins each run's time window to server metric samples.
- The owner queries the tables ad hoc.

Against adding anything: 0001 alone is 15 rows, and the stdlib could parse them
and write JSONL with no dependency at all. DuckDB is a native wheel (tens of MB)
and a new runtime dependency in a repo that has none. Its 2.0.0 major was released
on 2026-10-21, the day this decision was made.

## Decision

We will use DuckDB as the only engine for writing and querying analysis tables, and
keep the data in files:

- Parsing and joining evidence stays in plain Python functions (data in, data out),
  testable without any engine.
- DuckDB writes the resulting tables to Parquet under `data/` and runs analytic
  queries over Parquet and JSONL in place.
- No persistent `.duckdb` database file is part of the design; any one that appears
  is a disposable cache.
- The dependency is pinned to the 1.x line (`duckdb>=1.5.6,<2`) until 2.x has been
  out long enough to evaluate.

## Consequences

- Easier:
  - You can run `duckdb -c "select ... from 'data/run_table.parquet'"` the day the
    table lands.
  - Unit 2's group-by/pivot work and unit 3's time-window join (`ASOF JOIN`) are
    built in rather than hand-written.
  - DuckDB reads JSONL evidence in place, without a load step.
- Harder:
  - The repo gains one native runtime dependency.
  - The test environment needs the wheel.
  - A later move to 2.x is a deliberate upgrade with its own check.
- Parquet output is binary, so a diff of two runs' tables needs a query, not
  `git diff`. This is moot because `data/` is never committed.
- Follow-on: `pyproject.toml` gains `duckdb` in 0001's implementation, after the
  dependency-hygiene check.

## Alternatives considered

- **Stdlib only, JSONL output; defer DuckDB to unit 2.** Zero dependencies for
  0001. Not chosen: the product spec names Parquet. The owner wants to query the
  table immediately, and unit 2 would add DuckDB anyway.
- **SQLite (`sqlite3`, stdlib).** No dependency. Not chosen:
  - It stores data row by row, and the workload is scans and aggregations.
  - It cannot read Parquet or JSONL in place, so it needs a second copy of the
    data in a `.db` file. That cuts against files as the record of truth.
  - It has no Parquet writer, and its nested `attempts`/`model` fields would live
    as JSON text.
- **pyarrow as the Parquet writer.** Writes Parquet, but gives no query engine. Unit
  2 would still need one.
