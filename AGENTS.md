# Project instructions

Build this fresh course in DinosaurKeys/openstack-from-zero. Read the relevant phase before editing.

## Confirmed design
- R2d2: r2d2/joe, Ubuntu 24.04.5 LTS x86-64 on VMware; ens33 has observed DHCP address 192.168.162.131/24. Its configured gateway and DNS are 192.168.162.2. NAT mode stated by Joe; exact VMware product remains unknown and does not block the working outbound SSH path.
- Joe's full inspection report starting 2026-10-22T20:30:06Z ends NODE_IDENTITY_CHECKS_PASSED. All three authenticated as joe and matched Ubuntu 24.04, hostnames node1/node2/node3 and .200/.201/.202 with /24 prefixes. Management is enp0s31f6 on all three; configured default gateway and DNS are 192.168.0.1. R2d2 and all nodes report NTPSynchronized=yes; no accuracy bound was measured.
- USB NICs from that report: node1 enx00e04c557e58 / 00:e0:4c:55:7e:58; node2 enx00e04c2c1678 / 00:e0:4c:2c:16:78; node3 enx00e04c304688 / 00:e0:4c:30:46:88. All are administratively DOWN, without addresses. Carrier was unavailable; do not infer unplugged cables or link speed.
- Ubuntu root volumes are backed by NVMe on all nodes. Node3's SATA candidate is /dev/disk/by-id/ata-KINGSTON_SA400S37240G_50026B768317883A, serial 50026B768317883A, 240057409536 bytes, currently /dev/sda. lsblk shows no filesystem, child partitions or mount on it. Rootless inventory does not establish complete disk ownership, erasure safety or SMART health.
- Current step: Chapter 1.4 network plan. Ask whether USB NICs connect to the management switch/LAN, plus the LAN DHCP device and pool. Do not ask again for observed IPs, gateway, DNS, OS, NIC identities or disk serial. Sudo, permanent provider configuration, privileged disk ownership/health and Cinder VG creation remain unverified. No repeat of the completed installation/access inspection is needed.
- Controllers: all three. Computes: node2 and node3.
- Built-in NIC: management. USB NIC: external VM traffic.
- Cinder LVM is the intended storage approach; review actual node3 disk identity before provisioning.

## Teaching and execution
- One phase at a time, with short chapters, diagrams and clear finish checks.
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
