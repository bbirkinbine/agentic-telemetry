# Serena MCP — setup & verify

> **Purpose.** Operational runbook for turning on (or confirming) the
> optional `serena` code-navigation MCP for this repo. `AGENTS.md` →
> "Code navigation" owns the *decision* (when serena earns its keep, and
> the skip-by-default rule); this file owns the *mechanics* — install,
> verify, update, tear down. If you haven't decided whether to enable it,
> read that section first: serena is wasted effort on a small
> single-language repo.
>
> serena runs as a **local child process beside the agent and the code**,
> not a shared service — it needs filesystem access to the repo and boots
> a language server against it.

---

## Prerequisites

- **`uv` / `uvx` on `PATH`.** `uvx` fetches and runs serena on demand;
  nothing is installed globally. Check:
  ```bash
  uvx --version
  ```
  Missing → `brew install uv` (macOS), then reopen the session so the
  client inherits the updated `PATH`.
- **A git repo.** serena anchors to the project root and writes a
  `.serena/` dir there (gitignored by this template).
- **A language server for the repo's language.** serena manages this for
  most languages out of the box (Python included). A few expect the
  toolchain already present (e.g. `gopls` for Go, `rust-analyzer` for
  Rust). If startup complains about the language server, check serena's
  README for the per-language requirement.

---

## First-time setup

Register serena as a per-project MCP server, **from the repo root**:

Claude Code:

```bash
claude mcp add serena -- uvx --from git+https://github.com/oraios/serena \
  serena start-mcp-server --context claude-code --project "$(pwd)"
```

Codex:

```bash
codex mcp add serena -- uvx --from git+https://github.com/oraios/serena \
  serena start-mcp-server --context codex --project-from-cwd
```

- `--project "$(pwd)"` pins serena to this repo — run from the project
  root so it resolves correctly.
- `--context claude-code` selects the toolset tuned for Claude Code. It
  is correct for **both** the `claude` CLI and the Claude Code VS Code
  extension: they share one MCP config on this machine.
- `--context codex --project-from-cwd` selects Serena's Codex toolset and
  activates the repository where Codex starts. `codex mcp add` writes the
  registration to the user's Codex config; do not also add a duplicate
  project entry.

**Scope.** `claude mcp add` defaults to *local* scope — this project
only, stored in your user config, not committed:

| Scope | Flag | Where it lives | Use when |
|---|---|---|---|
| local (default) | — | user config, keyed to this repo path | solo, this machine |
| project | `--scope project` | `.mcp.json` in the repo (committable) | you want the registration tracked/shared |
| user | `--scope user` | user config, all repos | rare — serena is per-large-repo, not global |

`.serena/` (serena's cache + project config) stays gitignored regardless
of scope; only the `.mcp.json` registration becomes shareable under
`--scope project`.

---

## Verify (new or existing setup)

1. **Is it registered?**
   ```bash
   claude mcp list        # serena should appear; recent versions show a connection check (✓/✗)
   claude mcp get serena  # full config for the one server
   codex mcp list
   codex mcp get serena
   ```
2. **Is it live in a session?** Open (or reload) the client in this repo
   and inspect its MCP view. Serena should show as connected, with its tools
   visible — `find_symbol`, `find_referencing_symbols`,
   `get_symbols_overview`, and the symbol-edit tools. The VS Code
   extension needs a window/session reload to pick up a newly added
   server (MCP config is read at session start).
3. **Smoke-test a query.** Ask the agent a navigation question that
   forces a symbol lookup — e.g. *"use serena to find who calls
   `<a known function>`."* A precise caller list (not a grep-style text
   dump) means the language server resolved symbols.
4. **First-run cost.** The first query triggers indexing — slower, and a
   `.serena/` dir appears in the repo. Later queries are fast. By default
   serena also serves a small web dashboard on localhost (the URL is
   printed in its startup logs) where you can watch indexing progress and
   tool calls.

If `claude mcp list` shows serena but `/mcp` reports it failed to
connect, see **Troubleshooting**.

---

## Update / pin the version

The setup command runs serena from the **default branch** of its GitHub
repo — `uvx` resolves a version at fetch time, so you track upstream
`main` (a moving target). To pin for reproducibility, append
`@<tag-or-sha>` to the source:

```bash
claude mcp remove serena
claude mcp add serena -- uvx --from git+https://github.com/oraios/serena@<tag> \
  serena start-mcp-server --context claude-code --project "$(pwd)"

codex mcp remove serena
codex mcp add serena -- uvx --from git+https://github.com/oraios/serena@<tag> \
  serena start-mcp-server --context codex --project-from-cwd
```

`uvx` caches the resolved build; to force a re-pull after upstream
changes, `uv cache clean` (or pin a newer ref).

---

## Teardown / disable

```bash
claude mcp remove serena   # unregister (config only)
codex mcp remove serena    # unregister the Codex copy
rm -rf .serena/            # drop the local cache + project config
```

`remove` leaves `.serena/` behind — delete it too for a clean slate (it
regenerates on the next enable). Nothing serena writes is tracked by git,
so there is no commit to revert.

---

## Troubleshooting

- **`serena` not in `claude mcp list`** — `add` ran in a different
  directory or scope than you're checking. Re-run from the repo root;
  confirm `--scope`.
- **Listed but won't connect (`/mcp` shows failed)** — usually `uvx`
  isn't on the `PATH` the client launched with, or the first `uvx` fetch
  is still downloading. Confirm `uvx --version` in the same shell; retry
  once the initial pull finishes.
- **`uvx: command not found`** — `uv` isn't on `PATH`. Install it, then
  reopen the session so the client inherits the updated `PATH`.
- **First query hangs or is very slow** — initial indexing on a large
  repo. Let it complete once (watch the dashboard URL from the startup
  logs); it is fast afterward.
- **Stale or wrong symbol results after a big refactor** — drop the cache
  and re-index: `rm -rf .serena/`, then re-run a query.
- **Language server fails to start** — the repo's language needs its
  toolchain present (e.g. `gopls`, `rust-analyzer`). Install it and
  retry; see serena's README for the per-language requirement.
- **VS Code extension doesn't see a newly added server** — reload the
  window / restart the Claude Code session. MCP servers are read at
  session start, not hot-reloaded.
