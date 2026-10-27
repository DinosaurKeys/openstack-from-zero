# E — Move the VM for planned maintenance

[Phase 5](README.md) · [Previous: virtual router](D-router.md)

**Simple idea:** move the running VM to another healthy compute node, similar to VMware vMotion. Both hosts must remain working during this exercise.

**Status: test plan.** Migration prerequisites must be inspected before choosing a destination or issuing a migration command. No migration capability is assumed from a successful VM boot.

| Step | What we do | What we check |
|---|---|---|
| 1 | Record VM, network and data-volume placement/checksum | A known working starting point |
| 2 | Inspect destination CPU compatibility, RAM, disk and Nova capacity | The destination can accommodate this VM |
| 3 | Check Kolla/Nova/libvirt migration settings and transport | A supported, correctly configured migration path |
| 4 | Explain the exact command and failure/completion handling; ask approval | Scope and expected interruption understood |
| 5 | Migrate once between healthy compute nodes | Nova reports completion and the new compute host |
| 6 | Check SSH, IPs, DNS, HTTPS and mounted data/checksum | Workload and data remain usable |

This VM has a local root disk. A planned live migration must account for copying that disk through block migration; compatibility and transport must be checked first. Its attached Cinder volume stays on its existing storage node, unless a separate storage migration is requested. It is not reformatted.

Do not power off a source host or delete its files during migration. If migration does not complete, inspect Nova's actual state before selecting a recovery action. Moving back is a separate decision, not an automatic rollback promise.

**Pass:** confirmed compute-host change, guest/network checks pass, and the same Cinder data checksum remains valid. This proves planned migration, not automatic VM recovery or storage HA.

[Official Nova migration guide](https://docs.openstack.org/nova/latest/admin/configuring-migrations.html)
