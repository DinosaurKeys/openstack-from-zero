# Phase 3 — Install OpenStack with HA

[Home](../../README.md) · [HA settings](../phase-02/E-configure-lab.md) · [Architecture](../architecture.md)

**Goal:** deploy the three-node API and database HA services, and the agents that will host HA virtual routers. Fresh-install reports and failover tests remain required.

| Chapter | Purpose |
|---|---|
| [A — Prepare the nodes](A-prepare-nodes.md) | Standard Kolla host dependencies; reuse verified preparation on existing hosts |
| [B — Full Kolla prechecks](B-full-prechecks.md) | Validate the empty, prepared hosts and explicit HA configuration |
| [C — Deploy OpenStack](C-deploy-openstack.md) | Install API Keepalived/HAProxy, MariaDB/Galera and HA-capable Neutron agents |
| [D — CLI, HTTPS and Horizon](D-client-access.md) | Install/verify the CLI, load credentials, list VMs and verify API/browser access |
| [E — HA verification](E-ha-verification.md) | Distinguish configuration/replication observations from controlled failover tests |

For an existing deployment, start with the [fresh-install procedure](../fresh-install.md). It empties only the explained OpenStack scope and prepares the same final configuration used by Phase 2 E. No node OS reinstall, disk reprovisioning or CA rotation is required.

API VIP and Galera are created in **Chapter C**. Neutron's router-HA capability is deployed there too, but the actual virtual router is created with `ha=true` in **Phase 4 B**. Keep that distinction clear: installing L3 agents does not create tenant routers.

Glance remains on node1's local file storage. Cinder remains three independent local pools. Neither is replicated storage. VM, volume and failover tests are separate checkpoints after deployment/access/network observations pass.
