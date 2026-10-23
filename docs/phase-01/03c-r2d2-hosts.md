# 1.3c — Resolve node names on R2d2

[Phase 1](README.md) · [Previous: SSH keys](03b-ssh-keys.md)

**Goal:** let R2d2 find each node by its name, such as `node1`.

**Status: complete.** Joe's report from 2026-10-23 ends with `R2D2_HOSTS_READY`. The complete procedure remains below for study and rebuilding.

## 1. Purpose

`/etc/hosts` is a small local address book. Your inspection showed that R2d2 could reach the node IP addresses, but could not look up their names. The nodes already have the correct node-name entries.

This step adds these confirmed entries **on R2d2 only**:

```text
192.168.0.200 node1
192.168.0.201 node2
192.168.0.202 node3
```

For example, R2d2 can then translate `node1` into `192.168.0.200`. Name lookup alone does not prove that a node is reachable or that SSH login works; those were checked in earlier chapters.

## 2. What the script does

Read [phase1-hosts.py](../../scripts/phase1-hosts.py). It runs locally on **R2d2 as joe**.

| Step | What happens |
|---|---|
| Check | Confirm R2d2 and Ubuntu 24.04; read the existing hosts file |
| Preserve | Keep existing lines, including `127.0.1.1 r2d2`, localhost and IPv6 entries |
| Back up | Save the original before changing it, under `/var/lib/openstack-from-zero/hosts/` |
| Add | Add only missing node-name entries, inside a marked block |
| Verify | Use `getent ahostsv4` to check each name returns its expected IPv4 address |
| Report | Show before/after contents and save a private report in `reports/` |

An already-correct entry is kept. A conflicting node entry stops the script for review instead of being overwritten. Repeating the script does not duplicate entries. If its lookup verification fails after its own change, it restores the previous contents unless another process has since edited the file.

The script preserves the file's owner and permissions. It does not connect to the nodes or change their files. It does not change SSH keys, Chrony, NICs or DNS-server settings, and does not add R2d2's changing NAT address anywhere.

## 3. Download and run

In your **R2d2 terminal as joe**, run these three commands. They enter the repository, download this update, then apply the explained hosts-file change.

```bash
cd ~/openstack-from-zero
git pull --ff-only
python3 scripts/phase1-hosts.py --apply
```

`--apply` allows the file change. Without it, the script only shows a preview.

**Password prompt:** the script calls `sudo` because `/etc/hosts` belongs to root. If prompted, enter **joe's Ubuntu login password on R2d2**. This is not your SSH-key passphrase or a node's password. Do not put `sudo` before the command above.

## 4. Check and return the result

The lookup lines should show:

```text
node1 -> 192.168.0.200
node2 -> 192.168.0.201
node3 -> 192.168.0.202
RESULT: R2D2_HOSTS_READY
```

The script prints the private report's exact path and a `cat` command. Run that printed command and send back the report. **Stop here for review before the next configuration step.**

## 5. Reviewed result

| Check | Result from Joe's run |
|---|---|
| node1 | Resolved to `192.168.0.200` |
| node2 | Resolved to `192.168.0.201` |
| node3 | Resolved to `192.168.0.202` |
| Existing entries | localhost, `127.0.1.1 r2d2` and IPv6 lines preserved |
| Backup | Original hosts file saved privately on R2d2 |

The final marker was **`RESULT: R2D2_HOSTS_READY`**. This run changed only R2d2's hosts file. No node connection was made.

## 6. Short lecture — what your result means

R2d2 now has a local address-book entry for each node. Software using the system's name lookup can ask for `node1` and receive `192.168.0.200`.

The script used `getent ahostsv4` to check that system lookup returned the expected IPv4 address for every node. It verified name resolution; this run did not test a new SSH connection.

The hosts file answers “which IP belongs to this name?” Your SSH key answers “may joe log in?” These are separate jobs.

These entries apply only on R2d2. They do not create DNS records on the router or teach other machines how to find R2d2.

**Checkpoint complete.** Keep the full steps above. Joe has now authorized **[the next step: Chrony on R2d2](03d-r2d2-chrony.md)**.

References: [Linux hosts file](https://man7.org/linux/man-pages/man5/hosts.5.html) · [getent name lookups](https://man7.org/linux/man-pages/man1/getent.1.html).
