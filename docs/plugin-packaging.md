# Plugin packaging — client-specific distribution paths

**Status: documented, not yet adopted.** `bootstrap.sh` (copy +
`--update` sync) remains the canonical distribution mechanism for this
scaffolding. Claude Code, Codex, and Pi have different package/configuration
flows, so a future packaging change must publish separate client adapters
from the shared `workflow/` sources. A plugin is not the
portability layer; the checked-in repository files are.

## What remains portable without a plugin

The repository already carries every client's project surfaces:

- shared policy and role sources under `workflow/`;
- canonical `AGENTS.md` plus `CLAUDE.md`'s `@AGENTS.md` import;
- Claude adapters under `.claude/`;
- Codex skills under `.agents/skills/` and project configuration under
  `.codex/`;
- Pi prompts, roles, extension, and settings under `.pi/`, while Pi also
  discovers the portable `.agents/skills/` catalog;
- client-neutral hooks under `.agentic/hooks/`.

That layout works in a fresh clone and under `codex exec` without installing
a global plugin. It also keeps CI, pre-commit, docs, specs, and project
configuration versioned with the code they govern.

## Pi packaging path

Pi can load npm packages, but this scaffold deliberately checks its prompts,
roles, settings, and local lifecycle adapter into each project. The only
package dependency is an exact `pi-subagents` pin loaded extension-only;
bundled roles, skills, and prompts are disabled so they cannot create a second
workflow authority. Keep provider/model selection out of packaging.

## Claude Code packaging path

A Claude Code plugin bundles skills, commands, agents, hooks, and MCP
config into one versioned, installable unit, distributed through a
git-based marketplace:

```
/plugin marketplace add bbirkinbine/agentic-scaffold
/plugin install python-agentic-workflow
```

That is the sanctioned equivalent of what `bootstrap.sh --update` does
by copying MANAGED files — with version pinning, update notification,
and per-project enable/disable for free. Plugin-provided skills are
invocable as `/plugin-name:skill-name`, so collisions with project-local
commands resolve cleanly.

## What maps and what doesn't

| Scaffolding piece | Plugin home |
| --- | --- |
| `.claude/agents/*` (incl. optional) | plugin `agents/` — optional ones become install-time choices |
| `.claude/commands/*` | plugin `commands/` (or skills) |
| `.claude/skills/*` | plugin `skills/` |
| `.agentic/hooks/*` + `.claude/settings.json` wiring | plugin `hooks/` |
| `WORKFLOW.md`, `docs/*`, `.github/*`, `pyproject.toml`, `.pre-commit-config.yaml` | **stay with bootstrap.sh** — plugins ship agent surface, not repo files |

The split matters: a plugin cannot deliver the CI workflow, pre-commit
config, or docs tree. The likely end state is hybrid — a client plugin
for the interactive surface and a slimmer `bootstrap.sh` for repo files.

## The manifests (verified against current docs)

`.claude-plugin/plugin.json` — required fields only:

```json
{
  "name": "python-agentic-workflow",
  "description": "Spec -> Plan -> Test-first -> Implement -> Verify scaffolding for Python projects",
  "version": "1.0.0"
}
```

`.claude-plugin/marketplace.json` at the repo root makes this repo
itself the marketplace:

```json
{
  "version": 1,
  "metadata": { "description": "agentic-scaffold plugins" },
  "plugins": [
    {
      "name": "python-agentic-workflow",
      "description": "Python agentic-workflow scaffolding",
      "source": "./python"
    }
  ]
}
```

## Codex packaging path

Codex plugins can bundle skills, hooks, MCP configuration, apps, and related
project capabilities. If this scaffold is packaged for Codex, generate that
bundle from the same `workflow/` and `.agentic/` sources used by the checked-in
`.agents/` and `.codex/` adapters. Do not copy the Claude manifest or assume
Claude command names become Codex slash commands.

Codex plugin packaging is optional for distribution, not required for
capability parity. Until both packages have automated adapter-fidelity tests,
the repository-local Codex surface remains the supported path.

## When to actually do this

Trigger conditions — any one is enough:

- More than ~3 active projects consuming the scaffolding, making
  `--update` walks tedious.
- A second consumer besides this machine (the plugin gives them a
  version they can pin).
- The `.claude/` or `.codex/` surface starts changing faster than the
  repo-file surface.

Until then, the copy model's one real advantage holds: every consuming
project carries the full scaffolding in its own tree, readable and
hackable with no indirection.
