# 1.6 — Create the three Cinder LVM pools

[Phase 1](README.md) · [Previous: reviewed disk inspection](05-cinder-storage.md#5-reviewed-inspection-result)

**Goal:** create one empty `cinder-volumes` storage pool on each node's reviewed SATA disk.

**Status: Joe approved independent LVM pools with an empty cluster name and the explained cluster-check exception. Ready for Joe to run; live pool creation is pending.** Read the purpose and script explanation before running. The inspection is complete; all previous commands remain available in Chapter 1.5.

## 1. Agreed Kolla design

The reviewed Kolla-Ansible **2026.1** documentation supports LVM volume groups on storage nodes. Its default Cinder check also requires a cluster name when the inventory has multiple `cinder-volume` hosts. However, Cinder's LVM driver does not support active/active clustering, and Cinder rejects a cluster name with that driver.

Three independent local LVM backends need the documented exception to **that Cinder cluster check**, with the Cinder cluster name left empty. The agreed later deployment settings are:

```yaml
cinder_cluster_name: ""
cinder_cluster_skip_precheck: true
```

These settings do not enable replication. The exception skips the Cinder cluster-name check; it does not disable the LVM volume-group check or all Kolla prechecks. **Joe approved this specific exception and the independent-pool design.** The scripts below prepare disk pools only; these Kolla settings will be applied in the later deployment-configuration chapter.

**The pool-creation step is now authorized.** Read sections 2 and 3, then run the apply command in section 4 on R2d2. The optional read-only plan command remains available.

This source review uses 2026.1 as a reference. The deployment release on rebuilt R2d2 has not been established; its installed code and rendered inventory/configuration must be checked in the management-environment phase before deployment. No working three-node Cinder deployment is claimed here.

Source review: [Kolla Cinder guide](https://docs.openstack.org/kolla-ansible/2026.1/reference/storage/cinder-guide.html) · [Kolla checks](https://github.com/openstack/kolla-ansible/blob/stable/2026.1/ansible/roles/cinder/tasks/precheck.yml) · [Kolla Cinder template](https://github.com/openstack/kolla-ansible/blob/stable/2026.1/ansible/roles/cinder/templates/cinder.conf.j2) · [LVM driver](https://github.com/openstack/cinder/blob/stable/2026.1/cinder/volume/drivers/lvm.py) · [Base driver capability](https://github.com/openstack/cinder/blob/stable/2026.1/cinder/volume/driver.py) · [Cinder cluster validation](https://github.com/openstack/cinder/blob/stable/2026.1/cinder/volume/manager.py).

## 2. Which disks will change?

| Node | SATA serial | Stable target under `/dev/disk/by-id/` |
|---|---|---|
| node1 | `50026B7766070422` | `ata-KINGSTON_SUV400S37240G_50026B7766070422` |
| node2 | `4P3521354988` | `ata-ADATA_SU630_4P3521354988` |
| node3 | `50026B768317883A` | `ata-KINGSTON_SA400S37240G_50026B768317883A` |

Each target was observed as 240,057,409,536 bytes. The script checks hostname, Ubuntu version, management IP, serial, size and SATA transport again. It does not select a disk just because its name is `/dev/sda`.

**Apply mode writes LVM metadata to these disks.** This is real storage preparation, not a simulation. Your existing Ubuntu `ubuntu-vg` and NVMe disks are excluded. The script does not mount a filesystem, create VM volumes or start Cinder.

## 3. What the script does

Run [phase1-lvm.sh](../../scripts/phase1-lvm.sh) on **R2d2 as joe**. It sends [phase1-lvm-node.py](../../scripts/phase1-lvm-node.py) through SSH and sudo to each selected node.

| Stage | Purpose |
|---|---|
| Inspect again | Check exact disk identity, partitions, mounts, fstab, swap, holders, signatures and current LVM ownership |
| Protect Ubuntu | Check the backing disks of `/`, `/boot` and `/boot/efi`; record existing system LVM identities |
| Save evidence | Before a change, save a private state record under `/var/lib/openstack-from-zero/lvm/` on that node |
| `pvcreate <stable-disk-path>` | Initialize the reviewed SATA disk as an LVM physical volume |
| `vgcreate cinder-volumes <stable-disk-path>` | Put that physical volume into a new storage pool |
| Verify | Confirm one SATA PV and zero LVs in the pool, with Ubuntu mounts and system LVM identities unchanged |
| Report | Save one private combined report under `reports/` on R2d2 |

The evidence file is **not a backup of disk contents**. The script uses no force flags, formatting or signature-erasure commands. An unexpected signature, disk owner, mount or nonempty pool stops the operation for review. It cannot automatically answer an overwrite prompt.

A matching existing empty pool is verified without recreating it. If only the selected unassigned PV exists, it can finish the VG step. It stops on the first node needing review; completed changes on earlier nodes remain. There is no automatic disk rollback. This preparation script is for the empty-pool stage, not for modifying pools after Cinder has created volumes.

Prompts: the key-unlock prompt takes your SSH-key passphrase; sudo takes joe's Ubuntu login password **on the node currently shown**. No packages are installed and no Kolla configuration is changed.

## 4. Commands on R2d2

Download the chapter and script:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

To check the plan without disk writes:

```bash
bash scripts/phase1-lvm.sh --user joe --node all
```

Expected plan marker: `RESULT: LVM_PLAN_READY for all`.

**Joe has approved the section 1 decision.** To create the three pools now, run:

```bash
bash scripts/phase1-lvm.sh --user joe --node all --apply
```

`--apply` permits the two explained LVM creation operations. Do not put sudo before this wrapper. It uses sudo only on the selected nodes. For a later single-node run, replace `all` with `node1`, `node2` or `node3`.

## 5. Return the result

Each node must show `RESULT: CINDER_LVM_READY` followed by its hostname. Final success is:

```text
RESULT: CINDER_LVM_READY for all
```

Use the printed `cat` command to display the report and send it back. Stop for review before moving to the next chapter. A created pool is not proof that OpenStack volume creation or attachment works; that is tested after deployment.

## 6. Short lecture — after review

The short lesson will follow Joe's live result. All commands above will stay visible for study and rebuilding.

Command references: [pvcreate](https://man7.org/linux/man-pages/man8/pvcreate.8.html) · [vgcreate](https://man7.org/linux/man-pages/man8/vgcreate.8.html).
