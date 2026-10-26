# Chapter C — Deploy the HA services

[Phase 3](README.md) · [Previous: full prechecks](B-full-prechecks.md) · [Architecture](../architecture.md)

**Goal:** install OpenStack with API VIP HA, three-member MariaDB/Galera and HA-capable Neutron agents. The fresh deployment must supply its own successful report; local tests are not a live deployment result.

## 1. Requirements

The three hosts must already pass bootstrap/prechecks. [Phase 2 E](../phase-02/E-configure-lab.md) supplies explicit HA globals and the three-node inventory. For already deployed hosts, use [Fresh installation](../fresh-install.md) first; do not run empty-host prechecks against a live cloud.

## 2. What installation changes

Standard Kolla deploy pulls service images, creates service containers/configuration and persistent data, bootstraps databases/registrations, configures OVS bridges on the USB NICs and initializes Cinder's local backend. No raw-disk erasure, PV/VG recreation, credential rotation or CA regeneration occurs in this command.

| Feature | Deployed behaviour |
|---|---|
| API Keepalived + HAProxy | All three load-balancer nodes participate; one owns `192.168.0.151`; HAProxy forwards service requests |
| MariaDB/Galera + ProxySQL | Three database members; ProxySQL supplies database connection routing |
| Neutron HA capability | `l3_ha=true`, maximum three L3 agents per router, two DHCP agents per network |
| Actual HA tenant router | Created later in Phase 4 B, with `ha=true` |

The management IPs remain on `enp0s31f6`. USB NICs join `br-ex` without host IPs. Glance's local-file container is required on node1 only. Cinder's initialized thin pool is expected; its VG no longer has to be empty after deployment.

## 3. What the wrapper verifies

1. Check pinned tools/source, exact HA globals, existing private inventory/credentials/certificates and all three prepared hosts.
2. Run fresh full Kolla prechecks. Deployment cannot start if they fail.
3. Deploy all three nodes through the standard Kolla CLI and reviewed configuration directory.
4. Check every node's final recap; require no failed, unreachable or rescued tasks. The only accepted ignored task is the pinned MariaDB initial port-discovery timeout, with exact task/node evidence and at most one occurrence per node. Other ignored failures stop review.
5. Read running containers and configured health checks; verify USB bridge membership and retained management access/configuration.
6. Query every MariaDB member through its existing monitor account: Synced, Primary, cluster size 3, connected/ready ON and one valid shared cluster UUID.
7. Require exactly one observed API VIP owner. Read each deployed Neutron server configuration and require HA=true, three L3 agents and two DHCP agents.
8. Confirm private deployment inputs remain unchanged. Save one combined private report and Kolla transcripts.

These checks establish deployment/configuration and replication state. They do not prove traffic survives a failure. [Chapter E](E-ha-verification.md) separates those tests.

## 4. Run — R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-deploy.py --apply
```

Without `--apply`, it prints the plan and makes no connections or changes. The launcher selects the existing Kolla environment.

Enter the dedicated SSH-key passphrase if needed, then each named node's sudo password. No R2d2 sudo password is requested. Passwords stay in memory/child environments; they are not written into options files, command arguments or reports. Keep the terminal open through image downloads and service startup.

## 5. Return the result

Use the printed `cat` command. Expected marker:

```text
RESULT: OPENSTACK_DEPLOYED for all
```

On `OPENSTACK_DEPLOYMENT_NEEDS_REVIEW`, return the report before rerunning or changing settings. Earlier tasks may have changed nodes; no automatic retry, rollback, recovery or deletion runs. Keep the full report private.

## 6. Read-only verification after installation

```bash
/usr/bin/python3 scripts/phase3-verify.py --run
```

This collects fresh container/host/VIP observations and Galera status on all three nodes. It does not repeat deployment. Then use [Chapter D](D-client-access.md) for HTTPS/login/API checks.

**Lesson:** API Keepalived moves the management entry address; Galera replicates service records; Neutron per-router HA protects the VM routing path after a router exists. Those are different mechanisms. None automatically restarts a guest from a failed compute host or replicates local Glance/Cinder storage.

## Validation limits

Development tests cover command scope, state/recap gates, expected discovery waits, health observations, Galera/HA checks and credential privacy. The Kolla source is pinned and guarded. Live image pulls, node task execution, startup and failure behaviour remain checks performed on the actual lab.
