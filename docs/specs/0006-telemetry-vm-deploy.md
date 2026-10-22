# 0006 — Telemetry VM Deployment

**Status:** shipped
**Last updated:** 2026-10-22
**Depends on:** 0003, 0004
**Roadmap unit:** 3 (with 0003, 0004, 0005, 0007)

## Goal

Move the stack proven on the Mac (0003) to its permanent home: a `telemetry` VM
on the homelab Proxmox cluster, as `collector/README.md` designs it. Point the
ship script (0004) at it on a schedule. On the Mac the stack is a test bed: it
stops scraping when the Mac sleeps and keeps its data on one laptop. The VM keeps
scraping and holds the archive; backups come later, outside 0006 (D11).

## Success criteria

- The `telemetry` VM exists as `collector/README.md` → VM describes it: Ubuntu
  24.04 or 26.04 LTS, Docker Engine with the Compose plugin, 4 vCPU, 8 GB,
  250 GB thin disk, LAN and Tailnet only. The README records the release and
  architecture chosen, and the Docker install source. Docker's apt repository
  serves both `noble` (24.04) and `resolute` (26.04) for amd64 and arm64
  (checked 2026-10-21).
- `docker compose up -d` from `collector/` runs on the VM with the same tracked
  files as 0003. Only the untracked `.env` differs. `docker compose images`
  shows the same index digests as on the Mac.
- The data root is created with 0003's ownership steps, and no container logs
  a permission error on first start.
- **Networks.** The VM publishes its ports on its LAN IP only
  (`TELEMETRY_BIND_ADDR` = the VM's LAN address, held by a DHCP reservation). It
  runs no Tailscale: Tailnet clients reach it through the existing subnet router,
  which already advertises the LAN. A Tailnet client away from home reaches the
  OTLP and query ports, and a host outside both networks cannot (both checks
  deferred, D11). Docker bypasses
  `ufw`, so this is checked from such a host, not assumed from the firewall rules.
  The subnet router is a single point of failure for remote access; that is
  accepted here.
- **Loki history window.** `loki-config.yaml` sets
  `limits_config.reject_old_samples_max_age` to `17520h`, matching Loki's 730-day
  retention, and passes `loki -verify-config` on both entries of the pinned 3.7.9
  index. After the first ship cycle, the VM's Loki holds every ledger line in the
  landing zone (count of ledger lines in Loki, after an ingester flush, equals
  the count in the landing zone's ledgers), each at its own `ts`.
- 0003's checks pass on the VM:
  - health 200;
  - `telemetrygen` metric, log and span land in the archive and each store;
  - `up == 1` for the llama-swap job.
- `ship-ledgers.sh` runs from launchd on the Mac every 15 minutes against the
  VM. After one cycle, the VM landing zone matches the Mac's evidence. With the VM
  unreachable, the job exits 0 and the next cycle catches up.
  - **Plist.** A tracked template, `collector/mac-agent/com.example.ship-ledgers.plist`,
    runs the script every 900 s with `PROJECTS_ROOT` and `TELEMETRY_VM` set and
    its output written to a log file. It uses placeholder paths only; the owner
    copies it to `~/Library/LaunchAgents/` with real values.
  - **macOS privacy.** The owner's projects sit under a privacy-protected
    folder. The first launchd cycle shows whether the job can read it: a
    `no projects found` line means it cannot. The grant that worked (or the
    workaround) is recorded in `collector/README.md`.
  - **SSH identity.** The job uses the owner's existing SSH key. The script
    passes `-o BatchMode=yes` to `ssh` and to rsync's remote shell, so a key that
    is unavailable under launchd fails at once as a named failure instead of
    waiting on a prompt. (owner-approved with the spec, 2026-10-22)
- The inference host agent (0007) is repointed to the VM: its
  `TELEMETRY_OTLP_ENDPOINT` in the inference host's `.env` becomes the VM's
  OTLP/HTTP address, and `up{service_name="inference-models"}` appears in the VM's
  VictoriaMetrics. The Mac's `collector/.env` then goes back to
  `TELEMETRY_BIND_ADDR=127.0.0.1` (0007 D1 applied only while the stack ran on the
  Mac).
- The VM's address and ports are recorded in the vault's `ENDPOINTS.md`, never
  in a tracked file.
- `collector/README.md` → Verification records the VM bring-up date and the
  checks above.
- 0005 has shipped, so its check is repeated on the VM: `run-table
  --metrics-url` against the VM's VictoriaMetrics joins a delegate run made after
  deployment (owner decision, 2026-10-21, from the 0003 review). Accepted on the
  VM side; the status read `overlap` (D11).

## Non-goals

- Provisioning code in this repository. The owner's homelab repository creates
  the VM (an OpenTofu role, cloned from the lab's Ubuntu 24.04 template) and
  installs Docker (an Ansible role). This repository records only the result that
  `collector/README.md` → VM requires, and B1's check verifies it (2026-10-22;
  the draft said the VM would be built by hand).
- Moving data the Mac stack collected during testing. The VM starts empty; the
  landing zone refills from the ship script.
- Grafana dashboards. Their content is AGPL and never copied here.
- Collector auth, and exposure beyond LAN and Tailnet.
- Tailscale on the VM. The subnet router covers Tailnet access.
- Grafana data sources on the monitoring VM, PBS backups of the VM, and a NAS
  copy of `archive/` (owner decision D11, 2026-10-22; the draft listed them as
  success criteria).
- An easier install for the inference-host agent: a separate spec, numbered
  when drafted (owner decision, 2026-10-22). 0006 only repoints the existing agent, a one-line
  `.env` change that the inference host's sysadmin agent makes.
- A restricted SSH key for the ship job (for example `rrsync`). The owner's
  existing key is used.

## Notes

- **Out-of-repo actions need the owner:**
  - creating the VM;
  - the `.env` on it;
  - the launchd job;
  - the Grafana data sources;
  - PBS and NAS backup jobs;
  - `ENDPOINTS.md`.

  The orchestrator prepares commands and checks results. Repo changes are
  `collector/README.md`, `collector/loki-config.yaml` (re-validated),
  `collector/mac-agent/ship-ledgers.sh` (BatchMode) and the tracked plist
  template.
- **Settled at clarify (2026-10-22), from 0004 D7 and the 0003 review:**
  - Loki's window is widened to its retention (Success criteria), so a fresh VM
    backfills every ledger line into Loki.
  - The easier inference-host install is a separate spec (Non-goals).
  - One bind address, two networks: the LAN IP only; the Tailnet reaches it
    through the existing subnet router. This avoids both traps the 0003 review
    named (a Tailnet IP failing to bind when Docker starts before `tailscaled`,
    and IPv6 brackets in compose short syntax).
- **Remote mode runs for real here for the first time** (0004 tested it with
  stubs). The bring-up checks: openrsync on the Mac pushing to GNU rsync on the
  VM; transcript file modes (Claude Code writes `0600`) readable by the Collector's
  UID where it needs them (it reads only ledgers); and `nc` probing an address,
  not an SSH config alias. A project name with a space is refused in remote
  mode (D7).

## External references

- **Loki `limits_config`**: `reject_old_samples` and `reject_old_samples_max_age`.
  Authoritative source: the Grafana Loki configuration reference for 3.7.x
  (AGPL-3.0 docs: consulted and cited, never copied). The URL is pinned at plan
  time, and the value is checked by `loki -verify-config` and by the running
  Loki's `/config`.
- **launchd plist keys** (`StartInterval`, `EnvironmentVariables`,
  `StandardOutPath`, `StandardErrorPath`): Apple's `launchd.plist(5)` man page on
  the owner's Mac, read in-session at implement time.

## Standing decisions

Answered by the owner on 2026-10-22, approving the plan with every recommendation:

- **D1, stale wording.** `collector/env.example` and the `compose.yaml` header
  comment drop "LAN or Tailnet address" for the VM's LAN address, reached from
  the Tailnet through the subnet router. The compose change is comment-only and
  re-checked with `docker compose ... config --quiet`.
- **D2, Loki schema start.** No action: the earliest ledger line on the owner's
  Mac is 2026-10-14, after the schema's `from: "2026-01-01"` (checked 2026-10-22).
- **D3, VM OS.** Ubuntu 24.04 LTS (`noble`), amd64. Provisioned from the owner's
  homelab repository, not by hand (2026-10-22). The 250 GB thin disk is on
  node-local NVMe, and `/srv/telemetry` is a directory on the single root
  filesystem, not a separate mount.
- **D4, files onto the VM.** `scp` the tracked `collector/` files from the Mac;
  no repository credentials on the VM. `git clone` once the repository is public.
- **D5, macOS privacy.** The first launchd cycle runs with no grant. If the log
  shows `no projects found` or `Operation not permitted`, the owner grants Full
  Disk Access to `/bin/bash`, and the README records it with its trade-off
  (every launchd-run bash job gets it).
- **D6, the Mac stack.** Once the VM is live, `docker compose stop` on the Mac;
  its data root stays as a test bed, started on `127.0.0.1` for config trials.
- **Delegation mode: `direct`.** This session implements.

Answered by the owner on 2026-10-22, after `/review` and `/review-adversarial` of
Phase A:

- **D7, unsafe project names in remote mode.** ssh joins rsync's remote path into
  one command string that the VM's shell splits, and openrsync has no
  `--protect-args`. In remote mode a project whose directory name has characters
  other than letters, digits, `.`, `_` and `-` is skipped with one named line
  (exit 1). `--dest` mode still ships any name.
- **D8, probe timeout.** macOS `nc -w` is an idle timeout (an unreachable address
  took 75 s); the probe adds Apple's connect timeout, `nc -z -G 3 -w 3` (3.0 s).
- **D9, ssh timeouts.** The landing `mkdir` and rsync's remote shell run ssh with
  `-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15
  -o ServerAliveCountMax=4`, so a stalled link fails within about a minute
  instead of blocking later launchd cycles.
- **D10, Phase C comment updates.** After B19 returns the Mac to `127.0.0.1`,
  Phase C rewrites the "Mac binds 0.0.0.0 so the inference agent can reach it"
  comments in `collector/README.md`, `collector/env.example` and
  `collector/compose.yaml` (comment-only, re-checked with `config --quiet`),
  keeping one line of history pointing at 0007 D1.
- **D11, closing scope (owner, 2026-10-22).** 0006 ships with:
  - B20 (Grafana data sources), B21 (PBS) and B22 (NAS copy of `archive/`) out
    of scope, moved to Non-goals;
  - B10 (Tailnet and off-network reachability) deferred: the owner works on the
    home network for now;
  - B23 done: the orchestrator wrote the VM's entry in the vault's
    `ENDPOINTS.md`;
  - B24 accepted on its VM side (served model resolved; the server's tokens equal
    the session's full total). The `overlap` status comes from `run-table` not
    counting subagent usage, a spec 0001 reader gap that spec 0009 fixes.

## Approved implementation plan

**Approved:** 2026-10-22

This is the planner's plan, copied verbatim. Headings are demoted so they nest
under this section, and the repo-absolute spec path is shortened to a
repo-relative one; the planner's trailing source and path lists are omitted,
and the inference host's hostname is replaced by the role name "Host agent". The
owner approved it with D1–D6 above (D2 settled by the pre-check).

### Plan: 0006 — Telemetry VM deployment

Spec: `docs/specs/0006-telemetry-vm-deploy.md` (read on disk, clarify edits included). This plan gets copied verbatim into the tracked spec, so every address in it is a placeholder: VM `192.0.2.10`, inference host `gpu.example`, NAS `nas.example`. Real values come from the vault's `ENDPOINTS.md` and are typed only into the shell or into untracked files.

#### Files to touch
- `collector/loki-config.yaml` — under `limits_config`, add `reject_old_samples_max_age: 17520h` with a comment that it matches `retention_period` (spec 0006). Leave `reject_old_samples` at its default (true). Replace the stale header phrase "Assembled from docs; not yet run." This file is on the don't-touch list, so re-validate it on both entries of the pinned 3.7.9 index (step A4).
- `collector/mac-agent/ship-ledgers.sh` — define the ssh options once, for example `SSH_OPTS=(-o BatchMode=yes)`, and use them in two places:
  - the landing `mkdir`: `ssh "${SSH_OPTS[@]}" "$VM" mkdir -p -- …`;
  - every rsync in remote mode: `-e "ssh -o BatchMode=yes"`. In `--dest` mode rsync gets no `-e`.
  - Also: one line in `--help` (BatchMode: an unusable key fails at once, exit 1), and a hint in the `could not create …` message (ssh-agent or key).
  - Watch for bash 3.2 with `set -u`: expanding an empty array aborts. Use the two-branch call or `${arr[@]+"${arr[@]}"}`, never a bare empty `"${arr[@]}"`. CI runs bash 5 and will not catch this; only the Mac gate will.
- `collector/mac-agent/com.example.ship-ledgers.plist` (new) — XML plist with these keys:
  - `Label` `com.example.ship-ledgers`;
  - `ProgramArguments` `["/bin/bash", "/path/to/agentic-telemetry/collector/mac-agent/ship-ledgers.sh"]`;
  - `EnvironmentVariables` `{PROJECTS_ROOT: /path/to/projects, TELEMETRY_VM: telemetry.example}`;
  - `StartInterval` 900;
  - `StandardOutPath` and `StandardErrorPath` both `/path/to/logs/ship-ledgers.log`;
  - no `KeepAlive`.

  Placeholders must avoid `/home/` and `/Users/`, because the hygiene regex rejects them, so a path like `/path/to/home/...` fails. The keys are pinned from `man launchd.plist` on the Mac (A0).
- `collector/README.md`:
  - status banner;
  - VM table: release, architecture, Docker install source (Docker apt repo, `docker-ce` + `docker-compose-plugin`), LAN-IP-only network through the existing subnet router with no Tailscale on the VM, archive on local disk, backup via PBS plus a NAS rsync shown with `nas.example`;
  - Run locally: the line "The VM uses its LAN or Tailnet address" becomes LAN address only;
  - Flow step 3: the Loki window is now 730 days, so a first backfill reaches Loki;
  - Bring-up order step 7: launchd install (copy the plist, `plutil -lint`, `launchctl bootstrap`/`kickstart`), accepting the host key once, a `~/.ssh/config` `Host <address>` stanza for `User`, the privacy grant;
  - Inference host agent step 2: the endpoint is now the VM's LAN address;
  - Verification: a new 0006 block (Loki re-validation, VM bring-up date, each check), and remove "the VM deployment" from *Not yet done*.
- `collector/env.example`, `collector/compose.yaml` (header comment only) — both still say "LAN or Tailnet address". **Outside the spec's named file list, see D1.**
- `tests/test_collector_loki_config.py` (new) — pins `limits_config.reject_old_samples_max_age == "17520h"` and that it equals `retention_period`. `reject_old_samples` is absent or true, and `compactor.retention_enabled` is true.
- `tests/test_collector_launchd_plist.py` (new) — parses with `plistlib.loads(..., fmt=FMT_XML)`, which works on Linux CI; no `plutil` in tests. Pins:
  - the file is XML (`<?xml` plus the Apple DOCTYPE);
  - `Label` equals the file stem and starts with `com.example.`;
  - `StartInterval` is an `int` (not `bool`) equal to 900;
  - `ProgramArguments[0] == "/bin/bash"` and the last argument ends with `collector/mac-agent/ship-ledgers.sh`;
  - `EnvironmentVariables` holds string values for `PROJECTS_ROOT` and `TELEMETRY_VM`; `TELEMETRY_VM` ends in `.example` or is an RFC 5737 address;
  - `StandardOutPath` and `StandardErrorPath` are absolute and placeholder-only (no `/Users/`, `/home/`, `~`);
  - `KeepAlive` is absent or false.
- `tests/collector_files.py` — add a `SHIP_PLIST` path constant, and add the plist to `HYGIENE_FILES`, so the existing hygiene tests scan it.
- `tests/test_ship_ledgers_remote_shell.py` (new; `ship_ledgers_helpers.py` is at 287 lines, so put the helpers here) — uses the existing stubs (`nc` exit 0, recording `ssh` and `rsync`). Pins:
  - the one ssh call carries `-o BatchMode=yes` (or `-oBatchMode=yes`) before the host, and the first non-option argument is `telemetry.example`;
  - every remote rsync call has a remote shell, `-e VALUE` or `--rsh=VALUE`, whose `shlex.split` starts with `ssh` and contains BatchMode;
  - a `--dest` run through `write_rsync_passthrough` (real rsync) has no `-e`/`--rsh` and still copies. On the Mac this also exercises the bash 3.2 empty-array path;
  - `--help` mentions `BatchMode`.
- `tests/test_ship_ledgers_runs.py` — `test_remote_mode_creates_the_landing_directory_with_one_ssh` asserts `ssh_calls[0][0] == DEFAULT_VM`, which breaks once options come first. Change it to "the VM is the first non-option argument" (or `DEFAULT_VM in ssh_calls[0]`).
- `AGENTS.md` — the current-state block (0006 shipped, the Mac stack's role, next steps).
- `docs/specs/0006-telemetry-vm-deploy.md` — approved plan, status `shipping` → `shipped`, phase handoffs; `docs/specs/README.md` dashboard regenerated.

#### Order of operations

**Phase A — repo changes (orchestrator; reversible, in-plan)**
0. Pre-checks, read-only on the Mac:
   - (a) Find the earliest ledger `ts`: `find "$PROJECTS_ROOT" -maxdepth 6 \( -path '*/.agentic/delegate/ledger.jsonl' -o -path '*/.agentic/telemetry/*.jsonl' \) -exec jq -r '.ts // empty' {} + | sort | head -1`. If it is before `2026-01-01`, see D2.
   - (b) Fetch the Loki 3.7.x reference (External references below).
   - (c) `man launchd.plist`, for the four keys.
1. `/test-first`: the four test files above. Expected reds:
   - Loki: AssertionError on the value;
   - plist: AssertionError "expected tracked file … to exist" from `read_text` (also from the hygiene tests once the path is listed);
   - BatchMode and the edited runs test: AssertionError on argv.

   Audit that it touched `tests/` only.
2. Implement `loki-config.yaml`, `ship-ledgers.sh` and the plist; focused tests go green.
3. `plutil -lint collector/mac-agent/com.example.ship-ledgers.plist` on the Mac; record the result.
4. Re-validate Loki on both index entries, in the same form as the existing Verification entries:
   - arm64: `docker run --rm -v "$PWD/collector/loki-config.yaml:/etc/loki/config.yaml:ro" grafana/loki:3.7.9@sha256:0db216b6… -config.file=/etc/loki/config.yaml -verify-config`;
   - amd64: the same run on the amd64 manifest digest from `docker buildx imagetools inspect … --raw`, under emulation;
   - add `-print-config-stderr` once to confirm the parsed value (Loki prints `2y`).

   If D1 is approved, also run `docker compose --env-file collector/env.example -f collector/compose.yaml config --quiet`.
5. Write the README sections that don't depend on the bring-up. Run `.agentic/toolchain.sh gate`, then `/review-check`, `/review` and `/review-adversarial`, with fixes and re-reviews.
6. **Stop: commit approval** (Phase A commit). The VM should run reviewed files.

**Phase B — bring-up runbook.** Owner = owner action. Orch = the orchestrator runs it from the Mac, read-only queries only, with the owner approving any ssh. Host agent = the inference host's sysadmin agent. The orchestrator writes `VM=192.0.2.10` and the owner substitutes the real address.

| # | Step | Who | Check | Criterion |
|---|---|---|---|---|
| B1 | Create the VM (4 vCPU, 8 GB, 250 GB thin), Ubuntu per D3, DHCP reservation, Docker Engine and Compose plugin from Docker's apt repo | Owner | `lsb_release -ds; dpkg --print-architecture; docker version --format '{{.Server.Version}}'; docker compose version; apt-cache policy docker-ce \| grep -m1 http` | VM |
| B2 | Put the tracked `collector/` files on the VM (D4), `cp env.example .env` with `TELEMETRY_BIND_ADDR`=LAN IP and `TELEMETRY_ROOT=/srv/telemetry`, then the README's Linux ownership steps | Owner | `stat -c '%U:%G %a %n' /srv/telemetry/*` | data root |
| B3 | `docker compose up -d` | Owner | `docker compose ps` (all running); `docker compose logs \| grep -ci 'permission denied'` → 0 | permissions |
| B4 | Digests | Owner on the VM, Orch on the Mac | `docker image inspect --format '{{.RepoDigests}}' <each image>` on both hosts matches the README Images table. The image IDs from `docker compose images` differ by architecture unless the containerd image store is on (see Risks) | digests |
| B5 | Health | Orch | `curl -fsS -o /dev/null -w '%{http_code}\n' http://$VM:13133` → 200 | 0003 checks |
| B6 | telemetrygen smoke (the three `--profile smoke run` commands) | Owner on the VM | Orch: the README's three store queries with `127.0.0.1` replaced by `$VM`, 30 s later; owner: `zstd -dc /srv/telemetry/archive/{traces,metrics,logs}.jsonl.zst \| grep -c telemetrygen` ≥ 1 each | 0003 checks |
| B7 | Gateway scrape | Orch | `curl -s http://$VM:8428/api/v1/query --data-urlencode 'query=up{service_name="llama-swap"}'` → 1 | 0003 checks |
| B8 | Running config | Orch | `curl -s http://$VM:3100/config \| grep -E 'reject_old_samples(_max_age)?:'` → `true` and `2y` | Loki window |
| B9 | Listeners and reboot | Owner | `ss -ltnp` shows only `<LAN-IP>:{4317,4318,13133,8428,3100,3200}` (no `0.0.0.0`/`[::]`); `sudo reboot`, then `docker compose ps` is all up (the LAN IP is bound after boot) | networks |
| B10 | Reachability from outside | Owner | Mac on a phone hotspot: Tailscale on, `nc -zv -w3 $VM 4318` and `8428` succeed; Tailscale off, both fail; `nc -zv -w3 <public-IP> 4318` fails | networks |
| B11 | SSH from the Mac | Owner | accept the host key once (`ssh $VM true`); add `Host <VM-IP>` with `User` in `~/.ssh/config` (`TELEMETRY_VM` must be an address `nc` can probe, not an alias); `ssh -o BatchMode=yes $VM true` → 0 | SSH identity |
| B12 | Manual ship once | Orch | `TELEMETRY_VM=$VM PROJECTS_ROOT=… collector/mac-agent/ship-ledgers.sh; echo $?` → 0, one `shipped` line per project. This covers openrsync → GNU rsync and the project with a space in its name (see Risks) | ship |
| B13 | Install launchd | Owner | copy the plist to `~/Library/LaunchAgents/` with real values; `plutil -lint`; `launchctl bootstrap gui/$(id -u) <plist>`; `launchctl kickstart gui/$(id -u)/com.example.ship-ledgers`; `launchctl print gui/$(id -u)/com.example.ship-ledgers \| grep -E 'last exit\|run interval'` | plist |
| B14 | Privacy signal | Orch reads the log | log shows `shipped` lines. If it shows `no projects found` or `Operation not permitted` (the script itself sits in the protected folder too), go to D5; `log show --last 20m --predicate 'subsystem == "com.apple.TCC"' \| grep -i bash` names the denial. Record the grant in the README | macOS privacy |
| B15 | Landing equals Mac | Orch | `ship-ledgers.sh --dest <scratchpad>/landing`, then `find . -type f -exec shasum -a 256 {} + \| sort` there and `ssh $VM 'cd /srv/telemetry/landing/<host> && find . -type f -exec sha256sum {} + \| sort'`; `diff`. Only transcripts still being written may differ. Delete the scratch copy afterwards (it holds transcripts) | landing |
| B16 | Unreachable VM | Orch | `TELEMETRY_VM=192.0.2.1 …ship-ledgers.sh; echo $?` → one `unreachable, skipped` line, 0; then the next launchd cycle in the log ships the files created in between | offline |
| B17 | Loki equals landing | Owner flushes, Orch counts | owner: `curl -X POST http://$VM:3100/flush` (or wait ≥ 1 h). Landing count on the VM: `find /srv/telemetry/landing -mindepth 5 -maxdepth 5 \( -path '*/.agentic/delegate/ledger.jsonl' -o -path '*/.agentic/telemetry/*.jsonl' \) -exec cat {} + \| grep -c .`. Loki count: sum of `sum(count_over_time({landing_host=~".+"}[24h]))` via `query_range` at a 24 h step, in windows of 30 days or less (`max_query_length` default 721h), from a scratch script. Also `curl -s http://$VM:3100/metrics \| grep loki_discarded_samples_total` shows no `greater_than_max_sample_age` or `too_far_behind`. Spot-check one line's `ts` against its Loki timestamp. Take both counts with no delegate run in flight | Loki window |
| B18 | Repoint the inference agent | Host agent (prompt below), then Orch | `curl -s http://$VM:8428/api/v1/query --data-urlencode 'query=up{service_name="inference-models"}' -d nocache=1` returns both `model_id`s | repoint |
| B19 | Mac back to loopback | Owner | `sed -i '' 's/^TELEMETRY_BIND_ADDR=.*/TELEMETRY_BIND_ADDR=127.0.0.1/' collector/.env`, then `docker compose up -d` (or `stop`, per D6). Orch: `lsof -nP -iTCP:4318 -iTCP:8428 -sTCP:LISTEN` shows only `127.0.0.1` or nothing | Mac .env |
| B20 | Grafana data sources | Owner | VictoriaMetrics (Prometheus type) `http://$VM:8428`, Loki `:3100`, Tempo `:3200`; Explore `last_over_time(gen{service_name="telemetrygen"}[1h])` returns the series | Grafana |
| B21 | PBS | Owner | the VM in a PBS job; one task completes OK | backups |
| B22 | NAS rsync | Owner | a cron or systemd timer on the VM pushes `/srv/telemetry/archive/` to `nas.example`; restore one file to a scratch directory, then `zstd -dc <file> \| head -1 \| jq -e .`, and compare line counts with the VM copy at the time of the copy | backups |
| B23 | ENDPOINTS.md | Owner | the VM address and ports go in the vault, never in a tracked file | endpoints |
| B24 | 0005 join | Owner makes the delegate run (after B18), Orch | `uv run agentic-telemetry run-table --out data/run_table.parquet --metrics-url http://$VM:8428 <roots>` → that run's status is `joined` or `joined_approx` | 0005 |

Draft prompt for the host agent (B18): "In `/opt/agentic-telemetry/inference-agent/.env`, change only `TELEMETRY_OTLP_ENDPOINT` to `http://<VM-LAN-IP>:4318` (the owner gives you the address). Edit nothing else: not `targets.json`, llama-swap or the models. Run `docker compose up -d` in that directory. After 60 s, run the two delivery checks in step 6 of the README's inference agent section, and report `otelcol_exporter_sent_metric_points`, `otelcol_exporter_queue_size` and any `no such host` or `Exporting failed` lines. Report; don't fix."

**Phase C — record and close (orchestrator)**
1. Fill in the README's VM table values and the Verification block from the Phase B results (summaries only, no tool logs or addresses). Update the AGENTS.md current-state block. Mark the spec `shipped` and regenerate the dashboard; run `.agentic/hooks/closeout-check.sh`, the gate, and a focused `/review`.
2. **Stop: commit approval**, then the PR.

#### External references (pin at implement time)
- Loki `limits_config` (`reject_old_samples`, `reject_old_samples_max_age`, `retention_period`). Fetch https://grafana.com/docs/loki/v3.7.x/configure/ and, as backup, https://grafana.com/docs/loki/v3.7.x/operations/request-validation-rate-limits/ (it confirms the default of 1 week and the `greater_than_max_sample_age` reason). The docs are AGPL-3.0: cite the URL and retrieval date in the config comment and the README, and copy no text. Also confirm the `POST /flush` endpoint in the 3.7.x HTTP API reference before B17.
- launchd.plist(5): `man launchd.plist` on the Mac. It confirms `StartInterval <integer>` (an interval missed during sleep is skipped, not queued), `EnvironmentVariables` (string values only) and `StandardOutPath`. Cite it as the macOS man page and its retrieval date in the plist comment.
- openrsync(1) on this Mac documents `-e program` / `--rsh=program`, with RSYNC_RSH as the fallback, and says it "will generally handle quotes, but it makes no attempt to deal with escape sequences". So `-e "ssh -o BatchMode=yes"` is safe; avoid escaped quotes.

#### Risks / open questions
- **[DECISION NEEDED] D1 — stale "LAN or Tailnet" comments** in `collector/env.example` and the `compose.yaml` header, which the spec's file list doesn't name. Recommended: fix both. The compose change is comment-only; re-run `config --quiet` and record it, as 0007 did.
- **[DECISION NEEDED] D2 — ledger lines before the Loki schema's `from: "2026-01-01"`.** With a 730-day window, Loki would accept a 2025 `ts` that has no schema period, and flushing it could fail. Pre-check A0(a) settles it. If no line predates 2026-01-01, do nothing. Otherwise move `from` earlier on the empty VM; that is a schema change, so it needs its own approval and the Mac store question answered.
- **[DECISION NEEDED] D3 — Ubuntu release and architecture.** Recommended: 24.04 LTS (`noble`), amd64, which matches the README's "same as the rest of the lab".
- **[DECISION NEEDED] D4 — how the VM gets the tracked files.** Recommended: `scp` the five `collector/` files plus `inference-agent/` is not needed. This is the inference-agent pattern, with no repo credentials on the VM; `git clone` once the repo is public.
- **[DECISION NEEDED] D5 — macOS privacy (owner, at B14).** The projects and the script both sit under a protected folder, and launchd attributes access to `/bin/bash`. The options:
  - (a) Full Disk Access for `/bin/bash`: works, but every launchd-run bash job gets it;
  - (b) move `PROJECTS_ROOT` and the script copy outside protected folders;
  - (c) a dedicated wrapper binary (scope expansion).

  Recommended: try with no grant first. If the log shows the failure, take (a) and record it in the README with the trade-off.
- **[DECISION NEEDED] D6 — the Mac stack once the VM is live.** Recommended: `docker compose stop`, keeping the data root as a test bed (start it on 127.0.0.1 for config trials). Left running, it double-scrapes llama-swap's `/metrics`, which is harmless but duplicates data.
- **Backfill and Loki ordering.** The wider window removes "timestamp too old". The out-of-order limit (lines about 1 h behind their stream's newest line) remains:
  - one project's delegate ledger and its `telemetry/*.jsonl` share a stream, and the Collector reads the files concurrently;
  - today only delegate ledgers exist (scaffold Phase 1 hasn't landed), so the risk is low;
  - `loki_discarded_samples_total` in B17 names any refusal;
  - `creation_grace_period` covers only future timestamps, so it doesn't apply;
  - lines older than 730 days are refused and then removed by retention anyway.
- **openrsync → GNU rsync and paths with spaces.** openrsync builds the remote `rsync --server … <path>` line, and the remote shell may split it at a space. The spec requires a project name with a space at B12. If it lands wrong, stop: the fix is a scope change to `ship-ledgers.sh`. The likely fix is a protected-args option, if openrsync supports one, and it needs a check.
- **File modes.** `-p` keeps Claude Code's `0600` transcripts at 0600, which is fine because the Collector reads only ledgers. A `0700` `.agentic` directory or a `0600` ledger would block UID 10001: no permission error appears in the client, but B17's counts disagree and the Collector logs `permission denied`. Check: `sudo -u '#10001' find /srv/telemetry/landing -mindepth 5 -maxdepth 5 -name '*.jsonl' ! -readable`.
- **ssh-agent under launchd.** A gui-domain LaunchAgent inherits `SSH_AUTH_SOCK` from launchd. A passphrase key needs `UseKeychain`/`AddKeysToAgent` in `~/.ssh/config`. An agent that asks for approval (1Password, Secretive) may still prompt despite BatchMode: check that the first cycle finishes in seconds. BatchMode also fails on an unknown host key, which is why B11 accepts it by hand.
- **The LAN IP has to exist when Docker starts.** A DHCP reservation plus Docker's `After=network-online.target`; the B9 reboot proves it. If the address is missing, the containers fail to bind and don't retry.
- **The "same index digests" check in `docker compose images`** holds only with Docker's containerd image store (the default on fresh Engine 29 installs). Otherwise compare `RepoDigests` (B4). If neither shows the index digest, fix the spec's wording; don't work around it.
- **NAS copy of the archive file still being written.** The copy can end mid-frame, so the restore check decompresses lines and doesn't use `zstd -t`. No rotated file exists yet.
- **The launchd log file** grows without bound (about 10 lines per cycle). Acceptable; rotation is out of scope.

#### Out of scope (won't touch)
- Provisioning as code; Tailscale on the VM; Collector auth; anything exposed beyond LAN and Tailnet.
- Moving the Mac stack's data; Grafana dashboards.
- An easier inference-agent install (a separate spec); a restricted SSH key or `rrsync`.
- `otel-collector.yaml`, `tempo.yaml`, `inference-agent/*`, `mac-agent/otel-agent.yaml`, image pins, `pyproject.toml`.
- Any Python package code. `run-table` is used only as-is in B24.
- Editing the vault, `.env` files or the hosts. Those are owner or host-agent actions; the orchestrator only prepares commands and runs read-only checks.

### Review amendments to the approved plan (2026-10-22)

From `/review-adversarial` of Phase A. These replace the matching parts of the
plan above when Phase B runs. Note that `\|` inside the plan's table is a
Markdown escape; in a shell it is a plain `|`.

- **B2:** copy exactly `compose.yaml env.example otel-collector.yaml
  loki-config.yaml tempo.yaml` (never the Mac's ignored `.env`), then
  `cp env.example .env` on the VM.
- **B6:** count each signal separately, as root (the Collector writes archive
  files `0600` as UID 10001):
  `for s in traces metrics logs; do printf '%s ' $s; sudo zstd -dc /srv/telemetry/archive/$s.jsonl.zst | grep -c telemetrygen; done`.
- **B9:** check only the stack's ports, with privileges:
  `sudo ss -ltnpH '( sport = :4317 or sport = :4318 or sport = :13133 or sport = :8428 or sport = :3100 or sport = :3200 )'`;
  every line is the LAN IP, held by `docker-proxy`. (sshd on `0.0.0.0:22` and
  systemd-resolved are expected.)
- **B11:** add the `Host <VM-IP>` stanza with `User` first, then accept the
  host key (`ssh <VM-IP> true`).
- **B12:** no current project name has a space; D7 makes remote mode refuse
  such names, so B12 checks the three plain names only.
- **B13:** point the plist's log paths at a directory that exists and is not
  privacy-protected (`~/Library/Logs/`). After editing a loaded plist,
  `launchctl bootout gui/$(id -u)/com.example.ship-ledgers` before
  `bootstrap` again. If the log stays empty, read `last exit code` from
  `launchctl print gui/$(id -u)/com.example.ship-ledgers`.
- **B15:** sort with `LC_ALL=C sort` on both hosts so the lists compare.
- **B16:** expect about 3 s (D8), not 75 s.
- **B17:** one instant query covers every ledger line while the earliest is
  under 30 days old (`max_query_length` is `30d1h`):
  `sum(count_over_time({landing_host=~".+"}[30d]))`. Window only if older lines
  exist.
- **File modes (Risks):** UID 10001 has no passwd entry on the VM, so use
  `sudo setpriv --reuid=10001 --regid=10001 --clear-groups find /srv/telemetry/landing -mindepth 5 -maxdepth 5 \( -path '*/.agentic/delegate/ledger.jsonl' -o -path '*/.agentic/telemetry/*.jsonl' \) ! -readable`
  (ledgers only; Claude Code transcripts are `0600` by design). Append `2>&1`:
  it passes only when the combined output is empty, because a `0700` directory
  on the way down makes `find` print `Permission denied` and list nothing.
- **B22:** the NAS rsync job reads `archive/`, whose files are `0600` for UID
  10001, so it runs as root (or as UID 10001).
- **Phase C hygiene:** keep `<host>` literal in the Verification block (the
  landing directory is the Mac's short hostname), and summarize log lines
  instead of pasting them (they carry home paths).

### How the bring-up departed from the plan (2026-10-22)

Results are in `collector/README.md` → Verification.

- **B1:** the VM came from the homelab repository's OpenTofu role and Docker from
  its Ansible role, which also created `/srv/telemetry` with B2's ownership and
  set Docker's container log rotation. The B1 check is unchanged. The stack lives
  in `/opt/telemetry/collector`.
- **Who ran the steps:** with the owner approving each ssh and scp, the
  orchestrator ran B2–B9, B12–B17 and the `docker compose stop` half of B19. It
  wrote the VM's `.env` from `env.example`, never the Mac's `.env`; the owner set
  the Mac's `TELEMETRY_BIND_ADDR`. B13 installed the plist under the owner's own
  label, which the README now asks for. The Mac's containers also got restart
  policy `no`, so a restart does not bring them back.
- **B10:** deferred by the owner (Tailnet and off-network reachability).
- **File modes:** passes for every ledger. The check's output is not empty
  because `_sessions/claude-code` is `0700` by design; see the README.
- **B24:** no 0006 phase was left to hand off, so a throwaway spec on a local,
  never-pushed branch gave Flash Next a test-first+implement handoff. The VM side
  of the join is proven (served model resolved; server tokens equal the session's
  full total). The status read `overlap` because `run-table` does not count a
  subagent's usage, which Pi records on a tool result. That is a reader gap from
  spec 0001, fixed in spec 0009 (drafted), not a 0006 defect.
- **B20–B22:** out of scope (D11). **B23:** done; the orchestrator wrote the
  vault entry.
