# Project instructions

Build this fresh course in DinosaurKeys/openstack-from-zero. Read the relevant phase before editing.

## Confirmed design
- R2d2: r2d2/joe, Ubuntu 24.04.5 LTS x86-64 on VMware; ens33 has observed DHCP address 192.168.162.131/24. Its configured gateway and DNS are 192.168.162.2. NAT mode stated by Joe; exact VMware product remains unknown and does not block the working outbound SSH path.
- Joe's full inspection report starting 2026-10-22T20:30:06Z ends NODE_IDENTITY_CHECKS_PASSED. All three authenticated as joe and matched Ubuntu 24.04, hostnames node1/node2/node3 and .200/.201/.202 with /24 prefixes. Management is enp0s31f6 on all three; configured default gateway and DNS are 192.168.0.1. R2d2 and all nodes report NTPSynchronized=yes; no accuracy bound was measured.
- USB NICs: node1 enx00e04c557e58 / 00:e0:4c:55:7e:58; node2 enx00e04c2c1678 / 00:e0:4c:2c:16:78; node3 enx00e04c304688 / 00:e0:4c:30:46:88. Joe's network report starting 2026-10-22T21:15:28Z ends USB_NETWORK_READY for all. All native Netplan trials were accepted; management IP/routes/DNS were preserved, all USB NICs are UP with no host addresses and 1000 Mb/s full-duplex physical links, and all fresh SSH checks passed. No VM traffic or reboot test has run yet.
- Ubuntu root volumes are backed by NVMe on all nodes. Node3's SATA candidate is /dev/disk/by-id/ata-KINGSTON_SA400S37240G_50026B768317883A, serial 50026B768317883A, 240057409536 bytes, currently /dev/sda. lsblk shows no filesystem, child partitions or mount on it. Rootless inventory does not establish complete disk ownership, erasure safety or SMART health.
- Joe confirms all built-in/USB NICs connect through the same ordinary mini switch to a TP-Link home router on 192.168.0.0/24, without VLAN tagging. Home DHCP pool is 192.168.0.2–150 inclusive. Offered cloud-address range is .151–.250, but .200–.202 are the nodes and must be excluded. Initial proposal: .151 shared API VIP; .152–.199 external allocation pool. Leave .203–.250 outside the initial pool; do not claim those addresses are unoccupied. These are planning values, not assigned by NIC preparation.
- Chapter 1.4 is complete with the short NIC lesson. The USB-only Netplan fragment is saved on each node; actual MAC-based names are unchanged. Later Kolla inventory must select the correct per-node neutron_external_interface. No VLAN, bridge or host IP was created. Private backups remain on the nodes; reports stay on R2d2.
- Pending storage step: Chapter 1.5, run scripts/phase1-storage-check.sh --user joe on R2d2. It sends a read-only helper through SSH/sudo only to node3, checks the stable SATA identity, then records block/mount/fstab/swap/RAID/signature/LVM/available SMART evidence. It installs no packages and has no apply mode. Review the returned report before preparing cinder-volumes; missing health tools or nonzero commands are review items. Privileged disk ownership/health and VG creation remain unverified. Node sudo and live NIC preparation are now proven by Joe's network report.
- Local network validation passed 26 fixture tests; the read-only storage collector and wrapper have six additional fixture tests. These check identity rejection, read-only commands, incomplete evidence and SSH/report behavior, not live disk safety. Native Netplan is absent from the development environment; its real generator and trial passed on Joe's nodes.
- Current step: Joe authorized only a read-only SSH-key/Chrony/hostname inspection and its explained Markdown guide. Run scripts/phase1-access-state.py --user joe from R2d2, then review the returned report. It inventories local public-key fingerprints and agent entries, probes fresh public-key-only login, uses a password fallback only to read node facts, and reports existing Chrony, clock, hosts-file and IPv4 lookup state on all four machines. No keys are created/copied, no packages installed, and no system configuration changed. The live result is not yet known. Joe confirmed Chrony as the intended time tool; current installations and R2d2 key state must be observed first.
- Earlier openstack-iac-lab contains Hermes-specific SSH key setup and node-only Chrony/hosts tasks, with successful results recorded there. Do not transfer those completion claims to the rebuilt R2d2 or run the Hermes-specific script unchanged.
- Controllers: all three. Computes: node2 and node3.
- Built-in NIC: management. USB NIC: external VM traffic.
- Cinder LVM is the intended storage approach; review actual node3 disk identity before provisioning.

## Teaching and execution
- One phase at a time, with short chapters, diagrams and clear finish checks.
- Retain completed procedures, command blocks and explanations visibly for study. Ask Joe before deleting, shortening or hiding teaching material.
- Inspect available repository evidence before asking Joe to remember earlier work. Ask before proceeding to new configuration work; do not assume consent beyond the agreed step.
- Before a command, explain purpose, execution machine, inputs, changes and output.
- Put the short lecture after the result checkpoint.
- Consolidate repeated node inspection in scripts. Keep commands few and meaningful.
- Main chapters contain the working procedure. Private reports hold detailed execution evidence; do not append troubleshooting chronology to the course.
- Use observed output. Do not import old NIC MACs, disk identities, VIPs, DHCP ranges, credentials or completion claims.
- A proposal, local simulation and a live result are different things. Label them accurately.
- Joe executes infrastructure changes. Publishing a guide/script does not authorize running it on the lab.
- Disk operations require current identity, ownership and mount review plus approval of the exact operation/target. Keep installer prechecks enabled.
- Keep reports, passwords, SSH private keys and Terraform state out of Git.
- Preserve the user's chosen design unless they ask to change it.
