---
description: Invoke the test-first subagent to write failing tests from a spec. Never implements.
argument-hint: "[path to spec, or blank to resolve the active spec]"
---

Invoke the `test-first` subagent.

Spec selection:

- Resolve the active spec using `AGENTS.md` → **Shared workflow protocols** →
  **Active-spec resolution**: explicit spec path, then branch-number match,
  then the unique `shipping` spec. Ambiguity stops; never guess from the
  highest spec number.
- Require `**Status:** shipping`. For work that ran `/plan`, also require
  the human-approved `## Approved implementation plan`. If either approval
  record is missing, stop before spawning the subagent. For a Small
  task where plan was explicitly skipped, the orchestrator still marks the
  approved spec `shipping` before this phase.

Before invoking the subagent, record a pre-phase workspace snapshot:

1. Capture `git status --short`, `git diff --binary`, and
   `git diff --cached --binary`.
2. Record every untracked path from
   `git ls-files --others --exclude-standard`.
3. Record a content fingerprint for every tracked and untracked workspace file
   (for example, path + file type + the output of
   `git hash-object --no-filters -- path/to/file`).
   Status alone cannot detect an edit to a file that was already dirty before
   this phase.

Then invoke the subagent. It writes failing tests only. It does NOT implement.
It returns: the test file paths it wrote, the focused failing-test output it
captured, and a one-line summary per test describing the behavior pinned down.

When it returns, take the same snapshot again and compare it to the pre-phase
snapshot. Changes are allowed only under the test directories, in the shared
test-fixture files the stack's code-conventions rule names (`AGENTS.md` →
"Test-first"), or in an additional test-fixture/snapshot path explicitly named
in the approved plan. Phase-handoff metadata in the active spec may be written
by the orchestrator after this audit; the test-first subagent does not need to
edit it.

Any new or changed source file, application/package module, runtime
configuration, migration, or other implementation path is a **hard phase
failure**. Surface the exact unexpected paths and diff, preserve the user's
pre-existing changes, and stop before implementation with a checkpoint report
(`AGENTS.md` → **Shared workflow protocols** → **Checkpoint report**). Do not accept "the edit
was only a stub" and do not silently revert files.

Finally, the orchestrator independently re-runs the focused new tests with
`.agentic/toolchain.sh test <target>`. Surface the output and confirm they
fail for the expected missing behavior as the stack rule describes it (a
missing function, attribute, or export; a not-implemented stub; a
behavior-level assertion — not a typo, broken fixture, collection error, or
unrelated existing failure).
Do not rely only on the subagent's reported output.

**Rejected-value search.** When the spec, a review resolution, or a
remediation replaces constants, behavior, names, or provenance, search
production code, tests, fixtures, snapshots, and docs for every rejected
value and name. Each match is updated, left for a planned implementation or
docs step, or deliberately kept with a reason. A stale assertion that still
pins the old value passes against the old code, so the red run cannot expose
it, and no implementation can satisfy both it and the new tests. Record the
terms searched and any kept matches in the spec next to the test-first result
(the `## Phase handoff` block when one is written). This checks the oracle's
consistency; it does not limit which tests may be red.

Only a clean scope audit, this cause-specific red result, and the
rejected-value search (when it applies) permit implementation.
