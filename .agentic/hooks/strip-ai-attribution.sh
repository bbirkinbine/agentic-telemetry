#!/usr/bin/env bash
# pre-commit commit-msg hook: strip AI-attribution lines from the commit
# message. This is the MECHANICAL backstop for the rule in
# AGENTS.md — "no AI Co-Authored-By trailers, no generated-by-client
# footers." AI assistance is acknowledged
# once, in README.md; it does not belong in git history (which is forever
# and, on a public repo, world-indexed).
#
# IMPORTANT: this is a *git* hook run by the pre-commit framework at the
# commit-msg stage (wired in .pre-commit-config.yaml) — not an agent
# lifecycle hook. pre-commit hands it the path to the
# commit-message file as $1; it rewrites that file in place.
#
# It removes only AI-attributable lines: Co-Authored-By trailers naming
# Claude/Codex or their vendor noreply addresses, and generated-by-client
# footers (with or without a markdown link). Human
# Co-Authored-By trailers are left untouched — silently dropping a
# deliberately-added human co-author would be the wrong default.

set -euo pipefail

MSG_FILE="${1:?commit-msg hook expects the message-file path as \$1}"

# ERE, matched case-insensitively (grep -i):
#   - Co-Authored-By trailers attributable to Claude / anthropic noreply.
#   - "Generated with Claude Code" footers, with or without the leading
#     robot emoji and the "[Claude Code](...)" markdown link.
STRIP_RE='^[[:space:]]*Co-Authored-By:.*(Claude|Codex|noreply@(anthropic|openai)\.com)|Generated with \[?(Claude Code|Codex)'

grep -qiE "$STRIP_RE" "$MSG_FILE" || exit 0  # nothing to strip

tmp="$(mktemp)"
# Drop the matching lines, then collapse any trailing blank lines the
# removal left behind (git's message cleanup already ran before this hook,
# so it will not re-trim them). The awk buffers blank lines and only emits
# them when a non-blank line follows, so trailing blanks disappear while
# the blank between subject and body is preserved.
{ grep -viE "$STRIP_RE" "$MSG_FILE" || true; } | awk '
  /^[[:space:]]*$/ { pending++; next }
  { for (; pending > 0; pending--) print ""; print }
' > "$tmp"
mv "$tmp" "$MSG_FILE"

echo "strip-ai-attribution: removed AI-attribution line(s) from the commit message" >&2
exit 0
