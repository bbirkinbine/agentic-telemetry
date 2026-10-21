#!/usr/bin/env bash
# Local delegate runner — hands a whole phase of an approved spec to a local
# agent session (Pi, RPC mode) on a local model in a new git worktree, then
# verifies the full gate and the plan's scope there before committing the
# result on a handoff branch for the orchestrating session to review. It also
# qualifies local models, keeps their per-phase record, and selects among a
# project's candidates.
#
# Usage: .agentic/delegate-run.sh <subcommand> [args] [--profile <path>]
#
#   qualify <model>          Plumbing check: two direct requests to the model
#                            (a first response, then a known-answer edit of a
#                            generated file of about 270 lines), graded
#                            exactly. A model must pass before a handoff will
#                            use it; any change to its setup (model, quant,
#                            runtime, request settings, endpoint, runner
#                            version) needs a new pass.
#   record-review <run> pass|model-defect|spec-defect
#                            Record /review's final verdict on a verified
#                            handoff (spec-defect: the spec or plan was at
#                            fault, not the model; packet-defect is accepted
#                            as its older name). Until then a green run is
#                            pending.
#   policy                   Prints `auto` and exits 0 only when this
#                            checkout's [[project]] entry has policy = "auto",
#                            activation_approved = true, and at least one
#                            qualified candidate; otherwise prints `off: <why>`
#                            and exits 1. With a profile but a missing
#                            dependency, exits 2. The orchestrator checks this
#                            before offering a handoff.
#   activate | deactivate    Set this checkout's [[project]] entry to
#                            policy = "auto" with activation_approved = true
#                            (or back to "off"). Run only on the human's
#                            explicit yes; activate refuses unless a
#                            candidate is qualified. Prints before/after.
#   init [--key-env VAR] <base-url> ...
#                            Print a draft profile from what each server's
#                            /models lists: endpoints, one [[model]] per
#                            model with the fields to confirm marked, and a
#                            [[project]] entry for this checkout (policy off).
#                            Prints only; save it yourself after review. With
#                            an existing profile, prints only what is missing.
#                            --key-env names the variable holding the next
#                            URL's key.
#   discover                 What each endpoint's /models lists, matched to
#                            profile models and their qualification.
#   status                   Each profile model's qualification and its
#                            record per phase bucket in this project.
#   reset <model> <bucket>   Clear a demotion (phase:implement,
#                            phase:test-first+implement); starts a fresh record.
#   handoff <spec> --allow-commit [--phase implement|test-first+implement]
#                            [--model <name>] [--progress quiet|summary]
#                            [--foreground]
#                            Hand a whole phase of an approved spec to a
#                            local agent session (Pi, RPC mode) in a new git
#                            worktree: install, run the session headless on
#                            the model, then verify the full gate and that
#                            only the plan's files changed (and, for
#                            `implement`, no test file). --allow-commit
#                            acknowledges the human's explicit approval for
#                            the temporary handoff-branch commit. A verified
#                            run is committed on `handoff/<run>` for the
#                            orchestrator to review and merge; scored per
#                            phase. By default it launches detached, prints
#                            the run id, and exits 0 (2 if refused); follow
#                            it with handoff-status --wait. --foreground runs
#                            it attached and exits with the outcome. The
#                            session is supervised by a renewable inactivity
#                            lease, not a fixed limit; see
#                            docs/local-executor.md -> "Supervising a
#                            handoff" (log locations included).
#   handoff-status <run-id> [--wait <seconds>] [--interval <seconds>]
#                            [--since <cursor>]
#                            Token-free status from local files. Waits up to
#                            <seconds> for a final or paused state; exits
#                            with that state's handoff code, or 12 while the
#                            run is still going. --interval prints one
#                            bounded liveness line per interval while it
#                            waits (processes alive, last activity, running
#                            tool, lease, tool calls and changed paths since
#                            the previous line). The snapshot ends with
#                            `cursor: <segment>:<offset>`; --since <cursor>
#                            summarizes only the events after it.
#   handoff-resume <run-id> --allow-commit [--max-total <seconds>]
#                            [--progress quiet|summary] [--foreground]
#                            Continue a paused (or orphaned) handoff in the
#                            same worktree and Pi session, after checking the
#                            spec, plan, snapshot, branch, model fingerprint,
#                            and scope. --max-total raises the ceiling.
#   handoff-stop <run-id>    Ask a live handoff to checkpoint and pause.
#   handoff-clean [<run-id> | --all]
#                            List handoff worktrees, or remove one (or all)
#                            with its handoff/ branch after merging or
#                            abandoning it.
#
# Scoring: a success is a verified handoff that passed review; a failure is
# needs_frontier or a model-defect review. A model is demoted for a phase
# bucket after a model-defect review, or when it has at least 5 scored runs
# and the upper bound of the 95% Wilson interval over its last 20 is below
# 50%. Demotion warns but does not block a manual run. Selection explores
# candidates with fewer than 5 scored runs in list order, then ranks by the
# interval's lower bound; unqualified, demoted, and unreachable candidates
# are skipped.
#
# Profile: ${AGENTIC_DELEGATE_PROFILE:-${XDG_CONFIG_HOME:-~/.config}/agentic/local-models.toml}
# (outside the repository; it names private endpoints). Example:
#
#   [[endpoint]]
#   name = "lan-gateway"
#   base_url = "http://<host>:<port>/v1"
#   credential_env = "LOCAL_MODEL_TOKEN"   # optional; variable NAME, never the value
#
#   [[model]]                              # pi_thinking: optional for handoff;
#   name = "coder-a"                       # provider/model come from this profile
#   endpoint = "lan-gateway"
#   model_id = "<id the server reports>"
#   quant = "Q8_K"
#   runtime = "llama.cpp b5xxx"
#   context = 131072
#   timeout_seconds = 900                  # optional; whole-call limit
#   request = '{"temperature": 0.6}'       # optional; JSON merged into each request
#
# Exit status:
#   0   qualify: passed; handoff (--foreground, handoff-status,
#       handoff-resume): verified and committed on handoff/<run>
#   10  needs_frontier: the session ended with the gate red or changed
#       nothing, or no candidate was selectable; implement in the
#       orchestrating session
#   11  paused and resumable (no gate, no commit)
#   12  handoff-status only: still running when the wait ended
#   2   refused: usage, profile, spec, or workspace preconditions
#   5   qualify: the model failed the plumbing check
#   3   infrastructure failure: endpoint, transport, harness, RPC, install,
#       worktree, or verifier
#   4   integrity stop: out-of-plan or test-file changes, or a session or
#       verifier commit
#
# Handoff supervision: DELEGATE_HANDOFF_LEASE_SECONDS (default 3600) is the
# inactivity lease, renewed by model or tool activity and new worktree
# content; DELEGATE_HANDOFF_RECOVERY_SECONDS (900) extends it once during
# retry or compaction recovery; DELEGATE_HANDOFF_GRACE_SECONDS (600) is how
# long a checkpoint may take before abort; DELEGATE_HANDOFF_ABORT_SECONDS (60)
# how long abort and shutdown may take before signals;
# DELEGATE_HANDOFF_MAX_TOTAL_SECONDS
# (unset) is an optional ceiling on active time across segments. The
# deprecated DELEGATE_HANDOFF_TIMEOUT sets that ceiling.
# DELEGATE_HANDOFF_SCOPE_CHECK_SECONDS (5) is the least time between in-flight
# scope checks; each runs after a tool call, and a violation aborts the
# session at once and ends the run as an integrity stop. After each
# compaction the runner restates the scope rules to the session as a steer.
#
# Requires: bash, git, curl, jq (1.6+), perl (the step watchdog), and
# sha256sum or shasum.
#
# Progress is `summary` when stderr is a terminal and `quiet` otherwise.
# DELEGATE_PROGRESS_INTERVAL changes the 15-second heartbeat interval; it is
# primarily useful for deterministic smoke tests.

set -uo pipefail

# Part of every setup fingerprint: changes to qualify's direct request
# protocol, or to how handoff runs are scored, revoke prior qualifications
# and start fresh records.
HARNESS_VERSION="delegate-run-v2"
# Ranking and demotion use the Wilson score interval (E. B. Wilson, JASA 22,
# 1927; see docs/influences.md in agentic-scaffold). Scoring parameters are
# unsourced starting points (docs/local-delegate-runner.md in
# agentic-scaffold -> "Unsourced starting points"); tune them from the ledger.
EXPLORE_MIN=5
WINDOW=20
DEMOTE_MIN=5
DEMOTE_BELOW=0.5
Z=1.96
DIAG_LINES=120
DIAG_BYTES=12000
PROGRESS_MODE=quiet
PROGRESS_INTERVAL="${DELEGATE_PROGRESS_INTERVAL:-15}"
PROGRESS_PID=""
PROGRESS_PID_FILE=""

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 3
DELEGATE_DIR="$ROOT/.agentic/delegate"
TOOLCHAIN=.agentic/toolchain.sh

say() { printf 'delegate-run: %s\n' "$*" >&2; }
progress() { [[ "$PROGRESS_MODE" == summary ]] && say "progress: $*"; return 0; }

configure_progress() {
  local requested="${1:-}"
  if [[ -n "$requested" ]]; then
    case "$requested" in quiet | summary) PROGRESS_MODE="$requested" ;; *) say "unknown progress mode: $requested (quiet or summary)"; exit 2 ;; esac
  elif [[ -t 2 ]]; then
    PROGRESS_MODE=summary
  else
    PROGRESS_MODE=quiet
  fi
  [[ "$PROGRESS_INTERVAL" =~ ^[1-9][0-9]*$ ]] || { say "DELEGATE_PROGRESS_INTERVAL must be a positive whole number"; exit 2; }
}

stop_progress_sidecar() {
  if [[ -n "$PROGRESS_PID" ]]; then
    kill "$PROGRESS_PID" 2>/dev/null || true
    wait "$PROGRESS_PID" 2>/dev/null || true
    PROGRESS_PID=""
  fi
  [[ -n "$PROGRESS_PID_FILE" ]] && rm -f "$PROGRESS_PID_FILE"
  PROGRESS_PID_FILE=""
}

start_heartbeat() {
  local label="$1" started
  [[ "$PROGRESS_MODE" == summary ]] || return 0
  stop_progress_sidecar
  started="$(date +%s)"
  # One signal-aware process owns the timer; stopping it never waits for a
  # child `sleep` to finish and therefore cannot inflate model-call timing.
  perl -e '
    $SIG{TERM} = sub { exit 0 };
    my ($interval, $label, $started) = @ARGV;
    while (1) {
      select undef, undef, undef, $interval;
      my $elapsed = time - $started;
      print STDERR "delegate-run: progress: $label — ${elapsed}s elapsed\n";
    }
  ' "$PROGRESS_INTERVAL" "$label" "$started" &
  PROGRESS_PID=$!
}

# Keep summaries bounded and remove credential-shaped assignments before they
# reach the terminal. Raw Pi events remain in the ignored result directory.
bounded_progress_field() {
  printf '%s' "$1" | perl -MEncode=encode -CS -e '
    local $/;
    $_ = <STDIN> // "";
    s/[\r\n\t]/ /g;
    s/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/?/g;
    s/\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY)[A-Za-z0-9_]*=)(?:"[^"]*"|\047[^\047]*\047|\S+)/${1}[REDACTED]/ig;
    s/(Authorization:\s*(?:Bearer\s+)?)(\S+)/${1}[REDACTED]/ig;
    if (length(encode("UTF-8", $_)) <= 160) { print; exit 0 }
    my $out = "";
    for my $char (split //) {
      last if length(encode("UTF-8", $out . $char)) > 157;
      $out .= $char;
    }
    print $out, "...";
  '
}

render_pi_event() {
  local line="$1" type tool detail failed duration attempt maximum reason
  type="$(jq -r '.type // empty' <<<"$line" 2>/dev/null)" || return 0
  case "$type" in
    agent_start) progress "agent started" ;;
    agent_settled) progress "agent settled" ;;
    turn_start) progress "turn started" ;;
    tool_execution_start)
      tool="$(jq -r '.toolName // "unknown"' <<<"$line")"
      case "$tool" in
        read | edit | write) detail="$(jq -r '.args.path // .args.file_path // .args.filePath // empty' <<<"$line")" ;;
        bash) detail="$(jq -r '.args.command // empty' <<<"$line")" ;;
        *) detail="" ;;
      esac
      tool="$(bounded_progress_field "$tool")"
      if [[ -n "$detail" ]]; then
        progress "tool start: $tool — $(bounded_progress_field "$detail")"
      else
        progress "tool start: $tool"
      fi
      ;;
    tool_execution_end)
      tool="$(jq -r '.toolName // "unknown"' <<<"$line")"
      failed="$(jq -r 'if .isError == true then "failed" else "passed" end' <<<"$line")"
      duration="$(jq -r 'if (.durationMs | type) == "number" then " (" + (.durationMs | tostring) + "ms)" else "" end' <<<"$line")"
      progress "tool end: $(bounded_progress_field "$tool") — $failed$duration"
      ;;
    auto_retry_start)
      attempt="$(jq -r 'if (.attempt | type) == "number" then (.attempt | tostring) else "?" end' <<<"$line")"
      maximum="$(jq -r 'if (.maxAttempts | type) == "number" then (.maxAttempts | tostring) else "?" end' <<<"$line")"
      progress "retry $attempt/$maximum scheduled"
      ;;
    auto_retry_end)
      attempt="$(jq -r 'if (.attempt | type) == "number" then (.attempt | tostring) else "?" end' <<<"$line")"
      failed="$(jq -r 'if .success == true then "succeeded" else "failed" end' <<<"$line")"
      progress "retry $attempt $failed"
      ;;
    compaction_start)
      reason="$(jq -r '.reason // "unknown"' <<<"$line")"
      progress "compaction started ($(bounded_progress_field "$reason"))"
      ;;
    compaction_end)
      reason="$(jq -r '.reason // "unknown"' <<<"$line")"
      progress "compaction ended ($(bounded_progress_field "$reason"))"
      ;;
    message_end)
      reason="$(jq -r '.message.stopReason // empty' <<<"$line")"
      [[ "$reason" == error || "$reason" == aborted ]] && progress "assistant message ended: $reason"
      ;;
  esac
  return 0
}

usage() {
  sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# ---- exit paths --------------------------------------------------------------
# Every non-usage exit after the lock is taken goes through finish(), which
# writes the result file and the ledger record before exiting.

refuse() { say "refused: $*"; OUTCOME=refused; REASON="$*"; finish 2; }
infra() { say "infrastructure failure: $*"; OUTCOME=infrastructure; REASON="$*"; finish 3; }
integrity() { say "integrity stop: $*"; OUTCOME=integrity; REASON="$*"; finish 4; }

# ---- small helpers -------------------------------------------------------------

if command -v sha256sum >/dev/null 2>&1; then
  SHA=(sha256sum)
else
  SHA=(shasum -a 256)
fi

sha_file() { "${SHA[@]}" <"$1" | cut -d' ' -f1; }

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Bounded tail of a log, for escalation diagnostics.
bounded_tail() { tail -n "$DIAG_LINES" "$1" | tail -c "$DIAG_BYTES"; }

# Run a verifier command in the handoff worktree without the endpoint
# credential. Each verifier command gets DELEGATE_STEP_TIMEOUT seconds
# (default 900) so a change that hangs a test cannot hang the run. The
# command runs in its own process group, which is killed whole on timeout;
# exit status is 124. Process groups its descendants created (Pi runs each
# tool command in one) are outside that group, so they are collected before
# TERM and again before KILL and killed too. Where `ps` is unavailable
# (Codex's macOS sandbox denies it), only the command's own group is
# signalled.
STEP_TIMEOUT="${DELEGATE_STEP_TIMEOUT:-900}"

# The handoff requires perl rather than run a verifier with no time limit.
bounded() {
  perl -e '
      my $limit = shift;
      my $pid = fork() // die "fork: $!";
      if ($pid == 0) { setpgrp(0, 0); exec { $ARGV[0] } @ARGV or exit 127; }
      my $own = getpgrp(0);
      my $groups = sub {
        my (%kids, %pgid, %seen, %found);
        open my $ps, "-|", "ps -axo pid=,ppid=,pgid= 2>/dev/null" or return ();
        while (<$ps>) {
          my ($p, $pp, $g) = split;
          next unless defined $g;
          push @{ $kids{$pp} }, $p;
          $pgid{$p} = $g;
        }
        close $ps;
        my @queue = ($pid);
        while (@queue) {
          my $p = shift @queue;
          next if $seen{$p}++;
          my $g = $pgid{$p};
          $found{$g} = 1 if $p != $pid && $g && $g != $pid && $g != $own;
          push @queue, @{ $kids{$p} || [] };
        }
        return keys %found;
      };
      local $SIG{ALRM} = sub {
        my %stray = map { $_ => 1 } $groups->();
        kill "TERM", -$pid; sleep 2;
        $stray{$_} = 1 for $groups->();
        kill "KILL", -$_ for keys %stray;
        kill "KILL", -$pid; waitpid($pid, 0);
        print STDERR "delegate-run: timed out after ${limit}s: @ARGV\n";
        exit 124;
      };
      alarm $limit;
      waitpid($pid, 0);
      exit($? & 127 ? 128 + ($? & 127) : $? >> 8);
  ' "$STEP_TIMEOUT" "$@"
}

verify_cmd() {
  if [[ -n "${CRED_ENV:-}" ]]; then
    (cd "$TREE" && bounded env -u "$CRED_ENV" "$@")
  else
    (cd "$TREE" && bounded "$@")
  fi
}

# ---- profile (restricted TOML: [[table]] arrays of key = value) -----------------

# Prints one line per key: table<TAB>index<TAB>key<TAB>value. Strings are
# unquoted; arrays and anything else are passed through raw.
profile_entries() {
  awk '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    function value(v,   out, i, c) {
      v = trim(v)
      if (substr(v, 1, 1) == "\"") {
        out = ""
        for (i = 2; i <= length(v); i++) {
          c = substr(v, i, 1)
          if (c == "\\") { i++; out = out substr(v, i, 1); continue }
          if (c == "\"") return out
          out = out c
        }
        return out
      }
      if (substr(v, 1, 1) == "\047") {
        v = substr(v, 2)
        return substr(v, 1, index(v, "\047") - 1)
      }
      sub(/[ \t]+#.*$/, "", v)
      return v
    }
    /^[ \t]*(#|$)/ { next }
    /^[ \t]*\[\[[A-Za-z_]+\]\]/ {
      t = $0; sub(/^[ \t]*\[\[/, "", t); sub(/\]\].*$/, "", t)
      n[t]++; cur = t; next
    }
    /^[ \t]*\[/ { cur = ""; next }
    cur != "" && index($0, "=") {
      k = trim(substr($0, 1, index($0, "=") - 1))
      printf "%s\t%d\t%s\t%s\n", cur, n[cur], k, value(substr($0, index($0, "=") + 1))
    }
  ' "$1"
}

# profile_get <table> <name> <key>: the key of the entry whose name matches.
profile_get() {
  awk -F'\t' -v t="$1" -v name="$2" -v key="$3" '
    $1 == t && $3 == "name" && $4 == name { hit[$2] = 1 }
    $1 == t { rows[NR] = $0; idx[NR] = $2; keys[NR] = $3; vals[NR] = $4 }
    END {
      for (i = 1; i <= NR; i++) if ((idx[i] in hit) && keys[i] == key) { print vals[i]; exit }
    }
  ' "$PROFILE_TSV"
}

# ---- qualify request ------------------------------------------------------------

# source_bytes <label> <file> : byte-counted framing, not a sentinel or a
# Markdown example. A file can contain any apparent delimiter without ending
# the source early; the reader consumes exactly the advertised byte count.
source_bytes() {
  printf 'Source file %s: the next %s bytes are its literal contents. Read exactly that many bytes.\n' "$1" "$(wc -c <"$2" | tr -d ' ')"
  cat "$2"
  printf '\nEnd of byte-counted source file %s.\n' "$1"
}

# ---- profile, setup fingerprint, qualification --------------------------------

default_profile() {
  printf '%s' "${AGENTIC_DELEGATE_PROFILE:-${XDG_CONFIG_HOME:-$HOME/.config}/agentic/local-models.toml}"
}

# load_profile <path> : parses the profile into $STATE/profile.tsv and sets
# QUAL_FILE, the machine-wide qualification record kept beside it.
load_profile() {
  [[ -f "$1" ]] || refuse "profile not found: $1"
  PROFILE_TSV="$STATE/profile.tsv"
  profile_entries "$1" >"$PROFILE_TSV"
  QUAL_FILE="$(dirname "$1")/local-models-qualified.jsonl"
}

profile_models() { awk -F'\t' '$1 == "model" && $3 == "name" { print $4 }' "$PROFILE_TSV"; }

# load_model <name> : the model's settings and the fingerprint of its setup.
load_model() {
  MODEL_NAME="$1"
  MODEL_ID="$(profile_get model "$MODEL_NAME" model_id)"
  [[ -n "$MODEL_ID" ]] || refuse "no [[model]] named '$MODEL_NAME' with a model_id in the profile"
  ENDPOINT_NAME="$(profile_get model "$MODEL_NAME" endpoint)"
  QUANT="$(profile_get model "$MODEL_NAME" quant)"
  RUNTIME="$(profile_get model "$MODEL_NAME" runtime)"
  REQUEST_JSON="$(profile_get model "$MODEL_NAME" request)"
  TIMEOUT="$(profile_get model "$MODEL_NAME" timeout_seconds)"
  TIMEOUT="${TIMEOUT:-900}"
  [[ "$TIMEOUT" =~ ^[0-9]+$ ]] || refuse "timeout_seconds must be a whole number of seconds"
  local request_norm='{}'
  if [[ -n "$REQUEST_JSON" ]]; then
    request_norm="$(jq -S -c . <<<"$REQUEST_JSON" 2>/dev/null)" && jq -e 'type == "object"' <<<"$REQUEST_JSON" >/dev/null 2>&1 ||
      refuse "model '$MODEL_NAME': request must be a JSON object in a literal string, e.g. request = '{\"temperature\": 0.6}'"
  fi
  [[ -n "$ENDPOINT_NAME" ]] || refuse "model '$MODEL_NAME' names no endpoint"
  BASE_URL="$(profile_get endpoint "$ENDPOINT_NAME" base_url)"
  BASE_URL="${BASE_URL%/}"
  [[ "$BASE_URL" =~ ^https?://[^[:space:]]+$ ]] || refuse "endpoint '$ENDPOINT_NAME' needs an http(s) base_url"
  CRED_ENV="$(profile_get endpoint "$ENDPOINT_NAME" credential_env)"
  # Any change to what the model receives or how it is served is a new setup.
  FINGERPRINT="$(printf '%s\n' "$HARNESS_VERSION" "$BASE_URL" "$MODEL_ID" "$QUANT" "$RUNTIME" "$request_norm" |
    "${SHA[@]}" | cut -c1-16)"
}

# Qualification covers HTTP plumbing; handoff scores also identify the Pi
# harness and the exact generated provider configuration, without any key.
handoff_fingerprint() {
  local config thinking
  config="$(handoff_model_config)" || return 1
  thinking="$(profile_get model "$MODEL_NAME" pi_thinking)"
  FINGERPRINT="$(printf '%s\n' "$FINGERPRINT" "$HANDOFF_HARNESS" "$HANDOFF_HARNESS_VERSION" "$thinking" "$config" |
    "${SHA[@]}" | cut -c1-16)"
}

handoff_model_config() {
  local context thinking alias
  context="$(profile_get model "$MODEL_NAME" context)"
  context="${context:-32768}"
  [[ "$context" =~ ^[1-9][0-9]*$ ]] || { say "model '$MODEL_NAME': context must be a positive integer"; return 1; }
  alias="$(profile_get model "$MODEL_NAME" pi_model)"
  [[ -z "$alias" || "$alias" == "$ENDPOINT_NAME/$MODEL_ID" ]] || {
    say "model '$MODEL_NAME': remove pi_model; handoff uses its endpoint and model_id directly"; return 1;
  }
  thinking="$(profile_get model "$MODEL_NAME" pi_thinking)"
  jq -S -c -n --arg provider "$ENDPOINT_NAME" --arg url "$BASE_URL" --arg id "$MODEL_ID" \
    --arg key "${CRED_ENV:+\$$CRED_ENV}" --arg thinking "$thinking" --argjson context "$context" \
    --argjson request "$(request_extra)" '
    {providers: {($provider): {baseUrl: $url, api: "openai-completions",
      apiKey: (if $key == "" then "agentic-local" else $key end),
      models: [{id: $id, input: ["text"], contextWindow: $context,
        reasoning: ($thinking != "" and $thinking != "off"),
        samplingParams: $request}]}}}'
}

load_handoff_harness() {
  HANDOFF_HARNESS="${DELEGATE_HANDOFF_HARNESS:-pi}"
  command -v "$HANDOFF_HARNESS" >/dev/null 2>&1 || return 1
  HANDOFF_HARNESS_VERSION="$("$HANDOFF_HARNESS" --version 2>/dev/null | head -n 1)"
  [[ -n "$HANDOFF_HARNESS_VERSION" ]]
}

check_credential() {
  [[ -n "${CRED_ENV:-}" ]] || return 0
  [[ "$CRED_ENV" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || refuse "credential_env must be an environment variable name"
  [[ -n "${!CRED_ENV:-}" ]] || refuse "credential_env '$CRED_ENV' is not set in this environment"
}

# qualification_state <model> <fingerprint> : qualified | unqualified, from
# the latest qualify result for exactly this setup.
qualification_state() {
  local result=""
  [[ -f "$QUAL_FILE" ]] &&
    result="$(jq -r -s --arg m "$1" --arg f "$2" \
      'map(select(.model == $m and .fingerprint == $f)) | last | .result // ""' "$QUAL_FILE" 2>/dev/null)"
  if [[ "$result" == pass ]]; then echo qualified; else echo unqualified; fi
}

# ---- scoring ------------------------------------------------------------------

# score <model> <fingerprint> <bucket> : prints
#   scored passed pending defects low high demoted median-seconds
# over this project's ledger, since the last reset for that model and bucket.
score() {
  local counts="0	0	0	0	0"
  if [[ -f "$DELEGATE_DIR/ledger.jsonl" ]]; then
    counts="$(jq -r -s --arg m "$1" --arg f "$2" --arg b "$3" --argjson w "$WINDOW" '
      . as $all
      | ([$all | to_entries[] | select(.value.type == "reset" and .value.model == $m
          and .value.bucket == $b) | .key] | max // -1) as $since
      | (reduce ($all[] | select(.type == "review")) as $r ({}; .[$r.run] = $r.verdict)) as $rev
      | [$all | to_entries[] | select(.key > $since) | .value
          | select((.type // "run") == "run" and .model.name == $m and .fingerprint == $f
              and .bucket == $b and .qualified == true)
          | if .outcome == "needs_frontier" then "fail"
            elif .outcome == "green" then
              ($rev[.run] // "pending")
              | if . == "pass" then "pass" elif . == "model-defect" then "defect"
                elif . == "pending" then "pending" else "unscored" end
            else "unscored" end] as $o
      | ([$o[] | select(. == "pass" or . == "fail" or . == "defect")] | .[-$w:]) as $win
      | [$all | to_entries[] | select(.key > $since) | .value
          | select((.type // "run") == "run" and .model.name == $m and .fingerprint == $f
              and .bucket == $b and .qualified == true and (.outcome == "green" or .outcome == "needs_frontier"))
          | ([.attempts[]?.seconds] | add // 0)] as $secs
      | ($secs | .[-$w:] | sort) as $ss
      | [($win | length), ([$win[] | select(. == "pass")] | length),
         ([$o[] | select(. == "pending")] | length), ([$o[] | select(. == "defect")] | length),
         (if ($ss | length) == 0 then 0 else $ss[(($ss | length) / 2 | floor)] end)]
      | @tsv' "$DELEGATE_DIR/ledger.jsonl")" || counts="0	0	0	0	0"
  fi
  awk -F'\t' -v z="$Z" -v min="$DEMOTE_MIN" -v below="$DEMOTE_BELOW" '{
    n = $1; s = $2; lo = 0; hi = 1
    if (n > 0) {
      p = s / n; d = 1 + z * z / n
      c = (p + z * z / (2 * n)) / d
      h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
      lo = c - h; hi = c + h
      if (lo < 0) lo = 0
      if (hi > 1) hi = 1
    }
    demoted = ($4 > 0 || (n >= min && hi < below)) ? "demoted" : "ok"
    printf "%d %d %d %d %.2f %.2f %s %d\n", n, s, $3, $4, lo, hi, demoted, $5
  }' <<<"$counts"
}

# describe_score <model> <fingerprint> <bucket> : one human-readable line.
describe_score() {
  local n s pending defects lo hi demoted _median
  read -r n s pending defects lo hi demoted _median <<<"$(score "$@")"
  local line="$3: $s/$n passed"
  ((n > 0)) && line="$line (95% range $(awk -v x="$lo" 'BEGIN { printf "%d", x * 100 }')–$(awk -v x="$hi" 'BEGIN { printf "%d", x * 100 }')%)"
  ((pending > 0)) && line="$line, $pending awaiting review"
  ((defects > 0)) && line="$line, $defects review defect(s)"
  [[ "$demoted" == demoted ]] && line="$line — DEMOTED (reset with: delegate-run.sh reset $1 $3)"
  printf '%s\n' "$line"
}

# ---- project entry, selection, discovery ------------------------------------

# project_get <key> : the key of the [[project]] entry whose root is this
# checkout (compared as physical paths).
project_get() {
  local here idx root
  here="$(cd "$ROOT" && pwd -P)"
  while IFS=$'\t' read -r idx root; do
    [[ -d "$root" ]] || continue
    if [[ "$(cd "$root" && pwd -P)" == "$here" ]]; then
      awk -F'\t' -v i="$idx" -v k="$1" '$1 == "project" && $2 == i && $3 == k { print $4; exit }' "$PROFILE_TSV"
      return 0
    fi
  done <<<"$(awk -F'\t' '$1 == "project" && $3 == "root" { print $2 "\t" $4 }' "$PROFILE_TSV")"
}

# toml_list "[\"a\", \"b\"]" : one item per line.
toml_list() { tr -d '[]"'"'"' ' <<<"$1" | tr ',' '\n' | awk 'NF'; }

# reachable : the current model's endpoint answers at all within 5 seconds.
reachable() {
  if [[ -n "${CRED_ENV:-}" ]]; then
    [[ -n "${!CRED_ENV:-}" ]] || return 1
    curl -sS -o /dev/null --connect-timeout 5 --max-time 5 \
      -H @<(printf 'Authorization: Bearer %s\n' "${!CRED_ENV}") "$BASE_URL/models" 2>/dev/null
  else
    curl -sS -o /dev/null --connect-timeout 5 --max-time 5 "$BASE_URL/models" 2>/dev/null
  fi
}

# select_model : explore candidates with fewer than EXPLORE_MIN scored runs in
# list order, then rank by the interval lower bound, median time, list order.
select_model() {
  local raw name row rows=""
  raw="$(project_get candidates)"
  [[ -n "$raw" ]] || refuse "no --model and no [[project]] entry with candidates for $ROOT in the profile"
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    # Evaluate each candidate in a subshell so a misconfigured one is skipped
    # with its reason instead of ending the run.
    row="$(
      refuse() { printf '%s\tbad\t-\t0\t0\t0\t0\t0\t1\tok\t0\t0\t%s\n' "$name" "$*"; exit 0; }
      load_model "$name"
      state="$(qualification_state "$name" "$FINGERPRINT")"
      handoff_fingerprint || refuse "invalid handoff model configuration"
      read -r n s pend def lo hi dem med <<<"$(score "$name" "$FINGERPRINT" "$BUCKET")"
      up=0
      [[ "$state" == qualified && "$dem" == ok ]] && reachable && up=1
      why=""
      [[ "$state" == qualified ]] || why="unqualified"
      [[ "$dem" == ok ]] || why="${why:+$why, }demoted"
      [[ "$state" == qualified && "$dem" == ok && "$up" == 0 ]] && why="unreachable"
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "$state" "$FINGERPRINT" "$n" "$s" "$pend" "$def" "$lo" "$hi" "$dem" "$med" "$up" "$why"
    )"
    rows="${rows}${row}"$'\n'
  done <<<"$(toml_list "$raw")"
  printf '%s' "$rows" >"$STATE/candidates.tsv"

  # columns: 1 name 2 state 3 fp 4 n 5 s 6 pend 7 def 8 lo 9 hi 10 dem 11 med 12 up 13 why
  local phase="" chosen="" runner_up="" ranked
  chosen="$(awk -F'\t' -v min="$EXPLORE_MIN" '$12 == 1 && $4 < min { print $1; exit }' "$STATE/candidates.tsv")"
  # Ranked order: lower bound high to low, then median time, then list order.
  ranked="$(awk -F'\t' '$12 == 1 { printf "%s\t%s\t%d\t%s\n", $8, $11, NR, $1 }' "$STATE/candidates.tsv" |
    sort -t$'\t' -k1,1gr -k2,2n -k3,3n | cut -f4)"
  if [[ -n "$chosen" ]]; then
    phase=explore
    runner_up="$(grep -v -x -F "$chosen" <<<"$ranked" | head -n 1 || true)"
  elif [[ -n "$ranked" ]]; then
    phase=rank
    chosen="$(head -n 1 <<<"$ranked")"
    runner_up="$(sed -n 2p <<<"$ranked")"
  else
    OUTCOME=needs_frontier
    REASON="no selectable candidate for $BUCKET tasks: $(awk -F'\t' '{ printf "%s%s (%s)", sep, $1, $13; sep = ", " }' "$STATE/candidates.tsv")"
    say "needs_frontier: $REASON"
    SELECTION_JSON="$(selection_json none "" "")"
    finish 10
  fi
  SELECTION_JSON="$(selection_json "$phase" "$chosen" "$runner_up")"
  load_model "$chosen"
  check_credential
  handoff_fingerprint || refuse "invalid handoff model configuration"
  QUALIFIED=qualified
  local line
  line="$(awk -F'\t' -v c="$chosen" '$1 == c { printf "%d scored runs, %d passed, lower bound %.2f", $4, $5, $8 }' "$STATE/candidates.tsv")"
  if [[ "$phase" == explore ]]; then
    say "selected '$chosen' to explore ($line; fewer than $EXPLORE_MIN scored)${runner_up:+; next: '$runner_up'}"
  else
    say "selected '$chosen' by record ($line)${runner_up:+; runner-up: '$runner_up'}"
  fi
}

selection_json() {
  jq -c -n --arg phase "$1" --arg chosen "$2" --arg runner_up "$3" --rawfile rows "$STATE/candidates.tsv" '
    {phase: $phase, chosen: $chosen, runner_up: $runner_up,
     considered: [$rows | split("\n")[] | select(length > 0) | split("\t")
       | {model: .[0], state: .[1], scored: (.[3] | tonumber), passed: (.[4] | tonumber),
          low: (.[7] | tonumber), high: (.[8] | tonumber), demoted: (.[9] == "demoted"),
          median_seconds: (.[10] | tonumber), selectable: (.[11] == "1"), why: .[12]}]}'
}

# ---- model call ---------------------------------------------------------------

request_extra() {
  if [[ -n "${REQUEST_JSON:-}" ]]; then printf '%s' "$REQUEST_JSON"; else printf '{}'; fi
}

# Only qualify's direct file-return calls use this contract. Pi handoffs use
# their own provider and normal agent messages, never this request builder.
RAW_FILE_SYSTEM='You are a raw-file transformation endpoint. Your entire response is written directly to the target file. Return only the literal final file contents. The first character of your response must be the first character of the file. Do not emit Markdown fences, backticks, a filename, commentary, explanations, diffs, patches, or status text.'

# build_request <out> <content-file> : fixed system contract, then the task.
build_request() {
  jq -n --arg model "$MODEL_ID" --arg system "$RAW_FILE_SYSTEM" --rawfile content "$2" --argjson extra "$(request_extra)" \
    '({model: $model, stream: false} + $extra) | .messages = [
       {role: "system", content: $system}, {role: "user", content: $content}]' >"$1" ||
    infra "could not build the request (is 'request' in the profile valid JSON?)"
}

# call_model <label> <request-file> : writes $STATE/<label>.response.json and
# sets CALL_SECONDS. Returns 2 when the server accepted the connection but the
# model did not answer within timeout_seconds (thinking counts): that is the
# model's setup being too slow, which qualify records as a failure. Every
# other transport failure exits through infra().
call_model() {
  local label="$1" req="$2"
  local resp="$STATE/$label.response.json" err="$STATE/$label.curl.err" meta="$STATE/$label.curl.meta"
  local out curl_status=0 code
  start_heartbeat "generating — $label"
  if [[ -n "${CRED_ENV:-}" ]]; then
    # The header comes through a file descriptor so the credential never
    # appears in a process argument list.
    curl -sS --connect-timeout 10 --max-time "$TIMEOUT" \
      -H 'Content-Type: application/json' \
      -H @<(printf 'Authorization: Bearer %s\n' "${!CRED_ENV}") \
      --data-binary @"$req" -o "$resp" -w '%{http_code} %{time_total} %{time_connect}' \
      "$BASE_URL/chat/completions" >"$meta" 2>"$err" || curl_status=$?
  else
    curl -sS --connect-timeout 10 --max-time "$TIMEOUT" \
      -H 'Content-Type: application/json' \
      --data-binary @"$req" -o "$resp" -w '%{http_code} %{time_total} %{time_connect}' \
      "$BASE_URL/chat/completions" >"$meta" 2>"$err" || curl_status=$?
  fi
  stop_progress_sidecar
  out="$(cat "$meta" 2>/dev/null)"
  local connect
  read -r code CALL_SECONDS connect <<<"$out"
  if [[ "$curl_status" == 28 ]] && awk -v c="${connect:-0}" 'BEGIN { exit !(c > 0) }'; then
    CALL_SECONDS="$TIMEOUT"
    return 2
  fi
  if [[ "$curl_status" != 0 ]]; then
    infra "endpoint '$ENDPOINT_NAME' unreachable (curl exit $curl_status: $(head -c 300 "$err"))"
  fi
  if [[ "$code" != 2* ]]; then
    infra "endpoint '$ENDPOINT_NAME' returned HTTP $code: $(head -c 300 "$resp" 2>/dev/null)"
  fi
  jq -e 'type == "object"' "$resp" >/dev/null 2>&1 ||
    infra "endpoint '$ENDPOINT_NAME' returned malformed JSON"
  progress "response received — ${CALL_SECONDS}s"
}

# extract_file <label> : writes the returned file to $STATE/returned; on a
# format problem prints the reason and returns 1.
extract_file() {
  local resp="$STATE/$1.response.json" cand="$STATE/returned" finish first
  finish="$(jq -r '.choices[0].finish_reason // "missing"' "$resp")"
  jq -j '.choices[0].message.content // "" | if type == "string" then . else "" end' "$resp" >"$cand"

  if [[ "$finish" == length ]]; then
    echo "The response was cut off (finish_reason=length). Return the complete file; it must fit in one response."
    return 1
  fi
  if [[ "$finish" != stop ]]; then
    echo "The response did not finish normally (finish_reason=$finish)."
    return 1
  fi

  # Reasoning that the server left inline is transport, not the file.
  first="$(grep -m1 -v '^[[:space:]]*$' "$cand" || true)"
  if [[ "$first" == "<think>"* ]]; then
    awk 'f { print; next } /<\/think>/ { f = 1; sub(/.*<\/think>/, ""); if (length($0)) print }' \
      "$cand" | awk 'NF || p { p = 1; print }' >"$cand.tmp" && mv "$cand.tmp" "$cand"
    first="$(grep -m1 -v '^[[:space:]]*$' "$cand" || true)"
  fi

  if [[ ! -s "$cand" || -z "$first" ]]; then
    echo "The response was empty. Return the complete contents of the file."
    return 1
  fi
  if [[ "$first" == '```'* ]]; then
    echo "The response was wrapped in a markdown code fence. Return the file contents only, with no fence."
    return 1
  fi
  # Files end with a newline; formatters would flag the omission otherwise.
  if [[ "$(tail -c1 "$cand" | wc -l | tr -d ' ')" == 0 ]]; then
    printf '\n' >>"$cand"
  fi
  return 0
}

# ---- result and ledger ----------------------------------------------------------

ATTEMPTS_JSON='[]'
RUN_STARTED="$(date +%s)"
GATE_LEVEL=""
OUTCOME=""
REASON=""
LOCKED=0
STATE=""
WORK=""

finish() {
  local status="$1" result_dir
  if [[ -n "${RUN_ID:-}" ]]; then
    result_dir="$DELEGATE_DIR/results/$RUN_ID"
    mkdir -p "$result_dir"
    # A handoff's attempts are its session segments.
    [[ -f "$result_dir/state.json" ]] &&
      ATTEMPTS_JSON="$(jq -c '[.segments[] | {attempt: (.segment | tonumber), outcome: (.end // "incomplete"),
        detail: (.reason // ""), seconds: (.seconds // 0)}]' "$result_dir/state.json" 2>/dev/null || echo '[]')"
    [[ -n "$STATE" && -f "$STATE/last-diagnostics.txt" ]] &&
      cp "$STATE/last-diagnostics.txt" "$result_dir/last-diagnostics.txt"
    # The packet and packet_sha256 keys predate handoff and now hold the spec;
    # they keep their names so existing ledgers stay readable.
    jq -n \
      --arg ts "$(now_utc)" --arg run "$RUN_ID" --arg packet "${SPEC_REL:-}" \
      --arg packet_sha "${SPEC_SHA:-}" --arg outcome "$OUTCOME" --arg reason "$REASON" \
      --arg harness "$HARNESS_VERSION" --arg model "${MODEL_NAME:-}" --arg model_id "${MODEL_ID:-}" \
      --arg quant "${QUANT:-}" --arg runtime "${RUNTIME:-}" --arg endpoint "${ENDPOINT_NAME:-}" \
      --arg request "${REQUEST_JSON:-}" --arg target "${TARGET:-}" --arg bucket "${BUCKET:-}" \
      --arg snapshot "${SNAPSHOT_SHA:-}" --argjson attempts "$ATTEMPTS_JSON" \
      --arg gate "${GATE_LEVEL:-}" \
      --arg fingerprint "${FINGERPRINT:-}" --argjson qualified "$([[ "${QUALIFIED:-}" == qualified ]] && echo true || echo false)" \
      --arg mode "${MODE:-manual}" --argjson selection "${SELECTION_JSON:-null}" \
      --argjson wall "$(($(date +%s) - RUN_STARTED))" \
      '{type: "run", ts: $ts, run: $run, mode: $mode, selection: $selection, fingerprint: $fingerprint,
        qualified: $qualified, packet: $packet,
        packet_sha256: $packet_sha, target: $target, bucket: $bucket,
        snapshot_sha256: $snapshot, harness: $harness,
        model: {name: $model, model_id: $model_id, quant: $quant, runtime: $runtime,
                endpoint: $endpoint, request: $request},
        attempts: $attempts, outcome: $outcome, reason: $reason,
        gate: $gate,
        wall_seconds: $wall,
        review: null}' \
      >"$result_dir/result.json"
    jq -c . "$result_dir/result.json" >>"$DELEGATE_DIR/ledger.jsonl"
    if [[ -f "$result_dir/state.json" ]]; then
      state_update '.state = $o | .reason = $r | .exit_status = $s | .finished_at = $now' \
        --arg o "$OUTCOME" --arg r "$REASON" --argjson s "$status"
    fi
    launch_report "$OUTCOME" "$REASON" "$status"
    print_summary "$result_dir/result.json"
    say "result: ${result_dir#"$ROOT"/}/result.json (run id: $RUN_ID)"
    if [[ "$OUTCOME" == green || "$OUTCOME" == needs_frontier ]]; then
      say "record for '$MODEL_NAME', $(describe_score "$MODEL_NAME" "$FINGERPRINT" "$BUCKET")"
    fi
    if [[ "$OUTCOME" == green ]]; then
      say "after /review: delegate-run.sh record-review $RUN_ID pass|model-defect|spec-defect"
    fi
  fi
  exit "$status"
}

# The end-of-run summary: model, wall time, and each session segment.
print_summary() {
  [[ -n "${MODEL_NAME:-}" ]] || return 0
  say "summary: $OUTCOME — '$MODEL_NAME' on $TARGET ($BUCKET), $(jq -r .wall_seconds "$1")s wall"
  jq -r '.attempts[] | "  segment \(.attempt): \(.outcome), \(.seconds)s\(if .detail != "" then " — " + .detail else "" end)"' "$1" >&2
}

cleanup() {
  stop_progress_sidecar
  [[ -n "$WORK" && "${DELEGATE_KEEP_SCRATCH:-0}" != 1 ]] && rm -rf "$WORK"
  if [[ "$LOCKED" == 1 ]]; then
    rm -f "$DELEGATE_DIR/lock/owner"
    rmdir "$DELEGATE_DIR/lock" 2>/dev/null
  fi
  return 0
}

# ---- qualify ------------------------------------------------------------------

# The plumbing file: about 270 lines, so a setup that truncates, runs out of
# context, or times out on a file of ordinary size fails here. The edit asked
# for is trivial, so grading can be exact.
PLUMBING_COUNT=30
PLUMBING_RENAME=17
PLUMBING_CONSTANT=23

plumbing_file() {
  local i
  printf '"""Plumbing fixture for delegate-run qualify. Generated; do not edit."""\n\n'
  for ((i = 1; i <= PLUMBING_COUNT; i++)); do printf 'SCALE_%02d = %d\n' "$i" "$i"; done
  for ((i = 1; i <= PLUMBING_COUNT; i++)); do
    printf '\n\ndef metric_%02d(values):\n    """Return the scaled total for metric %d."""\n' "$i" "$i"
    printf '    total = 0\n    for value in values:\n        total += value * SCALE_%02d\n    return total\n' "$i"
  done
}

plumbing_expected() {
  plumbing_file | sed \
    -e "s/^def metric_${PLUMBING_RENAME}(values):\$/def weighted_metric_${PLUMBING_RENAME}(values):/" \
    -e "s/^SCALE_${PLUMBING_CONSTANT} = ${PLUMBING_CONSTANT}\$/SCALE_${PLUMBING_CONSTANT} = ${PLUMBING_CONSTANT}0/"
}

plumbing_packet() {
  cat <<EOF
# Task

Return \`plumbing.py\` with exactly two changes and nothing else changed:

1. Rename the function \`metric_${PLUMBING_RENAME}\` to \`weighted_metric_${PLUMBING_RENAME}\`. Change only its \`def\` line.
2. Change the constant line \`SCALE_${PLUMBING_CONSTANT} = ${PLUMBING_CONSTANT}\` to \`SCALE_${PLUMBING_CONSTANT} = ${PLUMBING_CONSTANT}0\`.

Keep every other line exactly as it is, including blank lines and docstrings.

# The file as it exists now

EOF
  plumbing_file >"$STATE/plumbing-source.py"
  source_bytes plumbing.py "$STATE/plumbing-source.py"
  cat <<'EOF'

# Output contract

> Return the complete final contents of `plumbing.py`, and nothing else. No
> diff, no patch, no commentary, no markdown fence around the file, no
> explanation of your changes.
EOF
}

# Whitespace-insensitive at line ends and file edges only.
# Blank lines print only when a non-blank line follows, which drops them at
# both ends of the file.
normalize() {
  tr -d '\r' <"$1" | sed 's/[[:space:]]*$//' |
    awk 'NF { for (; k > 0; k--) print ""; print; seen = 1; next } seen { k++ }'
}

record_qualification() {
  mkdir -p "$(dirname "$QUAL_FILE")"
  jq -c -n --arg ts "$(now_utc)" --arg model "$MODEL_NAME" --arg fingerprint "$FINGERPRINT" \
    --arg result "$1" --arg reason "$2" --arg first "${3:-}" --arg plumbing "${4:-}" \
    --argjson timeout "$TIMEOUT" --arg harness "$HARNESS_VERSION" --arg model_id "$MODEL_ID" \
    --arg quant "$QUANT" --arg runtime "$RUNTIME" --arg endpoint "$ENDPOINT_NAME" \
    '{ts: $ts, model: $model, fingerprint: $fingerprint, result: $result, reason: $reason,
      first_response_seconds: $first, plumbing_seconds: $plumbing, timeout_seconds: $timeout,
      harness: $harness, model_id: $model_id, quant: $quant, runtime: $runtime, endpoint: $endpoint}' \
    >>"$QUAL_FILE"
}

cmd_qualify() {
  local name="" profile
  profile="$(default_profile)"
  while (($#)); do
    case "$1" in
      --profile)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--profile requires a value"; exit 2; }
        profile="$2"; shift 2 ;;
      -*) say "unknown option: $1"; exit 2 ;;
      *) name="$1"; shift ;;
    esac
  done
  [[ -n "$name" ]] || { say "usage: delegate-run.sh qualify <model>"; exit 2; }
  require_deps curl jq awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/delegate-qualify.XXXXXX")" || exit 3
  STATE="$WORK/state"
  mkdir -p "$STATE"
  trap cleanup EXIT
  load_profile "$profile"
  load_model "$name"
  check_credential

  # Is the model listed? A warning only: not every server lists every model.
  local listing="$STATE/models.json" listed=0
  if [[ -n "${CRED_ENV:-}" ]]; then
    curl -sS --connect-timeout 10 --max-time 30 -o "$listing" \
      -H @<(printf 'Authorization: Bearer %s\n' "${!CRED_ENV}") \
      "$BASE_URL/models" 2>/dev/null && listed=1
  else
    curl -sS --connect-timeout 10 --max-time 30 -o "$listing" "$BASE_URL/models" 2>/dev/null && listed=1
  fi
  if [[ "$listed" == 1 ]] && jq -e '.data | type == "array"' "$listing" >/dev/null 2>&1; then
    jq -e --arg id "$MODEL_ID" 'any(.data[]; .id == $id)' "$listing" >/dev/null ||
      say "warning: '$ENDPOINT_NAME' does not list model_id '$MODEL_ID' in /models"
  fi

  say "qualifying '$MODEL_NAME' (setup $FINGERPRINT): first response, then a $(plumbing_file | wc -l | tr -d ' ')-line plumbing file"
  printf 'Reply with the single word OK.\n' >"$STATE/warmup.md"
  build_request "$STATE/warmup.request.json" "$STATE/warmup.md"
  if ! call_model warmup "$STATE/warmup.request.json"; then
    record_qualification fail "first response timed out after ${TIMEOUT}s (includes any model load)" "$TIMEOUT" ""
    say "not qualified: no first response within timeout_seconds=$TIMEOUT; raise it if the model needs longer to load"
    exit 5
  fi
  local first="$CALL_SECONDS"

  plumbing_packet >"$STATE/plumbing.md"
  build_request "$STATE/plumbing.request.json" "$STATE/plumbing.md"
  if ! call_model plumbing "$STATE/plumbing.request.json"; then
    record_qualification fail "plumbing file timed out after ${TIMEOUT}s" "$first" "$TIMEOUT"
    say "not qualified: the plumbing file did not come back within timeout_seconds=$TIMEOUT; raise it or cap the model's reasoning"
    exit 5
  fi
  local gen="$CALL_SECONDS" reason
  if ! reason="$(extract_file plumbing)"; then
    record_qualification fail "$reason" "$first" "$gen"
    say "not qualified: $reason"
    exit 5
  fi
  plumbing_expected >"$STATE/expected.py"
  normalize "$STATE/expected.py" >"$STATE/expected.norm"
  normalize "$STATE/returned" >"$STATE/returned.norm"
  if ! cmp -s "$STATE/expected.norm" "$STATE/returned.norm"; then
    local where
    where="$(diff "$STATE/expected.norm" "$STATE/returned.norm" | head -n 6 | tr '\n' ' ')"
    record_qualification fail "returned file differs from the expected edit: $where" "$first" "$gen"
    say "not qualified: the returned file differs from the expected edit"
    say "  first differences: $where"
    exit 5
  fi
  record_qualification pass "" "$first" "$gen"
  say "qualified: '$MODEL_NAME' — first response ${first}s (includes any model load), plumbing ${gen}s, timeout ${TIMEOUT}s"
  if awk -v a="$first" -v b="$gen" -v t="$TIMEOUT" 'BEGIN { exit !(a + b > 0.6 * t) }'; then
    say "warning: a cold load plus the plumbing file took over 60% of timeout_seconds; raise it for this model"
  fi
}

# ---- record-review, status, reset -------------------------------------------------

cmd_record_review() {
  local run="${1:-}" verdict="${2:-}"
  [[ -n "$run" && -n "$verdict" ]] || { say "usage: delegate-run.sh record-review <run> pass|model-defect|spec-defect"; exit 2; }
  case "$verdict" in
    pass | model-defect | spec-defect) ;;
    packet-defect) verdict="spec-defect" ;;
    *) say "unknown verdict: $verdict"; exit 2 ;;
  esac
  require_deps jq awk
  local ledger="$DELEGATE_DIR/ledger.jsonl" rec
  [[ -f "$ledger" ]] || { say "no ledger at $ledger"; exit 2; }
  rec="$(jq -c -s --arg r "$run" 'map(select((.type // "run") == "run" and .run == $r)) | last // empty' "$ledger")"
  [[ -n "$rec" ]] || { say "no run '$run' in the ledger"; exit 2; }
  # Older ledgers may hold runs from the removed per-file mode (buckets
  # small and medium); they stay as history and are no longer scored.
  [[ "$(jq -r '.bucket // ""' <<<"$rec")" == phase:* ]] || { say "run '$run' is from a removed delegation mode and is no longer scored; nothing to record"; exit 2; }
  [[ "$(jq -r .outcome <<<"$rec")" == green ]] || { say "run '$run' did not end green; nothing to review"; exit 2; }
  jq -c -n --arg ts "$(now_utc)" --arg run "$run" --arg verdict "$verdict" \
    '{type: "review", ts: $ts, run: $run, verdict: $verdict}' >>"$ledger"
  local model fingerprint bucket
  model="$(jq -r .model.name <<<"$rec")"
  fingerprint="$(jq -r .fingerprint <<<"$rec")"
  bucket="$(jq -r .bucket <<<"$rec")"
  say "recorded $verdict for $run"
  say "record for '$model', $(describe_score "$model" "$fingerprint" "$bucket")"
}

cmd_status() {
  local profile name state bucket has_harness=0
  profile="$(default_profile)"
  if [[ "${1:-}" == --profile ]]; then
    (($# >= 2)) && [[ -n "$2" ]] || { say "--profile requires a value"; exit 2; }
    profile="$2"; shift 2
  fi
  (($# == 0)) || { say "usage: delegate-run.sh status [--profile <path>]"; exit 2; }
  require_deps jq awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/delegate-status.XXXXXX")" || exit 3
  STATE="$WORK/state"
  mkdir -p "$STATE"
  trap cleanup EXIT
  load_profile "$profile"
  load_handoff_harness && has_harness=1
  [[ "$has_harness" == 1 ]] ||
    say "agent harness unavailable (${DELEGATE_HANDOFF_HARNESS:-pi}): showing qualification only; phase records need its version"
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    load_model "$name"
    state="$(qualification_state "$name" "$FINGERPRINT")"
    printf '%s (%s, setup %s): %s\n' "$name" "$MODEL_ID" "$FINGERPRINT" "$state"
    if [[ "$has_harness" == 1 ]] && handoff_fingerprint; then
      for bucket in phase:implement phase:test-first+implement; do
        printf '  %s\n' "$(describe_score "$name" "$FINGERPRINT" "$bucket")"
      done
    fi
  done <<<"$(profile_models)"
}

cmd_init() {
  local profile key_env="" url urls=() keys=()
  profile="$(default_profile)"
  while (($#)); do
    case "$1" in
      --key-env)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--key-env requires a value"; exit 2; }
        key_env="$2"; shift 2 ;;
      --profile)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--profile requires a value"; exit 2; }
        profile="$2"; shift 2 ;;
      -*) say "unknown option: $1"; exit 2 ;;
      *)
        [[ "$1" =~ ^https?://[^[:space:]]+$ ]] || { say "not an http(s) base URL: $1"; exit 2; }
        if [[ -n "$key_env" && ! "$key_env" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
          say "--key-env takes a variable name, never a key: $key_env"
          exit 2
        fi
        urls+=("${1%/}"); keys+=("$key_env"); key_env=""; shift ;;
    esac
  done
  ((${#urls[@]})) || { say "usage: delegate-run.sh init [--key-env VAR] <base-url> ..."; exit 2; }
  require_deps curl jq awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/delegate-init.XXXXXX")" || exit 3
  STATE="$WORK/state"
  mkdir -p "$STATE"
  trap cleanup EXIT
  PROFILE_TSV="$STATE/profile.tsv"
  : >"$PROFILE_TSV"
  [[ -f "$profile" ]] && profile_entries "$profile" >"$PROFILE_TSV"

  local i ep listing id name ctx models=() existing
  printf '# Draft for %s: review, edit, then save it yourself.\n' "$profile"
  printf '# Confirm quant and runtime (servers do not report them), delete models you\n'
  printf '# will not use, list your choices in candidates, then run: delegate-run.sh qualify <name>\n'
  for ((i = 0; i < ${#urls[@]}; i++)); do
    url="${urls[$i]}"
    ep="$(awk -F'\t' -v u="$url" '$1 == "endpoint" && $3 == "base_url" && $4 == u { print $2; exit }' "$PROFILE_TSV")"
    if [[ -n "$ep" ]]; then
      ep="$(awk -F'\t' -v i="$ep" '$1 == "endpoint" && $2 == i && $3 == "name" { print $4; exit }' "$PROFILE_TSV")"
      printf '\n# endpoint %s (%s) is already in the profile\n' "$ep" "$url"
    else
      ep="$(sed -E 's#^https?://##; s#/.*$##; s#[^A-Za-z0-9]+#-#g; s#-+$##' <<<"$url")"
      printf '\n[[endpoint]]\nname = "%s"\nbase_url = "%s"\n' "$ep" "$url"
      [[ -n "${keys[$i]}" ]] && printf 'credential_env = "%s"\n' "${keys[$i]}"
    fi
    listing="$STATE/listing-$i.json"
    CRED_ENV="${keys[$i]}"
    if [[ -n "$CRED_ENV" && -n "${!CRED_ENV:-}" ]]; then
      curl -sS --connect-timeout 5 --max-time 15 -o "$listing" \
        -H @<(printf 'Authorization: Bearer %s\n' "${!CRED_ENV}") "$url/models" 2>/dev/null
    else
      curl -sS --connect-timeout 5 --max-time 15 -o "$listing" "$url/models" 2>/dev/null
    fi
    if ! jq -e '.data | type == "array"' "$listing" >/dev/null 2>&1; then
      printf '# %s: no model listing (unreachable, or set %s)\n' "$url" "${CRED_ENV:-a key variable with --key-env}"
      continue
    fi
    while IFS=$'\t' read -r id ctx; do
      [[ -n "$id" ]] || continue
      existing="$(awk -F'\t' -v e="$ep" -v m="$id" '
        $1 == "model" && $3 == "endpoint" && $4 == e { ep[$2] = 1 }
        $1 == "model" && $3 == "model_id" && $4 == m { mid[$2] = 1 }
        END { for (k in ep) if (k in mid) { print "yes"; exit } }' "$PROFILE_TSV")"
      if [[ "$existing" == yes ]]; then
        printf '# model %s on %s is already in the profile\n' "$id" "$ep"
        continue
      fi
      name="$(sed -E 's#[^A-Za-z0-9]+#-#g; s#^-+|-+$##g' <<<"$id" | tr 'A-Z' 'a-z')"
      models+=("$name")
      printf '\n[[model]]\nname = "%s"\nendpoint = "%s"\nmodel_id = "%s"\n' "$name" "$ep" "$id"
      printf 'quant = "CONFIRM"\nruntime = "CONFIRM"\n'
      [[ "$ctx" =~ ^[0-9]+$ && "$ctx" -gt 0 ]] && printf 'context = %s\n' "$ctx"
      printf 'timeout_seconds = 3600   # one call: swap, load, thinking, reply\n'
      printf "request = '{\"max_tokens\": 65536}'   # pin sampling and reasoning here\n"
    done <<<"$(jq -r '.data[] | [.id, ((.meta.n_ctx // .meta.n_ctx_train // .context_length // "") | tostring)] | @tsv' "$listing")"
  done

  local here
  here="$(cd "$ROOT" && pwd -P)"
  if [[ -n "$(project_get root 2>/dev/null)" ]]; then
    printf '\n# [[project]] for %s is already in the profile\n' "$here"
  else
    printf '\n[[project]]\nroot = "%s"\npolicy = "off"   # "auto" with activation_approved = true lets Implement delegate\n' "$here"
    printf 'candidates = []   # e.g. [%s]\n' "$( ((${#models[@]})) && printf '"%s", ' "${models[@]:0:2}" | sed 's/, $//')"
  fi
}

cmd_discover() {
  local profile endpoint id name listing found candidates
  profile="$(default_profile)"
  if [[ "${1:-}" == --profile ]]; then
    (($# >= 2)) && [[ -n "$2" ]] || { say "--profile requires a value"; exit 2; }
    profile="$2"; shift 2
  fi
  (($# == 0)) || { say "usage: delegate-run.sh discover [--profile <path>]"; exit 2; }
  require_deps curl jq awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/delegate-discover.XXXXXX")" || exit 3
  STATE="$WORK/state"
  mkdir -p "$STATE"
  trap cleanup EXIT
  load_profile "$profile"
  candidates="$(toml_list "$(project_get candidates)")"
  while IFS= read -r endpoint; do
    [[ -n "$endpoint" ]] || continue
    BASE_URL="$(profile_get endpoint "$endpoint" base_url)"
    BASE_URL="${BASE_URL%/}"
    CRED_ENV="$(profile_get endpoint "$endpoint" credential_env)"
    listing="$STATE/$endpoint.json"
    found=0
    if [[ -n "$CRED_ENV" && -n "${!CRED_ENV:-}" ]]; then
      curl -sS --connect-timeout 5 --max-time 15 -o "$listing" \
        -H @<(printf 'Authorization: Bearer %s\n' "${!CRED_ENV}") "$BASE_URL/models" 2>/dev/null && found=1
    elif [[ -z "$CRED_ENV" ]]; then
      curl -sS --connect-timeout 5 --max-time 15 -o "$listing" "$BASE_URL/models" 2>/dev/null && found=1
    fi
    if [[ "$found" != 1 ]] || ! jq -e '.data | type == "array"' "$listing" >/dev/null 2>&1; then
      printf '%s: unreachable or no model listing%s\n' "$endpoint" "${CRED_ENV:+ (credential_env $CRED_ENV)}"
      continue
    fi
    printf '%s:\n' "$endpoint"
    while IFS= read -r id; do
      [[ -n "$id" ]] || continue
      local matched=0
      while IFS= read -r name; do
        [[ -n "$name" ]] || continue
        [[ "$(profile_get model "$name" endpoint)" == "$endpoint" &&
          "$(profile_get model "$name" model_id)" == "$id" ]] || continue
        matched=1
        (
          refuse() { printf '  %s -> %s: misconfigured (%s)\n' "$id" "$name" "$*"; exit 0; }
          load_model "$name"
          printf '  %s -> %s: %s%s\n' "$id" "$name" "$(qualification_state "$name" "$FINGERPRINT")" \
            "$(grep -q -x -F "$name" <<<"$candidates" && echo ", project candidate")"
        )
      done <<<"$(profile_models)"
      [[ "$matched" == 1 ]] || printf '  %s: available, not in the profile\n' "$id"
    done <<<"$(jq -r '.data[].id' "$listing")"
    while IFS= read -r name; do
      [[ -n "$name" && "$(profile_get model "$name" endpoint)" == "$endpoint" ]] || continue
      id="$(profile_get model "$name" model_id)"
      jq -e --arg id "$id" 'any(.data[]; .id == $id)' "$listing" >/dev/null ||
        printf '  %s -> %s: configured but not listed by the server\n' "$id" "$name"
    done <<<"$(profile_models)"
  done <<<"$(awk -F'\t' '$1 == "endpoint" && $3 == "name" { print $4 }' "$PROFILE_TSV")"
}

cmd_policy() {
  local profile policy approved name qualified=""
  profile="$(default_profile)"
  if [[ "${1:-}" == --profile ]]; then
    (($# >= 2)) && [[ -n "$2" ]] || { say "--profile requires a value"; exit 2; }
    profile="$2"; shift 2
  fi
  (($# == 0)) || { say "usage: delegate-run.sh policy [--profile <path>]"; exit 2; }
  # No profile means delegation is off; say so without needing jq.
  [[ -f "$profile" ]] || { echo "off: no profile at $profile"; exit 1; }
  require_deps jq awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/delegate-policy.XXXXXX")" || exit 3
  STATE="$WORK/state"
  mkdir -p "$STATE"
  trap cleanup EXIT
  load_profile "$profile"
  policy="$(project_get policy)"
  approved="$(project_get activation_approved)"
  [[ -n "$policy" ]] || { echo "off: no [[project]] entry for this checkout"; exit 1; }
  [[ "$policy" == auto ]] || { echo "off: policy is '$policy'"; exit 1; }
  [[ "$approved" == true ]] || { echo "off: activation_approved is not true"; exit 1; }
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    if (
      refuse() { exit 1; }
      load_model "$name"
      [[ "$(qualification_state "$name" "$FINGERPRINT")" == qualified ]]
    ); then
      qualified="${qualified:+$qualified, }$name"
    fi
  done <<<"$(toml_list "$(project_get candidates)")"
  [[ -n "$qualified" ]] || { echo "off: no candidate is qualified for its current setup"; exit 1; }
  echo "auto (qualified candidates: $qualified)"
}

# project_index : the [[project]] occurrence number for this checkout, or empty.
project_index() {
  local here idx root
  here="$(cd "$ROOT" && pwd -P)"
  while IFS=$'\t' read -r idx root; do
    [[ -n "$root" && -d "$root" ]] || continue
    [[ "$(cd "$root" && pwd -P)" == "$here" ]] && { printf '%s\n' "$idx"; return 0; }
  done <<<"$(awk -F'\t' '$1 == "project" && $3 == "root" { print $2 "\t" $4 }' "$PROFILE_TSV")"
}

# activate | deactivate : set this checkout's policy in the profile. Run only
# on the human's explicit yes; activate refuses without a qualified candidate.
cmd_set_policy() {
  local want="$1" profile idx name qualified=""
  shift
  profile="$(default_profile)"
  if [[ "${1:-}" == --profile ]]; then
    (($# >= 2)) && [[ -n "$2" ]] || { say "--profile requires a value"; exit 2; }
    profile="$2"; shift 2
  fi
  (($# == 0)) || { say "usage: delegate-run.sh activate|deactivate [--profile <path>]"; exit 2; }
  require_deps jq awk
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/delegate-policy.XXXXXX")" || exit 3
  STATE="$WORK/state"
  mkdir -p "$STATE"
  trap cleanup EXIT
  load_profile "$profile"
  idx="$(project_index)"
  [[ -n "$idx" ]] || refuse "no [[project]] entry for $ROOT in $profile; add one (delegate-run.sh init prints a draft)"
  local policy approved
  if [[ "$want" == auto ]]; then
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      if (
        refuse() { exit 1; }
        load_model "$name"
        [[ "$(qualification_state "$name" "$FINGERPRINT")" == qualified ]]
      ); then
        qualified="${qualified:+$qualified, }$name"
      fi
    done <<<"$(toml_list "$(project_get candidates)")"
    [[ -n "$qualified" ]] || refuse "no candidate in this checkout's [[project]] entry is qualified; run qualify first"
    policy='"auto"'; approved=true
  else
    policy='"off"'; approved=false
  fi
  say "before: policy = $(project_get policy), activation_approved = $(project_get activation_approved)"
  local tmp="$profile.delegate-run.$$"
  awk -v idx="$idx" -v policy="$policy" -v approved="$approved" '
    function close_block() {
      if (inblock) {
        if (!seen_p) print "policy = " policy
        if (!seen_a) print "activation_approved = " approved
      }
      inblock = 0
    }
    /^[ \t]*\[\[project\]\]/ { close_block(); n++; if (n == idx) { inblock = 1; seen_p = 0; seen_a = 0 } print; next }
    /^[ \t]*\[/ { close_block(); print; next }
    inblock && /^[ \t]*policy[ \t]*=/ { print "policy = " policy; seen_p = 1; next }
    inblock && /^[ \t]*activation_approved[ \t]*=/ { print "activation_approved = " approved; seen_a = 1; next }
    { print }
    END { close_block() }
  ' "$profile" >"$tmp" || { rm -f "$tmp"; infra "could not stage updated profile"; }
  # A failed stat may still write stdout (GNU stat -f does). Keep only the
  # successful invocation's output before handing permissions to chmod.
  local permissions
  if ! permissions="$(stat -c %a "$profile" 2>/dev/null)"; then
    permissions="$(stat -f %Lp "$profile" 2>/dev/null)" || { rm -f "$tmp"; infra "could not read profile permissions"; }
  fi
  chmod "$permissions" "$tmp" && mv -f "$tmp" "$profile" ||
    { rm -f "$tmp"; infra "could not rewrite $profile"; }
  load_profile "$profile"
  say "after:  policy = $(project_get policy), activation_approved = $(project_get activation_approved)"
  if [[ "$want" == auto ]]; then
    say "qualified candidates: $qualified. Each run still picks its delegation mode (direct or handoff)."
  fi
}

cmd_reset() {
  local model="${1:-}" bucket="${2:-}"
  [[ -n "$model" && -n "$bucket" ]] || { say "usage: delegate-run.sh reset <model> <bucket>"; exit 2; }
  case "$bucket" in phase:implement | phase:test-first+implement) ;; *) say "unknown bucket: $bucket (phase:implement or phase:test-first+implement)"; exit 2 ;; esac
  require_deps jq
  mkdir -p "$DELEGATE_DIR"
  jq -c -n --arg ts "$(now_utc)" --arg model "$model" --arg bucket "$bucket" \
    '{type: "reset", ts: $ts, model: $model, bucket: $bucket}' >>"$DELEGATE_DIR/ledger.jsonl"
  say "reset '$model' for $bucket tasks; its record starts fresh"
}

# ---- handoff: Pi RPC session control --------------------------------------------
# One segment is one Pi process in RPC mode. The runner owns the only writer:
#   runner fd 8 --FIFO--> Perl supervisor --pipe--> Pi stdin
#   Pi stdout --> segments/NNNN/events.jsonl (a regular file: Pi never blocks)
#   Pi stderr --> segments/NNNN/harness.log
# Pi never reads the FIFO itself: Node misses FIFO EOF on macOS, and Codex's
# macOS sandbox denies the /dev/fd paths Bash 3.2 process substitution needs.
# The supervisor relays into a real pipe, closing it at FIFO EOF; puts Pi in
# its own process group; records its pid; and writes its exit status
# atomically (Bash 3.2 has no non-blocking wait, and kill -0 is true for a
# zombie). Closing Pi's stdin during a run abandons the run, so the runner
# closes it only after agent_settled or a completed abort. Never call rpc_*
# inside $(...): the subshell would consume events from fd 9 and lose the
# state it parsed. Design: docs/resumable-handoff.md in agentic-scaffold.

CHECKPOINT_MESSAGE='Checkpoint requested by the handoff supervisor. Do not start new implementation work. Finish only the operation in progress, inspect the worktree, append a concise "### Paused handoff" entry under "## Phase handoff" in the spec (what is done, what is next, anything left red), do not commit, and stop.'

# descendant_groups <pid> : process groups of <pid>'s descendants other than
# its own (Pi runs each tool command in one). Prints nothing where ps is
# unavailable, as inside Codex's macOS sandbox.
descendant_groups() {
  ps -axo pid=,ppid=,pgid= 2>/dev/null | awk -v root="$1" '
    { kids[$2] = kids[$2] " " $1; pg[$1] = $3 }
    END {
      q[1] = root; head = 1; tail = 1
      while (head <= tail) {
        p = q[head++]
        if (seen[p]++) continue
        if (p != root && pg[p] != "" && pg[p] != root) out[pg[p]] = 1
        m = split(kids[p], k, " ")
        for (i = 1; i <= m; i++) q[++tail] = k[i]
      }
      for (g in out) print g
    }'
}

rpc_start() {
  local cwd="$1"
  shift
  RPC_PENDING="" RPC_SETTLED=0 RPC_SETTLED_ABORTED=false RPC_SEQ=0 RPC_PID=""
  RPC_LAST_ACTIVITY="$(date +%s)"
  RPC_LAST_EVENT="" RPC_LAST_EVENT_AT="$RPC_LAST_ACTIVITY" RPC_ACTIVE_TOOL="" RPC_ACTIVE_TOOL_AT=0
  RPC_RETRIES=0 RPC_COMPACTIONS=0 RPC_RECOVERING=0 RPC_DIALOGS=0
  RPC_TOOL_ENDED=0 RPC_COMPACTED=0
  mkdir -p "$SEG_DIR/responses" || return 1
  : >"$SEG_DIR/events.jsonl"
  : >"$SEG_DIR/harness.log"
  rm -f "$SEG_DIR/exit.status" "$SEG_DIR/pi.pid" "$SEG_DIR/cmd.fifo"
  mkfifo "$SEG_DIR/cmd.fifo" || return 1
  # O_RDWR: neither side's open blocks, even if the supervisor dies first.
  exec 8<>"$SEG_DIR/cmd.fifo"
  (cd "$cwd" && exec perl -MPOSIX=:sys_wait_h -e '
      my ($status, $pidfile, $fifo) = splice @ARGV, 0, 3;
      open my $in, "<", $fifo or die "fifo: $!";
      pipe(my $r, my $w) or die "pipe: $!";
      my $pid = fork() // die "fork: $!";
      if ($pid == 0) {
        close $w; close $in; open STDIN, "<&", $r or exit 127; close $r;
        setpgrp(0, 0); exec { $ARGV[0] } @ARGV or exit 127;
      }
      close $r;
      open my $f, ">", "$pidfile.tmp" or die; print $f "$pid\n"; close $f; rename "$pidfile.tmp", $pidfile;
      $SIG{TERM} = sub { kill "TERM", -$pid };
      $SIG{PIPE} = "IGNORE";
      my ($done, $rc, $buf, $rin) = (0);
      my $exit = sub { $? & 127 ? 128 + ($? & 127) : $? >> 8 };
      while (!$done) {
        vec($rin = "", fileno($in), 1) = 1;
        if (select(my $rout = $rin, undef, undef, 0.5) > 0) {
          my $n = sysread $in, $buf, 65536;
          if (!defined $n) { next if $!{EINTR}; last }
          last if $n == 0;
          syswrite $w, $buf;
        }
        if (waitpid($pid, WNOHANG) == $pid) { $done = 1; $rc = $exit->() }
      }
      close $w;
      unless ($done) { 1 while waitpid($pid, 0) == -1 && $!{EINTR}; $rc = $exit->() }
      open $f, ">", "$status.tmp" or die; print $f "$rc\n"; close $f; rename "$status.tmp", $status;
      exit $rc;
    ' "$SEG_DIR/exit.status" "$SEG_DIR/pi.pid" "$SEG_DIR/cmd.fifo" "$@") \
    <&- >"$SEG_DIR/events.jsonl" 2>"$SEG_DIR/harness.log" 8>&- 9>&- &
  exec 9<"$SEG_DIR/events.jsonl"
  while [[ ! -s "$SEG_DIR/pi.pid" && ! -e "$SEG_DIR/exit.status" ]]; do sleep 0.05; done
  RPC_PID="$(cat "$SEG_DIR/pi.pid" 2>/dev/null)"
  [[ -n "$RPC_PID" ]]
}

rpc_alive() { [[ -n "${SEG_DIR:-}" && ! -e "$SEG_DIR/exit.status" ]]; }

# rpc_send <json object> : adds an id, sets RPC_ID.
rpc_send() {
  local rec
  RPC_SEQ=$((RPC_SEQ + 1))
  RPC_ID="c$RPC_SEQ"
  rec="$(jq -c --arg id "$RPC_ID" '. + {id: $id}' <<<"$1")" || return 1
  printf '%s\n' "$rec" >&8 2>/dev/null
}

# Consume every complete record appended since the last call. Token deltas
# and tool updates dominate the stream; they are routed by prefix without jq.
rpc_pump() {
  local line type now id method
  now="$(date +%s)"
  while :; do
    line=""
    if ! IFS= read -r line <&9; then
      # read advances past a partial final line; keep it for the next call.
      RPC_PENDING+="$line"
      return 0
    fi
    line="$RPC_PENDING$line"
    RPC_PENDING=""
    RPC_LAST_EVENT_AT="$now"
    case "$line" in
      '{"type":"message_update"'*) RPC_LAST_ACTIVITY="$now"; RPC_RECOVERING=0; continue ;;
      '{"type":"tool_execution_update"'*) RPC_LAST_ACTIVITY="$now"; continue ;;
    esac
    type="$(jq -r '.type // ""' <<<"$line" 2>/dev/null)" || type=""
    [[ -n "$type" ]] || continue
    RPC_LAST_EVENT="$type"
    case "$type" in
      response)
        id="$(jq -r '.id // "noid"' <<<"$line")"
        [[ "$id" =~ ^[A-Za-z0-9_-]+$ ]] && printf '%s\n' "$line" >"$SEG_DIR/responses/$id.json"
        continue
        ;;
      agent_start) RPC_SETTLED=0; RPC_LAST_ACTIVITY="$now" ;;
      turn_start | turn_end | message_start | message_end) RPC_LAST_ACTIVITY="$now"; RPC_RECOVERING=0 ;;
      tool_execution_start)
        RPC_LAST_ACTIVITY="$now"
        RPC_ACTIVE_TOOL="$(jq -r '.toolName // "tool"' <<<"$line")"
        RPC_ACTIVE_TOOL_AT="$now"
        ;;
      tool_execution_end) RPC_LAST_ACTIVITY="$now"; RPC_ACTIVE_TOOL=""; RPC_TOOL_ENDED=1 ;;
      auto_retry_start) RPC_RETRIES=$((RPC_RETRIES + 1)); RPC_RECOVERING=1 ;;
      compaction_start) RPC_COMPACTIONS=$((RPC_COMPACTIONS + 1)); RPC_RECOVERING=1 ;;
      compaction_end)
        RPC_RECOVERING=0
        [[ "$(jq -r '.aborted // false' <<<"$line")" == true ]] || RPC_COMPACTED=1
        ;;
      agent_settled)
        RPC_SETTLED=1
        RPC_SETTLED_ABORTED="$(jq -r 'if .aborted == true then "true" else "false" end' <<<"$line")"
        ;;
      extension_ui_request)
        # Dialogs block Pi until answered, and no one is watching a handoff.
        method="$(jq -r '.method // ""' <<<"$line")"
        case "$method" in
          select | confirm | input | editor)
            id="$(jq -r '.id // ""' <<<"$line")"
            jq -c -n --arg id "$id" '{type: "extension_ui_response", id: $id, cancelled: true}' >&8 2>/dev/null
            RPC_DIALOGS=$((RPC_DIALOGS + 1))
            printf '%s cancelled %s dialog\n' "$(now_utc)" "$method" >>"$SEG_DIR/dialogs.log"
            progress "cancelled an extension $method dialog (no one is watching)"
            ;;
        esac
        ;;
    esac
    [[ "$PROGRESS_MODE" == summary ]] && render_pi_event "$line"
  done
}

# rpc_call <json> <seconds> : sets RPC_REPLY; 3 if Pi exited, 124 on timeout.
rpc_call() {
  local deadline
  RPC_REPLY=""
  rpc_send "$1" || return 3
  deadline=$(($(date +%s) + $2))
  while :; do
    rpc_pump
    if [[ -f "$SEG_DIR/responses/$RPC_ID.json" ]]; then
      RPC_REPLY="$(cat "$SEG_DIR/responses/$RPC_ID.json")"
      return 0
    fi
    rpc_alive || return 3
    (($(date +%s) < deadline)) || return 124
    sleep 0.1
  done
}

rpc_ok() { [[ "$(jq -r '.success // false' <<<"$RPC_REPLY" 2>/dev/null)" == true ]]; }

# rpc_stop <seconds> : close Pi's stdin and wait for it to exit; then TERM
# its group, and KILL that group and every descendant group seen. Sets
# RPC_EXIT and RPC_SIGNALLED.
rpc_stop() {
  local deadline groups g
  RPC_SIGNALLED=0
  exec 8>&-
  deadline=$(($(date +%s) + $1))
  while rpc_alive && (($(date +%s) < deadline)); do sleep 0.1; done
  if rpc_alive && [[ -n "$RPC_PID" ]]; then
    RPC_SIGNALLED=1
    groups="$(descendant_groups "$RPC_PID") ${TOOL_GROUPS:-}"
    kill -TERM -- "-$RPC_PID" 2>/dev/null
    sleep 2
    groups="$groups $(descendant_groups "$RPC_PID")"
    for g in $groups; do kill -KILL -- "-$g" 2>/dev/null; done
    kill -KILL -- "-$RPC_PID" 2>/dev/null
  fi
  # The supervisor reaps on its next poll; read the status only once it exists.
  deadline=$(($(date +%s) + 10))
  while rpc_alive && (($(date +%s) < deadline)); do sleep 0.1; done
  rpc_pump
  exec 9<&-
  RPC_EXIT="$(cat "$SEG_DIR/exit.status" 2>/dev/null)"
  rm -f "$SEG_DIR/cmd.fifo"
}

# ---- handoff: logical run state ---------------------------------------------------
# results/<run>/state.json is the logical handoff across segments: identity,
# fingerprint, session, segment history, and live supervision fields. Status
# reads it (and the files beside it) and never talks to a live Pi process.

state_path() { printf '%s\n' "$DELEGATE_DIR/results/$1/state.json"; }

# state_update <jq filter> [jq args...] : atomic update of this run's state.
state_update() {
  local file filter="$1" tmp
  shift
  file="$(state_path "$RUN_ID")"
  tmp="$file.tmp.$$"
  jq --argjson now "$(date +%s)" "$@" "$filter | .updated_at = \$now" "$file" >"$tmp" && mv "$tmp" "$file"
}

# Lock ownership: "<pid> <start time>" (start time only where ps is allowed).
proc_start_time() { ps -o lstart= -p "$1" 2>/dev/null | tr -s ' ' | sed 's/^ //'; }

lock_is_stale() {
  local owner pid started=""
  owner="$(cat "$DELEGATE_DIR/lock/owner" 2>/dev/null)" || return 1
  pid="${owner%% *}"
  [[ "$owner" == *" "* ]] && started="${owner#* }"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 0
  [[ -n "$started" && "$(proc_start_time "$pid")" != "$started" ]]
}

take_lock() {
  mkdir -p "$DELEGATE_DIR" || return 1
  if ! mkdir "$DELEGATE_DIR/lock" 2>/dev/null; then
    lock_is_stale || return 1
    say "taking over a stale lock left by process $(cut -d' ' -f1 "$DELEGATE_DIR/lock/owner")"
    rm -f "$DELEGATE_DIR/lock/owner"
    rmdir "$DELEGATE_DIR/lock" 2>/dev/null
    mkdir "$DELEGATE_DIR/lock" 2>/dev/null || return 1
  fi
  printf '%s %s\n' "$$" "$(proc_start_time "$$")" >"$DELEGATE_DIR/lock/owner"
  LOCKED=1
}

lock_refusal() { say "another run holds $DELEGATE_DIR/lock (owner: $(cat "$DELEGATE_DIR/lock/owner" 2>/dev/null || echo unknown)); remove it if no run is active"; }

# Detached launch: the background runner reports here once its fast
# preconditions pass (or why it stopped), so refusals stay synchronous.
launch_report() {
  [[ -n "${DELEGATE_LAUNCH_FILE:-}" && -z "${LAUNCH_REPORTED:-}" ]] || return 0
  LAUNCH_REPORTED=1
  jq -c -n --arg status "$1" --arg reason "${2:-}" --argjson code "${3:-0}" \
    '{status: $status, reason: $reason, code: $code}' >"$DELEGATE_LAUNCH_FILE.tmp" &&
    mv "$DELEGATE_LAUNCH_FILE.tmp" "$DELEGATE_LAUNCH_FILE"
}

runner_path() { printf '%s/%s\n' "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" "$(basename "${BASH_SOURCE[0]}")"; }

# handoff_launch <run-id> <subcommand args...> : start this runner detached
# (new session, no terminal, output to results/<run>/runner.log), wait for
# its launch report, and print how to follow it. Exits with the report's
# code for a refusal, 0 once the run is under way.
handoff_launch() {
  local run="$1" log launch report status reason code waited=0
  shift
  mkdir -p "$DELEGATE_DIR/results/$run" || { say "could not create the result directory"; exit 3; }
  log="$DELEGATE_DIR/results/$run/runner.log"
  launch="$DELEGATE_DIR/results/$run/launch.$$.json"
  rm -f "$launch"
  # Double fork with setsid (macOS has no setsid command): the orchestrator's
  # tool-call timeout or process-group kill cannot reach the runner, and the
  # runner's exit is what ends Pi (its stdin closes).
  DELEGATE_LAUNCH_FILE="$launch" perl -MPOSIX -e '
    my $log = shift;
    exit 0 if fork();
    POSIX::setsid() or die "setsid: $!";
    exit 0 if fork();
    open STDIN, "<", "/dev/null";
    open STDOUT, ">>", $log or die "log: $!";
    open STDERR, ">&", \*STDOUT;
    exec @ARGV or exit 127;
  ' "$log" bash "$(runner_path)" "$@" || { say "could not launch the handoff"; exit 3; }
  while [[ ! -e "$launch" ]]; do
    if ((waited >= 1200)); then
      say "the handoff did not report within 120s; see ${log#"$ROOT"/}"
      exit 3
    fi
    sleep 0.1
    waited=$((waited + 1))
  done
  report="$(cat "$launch")"
  rm -f "$launch"
  status="$(jq -r .status <<<"$report")"
  reason="$(jq -r .reason <<<"$report")"
  code="$(jq -r .code <<<"$report")"
  if [[ "$status" != running ]]; then
    say "$status: $reason"
    say "log: ${log#"$ROOT"/}"
    exit "$code"
  fi
  say "launched $run in the background; log: ${log#"$ROOT"/}"
  say "follow it with: .agentic/delegate-run.sh handoff-status $run --wait 540 --interval 60"
  say "  (repeat while it exits 12, adding --since <the cursor it printed>; then act on its exit status)"
  printf '%s\n' "$run"
  exit 0
}

# ---- handoff: a whole phase to a local agent session ----------------------------

plan_files() {
  awk '/^## Files to touch/ { on = 1; next } on && /^## / { exit }
    on && /^- `/ { sub(/^- `/, ""); sub(/`.*/, ""); print }' "$1"
}

# is_test_path <path> : the stack-neutral test-file rule used by the
# implement phase (tests are the oracle there and must not change).
is_test_path() {
  [[ "$1" =~ (^|/)(tests?|__tests__|spec)/ || "$1" =~ [._-](test|spec)\.[A-Za-z0-9]+$ || "$1" =~ (^|/)test_[^/]*$ ]]
}

handoff_prompt() {
  local spec="$1" phase="$2" phases
  case "$phase" in
    implement) phases="implementation, then /review-check" ;;
    *) phases="/test-first, then implementation, then /review-check" ;;
  esac
  cat <<PROMPT
You are a local model implementing one phase of an approved spec in an isolated worktree. Spec: $spec. Its plan is approved; the spec, its standing decisions, and its clarified decisions are final inputs.

Run $phases, following AGENTS.md. Rules:
- Change only the files the plan lists under "## Files to touch", plus the spec's own phase-handoff note. If the work needs a file the plan does not list, stop and say so in the handoff note.$( [[ "$phase" == implement ]] && printf '\n- The tests already exist and are the oracle: do not edit, add, or delete any test file. If a test looks wrong, stop and say so in the handoff note.' )
- Do not use local delegation (.agentic/delegate-run.sh or /delegate) and pass no model override to any subagent.
- Do not commit, push, or change branches. Do not ask the human anything: no one is watching. Where the spec leaves a choice open, pick the option that keeps the approved plan intact and record it in the handoff note.
- Stop when /review-check is green, or when you are blocked. Before stopping, append a short "### After handoff" entry under "## Phase handoff" in the spec: what you did, any choice you made, and anything left red or blocked.
PROMPT
}

handoff_head_ok() {
  [[ "$(git -C "$1" rev-parse HEAD)" == "$SNAPSHOT_SHA" &&
    "$(git -C "$1" symbolic-ref -q HEAD)" == "refs/heads/$2" ]]
}

# handoff_classify <wt> <spec> <phase> <dir> : the one scope rule set, shared
# by the final audit and the in-flight check. Writes every path changed since
# the starting commit (staged, unstaged, deleted, untracked) to <dir>/changed,
# and those outside the plan, and test files changed in the implement phase,
# to <dir>/outside and <dir>/tests-touched. Returns 1 if git fails, 2 for a
# path containing a newline.
handoff_classify() {
  local wt="$1" spec="$2" phase="$3" dir="$4" f
  {
    git -C "$wt" diff --name-only --no-renames -z "$SNAPSHOT_SHA" -- &&
      git -C "$wt" ls-files --others --exclude-standard -z
  } >"$dir/changed.nul" || return 1
  : >"$dir/changed"
  while IFS= read -r -d '' f; do
    [[ "$f" != *$'\n'* ]] || return 2
    printf '%s\n' "$f" >>"$dir/changed"
  done <"$dir/changed.nul"
  LC_ALL=C sort -u "$dir/changed" >"$dir/changed.sorted"
  mv "$dir/changed.sorted" "$dir/changed"
  : >"$dir/outside"
  : >"$dir/tests-touched"
  while IFS= read -r f; do
    [[ -n "$f" ]] || continue
    [[ "$f" == "$spec" || "$f" == docs/specs/README.md ]] && continue
    grep -qxF "$f" "$STATE/plan-files" || printf '%s\n' "$f" >>"$dir/outside"
    if [[ "$phase" == implement ]] && is_test_path "$f"; then printf '%s\n' "$f" >>"$dir/tests-touched"; fi
  done <"$dir/changed"
  return 0
}

# Audit the complete final tree against the starting commit, including
# staged, unstaged, deleted, and untracked files. Run before and after the
# gate: verifiers can write files too. A session must not move HEAD.
audit_handoff() {
  local wt="$1" spec="$2" phase="$3" branch="$4" result_dir="$5" rc=0
  handoff_head_ok "$wt" "$branch" ||
    integrity "the session or verifier committed or changed branches (worktree kept at $wt)"
  handoff_classify "$wt" "$spec" "$phase" "$STATE" || rc=$?
  ((rc == 2)) && integrity "a changed path contains a newline"
  ((rc == 0)) || infra "could not list handoff changes"
  jq -n --arg wt "$wt" --arg branch "$branch" --arg phase "$phase" --arg harness "$HANDOFF_HARNESS" \
    --arg harness_version "$HANDOFF_HARNESS_VERSION" --arg pi_model "$ENDPOINT_NAME/$MODEL_ID" \
    --rawfile changed "$STATE/changed" --rawfile outside "$STATE/outside" --rawfile tests "$STATE/tests-touched" \
    '{worktree: $wt, branch: $branch, phase: $phase, harness: $harness, harness_version: $harness_version,
      pi_model: $pi_model, changed: ($changed | split("\n") | map(select(. != ""))),
      outside_plan: ($outside | split("\n") | map(select(. != ""))),
      test_files_changed: ($tests | split("\n") | map(select(. != "")))}' >"$result_dir/handoff.json"
  [[ -s "$STATE/outside" ]] && integrity "changes outside the plan's files: $(tr '\n' ' ' <"$STATE/outside")(worktree kept at $wt)"
  [[ -s "$STATE/tests-touched" ]] && integrity "the implement phase changed test files: $(tr '\n' ' ' <"$STATE/tests-touched")(worktree kept at $wt)"
  return 0
}

# In-flight scope check after a tool call: the audit's rules, read-only and
# without the index lock Pi's own git commands may need. Sets SCOPE_VIOLATION
# and returns 1 on a violation. A git failure is not one; the final audit
# still runs.
handoff_scope_check() {
  local rc=0
  SCOPE_VIOLATION=""
  mkdir -p "$STATE/live" || return 0
  if ! GIT_OPTIONAL_LOCKS=0 handoff_head_ok "$TREE" "$BRANCH" 2>/dev/null; then
    SCOPE_VIOLATION="the session committed or changed branches"
    return 1
  fi
  GIT_OPTIONAL_LOCKS=0 handoff_classify "$TREE" "$SPEC_REL" "$PHASE" "$STATE/live" 2>/dev/null || rc=$?
  case "$rc" in
    0) ;;
    2) SCOPE_VIOLATION="a changed path contains a newline"; return 1 ;;
    *) return 0 ;;
  esac
  if [[ -s "$STATE/live/tests-touched" ]]; then
    SCOPE_VIOLATION="the implement phase changed test files: $(tr '\n' ' ' <"$STATE/live/tests-touched")"
  elif [[ -s "$STATE/live/outside" ]]; then
    SCOPE_VIOLATION="changes outside the plan's files: $(tr '\n' ' ' <"$STATE/live/outside")"
  fi
  SCOPE_VIOLATION="${SCOPE_VIOLATION% }"
  [[ -z "$SCOPE_VIOLATION" ]]
}

# The rules restated after a compaction, since Pi cannot append them to its
# default compaction summary. Bounded: a long plan is named by count only.
handoff_guardrail_message() {
  local spec="$1" phase="$2" count files
  count="$(grep -c . "$STATE/plan-files")"
  files="$(awk '{ printf "%s%s", sep, $0; sep = ", " }' "$STATE/plan-files")"
  if ((count <= 20 && ${#files} <= 1500)); then
    files="the files listed under \"## Files to touch\" in $spec ($files)"
  else
    files="the $count files listed under \"## Files to touch\" in $spec"
  fi
  printf '%s' "Handoff supervisor reminder after context compaction: the original handoff rules still apply. Change only $files, plus the spec's own \"## Phase handoff\" note."
  [[ "$phase" == implement ]] &&
    printf '%s' " This is the implement phase: the tests are the oracle; do not edit, add, or delete any test file."
  printf '%s' " Do not commit, push, or change branches. If a test or the plan conflicts with what the work requires, stop and record the conflict under \"## Phase handoff\" in the spec instead of editing the test or a file outside the plan."
}

handoff_resume_prompt() {
  cat <<PROMPT
You are resuming a paused local handoff in the same worktree and session. Spec: $1.

Continue the approved phase from the current state of the worktree. First inspect the worktree and the latest "### Paused handoff" entry in the spec, and do not repeat completed work. Every rule from the original handoff prompt still applies. Finish the remaining work and the existing /review-check obligation, then append a short "### After handoff" entry as before.
PROMPT
}

# Supervision settings. The lease renews on meaningful activity; there is no
# default total limit. DELEGATE_HANDOFF_TIMEOUT (the old hard limit) is read
# as the absolute ceiling, which now pauses the run instead of killing it.
handoff_config() {
  local v
  LEASE_SECONDS="${DELEGATE_HANDOFF_LEASE_SECONDS:-3600}"
  RECOVERY_SECONDS="${DELEGATE_HANDOFF_RECOVERY_SECONDS:-900}"
  GRACE_SECONDS="${DELEGATE_HANDOFF_GRACE_SECONDS:-600}"
  ABORT_SECONDS="${DELEGATE_HANDOFF_ABORT_SECONDS:-60}"
  SCOPE_CHECK_SECONDS="${DELEGATE_HANDOFF_SCOPE_CHECK_SECONDS:-5}"
  MAX_TOTAL="${DELEGATE_HANDOFF_MAX_TOTAL_SECONDS:-}"
  if [[ -z "$MAX_TOTAL" && -n "${DELEGATE_HANDOFF_TIMEOUT:-}" ]]; then
    MAX_TOTAL="$DELEGATE_HANDOFF_TIMEOUT"
    say "warning: DELEGATE_HANDOFF_TIMEOUT is deprecated; it now sets the absolute ceiling (DELEGATE_HANDOFF_MAX_TOTAL_SECONDS), which pauses the run at a checkpoint instead of killing it"
  fi
  for v in "$LEASE_SECONDS" "$RECOVERY_SECONDS" "$GRACE_SECONDS" "$ABORT_SECONDS" "$SCOPE_CHECK_SECONDS" ${MAX_TOTAL:+"$MAX_TOTAL"}; do
    [[ "$v" =~ ^[1-9][0-9]*$ ]] || { say "handoff time settings must be positive whole seconds (got '$v')"; exit 2; }
  done
}

# A content manifest of the worktree, for "has anything changed" at lease
# expiry. GIT_OPTIONAL_LOCKS=0 keeps git from taking the index lock Pi's own
# git commands may need.
worktree_manifest() {
  {
    GIT_OPTIONAL_LOCKS=0 git -C "$TREE" diff --no-ext-diff "$SNAPSHOT_SHA" -- 2>/dev/null
    GIT_OPTIONAL_LOCKS=0 git -C "$TREE" ls-files --others --exclude-standard 2>/dev/null
  } | "${SHA[@]}" | cut -c1-16
}

# The arguments and environment every segment's Pi process gets.
handoff_session_setup() {
  local pi_thinking
  pi_thinking="$(profile_get model "$MODEL_NAME" pi_thinking)"
  HANDOFF_ARGS=(--mode rpc --approve --provider "$ENDPOINT_NAME" --model "$MODEL_ID" --session-dir "$RESULT_DIR/session")
  [[ -n "$pi_thinking" ]] && HANDOFF_ARGS+=(--thinking "$pi_thinking")
  HANDOFF_ENV=(PATH="$PATH" HOME="$HOME" LANG="${LANG:-C.UTF-8}" TERM=dumb PI_CODING_AGENT_DIR="$RESULT_DIR/pi-agent")
  [[ -n "${TMPDIR:-}" ]] && HANDOFF_ENV+=(TMPDIR="$TMPDIR")
  [[ -n "${CRED_ENV:-}" ]] && HANDOFF_ENV+=("$CRED_ENV=${!CRED_ENV}")
  # Pi installs the packages in .pi/settings.json into the worktree's ignored
  # .pi/npm/ on start. Its npm cache stays in the run directory, which is
  # writable inside Codex's sandbox where ~/.npm is not.
  HANDOFF_ENV+=(npm_config_cache="$RESULT_DIR/npm-cache")
}

# Write the live supervision fields; cheap enough for every few seconds.
handoff_status_write() {
  TOOL_GROUPS="$(descendant_groups "$RPC_PID" | tr '\n' ' ')"
  state_update '.live = {segment: $seg, pi_pid: $pid, last_event: $ev, last_event_at: $evat,
      last_activity_at: $act, active_tool: $tool, active_tool_since: $toolat, retries: $retries, compactions: $comp,
      dialogs_cancelled: $dialogs, lease_expires_at: $lease, checkpoint: $cp,
      checkpoint_requested_at: $cpat, tool_groups: ($groups | split(" ") | map(select(. != "")))}' \
    --arg seg "$SEG_N" --arg pid "$RPC_PID" --arg ev "$RPC_LAST_EVENT" --argjson evat "$RPC_LAST_EVENT_AT" \
    --argjson act "$RPC_LAST_ACTIVITY" --arg tool "$RPC_ACTIVE_TOOL" --argjson toolat "$RPC_ACTIVE_TOOL_AT" \
    --argjson retries "$RPC_RETRIES" \
    --argjson comp "$RPC_COMPACTIONS" --argjson dialogs "$RPC_DIALOGS" --argjson lease "$1" \
    --arg cp "$2" --argjson cpat "$3" --arg groups "$TOOL_GROUPS"
}

# handoff_segment <prompt-file> [<session-file>] : run one supervised Pi
# segment. Sets SEG_END (completed, paused, or failed), SEG_REASON, and
# SEG_FORCED. Nothing here calls a model except the session itself.
handoff_segment() {
  local prompt_file="$1" session="${2:-}" n started now lease_end checkpoint_at=0 checkpoint_reason=""
  local grace_used=0 next_status=0 next_beat manifest current active_before ceiling_at=0 model
  local next_scope=0 steers=0 guardrail
  n="$(jq '.segments | length' "$(state_path "$RUN_ID")")"
  SEG_N="$(printf '%04d' $((n + 1)))"
  SEG_DIR="$RESULT_DIR/segments/$SEG_N"
  SEG_END=failed SEG_REASON="" SEG_FORCED=0 TOOL_GROUPS=""
  mkdir -p "$SEG_DIR" || { SEG_REASON="could not create $SEG_DIR"; return; }
  started="$(date +%s)"
  active_before="$(jq '.active_seconds // 0' "$(state_path "$RUN_ID")")"
  [[ -n "${MAX_TOTAL:-}" ]] && ceiling_at=$((started + MAX_TOTAL - active_before))
  state_update '.state = "running" | .runner_pid = $rp | .segments += [{segment: $seg, started_at: $s}]' \
    --argjson rp "$$" --arg seg "$SEG_N" --argjson s "$started"
  local -a args=("${HANDOFF_ARGS[@]}")
  [[ -n "$session" ]] && args+=(--session "$session")
  say "session segment $SEG_N: $HANDOFF_HARNESS on '$MODEL_NAME' ($ENDPOINT_NAME/$MODEL_ID); lease ${LEASE_SECONDS}s${MAX_TOTAL:+, ceiling ${MAX_TOTAL}s of active time}"
  if ! rpc_start "$TREE" env -i "${HANDOFF_ENV[@]}" "$HANDOFF_HARNESS" "${args[@]}"; then
    SEG_REASON="could not start $HANDOFF_HARNESS"
    return
  fi
  state_update '.live.pi_pid = $p' --arg p "$RPC_PID"

  # The session identity, and the model Pi resolved: an unknown model id
  # does not fail Pi's startup, so compare it with the profile here.
  if ! rpc_call '{"type":"get_state"}' 120 || ! rpc_ok; then
    SEG_REASON="the harness did not answer get_state: $(bounded_tail "$SEG_DIR/harness.log" | tail -n 3 | tr '\n' ' ')"
    rpc_stop 10
    return
  fi
  model="$(jq -r '"\(.data.model.provider // "")/\(.data.model.id // "")"' <<<"$RPC_REPLY")"
  if [[ "$model" != "$ENDPOINT_NAME/$MODEL_ID" ]]; then
    SEG_REASON="the harness resolved model '$model', not '$ENDPOINT_NAME/$MODEL_ID'"
    rpc_stop 10
    return
  fi
  state_update '.session_file = $f | .session_id = $i' \
    --arg f "$(jq -r '.data.sessionFile // ""' <<<"$RPC_REPLY")" --arg i "$(jq -r '.data.sessionId // ""' <<<"$RPC_REPLY")"
  if ! rpc_call "$(jq -c -n --rawfile m "$prompt_file" '{type: "prompt", message: $m}')" 120 || ! rpc_ok; then
    SEG_REASON="the harness did not accept the prompt: $(jq -r '.error? // "no response"' <<<"${RPC_REPLY:-null}" 2>/dev/null)"
    rpc_stop 10
    return
  fi

  manifest="$(worktree_manifest)"
  printf '%s\n' "$manifest" >>"$SEG_DIR/manifests"
  guardrail="$(handoff_guardrail_message "$SPEC_REL" "$PHASE")"
  lease_end=$((RPC_LAST_ACTIVITY + LEASE_SECONDS))
  next_beat=$(($(date +%s) + PROGRESS_INTERVAL))
  while :; do
    rpc_pump
    now="$(date +%s)"
    if ((RPC_SETTLED)); then
      if ((checkpoint_at > 0)); then
        SEG_END=paused
        SEG_REASON="$checkpoint_reason"
        [[ "$RPC_SETTLED_ABORTED" == true ]] && SEG_FORCED=1
      else
        SEG_END=completed
      fi
      break
    fi
    if ! rpc_alive; then
      SEG_REASON="the harness exited $(cat "$SEG_DIR/exit.status" 2>/dev/null) before settling: $(bounded_tail "$SEG_DIR/harness.log" | tail -n 3 | tr '\n' ' ')"
      break
    fi
    # Fail fast on scope: check after tool calls, at most once every
    # SCOPE_CHECK_SECONDS. A violation aborts at once (no pause note: the
    # session is what broke scope); the audit after the segment, unchanged,
    # turns it into the integrity outcome and keeps the worktree.
    if ((RPC_TOOL_ENDED && now >= next_scope)); then
      RPC_TOOL_ENDED=0
      next_scope=$((now + SCOPE_CHECK_SECONDS))
      if ! handoff_scope_check; then
        say "early scope stop: $SCOPE_VIOLATION; aborting the session"
        state_update '.early_scope_stop = {at: $now, segment: $seg, reason: $r}' --arg seg "$SEG_N" --arg r "$SCOPE_VIOLATION"
        SEG_FORCED=1
        rpc_call '{"type":"clear_queue"}' "$ABORT_SECONDS"
        rpc_call '{"type":"abort"}' "$ABORT_SECONDS"
        rpc_pump
        SEG_END=paused
        SEG_REASON="early scope stop: $SCOPE_VIOLATION"
        break
      fi
    fi
    # Restate the rules once per compaction; the summary may have dropped
    # them. A steer that is not accepted is reported, not fatal.
    if ((RPC_COMPACTED)); then
      RPC_COMPACTED=0
      if ((checkpoint_at == 0)); then
        if rpc_call "$(jq -c -n --arg m "$guardrail" '{type: "steer", message: $m}')" 60 && rpc_ok; then
          steers=$((steers + 1))
          progress "guardrails restated after compaction"
        else
          progress "the post-compaction guardrail steer was not accepted"
        fi
      fi
    fi
    if ((checkpoint_at == 0)); then
      if ((RPC_LAST_ACTIVITY + LEASE_SECONDS > lease_end)); then
        lease_end=$((RPC_LAST_ACTIVITY + LEASE_SECONDS))
        grace_used=0
      fi
      if [[ -n "${HANDOFF_STOP:-}" ]]; then
        checkpoint_reason="operator stop"
      elif ((ceiling_at > 0 && now >= ceiling_at)); then
        checkpoint_reason="absolute ceiling of ${MAX_TOTAL}s active time reached"
      elif ((now >= lease_end)); then
        # Lease expired: renew on new worktree content (never on a manifest
        # seen before), once on bounded retry/compaction recovery, otherwise
        # checkpoint. No model is asked.
        current="$(worktree_manifest)"
        if ! grep -qxF "$current" "$SEG_DIR/manifests"; then
          printf '%s\n' "$current" >>"$SEG_DIR/manifests"
          lease_end=$((now + LEASE_SECONDS))
          progress "lease renewed: the worktree changed"
        elif ((RPC_RECOVERING && !grace_used)); then
          grace_used=1
          lease_end=$((now + RECOVERY_SECONDS))
          progress "lease extended ${RECOVERY_SECONDS}s: retry or compaction recovery in progress"
        else
          checkpoint_reason="no meaningful activity for ${LEASE_SECONDS}s${RPC_ACTIVE_TOOL:+ (tool '$RPC_ACTIVE_TOOL' still running)}"
        fi
      fi
      if [[ -n "$checkpoint_reason" ]]; then
        say "requesting a checkpoint: $checkpoint_reason"
        checkpoint_at="$now"
        if ! rpc_call "$(jq -c -n --arg m "$CHECKPOINT_MESSAGE" '{type: "steer", message: $m}')" 60 || ! rpc_ok; then
          say "the checkpoint steer was not accepted; forcing the pause"
          checkpoint_at=$((now - GRACE_SECONDS))
        fi
      fi
    elif ((now >= checkpoint_at + GRACE_SECONDS)); then
      # Not settled within the grace: drop queued input and abort. abort
      # returns once Pi is idle; rpc_stop signals only if it never does.
      say "no settle ${GRACE_SECONDS}s after the checkpoint request; aborting"
      SEG_FORCED=1
      rpc_call '{"type":"clear_queue"}' "$ABORT_SECONDS"
      rpc_call '{"type":"abort"}' "$ABORT_SECONDS"
      rpc_pump
      SEG_END=paused
      SEG_REASON="$checkpoint_reason (forced: the session did not settle)"
      break
    fi
    if ((now >= next_status)); then
      handoff_status_write "$lease_end" "$checkpoint_reason" "$checkpoint_at"
      next_status=$((now + 5))
    fi
    if ((now >= next_beat)); then
      progress "session running — $((now - started))s elapsed, last activity $((now - RPC_LAST_ACTIVITY))s ago${RPC_ACTIVE_TOOL:+, tool: $RPC_ACTIVE_TOOL}"
      next_beat=$((now + PROGRESS_INTERVAL))
    fi
    sleep 0.2
  done
  handoff_status_write "$lease_end" "$checkpoint_reason" "$checkpoint_at"
  rpc_stop "$ABORT_SECONDS"
  ((RPC_SIGNALLED)) && SEG_FORCED=1
  now="$(date +%s)"
  jq -n --arg seg "$SEG_N" --arg end "$SEG_END" --arg reason "$SEG_REASON" --argjson forced "$SEG_FORCED" \
    --arg exit "${RPC_EXIT:-}" --argjson s "$started" --argjson e "$now" --argjson retries "$RPC_RETRIES" \
    --argjson comp "$RPC_COMPACTIONS" --argjson dialogs "$RPC_DIALOGS" --arg last "$RPC_LAST_EVENT" \
    --argjson steers "$steers" \
    '{segment: $seg, end: $end, reason: $reason, forced: ($forced == 1), harness_exit: $exit,
      started_at: $s, ended_at: $e, seconds: ($e - $s), retries: $retries, compactions: $comp,
      guardrail_steers: $steers, dialogs_cancelled: $dialogs, last_event: $last}' >"$SEG_DIR/status.json"
  state_update '.segments[-1] += $st | .active_seconds = ((.active_seconds // 0) + $st.seconds) | del(.live)' \
    --argjson st "$(cat "$SEG_DIR/status.json")"
}

# After a segment: verify and commit a completed phase, or audit and pause.
handoff_after_segment() {
  local wt="$TREE" gate_status=0 last
  case "$SEG_END" in
    failed) infra "$SEG_REASON; worktree kept at $wt" ;;
    paused)
      progress "auditing the paused worktree (no gate, no commit)"
      state_update '.state = "auditing"'
      audit_handoff "$wt" "$SPEC_REL" "$PHASE" "$BRANCH" "$RESULT_DIR"
      # A session that broke scope is not resumable, even if it reverted the
      # edit before the abort landed and the audit found a clean tree.
      [[ "$SEG_REASON" == "early scope stop: "* ]] && integrity "$SEG_REASON (worktree kept at $wt)"
      last="$(tail -n 1 "$(jq -r '.session_file // ""' "$(state_path "$RUN_ID")")" 2>/dev/null |
        jq -r '"\(.type // "?") \(.timestamp // "")"' 2>/dev/null)"
      state_update '.state = "paused" | .reason = $r | .forced = $f | .paused_at = $now' \
        --arg r "$SEG_REASON" --argjson f "$([[ "$SEG_FORCED" == 1 ]] && echo true || echo false)"
      launch_report paused "$SEG_REASON" 11
      say "paused: $SEG_REASON"
      if [[ "$SEG_FORCED" == 1 ]]; then
        say "the pause was forced: the last model turn, tool result, or file write may be incomplete"
      else
        say "the session settled at a checkpoint and wrote its pause note"
      fi
      say "worktree: $wt (branch $BRANCH); last session entry: ${last:-unknown}"
      say "status: delegate-run.sh handoff-status $RUN_ID"
      say "resume: delegate-run.sh handoff-resume $RUN_ID --allow-commit"
      say "abandon: delegate-run.sh handoff-clean $RUN_ID"
      exit 11
      ;;
  esac
  state_update '.state = "verifying"'
  progress "independently verifying — plan scope"
  audit_handoff "$wt" "$SPEC_REL" "$PHASE" "$BRANCH" "$RESULT_DIR"
  [[ -s "$STATE/changed" ]] || { OUTCOME=needs_frontier; REASON="the session changed nothing"; say "needs_frontier: $REASON"; finish 10; }
  progress "independently verifying — full gate"
  verify_cmd bash "$TOOLCHAIN" gate >"$RESULT_DIR/gate.log" 2>&1 || gate_status=$?
  audit_handoff "$wt" "$SPEC_REL" "$PHASE" "$BRANCH" "$RESULT_DIR"
  if ((gate_status != 0)); then
    OUTCOME=needs_frontier
    REASON="the session ended with the gate red: $(bounded_tail "$RESULT_DIR/gate.log" | tail -n 5 | tr '\n' ' ')"
    say "needs_frontier: $REASON"
    say "worktree kept at $wt; read the spec's '### After handoff' note there"
    finish 10
  fi
  GATE_LEVEL=full
  git -C "$wt" add -A >/dev/null 2>&1 &&
    git -C "$wt" commit -q -m "Local handoff $RUN_ID ($PHASE on $MODEL_NAME)" >"$STATE/commit.log" 2>&1 ||
    infra "could not commit on $BRANCH: $(bounded_tail "$STATE/commit.log")"
  OUTCOME=green
  REASON=""
  say "green: $PHASE verified (full gate, plan scope) and committed on $BRANCH."
  say "Next: review the branch (git diff HEAD...$BRANCH), then: git merge --squash $BRANCH"
  say "  then: delegate-run.sh handoff-clean $RUN_ID"
  finish 0
}

handoff_scratch() {
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/delegate-handoff.XXXXXX")" || infra "could not create scratch directory"
  STATE="$WORK/state"
  mkdir -p "$STATE"
}

# A stop request (handoff-stop sends TERM) becomes a checkpoint, not a kill.
handoff_trap_stop() {
  trap 'HANDOFF_STOP=1' TERM
}

cmd_handoff() {
  local spec="" phase=implement profile allow_commit=0 progress_mode="" foreground=0 run_id=""
  profile="$(default_profile)"
  MODEL_NAME=""
  while (($#)); do
    case "$1" in
      --phase)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--phase requires a value"; exit 2; }
        phase="$2"; shift 2 ;;
      --model)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--model requires a value"; exit 2; }
        MODEL_NAME="$2"; shift 2 ;;
      --profile)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--profile requires a value"; exit 2; }
        profile="$2"; shift 2 ;;
      --progress)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--progress requires a value"; exit 2; }
        progress_mode="$2"; shift 2 ;;
      --run-id)
        (($# >= 2)) && [[ "$2" =~ ^handoff-[A-Za-z0-9._-]+$ ]] || { say "--run-id requires a handoff run id"; exit 2; }
        run_id="$2"; shift 2 ;;
      --allow-commit) allow_commit=1; shift ;;
      --foreground) foreground=1; shift ;;
      -h | --help) usage; exit 0 ;;
      -*) say "unknown option: $1"; exit 2 ;;
      *) [[ -z "$spec" ]] || { say "one spec per handoff"; exit 2; }; spec="$1"; shift ;;
    esac
  done
  [[ -n "$spec" ]] || { say "usage: delegate-run.sh handoff <spec> --allow-commit [--phase implement|test-first+implement] [--model <name>] [--progress quiet|summary] [--foreground]"; exit 2; }
  configure_progress "$progress_mode"
  case "$phase" in implement | test-first+implement) ;; *) say "unknown phase: $phase"; exit 2 ;; esac
  [[ "$allow_commit" == 1 ]] || {
    say "refused: handoff creates a temporary commit on handoff/<run>; obtain explicit current-chat commit approval, then pass --allow-commit"
    exit 2
  }
  handoff_config
  require_deps git jq awk perl curl
  [[ -z "$run_id" ]] && run_id="handoff-$(basename "$spec" .md)-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  if [[ "$foreground" == 0 ]]; then
    local -a again=("$spec" --allow-commit --phase "$phase" --profile "$profile" --run-id "$run_id"
      --progress "${progress_mode:-summary}" --foreground)
    [[ -n "$MODEL_NAME" ]] && again+=(--model "$MODEL_NAME")
    handoff_launch "$run_id" handoff "${again[@]}"
  fi

  MODE=manual
  [[ -n "$MODEL_NAME" ]] || MODE=selected
  load_handoff_harness || { launch_report refused "agent harness unavailable: ${DELEGATE_HANDOFF_HARNESS:-pi}" 2; say "agent harness unavailable: ${DELEGATE_HANDOFF_HARNESS:-pi}"; exit 2; }
  git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { launch_report refused "not a git work tree" 2; say "not a git work tree: $ROOT"; exit 2; }
  mkdir -p "$DELEGATE_DIR/results"
  take_lock || { launch_report refused "another run holds the lock" 2; lock_refusal; exit 2; }
  trap cleanup EXIT
  handoff_trap_stop
  handoff_scratch
  RUN_ID="$run_id"
  RESULT_DIR="$DELEGATE_DIR/results/$RUN_ID"
  [[ -f "$ROOT/$spec" ]] || refuse "spec not found: $spec"
  case "$spec" in /* | *..*) refuse "spec path must be relative to the repository: $spec" ;; esac
  SPEC_REL="$spec"
  SPEC_SHA="$(sha_file "$ROOT/$spec")"
  TARGET="$spec"
  PHASE="$phase"
  BUCKET="phase:$phase"

  # -- preconditions
  grep -q '^\*\*Status:\*\* shipping' "$ROOT/$spec" || refuse "spec status is not 'shipping' (the plan must be approved first)"
  grep -q '^## Approved implementation plan' "$ROOT/$spec" || refuse "spec has no '## Approved implementation plan'"
  plan_files "$ROOT/$spec" >"$STATE/plan-files"
  [[ -s "$STATE/plan-files" ]] || refuse "spec lists no files under '## Files to touch'"
  [[ -z "$(git -C "$ROOT" status --porcelain)" ]] || refuse "the working tree is not clean; commit or stash first (the worktree starts from HEAD)"

  # -- model
  load_profile "$profile"
  if [[ -n "$MODEL_NAME" ]]; then
    load_model "$MODEL_NAME"
    check_credential
    QUALIFIED="$(qualification_state "$MODEL_NAME" "$FINGERPRINT")"
    [[ "$QUALIFIED" == qualified ]] ||
      refuse "model '$MODEL_NAME' is $QUALIFIED for its current setup; run: delegate-run.sh qualify $MODEL_NAME"
    handoff_fingerprint || refuse "invalid handoff model configuration"
    [[ "$(score "$MODEL_NAME" "$FINGERPRINT" "$BUCKET" | cut -d' ' -f7)" != demoted ]] ||
      say "warning: '$MODEL_NAME' is demoted for $BUCKET tasks; this manual run is still scored"
  else
    select_model
  fi

  # -- logical run state; the launcher returns once this exists
  local base wt
  base="${DELEGATE_HANDOFF_DIR:-${TMPDIR:-/tmp}/agentic-handoff}"
  wt="$base/$RUN_ID"
  BRANCH="handoff/$RUN_ID"
  SNAPSHOT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
  mkdir -p "$RESULT_DIR/session" "$RESULT_DIR/pi-agent" "$RESULT_DIR/segments"
  jq -n --arg run "$RUN_ID" --arg spec "$spec" --arg phase "$phase" --arg profile "$profile" \
    --arg model "$MODEL_NAME" --arg mode "$MODE" --argjson selection "${SELECTION_JSON:-null}" \
    --arg wt "$wt" --arg branch "$BRANCH" --arg snapshot "$SNAPSHOT_SHA" --arg spec_sha "$SPEC_SHA" \
    --arg plan_sha "$(sha_file "$STATE/plan-files")" --arg fp "$FINGERPRINT" --arg qualified "${QUALIFIED:-}" \
    --arg harness "$HANDOFF_HARNESS" --arg hv "$HANDOFF_HARNESS_VERSION" --argjson started "$(date +%s)" \
    --argjson max "${MAX_TOTAL:-null}" --argjson rp "$$" \
    '{run: $run, state: "preparing", spec: $spec, phase: $phase, profile: $profile, model: $model,
      mode: $mode, selection: $selection, worktree: $wt, branch: $branch, snapshot: $snapshot,
      spec_sha256: $spec_sha, plan_sha256: $plan_sha, fingerprint: $fp, qualified: $qualified,
      harness: $harness, harness_version: $hv, started_at: $started, updated_at: $started,
      max_total_seconds: $max, active_seconds: 0, runner_pid: $rp, segments: []}' >"$(state_path "$RUN_ID")"
  launch_report running "" 0

  # -- worktree
  mkdir -p "$base" || infra "could not create $base"
  git -C "$ROOT" worktree add -q -b "$BRANCH" "$wt" "$SNAPSHOT_SHA" >"$STATE/worktree.log" 2>&1 ||
    infra "could not create the worktree: $(bounded_tail "$STATE/worktree.log")"
  TREE="$wt"
  progress "handoff $RUN_ID — '$MODEL_NAME', $BUCKET; logs: ${RESULT_DIR#"$ROOT"/}/"
  handoff_model_config >"$RESULT_DIR/pi-agent/models.json" || refuse "invalid handoff model configuration"
  say "worktree: $wt (branch $BRANCH)"

  verify_cmd bash "$TOOLCHAIN" install >"$RESULT_DIR/install.log" 2>&1 ||
    say "warning: '$TOOLCHAIN install' failed in the worktree; see ${RESULT_DIR#"$ROOT"/}/install.log"
  local ready=0
  verify_cmd bash "$TOOLCHAIN" ready >"$STATE/ready.log" 2>&1 || ready=$?
  ((ready == 0)) || refuse "the gate is not ready in the worktree ($TOOLCHAIN ready exited $ready); remove it with: delegate-run.sh handoff-clean $RUN_ID"
  if [[ "$phase" == implement ]] && verify_cmd bash "$TOOLCHAIN" test >"$STATE/baseline.log" 2>&1; then
    refuse "the tests already pass: nothing to implement (run test-first first, or use --phase test-first+implement); remove the worktree with: delegate-run.sh handoff-clean $RUN_ID"
  fi

  # -- session
  # A new worktree lacks the ignored .pi/npm/; reuse the checkout's installed
  # Pi packages so the session does not depend on the npm registry.
  if [[ -d "$ROOT/.pi/npm" && ! -e "$wt/.pi/npm" ]] && git -C "$wt" check-ignore -q .pi/npm/; then
    mkdir -p "$wt/.pi" && cp -R "$ROOT/.pi/npm" "$wt/.pi/npm" ||
      say "warning: could not copy .pi/npm/ into the worktree; Pi will install its packages"
  fi
  handoff_prompt "$spec" "$phase" >"$RESULT_DIR/prompt.md"
  handoff_session_setup
  handoff_segment "$RESULT_DIR/prompt.md"
  handoff_after_segment
}

# handoff_state_live <state.json> : is this run's runner or Pi still alive?
handoff_live() {
  local file="$1" pid
  for pid in $(jq -r '[.runner_pid, (.live.pi_pid // empty)] | map(tostring) | .[]' "$file" 2>/dev/null); do
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && return 0
  done
  return 1
}

# The logical state as status reports it: a non-terminal run whose runner
# and Pi are both gone is "orphaned" (resumable, after cleanup).
handoff_effective_state() {
  local file="$1" state
  state="$(jq -r '.state // "unknown"' "$file")"
  case "$state" in
    preparing | running | auditing | verifying) handoff_live "$file" || state=orphaned ;;
  esac
  printf '%s\n' "$state"
}

handoff_state_code() {
  case "$1" in
    green) echo 0 ;; needs_frontier) echo 10 ;; paused | orphaned) echo 11 ;;
    integrity) echo 4 ;; infrastructure) echo 3 ;; refused) echo 2 ;; *) echo 12 ;;
  esac
}

# ---- handoff-status: event cursor and interim lines --------------------------
# A cursor is "<segment>:<byte offset>" into that segment's events.jsonl,
# always at a line boundary ("0000:0" is before the first segment). Each
# caller keeps its own; nothing about a viewer is stored in the run.

# events_end <file> : byte offset just past the last complete line.
events_end() {
  perl -e '
    open my $f, "<", $ARGV[0] or do { print "0\n"; exit 0 };
    my ($pos, $buf) = (-s $f);
    while ($pos > 0) {
      my $n = $pos < 65536 ? $pos : 65536;
      $pos -= $n;
      seek $f, $pos, 0;
      read $f, $buf, $n;
      my $i = rindex $buf, "\n";
      if ($i >= 0) { print $pos + $i + 1, "\n"; exit 0 }
    }
    print "0\n";
  ' "$1"
}

handoff_latest_segment() {
  local d latest=0000
  for d in "$1"/segments/[0-9][0-9][0-9][0-9]; do [[ -d "$d" ]] && latest="${d##*/}"; done
  printf '%s\n' "$latest"
}

# handoff_cursor_valid <run-dir> <cursor> : names a segment of this run and an
# offset no further than its complete lines.
handoff_cursor_valid() {
  local seg off
  [[ "$2" =~ ^([0-9]{4}):([0-9]{1,15})$ ]] || return 1
  seg="${BASH_REMATCH[1]}" off="${BASH_REMATCH[2]}"
  [[ "$seg" == 0000 ]] && { [[ "$off" == 0 ]]; return; }
  [[ -f "$1/segments/$seg/events.jsonl" ]] && ((10#$off <= $(events_end "$1/segments/$seg/events.jsonl")))
}

# handoff_events_since <run-dir> <valid cursor> : one JSON object counting the
# Pi events after the cursor, across later segments too, with the cursor for
# the next call. Only event types and sanitized tool names are read; message,
# argument, and result content never leaves events.jsonl. Token deltas and
# tool updates are dropped before jq.
handoff_events_since() {
  local dir="$1" seg="${2%%:*}" off="${2#*:}" latest last_end s file start end n
  latest="$(handoff_latest_segment "$dir")"
  # Fixed before reading, so the returned cursor is exactly where this stopped.
  last_end="$(events_end "$dir/segments/$latest/events.jsonl")"
  for ((n = 10#$seg; n <= 10#$latest; n++)); do
    s="$(printf '%04d' "$n")"
    file="$dir/segments/$s/events.jsonl"
    [[ -f "$file" ]] || continue
    start=0
    [[ "$s" == "$seg" ]] && start=$((10#$off))
    end="$last_end"
    [[ "$s" == "$latest" ]] || end="$(events_end "$file")"
    ((end > start)) && tail -c "+$((start + 1))" "$file" | head -c "$((end - start))"
  done | grep -v -e '^{"type":"message_update"' -e '^{"type":"tool_execution_update"' |
    jq -R -n -c --arg cursor "$latest:$last_end" --argjson segs "$((10#$latest - 10#$seg))" '
      reduce (inputs | fromjson? | objects) as $e ({tools: {}, calls: 0, failed: 0, turns: 0, retries: 0,
          compactions: 0, dialogs: 0};
        if $e.type == "tool_execution_end" then
          .calls += 1 | .failed += (if $e.isError == true then 1 else 0 end)
          | .tools[($e.toolName | tostring | if test("^[A-Za-z0-9_.-]{1,32}$") then . else "other" end)] += 1
        elif $e.type == "turn_end" then .turns += 1
        elif $e.type == "auto_retry_start" then .retries += 1
        elif $e.type == "compaction_end" then .compactions += 1
        elif $e.type == "extension_ui_request" then .dialogs += 1
        else . end)
      | .segments_started = $segs | .cursor = $cursor
      | .tool_list = (.tools | to_entries | sort_by(-.value, .key)
          | (.[:5] | map("\(.key) \(.value)")) + (if length > 5 then ["+\(length - 5) more"] else [] end)
          | join(", "))'
}

handoff_changed_count() {
  local file="$1" wt
  wt="$(jq -r .worktree "$file")"
  [[ -d "$wt" ]] || { echo 0; return; }
  { GIT_OPTIONAL_LOCKS=0 git -C "$wt" diff --name-only "$(jq -r .snapshot "$file")" -- 2>/dev/null
    GIT_OPTIONAL_LOCKS=0 git -C "$wt" ls-files --others --exclude-standard 2>/dev/null; } | sort -u | grep -c . || true
}

# handoff_interim_line <state.json> <state> <elapsed> <events-json> <changed> <delta>
# : one bounded liveness line from local files. It reports that processes are
# alive and when they last emitted anything, never what the session wrote.
handoff_interim_line() {
  local file="$1" state="$2" runner="gone" pi="gone" pid stage="" line
  pid="$(jq -r '.runner_pid // ""' "$file")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && runner=alive
  pid="$(jq -r '.live.pi_pid // ""' "$file")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && pi=alive
  if [[ "$state" == verifying && -f "${file%/state.json}/runner.log" ]]; then
    stage="$(grep -oE 'progress: independently verifying — (plan scope|full gate)$' "${file%/state.json}/runner.log" | tail -n 1)"
    stage="${stage##*— }"
  fi
  line="$(jq -r --arg state "$state" --arg runner "$runner" --arg pi "$pi" --arg stage "$stage" \
    --argjson now "$(date +%s)" --argjson el "$3" --argjson ev "$4" --argjson ch "$5" --argjson d "$6" '
    def ago($t): if ($t | type) == "number" and $t > 0 then "\($now - $t)s ago" else "n/a" end;
    def tool: tostring | if test("^[A-Za-z0-9_.-]{1,32}$") then . else "other" end;
    # Most useful first: the 200-character bound trims the tool list.
    [ "[+\($el)s] \($state)\(if $stage != "" then " (\($stage))" else "" end): runner \($runner)\(if .live then ", pi \($pi)" else "" end)",
      (if .live then
        "last activity \(ago(.live.last_activity_at)), last event \(ago(.live.last_event_at))",
        (if (.live.active_tool // "") == "" then "no tool running"
         else "tool \(.live.active_tool | tool)\(if (.live.active_tool_since // 0) > 0 then " for \($now - .live.active_tool_since)s" else "" end)" end),
        "lease \((.live.lease_expires_at // $now) - $now)s left",
        (if (.live.checkpoint // "") != "" then "checkpoint requested" else empty end)
       else empty end),
      "changed paths \($ch) (\(if $d >= 0 then "+" else "" end)\($d))",
      ([ "since previous: \($ev.calls) tool calls\(if $ev.calls > 0 then " (\($ev.tool_list)\(if $ev.failed > 0 then "; \($ev.failed) failed" else "" end))" else "" end)",
         (if $ev.retries > 0 then "retries +\($ev.retries)" else empty end),
         (if $ev.compactions > 0 then "compactions +\($ev.compactions)" else empty end),
         (if $ev.segments_started > 0 then "segments +\($ev.segments_started)" else empty end)
       ] | join(", "))
    ] | join("; ")' "$file")"
  printf '%s\n' "${line:0:200}"
}

# handoff-status <run> [--wait <s>] [--interval <s>] [--since <cursor>]
# : token-free; reads local files only.
cmd_handoff_status() {
  local run="" wait=0 interval=0 since="" file dir state deadline now code started next_line=0
  local mark="" events changed=0 changed_now since_ok=0
  while (($#)); do
    case "$1" in
      --wait)
        (($# >= 2)) && [[ "$2" =~ ^[0-9]+$ ]] || { say "--wait requires whole seconds"; exit 2; }
        wait="$2"; shift 2 ;;
      --interval)
        (($# >= 2)) && [[ "$2" =~ ^[1-9][0-9]*$ ]] || { say "--interval requires a positive whole number of seconds"; exit 2; }
        interval="$2"; shift 2 ;;
      --since)
        (($# >= 2)) || { say "--since requires the cursor printed by a previous handoff-status"; exit 2; }
        since="$2"; shift 2 ;;
      -*) say "unknown option: $1"; exit 2 ;;
      *) run="$1"; shift ;;
    esac
  done
  [[ -n "$run" ]] || { say "usage: delegate-run.sh handoff-status <run-id> [--wait <seconds>] [--interval <seconds>] [--since <cursor>]"; exit 2; }
  require_deps jq
  file="$(state_path "$run")"
  [[ -f "$file" ]] || { say "no handoff run '$run' (no ${file#"$ROOT"/})"; exit 2; }
  dir="${file%/state.json}"
  # A bad cursor never changes the exit status, which is the run's state.
  if [[ -n "$since" ]]; then
    if handoff_cursor_valid "$dir" "$since"; then
      since_ok=1
    else
      say "note: cursor '$(bounded_progress_field "$since")' does not match this run's events; showing the usual progress lines"
    fi
  fi
  started="$(date +%s)"
  deadline=$((started + wait))
  if ((interval > 0 && wait > 0)); then
    next_line=$((started + interval))
    mark="$(handoff_latest_segment "$dir")"
    mark="$mark:$(events_end "$dir/segments/$mark/events.jsonl")"
    changed="$(handoff_changed_count "$file")"
  fi
  while :; do
    state="$(handoff_effective_state "$file")"
    code="$(handoff_state_code "$state")"
    [[ "$code" != 12 ]] && break
    now="$(date +%s)"
    ((now < deadline)) || break
    if ((next_line > 0 && now >= next_line)); then
      # Re-read the state: a runner that just finished would otherwise be
      # reported as gone in a line for a state it already left.
      state="$(handoff_effective_state "$file")"
      [[ "$(handoff_state_code "$state")" == 12 ]] || continue
      events="$(handoff_events_since "$dir" "$mark")"
      mark="$(jq -r .cursor <<<"$events")"
      changed_now="$(handoff_changed_count "$file")"
      handoff_interim_line "$file" "$state" "$((now - started))" "$events" "$changed_now" "$((changed_now - changed))"
      changed="$changed_now"
      next_line=$((now + interval))
    fi
    if ((interval > 0)); then sleep 1; else sleep 2; fi
  done
  now="$(date +%s)"
  jq -r --arg state "$state" --argjson now "$now" '
    def age($t): if ($t | type) == "number" then "\($now - $t)s ago" else "n/a" end;
    "run: \(.run)",
    "state: \($state)\(if .reason then " — \(.reason)" else "" end)\(if .forced == true and $state == "paused" then " (forced)" else "" end)",
    "spec: \(.spec) (\(.phase)); model: \(.model)",
    "worktree: \(.worktree) (branch \(.branch))",
    "segments: \(.segments | length); active time: \(.active_seconds // 0)s; started \(age(.started_at))",
    (if .max_total_seconds then "absolute ceiling: \(.max_total_seconds)s of active time" else "absolute ceiling: none" end),
    (if .live then
      "live segment \(.live.segment): last event \(.live.last_event // "none") \(age(.live.last_event_at)); last activity \(age(.live.last_activity_at))",
      "active tool: \(if (.live.active_tool // "") == "" then "none" else .live.active_tool end); retries \(.live.retries); compactions \(.live.compactions); dialogs cancelled \(.live.dialogs_cancelled)",
      "lease expires in \((.live.lease_expires_at // $now) - $now)s\(if (.live.checkpoint // "") != "" then "; checkpoint requested: \(.live.checkpoint)" else "" end)"
     else empty end)' "$file"
  if [[ "$state" == running || "$state" == orphaned || "$state" == paused ]]; then
    [[ -d "$(jq -r .worktree "$file")" ]] && say "changed paths in the worktree: $(handoff_changed_count "$file")"
  fi
  if [[ "$state" == orphaned ]]; then
    local g alive=""
    for g in $(jq -r '.live.tool_groups // [] | .[]' "$file"); do
      kill -0 -- "-$g" 2>/dev/null && alive="$alive $g"
    done
    say "the runner and Pi are gone without a final state${alive:+; tool process groups still alive:$alive}"
  fi
  if ((since_ok)); then
    # Counts since the caller's cursor replace the raw tool lines; runner
    # notes (lease, checkpoint, verification) still follow, without heartbeats.
    events="$(handoff_events_since "$dir" "$since")"
    jq -r --arg since "$since" '"since \($since): \(.calls) tool calls\(if .calls > 0 then " (\(.tool_list)\(if .failed > 0 then "; \(.failed) failed" else "" end))" else "" end); \(.turns) turns; retries \(.retries); compactions \(.compactions); dialogs \(.dialogs); segments started \(.segments_started)"' <<<"$events"
    if [[ -f "$dir/runner.log" ]]; then
      grep 'progress:' "$dir/runner.log" |
        grep -v -e 'progress: tool start:' -e 'progress: tool end:' -e 'progress: turn started' -e 'progress: session running —' |
        tail -n 5 | sed 's/^/  /'
    fi
    printf 'cursor: %s\n' "$(jq -r .cursor <<<"$events")"
  else
    if [[ -f "$dir/runner.log" ]]; then
      grep 'progress:' "$dir/runner.log" | tail -n 5 | sed 's/^/  /'
    fi
    mark="$(handoff_latest_segment "$dir")"
    printf 'cursor: %s:%s\n' "$mark" "$(events_end "$dir/segments/$mark/events.jsonl")"
  fi
  case "$state" in
    paused | orphaned) say "next: delegate-run.sh handoff-resume $run --allow-commit (or handoff-clean $run)" ;;
    green) say "next: review git diff HEAD...$(jq -r .branch "$file"), then merge with --squash" ;;
  esac
  exit "$code"
}

# handoff-stop <run> : ask a live run to checkpoint and pause.
cmd_handoff_stop() {
  local run="${1:-}" file pid
  [[ -n "$run" ]] || { say "usage: delegate-run.sh handoff-stop <run-id>"; exit 2; }
  require_deps jq
  file="$(state_path "$run")"
  [[ -f "$file" ]] || { say "no handoff run '$run'"; exit 2; }
  pid="$(jq -r '.runner_pid // ""' "$file")"
  [[ "$(handoff_effective_state "$file")" == running && "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null ||
    { say "run '$run' has no live session to stop"; exit 2; }
  kill -TERM "$pid" || { say "could not signal the runner ($pid)"; exit 3; }
  say "asked $run to checkpoint and pause; follow it with: delegate-run.sh handoff-status $run --wait 540 --interval 60"
}

# Kill process groups a dead run's tools left behind (recorded while live).
handoff_kill_leftovers() {
  local file="$1" g
  handoff_live "$file" && return 1
  for g in $(jq -r '.live.tool_groups // [] | .[]' "$file" 2>/dev/null); do
    [[ "$g" =~ ^[0-9]+$ ]] || continue
    kill -0 -- "-$g" 2>/dev/null || continue
    kill -TERM -- "-$g" 2>/dev/null
    sleep 1
    kill -KILL -- "-$g" 2>/dev/null
    say "killed leftover tool process group $g"
  done
  return 0
}

resume_refuse() { launch_report refused "$*" 2; say "refused: $*"; exit 2; }

# handoff-resume <run> --allow-commit [--max-total <s>] [--progress m] [--foreground]
cmd_handoff_resume() {
  local run="" allow_commit=0 progress_mode="" foreground=0 max_total="" file state n wt
  while (($#)); do
    case "$1" in
      --allow-commit) allow_commit=1; shift ;;
      --foreground) foreground=1; shift ;;
      --max-total)
        (($# >= 2)) && [[ "$2" =~ ^[1-9][0-9]*$ ]] || { say "--max-total requires whole seconds"; exit 2; }
        max_total="$2"; shift 2 ;;
      --progress)
        (($# >= 2)) && [[ -n "$2" ]] || { say "--progress requires a value"; exit 2; }
        progress_mode="$2"; shift 2 ;;
      -*) say "unknown option: $1"; exit 2 ;;
      *) [[ -z "$run" ]] || { say "one run per resume"; exit 2; }; run="$1"; shift ;;
    esac
  done
  [[ -n "$run" ]] || { say "usage: delegate-run.sh handoff-resume <run-id> --allow-commit [--max-total <seconds>] [--progress quiet|summary] [--foreground]"; exit 2; }
  configure_progress "$progress_mode"
  [[ "$allow_commit" == 1 ]] || {
    say "refused: a resumed handoff can still create the temporary commit on handoff/<run>; obtain explicit current-chat commit approval, then pass --allow-commit"
    exit 2
  }
  require_deps git jq awk perl curl
  file="$(state_path "$run")"
  [[ -f "$file" ]] || { say "no handoff run '$run'"; exit 2; }
  if [[ "$foreground" == 0 ]]; then
    local -a again=("$run" --allow-commit --progress "${progress_mode:-summary}" --foreground)
    [[ -n "$max_total" ]] && again+=(--max-total "$max_total")
    handoff_launch "$run" handoff-resume "${again[@]}"
  fi

  # Resume refusals leave the paused run as it was.
  state="$(handoff_effective_state "$file")"
  [[ "$state" == paused || "$state" == orphaned ]] || resume_refuse "run '$run' is $state, not paused"
  take_lock || { lock_refusal; resume_refuse "another run holds the lock"; }
  trap cleanup EXIT
  handoff_trap_stop
  handoff_scratch
  if [[ "$state" == orphaned ]]; then
    say "run '$run' was orphaned (runner and Pi gone); treating it as a forced pause"
    handoff_kill_leftovers "$file" || resume_refuse "run '$run' still has a live process"
  fi
  RUN_ID="$run"
  RESULT_DIR="$DELEGATE_DIR/results/$RUN_ID"
  SPEC_REL="$(jq -r .spec "$file")"
  TARGET="$SPEC_REL"
  PHASE="$(jq -r .phase "$file")"
  BUCKET="phase:$PHASE"
  BRANCH="$(jq -r .branch "$file")"
  SNAPSHOT_SHA="$(jq -r .snapshot "$file")"
  MODE="$(jq -r .mode "$file")"
  SELECTION_JSON="$(jq -c .selection "$file")"
  QUALIFIED="$(jq -r '.qualified // ""' "$file")"
  RUN_STARTED="$(jq -r .started_at "$file")"
  wt="$(jq -r .worktree "$file")"
  TREE="$wt"

  # -- the same logical handoff, or nothing
  # git lists physical paths (macOS: /private/var for /var).
  [[ -d "$wt" ]] && git -C "$ROOT" worktree list --porcelain | grep -qxF "worktree $(cd "$wt" && pwd -P)" ||
    resume_refuse "the worktree $wt no longer exists"
  git -C "$ROOT" rev-parse -q --verify "refs/heads/$BRANCH" >/dev/null || resume_refuse "the branch $BRANCH no longer exists"
  [[ "$(git -C "$wt" rev-parse HEAD)" == "$SNAPSHOT_SHA" &&
    "$(git -C "$wt" symbolic-ref -q HEAD)" == "refs/heads/$BRANCH" ]] ||
    resume_refuse "the worktree moved off its starting commit or branch (a session or verifier commit)"
  [[ -f "$ROOT/$SPEC_REL" && "$(sha_file "$ROOT/$SPEC_REL")" == "$(jq -r .spec_sha256 "$file")" ]] ||
    resume_refuse "the spec $SPEC_REL changed in the checkout since the handoff started"
  SPEC_SHA="$(jq -r .spec_sha256 "$file")"
  plan_files "$ROOT/$SPEC_REL" >"$STATE/plan-files"
  [[ "$(sha_file "$STATE/plan-files")" == "$(jq -r .plan_sha256 "$file")" ]] || resume_refuse "the approved plan's file list changed"
  load_handoff_harness || resume_refuse "agent harness unavailable: ${DELEGATE_HANDOFF_HARNESS:-pi}"
  load_profile "$(jq -r .profile "$file")"
  MODEL_NAME="$(jq -r .model "$file")"
  load_model "$MODEL_NAME"
  check_credential
  handoff_fingerprint || resume_refuse "invalid handoff model configuration"
  [[ "$FINGERPRINT" == "$(jq -r .fingerprint "$file")" ]] ||
    resume_refuse "the model, endpoint, request settings, Pi version, or provider configuration changed (fingerprint $FINGERPRINT, recorded $(jq -r .fingerprint "$file"))"
  [[ "$(handoff_model_config)" == "$(cat "$RESULT_DIR/pi-agent/models.json" 2>/dev/null)" ]] ||
    resume_refuse "the generated provider configuration changed"
  local session
  session="$(jq -r '.session_file // ""' "$file")"
  n="$(find "$RESULT_DIR/session" -name '*.jsonl' -type f 2>/dev/null | wc -l | tr -d ' ')"
  # A runner that died before recording its session still left exactly one.
  [[ -z "$session" && "$n" == 1 ]] && session="$(find "$RESULT_DIR/session" -name '*.jsonl' -type f)"
  [[ -n "$session" && -f "$session" && "$n" == 1 ]] || resume_refuse "expected exactly one saved Pi session in ${RESULT_DIR#"$ROOT"/}/session (found $n)"
  handoff_config
  [[ -n "$max_total" ]] && MAX_TOTAL="$max_total"
  [[ -z "$MAX_TOTAL" ]] && MAX_TOTAL="$(jq -r '.max_total_seconds // ""' "$file")"
  if [[ -n "$MAX_TOTAL" ]] && (($(jq -r '.active_seconds // 0' "$file") >= MAX_TOTAL)); then
    resume_refuse "the absolute ceiling (${MAX_TOTAL}s of active time) is spent; pass --max-total <seconds> to raise it"
  fi
  state_update '.max_total_seconds = $m' --argjson m "${MAX_TOTAL:-null}"
  # Scope and test-file audit before resuming; a violation is an integrity stop.
  audit_handoff "$wt" "$SPEC_REL" "$PHASE" "$BRANCH" "$RESULT_DIR"
  launch_report running "" 0

  handoff_resume_prompt "$SPEC_REL" >"$RESULT_DIR/resume-prompt.md"
  handoff_session_setup
  handoff_segment "$RESULT_DIR/resume-prompt.md" "$session"
  handoff_after_segment
}

# handoff-clean [<run-id> | --all] : remove handoff worktrees and their
# branches. A run id removes that one; --all removes every handoff worktree
# of this repository. Without either, lists them. Never touches the main
# working tree or any branch outside handoff/, and skips a run that is live.
cmd_handoff_clean() {
  require_deps git awk jq
  local target="${1:-}" path branch removed=0 file
  git -C "$ROOT" worktree prune
  while IFS=$'\t' read -r path branch; do
    [[ "$branch" == handoff/* ]] || continue
    file="$(state_path "${branch#handoff/}")"
    if [[ -z "$target" ]]; then
      printf '%s\t%s\t%s\n' "${branch#handoff/}" "$path" "$([[ -f "$file" ]] && handoff_effective_state "$file" || echo unknown)"
      continue
    fi
    [[ "$target" == --all || "$target" == "${branch#handoff/}" ]] || continue
    if [[ -f "$file" ]] && ! handoff_kill_leftovers "$file"; then
      say "skipped ${branch#handoff/}: its run is live (stop it with handoff-stop first)"
      continue
    fi
    git -C "$ROOT" worktree remove --force "$path" 2>/dev/null || rm -rf "$path"
    git -C "$ROOT" branch -D "$branch" >/dev/null 2>&1
    say "removed $branch ($path)"
    removed=$((removed + 1))
  done < <(git -C "$ROOT" worktree list --porcelain |
    awk '/^worktree / { p = substr($0, 10) } /^branch refs\/heads\// { print p "\t" substr($0, 19) }')
  # Branches whose worktree is already gone.
  if [[ -n "$target" ]]; then
    while IFS= read -r branch; do
      [[ -n "$branch" ]] || continue
      [[ "$target" == --all || "$target" == "${branch#handoff/}" ]] || continue
      git -C "$ROOT" branch -D "$branch" >/dev/null 2>&1 && { say "removed $branch"; removed=$((removed + 1)); }
    done < <(git -C "$ROOT" branch --list 'handoff/*' --format='%(refname:short)')
    git -C "$ROOT" worktree prune
    ((removed > 0)) || { say "no handoff worktree matches '$target'"; exit 2; }
  fi
}

require_deps() {
  local dep
  for dep in "$@"; do
    command -v "$dep" >/dev/null 2>&1 || { say "missing dependency: $dep"; exit 2; }
  done
}

case "${1:-}" in
  qualify) shift; cmd_qualify "$@" ;;
  record-review) shift; cmd_record_review "$@" ;;
  init) shift; cmd_init "$@" ;;
  discover) shift; cmd_discover "$@" ;;
  policy) shift; cmd_policy "$@" ;;
  status) shift; cmd_status "$@" ;;
  reset) shift; cmd_reset "$@" ;;
  handoff) shift; cmd_handoff "$@" ;;
  handoff-clean) shift; cmd_handoff_clean "$@" ;;
  handoff-status) shift; cmd_handoff_status "$@" ;;
  handoff-resume) shift; cmd_handoff_resume "$@" ;;
  handoff-stop) shift; cmd_handoff_stop "$@" ;;
  activate) shift; cmd_set_policy auto "$@" ;;
  deactivate) shift; cmd_set_policy off "$@" ;;
  -h | --help | "") usage; [[ -n "${1:-}" ]] ;;
  *) say "unknown subcommand: $1 (run with --help)"; exit 2 ;;
esac
