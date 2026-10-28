# Recovery command reference

[Simple reboot guide](reboot-and-recovery.md) · [Home](../README.md) · [Viewing commands](command-glossary.md)

**Start here after powering the nodes on. Check first; repair only the component that needs it.** This page is for R2d2 and the current three-node lab, with API VIP **192.168.0.151**.

## 1. Check the cluster — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/lab-health.py --check
```

Enter joe's sudo password for each node when prompted. Read the short table and **Needs attention** list: it names unhealthy containers and unavailable host checks. Return the printed TXT summary; you do not need to interpret the JSON. The private JSON is available for detailed review.

| Check | What we want |
|---|---|
| Management access | node1 `.200`, node2 `.201`, node3 `.202` reachable with the expected identity |
| Core containers | Expected services running, including Neutron API/RPC/periodic workers; configured healthchecks passed. Glance is required on node1 only |
| MariaDB/Galera | All three `Synced`, `Primary`, connected/ready, size `3`, same cluster UUID |
| RabbitMQ | Application running on each member, no local resource alarms, all three members in each cluster view, no partitions |
| API VIP | `.151` on exactly one node's management interface |
| USB / br-ex | Each provider NIC linked, unnumbered and attached to `br-ex` |
| HTTPS / Horizon | Authentication, service list reads and the Horizon login page work |
| Nova | Three scheduler, conductor and compute services enabled/up; three hypervisors enabled/up |
| Neutron | Twelve expected agents alive/enabled; tracked HA router reports one active and two standby agents |
| Cinder | Three scheduler and volume services enabled/up |
| Existing VM / volume | Tracked VM ACTIVE with no task, original data volume attached and in-use |
| Exercise checkpoints | No unresolved migration, volume operation or restoration marker |

**PASS** means that check passed. **REVIEW** means an observed state differs from the target. **UNKNOWN** means the check could not obtain evidence; it does not mean the service is definitely broken.

`RESULT: CLUSTER_HEALTH_SNAPSHOT_PASSED` means these snapshot checks passed. `CLUSTER_HEALTH_NEEDS_REVIEW` means return the summary before changes. Detailed reasons are in the private JSON. An unresolved migration can require review even when the control services pass. The health script never clears that marker.

This check creates only local reports and may unlock the existing management SSH key. It does not restart services, bootstrap a database, change cloud objects or run guest commands. Container/API state does not prove guest traffic, volume contents, successful migration, browser sign-in or failover. RabbitMQ checks do not test every queue or an end-to-end Nova RPC request. Booting services may initially need time; rerun the same check to obtain a fresh snapshot without repairing anything.

## 2. View services and agents — on R2d2

These are separate checks you can run whenever you want. They authenticate through the private HTTPS profile, then only read API data. No OpenStack CLI installation is required.

**Nova services** — the VM scheduler, conductor and compute workers:

```bash
/usr/bin/python3 scripts/lab-view.py compute-services --table
```

Target: `enabled` and `up` for all three nodes. **Enabled** means allowed to work; **up** means Nova sees the service heartbeat. A running Docker container alone does not prove this.

If a compute service is disabled, read its **Disabled reason** column before any enable action. The table only reports Nova's recorded reason; it does not enable the service or establish whether that reason is still current. For an enabled service, an absent reason is shown as `-`; for a disabled service it remains `unknown`.

**Hypervisors** — hosts that run VMs:

```bash
/usr/bin/python3 scripts/lab-view.py hypervisors --table
```

Target: node1, node2 and node3, each `enabled` and `up`.

**Neutron agents** — the node workers for VM networking:

```bash
/usr/bin/python3 scripts/lab-view.py agents --table
```

Target: twelve agents: DHCP, L3/router, Open vSwitch and metadata on each node. **Alive: yes** means Neutron sees its heartbeat; **Enabled: yes** means it is administratively allowed to work.

**Cinder services** — the volume scheduler and storage workers:

```bash
/usr/bin/python3 scripts/lab-view.py cinder-services --table
```

Target: three `cinder-scheduler` and three `cinder-volume` services, each `enabled` and `up`. This does not prove that the VM can read its attached disk.

**Router HA roles**:

```bash
/usr/bin/python3 scripts/lab-view.py router
```

Read `hosting_agents`: one `active`, two `standby`, all alive/enabled. Role state is separate from guest traffic.

If an API read fails, its service/agent state is **unknown**. Use the host/container findings and dependency checks below; do not conclude all agents are down. These viewing commands never restart or enable anything.

## 3. Choose the recovery step from the report

| Finding | Next action |
|---|---|
| A node is unreachable | Check its power, console, management network and SSH first. Do not recover the database with a missing/unobserved node |
| All three Galera members meet the target | Skip database recovery |
| A Primary component survives, but a member is absent or syncing | Inspect that member and its logs; do not bootstrap a second cluster |
| All members stopped after full shutdown, no surviving Primary component, all nodes reachable | Review the database evidence, then approve Kolla MariaDB recovery below |
| Database checks unavailable or contradictory | Establish the actual state first; container health or a failed SQL query alone is insufficient |
| Database healthy; ProxySQL or RabbitMQ needs review | Inspect that dependency before restarting Nova or Neutron |
| Dependencies healthy; one application service remains unhealthy | Inspect its unit and logs; approve a restart of that exact service only |
| VM ERROR, pending migration, or unavailable data volume | Separate workload recovery review. Do not reset VM state or retry the migration to make the check pass |

The check order is **host access → database → database proxy and message broker → APIs and application services → networking and workloads**. This is a diagnosis order, not an instruction to restart every component.

## 4. MariaDB recovery — only after the condition above is confirmed

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
/usr/bin/python3 scripts/lab-health.py --check
```

Require all three database members to meet the Galera target and share their UUID before repairing database-dependent applications. If recovery fails, return its output; do not repeat it or delete database volumes.

## 5. Inspect one service before a targeted restart

For the reviewed API-only scope below, use the locally tested [API recovery helper](../scripts/lab-recover-api.py). It is separate from the read-only health checker:

| Approved container | Allowed nodes |
|---|---|
| `placement_api` | node1, node3 |
| `nova_api`, `nova_metadata` | node1, node2, node3 |
| `neutron_server`, `cinder_api` | node1, node2, node3 |

On R2d2:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/lab-recover-api.py --plan
```

Read-only plan: enter the three sudo passwords; read which of the approved containers remain unhealthy. The maximum scope is 14 containers. Healthy ones are skipped. Unknown state, missing containers, unexpected failures, unhealthy database/broker/proxy dependencies or changed identities block recovery.

After approving that exact scope, run:

```bash
/usr/bin/python3 scripts/lab-recover-api.py --apply
```

Apply refreshes the plan, then restarts one still-unhealthy approved API container at a time through its verified Kolla unit. It rechecks dependencies before each action and requires the same container to become healthy before continuing. It records each attempt before sending it. A failure, lost response or interruption stops the sequence; return the printed summary instead of repeating `--apply`.

The helper performs no MariaDB/RabbitMQ/ProxySQL/compute/agent restart, compute enable, VM operation or migration retry. Existing workload and migration checkpoints remain byte-for-byte unchanged. Its separate recovery receipt records only its own actions. A surviving pending migration requires separate review even if the APIs recover. An earlier apply receipt blocks another apply until reviewed; there is no force/reset option.

At the end, it checks HTTPS and service state. `APPROVED_API_CONTAINERS_HEALTHY` confirms container checks; `API_AND_SERVICE_CHECKS_PASSED` confirms the listed API/service checks too. Neither means the unresolved VM/migration or guest data has been recovered. Keep and return the report. `--check` delegates to the ordinary read-only cluster checker.

### Manual reference

Example: inspecting **node1 Nova compute**, from R2d2. Substitute only the node/service identified in the reviewed report:

```bash
ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200 \
  'sudo systemctl status kolla-nova_compute-container.service --no-pager; sudo journalctl -u kolla-nova_compute-container.service -n 40 --no-pager'
```

Use Kolla's systemd unit: `kolla-CONTAINER_NAME-container.service`. Inspect output privately; logs can contain sensitive details. The health check does not collect raw logs or credentials.

**Only after approving this exact restart**, with its dependencies healthy:

```bash
ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200 \
  'sudo systemctl restart kolla-nova_compute-container.service'

/usr/bin/python3 scripts/lab-health.py --check
```

Common container names are `proxysql`, `nova_scheduler`, `nova_conductor`, `nova_compute`, `neutron_server`, `neutron_l3_agent`, `neutron_dhcp_agent` and `neutron_openvswitch_agent`. Restarting an L3 agent alone does not prove the router recovered; recheck its HA roles and guest traffic. Do not enable an administratively disabled compute service without reviewing why it was disabled. RabbitMQ reset/redeploy is outside normal reboot recovery and is not automated here.

## 6. Future maintenance: one node at a time

1. Run the health check and resolve outstanding issues first.
2. Review the selected node's VMs and storage. Local VM root disks and Cinder LVM are not replicated: rebooting their host can interrupt the VM or its data access. Other healthy controllers cannot replace those local disks. Glance images also live on node1.
3. Agree on the interruption or complete a separately verified workload move/shutdown plan. Never treat planned migration as successful without its completion report.
4. Reboot only the selected node. Example for **node1**, after approval:

   ```bash
   ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200 \
     'test "$(hostname -s)" = node1 && sudo reboot'
   ```

5. Wait for that node to return, rerun `scripts/lab-health.py --check`, and review guest network/data checks separately. Proceed to another node only after recovery is confirmed.

A full three-node power-off is a **cold boot**, not a rolling reboot. All database and broker members stop together; startup may need coordinated recovery. After powering on, return to step 1. This page does not provide an unattended full-cluster shutdown script.

[Health script](../scripts/lab-health.py) · [Node collector](../scripts/lab-health-node.py) · [Kolla operations](https://docs.openstack.org/kolla-ansible/latest/user/operating-kolla.html) · [Pinned MariaDB recovery implementation](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/mariadb/tasks/recover_cluster.yml) · [RabbitMQ health checks](https://www.rabbitmq.com/docs/monitoring)

