# landing/

Drop zone for file-based evidence from each Mac, copied by
`mac-agent/ship-ledgers.sh` (spec 0004). Files here are evidence: never edited by
hand, never committed, and never deleted by the script.

Layout:

- `<host>/<project>/.agentic/delegate/ledger.jsonl`: delegate runner ledger
- `<host>/<project>/.agentic/delegate/results/<run>/{result.json,state.json,session/*.jsonl}`:
  what `run-table` reads for each run, and nothing else (no `pi-agent/`,
  `npm-cache/`, `segments/`, logs or prompts, and never a symlink)
- `<host>/<project>/.agentic/telemetry/*.jsonl`: tool-call ledger (scaffold Phase 1)
- `<host>/_sessions/{claude-code,pi,codex}/...`: client transcripts, Codex
  `.jsonl.zst` included

`.agentic` stays in the path so `run-table` names each project after the parent of
`.agentic`. Point `run-table` and `session-table` at `landing/<host>/` to read it.

The Collector's filelog receiver tails only the ledgers
(`*/*/.agentic/delegate/ledger.jsonl` and `*/*/.agentic/telemetry/*.jsonl`). It
parses one JSON object per line, timestamps it with the line's own `ts`, and
records `landing_host` and `landing_project` from the path. A checkpoint under
`collector-state/` makes sure each line is ingested once, even if the file is
re-synced. Transcripts are never tailed: they carry captured tool content and are
read in batch.
