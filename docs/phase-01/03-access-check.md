# 1.3 — Check access from R2d2

[Phase 1](README.md) · [Previous: Ubuntu nodes](02-ubuntu-nodes.md)

**Purpose:** test the real NAT-to-node path and collect facts before writing network or disk configuration.

Run these commands **on R2d2**, using its console.

## A. Download the course once

Git downloads and updates this repository. These package commands install Git and the SSH client as Ubuntu system packages on R2d2, together with their required dependencies.

```bash
sudo apt update
sudo apt install --yes git openssh-client
git clone https://github.com/DinosaurKeys/openstack-from-zero.git "$HOME/openstack-from-zero"
cd "$HOME/openstack-from-zero"
```

If you already cloned this repository at that path, enter it and run `git pull --ff-only` instead of cloning it again. The course directory stays at `~/openstack-from-zero`.

## B. Check the management connection

The [inspection script](../../scripts/phase1-check.py) uses Python's standard library; it needs no pip packages or virtual environment.

| What it does | Where |
|---|---|
| Read hostname, Ubuntu version, addresses, routes and interface/disk facts | R2d2 |
| Try a TCP connection to port 22 on .200, .201 and .202 | From R2d2 |
| Save one combined private report | `reports/` inside this checkout |

It makes no node configuration changes. It does not install packages, change NICs or create LVM volumes.

```bash
python3 scripts/phase1-check.py
```

**Success check:** `LOCAL_AND_TCP_CHECKS_PASSED`. Each node accepted a TCP connection to its SSH port. This has not tested node login yet.

If a connection fails, keep its result for review. Do not change NAT settings based only on an assumption.

## C. Read the nodes through SSH

This is the same inspection script with node login enabled. Replace `joe` only if your node account has a different name.

```bash
python3 scripts/phase1-check.py --ssh --user joe
```

It connects once to each node. Password login is supported. On a first connection, compare the displayed SSH fingerprint with the node's console before accepting it. To display that fingerprint at the node console, use `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.

The script uses normal SSH host-key checking. A changed-key warning is a checkpoint to review, rather than something the script removes.

**Success check:** `NODE_IDENTITY_CHECKS_PASSED`. It means SSH login worked and all three nodes matched Ubuntu 24.04, their intended names and management addresses with a /24 prefix.

## Read the result

| Output | Meaning |
|---|---|
| `PASS` | The named check passed |
| `FAIL` | That check did not pass; review it before continuing |
| `REVIEW` | Evidence was collected; a decision or configuration step remains |

The script prints a short summary, the exact report path and a command to read it. The `cat` command prints the report's contents. The report includes observed NIC names/MACs, disk models/serials, stable disk paths, routes, DNS and time-sync information.

**Send back that single report for review.** It stays out of Git.

The script does not test sudo, certify disk health or prove that a disk is safe to erase. Its success is an access/identity checkpoint, not full OpenStack readiness.

## Short lecture — after the checkpoint

There are two different tests:

1. **TCP port 22:** can R2d2 reach an SSH listener?
2. **SSH inspection:** can your account log in, and is this the intended node?

This is why a reachable port alone does not prove that a node is ready.

**Next:** [1.4 — Prepare the two NICs and Cinder storage](04-nics-and-storage.md).
