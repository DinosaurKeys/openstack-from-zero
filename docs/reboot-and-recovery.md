# Reboot and recovery

[Home](../README.md) · [Command glossary](command-glossary.md)

Run these commands on **R2d2 as joe**, from `~/openstack-from-zero`. R2d2 stays running when the three OpenStack nodes shut down.

- [After power-on: check the cluster](#after-power-on-check-the-cluster)
- [Normal shutdown: two steps](#normal-shutdown-two-steps)
- [Only if shutdown is interrupted](#only-if-shutdown-is-interrupted)
- [If a cluster check fails](#if-a-cluster-check-fails)
- [Optional reference](#optional-reference)

To get the current scripts:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

<a id="1-check-after-power-on"></a>

## After power-on: check the cluster

Start **node3**, then **node2 and node1**. Node3 is the last database/broker member stopped by the shutdown procedure below. Let all three hosts boot; do not wait for a healthy cluster before starting the other hosts.

Run:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Enter joe's sudo password for each node. The dashboard checks the database (MariaDB/Galera), messaging (RabbitMQ), containers, APIs, services, network agents, router HA and host connections. It does not restart anything.

| Result | What to do |
|---|---|
| `REBOOT_SERVICES_HEALTHY` | The service checks passed. No service restart is needed. |
| `REBOOT_SERVICES_NEED_REVIEW` | Read the affected rows and use the recovery reference below. |
| `REBOOT_RECOVERY_NEEDS_REVIEW` | The check could not finish. Review its error; do not assume the cluster is healthy or stopped. |

The terminal prints the private TXT report path under `reports/`. If a check needs review, share that report; you do not need to interpret the JSON file.

This does not start VMs, repair services or retry migrations. VM/data checks are separate. Starting node3 first does not automatically bootstrap Galera; choose recovery from the fresh report.

<a id="7-future-reboot-and-shutdown"></a>
<a id="full-shutdown--node1-node2-and-node3"></a>

## Normal shutdown: two steps

First finish cloud/storage operations and cleanly shut down any running VMs. The service-stop script **blocks if VM domains are active**, including paused VMs; it does not shut down guests for you.

### 1. Stop OpenStack services

```bash
/usr/bin/python3 scripts/lab-shutdown.py --apply
```

This already checks readiness, so a separate shutdown `--check` is **optional**. If readiness fails, it does not start stopping services.

Type **`STOP ALL CLOUD SERVICES`** when asked. The script stops:

1. Applications and agents, then remaining DHCP/router helpers.
2. Proxies/VIP services and RabbitMQ.
3. MariaDB, one member at a time: node1 → node2 → node3.

It verifies clean database exits and saved Galera positions. This can take several minutes. Ubuntu remains powered on. Wait for:

```text
RESULT: READY_FOR_MANUAL_POWEROFF
```

If it reports `SHUTDOWN_NEEDS_REVIEW`, **do not power off or repeat `--apply`**. Follow the interrupted-shutdown section below.

### 2. Power off the hosts

Only after `READY_FOR_MANUAL_POWEROFF`:

```bash
bash scripts/lab-poweroff.sh --apply
```

The Bash script first checks the saved service-stop record. Type **`POWER OFF ALL NODES`** when asked, then enter joe's sudo password for each node. It checks each host before scheduling its normal Ubuntu poweroff in **one minute**.

It requires no running containers/VM processes and clean MariaDB/RabbitMQ exit states. A failed check or unacknowledged request stops the script; earlier hosts may already have shutdown scheduled. Wait until the physical hosts are fully off before removing electricity. An acknowledged request or lost SSH alone is not proof of completed poweroff.

**That is the normal sequence: `lab-shutdown.py --apply` → wait for READY → `lab-poweroff.sh --apply`.** Do not run `--finish` during a normal successful shutdown.

<a id="finish-an-interrupted-network-helper-stop"></a>

## Only if shutdown is interrupted

Keep the private TXT report and saved progress file (the receipt). Some services may already be stopped. Do not restart everything, delete the saved progress file or repeat the normal `--apply` command.

For a **reviewed interruption in the DHCP/router-helper stage**, run:

```bash
/usr/bin/python3 scripts/lab-shutdown.py --finish
```

`--finish` means **continue the interrupted service shutdown**. It checks all three nodes and the saved stage, then asks for confirmation. It skips the completed initial service-stop stage and handles the remaining helpers, proxies, RabbitMQ and MariaDB. It leaves Ubuntu powered on.

It is not a general retry: another failure stage, unhealthy dependencies, running VMs/clients or an unclear receipt blocks it. Review those findings before taking another action.

After `READY_FOR_MANUAL_POWEROFF`, return to **step 2** above. If `--finish` fails, leave the hosts powered on and return its report.

<a id="2-read-the-result-and-choose-the-action"></a>

## If a cluster check fails

Use a **fresh** `lab-reboot-verify.py --check` report, not an old saved snapshot.

| Finding | Meaning / next step |
|---|---|
| `UP/healthy`, service/agent `UP/enabled` | Leave it running. |
| Container `DOWN` or `UP/unhealthy` | Inspect its unit, logs and dependencies before selecting a restart. |
| Healthy container, heartbeat `DOWN` | The application is not reporting; inspect database/RPC/libvirt dependencies. |
| `UNKNOWN` or `NOT LISTED` | The check could not establish the state; this is not proof the service is down. |
| `starting` | Let initialization finish before deciding whether it failed. |
| `disabled` | Read the disable reason. A restart does not authorize enabling it. |

`UP/no-check` means the container has no healthcheck configured. Glance runs on **node1 only**; `N/A (placement)` on node2/node3 is expected.

Check dependencies in this order: **host access → MariaDB → ProxySQL/RabbitMQ → APIs → compute/network agents**. Healthy services need no repair. The references below are conditional, not extra steps to run after every boot.

<a id="3-restart-one-failed-service-or-agent"></a>

### Restart one failed service or agent

<details>
<summary>Open only after selecting and approving the failed node/container</summary>

Connect from R2d2 to the affected node. Example: **node2**:

```bash
ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.201
```

On that node, confirm the hostname and select **one** reviewed container. Example: `nova_compute`:

```bash
hostname -s
lab_service=nova_compute
lab_unit="kolla-${lab_service}-container.service"
sudo systemctl status "$lab_unit" --no-pager
sudo journalctl -u "$lab_unit" -n 40 --no-pager
```

After approval of that exact node/service:

```bash
sudo systemctl restart "$lab_unit"
sudo systemctl is-active "$lab_unit"
sudo docker inspect --format 'State={{.State.Status}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' "$lab_service"
```

Allow startup to finish. If it remains unhealthy or exits again, review its new errors rather than repeatedly restarting it. Return to R2d2:

```bash
exit
/usr/bin/python3 scripts/lab-reboot-verify.py --check
```

Require both container health and the relevant API/service/agent checks to recover. Do not replace one selected restart with a restart of Docker, `kolla.target` or every container.

| Component | Container names — select one |
|---|---|
| Identity | `keystone_api`, `keystone_fernet`, `keystone_ssh` |
| Images — node1 only | `glance_api` |
| Placement | `placement_api` |
| Compute APIs/workers | `nova_api`, `nova_metadata`, `nova_scheduler`, `nova_conductor`, `nova_compute` |
| Network APIs/workers | `neutron_server`, `neutron_rpc_server`, `neutron_periodic_worker` |
| Volume APIs/workers | `cinder_api`, `cinder_scheduler` |
| Dashboard | `horizon` |
| DHCP agent | `neutron_dhcp_agent` |
| Router agent | `neutron_l3_agent` |
| Open vSwitch agent | `neutron_openvswitch_agent` |
| Metadata agent | `neutron_metadata_agent` |

For these main containers, the unit pattern is **`kolla-CONTAINER_NAME-container.service`**, preserving underscores. For example, DHCP uses `kolla-neutron_dhcp_agent-container.service`.

**Infrastructure needs its own impact review:** `mariadb`, `proxysql`, `rabbitmq`, `haproxy`, `keepalived`, `nova_libvirt`, `cinder_volume`, `openvswitch_db`, `openvswitch_vswitchd`. Do not treat these as ordinary application restarts. Router-agent recovery alone does not prove forwarding or restart every router helper. Local Cinder storage and running VMs require separate consideration.

</details>

<a id="5-mariadb-cold-boot-recovery"></a>

### MariaDB cold-boot recovery

<details>
<summary>Open only for a reviewed, fully stopped database cluster</summary>

- All three Synced/Primary, size 3, same UUID: **skip recovery**.
- A Primary component survives, or a member is absent/syncing: inspect that state; **do not bootstrap another cluster**.
- All database members are stopped, no Primary survives and all nodes are observable: review and approve coordinated recovery.

Run once on R2d2, against all three nodes together:

```bash
source /home/joe/venvs/kolla/bin/activate
ANSIBLE_BECOME_ASK_PASS=True kolla-ansible mariadb-recovery \
  --configdir "$HOME/openstack-from-zero/private/phase2/kolla" \
  -i "$HOME/openstack-from-zero/private/phase2/inventory.ini" \
  --limit baremetal --become \
  -e ansible_python_interpreter=/usr/bin/python3
```

Kolla checks recovery positions and chooses the bootstrap member. Do not guess a host, edit `grastate.dat` or run `galera_new_cluster` manually. If recovery fails, return its output; do not repeat it or delete database volumes.

After completion, run `/usr/bin/python3 scripts/lab-reboot-verify.py --check`. Require all three members Synced/Primary/connected/ready, size 3 and the same UUID before choosing any API repair. Recovery restores database operation; it does not guarantee transactions lost during abrupt power failure can be recovered.

</details>

<a id="4-supported-api-recovery-helper"></a>

### Limited API recovery helper

<details>
<summary>Advanced reference — not a general restart-all command</summary>

`lab-recover-api.py` supports only this fixed profile:

| Containers | Nodes |
|---|---|
| `placement_api` | node1, node3 |
| `nova_api`, `nova_metadata`, `neutron_server`, `cinder_api` | node1, node2, node3 |

Inspect the live plan:

```bash
/usr/bin/python3 scripts/lab-recover-api.py --plan
```

Only after reviewing and approving those exact targets:

```bash
/usr/bin/python3 scripts/lab-recover-api.py --apply
```

It checks healthy dependencies, skips healthy targets and restarts each still-failed approved container once. Unknown/starting/missing state or failures outside scope block it. An earlier apply receipt blocks repetition; do not delete it. This helper does not repair databases, brokers, computes, agents, VMs or migrations. Verify afterward with the live dashboard.

</details>

<a id="6-confirm-service-recovery"></a>

After any approved repair, use the **same live cluster check at the top of this page** to confirm recovery. A healthy container alone does not prove application operation.

<a id="8-optional-service-and-agent-views"></a>

## Optional reference

<details>
<summary>Extra checks, saved reports and individual component views</summary>

| Optional command | Purpose |
|---|---|
| `/usr/bin/python3 scripts/lab-shutdown.py --check` | Preview shutdown readiness without stopping services; already included in normal `--apply`. |
| `bash scripts/lab-poweroff.sh --check` | Check readiness for final host poweroff without scheduling it. |
| `/usr/bin/python3 scripts/lab-reboot-verify.py --report reports/reboot-verify-TIMESTAMP-ID.json` | Redisplay an actual saved report; labelled SAVED SNAPSHOT, with no new lab checks. |

Individual service views:

```bash
/usr/bin/python3 scripts/lab-view.py compute-services --table
/usr/bin/python3 scripts/lab-view.py hypervisors --table
/usr/bin/python3 scripts/lab-view.py agents --table
/usr/bin/python3 scripts/lab-view.py cinder-services --table
/usr/bin/python3 scripts/lab-view.py router
```

Target: expected services/hypervisors up and enabled, all twelve expected Neutron agents alive/enabled, router one active/two standby. A service restart is not permission to enable a disabled service. Heartbeats do not prove guest traffic or storage operation.

Only when workload verification is separately requested:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check --with-workload
```

This adds read-only VM/runtime/guest/data/migration checks. It does not start VMs, mount/format data, change attachments, retry migrations or clear records.

| Node | Management IP |
|---|---|
| node1 | `192.168.0.200` |
| node2 | `192.168.0.201` |
| node3 | `192.168.0.202` |

API VIP: **192.168.0.151**. The API VIP and virtual-router HA are separate. Local VM root disks, Cinder LVM and node1's Glance storage are not replicated; no automatic VM recovery is provided.

</details>

<details>
<summary>Reboot just one host</summary>

Run the live cluster check first. Review that node's VMs/storage and approve the interruption separately. Cleanly shut down its guests, or use a separately verified migration plan. Example for **node1**, from R2d2:

```bash
ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200 \
  'test "$(hostname -s)" = node1 && sudo reboot'
```

Wait for that node to return, then run the live cluster check again before touching another node. The full service-stop/poweroff procedure above is for a whole-lab outage, not a rolling reboot.

</details>

[Pinned Kolla systemd units](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/module_utils/kolla_systemd_worker.py) · [Pinned MariaDB recovery](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/mariadb/tasks/recover_cluster.yml)
