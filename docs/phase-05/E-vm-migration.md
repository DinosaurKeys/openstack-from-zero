# E — VM and storage migration

[Phase 5](README.md) · [Previous: virtual router](D-router.md) · [Reboot and recovery](../reboot-and-recovery.md)

**Goal:** see where the VM and its disks are, choose a destination, then move one thing at a time. Use the CLI for clear inventory tables and the numbered menu to preview and request a reviewed move.

**Order:** services healthy → read VM/storage locations → preview → approve → verify. After a reboot, finish [service recovery](../reboot-and-recovery.md) first. A healthy cluster does not establish a VM's runtime or data state.

## What moves?

| Choice | What moves | What stays in place |
|---|---|---|
| Live VM + OS disk | Running VM and a copy of its local OS disk; a brief pause is possible | Attached Cinder data volumes keep their storage backends |
| Cold VM + OS disk | Nova shuts down a running VM and copies its local OS disk; expect downtime | Attached data volumes keep their storage backends; an already shut-off VM remains off |
| Attached data volume | Cinder requests a move to another storage backend while the VM is running | VM compute location and local OS disk |
| Detached data volume | Cinder moves an already detached, available volume | The menu does not unmount or detach a guest disk |

To move **compute and all disks**, move the VM/OS disk, verify it, then move the data volume and verify it. Moving a volume does not replicate it or add storage HA.

<a id="1-see-what-exists--run-on-r2d2"></a>

## 1. Read the services and inventory — on R2d2

Check services with the same command used after a reboot:

```bash
cd ~/openstack-from-zero
bash scripts/lab-recover.sh --check
```

It includes the **compute-service, network-agent and volume-service tables** automatically. If you already have a current healthy check, continue below. Otherwise, resolve its named failures using [the reboot guide](../reboot-and-recovery.md) before planning a move.

For VM/storage inventory, activate the CLI and load the existing connection settings once in this terminal:

```bash
source "$HOME/venvs/openstack-client/bin/activate"
source "$HOME/openstack-from-zero/private/phase2/kolla/public-openrc.sh"
```

[Phase 3 D](../phase-03/D-client-access.md) covers CLI installation. Keep credentials private. The migration preview still checks actual VM runtime and destination capacity.

**List VMs and their compute hosts:**

```bash
openstack server list --all-projects --long -c Name -c Status -c Host -c ID
```

**List Cinder volumes:**

```bash
openstack volume list --all-projects -c Name -c Status -c Size -c ID
```

For the course data volume, read its storage backend and attachment:

```bash
openstack volume show lab-data-01 \
  -c id -c name -c status -c size \
  -c os-vol-host-attr:host -c attachments
```

`lab-data-01` is the course example; for another volume use its name or UUID from the list. UUIDs distinguish duplicate names. Host/backend visibility and all-project lists require admin policy access. The menu selects VMs in the authenticated course project.

| Field | Meaning |
|---|---|
| VM `Host` | Compute node assigned in Nova; the preview also checks actual runtime |
| Volume `os-vol-host-attr:host` | Storage backend, such as `node3@lvm-1#lvm-1` |
| Volume `attachments` | VM UUID and guest device attached to that volume; an attachment's host is the compute host |

A VM on node1 can use a data volume stored on node3. The VM's **local OS disk is not a Cinder volume** and does not appear in `volume list`.

If a host or backend field is missing, return the output for review; do not infer the storage host from an attachment's compute host.

| VM status | Next step |
|---|---|
| `ACTIVE` | Candidate for a live or cold preview; actual runtime must agree |
| `SHUTOFF` | Candidate for a cold preview |
| `ERROR`, unknown host, locked, or an operation in progress | Review the VM before migration; do not reset its status to bypass the problem |

Normal start/shutdown commands are in the [CLI VM reference](../openstack-cli.md#4-start-or-shut-down-a-vm); [Horizon controls and read-only recovery checks](../command-glossary.md#vm-power-controls) are also available.

<details>
<summary>Other inventory views: short tables and service containers</summary>

These existing helpers also show VM/volume locations, using the private API profile:

```bash
/usr/bin/python3 scripts/lab-view.py servers --table --all-projects
/usr/bin/python3 scripts/lab-view.py volumes --table --all-projects
bash scripts/lab-containers.sh
```

The last command reads Docker containers on all three nodes over SSH; enter joe's sudo passwords. Add `node2` to select only node2. It includes stopped containers and sends no restart commands. VMs are the guests being migrated; these containers run OpenStack services. Horizon also shows VM hosts under **Admin → Compute → Instances**.

</details>

<a id="1-open-the-preview-menu-on-r2d2"></a>

## 2. Open the preview menu on R2d2

```bash
bash scripts/lab-migrate.sh --plan
```

This is read-only: it lets you select an operation and checks readiness without requesting a move. Enter `0` to cancel.

```text
1) Live VM + local OS disk
2) Cold VM + local OS disk
3) Move an attached data volume
4) Move an already detached data volume
0) Cancel
```

## 3. Select the VM and locations

1. Choose the operation.
2. Choose a source-node filter, or all nodes.
3. Select the VM by its displayed name, state and UUID.
4. Choose a destination compute node. For a volume move, choose the volume and destination storage backend instead.
5. Read the plan and return its short report for review.

The actual source comes from OpenStack and is checked. Selecting a source filter does not change a VM's location or rename it. For an attached-volume move the VM must be ACTIVE and that volume attached only to it. For a detached-volume move the volume must already be `available`; shutting down a VM does not detach its disk.

## 4. Review readiness

The preview checks VM identity/actual runtime, healthy cluster services, CPU compatibility, physical RAM/disk space, Placement capacity, volume attachments and destination backend capacity/compatibility as applicable.

**`up/enabled` alone does not prove migration readiness.** Nova/Cinder still perform their own scheduling and transfer checks. A saved unfinished migration or a VM in ERROR blocks another move; review its recovery separately and preserve the record.

The menu covers image-based VMs with local OS disks on these three compute nodes, and unencrypted, single-attachment iSCSI data volumes without snapshots or group membership. Other root-disk/storage/device types require a separately checked plan.

## 5. Run the reviewed move

After approval of the exact previewed operation, VM/volume and destination:

```bash
bash scripts/lab-migrate.sh
```

Select the operation and locations again. The menu checks fresh values, shows the move and expected downtime, then asks you to type **`MIGRATE`**. It sends one migration request and displays progress. It does not automatically retry or force-complete a failed/uncertain request.

A cold move normally reaches `VERIFY_RESIZE`: after checking the destination, the menu asks separately to confirm the new placement. Declining leaves it awaiting confirmation. If Nova has already auto-confirmed, the menu observes that result without confirming again.

## 6. Read the result

| Result | Meaning |
|---|---|
| `MIGRATION_PLAN_ONLY` | Preview completed; no move requested |
| `MIGRATION_PLAN_BLOCKED` | A prerequisite needs review; the preview requested no move |
| `MIGRATION_API_AND_HOST_CHECKS_PASSED` | API completion, destination, runtime/attachments and final cluster checks passed |
| `MIGRATION_NEEDS_REVIEW` | Keep the report/progress record; an operation may still run, so do not repeat it |

After a successful move, repeat the VM list and the selected volume's `show` command from step 1:

| Move | What to confirm |
|---|---|
| VM/OS disk | Same VM UUID, expected compute `Host`; data-volume backend unchanged |
| Data volume | Same volume UUID and size, expected storage backend; compute host unchanged |

Also check **inside the guest**: console/SSH access, networking, the data disk's mount and the same saved file/checksum from before the move. After a guest reboot or cold move, the course data filesystem may need an explicit mount: use [the existing-filesystem mount and checksum steps](../phase-04/E-cinder-volume.md#after-a-guest-reboot-or-cold-move). API/host success does not establish guest data integrity or zero downtime.

<a id="run-order-for-this-lesson"></a>

Finish and verify each exercise before the next: **live VM/OS disk → attached data volume → separate cold-migration exercise**. Guest checks belong between moves. Storage HA, automatic VM recovery and host-failure tests remain separate.

The menu preserves existing Phase 4/5 records and deployment inputs. Records that pin old locations require separate reconciliation after a reviewed migration. The earlier fixed-route `phase5-move-vm.py` has its own scope/record; this menu does not replay or clear it.

[Menu script](../../scripts/lab-migrate.py) · [Command glossary](../command-glossary.md) · [Nova migration](https://docs.openstack.org/nova/2026.2/admin/configuring-migrations.html) · [Nova cold migration](https://docs.openstack.org/nova/latest/contributor/resize-and-cold-migrate.html) · [Cinder volume migration](https://docs.openstack.org/cinder/2026.2/admin/volume-migration.html)
