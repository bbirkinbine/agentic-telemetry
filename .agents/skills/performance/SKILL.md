---
name: performance
description: Invoke the performance-reviewer subagent on the complete current semantic change set. Requires the opt-in subagent to be installed in this project.
---

In these shared instructions, `$ARGUMENTS` means the arguments supplied with this skill invocation. Any `/<name>` cross-reference names another workflow: invoke `$<name>` in Codex or the matching `/<name>` prompt (or `/skill:<name>`) in Pi.


Invoke the `performance-reviewer` subagent.

Preflight: confirm the active client's `performance-reviewer` adapter exists
(`.claude/agents/performance-reviewer.md` for Claude Code,
`.codex/agents/performance-reviewer.toml` for Codex, or
`.pi/agents/performance-reviewer.md` for Pi). If not, this project
hasn't opted into performance review. Tell the user:

```
Performance-reviewer is not installed in this project. To enable:

  cp path/to/agentic-scaffold/python/.claude/agents/optional/performance-reviewer.md \
     .claude/agents/performance-reviewer.md
  cp path/to/agentic-scaffold/python/.codex/agents/optional/performance-reviewer.toml \
     .codex/agents/performance-reviewer.toml
  cp path/to/agentic-scaffold/python/.pi/agents/optional/performance-reviewer.md \
     .pi/agents/performance-reviewer.md

Then add a one-line mention under "Subagents" in AGENTS.md. CLAUDE.md
continues to import that contract with @AGENTS.md.
```

And stop.

If installed, proceed.

Spec and change-set selection:

- Resolve the active spec using `AGENTS.md` → **Shared workflow protocols** →
  **Active-spec resolution**.
- By default, build the complete pre-commit semantic change set from
  `AGENTS.md` → **Shared workflow protocols** → **Semantic change set**. Pass
  the fresh read-only reviewer the spec path, base ref, merge-base, status,
  tracked working-tree diff, and untracked-path manifest; it must inspect the
  untracked files too.
- If `$ARGUMENTS` explicitly contains `<base>..<head>`, use that committed
  historical range instead and state that current working-tree changes were
  not reviewed.

The performance-reviewer produces a Ghostwriter-style finding list (severity,
category, location, evidence, impact, suggested fix, verification command per
finding). It recommends profiling / measurement commands for the stack (for
Python: `py-spy`, `scalene`, `pytest-benchmark`; for any stack: `EXPLAIN
ANALYZE`) — it does NOT run them. Surface the
findings verbatim and surface the recommended commands clearly so the human can
choose to run them. After a performance fix, synchronize affected docs and run
the complete gate, then invoke a fresh performance reviewer on the updated
semantic change set to verify the resolution; a mechanical green gate does not
close a performance finding.
