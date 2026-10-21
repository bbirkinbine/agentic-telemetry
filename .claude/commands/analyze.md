---
description: Read-only cross-artifact consistency check — spec vs tests vs semantic change set vs standing rules. Run after /test-first or before /review. Reports findings; changes nothing.
argument-hint: "[spec-path] [<base>..<head> for an explicit historical range]"
---

Cross-artifact consistency check. The reviewers judge the *code*; this
command checks that the artifacts agree with *each other* — that the
tests actually cover the spec and the change set actually serves it. Run it
at either of two points: after `/test-first` (before implementation —
the cheap time to find a coverage hole) or before `/review`.

Invoke the fresh read-only `analyzer` subagent. It must not inherit or receive
the implementation reasoning; pass durable artifacts and the change-set
snapshot, not a persuasive summary from the coder. The analyzer reports
findings and changes nothing.

Inputs:

1. Resolve the active spec using `AGENTS.md` → **Shared workflow protocols** →
   **Active-spec resolution**.
2. By default, build the complete pre-commit semantic change set from
   `AGENTS.md` → **Shared workflow protocols** → **Semantic change set**. Pass
   the analyzer the spec path, base ref, merge-base, status, tracked
   working-tree diff, and untracked-path manifest; it must inspect every
   untracked file relevant to the spec and tests. If `$ARGUMENTS` explicitly
   contains `<base>..<head>`, use that historical committed range and state
   that current working-tree changes were not analyzed.
3. Read the test files the feature added or modified.

The analyzer then:

1. Build the coverage table — one row per success criterion:

   | Success criterion | Covering test(s) | Status |
   | --- | --- | --- |
   | <criterion, abbreviated> | `tests/<file>::<test name>` | covered / partial / **uncovered** |

2. Then check, in both directions:
   - **Spec → tests:** every success criterion has at least one test
     that would fail if the behavior were wrong (not merely a test that
     exercises the code path).
   - **Tests → spec:** tests that pin down behavior the spec never
     defines — undeclared scope that should be either specced or
     dropped.
   - **Non-goals:** anything in the change set or tests implementing a
     declared non-goal.
   - **External references:** values in the change set that claim outside
     authority (constants, format markers, API contracts) but aren't
     declared in the spec's `## External references`, or declared
     sources with no pinned URL/date in the code.
   - **Standing rules:** the change set against the complete contract in
     `AGENTS.md`.
3. Tag each finding `[auto-fix]`, `[ask-user]`, or `[no-op]` with the
   same meanings the reviewers use. A coverage hole found before
   implementation is `[auto-fix]` (route it back to `/test-first`); a
   spec contradiction is always `[ask-user]`.

Surface the analyzer's output verbatim. It ends with one line: `consistent` or
`N findings (M ask-user)`. If there is no unambiguous active spec, stop before
invoking it — there is nothing reliable to compare. A stop here or on an
`[ask-user]` finding ends with a checkpoint report
(`AGENTS.md` → **Shared workflow protocols** → **Checkpoint report**).
