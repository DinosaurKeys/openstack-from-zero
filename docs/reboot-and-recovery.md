# Reboot and recovery

[Home](../README.md) · [Command glossary](command-glossary.md)

**One guide: check → select an approved repair → verify.** Run from **R2d2 as joe**. This page is a reference for failures; healthy services need no restart.

- [1. Check after power-on](#1-check-after-power-on)
- [2. Read the result and choose the action](#2-read-the-result-and-choose-the-action)
- [3. Restart one failed service or agent](#3-restart-one-failed-service-or-agent)
- [4. Supported API recovery helper](#4-supported-api-recovery-helper)
- [5. MariaDB cold-boot recovery](#5-mariadb-cold-boot-recovery)
- [6. Confirm service recovery](#6-confirm-service-recovery)
- [7. Future reboot and shutdown](#7-future-reboot-and-shutdown)
- [8. Optional service and agent views](#8-optional-service-and-agent-views)

## 1. Check after power-on

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Enter each node's sudo password. The dashboard shows containers, Nova services, hypervisors, Neutron agents, Cinder services, Galera, RabbitMQ, router HA and HTTPS APIs separately, by node.

The default scope is **cluster services and agents only**. VM/migration checks are paused and their records remain unchanged. Read the TXT report; JSON interpretation is not required.

| Node | Management IP |
|---|---|
| node1 | `192.168.0.200` |
| node2 | `192.168.0.201` |
| node3 | `192.168.0.202` |

The API VIP is **192.168.0.151**. Glance runs on node1 only, so its other container cells show `N/A (placement)`.

## 2. Read the result and choose the action

| Dashboard finding | Meaning / action |
|---|---|
| `UP/healthy` container, service or agent `UP/enabled` | Leave it running |
| `UP/unhealthy` | The process is running but its healthcheck fails; inspect its logs and dependencies |
| Container `DOWN` | Inspect the affected Kolla unit and logs |
| Container healthy, heartbeat `DOWN` | The application is not reporting correctly; inspect its database/RPC/libvirt dependencies before choosing a restart |
| `disabled` | Administrative state; read the disable reason. A restart is not permission to enable it |
| `UNKNOWN` or `NOT LISTED` | Establish access/API/registration state first; do not treat it as a confirmed stopped process |
| `starting` | Initialization is in progress; allow time for healthchecks before judging success |


Container health, service heartbeat and guest operation are different checks. `UP/no-check` means no container healthcheck is configured. `-` under Disabled reason means an enabled service has no returned disable reason; `unknown` on a disabled service means no reason was available.

Check dependencies in this order: **node access → MariaDB → ProxySQL/RabbitMQ → APIs → compute/network agents**.

| Finding | Next step |
|---|---|
| Node/SSH/sudo observation unavailable | Fix access before selecting a service repair |
| Galera: all three Synced/Primary, size 3, same UUID | Skip database recovery |
| A Primary component survives, a member is absent or syncing | Inspect that member; do not bootstrap a second cluster |
| Complete stopped database cluster, no surviving Primary, all nodes observable | Review and approve [MariaDB recovery](#5-mariadb-cold-boot-recovery) |
| Database/proxy/broker healthy, one application/agent failed | [Inspect and restart only the approved target](#3-restart-one-failed-service-or-agent) |
| Several APIs unhealthy within the supported scope | Review the [API helper plan](#4-supported-api-recovery-helper) |
| VM/migration/data issue while cluster services pass | Keep it separate; do not restart healthy services to repair VM state |

## 3. Restart one failed service or agent

Use a fresh `--check` report, not an older `--report` snapshot, to identify **node + container**. Review that exact target and obtain approval before restarting it.

### 3.1 Connect to the affected node — from R2d2

| Node | Management IP |
|---|---|
| node1 | `192.168.0.200` |
| node2 | `192.168.0.201` |
| node3 | `192.168.0.202` |

**Example target: node2 / `nova_compute` / `kolla-nova_compute-container.service`.** Use this example only if that is the failed, reviewed target. Otherwise choose the correct node and container from the tables below.

```bash
ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.201
```

The following commands run **on the selected node**:

```bash
hostname -s
lab_service=nova_compute
lab_unit="kolla-${lab_service}-container.service"
```

Confirm the hostname matches your selected node. The Kolla unit pattern is **`kolla-CONTAINER_NAME-container.service`**; keep the underscores in the container name.

### 3.2 Inspect the selected service — on that node

```bash
sudo systemctl status "$lab_unit" --no-pager
sudo journalctl -u "$lab_unit" -n 40 --no-pager
```

`systemctl status` can return a nonzero exit code when the service is stopped/failed; that is part of the observation. The journal shows service startup/exit information. Application logs can provide additional errors; inspect them privately and redact credentials before sharing excerpts.

### 3.3 Restart only the approved target — on that node

After approval of the exact node/service:

```bash
sudo systemctl restart "$lab_unit"
```

Use the Kolla systemd unit, which supervises the container. Do not replace this with a restart of Docker, `kolla.target`, every container, or every node.

### 3.4 Verify — on that node, then R2d2

Allow the service to initialize, then inspect:

```bash
sudo systemctl is-active "$lab_unit"
sudo docker inspect --format 'State={{.State.Status}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' "$lab_service"
```

Target: unit `active`, container `running`, and `healthy` when a healthcheck is configured. `not-configured` is not a failed healthcheck; use the application/agent checks too. If it says `starting`, check again after its startup interval. If it remains unhealthy or exits again, stop and review the new errors instead of repeatedly restarting it.

Return to **R2d2**:

```bash
exit
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Confirm the service/agent heartbeat, APIs and relevant router roles have recovered. A container becoming healthy alone does not prove this. Keep the fresh report. The healthy service result is `REBOOT_SERVICES_HEALTHY`; it makes no VM/data/migration recovery claim.

### Container and unit names

Set `lab_service` to the **one** reviewed container name. The same Kolla unit pattern applies to each name.

#### Application APIs and workers

| Component | Container name |
|---|---|
| Keystone identity API | `keystone_api` |
| Glance image API — node1 only in this lab | `glance_api` |
| Placement capacity API | `placement_api` |
| Nova API | `nova_api` |
| Nova metadata | `nova_metadata` |
| Nova scheduler | `nova_scheduler` |
| Nova conductor | `nova_conductor` |
| Nova compute | `nova_compute` |
| Neutron network API | `neutron_server` |
| Neutron RPC worker | `neutron_rpc_server` |
| Neutron periodic worker | `neutron_periodic_worker` |
| Cinder volume API | `cinder_api` |
| Cinder scheduler | `cinder_scheduler` |
| Horizon dashboard | `horizon` |

#### Neutron agents

| Agent | Container name | Exact systemd unit |
|---|---|---|
| DHCP | `neutron_dhcp_agent` | `kolla-neutron_dhcp_agent-container.service` |
| Router / L3 | `neutron_l3_agent` | `kolla-neutron_l3_agent-container.service` |
| Open vSwitch | `neutron_openvswitch_agent` | `kolla-neutron_openvswitch_agent-container.service` |
| Metadata | `neutron_metadata_agent` | `kolla-neutron_metadata_agent-container.service` |

Afterward, require the agent to be alive/enabled. For L3, also verify one active and two standby router replicas. DHCP leases, metadata access, tunnels and VM traffic require their own agreed checks when relevant; agent heartbeat alone does not prove them.

L3-agent process recovery alone does not prove router forwarding or restart every per-router Keepalived process. Use the router roles and agreed traffic checks to judge recovery.

#### Infrastructure and storage — separate impact review

| Component | Container name | Required review |
|---|---|---|
| MariaDB/Galera | `mariadb` | Check Primary/Synced membership first. A complete stopped cluster needs the [MariaDB recovery procedure](#5-mariadb-cold-boot-recovery), not a guessed bootstrap |
| Database proxy | `proxysql` | Database health and backend connectivity |
| Message broker | `rabbitmq` | Cluster members, partitions, alarms and quorum; no group restart/reset |
| API load balancer | `haproxy` | API availability and healthy backends |
| API VIP manager | `keepalived` | Current owner and takeover impact; [manual VIP reference](command-glossary.md#6-manual-api-vip-failover-and-restoration) |
| Libvirt | `nova_libvirt` | Existing VM/host connections and separate workload impact review |
| Local Cinder storage worker | `cinder_volume` | Attached volumes and storage access; local LVM is not replicated |
| OVS database / switch | `openvswitch_db`, `openvswitch_vswitchd` | Management/provider layout and VM-network impact; do not restart both as an automatic fix |

These names identify the units; the table does not approve their restart. Review the component's cluster/workload impact before selecting an exact action. Never loop over all entries.


## 4. Supported API recovery helper

This is an optional, limited recovery profile for the following reviewed scope:

| Container | Allowed nodes |
|---|---|
| `placement_api` | node1, node3 |
| `nova_api`, `nova_metadata` | node1, node2, node3 |
| `neutron_server`, `cinder_api` | node1, node2, node3 |

On R2d2, inspect the live plan:

```bash
/usr/bin/python3 scripts/lab-recover-api.py --plan
```

After approval of its exact targets:

```bash
/usr/bin/python3 scripts/lab-recover-api.py --apply
```

The maximum scope is 14 containers. Healthy ones are skipped. Unknown/starting/missing state, changed identities, unhealthy dependencies or new failures outside scope block it. Each restart uses the verified Kolla unit and must become healthy before the next one.

This helper does not restart databases, brokers, proxies, computes or agents; it does not enable compute services or change VM/volume/migration state. It records each attempt before sending it. A previous apply receipt blocks another apply. If it stops or loses a response, return the report; do not delete the receipt or repeat `--apply` to force progress.

Its final service/API checks are distinct from VM/data recovery. Use the dashboard in section 6 for the current cluster-service state.

## 5. MariaDB cold-boot recovery

This is a changing operation, not part of the health check. Review the fresh report and approve it first. Run once from R2d2, against **all three nodes together**:

```bash
cd ~/openstack-from-zero
source /home/joe/venvs/kolla/bin/activate

ANSIBLE_BECOME_ASK_PASS=True kolla-ansible mariadb-recovery \
  --configdir "$HOME/openstack-from-zero/private/phase2/kolla" \
  -i "$HOME/openstack-from-zero/private/phase2/inventory.ini" \
  --limit baremetal \
  --become \
  -e ansible_python_interpreter=/usr/bin/python3
```

Kolla performs its recovery-position checks and bootstrap selection. Do not choose a bootstrap host by guessing, edit `grastate.dat` manually or run `galera_new_cluster`. Recovery restores cluster operation; it is not a backup restore and does not guarantee recovery of transactions lost during abrupt power loss.

After completion:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Require all three database members to meet the Galera target and share their UUID before repairing database-dependent applications. If recovery fails, return its output; do not repeat it or delete database volumes.


## 6. Confirm service recovery

Back on R2d2:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Require the affected container to be running/healthy when a healthcheck exists, its service/agent heartbeat up and enabled, and relevant APIs/router roles to pass. Healthy container status alone is insufficient.

- **`REBOOT_SERVICES_HEALTHY`**: the collected cluster/service/agent checks passed; no restart is indicated.
- **`REBOOT_SERVICES_NEED_REVIEW`**: inspect the DOWN/unhealthy/UNKNOWN entries before selecting another action.

Keep the fresh TXT report. No restart, VM action, attachment change or checkpoint clearing is performed by the checker. RabbitMQ cluster health does not prove every application RPC; browser sign-in, failover, storage HA and guest behavior are separate.

### Redisplay a saved report

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --report reports/reboot-verify-TIMESTAMP-ID.json
```

Use the actual existing JSON sibling of your TXT report. This formats saved evidence without SSH, passwords or new probes. It is labelled **SAVED SNAPSHOT**, not a current measurement.

## 7. Future reboot and shutdown

**Rolling reboot — one host at a time:**

1. Run the live dashboard and resolve service issues.
2. Review the chosen node's VMs and storage; agree on the interruption or a separately verified workload plan.
3. After approval, reboot only that node. Example for **node1**, from R2d2:

   ```bash
   ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200 \
     'test "$(hostname -s)" = node1 && sudo reboot'
   ```

4. Wait for its return, run the dashboard again, and review any agreed workload checks before touching the next node.

**Full shutdown — all hosts stop:**

1. Finish pending operations and agree on workload/storage shutdown.
2. Cleanly unmount application data and shut down guests through the agreed procedure.
3. Stop cloud services and then power down the hosts using a reviewed shutdown plan.
4. After power-on, return to section 1. A full cold boot may need coordinated MariaDB recovery; it does not always need it.

These instructions do not execute an unattended shutdown. Local VM root disks, local Cinder LVM and node1's Glance storage are not replicated. Other healthy controllers cannot replace those local disks. The lab has no automatic VM recovery.

## 8. Optional service and agent views

These are reference commands, not additional mandatory steps after the dashboard:

```bash
/usr/bin/python3 scripts/lab-view.py compute-services --table
/usr/bin/python3 scripts/lab-view.py hypervisors --table
/usr/bin/python3 scripts/lab-view.py agents --table
/usr/bin/python3 scripts/lab-view.py cinder-services --table
/usr/bin/python3 scripts/lab-view.py router
```

Services should be enabled/up; all twelve expected Neutron agents Alive/Enabled yes; the HA router should have one active and two standby replicas. A restart is not approval to enable a disabled service.

### VM and migration — later

Only when workload verification is separately requested:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check --with-workload
```

This adds read-only runtime, pinned guest SSH/network/DHCP/metadata/DNS/HTTPS, volume attachment/filesystem/mount/checksum and recorded migration observations, separately from cluster service health. It does not start/reboot/reset a VM, retry migration, mount/format a disk or clear checkpoints.

[Pinned Kolla systemd unit template](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/module_utils/kolla_systemd_worker.py) · [Pinned MariaDB recovery](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/mariadb/tasks/recover_cluster.yml)
