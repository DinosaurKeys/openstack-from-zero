# Project instructions

Build this fresh course in DinosaurKeys/openstack-from-zero. Read the relevant phase before editing.

## Confirmed design
- R2d2 installation confirmed from Joe's 2026-10-22 terminal output: hostname r2d2, account joe, Ubuntu 24.04.5 LTS x86-64, VMware VM; ens33 has DHCP address 192.168.162.131/24. NAT mode was stated by Joe. Exact VMware product and gateway/DNS remain unknown.
- ICMP replies received at .200, .201 and .202 from R2d2. This confirms address reachability, not node OS/identity, SSH, sudo or storage readiness. Next: one combined SSH inspection using the existing Phase 1 script. Do not repeat the completed R2d2 installation or pings.
- Intended nodes: Ubuntu Server 24.04 LTS; node1 192.168.0.200, node2 192.168.0.201, node3 192.168.0.202. Actual OS and hostnames await SSH inspection in this course.
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
