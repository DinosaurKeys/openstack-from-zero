# Phase 4 — Virtual networking and the first VM

[Home](../../README.md) · [Completed Phase 3](../phase-03/README.md)

**Current: Chapter A is complete.** Its live report confirmed all three host layouts, all 12 agents alive/enabled and no existing Neutron network objects. Joe approved preparing [Chapter B](B-create-networks.md); its script and guide are published, with live creation pending. Read its exact change scope before applying.

| Chapter | Goal | Status |
|---|---|---|
| [A — Understand and inspect the network](A-network-inspection.md) | Read the two-NIC diagram and collect actual Neutron/OVS state | Complete — live inventory reviewed |
| [B — Create the virtual networks](B-create-networks.md) | Create the approved external/private networks, subnets and one router | Published — local tests passed; live creation pending |
| C — Prepare the first VM | Agree image, flavor, key and security-group access | Planned |
| D — Boot and test VM traffic | Verify the private IP, floating IP and actual USB/LAN path | Planned |
| E — Test a Cinder volume | Attach a volume and verify data persistence | Planned |
| F — Reproduce with Terraform | Codify the verified workload | Planned |

Failover needs its own explained test scope after the workload works; three controllers do not by themselves prove router, image-storage or volume failover.

![Two-NIC network and future VM traffic](../images/phase4-usb-network.svg)
