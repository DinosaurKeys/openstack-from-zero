# Chapter B — Install the Ubuntu nodes

[Phase 1](README.md) · [Previous: R2d2](A-r2d2.md)

**Purpose:** give each node a clean operating system and a known management connection.

**Checkpoint complete:** the reviewed SSH inspection confirms Ubuntu 24.04, the intended hostnames and management addresses on all three nodes. Their Ubuntu root volumes are on NVMe. The steps below are installation reference.

## Steps

1. Boot each node from the **Ubuntu Server 24.04 LTS amd64** installer.
2. Configure the **built-in NIC** for management using this table:

| Node | Hostname | IPv4 address | Subnet |
|---|---|---|---|
| node1 | `node1` | `192.168.0.200` | `192.168.0.0/24` |
| node2 | `node2` | `192.168.0.201` | `192.168.0.0/24` |
| node3 | `node3` | `192.168.0.202` | `192.168.0.0/24` |

For this lab, set **gateway `192.168.0.1`** and **DNS `192.168.0.1`**. These values were confirmed from the nodes. A different network requires a separately reviewed plan before using the course scripts.

3. Leave the **USB NIC** without IPv4 or IPv6 configuration for now. In the installer, edit that adapter and disable both address methods. Its final provider-network setup comes after device review.
4. Select the intended **NVMe OS disk** for Ubuntu. Identify it by the displayed model and capacity. Leave the separate SATA disk out of the installation.
5. Review the disk summary before confirming the erase/install operation. Creating Ubuntu's own LVM layout on the NVMe is acceptable.
6. Create account **`joe`** on every node. The full course requires this account and its home directory; an alternative username is not supported throughout the course.
7. Select **Install OpenSSH server**. If offered an SSH-key-only restriction, leave password login available for the initial access check.
8. Finish installation, remove the installation media and reboot.

## Success check

Each node boots, accepts a console login and shows its intended hostname. Chapter C verifies Ubuntu, the management address and SSH from R2d2.

## Short lecture — after the checkpoint

**OpenSSH server** accepts remote shell connections on a node. R2d2 uses the **OpenSSH client** to connect.

The built-in NIC carries administration traffic. The USB NIC will carry external VM traffic. The confirmed per-node NIC names and MAC addresses are in [Chapter H](H-nics-and-storage.md). Recheck them after a rebuild before applying the USB configuration.

Ubuntu's LVM on the NVMe and Cinder LVM on **each node's separate SATA disk** serve different purposes. Each node has its own `cinder-volumes` group; the three pools are independent, without replication.

**Continue:** [Phase 1 progress and current chapter](README.md).

References: [Ubuntu installation](https://canonical-subiquity.readthedocs-hosted.com/en/latest/howto/basic-server-installation.html) · [Ubuntu OpenSSH](https://ubuntu.com/server/docs/how-to/security/openssh-server/).
