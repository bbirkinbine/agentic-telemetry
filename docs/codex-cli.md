# Codex CLI

This scaffold installs Claude Code, Codex, and Pi support together. All three
clients read the same project contract and produce the same durable artifacts: specs,
ADRs, phase handoffs, tests, review findings, and verification output. You can
stop after a phase in one client and resume from the repository in another.
Conversation history and client UI state do not transfer.

Verification baseline: Codex CLI 0.146.0 and the official Codex documentation,
checked 2026-07-30.

## What bootstrap installs

| Shared behavior | Claude Code | Codex |
| --- | --- | --- |
| Project contract | `CLAUDE.md` imports `AGENTS.md` | `AGENTS.md` |
| Phase workflows | `.claude/commands/<name>.md` → `/<name>` | `.agents/skills/<name>/SKILL.md` → `$<name>` or `/skills` |
| Specialist roles | `.claude/agents/*.md` | `.codex/agents/*.toml` |
| Reusable Python skills | `.claude/skills/` | `.agents/skills/` |
| Lifecycle wiring | `.claude/settings.json` | `.codex/hooks.json` |
| Shared hook logic | `.agentic/hooks/` | `.agentic/hooks/` |
| Secret-read policy | Claude permission deny list | Codex permission profile |
| Command policy | behavioral contract + hooks | `.codex/rules/safety.rules` + hooks |

`AGENTS.md` is the complete canonical contract. `CLAUDE.md` contains only
`@AGENTS.md`, using Claude Code's supported import syntax, so neither client
has a separate copy of project policy to maintain. Claude Code may ask for
one-time approval when it first encounters a checked-in import.

Codex discovers repository skills under `.agents/skills/`, project agents
under `.codex/agents/`, and project configuration, hooks, and rules from the
trusted `.codex/` layer. See the official documentation for
[AGENTS.md discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[skills](https://learn.chatgpt.com/docs/build-skills),
[custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[hooks](https://learn.chatgpt.com/docs/hooks), and
[rules](https://learn.chatgpt.com/docs/agent-configuration/rules).

## Start a fresh project

Bootstrap normally; multi-client support is the default:

```bash
cd your-project
bash path/to/agentic-scaffold/<stack>/bootstrap.sh --core   # python, typescript, or custom
.agentic/toolchain.sh install
pre-commit install    # the bootstrap prints the stack's install line
```

Start Codex from the repository root:

```bash
codex
```

Codex builds its `AGENTS.md` chain once, from the repository root down to
the launch directory. If an area has a nested `AGENTS.md`, launch with
`codex --cd path/to/area` for that session. Starting at the root does not
cause nested instructions to load later when files are read; keep every
critical repository-wide rule in the root contract.

Review the normal project trust prompt. Project `.codex/` configuration,
agents, hooks, and rules are ignored until the project layer is trusted. In
the CLI, run `/hooks` to review and trust each current hook definition; Codex
records trust against the hook hash, so changed hooks require review again.
Do not use `--dangerously-bypass-hook-trust` as the normal setup path.

Run `/skills` to inspect the installed workflows, or invoke one explicitly:

```text
$spec add export command
$plan docs/specs/0001-add-export-command.md
$test-first docs/specs/0001-add-export-command.md
$review-check
$review
```

The checkpoints are the same as Claude Code: `spec` stops for spec ownership,
`plan` stops before test or implementation writes, `test-first` stops after a
cause-specific red test, and `review-check` stops before commit.

## Continue a Claude Code feature in Codex

Before switching clients, append the documented `## Phase handoff` block to
the active spec. In the new Codex session:

1. Confirm the current branch with `git branch --show-current`.
2. Read the active spec and its latest `## Phase handoff`.
3. Inspect `git status --short` and the current diff.
4. Invoke the next workflow skill named in the handoff.

No regeneration is required. Codex reads the same `AGENTS.md`, spec, tests,
and diff that Claude Code left behind. Switching in the other direction uses
the same procedure; Claude Code imports `AGENTS.md` through `CLAUDE.md`.

For a project created before the current client adapters, run:

```bash
bash path/to/agentic-scaffold/<stack>/bootstrap.sh --update --core
```

The update installs the Codex surface. If `AGENTS.md` is still the scaffold's
old unmodified pointer, bootstrap recognizes it and moves the customized
`CLAUDE.md` policy into canonical `AGENTS.md`, then replaces the duplicate
with the `@AGENTS.md` import. Existing byte-identical and Claude-only layouts
migrate the same way. Legacy `.claude/rules/` copies are removed after stock
rules are replaced by the canonical standing-rule block and customized rules
are preserved in `AGENTS.md`. A project-owned pair with different policy is
left untouched and gets a warning; reconcile it manually rather than
discarding either side. Later updates refresh only the marked standing-rule
block in `AGENTS.md`, preserve project policy outside it, reuse the recorded
profile and hook choices, and avoid overwriting customized client
configuration.

For Pi startup, trust/reload behavior, prompts, roles, and model portability,
see [`pi-agent.md`](pi-agent.md). Pi remains a separate adapter over the same
repository contract; this page documents Codex-specific behavior.

## Non-interactive Codex

Codex skills and agents also work under `codex exec`. Keep global flags before
the `exec` subcommand:

```bash
codex --ask-for-approval never --sandbox read-only exec \
  '$plan docs/specs/0001-add-export-command.md'

codex --ask-for-approval never --sandbox workspace-write exec \
  '$review-check Run the complete local gate and report every command.'
```

Use read-only mode for planning and review. `test-first` and implementation
need workspace write access. `review-check` uses workspace write because its
format step may update files.

## Opt-in roles

Security and performance reviewers remain per-project opt-ins. Copy all
three adapters so every supported client can invoke the role:

```bash
cp path/to/agentic-scaffold/python/.claude/agents/optional/security-reviewer.md \
   .claude/agents/security-reviewer.md
cp path/to/agentic-scaffold/python/.codex/agents/optional/security-reviewer.toml \
   .codex/agents/security-reviewer.toml
cp path/to/agentic-scaffold/python/.pi/agents/optional/security-reviewer.md \
   .pi/agents/security-reviewer.md
```

Repeat with `performance-reviewer` when its trigger applies, then record the
enabled role in `AGENTS.md`; every client reads that canonical
contract (Claude through its import).

## Guardrails and client differences

- `.codex/config.toml` selects a workspace permission profile that denies
  `.env*`, `*.pem`, and `*.key` reads. A live `--sandbox` or permission
  override replaces that project default, so the behavioral secret rule,
  `.gitignore`, pre-commit, and CI remain necessary.
- Codex project hooks require trust. Untrusted hooks are skipped rather than
  silently treated as active.
- Both clients show branch, model, and remaining context in their configured
  status line. Their rendering differs, but the workflow signal is the same.
- Claude slash commands and Codex skills have different invocation syntax.
  They are rendered from the same source and preserve the same input, output,
  write boundary, and stop point.
- Hook and command rules are guardrails, not a complete sandbox. Pre-commit
  and CI remain the client-independent enforcement boundary.
- A subagent's configured sandbox is its default. Parent-session live
  permission overrides can take precedence, so launch review workflows from
  a read-only parent when the boundary itself is under test.

## Validate the adapters

From the scaffold repository:

```bash
bash scripts/render-client-surfaces.sh
bash scripts/validate-codex-adapters.sh
bash scripts/smoke-test.sh minimal
bash scripts/smoke-test.sh core
bash scripts/smoke-test.sh full
bash scripts/smoke-test-typescript.sh
```

The adapter validator checks the canonical contract/import relationship and
byte budget, complete adapter inventories, role prompt fidelity, TOML/JSON
parsing, normalized hook matrices and payload fixtures, and Codex
command-rule decisions. The Codex binary is required so policy coverage
cannot silently disappear. Smoke tests assert installation and update
transitions, then run the fresh project's gate through `.agentic/toolchain.sh`.
