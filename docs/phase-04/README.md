# Phase 4 — Virtual networking and the first VM

[Home](../../README.md) · [Completed Phase 3](../phase-03/README.md)

**Current: Chapters A and B are complete.** [Chapter B's reviewed live result](B-create-networks.md#7-finish-check-and-short-lesson) confirms the external/private networks and router. The router is on node1, with external IP `192.168.0.192` and private gateway `10.10.10.1`; both ports are ACTIVE. First-VM preparation is next by agreement. VM traffic, DNS operation and failover remain untested.

| Chapter | Goal | Status |
|---|---|---|
| [A — Understand and inspect the network](A-network-inspection.md) | Read the two-NIC diagram and collect actual Neutron/OVS state | Complete — live inventory reviewed |
| [B — Create the virtual networks](B-create-networks.md) | Create the approved external/private networks, subnets and one router | Complete — live creation report reviewed |
| C — Prepare the first VM | Agree image, flavor, key and security-group access | Planned |
| D — Boot and test VM traffic | Verify the private IP, floating IP and actual USB/LAN path | Planned |
| E — Test a Cinder volume | Attach a volume and verify data persistence | Planned |
| F — Reproduce with Terraform | Codify the verified workload | Planned |

Failover needs its own explained test scope after the workload works; three controllers do not by themselves prove router, image-storage or volume failover.

![Two-NIC network and future VM traffic](../images/phase4-usb-network.svg)
