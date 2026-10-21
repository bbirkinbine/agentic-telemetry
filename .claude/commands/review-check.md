---
description: Run the full local quality gate (lint, format, typecheck, tests) through .agentic/toolchain.sh before invoking /review. Refuses to declare pass on any failure.
---

This is the pre-`/review` quality gate. It does not declare a feature
"done" — only that the local checks pass. The human still runs `/review`
(and `/security` / `/performance` if relevant) before commit.

The tools are named in one place, `.agentic/toolchain.sh`; `AGENTS.md` →
"Stack" says which tools stand behind each step. Run the steps in order and
report results:

1. `.agentic/toolchain.sh lint` — lint
2. `.agentic/toolchain.sh format` — apply formatting in place (the PostToolUse
   hook formats on every edit, so this is usually a no-op; we run it anyway
   so the gate is reproducible from a fresh clone)
3. `.agentic/toolchain.sh typecheck` — type check
4. `.agentic/toolchain.sh test` — tests (fail-fast, short output)

If any step fails:

- Do NOT declare the gate passed.
- Show the failing output verbatim (the failing test names, the type
  errors, the lint findings).
- Inside an approved plan, fix in-scope failures and re-run the gate
  (`AGENTS.md` → **Approval matrix**).
- Stop with a checkpoint report (`AGENTS.md` → **Shared workflow
  protocols** → **Checkpoint report**) when the fix would change the
  approved approach, touch a file or behavior the plan does not name, or
  needs a human decision, or when no plan is approved.

If all steps pass:

- Summarize: which steps ran, how long it took, test count.
- Suggest the next step explicitly:
  - `/review` — always, for an independent code review.
  - `/security` — if the project opted into `security-reviewer` AND the
    diff touches any of the security triggers listed in this project's
    `README.md` ("Opt-in subagents" → `security-reviewer.md`).
  - `/performance` — if the project opted into `performance-reviewer`
    AND the diff touches any of the performance triggers listed in
    `README.md` ("Opt-in subagents" → `performance-reviewer.md`).
- Do NOT commit. The human commits.

First syntax-check the runner with `bash -n .agentic/toolchain.sh`, then
check `.agentic/toolchain.sh ready`. Exit 0 means run the steps above.
Exit 3 means intentionally unconfigured: say that no gate ran and why,
and use `/review` to verify the evidence. Any other exit status, a missing
runner, or a syntax error is a broken gate: report failure and repair it.
Do not substitute commands of your own for an undefined step. In the custom
stack, set `TC_CONFIGURED=1` once the steps and directories are defined;
keep it active thereafter so configuration regressions fail validation.
