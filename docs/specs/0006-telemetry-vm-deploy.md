# 0006 — Telemetry VM Deployment

**Status:** draft
**Last updated:** 2026-10-21
**Depends on:** 0003, 0004
**Roadmap unit:** 3 (with 0003, 0004, 0005, 0007)

## Goal

Move the stack proven on the Mac (0003) to its permanent home: a `telemetry` VM
on the homelab Proxmox cluster, as `collector/README.md` designs it. Point the
ship script (0004) at it on a schedule. On the Mac the stack is a test bed: it
stops scraping when the Mac sleeps and keeps its data on one laptop. The VM keeps
scraping, holds the archive, and is backed up.

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
- On the VM, published ports are reachable only from the LAN and Tailnet.
  Docker bypasses `ufw`, so this is checked from a host outside both, not
  assumed from the firewall rules.
- 0003's checks pass on the VM:
  - health 200;
  - `telemetrygen` metric, log and span land in the archive and each store;
  - `up == 1` for the llama-swap job.
- `ship-ledgers.sh` runs from launchd on the Mac every 15 minutes against the
  VM. After one cycle, the VM landing zone matches the Mac's evidence. With the VM
  unreachable, the job exits 0 and the next cycle catches up.
- The inference host agent (0007) is repointed to the VM: its
  `TELEMETRY_OTLP_ENDPOINT` in the inference host's `.env` becomes the VM's
  OTLP/HTTP address, and `up{service_name="inference-models"}` appears in the VM's
  VictoriaMetrics. The Mac's `collector/.env` then goes back to
  `TELEMETRY_BIND_ADDR=127.0.0.1` (0007 D1 applied only while the stack ran on the
  Mac).
- The three data sources (VictoriaMetrics, Loki, Tempo) are added to the existing
  Grafana on the monitoring VM. A query from Grafana returns the synthetic metric.
- Backups:
  - PBS backs up the VM.
  - The archive is rsynced to the NAS.
  - One restore of an archive file from the NAS is checked.
- The VM's address and ports are recorded in the vault's `ENDPOINTS.md`, never
  in a tracked file.
- `collector/README.md` → Verification records the VM bring-up date and the
  checks above.
- Only if 0005 has already shipped: `run-table --metrics-url` against the VM's
  VictoriaMetrics joins a delegate run made after deployment (0005's check,
  repeated on the VM). If 0005 ships later, 0005 runs this check against the VM
  (owner decision, 2026-10-21, from the 0003 review).

## Non-goals

- Provisioning as code (Terraform, cloud-init). The VM is created by hand
  following the README's bring-up order.
- Moving data the Mac stack collected during testing. The VM starts empty; the
  landing zone refills from the ship script.
- Grafana dashboards. Their content is AGPL and never copied here.
- Collector auth, and exposure beyond LAN and Tailnet.

## Notes

- **Out-of-repo actions need the owner:**
  - creating the VM;
  - the `.env` on it;
  - the launchd job;
  - the Grafana data sources;
  - PBS and NAS backup jobs;
  - `ENDPOINTS.md`.

  The orchestrator prepares commands and checks results. Repo changes are
  limited to `collector/README.md`, and a launchd plist template with placeholder
  paths if one is tracked. <!-- assumption: a tracked plist template under collector/mac-agent/ with placeholders -->
- If 0005 ships before this spec, its manual check runs against the Mac stack
  and is repeated here.
- **Open question: one bind address, two networks** (from the 0003 review).
  `TELEMETRY_BIND_ADDR` is a single IP, but the VM should be reachable from both
  the LAN and the Tailnet. Settle it here: publish on several addresses, or pick
  one network. Two traps to account for:
  - a Tailnet IP fails to bind if Docker starts before `tailscaled`;
  - an IPv6 value needs brackets in compose short syntax (`[addr]:4317:4317`).
