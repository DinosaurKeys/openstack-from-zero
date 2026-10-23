# Chapter I — Prepare Cinder storage

[Phase 1](README.md) · [Previous: USB NIC result and lesson](H-nics-and-storage.md)

**Storage step: inspect the SATA disks on node1, node2 and node3.** We review one combined report before preparing any Cinder storage pools.

**Current step: read-only inspection on all three nodes, authorized by Joe.** SSH keys, node-name resolution and Chrony on R2d2 are complete. **Disk inspection is complete and reviewed.** Cinder-pool creation remains pending. For a rebuild, follow the preceding chapters first.

A Cinder volume is a virtual disk you can attach to a VM. Before assigning physical storage to Cinder, we need current evidence that identifies the intended disk and shows any existing use.

![All three nodes: existing Ubuntu on NVMe and separate SATA disks for planned local Cinder pools](../images/storage-layout.svg)

Each node has its own SATA disk and its own Cinder LVM pool. Pool creation is now verified in [Chapter J](J-cinder-lvm.md#6-reviewed-creation-result). Cinder services and VM volumes are still future work; the original inspection result below describes the disks before preparation.

## 1. Which disk and why?

The intended scope is separate SATA storage on all three nodes. Ubuntu stays on its existing NVMe disks. This inspection discovers the current SATA identities; it does not assume that every machine calls its SATA disk `/dev/sda`.

| Node | Management address | Current SATA details |
|---|---|---|
| node1 | `192.168.0.200` | Kingston SUV400S37240G; serial `50026B7766070422` |
| node2 | `192.168.0.201` | ADATA SU630; serial `4P3521354988` |
| node3 | `192.168.0.202` | Kingston SA400S37240G; serial `50026B768317883A` |

The earlier node3 inventory remains below as a reference, not as a substitute for fresh discovery.

| Item | Previously observed on node3 |
|---|---|
| SATA disk | `/dev/sda`, KINGSTON SA400S3 |
| Serial | `50026B768317883A` |
| Capacity | 240 GB — `240057409536` bytes |
| Ubuntu root | NVMe → Ubuntu LVM → `/` |

Previously observed stable identifier for node3:

`/dev/disk/by-id/ata-KINGSTON_SA400S37240G_50026B768317883A`

The earlier node3 inventory showed no filesystem, partitions or mounts on that disk. The updated collector lists current physical disks, selects devices whose reported transport is SATA, and records their model, serial, size and stable identifiers. It rechecks each identity during collection. If transport or identity is unclear, it reports that for review rather than guessing.

Administrator checks inspect existing use and health evidence before making a storage change. NVMe disks appear in the inventory so we can identify Ubuntu's storage, but they are not selected as SATA candidates.

## 2. What this script does

Run the [phase1-storage-check.sh](../../scripts/phase1-storage-check.sh) on **R2d2**. It connects to **node1, node2 and node3** in order, using `joe` and sudo.

| Check | Why we need it |
|---|---|
| Hostname, SATA transport, model, serial, size and stable identifiers | Record the actual machine and disks |
| Block devices, Ubuntu mounts and fstab | Show where Ubuntu lives and any configured disk use |
| Swap, RAID, holders and existing LVM | Show storage already in use |
| `wipefs --no-act` | Read filesystem/partition/RAID signatures; `--no-act` prevents writes |
| `smartctl --all`, if installed | Read existing disk health data; starts no self-test |

It uses the [phase1-storage-node.py helper](../../scripts/phase1-storage-node.py), installs no packages, and changes no disk configuration. It saves one private report under `reports/` on R2d2. Missing tools or failed commands are recorded for review.

The wrapper sends the helper through SSH and runs it with sudo on each selected node. It does not install the helper there. It selects your existing `~/.ssh/openstack-from-zero` private key when that regular file is available; the private key remains on R2d2. On a rebuild without that file, existing SSH authentication is used instead.

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
- **`[sudo] password for joe:`**: enter joe's Ubuntu login password **on the node currently shown**. Administrator access is needed to read complete storage information.
- **SSH account password**, if requested: enter joe's login password for the node currently shown. The wrapper can use password authentication if key authentication is unavailable.

```bash
cd ~/openstack-from-zero
git pull --ff-only
bash scripts/phase1-storage-check.sh --user joe
```

The command above now inspects **all three nodes by default**. To make that scope explicit, this equivalent command is available:

```bash
bash scripts/phase1-storage-check.sh --user joe --node all
```

You only need to run one version. For a later inspection of one node, use `--node node1`, `--node node2` or `--node node3`.

## 4. Return the report

The script prints the report path and the `cat` command to read it. Run that printed command on R2d2 and **send back that report**. Stop here before any storage changes.

The report contains a separate section and collection status for each node, then one combined result. Collection continues to the other nodes if one node needs review.

`STORAGE_INSPECTION_COLLECTED` means the requested commands returned successfully on every selected node. It does not mean the disk is empty or approved for use. `STORAGE_INSPECTION_NEEDS_REVIEW` means some evidence could not be collected; send that report too.

After review, we explain and approve the exact disk operation, then prepare the Cinder pool. The short LVM lesson follows the completed storage checkpoint.

## 5. Reviewed inspection result

Joe's combined report `phase1-storage-20261023T105018Z-y9zYak.txt` ends with **`RESULT: STORAGE_INSPECTION_COLLECTED`**. Every selected node returned `COLLECTED`.

| Check | Observed on all three nodes |
|---|---|
| SATA disk | `/dev/sda`, exactly `240057409536` bytes per node |
| Partitions and mounts on SATA | None detected |
| SATA filesystem/RAID signatures | None reported by `wipefs --no-act` |
| SATA kernel holders | None |
| Existing LVM | Only `ubuntu-vg` on `/dev/nvme0n1p3`; SATA is not an existing PV |
| Ubuntu root and boot | Backed by NVMe |
| Active swap | `/swap.img` file |
| Active kernel RAID arrays | None listed |
| SMART overall assessment | `PASSED` on all three SATA disks |

Confirmed SATA identifiers:

| Node | Stable disk path |
|---|---|
| node1 | `/dev/disk/by-id/ata-KINGSTON_SUV400S37240G_50026B7766070422` |
| node2 | `/dev/disk/by-id/ata-ADATA_SU630_4P3521354988` |
| node3 | `/dev/disk/by-id/ata-KINGSTON_SA400S37240G_50026B768317883A` |

**Health detail retained:** node1 reports `Runtime_Bad_Block` raw value `11`, alongside SMART overall `PASSED` and `Current_Pending_Sector = 0`. No vendor-specific interpretation is assumed. Each disk's existing extended self-test log records a completed test without error; this inspection did not start a new test. SMART results are observations, not a guarantee against future failure.

No disk configuration was changed. No `cinder-volumes` pool exists in this report.

## 6. Short lecture — what this result means

The inspection found no current use of these SATA disks in the mounts, signatures, RAID, holders and LVM information collected. This is stronger evidence than checking the disk name alone, but it is not a claim that every byte is blank.

Ubuntu's disks are separate: the NVMe disk contains partitions and the existing `ubuntu-vg` pool. The SATA disk is the candidate for a different pool named `cinder-volumes`.

In LVM terminology, a **physical volume (PV)** is a disk or partition prepared for LVM. A **volume group (VG)** combines that space into a pool. **Logical volumes (LVs)** are virtual disks allocated from the pool.

The intended next preparation would create one SATA-backed pool on each node. Those would be three separate local pools, with no automatic replication between them. The provisioning script and its Kolla requirement are now explained in **[Chapter J — Create the three Cinder LVM pools](J-cinder-lvm.md)**. Read its pending decision before applying. All inspection commands above remain available for rebuilding.

The intended layout uses a separate local LVM pool on each storage node. These pools do not automatically replicate volumes between nodes. Before provisioning, we must validate the Kolla configuration for separate LVM backends; the Kolla guide flags limitations in multi-controller configurations. Keep Kolla prechecks enabled. Volume access from both computes is tested in the first-VM phase.

References: [Signature inspection](https://man7.org/linux/man-pages/man8/wipefs.8.html) · [LVM reports](https://man7.org/linux/man-pages/man8/pvs.8.html) · [Kolla Cinder LVM](https://docs.openstack.org/kolla-ansible/2026.1/reference/storage/cinder-guide.html#lvm).
