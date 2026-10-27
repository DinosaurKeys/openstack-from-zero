# Phase 4 — HA virtual networking and the first VM

[Home](../../README.md) · [Phase 3](../phase-03/README.md) · [Architecture](../architecture.md)

Start after the fresh HA deployment, Galera/container observations, verified HTTPS/API access and Horizon login pass. The agents are installed in Phase 3; this phase creates and verifies an actual HA virtual router.

| Chapter | Goal | Checkpoint |
|---|---|---|
| [A — Inspect the network](A-network-inspection.md) | Collect actual Neutron/OVS configuration | All three nodes, expected mappings and HA settings reviewed |
| [B — Create the HA networks/router](B-create-networks.md) | Two networks, two subnets, one HA router | One active/two standby router replicas and two DHCP agents |
| [C — Prepare the first VM](C-prepare-vm.md) | Ubuntu image, flavor, guest key, limited security group | Complete — verified image/flavor/key/security-group report; no VM created by C |
| [D — Boot and test VM traffic](D-boot-and-test-vm.md) | Create one VM/floating IP; test SSH, DHCP/DNS and outbound HTTPS | Complete — reviewed live boot/SSH/DHCP/metadata/DNS/HTTPS/floating-IP ping passed |
| [E — Test a Cinder volume](E-cinder-volume.md) | One data volume; safe detach/reattach and file checksum | Complete — reviewed live attachment, detach/reattach and unchanged file checksum passed |


Fresh-install resource IDs, external router IP and active node come from the new report. Do not carry IDs, addresses or success claims from another deployment into it.

![Management, API HA and router HA](../images/ha-architecture.svg)

[Lab command glossary](../command-glossary.md) · [Next: Phase 5 — HA validation and VM migration](../phase-05/README.md)

HA storage/Ceph and automatic VM recovery remain deferred. Independent Cinder LVM volumes are not replicated.
