# 1.2 — Install the Ubuntu nodes

[Phase 1](README.md) · [Previous: R2d2](01-r2d2.md)

**Purpose:** give each node a clean operating system and a known management connection.

## Steps

1. Boot each node from the **Ubuntu Server 24.04 LTS amd64** installer.
2. Configure the **built-in NIC** for management using this table:

| Node | Hostname | IPv4 address | Subnet |
|---|---|---|---|
| node1 | `node1` | `192.168.0.200` | `192.168.0.0/24` |
| node2 | `node2` | `192.168.0.201` | `192.168.0.0/24` |
| node3 | `node3` | `192.168.0.202` | `192.168.0.0/24` |

Use the actual LAN gateway and DNS from your router/network settings. The gateway and DNS are still unconfirmed in this rebuild. If you do not know them, establish them before finishing manual IPv4 configuration.

3. Leave the **USB NIC** without IPv4 or IPv6 configuration for now. In the installer, edit that adapter and disable both address methods. Its final provider-network setup comes after device review.
4. Select the intended **NVMe OS disk** for Ubuntu. Identify it by the displayed model and capacity. Leave the separate SATA disk out of the installation.
5. Review the disk summary before confirming the erase/install operation. Creating Ubuntu's own LVM layout on the NVMe is acceptable.
6. Create your local account. The examples use `joe`; the script accepts another username.
7. Select **Install OpenSSH server**. If offered an SSH-key-only restriction, leave password login available for the initial access check.
8. Finish installation, remove the installation media and reboot.

## Success check

Each node boots, accepts a console login and shows its intended hostname. Chapter 1.3 verifies Ubuntu, the management address and SSH from R2d2.

## Short lecture — after the checkpoint

**OpenSSH server** accepts remote shell connections on a node. R2d2 uses the **OpenSSH client** to connect.

The built-in NIC carries administration traffic. The USB NIC will carry external VM traffic. NIC names and MAC addresses will be read from these new installations.

Ubuntu's LVM on the NVMe and the future Cinder LVM on node3's SATA disk serve different purposes. Cinder's separate volume group will be named `cinder-volumes`.

**Next:** [1.3 — Check access from R2d2](03-access-check.md).

References: [Ubuntu installation](https://canonical-subiquity.readthedocs-hosted.com/en/latest/howto/basic-server-installation.html) · [Ubuntu OpenSSH](https://ubuntu.com/server/docs/how-to/security/openssh-server/).
