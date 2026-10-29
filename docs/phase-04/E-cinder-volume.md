# Chapter E — Attach a data volume and verify persistence

[Phase 4](README.md) · [Previous: VM networking](D-boot-and-test-vm.md) · [Viewing commands](../command-glossary.md)

**Status: complete — reviewed live report `phase4-volume-20261027T145218Z-_v23b1sz.txt` passed.** Chapter D's reviewed live report passed VM boot, SSH, DHCP, metadata, DNS, outbound HTTPS and floating-IP ping. The VM was on node1; the active router was on node3. Scripts read current IDs and placement rather than assuming those snapshots remain current.

## 1. Two disks, two purposes

A Cinder volume is an additional virtual disk. Think of adding a second virtual disk to a VMware VM.

| Disk | Purpose | This chapter |
|---|---|---|
| Existing 10 GiB root disk | Ubuntu and its system files, on the compute node | Kept intact |
| New 1 GiB data volume | A small independent data disk managed by Cinder | Create, attach, format, mount and test |

**1 GiB is disk capacity, not RAM.** The volume occupies one storage backend; Cinder selects that backend. It can be on the same node as the VM or another node. The report shows both locations.

```mermaid
flowchart TD
    VM["lab-vm-01"]
    ROOT["Existing local root disk: 10 GiB"]
    DATA["New Cinder data volume: 1 GiB"]
    COMPUTE["Current compute node"]
    STORAGE["Storage node chosen by Cinder"]
    VM --> ROOT
    VM --> DATA
    ROOT --> COMPUTE
    DATA --> STORAGE
```

The compute host accesses the Cinder LVM volume through iSCSI. The drawing shows storage relationships, not a packet capture. Independent LVM pools are **not replicated storage HA**. This chapter adds no Ceph or automatic VM recovery.

## 2. What formatting and mounting mean

**Formatting** creates an ext4 filesystem: the structure Linux needs to store directories and files. **Mounting** makes that filesystem accessible at a directory, here `/mnt/lab-data`.

The script does not assume that the new disk is `/dev/vdb` or `/dev/sdb`:

1. Read the guest's original disk list and identify its root filesystem before attaching anything.
2. Create one uniquely tracked `lab-data-01` volume and attach it to the existing tracked VM.
3. Match exactly one newly appeared disk's serial to Cinder's volume UUID. Virtio may expose the UUID's first 20 characters; the original disk baseline, unique match and exact 1 GiB capacity must agree too.
4. Reject original disks, unexpected partitions/devices, mounted disks, swap, active holders and filesystem signatures. Read the entire new disk and require zero-filled content.
5. Format only that identified blank data disk. Create no partition table and never run a forced format.

Unexpected evidence stops the script for review. It does not erase existing data to make a disk qualify.

## 3. What the persistence test does

| Step | Action | Evidence |
|---|---|---|
| Prepare | Check VM identity, trusted SSH key, guest tools, Cinder services/pools/type and quotas | Known working VM and suitable LVM backend |
| Attach | Create one 1 GiB volume and attach it through Nova | Cinder and Nova agree on the volume and VM IDs |
| Initialize | Perform the disk checks above; create ext4 and mount it | Known filesystem UUID and correct device |
| Write | Create `/mnt/lab-data/persistence-test.txt`; flush its bytes | Save its SHA256 checksum |
| Detach | Flush/unmount; confirm it is unmounted; ask Nova to detach | Volume becomes available and disk disappears from the guest |
| Reattach | Attach the same volume; identify it again; mount the existing filesystem | Same volume and filesystem UUID; no second format |
| Verify | Read the test file again | Same contents and SHA256 checksum |

A checksum is a fingerprint of the file's bytes. Matching checksums and exact content before/after show that the data survived this detach/reattach exercise.

The guest helper uses `ubuntu` SSH key login and `sudo -n`. It changes only the new data filesystem, its mount directory and test file. It does not install packages, change `/etc/fstab`, reboot the VM, touch node VGs or edit OpenStack configuration. Existing packages must supply the required tools.

## 4. Review the plan — run on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase4-volume.py
```

Default mode prints the plan without connections, files or resource changes.

Read the scope above before applying: **the command formats the newly created and verified blank data volume**, then runs the complete persistence test.

## 5. Apply and verify

On R2d2:

```bash
/usr/bin/python3 scripts/phase4-volume.py --apply
```

If requested, enter the existing **guest SSH-key passphrase** from Chapter C. Typing is hidden; no physical-node sudo password is expected. The SSH host key must match Chapter D's private pinned identity; this command does not replace that trust.

Keep the terminal open. API waits and guest-device visibility checks are bounded. API/guest state that does not meet the checks is reported rather than automatically repaired.

The result should end with:

```text
RESULT: CINDER_VOLUME_PERSISTENCE_VERIFIED
```

Use the exact `cat` command printed at the end and return that report. It includes the actual volume ID, storage backend/node, compute host, guest device, filesystem UUID and checksum evidence.

## 6. What remains afterward

- The same volume remains **attached and mounted** at `/mnt/lab-data`.
- The root-owned test file remains in that filesystem; the VM continues running.
- Nova's attachment is explicitly configured to retain the volume if the VM is deleted. Deleting a VM is not part of this chapter.
- No persistent mount entry is added. After a guest reboot or cold move, follow the [existing-filesystem mount steps](#after-a-guest-reboot-or-cold-move) below before checking data.
- The local root disk and the Cinder data volume remain separate. A data volume does not convert this VM into a boot-from-volume VM.

A later repeat of `--apply` on a completed exercise checks the tracked volume; it does not format it or repeat detach/reattach. Explicit verification mode reads the completed mounted volume without create, attach, detach, format or mount commands:

```bash
/usr/bin/python3 scripts/phase4-volume.py --verify
```

Private tracking is `private/phase4/volume.json`. It records each confirmed checkpoint and a pending marker before mutations. If an operation's outcome is uncertain, the marker prevents repetition. On `CINDER_VOLUME_NEEDS_REVIEW`, return the report; do not remove the marker/state or run manual format/force-detach commands. No automatic cleanup or deletion occurs. Normal reads/probes can generate logs and filesystem access metadata.

To collect the saved checkpoint and current Cinder/Nova API state, including while an operation is pending, run this observation mode on R2d2:

```bash
/usr/bin/python3 scripts/phase4-volume.py --observe
```

It authenticates normally, then uses GET requests only. It does not SSH into the guest, format, mount, attach, detach, retry a request or clear the pending marker. `CINDER_VOLUME_STATE_OBSERVED` means the API snapshot was collected; it does not mean the persistence exercise passed. Return that private report before deciding a next action.

### Explicit reattachment of the existing data volume

After reviewing the observation report and authorizing reattachment, use this mode on R2d2:

```bash
/usr/bin/python3 scripts/phase4-volume.py --reattach
```

It requires the saved checkpoint for the existing initialized volume, its original filesystem UUID and test-file checksum. Fresh Cinder/Nova reads must show the volume available with no attachments, and the guest must confirm it is absent. It records that reconciliation and one new pending attachment request before proceeding. Do not edit the private state yourself.

The command attaches that same volume once, waits for it to appear, mounts the existing ext4 filesystem and verifies the original file/checksum. It cannot create a volume, format a disk, overwrite the test file, detach a disk or reboot. If the request is uncertain, it stops without a second attempt; return the report. A confirmed attachment can continue through the remaining checks, and a completed run only verifies on repetition.

Expected success remains `CINDER_VOLUME_PERSISTENCE_VERIFIED`. The volume remains mounted at `/mnt/lab-data`.

## 7. View it yourself

On R2d2:

```bash
/usr/bin/python3 scripts/lab-view.py volumes
/usr/bin/python3 scripts/lab-view.py servers
```

In Horizon: **Project → Volumes → Volumes** shows the volume and attachment. **Project → Compute → Instances** shows the VM. Browser sign-in is a separate user-confirmed check.

From your existing SSH session **inside the VM**, these commands only inspect:

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,SERIAL
findmnt --mountpoint /mnt/lab-data
sudo cat /mnt/lab-data/persistence-test.txt
sudo sha256sum /mnt/lab-data/persistence-test.txt
```

`lsblk` lists disks, `findmnt` shows the mounted filesystem, `cat` reads the file, and `sha256sum` prints its fingerprint. Do not copy a device name from an example into a format command.

## After a guest reboot or cold move

**Mounting reconnects an existing filesystem to a directory. It does not create another disk or filesystem.** This procedure is for the tracked course volume only, after the VM is confirmed running with that original volume attached. An ERROR VM, uncertain migration or identity mismatch needs review first.

### 1. Read the saved identity — on R2d2

```bash
/usr/bin/python3 scripts/phase4-volume.py --observe
```

Read the printed report. Note the original **volume ID**, **filesystem UUID** and **SHA256**, and confirm Cinder/Nova still attach that volume to the intended VM. The filesystem UUID identifies the ext4 contents; it is different from Cinder's volume ID. This API snapshot alone does not prove the guest is running.

Connect to the VM using [Chapter D's pinned SSH command](D-boot-and-test-vm.md#7-view-and-recheck), with its verified current floating IP.

### 2. Identify the disk — inside the Ubuntu guest

```bash
lsblk -b -o NAME,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS,SERIAL
findmnt --mountpoint /mnt/lab-data
```

Require exactly one matching data disk and one occurrence of its filesystem UUID. Match all three: the saved volume ID in the disk serial (possibly its first 20 characters), capacity **1073741824 bytes**, and the saved filesystem UUID with type **ext4**. Select the data disk, not Ubuntu's root disk. A different device letter after a move is not a new volume.

If the matching disk is already mounted at `/mnt/lab-data`, skip to step 4. If `findmnt` returns no mount, its nonzero exit is expected; continue only after the identity matches and the disk has no other mount.

### 3. Mount the existing filesystem

Replace `FILESYSTEM_UUID_FROM_REPORT` below with the saved filesystem UUID. Confirm `/mnt/lab-data` is the existing empty directory. Then run **inside the guest**:

```bash
test -d /mnt/lab-data && test ! -L /mnt/lab-data &&
  lab_mount_contents=$(sudo ls -A /mnt/lab-data) &&
  test -z "$lab_mount_contents" &&
  sudo mount -t ext4 -o nodev,nosuid,noexec \
    UUID=FILESYSTEM_UUID_FROM_REPORT /mnt/lab-data
```

This is an explicit mount action. It creates no volume, formats nothing and adds no fstab entry. If it fails, return the output for review before another action.

### 4. Verify the same data

```bash
findmnt --mountpoint /mnt/lab-data --output TARGET,SOURCE,FSTYPE,UUID
sudo sha256sum /mnt/lab-data/persistence-test.txt
```

Require the original filesystem UUID and original saved SHA256. The file is root-owned, so use `sudo` to read it. A matching checksum establishes this file's contents after the mount; it does not establish storage HA. After each later guest reboot, repeat the identity check and mount only if needed.

## 8. What this proves and the agreed sequence

A passed report proves this one volume's attachment, filesystem operation and data persistence across a safe detach/reattach. It does **not** prove survival of a storage-node failure, replication, backup, reboot persistence, migration or automatic VM recovery.

The learning order remains:

1. Finish and review this Cinder volume report.
2. Continue with [Phase 5 — HA validation and VM migration](../phase-05/README.md), starting with readiness checks.
3. Review and approve each HA interruption and planned migration before execution.
4. Assess HA storage/Ceph and automatic VM recovery later. No reinstall is needed for this chapter.

## Local validation and limits

Tests simulate the full lifecycle, interrupted writes, duplicate resources, identity drift, quota gates, root-disk protection, serial matching, zero-data checks, safe unmount and checksum verification. A disposable regular image file is formatted with the exact ext4 options and exercised using local filesystem tools; no host block device or mounted lab disk is used. These are local tests, not a real Cinder attachment, iSCSI connectivity test or live data-persistence result. Joe's reviewed live report supplies attachment and detach/reattach persistence evidence; failure and migration tests remain separate.

[Volume API](https://docs.openstack.org/api-ref/block-storage/v3/) · [Nova volume attachments](https://docs.openstack.org/api-ref/compute/) · [Cinder LVM driver](https://docs.openstack.org/cinder/latest/configuration/block-storage/drivers/lvm-volume-driver.html)
