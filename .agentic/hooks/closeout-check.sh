#!/usr/bin/env bash
# closeout-check — refuse to merge a feature PR whose close-tasks are missing.
#
# The close-tasks rule (AGENTS.md -> Git workflow -> "Close-tasks ride in
# the PR they belong to") says a change's own bookkeeping rides the feature
# branch: the spec's **Status:** flip to shipped, the regenerated
# docs/specs/README.md dashboard, and the AGENTS.md current-state block. The
# rule is explicit and has still been skipped in practice, costing a whole
# extra PR for pure bookkeeping. This is the check that makes it stick.
#
# Runs as a CI job on pull_request, deliberately not as a local pre-push
# hook: close-out is only expected once the work is finished, so blocking
# every intermediate push would train people into `--no-verify`, which is
# how the written rule died in the first place. CI re-runs on each push, so
# close-out that lands late still satisfies it — matching the rule's own
# "committed before the PR is opened (or pushed to the same branch before it
# merges)".
#
# Client-neutral by construction: git and CI are the layers every supported
# client shares, so no client can bypass this from its own hooks.
#
# Silent on anything that is not finished spec work: a `<type>/<slug>` chore
# branch, a branch whose number has no spec, or a project that does not use
# docs/specs at all. Each check skips itself rather than inventing a rule.
#
# Branch name comes from $CLOSEOUT_BRANCH, then $GITHUB_HEAD_REF (set by
# GitHub Actions on pull_request, where HEAD is detached), then the current
# branch. Usage: closeout-check.sh [branch-name]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT" || exit 0

branch="${1:-${CLOSEOUT_BRANCH:-${GITHUB_HEAD_REF:-}}}"
if [[ -z "$branch" ]]; then
  branch="$(git branch --show-current 2>/dev/null)" || exit 0
fi
[[ -n "$branch" ]] || exit 0

# Both branch conventions: `spec-NNNN-<slug>` (local numbering) and
# `<issue-number>-<slug>` (issue mode). `<type>/<slug>` chores are exempt —
# they have no spec to close out.
if [[ "$branch" =~ ^spec-([0-9]{4})- ]]; then
  num="${BASH_REMATCH[1]}"
elif [[ "$branch" =~ ^([0-9]+)- ]]; then
  num="$(printf '%04d' "${BASH_REMATCH[1]}")"
else
  exit 0
fi

shopt -s nullglob
spec_matches=(docs/specs/"$num"-*.md)
shopt -u nullglob
# No spec for this branch number: nothing to close out. Ambiguity is a real
# problem rather than a pass — the same rule the active-spec protocol uses.
if ((${#spec_matches[@]} == 0)); then
  exit 0
elif ((${#spec_matches[@]} > 1)); then
  echo "closeout-check: spec number ${num} matches ${#spec_matches[@]} files:" >&2
  printf '  - %s\n' "${spec_matches[@]}" >&2
  echo "Spec numbers are identities and must be unique. Rename or remove the duplicate." >&2
  exit 1
fi
spec_file="${spec_matches[0]}"

missing=()

# 1. The spec must say it shipped. `shipped` in an open PR means "ships when
# this PR merges" — the intended reading, not a lie about current state.
# `draft` and `shipping` are in-flight states, so both fail here.
status="$(
  awk '/^\*\*Status:\*\*/ {
         sub(/^\*\*Status:\*\* */, ""); sub(/[ \t]+$/, ""); print; exit
       }' "$spec_file" 2>/dev/null
)"
case "$status" in
  shipped) ;;
  # A spec that was abandoned or superseded is also closed out; it just did
  # not ship. Evergreen specs never close.
  abandoned | superseded-by-* | evergreen) ;;
  "") missing+=("${spec_file}: no '**Status:**' field") ;;
  *) missing+=("${spec_file}: still '**Status:** ${status}' — flip it to shipped") ;;
esac

# 2. The dashboard must already be regenerated on this branch. Regenerate
# into a copy and compare rather than rewriting the file: a check that
# silently edits the tree hides the very drift it is reporting.
readme="docs/specs/README.md"
regen="$SCRIPT_DIR/specs-status.sh"
if [[ -f "$readme" && -x "$regen" ]]; then
  backup="$(mktemp)"
  cp "$readme" "$backup"
  if "$regen" >/dev/null 2>&1 && ! cmp -s "$readme" "$backup"; then
    missing+=("${readme}: dashboard is stale — run .agentic/hooks/specs-status.sh")
  fi
  cp "$backup" "$readme"
  rm -f "$backup"
fi

# Deliberately NOT checked: whether AGENTS.md's "Open work / current state"
# block mentions the spec. The close-tasks rule does list it, but it is the
# one item no grep can actually measure — presence of the number is a proxy
# for a useful note, and the proxy fails both ways. It also pressures that
# block toward a changelog when it is meant to be short and rewritten. The
# two checks above are unambiguous facts about file state; that one is a
# judgment call, and a gate with false-positive surface gets deleted, which
# costs the gate and the habit both.

if ((${#missing[@]})); then
  echo "" >&2
  echo "close-out incomplete for spec ${num} — these belong on THIS branch," >&2
  echo "before the PR merges, not in a follow-up:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "" >&2
  echo "See AGENTS.md -> Git workflow -> 'Close-tasks ride in the PR they belong to'." >&2
  exit 1
fi

echo "close-out: spec ${num} bookkeeping present"
