# 1.5 — Prepare Cinder storage

[Phase 1](README.md) · [Previous: USB NIC result and lesson](04-nics-and-storage.md)

**Storage step: inspect node3's SATA disk.** We review this report before creating the `cinder-volumes` storage pool.

**Current step: read-only inspection, authorized by Joe.** SSH keys, node-name resolution and Chrony on R2d2 are complete. Disk inspection and Cinder-pool creation are still pending. For a rebuild, follow the preceding chapters first.

A Cinder volume is a virtual disk you can attach to a VM. Before assigning physical storage to Cinder, we need current evidence that identifies the intended disk and shows any existing use.

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

The wrapper sends the helper through SSH and runs it with sudo on node3. It does not install the helper there. It selects your existing `~/.ssh/openstack-from-zero` private key when that regular file is available; the private key remains on R2d2. On a rebuild without that file, existing SSH authentication is used instead.

The inspection commands are:

- `lsblk` and `findmnt`: list disks, their relationships and mounts.
- Read `/proc/swaps`, `/proc/mdstat` and kernel holders: identify swap, active RAID and devices used by other block devices.
- `wipefs --no-act`: read signatures without writing.
- `pvs`, `vgs` and `lvs`, all with `--readonly`: report existing LVM physical disks, pools and logical volumes.
- `smartctl --all`: read available SMART health information, without starting a self-test.

There is no `--apply` mode. No formatting, erasing or LVM creation is included.

## 3. Run on R2d2

These commands enter the course folder, download the update, and run the inspection. Run them **on R2d2 as joe**, without putting sudo before the script. `--user joe` selects the node SSH account.

Understand the prompts before running:

- **SSH key unlock**, if requested: enter the passphrase chosen for your project private key. An already-unlocked agent key may avoid this prompt.
- **`[sudo] password for joe:`**: enter joe's Ubuntu login password **on node3**. Administrator access is needed to read complete storage information.
- **SSH account password**, if requested: enter joe's node3 login password. The wrapper can use password authentication if key authentication is unavailable.

```bash
cd ~/openstack-from-zero
git pull --ff-only
bash scripts/phase1-storage-check.sh --user joe
```

## 4. Return the report

The script prints the report path and the `cat` command to read it. Run that printed command on R2d2 and **send back that report**. Stop here before any storage changes.

`STORAGE_INSPECTION_COLLECTED` means the requested commands returned successfully. It does not mean the disk is empty or approved for use. `STORAGE_INSPECTION_NEEDS_REVIEW` means some evidence could not be collected; send that report too.

After review, we explain and approve the exact disk operation, then prepare the Cinder pool. The short LVM lesson follows the completed storage checkpoint.

## 5. Short lecture — after review

The inspection lesson will be added after Joe's actual report is reviewed. All inspection commands and rebuilding steps above will remain visible.

The planned pool is local to node3. Its volumes are not replicated across the three controllers. Keep Kolla prechecks enabled; volume access from both computes is tested in the first-VM phase.

References: [Signature inspection](https://man7.org/linux/man-pages/man8/wipefs.8.html) · [LVM reports](https://man7.org/linux/man-pages/man8/pvs.8.html) · [Kolla Cinder LVM](https://docs.openstack.org/kolla-ansible/2026.1/reference/storage/cinder-guide.html#lvm).
