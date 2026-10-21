---
description: Interview the human to create or refresh docs/specs/0000-product.md — the product-level spec (the PRD's job). One question at a time; writes the file from the answers.
argument-hint: [product name — defaults to the repo name]
---

Product-spec interview. Where `/spec` covers one unit of work, this
covers the product itself. The output is `docs/specs/0000-product.md` —
status `evergreen`, the one spec that is revised in place (see
`docs/specs/README.md` → "The product spec").

A blank PRD template gets ignored. This command exists so the file is
produced by interview instead: you ask, the human answers, you write.
Do NOT answer your own questions or fill sections from inference — the
point is to surface the human's product judgment, not substitute yours.

Procedure:

1. Pick the mode:
   - `docs/specs/0000-product.md` absent → **create mode**: ask all
     seven questions below, one at a time, in order.
   - File exists → **refresh mode**: read it, compare against the open
     issue list (`gh issue list -s open` if available), and ask only
     about sections that are missing, vague, or contradicted by where
     the project has gone since. Three questions maximum. A product
     that names a reference but has no `## Reference behavior` counts as
     missing a section; its confirmation batch (step 4) is one question.
     If nothing needs asking, say "product spec is current" and stop.
2. Ask **one question at a time** and wait for the answer before the
   next. If an answer is vague ("not sure", "depends"), push back once:
   "what would have to be true for that to be concrete?" Don't push
   twice — record the honest unknown under `## Open questions` instead
   of inventing certainty.
3. If any answer names a reference the product should resemble ("plays
   like arcade Pac-Man", "a Trello-style board", "git-compatible"), it
   is load-bearing: a one-line mention gives later specs nothing to
   trace, and "original, not a copy" drifts into "not like the
   reference". Do step 4 before writing.
4. **Reference behavior** — ask which edition, version, or observed
   system the human means, then retrieve authoritative sources for the
   behaviors whose correctness depends on that reference. Prefer official
   documentation; when only observable behavior or secondary documentation
   exists, label it empirical or secondary instead of presenting it as
   authoritative. Never reconstruct rules, timers, scoring values, commands,
   formats, or conventions from model memory. If a claim cannot be sourced,
   put it in `## Open questions` or restate it as an original behavior the
   human deliberately chooses. List the sourced, recognizable behaviors
   concretely enough to test, cite each row, and mark each `must`, `later`,
   or `won't` with your best guess. List separately what must not be copied
   (artwork, audio, code, trademarks, or protected data). Show the inventory
   in one batch and ask the human to correct the marks; record only what they
   confirm. Following verified behavior and copying protected expression are
   separate decisions; keep them apart. Record every cited source with URL,
   retrieval date, and license/status under `## External references`, using
   the provenance categories in `docs/specs/README.md`.
5. After the last answer, write the file from the answers, keeping the
   human's voice. Skeleton below. Stop after writing; surface the path
   and do NOT continue into `/spec` or `/plan`. End with a checkpoint
   report (`AGENTS.md` → **Shared workflow protocols** → **Checkpoint
   report**) whose continue line is `/spec <first roadmap unit>`.

Questions:

1. **What pain does this remove, and for whom?** A concrete user, role,
   or system — "future users" is not an answer. What do they do today
   without it?
2. **Who is this NOT for?** The adjacent audience you are deliberately
   not serving, and the existing alternative you are not trying to
   beat. (This is the answer that keeps feature specs from drifting.)
3. **How will you know it's working?** Product-level, observable in the
   world — "I stopped doing X by hand", "N people besides me use it" —
   not "tests pass" or "code is clean".
4. **What would make you kill it in 6 months?** The failure mode that
   would make you say "this shouldn't exist".
5. **What directions are you deliberately not pursuing?** Product
   non-goals — features or audiences you might be tempted toward but
   are ruling out, so individual specs don't relitigate them.
6. **What constraints is this built under?** Platform, licensing,
   budget, time, "solo-maintained", "must run on the homelab" — the
   facts a planner must not design against.
7. **What are the first two or three units of work?** These become the
   first issues and feature specs; they seed `## Roadmap pointers`.
   With a reference inventory, each unit names the items it covers.

File skeleton (substitute the product name from `$ARGUMENTS` or the
repo name, and today's date):

```markdown
# 0000 — <Product name>

**Status:** evergreen
**Last updated:** YYYY-MM-DD

## Problem

<Q1 and Q2: the pain, the concrete user, who it is NOT for, and the
alternative not being competed with.>

## Success metrics

<Q3: observable, product-level outcomes.>

## Kill criteria

<Q4: what would make this not worth continuing.>

## Product non-goals

- <Q5: one bullet per ruled-out direction.>

## Constraints and assumptions

- <Q6: one bullet per constraint.>

## Reference behavior

<Only when the product names a reference (step 4). Omit otherwise.>

Reference: <what the product should resemble, and how closely>

| Item | Expected behavior | Source | Mark | Spec |
| --- | --- | --- | --- | --- |
| <R1> | <the verified or explicitly original behavior> | <S1, empirical, or original> | must / later / won't | <NNNN once a spec covers it> |

Not copied: <assets, code, trademarks, or data the human ruled out>

## External references

<Only when Reference behavior cites outside authority or observed behavior.
For each S-number: source name, URL or observation record, retrieval/capture
date, license/status, and authoritative / secondary / empirical. A feature
spec that implements the item repeats the applicable source in its own
External references section.>

## Roadmap pointers

<Q7: links to the issues/specs currently serving the direction —
a list of links, not a plan, each naming the reference items it covers
when there is an inventory. Update as specs land; the issue tracker
stays the backlog.>

## Open questions

- <Anything the human answered with an honest "don't know yet".>
```

This file is evergreen: when product direction changes later, run
`/product-spec` again (refresh mode) rather than editing history into
it — the design-log record of *why* lives in the feature specs and
their issues.
