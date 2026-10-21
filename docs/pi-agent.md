# Pi agent

Pi is the third supported client for this scaffold. It reads the same canonical
`AGENTS.md`, discovers the same repository skills under `.agents/skills/`,
runs the same `.agentic/toolchain.sh`, and is designed to leave the same
durable specs, plans, tests, and review artifacts as Claude Code and Codex CLI;
authenticated end-to-end field evidence
is still tracked as a gap. Pi chooses the active model;
the project adapter does not pin a provider or model, so any Pi-supported
remote or local model can run the workflow if it reliably uses the required
tools.

## First start and trust

Start Pi from the repository root:

```bash
pi
```

Review the project trust prompt before approving it. Trust permits Pi to load
`.pi/settings.json`, the local extension, project prompts, project agents, and
the pinned `pi-subagents` package. It is a resource-loading decision, not a
sandbox. After bootstrap in an already-running session, restart Pi (preferred)
or run `/reload`, then use `/trust` if the project has not yet been approved.

Check startup diagnostics, run `/session`, and confirm these surfaces loaded:

- prompt templates such as `/spec`, `/plan`, and `/review-check`;
- repository skills such as `/skill:spec`;
- the `agentic-scaffold` project extension;
- `pi-subagents` and the project roles under `.pi/agents/`.

The prompt form is the normal interactive entry point. `/skill:<name>` remains
a portable explicit fallback. Prompt and skill text render from the same
workflow sources.

## Workflow mapping

| Phase | Pi entry point | Specialist |
| --- | --- | --- |
| Spec | `/spec <name>` | main session |
| Plan | `/plan` | fresh `planner` child |
| Test-first | `/test-first` | test-writing child |
| Gate | `/review-check` | main session through `.agentic/toolchain.sh` |
| Review | `/review` | fresh read-only `reviewer` child |

Core and full profiles add the same optional workflows documented in
`WORKFLOW.md`. Pi child roles inherit the selected session model unless the
operator configures a model override outside this scaffold. A provider switch
therefore does not change the repository workflow.

## Lifecycle guardrails

`.pi/extensions/agentic-hooks.ts` translates Pi lifecycle events to the
shared shell hooks:

- session start: branch and uncommitted-changes warnings;
- before shell execution: destructive-command and sensitive-file guard;
- after `edit` or `write`: format and spec-dashboard hooks;
- before final settlement: the default quality gate, with at most one
  automatic continuation;
- turn end: branch/model/context status.

Every child role loads a child-only entry point for the same adapter, so
sensitive-read guards do not disappear in read-only children and writer
children retain destructive-command and post-edit hooks. The child entry point
does not register session-start, status, or settlement handlers: parent-only
completion enforcement must not tell `test-first` to repair its intentional red
phase.

The extension reads strict/default/no-stop choices from
`.agentic/scaffold-state`, so bootstrap flags apply to all three clients. The
shell scripts and `.agentic/toolchain.sh` remain authoritative.

Pi does not expose a way for this adapter to add instructions to its built-in
compaction prompt without replacing compaction itself. Before a manual
`/compact`, write the documented phase handoff. This is a known parity gap,
not silently emulated behavior.

## Safety boundary

Pi tools and project extensions run with the operating-system permissions of
the Pi process. Tool-call guards, role allowlists, and project trust reduce
mistakes but are not an OS security boundary. For unattended work, run Pi in a
container, VM, or reviewed sandbox and provide only the credentials and
network access the task needs.

Read-only Pi roles omit `edit`, `write`, and `bash`; the parent resolves Git
diffs and validation evidence before launching them. The test-first role can
write and run tests, so the parent still performs the scaffold's before/after
workspace fingerprint audit.

## Switching clients

Persist the approved plan and latest `## Phase handoff` in the active spec,
then end the current session. Start Claude Code, Codex, or Pi from the same
worktree. Conversation history and UI state do not transfer; repository
artifacts do. Re-run the current phase's entry checks after switching.

## Troubleshooting

- Missing prompts or roles: approve project trust, then restart or `/reload`.
- Skills shown as subagents: verify `.pi/settings.json` excludes
  `../.agents` from legacy agent discovery.
- A customized `.pi/settings.json` was preserved on scaffold update: merge the
  new package/discovery settings manually, then reload.
- Gate continues once and remains red: inspect the reported failing step and
  run `/review-check`; the extension deliberately does not loop forever.
