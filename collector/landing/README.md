# landing/

Drop zone for sources that cannot speak OTLP. Layout: `landing/<host>/<project>/<file>.jsonl`.
The Collector's filelog receiver tails every `*.jsonl` under here, parses one JSON object
per line, and records `landing_host` and `landing_project` from the path. A checkpoint under
`collector-state/` guarantees each line is ingested once even if the file is re-synced.

Expected contents from each Mac (rsynced by `scripts/ship-ledgers.sh`, to be written):

- `.agentic/telemetry/<session>.jsonl`  tool-call ledger (scaffold Phase 1)
- `.agentic/delegate/ledger.jsonl` and `results/<run>/result.json`  delegate runner
- Pi session files for runs you want kept (`~/.pi/agent/sessions/...`)
- Claude Code transcripts for eval runs (`~/.claude/projects/<project>/<session>.jsonl`)
