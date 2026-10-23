# 1.5 — Prepare Cinder storage

[Phase 1](README.md) · [Previous: USB NIC result and lesson](04-nics-and-storage.md)

**Storage step: inspect node3's SATA disk.** We review this report before creating the `cinder-volumes` storage pool.

For the current lab, first complete and review [1.3a — SSH keys, time and host names](03a-access-state.md), then return to these storage steps.

![Ubuntu uses NVMe; the separate SATA disk is planned for Cinder volumes](../images/storage-layout.svg)

## 1. Which disk and why?

Cinder will use node3's separate SATA disk to supply persistent VM volumes. Ubuntu stays on its existing NVMe disk.

| Item | Previously observed on node3 |
|---|---|
| SATA disk | `/dev/sda`, KINGSTON SA400S3 |
| Serial | `50026B768317883A` |
| Capacity | 240 GB — `240057409536` bytes |
| Ubuntu root | NVMe → Ubuntu LVM → `/` |

The script locates the SATA disk using this stable identifier, then checks its serial and size again:

`/dev/disk/by-id/ata-KINGSTON_SA400S37240G_50026B768317883A`

The earlier inventory showed no filesystem, partitions or mounts on it. Administrator checks now let us inspect its existing use and health evidence before making a storage change.

## 2. What this script does

Run the [phase1-storage-check.sh](../../scripts/phase1-storage-check.sh) on **R2d2**. It connects only to **node3**, using `joe` and sudo.

| Check | Why we need it |
|---|---|
| Hostname, disk serial and size | Confirm the intended machine and disk |
| Block devices, Ubuntu mounts and fstab | Show where Ubuntu lives and any configured disk use |
| Swap, RAID, holders and existing LVM | Show storage already in use |
| `wipefs --no-act` | Read filesystem/partition/RAID signatures; `--no-act` prevents writes |
| `smartctl --all`, if installed | Read existing disk health data; starts no self-test |

It uses the [phase1-storage-node.py helper](../../scripts/phase1-storage-node.py), installs no packages, and changes no disk configuration. It saves one private report under `reports/` on R2d2. Missing tools or failed commands are recorded for review.

## 3. Run on R2d2

These commands enter the course folder, download the update, and run the inspection. `--user joe` selects the node SSH account. SSH and sudo may ask for node3's password.

```bash
cd ~/openstack-from-zero
git pull --ff-only
bash scripts/phase1-storage-check.sh --user joe
```

## 4. Return the report

The script prints the report path and the `cat` command to read it. **Send back that report.**

`STORAGE_INSPECTION_COLLECTED` means the requested commands returned successfully. It does not mean the disk is empty or approved for use. `STORAGE_INSPECTION_NEEDS_REVIEW` means some evidence could not be collected; send that report too.

After review, we explain and approve the exact disk operation, then prepare the Cinder pool. The short LVM lesson follows the completed storage checkpoint.

The planned pool is local to node3. Its volumes are not replicated across the three controllers. Keep Kolla prechecks enabled; volume access from both computes is tested in the first-VM phase.

References: [Signature inspection](https://man7.org/linux/man-pages/man8/wipefs.8.html) · [LVM reports](https://man7.org/linux/man-pages/man8/pvs.8.html) · [Kolla Cinder LVM](https://docs.openstack.org/kolla-ansible/2026.1/reference/storage/cinder-guide.html#lvm).
