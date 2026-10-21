#!/usr/bin/env bash
# Batch path: copy file-based evidence from this Mac to the VM landing. Idempotent;
# safe to run every 15 minutes from launchd. Exits quietly when the VM is unreachable.
# Layout on the VM: landing/<host>/<project>/<file>.jsonl
set -euo pipefail
VM="${TELEMETRY_VM:-telemetry.example}"
DEST="${TELEMETRY_LANDING:-/srv/telemetry/landing}/$(hostname -s)"
SRC_ROOT="${PROJECTS_ROOT:-$HOME/src}"   # override with PROJECTS_ROOT

# Reachable? (Tailscale or LAN). If not, do nothing; next run will catch up.
nc -z -w 3 "$VM" 22 2>/dev/null || exit 0

for proj in "$SRC_ROOT"/*/; do
  name="$(basename "$proj")"
  for dir in .agentic/telemetry .agentic/delegate; do
    [[ -d "$proj/$dir" ]] || continue
    rsync -a --include='*/' --include='*.jsonl' --include='result.json' --exclude='*' \
      "$proj/$dir/" "$VM:$DEST/$name/$(basename "$dir")/"
  done
done

# Session files you want kept beyond the clients' own retention. Claude Code deletes
# after cleanupPeriodDays; Pi and Codex keep theirs. Copy all three; the VM dedups by path.
rsync -a --include='*/' --include='*.jsonl' --exclude='*' \
  "$HOME/.claude/projects/" "$VM:$DEST/_sessions/claude-code/"
rsync -a --include='*/' --include='*.jsonl' --exclude='*' \
  "$HOME/.pi/agent/sessions/" "$VM:$DEST/_sessions/pi/"
[[ -d "$HOME/.codex/sessions" ]] && rsync -a --include='*/' --include='*.jsonl' --exclude='*' \
  "$HOME/.codex/sessions/" "$VM:$DEST/_sessions/codex/"
