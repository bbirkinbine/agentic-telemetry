#!/usr/bin/env bash
# Batch path: copy file-based evidence from this Mac to the collector landing zone
# (spec 0004). Idempotent; safe to run every 15 minutes from launchd.
#
# Layout under the landing root:
#   <host>/<project>/.agentic/delegate/...   ledger, result.json, state.json, run transcripts
#   <host>/<project>/.agentic/telemetry/...  tool-call ledgers (scaffold Phase 1)
#   <host>/_sessions/{claude-code,pi,codex}/ client transcripts
# `.agentic` is kept so run-table names each project after the parent of `.agentic`.
#
# Runs on macOS /bin/bash 3.2 (no associative arrays, no mapfile) and on either
# rsync: macOS ships openrsync as /usr/bin/rsync, so the filters use only anchored
# `/` patterns, a trailing `/` for directories, and `*`.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ship-ledgers.sh [--dest DIR] [--max-depth N] [--help]

Copy agentic evidence from this Mac into the collector landing zone, so the
run table, the session table and the Collector read it from one place.

From every project under PROJECTS_ROOT (a directory holding .agentic/delegate/
or .agentic/telemetry/), it copies only what run-table reads:
  .agentic/delegate/ledger.jsonl
  .agentic/delegate/results/<run>/{result.json,state.json,session/*.jsonl}
  .agentic/telemetry/*.jsonl
Never pi-agent/, npm-cache/, segments/, logs or prompts. Client transcripts go
to <host>/_sessions/{claude-code,pi,codex}/ (Codex .jsonl.zst included). Nothing
is ever deleted from the landing zone.

Options:
  --dest DIR       Copy into the local directory DIR (created if missing; its
                   parent must exist), e.g. the local stack's landing zone.
                   Without it, copy to TELEMETRY_VM over SSH.
  --max-depth N    How many levels below PROJECTS_ROOT to search (default 3).
  --help           Show this help and exit.

Environment:
  PROJECTS_ROOT                Where projects are searched (default $HOME/src).
  TELEMETRY_VM                 The VM to copy to (default telemetry.example).
  TELEMETRY_LANDING            The landing root on the VM (default /srv/telemetry/landing).
  CLAUDE_CONFIG_DIR            Claude Code's directory (default ~/.claude); transcripts under projects/.
  PI_CODING_AGENT_SESSION_DIR  Pi's session directory; else PI_CODING_AGENT_DIR/sessions.
  PI_CODING_AGENT_DIR          Pi's agent directory (default ~/.pi/agent).
  CODEX_HOME                   Codex's directory (default ~/.codex); transcripts under sessions/.

Symlinks are never copied. A PROJECTS_ROOT with no projects prints one line
(macOS privacy settings can hide a protected directory from launchd jobs).

Exit status: 0 when everything copied, or when the VM is unreachable and
nothing else failed (one line; the next run catches up); 1 when a copy failed,
ssh could not create the landing directory, two projects share a directory name
(both skipped, everything else ships), or a project's .agentic, .agentic/delegate
or .agentic/telemetry is a symlink (skipped), even if the VM is unreachable; 2
for a usage error, including a PROJECTS_ROOT that is missing or not a directory.
EOF
}

die_usage() {
  echo "ship-ledgers: $1 (see --help)" >&2
  exit 2
}

DEST=""
MAX_DEPTH=3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help | -h)
      usage
      exit 0
      ;;
    --dest)
      [[ $# -ge 2 && -n "$2" ]] || die_usage "--dest needs a directory"
      # A value starting with '-' is a mistyped option, and rsync would parse it as one.
      [[ "$2" != -* ]] || die_usage "--dest needs a directory, got '$2'"
      DEST="$2"
      shift 2
      ;;
    --max-depth)
      [[ $# -ge 2 ]] || die_usage "--max-depth needs a positive integer"
      [[ "$2" =~ ^[1-9][0-9]*$ ]] || die_usage "--max-depth must be a positive integer, got '$2'"
      MAX_DEPTH="$2"
      shift 2
      ;;
    -*) die_usage "unknown option '$1'" ;;
    *) die_usage "unexpected argument '$1'" ;;
  esac
done

if [[ -n "$DEST" ]]; then
  [[ -d "$(dirname -- "$DEST")" ]] || die_usage "--dest parent directory '$(dirname -- "$DEST")' does not exist"
  [[ ! -e "$DEST" || -d "$DEST" ]] || die_usage "--dest '$DEST' exists and is not a directory"
fi

SRC_ROOT="${PROJECTS_ROOT:-$HOME/src}"
[[ -e "$SRC_ROOT" ]] || die_usage "PROJECTS_ROOT '$SRC_ROOT' does not exist"
[[ -d "$SRC_ROOT" ]] || die_usage "PROJECTS_ROOT '$SRC_ROOT' is not a directory"
VM="${TELEMETRY_VM:-telemetry.example}"
LANDING="${TELEMETRY_LANDING:-/srv/telemetry/landing}"
HOST="$(hostname -s)"

# --- Discovery: every directory holding .agentic evidence, up to MAX_DEPTH levels down.
# Globs skip hidden directories (and with them .git); node_modules and symlinked
# directories are skipped by name and test. A found project is not searched further.
FAILED=0
PROJECTS=()
find_projects() {
  local dir="$1" depth="$2" child
  for child in "$dir"/*/; do
    child="${child%/}"
    [[ -d "$child" && ! -L "$child" ]] || continue
    [[ "$(basename -- "$child")" != node_modules ]] || continue
    if [[ -d "$child/.agentic/delegate" || -d "$child/.agentic/telemetry" ]]; then
      # Copies skip symlinks, so a linked evidence directory would ship as nothing
      # while reporting success: refuse it by name instead (spec 0004, D9).
      if [[ -L "$child/.agentic" || -L "$child/.agentic/delegate" || -L "$child/.agentic/telemetry" ]]; then
        echo "ship-ledgers: skipped $child: .agentic or its evidence directory is a symlink" >&2
        FAILED=1
      else
        PROJECTS+=("$child")
      fi
    elif ((depth < MAX_DEPTH)); then
      find_projects "$child" $((depth + 1))
    fi
  done
}
find_projects "$SRC_ROOT" 1
# Under launchd, macOS privacy settings can hide a protected root's contents: say so.
((${#PROJECTS[@]} > 0)) || echo "ship-ledgers: no projects found under $SRC_ROOT (depth $MAX_DEPTH)"

# Two projects with the same directory name would land in one place: skip both,
# keep shipping everything else, and exit 1 at the end (spec 0004, D6).
SKIP=()
i=0
while ((i < ${#PROJECTS[@]})); do
  SKIP[i]=0
  i=$((i + 1))
done
i=0
while ((i < ${#PROJECTS[@]})); do
  j=$((i + 1))
  while ((j < ${#PROJECTS[@]})); do
    if [[ "$(basename -- "${PROJECTS[$i]}")" == "$(basename -- "${PROJECTS[$j]}")" ]]; then
      echo "ship-ledgers: two projects share the name '$(basename -- "${PROJECTS[$i]}")':" \
        "${PROJECTS[$i]} and ${PROJECTS[$j]}; skipped both" >&2
      SKIP[i]=1
      SKIP[j]=1
      FAILED=1
    fi
    j=$((j + 1))
  done
  i=$((i + 1))
done

# --- Destination.
if [[ -n "$DEST" ]]; then
  [[ -d "$DEST" ]] || mkdir -- "$DEST"
  mkdir -p -- "$DEST/$HOST/_sessions"
  TARGET="$DEST/$HOST"
else
  # Reachable? (Tailscale or LAN). If not, do nothing; the next run catches up.
  if ! nc -z -w 3 "$VM" 22 >/dev/null 2>&1; then
    echo "ship-ledgers: $VM unreachable, skipped"
    # 0 for a plain offline run; 1 when discovery already found a problem (D10).
    exit "$FAILED"
  fi
  if ! ssh "$VM" mkdir -p -- "$LANDING/$HOST/_sessions"; then
    echo "ship-ledgers: could not create $LANDING/$HOST/_sessions on $VM over ssh" >&2
    exit 1
  fi
  TARGET="$VM:$LANDING/$HOST"
fi

copy() {
  local label="$1" source="$2" target="$3"
  shift 3
  # -rpt, not -a: without -l a symlink is skipped, so neither a link nor its
  # target (a credential, or an absolute home path) reaches the landing zone.
  if rsync -rpt "$@" "$source/" "$target/"; then
    echo "ship-ledgers: shipped $label"
  else
    echo "ship-ledgers: copy failed: $source" >&2
    FAILED=1
  fi
}

# --- Project evidence: the reader allowlist, nothing else.
PROJECT_FILTER=(
  '--include=/.agentic/'
  '--include=/.agentic/delegate/'
  '--include=/.agentic/delegate/ledger.jsonl'
  '--include=/.agentic/delegate/results/'
  '--include=/.agentic/delegate/results/*/'
  '--include=/.agentic/delegate/results/*/result.json'
  '--include=/.agentic/delegate/results/*/state.json'
  '--include=/.agentic/delegate/results/*/session/'
  '--include=/.agentic/delegate/results/*/session/*.jsonl'
  '--include=/.agentic/telemetry/'
  '--include=/.agentic/telemetry/*.jsonl'
  '--exclude=*'
)
i=0
while ((i < ${#PROJECTS[@]})); do
  project="${PROJECTS[$i]}"
  if ((SKIP[i] == 0)); then
    copy "$(basename -- "$project")" "$project" "$TARGET/$(basename -- "$project")" \
      "${PROJECT_FILTER[@]}"
  fi
  i=$((i + 1))
done

# --- Client transcripts, kept beyond the clients' own retention (Claude Code
# deletes after cleanupPeriodDays). Directories resolve as session-table's do.
expand_home() {
  case "$1" in
    "~") echo "$HOME" ;;
    "~/"*) echo "$HOME/${1#\~/}" ;;
    *) echo "$1" ;;
  esac
}
if [[ -n "${PI_CODING_AGENT_SESSION_DIR:-}" ]]; then
  PI_SESSIONS="$(expand_home "$PI_CODING_AGENT_SESSION_DIR")"
else
  PI_SESSIONS="$(expand_home "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}")/sessions"
fi
CLAUDE_SESSIONS="$(expand_home "${CLAUDE_CONFIG_DIR:-$HOME/.claude}")/projects"
CODEX_SESSIONS="$(expand_home "${CODEX_HOME:-$HOME/.codex}")/sessions"

TRANSCRIPTS=('--include=*/' '--include=*.jsonl' '--exclude=*')
[[ -d "$CLAUDE_SESSIONS" ]] &&
  copy "claude-code sessions" "$CLAUDE_SESSIONS" "$TARGET/_sessions/claude-code" "${TRANSCRIPTS[@]}"
[[ -d "$PI_SESSIONS" ]] &&
  copy "pi sessions" "$PI_SESSIONS" "$TARGET/_sessions/pi" "${TRANSCRIPTS[@]}"
[[ -d "$CODEX_SESSIONS" ]] &&
  copy "codex sessions" "$CODEX_SESSIONS" "$TARGET/_sessions/codex" \
    '--include=*/' '--include=*.jsonl' '--include=*.jsonl.zst' '--exclude=*'

exit "$FAILED"
