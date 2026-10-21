#!/usr/bin/env bash
# SessionStart hook — warns when a coding session opens on main/master, or
# in a checkout that already has uncommitted changes.
#
# Feature and fix code must be written on a dedicated branch, never on
# main. This hook is the early reminder; where the flavor installs
# pre-commit, its no-commit-to-branch hook is the hard backstop. See
# AGENTS.md -> "Git workflow".
#
# Hook output is surfaced to the agent at session start.

# symbolic-ref, not `rev-parse --abbrev-ref HEAD`: a repository with no
# commits yet has a branch but no revision, and rev-parse answers "HEAD"
# there. That is the founding session, where this warning is also the
# first evidence that the client loaded its hooks. Detached HEAD prints
# nothing and stays silent.
branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || true)"

case "$branch" in
  main | master)
    echo "Git: this session started on '$branch'. Per AGENTS.md -> Git workflow,"
    echo "do not write feature or fix code on '$branch'. Create a branch first:"
    echo "  - issue-tracked work:  gh issue develop <N> --name <N>-<slug> --checkout"
    echo "  - untracked tiny work: git switch -c <type>/<slug>"
    ;;
esac

# Uncommitted changes at session start were made by someone else: another
# agent, a person, or an earlier session. Two writers in one checkout mix
# their edits into one tree and one diff (AGENTS.md -> "One writing agent per
# checkout"). A repository with no commits is the founding session, where
# everything is uncommitted by design. GIT_OPTIONAL_LOCKS=0 keeps git from
# taking the index lock another agent's git command may hold.
if git rev-parse -q --verify HEAD >/dev/null 2>&1 &&
  [[ -n "$(GIT_OPTIONAL_LOCKS=0 git status --porcelain 2>/dev/null | head -n 1)" ]]; then
  echo "Git: this checkout already has uncommitted changes. If another agent or"
  echo "person may be editing here, start this session in its own worktree"
  echo "instead (git worktree add ../<repo>-<branch> -b <branch>). If the changes"
  echo "are yours from an earlier session, continue."
fi
