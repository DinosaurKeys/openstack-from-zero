# E — VM and storage migration

[Phase 5](README.md) · [Previous: virtual router](D-router.md)

**Goal:** choose a VM and destination from numbered lists, then learn live migration, shutdown/cold migration and moving a data volume. No migration commands or VM names need to be edited.

**Status:** menu implementation ready for live readiness review. Reboot service recovery passed; that does not establish the selected VM's current runtime or data state. Source nodes, destinations and available capacity must come from fresh checks.

## What moves?

| Operation | Plain meaning | What stays in place |
|---|---|---|
| Live VM + OS disk | Move the running VM and copy its local OS disk to another compute node. A brief pause is possible. | Attached Cinder volumes keep their existing storage backends. |
| Cold VM + OS disk | Nova performs a planned shutdown/cold move and copies the local OS disk. Expect downtime. A VM already shut off remains off. | Attached Cinder volumes keep their storage backends. |
| Attached data volume | Cinder moves a selected data volume to another backend while its VM remains running. | The VM's compute node and local OS disk stay where they are. |
| Detached data volume | Cinder moves an already detached data volume to another backend. | The menu does not shut down a guest, unmount filesystems or detach volumes. |

For **compute and all storage**, do two exercises: move the VM/OS disk, verify it, then move the data volume and verify it. Finish one operation before requesting another. Storage migration moves data; it does not add replication or storage HA.

The menu covers this lab's three compute nodes, image-based VMs with local OS disks, and ordinary unencrypted, single-attachment iSCSI data volumes without snapshots or group membership. Other storage/device types need a separately checked plan. OpenStack documents an attached LVM-volume migration using Nova's libvirt driver; this does not prove that a particular live request will succeed in our lab.

## 1. See what exists — run on R2d2

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/lab-view.py servers --table --all-projects
/usr/bin/python3 scripts/lab-view.py volumes --table --all-projects
```

The VM table shows **Name, Status (Nova), Compute host and VM ID**. The volume table shows its **storage backend and attached VM ID**. A volume can be on node3 while its VM runs on node1; those are separate locations. The VM's local OS disk is not a Cinder volume.

| VM status | What to do before migration |
|---|---|
| `ACTIVE` | Candidate for a live or cold preview; the menu still verifies the actual runtime. |
| `SHUTOFF` | Candidate for a cold preview. |
| `ERROR`, unknown host, or an operation in progress | Review that VM first. Do not request a migration or reset its state to hide the problem. |

Use the UUID to distinguish duplicate VM names. These API lists are read-only. All-project views require admin policy access; the migration menu selects VMs in the authenticated course project only. Missing host/backend information is shown as unknown, not guessed.

To see **all Docker containers, including stopped ones**, on the three nodes:

```bash
bash scripts/lab-containers.sh
```

This connects to each node and prints its container names and states. Enter joe's sudo password when asked. Add `node2` to view only node2. It sends no restart commands.

**VMs are the guests you migrate. Containers run the OpenStack services.** Moving a VM does not move those service containers. `Up` in the container list is a process state; the VM's `ACTIVE` state comes from Nova. Neither is a substitute for the migration readiness checks.

For a browser view, use **Horizon → Admin → Compute → Instances**; its table includes **Host**. Use the CLI/table commands above when returning evidence for review.

<details>
<summary>Equivalent standard OpenStack CLI command</summary>

If the OpenStack CLI is already installed and authenticated with the required admin access:

```bash
openstack server list --all-projects --long \
  -c ID -c Name -c Status -c Host
```

`--long` includes the Host field in the extended list. The course's `lab-view.py` commands use the existing private HTTPS profile and need no separate CLI setup.

</details>

<a id="1-open-the-preview-menu-on-r2d2"></a>

## 2. Open the preview menu on R2d2

Once the selected VM's inventory is understood:

```bash
bash scripts/lab-migrate.sh --plan
```

`--plan` permits selections and read-only checks. It cannot send a migration. Enter `0` to cancel a selection.

The choices are:

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
3. Select the existing VM by its displayed name, state and UUID.
4. Select the destination compute node. For a data-volume operation, select the volume and destination storage backend instead.
5. Read the plan and any blocked requirement; return the short report for review.

The **source is read from OpenStack**, then checked. Selecting another source means selecting a VM currently on that source; the menu does not invent or overwrite its location. VM selection does not rename a VM. Duplicate names are distinguished by UUID.

For an attached-volume move, the VM must be ACTIVE and the volume attached only to that VM. For an offline-volume move, the volume must already be detached and `available`. Shutting down a VM alone does not detach its data volume.

## 4. Review readiness

The menu checks:

- Current VM identity/power state, actual runtime and saved migration records.
- Cluster services, APIs, agents and database membership.
- Compute service eligibility, CPU compatibility, physical RAM/local-disk headroom and Placement capacity for a VM move.
- Volume identity, attachment, backend compatibility, destination storage capacity and migration restrictions for a data-volume move.

An unresolved saved migration remains preserved and blocks another move of that VM/volume. A VM in ERROR is not silently started or reset. Review and approve any needed VM recovery separately. The preview will show the observed VM and storage state first.

These are observed prerequisites; Nova/Cinder still run their own scheduling, migration and transfer checks. A control-connection read does not prove the later disk/memory transfer will complete.

## 5. Run the reviewed move

Only after the preview and exact VM/destinations have been reviewed and approved:

```bash
bash scripts/lab-migrate.sh
```

Choose the operation and locations again. The menu uses fresh values, displays the move and its downtime, then asks:

```text
Type MIGRATE to approve, or press Enter to cancel:
```

It rechecks the selections before sending one migration request. It shows state changes while waiting. It does not automatically retry, force-complete, rename, format, detach or reset anything.

A cold move normally reaches `VERIFY_RESIZE`: Nova is waiting for acceptance of the new placement. After destination/runtime checks pass, the menu asks for a second approval to confirm it. If you decline, it leaves the move awaiting confirmation and preserves the record. Nova may auto-confirm if configured to do so; the menu observes that state rather than sending another request.

## 6. Read the result

| Result | Meaning |
|---|---|
| `MIGRATION_PLAN_ONLY` | Preview completed. No migration requested. |
| `MIGRATION_PLAN_BLOCKED` | A prerequisite needs review. No migration was requested by the preview. |
| `MIGRATION_API_AND_HOST_CHECKS_PASSED` | Nova/Cinder completion, selected destination, expected VM runtime/attachments and final cluster checks passed. |
| `MIGRATION_NEEDS_REVIEW` | Keep the report. A submitted operation may still be running; do not repeat it or delete its progress file. |

**Guest checks remain separate.** Through Horizon's console or SSH, check login, networking, the mounted data disk and the same saved file/checksum before and after each exercise. The menu's success marker does not claim guest data integrity, uninterrupted TCP sessions, zero downtime or automatic recovery after host failure.

The menu preserves existing Phase 4/5 exercise files and deployment inputs. After a reviewed migration, any course records that pin old compute/storage locations need separate reconciliation; they are not silently changed by this tool.

## Run order for this lesson

1. Read the VM/volume inventory, choose the VM, then preview its current state and resolve any readiness blocker.
2. Review and approve the live VM/OS-disk move first.
3. Check guest access, network and data.
4. Review and approve the attached data-volume move, subject to the observed backend path.
5. Check the data and its new backend.
6. Plan the cold migration as a separate exercise with approved downtime. Choose destinations from the VM's then-current location.

The earlier fixed-route script `phase5-move-vm.py` has a different saved plan and scope. The numbered menu does not replay it or clear its record.

[Menu script](../../scripts/lab-migrate.py) · [Command glossary](../command-glossary.md) · [Server list reference](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/compute/v2/index.html#server-list) · [Nova live migration](https://docs.openstack.org/nova/2026.2/admin/configuring-migrations.html) · [Nova cold migration](https://docs.openstack.org/nova/latest/contributor/resize-and-cold-migrate.html) · [Cinder volume migration](https://docs.openstack.org/cinder/2026.2/admin/volume-migration.html)
