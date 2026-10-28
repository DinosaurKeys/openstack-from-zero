# Reboot and recovery — check → recover → verify

[Home](../README.md) · [Manual command reference](reboot-recovery-reference.md) · [Command glossary](command-glossary.md)

Run these steps on **R2d2 as joe**. The nodes are node1 `.200`, node2 `.201`, node3 `.202`; the API VIP is **192.168.0.151**.

**Use one report to choose the next action. Repair needs a reviewed target and approval.**

## 1. Check after power-on

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Enter each node's sudo password. Read the dashboard: containers, Nova services, hypervisors, Neutron agents, Cinder services, Galera, RabbitMQ, router HA roles and HTTPS APIs are shown separately, by node. Return the printed TXT report; you do not need to interpret JSON.

Container cells show **UP/healthy**, **UP/unhealthy**, **DOWN**, **MISSING** or **UNKNOWN**. Service and agent cells show the heartbeat and administrative state separately, such as **UP/enabled** or **DOWN/disabled**. **UNKNOWN** is a failed/unavailable observation, not proof of DOWN. Healthy services need no restart.

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

## 3. Verify service recovery

Once the service recovery steps are complete:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Enter the three node sudo passwords. The default scope is **cluster services and agents only**. VM, data and migration checks are paused and do not affect this service result. Existing checkpoints remain unchanged.

Return the printed short TXT summary:

- `REBOOT_SERVICES_HEALTHY`: the collected cluster/service/agent checks passed; no restart is indicated.
- `REBOOT_SERVICES_NEED_REVIEW`: inspect the visible DOWN/unhealthy/UNKNOWN entries; approve exact repair targets before changing anything.

The command only observes; it performs no restart, VM action, attachment change or checkpoint clearing. Container health and service heartbeat are separate checks. RabbitMQ cluster health does not test every application RPC. Browser sign-in, failure tests, storage HA and VM operation are separate.

### Redisplay an existing report without another lab check

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --report reports/reboot-verify-TIMESTAMP-ID.json
```

Replace the filename with the existing JSON sibling of your TXT report. This formats saved evidence into the same readable dashboard: no SSH, passwords, probes or changes. It is clearly labelled **SAVED SNAPSHOT**, not a new measurement.

### VM verification — later, only when requested

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check --with-workload
```

This optional read-only mode adds actual VM runtime, pinned guest SSH/network/DHCP/metadata/DNS/HTTPS, volume attachment/filesystem/mount/checksum and recorded migration observations. Its VM/migration result is separate from cluster service health. It never starts/reboots a VM, resets Nova state, mounts/formats a disk or clears checkpoints. An unmounted volume is reported as unverified, not automatically mounted. Migration remains paused.

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
