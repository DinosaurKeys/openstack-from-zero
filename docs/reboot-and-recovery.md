# Reboot and recovery — check → recover → verify

[Home](../README.md) · [Manual command reference](reboot-recovery-reference.md) · [Command glossary](command-glossary.md)

Run these steps on **R2d2 as joe**. The nodes are node1 `.200`, node2 `.201`, node3 `.202`; the API VIP is **192.168.0.151**.

**Use one report to choose the next action. Repair needs a reviewed target and approval.**

## 1. Check after power-on

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/lab-health.py --check
```

Enter each node's sudo password. Read the short table and **Needs attention** list. Return the printed TXT summary; you do not need to interpret JSON.

| Result | Meaning |
|---|---|
| PASS | That check passed |
| REVIEW | A measured state differs from the target |
| UNKNOWN | The check could not obtain evidence; the component is not proven down |

The check order follows the service dependencies: **nodes → MariaDB → ProxySQL/RabbitMQ → APIs → compute/network agents → VM/data**.

## 2. Recover only what needs it

| Finding | Next step |
|---|---|
| SSH or sudo unavailable | Fix access to that node before selecting recovery |
| All three Galera members Synced/Primary, size 3, same UUID | Skip MariaDB recovery |
| A Primary component survives, but a member is missing or syncing | Inspect that member; do not bootstrap a second cluster |
| Complete database outage after all nodes stopped; no surviving Primary component and all nodes observable | Review and approve [Kolla MariaDB recovery](reboot-recovery-reference.md#4-mariadb-recovery--only-after-the-condition-above-is-confirmed) |
| Database/proxy/broker healthy, application APIs unhealthy | Review the [API recovery scope](reboot-recovery-reference.md#5-inspect-one-service-before-a-targeted-restart); skip healthy containers |
| Compute service down or disabled | Read its disable reason and startup logs; approve only the affected service action |
| Neutron agent down or router roles incorrect | Inspect the affected agent and router; container health alone does not prove forwarding |
| VM ERROR, no running VM, data unmounted or checksum unavailable | Separate workload review; do not retry migration, reset state, mount or format automatically |

For the supported API recovery profile, inspect the plan:

```bash
/usr/bin/python3 scripts/lab-recover-api.py --plan
```

After approval of its exact targets:

```bash
/usr/bin/python3 scripts/lab-recover-api.py --apply
```

It restarts one still-unhealthy approved API container at a time and checks it before continuing. Its scope is limited; it is not a general database/compute/network/VM repair tool. A previous apply receipt blocks another apply. On failure or interruption, return its report—do not delete the receipt or rerun it to force progress.

## 3. Finish with ONE combined verification

Once the service recovery steps are complete:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Enter the three node sudo passwords and unlock the existing guest key if asked. Keep the terminal open; the checks may take several minutes.

This command checks:

1. Cluster containers, Galera, RabbitMQ, VIP, HTTPS, Nova/hypervisor/Cinder services, Neutron agents and router API state.
2. The existing VM's Nova status and whether its UUID is actually running on exactly one hypervisor.
3. Guest SSH with its existing pinned identity; DHCP, routes, metadata, DNS and outbound HTTPS.
4. The existing volume's attachment/backend, actual guest disk and filesystem UUID, mount and saved file checksum.
5. The recorded migration and unchanged existing checkpoints/configuration.

**It only observes.** It never starts/reboots a VM, retries migration, resets Nova state, mounts/formats a disk, changes attachments or clears a pending checkpoint.

If the volume is present but unmounted, it reports that explicitly. Data is **unverified**, not declared lost. A guest with working SSH but Nova `ERROR` is also reported as a mismatch; it is not silently marked recovered.

Return the printed short TXT summary:

- `REBOOT_RECOVERY_VERIFIED`: all listed checks passed, including the guest/data and outstanding-checkpoint checks.
- `REBOOT_RECOVERY_NEEDS_REVIEW`: the report identifies what remains unresolved. Approve an exact repair before changes.

Browser sign-in, failure testing, storage HA and a successful VM migration are separate from this reboot check. Migration stays paused until its earlier record is reconciled and a new move is separately approved.

## 4. Planned maintenance and full shutdown

**Rolling reboot:** choose one node, review its workloads/storage, approve the interruption, reboot that node, then complete verification before touching another node.

**Full shutdown:** first finish pending operations and agree on workload shutdown and storage handling. Cleanly unmount application data and shut down guests through the agreed guest/OpenStack procedure, then stop cloud services and finally power down the hosts using a reviewed shutdown plan. This page does not execute a full-cluster shutdown or assume a failed migration is safe to ignore.

Local VM root disks, local Cinder LVM and node1's Glance storage are not replicated. Rebooting their host can interrupt the workload even while other controllers are healthy. The lab has no automatic VM recovery.

After power-on, start at step 1. A full cold boot may need coordinated database recovery; it does not always need it.

## Optional manual views — services and agents

These are reference commands, not extra mandatory steps after the combined check:

```bash
/usr/bin/python3 scripts/lab-view.py compute-services --table
/usr/bin/python3 scripts/lab-view.py hypervisors --table
/usr/bin/python3 scripts/lab-view.py agents --table
/usr/bin/python3 scripts/lab-view.py cinder-services --table
/usr/bin/python3 scripts/lab-view.py router
```

Services should be enabled/up; agents Alive/Enabled `yes`; the HA router should have one active and two standby replicas. `Disabled reason: -` means an enabled service has no returned disable reason; `unknown` on a disabled service means no reason was available. Container health, service heartbeat and guest connectivity are separate checks.

[Detailed commands and conditional repairs](reboot-recovery-reference.md) · [Viewing glossary](command-glossary.md)
