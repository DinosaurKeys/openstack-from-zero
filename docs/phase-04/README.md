# Phase 4 — Virtual networking and the first VM

[Home](../../README.md) · [Completed Phase 3](../phase-03/README.md)

**Current: Chapter A is published for read-only inspection. Its live report is pending.** No network or VM creation is authorized by this chapter.

| Chapter | Goal | Status |
|---|---|---|
| [A — Understand and inspect the network](A-network-inspection.md) | Read the two-NIC diagram and collect actual Neutron/OVS state | Published — live inspection pending |
| B — Create the virtual networks | Agree names, tenant subnet and allocations; create external and tenant networks plus router | Planned after A review and approval |
| C — Prepare the first VM | Agree image, flavor, key and security-group access | Planned |
| D — Boot and test VM traffic | Verify the private IP, floating IP and actual USB/LAN path | Planned |
| E — Test a Cinder volume | Attach a volume and verify data persistence | Planned |
| F — Reproduce with Terraform | Codify the verified workload | Planned |

Failover needs its own explained test scope after the workload works; three controllers do not by themselves prove router, image-storage or volume failover.

![Two-NIC network and future VM traffic](../images/phase4-usb-network.svg)
