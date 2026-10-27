# E — Move the VM for planned maintenance

[Phase 5](README.md) · [Previous: virtual router](D-router.md)

**Goal:** move the existing running `lab-vm-01` from **node1 to node2**, similar to VMware vMotion. The destination was selected after reviewing live CPU, memory, disk, libvirt and Nova/Placement observations.

**Status: inspection passed; preparation/apply script locally tested. Migration pending.** Both hosts stay working throughout this exercise.

| Component | Intended result |
|---|---|
| VM memory and execution | Move to node2 |
| Local root disk | Copy to node2 through Nova block live migration |
| Existing Cinder data volume | Stay on its original node3 backend; remain attached to the same VM |
| VM identity, private IP and floating IP | Stay the same |

## 1. Prepare — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase5-move-vm.py --prepare
```

Unlock the existing guest key and enter node sudo passwords when asked. Typing is hidden. Preparation rechecks current health, the same VM/volume, data checksum, destination CPU and capacity, configured instance path and libvirt connection. It rejects an unresolved previous migration.

It also tests the installed Nova VNC XML transformation against a synthetic document and checks that migration uses it. This confirms that the destination's VNC listen address can replace the source's address. It does not edit Nova configuration or the VM.

Expected result:

```text
RESULT: VM_MIGRATION_PREPARED
```

**Return the short report before applying.** Review the node1 → node2 scope and approve the move first. The private plan expires after 30 minutes. Preparation requests no migration.

## 2. Apply the reviewed plan

Only after reviewing and approving preparation:

```bash
/usr/bin/python3 scripts/phase5-move-vm.py --apply
```

The script:

1. Rechecks the prepared VM, configuration/container identities, current source, destination capacity and CPU compatibility.
2. Records the guest boot ID and durable pending state before issuing **one** Nova `os-migrateLive` request for node2, with `block_migration: true` and API microversion 2.79. Scheduler checks remain enabled; no force option is used.
3. Polls the VM and its migration record while sampling guest network checks. Nova's migration prechecks are asynchronous; HTTP 202 alone does not mean completion.
4. Requires a completed live-migration record and the same VM ACTIVE on node2 with no task in progress.
5. Checks that the VM is active only on node2, its boot ID is unchanged, guest network and original data checksum pass, IPs and volume backend/UUID remain unchanged, and the destination VNC address matches its configuration.
6. Rechecks host/API/Horizon/database health and protected configuration/container identities before clearing the pending state.

Expected success:

```text
RESULT: VM_LIVE_MIGRATION_VERIFIED
```

Keep the terminal open and return the short report. The script observes for up to 20 minutes; individual bounded API requests may extend that window. A window ending does not cancel a migration. The VM stays on node2 after success; moving it back is a separate decision.

## 3. If the result needs review

A lost response or interrupted script does not tell us whether Nova accepted or completed the move. On R2d2, collect actual API state:

```bash
/usr/bin/python3 scripts/phase5-move-vm.py --observe
```

This uses GET requests, preserves the pending checkpoint and issues no migration/recovery action. It shows the VM/migration state, Nova fault code/message and the latest recorded live-migration action with failed-event names and exception class names when available. The original request ID was not saved, so the action is explicitly identified as the latest one for this VM. Full tracebacks and authentication text are not printed. API status alone does not establish whether the guest is running. **Return the report before another action.** If Nova completion is confirmed and a full final verification is requested:

```bash
/usr/bin/python3 scripts/phase5-move-vm.py --verify
```

Verification changes no cloud resource. It clears the local pending marker only after the completed migration and final VM/network/data/host checks pass.

Do not repeat the migration request, clear private state, force-complete, evacuate, reboot, delete source files or stop a host to make the report pass. There is no automatic rollback or move back.

For a messaging timeout, use [the read-only evidence collector](../../scripts/phase5-migration-diagnose.py) when requested. On R2d2, supply the reviewed request ID and UTC incident window:

```bash
/usr/bin/python3 scripts/phase5-migration-diagnose.py --help
```

The reviewer supplies the complete command with `--run`, `--request-id`, `--start` and `--end`. Enter each node's sudo password when asked, then return the printed private report. It collects Nova/RabbitMQ container state, bounded incident log excerpts and selected installed Nova RPC settings/source facts on all three nodes. It requests no migration, repair, service change or checkpoint update. Collection is not a diagnosis; empty excerpts and current container health do not establish the incident's cause. Older/rotated log files and broker queue health are outside this collector's scope. Keep the report private.

## Read-only inspection reference

```bash
/usr/bin/python3 scripts/phase5-migration.py --inspect
```

This separate inspection script selects no destination and has no apply mode. Its terminal/TXT report is short; full technical details are in a private sibling JSON file. `NOT CHECKED` means a prerequisite observation was unavailable, and `NOT CONFIRMED` means the query did not confirm that requirement. Collection is not migration approval.

The physical review margins are 1 GiB RAM and 2 GiB disk beyond this guest's requirement; they are not reservations. Preparation also checks Placement inventory/usage, but Nova still performs its own asynchronous scheduling/device/storage checks. The current libvirt control connection uses TCP on the management network; API HTTPS does not encrypt that separate connection, and a control read does not prove the later QEMU stream ports work.

## Limits

Network probes are samples, not exact downtime or uninterrupted-session proof. Same guest boot ID is checked to detect a guest reboot. Planned migration does not prove automatic VM recovery, host-failure recovery, storage replication or storage HA. The Cinder backend is not migrated or reformatted.

[Migration script](../../scripts/phase5-move-vm.py) · [Inspection script](../../scripts/phase5-migration.py) · [Node helper](../../scripts/phase5-migration-node.py) · [Nova live-migration API](https://docs.openstack.org/api-ref/compute/#live-migrate-server-os-migratelive-action) · [Nova graphics XML handling](https://github.com/openstack/nova/blob/34.0.0/nova/virt/libvirt/migration.py)
