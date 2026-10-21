#!/usr/bin/env bash
# Shared PostToolUse hook — format after edits, with optional strict checks.
#
# Formats only the files the edit touched, read from the hook payload on
# stdin: `tool_input.file_path` (Claude Code, Pi) or the `*** Add File:` /
# `*** Update File:` / `*** Move to:` lines of a Codex apply_patch. Formatting
# the whole tree here would rewrite files the edit never named, which breaks
# the test-first scope audit, a handoff's plan scope, and the human's
# uncommitted work. The gate's format-check still covers the whole tree.

set -euo pipefail

# Run from the project root (two levels above .agentic/hooks/) so the
# readiness guard and formatting target the project no matter what CWD the
# invoking client used. Bail quietly if resolution fails — a formatting
# hook must never fail the tool call over its own infrastructure.
# The tools are named only in .agentic/toolchain.sh.
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 0
root="$(pwd -P)"

payload=""
[[ -t 0 ]] || payload="$(cat)"

TOOLCHAIN=.agentic/toolchain.sh
bash -n "$TOOLCHAIN" || exit 1
status=0
bash "$TOOLCHAIN" ready || status=$?
[[ "$status" == 3 ]] && exit 0
[[ "$status" == 0 ]] || exit "$status"

# Relative paths resolve against the client's working directory.
cwd="$(printf '%s' "$payload" \
  | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[[ -n "$cwd" ]] || cwd="$root"

paths=()
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  [[ "$path" == /* ]] || path="$cwd/$path"
  # Deleted files and paths outside the project are not ours to format.
  [[ -f "$path" ]] || continue
  dir="$(cd "$(dirname "$path")" && pwd -P)" || continue
  path="$dir/$(basename "$path")"
  [[ "$path" == "$root"/* ]] || continue
  paths+=("$path")
done < <(
  printf '%s\n' "$payload" \
    | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
  # apply_patch arrives as one JSON string, so its newlines are literal \n.
  printf '%s\n' "$payload" | awk '{ gsub(/\\n/, "\n"); print }' \
    | sed -En 's/^\*\*\* (Add File|Update File|Move to): (.*)$/\2/p'
)

if ((${#paths[@]})); then
  bash "$TOOLCHAIN" format "${paths[@]}"
fi

if [[ "${1:-}" == "--strict" ]]; then
  bash "$TOOLCHAIN" lint
  bash "$TOOLCHAIN" typecheck
fi
