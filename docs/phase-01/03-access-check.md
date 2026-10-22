# 1.3 — Check access from R2d2

[Phase 1](README.md) · [Next: NICs and storage](04-nics-and-storage.md)

**Status: complete.** Your report ends with **`NODE_IDENTITY_CHECKS_PASSED`**.

| Node | SSH account | Ubuntu | Verified management address |
|---|---|---|---|
| node1 | `joe` | 24.04 | `192.168.0.200/24` |
| node2 | `joe` | 24.04 | `192.168.0.201/24` |
| node3 | `joe` | 24.04 | `192.168.0.202/24` |

All three use `enp0s31f6` for management, with configured gateway and DNS `192.168.0.1`. Their reports and R2d2's report show `NTPSynchronized=yes`.

NIC identities and storage observations are reviewed in [Chapter 1.4](04-nics-and-storage.md). The full private report stays outside Git.

## Short lecture — what your result means

Ping showed that the addresses respond. SSH has now authenticated your account and read the actual operating systems, hostnames and addresses. That establishes which machines R2d2 is administering.

The three nodes expose a CPU virtualization flag and `/dev/kvm`. That is useful evidence for the planned computes; running a VM is a later test. R2d2 is the administration VM and does not need to run the cloud's VMs.

**Next:** [1.4 — Prepare the two NICs and Cinder storage](04-nics-and-storage.md).

<details>
<summary>Completed inspection — command reference</summary>

## A. Download the course once

Git downloads and updates this repository; the SSH client connects to the nodes. Both are Ubuntu system packages on R2d2, installed with their required dependencies.

The commands below refresh the package list, install those two tools, download the course, then enter its folder.

```bash
sudo apt update
sudo apt install --yes git openssh-client
git clone https://github.com/DinosaurKeys/openstack-from-zero.git "$HOME/openstack-from-zero"
cd "$HOME/openstack-from-zero"
```

If this checkout already exists, use the following instead of cloning again:

```bash
cd "$HOME/openstack-from-zero"
git pull --ff-only
```

## B. Run one combined inspection

The [script](../../scripts/phase1-check.py) uses Python's standard library. It needs no pip packages or virtual environment.

| What it does | Where |
|---|---|
| Read Ubuntu, hostname, addresses and network/storage facts | R2d2 |
| Check port 22, then log in once to each node | R2d2 → .200, .201 and .202 |
| Verify each node's Ubuntu version, hostname and intended /24 address | Through SSH |
| Record NIC names/MACs, routes, DNS, disks and stable disk paths | Through SSH |
| Write one combined private report | `reports/` inside this checkout |

It reads the nodes without changing their configuration. SSH may save a host key in R2d2's normal known-hosts file when you accept it.

```bash
python3 scripts/phase1-check.py --ssh --user joe
```

Here, `--ssh` enables node inspection and `--user joe` selects the node login account. Replace `joe` only if the nodes use a different account. Password login is supported.

On a first connection, verify the displayed host fingerprint against the node console before accepting it. For an ED25519 host key, display it at that node's console with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. A changed-key warning remains visible for review.

## Success check

The final marker is **`NODE_IDENTITY_CHECKS_PASSED`**.

That means R2d2 passed its checks and all three node logins matched Ubuntu 24.04, their intended names and management addresses with a /24 prefix.

| Output | Meaning |
|---|---|
| `PASS` | The named check passed |
| `FAIL` | That check needs review |
| `REVIEW` | A device/network decision or preparation step remains |

The script prints the report path and a `cat` command to read it. Reports stay out of Git.

Sudo, disk health, permission to erase a disk and full OpenStack readiness are separate checks.

</details>
