# 1.3c — Resolve node names on R2d2

[Phase 1](README.md) · [Previous: SSH keys](03b-ssh-keys.md)

**Goal:** let R2d2 find each node by its name, such as `node1`.

**Status: ready for Joe to run.** The live result is pending. Keep this procedure for rebuilding too.

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

## 5. Short lecture — after review

The short lesson will be added here after Joe's actual result is reviewed. The procedure above will stay visible.

References: [Linux hosts file](https://man7.org/linux/man-pages/man5/hosts.5.html) · [getent name lookups](https://man7.org/linux/man-pages/man1/getent.1.html).
