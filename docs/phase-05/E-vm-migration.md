# E — VM and storage migration

[Phase 5](README.md) · [Previous: virtual router](D-router.md) · [Reboot and recovery](../reboot-and-recovery.md)

**Goal:** select an existing VM or data volume, choose where it should go, review the plan, then request one move. Run the commands **on R2d2 as joe**.

The normal route is **open menu → select → checks → review → approve → verify**. The menu loads its own tools and connection settings. No CLI activation or routine Git update is needed for it.

## 1. Choose what moves

| Menu choice | What moves | VM power | What stays |
|---|---|---|---|
| **1 — Live VM + OS disk** | VM memory and a copy of its local OS disk | Keeps running; a brief pause is possible | Attached Cinder data volumes keep their storage backends |
| **2 — Cold VM + OS disk** | VM placement and a copy of its local OS disk | Nova shuts down a running VM; expect downtime. An already shut-off VM stays off | Attached Cinder data volumes keep their storage backends |
| **3 — Attached data volume** | One Cinder data volume to another backend | VM must be running | VM compute host and local OS disk |
| **4 — Detached data volume** | One already detached, available data volume | No VM power action | The menu does not unmount or detach a disk |

For example, a VM on node3 can use a Cinder volume stored on node1. **Compute host and storage backend are different locations.** Moving either one does not replicate it or add storage HA.

Nova supports local-disk block live migration and cold migration. Cinder documents attached LVM volume migration through Nova's libvirt driver and detached volume copies. These supported workflows still need checks in this lab; support is not a successful live test. [Nova live migration](https://docs.openstack.org/nova/2026.2/admin/configuring-migrations.html) · [Nova cold migration](https://docs.openstack.org/nova/2026.2/admin/migration.html) · [Cinder migration](https://docs.openstack.org/cinder/2026.2/admin/volume-migration.html)

## 2. Understand the commands

| Command | Purpose | What it changes |
|---|---|---|
| `bash scripts/lab-migrate.sh` | Select, check and review a move; asks for `MIGRATE` before sending it | The exact approved move |
| `bash scripts/lab-migrate.sh --plan` | Practice the selections and run readiness checks | No move or tracking update |
| `bash scripts/lab-migrate.sh --observe` | Read current VM, data-volume and migration records | No move or tracking update |
| `bash scripts/lab-migrate.sh --reconcile` | Review and acknowledge one old failed menu task | Only that local tracking record, after a separate confirmation |

**`--reconcile` does not fix or retry a migration.** It checks the old result, retains the original tracking file, and closes its pending flag only when the evidence is sufficient. Use it only if an old failed task blocks a new move; the procedure is below.

<a id="1-see-what-exists--run-on-r2d2"></a>

## 3. Check readiness and inventory

The menu reads current VM names, UUIDs, power state, compute host and attached-volume backends. It also checks cluster services and destination resources before asking for approval. After a reboot, resolve service failures with [the reboot guide](../reboot-and-recovery.md) first. Its normal check is `bash scripts/lab-recover.sh --check`.

Before moving a running VM, check its console/SSH access, application/network operation and any data-file checksum you intend to compare afterward. The menu checks API and hypervisor state; **it does not check guest applications or files**. For the course data disk, use [the existing filesystem and checksum procedure](../phase-04/E-cinder-volume.md#after-a-guest-reboot-or-cold-move). Do not initialize or format it again.

<details>
<summary>CLI inventory: show VMs, disks and their locations</summary>

Load the installed CLI and private connection settings once in this terminal:

```bash
source "$HOME/venvs/openstack-client/bin/activate"
source "$HOME/openstack-from-zero/private/phase2/kolla/public-openrc.sh"
```

Then list VMs and volumes:

```bash
openstack server list --all-projects --long -c Name -c Status -c Host -c ID
openstack volume list --all-projects -c Name -c Status -c Size -c ID
openstack volume show lab-data-01   -c id -c name -c status -c size   -c os-vol-host-attr:host -c attachments
```

Replace `lab-data-01` with the selected volume's name or UUID. Use UUIDs when names are duplicated. The all-project and host fields require admin policy access. The menu operates within the authenticated course project.

| Field | Meaning |
|---|---|
| VM `Host` | Nova's assigned compute node; the menu also checks actual runtime |
| Volume `os-vol-host-attr:host` | Storage backend, such as `node3@lvm-1#lvm-1` |
| Volume `attachments` | Attached VM; an attachment's host is the compute host |

The VM's local OS disk does not appear in `volume list`. If a backend or host is unavailable, stop for review rather than infer its location.

[CLI installation and VM power controls](../openstack-cli.md) · [Short inventory tables and container view](../command-glossary.md)

</details>

<a id="1-open-the-preview-menu-on-r2d2"></a>

## 4. Open the menu and select one move

```bash
cd ~/openstack-from-zero
bash scripts/lab-migrate.sh
```

Opening the menu does not request a migration. Choose the operation, source filter, VM and destination. Enter `0` to cancel. You can enter node names instead of their menu numbers.

For a **live VM move from node3 to node2**, the selections are:

| Prompt | Select |
|---|---|
| Operation | `1` — Live VM + local OS disk |
| Source filter | Enter `node3` |
| Existing VM | Select its name and UUID |
| Destination compute | Enter `node2` |

This is an example; use the VM's freshly displayed source. The source filter lists VMs already on that node. It does not move or rename them. An empty filter returns to the source menu. Source and destination must differ.

For choices **3 or 4**, select the data volume and destination storage backend instead of a compute destination. Choice 4 lists already detached volumes; shutting down a VM does not detach its data volume.

Enter joe's sudo passwords when requested. The checks read host/container identity, runtime, CPU compatibility, physical RAM/disk headroom and Placement capacity. Storage choices also check attachment state, destination capacity and backend compatibility. A failed or unavailable check stops before submission.

## 5. Review the plan and approve

Read the displayed **operation, VM/volume UUID, source, destination and power impact**. A live VM move also states that Cinder data backends stay in place. A storage move states that compute and the OS disk stay in place.

If those details are correct, type:

```text
MIGRATE
```

The script checks fresh state again, saves a pending tracking record, then sends **one migration request**. It prints the returned request ID when available and observes progress. A pending flag means the outcome needs verification; it is not proof that Nova is still migrating.

For a **cold move**, Nova normally reaches `VERIFY_RESIZE`. After destination checks, the menu separately asks whether to confirm the new placement. Confirming completes the move and allows Nova to release the old copy. Declining leaves it awaiting confirmation. If Nova already auto-confirmed, the menu sends no extra confirmation.

## 6. Read the result and verify

| Result | Meaning / next action |
|---|---|
| `MIGRATION_API_AND_HOST_CHECKS_PASSED` | API completion, expected placement/runtime, attachment and final cluster checks passed; verify the guest next |
| `MIGRATION_PLAN_ONLY` | Preview finished; no move requested |
| `MIGRATION_PLAN_BLOCKED` | Preview found a blocker; no move requested |
| `MIGRATION_NEEDS_REVIEW` | Keep the report; read current state before selecting recovery or another request |
| `MIGRATION_STATE_OBSERVED` | State read only; tracking records remain unchanged |
| `FAILED_MENU_ATTEMPT_ACKNOWLEDGED` | One old failed local task reviewed and closed; no cloud operation performed |

The script prints `NO MIGRATION REQUEST SENT BY THIS RUN` if it stopped before sending an action. If its observation window ends, the operation may still continue; that message does not cancel it. Do not repeat an accepted or uncertain request.

Read the current API state with:

```bash
bash scripts/lab-migrate.sh --observe
```

| What you see | Meaning |
|---|---|
| `COLD VM move (migration)` with `confirmed` | Nova records a completed cold move |
| `LIVE VM move (live-migration)` with `completed` | Nova records a completed live move |
| Current operation `NONE` | Nova reports no current task |
| Local record `PENDING` | Saved task still needs review; it is separate from Nova's current operation |

**A successful cold move does not prove live migration works.** Check the operation type as well as the host. This API view does not establish actual guest runtime, data integrity or zero downtime.

After success, confirm the same VM/volume UUID and the expected locations, then check guest console/SSH, networking, applications, data-disk mount and saved checksum. After a cold move or guest reboot, the course disk may need an explicit mount of its **existing filesystem**; use the linked procedure in step 3.

<a id="run-order-for-this-lesson"></a>

Finish guest/data checks between moves. To move compute and all disks, move the VM/OS disk first, verify it, then separately move the data volume and verify it. Ceph, storage HA, automatic recovery and host-failure tests remain deferred.

## Only if an old failed task blocks the menu

A failed menu run can leave a local `menu-migration-....json` record marked PENDING. A later manual move does not automatically close it. Preserve the record; read it with `--observe` first.

**Purpose of `--reconcile`: acknowledge the old failed task, without changing the VM or its disks.** After reviewing the current state:

```bash
bash scripts/lab-migrate.sh --reconcile
```

1. Select the specific failed menu record.
2. Check the displayed VM UUID, failed migration and current placement. If the host changed, the helper requires a later completed Nova move matching it.
3. Type `ACKNOWLEDGE FAILED ATTEMPT` only for that reviewed record.
4. Require `RESULT: FAILED_MENU_ATTEMPT_ACKNOWLEDGED` before preparing a new move.

The helper checks identity/history twice and retains the original file byte for byte under `private/phase5/migration-history/`. It changes only pending/stage/resolution metadata. It accepts one unambiguous failed VM move with an idle VM, or a definite recorded HTTP rejection with no new migration. Other records are preserved. It cannot close active, uncertain, successful-but-unverified or storage-migration outcomes, and it does not repair live migration.

<details>
<summary>Older fixed-route script: move-vm-state.json</summary>

The earlier `phase5-move-vm.py` uses a separate record. Review its read-only state with:

```bash
/usr/bin/python3 scripts/phase5-move-vm.py --observe
```

Its explicit acknowledgement helper accepts only the original failed migration when that same VM is ACTIVE/idle on its original source. After reviewing that exact migration UUID, replace `FAILED_MIGRATION_UUID` below:

```bash
/usr/bin/python3 scripts/phase5-reconcile-migration.py --apply   --migration-id FAILED_MIGRATION_UUID
```

Require `RESULT: FAILED_MIGRATION_RECONCILED`. The original record is archived; no cloud request is sent. Different or changed-host outcomes need separate review. Do not delete the record to bypass it.

</details>

<details>
<summary>Technical scope and checked sources</summary>

The menu covers image-based VMs with local OS disks and ordinary unencrypted, single-attachment iSCSI data volumes without snapshots or group membership. Read-only/config-drive media block the live-disk-copy path. Volume-backed roots and other device/storage types need a separately checked plan.

The live POST uses Nova API `2.30`, matching the [Horizon 2026.2 live-migrate feature](https://github.com/openstack/horizon/blob/504dbe4ada8abda8526ba46fffc03001b50ac583/openstack_dashboard/api/microversions.py), with `block_migration: true` and scheduler validation of the selected host. Reads and cold requests use `2.79`. API `2.34` introduced asynchronous live prechecks. This client compatibility choice does **not** establish the cause or resolution of an RPC timeout. See the [Nova API reference](https://docs.openstack.org/api-ref/compute/#live-migrate-server-os-migratelive-action).

An authenticated libvirt read, healthy container or service heartbeat does not test the full migration data path or RPC response path. Nova still performs scheduling and transfer checks. A `MessagingTimeout` means the caller did not receive its expected RPC response within the wait period; the message alone does not identify the broken component.

The [oslo.messaging 18.3.1 release notes](https://docs.openstack.org/releasenotes/oslo.messaging/2026.2.html#id1) describe a reply-queue collision fix that can cause RPC timeouts. Its applicability requires the installed source/configuration and matching runtime evidence; it is not a confirmed lab diagnosis. Do not select a library/image change or timeout increase from that exception name alone.

[Cinder's API migration conditions](https://docs.openstack.org/api-ref/block-storage/v3/#migrate-a-volume) · [OpenStack CLI migration reference](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/compute/v2/index.html#server-migrate) · [Cinder volume command](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/volume/v3/index.html#volume-migrate)

</details>
