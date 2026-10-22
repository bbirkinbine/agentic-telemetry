# agentic-telemetry

Collects and analyzes telemetry from coding-agent sessions run with
[agentic-scaffold](https://github.com/bbirkinbine/agentic-scaffold): the homelab
collector VM, readers over tool-call ledgers and delegate runs, per-model failure
matrices, and a review app for labeling runs. Private: it names homelab hosts.

> ## Status
>
> Published as a personal lab, not an actively
> maintained product. Issues and PRs are welcome but won't get fast
> turnaround. Pin a specific commit if you depend on a snapshot.

## Requirements

- Python 3.12 and `uv`
- Docker, for validating and running the collector stack; the `gh` CLI for PRs

## Quick start

```bash
git clone git@github.com:bbirkinbine/agentic-telemetry.git
cd agentic-telemetry
.agentic/toolchain.sh install   # install dependencies
mkdir -p data
uv run agentic-telemetry run-table --out data/run_table.parquet ~/src/my-project
# one row per delegate run; see AGENTS.md → How to run things
uv run agentic-telemetry run-table --metrics-url http://127.0.0.1:8428 \
  --out data/run_table.parquet ~/src/my-project
# adds server_* columns from VictoriaMetrics (read-only): the model each run ran on,
# tokens and throughput from llama.cpp counters, a cold-start flag, and a status
# (joined, overlap, uncovered, ...); see docs/specs/0005-server-metrics-join.md
uv run agentic-telemetry session-table --out data/session_table.parquet
# one row per Pi, Claude Code or Codex session, from each client's default session directory
```

## Repository layout

- `collector/`: the telemetry VM design, compose and validated Collector, Loki and
  Tempo configs; `collector/mac-agent/` is the laptop-side buffering agent and the
  ledger ship script; `collector/clients/` holds client config snippets
- `analysis/`: the analysis plan: telemetry review vs public benchmarks vs private
  evals, the agent/server join, and the pattern catalog
- `src/agentic_telemetry/`: the Python package (readers, run table, session table,
  matrices, review app)
- `tests/`: the suite
- `docs/specs/`: specs; `docs/adr/`: architecture decisions

## Development

```bash
.agentic/toolchain.sh install   # dependencies
.agentic/toolchain.sh test      # tests
.agentic/toolchain.sh lint      # lint
.agentic/toolchain.sh typecheck # type-check
.agentic/toolchain.sh gate      # all of the above plus the format check
uv run pre-commit install    # commit guard
```

See [`AGENTS.md`](AGENTS.md) for the per-project contract every client
follows (Claude Code imports it through [`CLAUDE.md`](CLAUDE.md)) and
[`WORKFLOW.md`](WORKFLOW.md) for the spec-driven loop.

## Acknowledgements

This project was developed with the assistance of AI tools.

## License

MIT — see [`LICENSE`](LICENSE).
