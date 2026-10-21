---
description: Invoke the security-reviewer subagent on the complete current semantic change set. Requires the opt-in subagent to be installed in this project.
argument-hint: "[spec-path] [<base>..<head> for an explicit historical range]"
---

Invoke the `security-reviewer` subagent.

Preflight: confirm the active client's `security-reviewer` adapter exists
(`.claude/agents/security-reviewer.md` for Claude Code,
`.codex/agents/security-reviewer.toml` for Codex, or
`.pi/agents/security-reviewer.md` for Pi). If not, this project
hasn't opted into security review. Tell the user:

```
Security-reviewer is not installed in this project. To enable:

  cp path/to/agentic-scaffold/python/.claude/agents/optional/security-reviewer.md \
     .claude/agents/security-reviewer.md
  cp path/to/agentic-scaffold/python/.codex/agents/optional/security-reviewer.toml \
     .codex/agents/security-reviewer.toml
  cp path/to/agentic-scaffold/python/.pi/agents/optional/security-reviewer.md \
     .pi/agents/security-reviewer.md

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

The security-reviewer produces a Ghostwriter-style finding list (severity,
category, location, evidence, why-it-matters, suggested fix, verification
command per finding). It recommends verification commands (`pip-audit`,
`bandit`, `semgrep`, `gitleaks`) — it does NOT run them. Surface the findings
verbatim and surface the recommended commands clearly so the human can choose
to run them. After a security fix, synchronize affected docs and run the
complete gate, then invoke a fresh security reviewer on the updated semantic
change set to verify the resolution; a mechanical green gate does not close a
security finding.
