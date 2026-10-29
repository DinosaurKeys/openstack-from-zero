# Shutdown, power-on and recovery

[Home](../README.md) · [Command glossary](command-glossary.md)

Run commands on **R2d2 as joe**, from `~/openstack-from-zero`. Keep R2d2 running.

**The order:** stop cloud services → power off hosts → power on hosts → check → repair only the named failures → check again.

| Need | Go to |
|---|---|
| Shut down the whole lab | [1. Stop services](#normal-shutdown-two-steps), then [2. Power off](#2-power-off-ubuntu) |
| Start after a shutdown | [3. Power on](#after-power-on-check-the-cluster), then [4. Check](#4-read-the-live-dashboard) |
| Database unavailable | [5. MariaDB recovery](#5-mariadb-cold-boot-recovery) |
| Failed API or control containers | [6. Control-service recovery](#6-recover-failed-control-services) |
| Compute or network agent still DOWN | [7. Manual service and agent restarts](#manual-service-and-agent-restarts) |
| Check the final result | [8. Confirm recovery](#what-a-healthy-result-means) |

Get the current guide and scripts:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

<a id="7-future-reboot-and-shutdown"></a>
<a id="full-shutdown--node1-node2-and-node3"></a>

## Normal shutdown: two steps

Finish cloud/storage operations and **cleanly shut down running VMs first**. The scripts check for active VM processes but do not shut down guests. Leave the hosts on while services stop.

### 1. Stop OpenStack services

```bash
/usr/bin/python3 scripts/lab-shutdown.py --apply
```

Enter joe's sudo passwords. Review the checks, then type **`STOP ALL CLOUD SERVICES`**.

| Order | What stops |
|---|---|
| First | OpenStack applications, workers, agents and DHCP/router helpers |
| Next | Proxies/API VIP services, then RabbitMQ |
| Last | MariaDB on node1, node2, then node3; each clean exit and database position is checked |

This can take several minutes. **Ubuntu stays on.** Continue only after:

```text
RESULT: READY_FOR_MANUAL_POWEROFF
```

If shutdown needs review, keep the hosts on and use [the interrupted-shutdown reference](#only-if-shutdown-is-interrupted).

### 2. Power off Ubuntu

```bash
bash scripts/lab-poweroff.sh --apply
```

Type **`POWER OFF ALL NODES`**. The script verifies the completed service-stop record and schedules normal Ubuntu poweroff in **one minute** on each host. Wait until all three physical hosts are off before removing electricity.

`lab-shutdown.py --check` is optional: `--apply` already performs its checks. **`--finish` is only for a reviewed interrupted shutdown.**

<a id="1-check-after-power-on"></a>

## After power-on: check the cluster

### 3. Start the hosts

1. Power on **node3 first**. It is the last database/broker member stopped by the procedure above.
2. Power on **node2 and node1**. Let all three Ubuntu hosts boot.
3. Continue from R2d2. Starting node3 first does not itself recover the database cluster.

### 4. Read the live dashboard

```bash
bash scripts/lab-recover.sh --check
```

This is read-only. Enter joe's sudo password for each node. Read **Galera and RabbitMQ first**, then containers, APIs and service/agent heartbeats.

| What you see | Meaning / next action |
|---|---|
| `PASS` | That check passed; leave it alone. |
| `FAIL` | A named check did not pass; follow its component below. |
| `BLOCKED` | The check could not finish; this is not proof the component is down. |
| Container `EXITED` | Its process is stopped. Exit 0 means it stopped without reporting an error. |
| Container `running/unhealthy` | Its process runs, but its healthcheck fails. |
| Container `running/starting` | Startup is still being checked. Observe before choosing a restart. |
| Service `DOWN` / agent `alive=False` | OpenStack is not receiving a current heartbeat, even if the container is healthy. |
| Service `disabled` | It is excluded from new scheduling work. This is separate from whether it is UP or DOWN. |

**Container health, service heartbeat and scheduling permission are three different checks.** A healthy container alone does not prove that the service is communicating with OpenStack.

If the result is **`REBOOT_SERVICES_HEALTHY`**, stop here. Otherwise, follow only the relevant repair below. The printed TXT report identifies failures; JSON is optional technical detail.

## Recover only what needs repair

<a id="5-mariadb-cold-boot-recovery"></a>

### 5. Recover MariaDB — only when needed

| Galera result | Action |
|---|---|
| All three Synced/Primary, size 3, connected/ready, same cluster UUID | Skip database recovery. |
| All three MariaDB containers confirmed stopped with exit 0 after a whole-lab shutdown | Use the coordinated recovery command below. |
| A Primary member survives, a node is unreachable, or state is partial/unclear | Review the report first; this is not the complete-outage procedure. |

For the reviewed complete database outage, run once on R2d2:

```bash
source /home/joe/venvs/kolla/bin/activate
ANSIBLE_BECOME_ASK_PASS=True kolla-ansible mariadb-recovery \
  --configdir "$HOME/openstack-from-zero/private/phase2/kolla" \
  -i "$HOME/openstack-from-zero/private/phase2/inventory.ini" \
  --limit baremetal --become \
  -e ansible_python_interpreter=/usr/bin/python3
```

Kolla checks database positions and coordinates recovery. **Do not manually restart all MariaDB containers first.** Do not select a bootstrap node or edit `grastate.dat` yourself.

Require `failed=0` and `unreachable=0` for all nodes, then run the dashboard again. Continue only with **3/3 Synced/Primary**, size 3, same UUID and healthy RabbitMQ/ProxySQL. If recovery fails, keep its report instead of repeating it.

### 6. Recover failed control services

When Galera, RabbitMQ and the proxies are healthy but API/control containers fail:

```bash
bash scripts/lab-recover.sh --apply
```

Review the node/container plan and type **`RESTART LISTED SERVICES`**. Healthy containers are skipped; each approved failed target gets at most one restart. Starting targets are observed first. **DEFER** means no restart was sent and the target still needs checking.

| This helper can restart | It leaves for manual review |
|---|---|
| Placement API; Nova API, metadata, conductor and scheduler; Neutron server, RPC server and periodic worker; Cinder API | Nova compute/libvirt, Neutron agents, Cinder scheduler/volume, identity/image/dashboard services, proxies and RabbitMQ |

The helper checks all services and agents at the end, but its automatic restart list covers **control containers only**. A final failure is not necessarily another API failure.

**If it reports `Control-container restart targets: 0`, stop repeating `--apply`.** Use step 7 for any named compute or agent failures. The helper does not restart a healthy container just because its service heartbeat is DOWN, and it does not enable disabled computes.

`--plan` is an optional read-only preview of the supported container restarts. A missing observation, changed identity or uncertain restart outcome stops automatic repair. Keep the report and progress records.

<a id="manual-reference"></a>
<a id="2-read-the-result-and-choose-the-action"></a>
<a id="if-a-cluster-check-fails"></a>
<a id="3-restart-one-failed-service-or-agent"></a>
<a id="manual-service-and-agent-restarts"></a>

### 7. Restart one failed service or agent manually

**Use a fresh dashboard to choose one node and one component.** First check its log and dependencies. A DOWN heartbeat with a healthy container does not establish the cause. Repair one selected component, then check its heartbeat before moving on.

Restarting compute/agent processes can interrupt management or network activity. Keep VM/migration/storage operations paused during this procedure.

#### A. Connect to the affected node

Choose **one** connection from R2d2:

| Node | Command |
|---|---|
| node1 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200` |
| node2 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.201` |
| node3 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.202` |

Run `hostname -s` to confirm the node. An eventual **“Connection … closed”** after `exit` is normal.

#### B. Inspect the selected component

Example: `nova_compute`. Change only the first line to a container name from the table below:

```bash
lab_service=nova_compute
lab_unit="kolla-${lab_service}-container.service"
sudo systemctl status "$lab_unit" --no-pager
sudo docker inspect --format 'State={{.State.Status}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$lab_service"
sudo journalctl -u "$lab_unit" -n 30 --no-pager
```

`systemctl`/Docker show the process and its startup history. **The application log shows why the service may not be communicating.** For a running container, use the matching read command:

| Dashboard component | `lab_service` | Application log command on that node |
|---|---|---|
| `nova-compute` | `nova_compute` | `sudo docker exec nova_compute tail -n 40 /var/log/kolla/nova/nova-compute.log` |
| Open vSwitch agent | `neutron_openvswitch_agent` | `sudo docker exec neutron_openvswitch_agent tail -n 40 /var/log/kolla/neutron/neutron-openvswitch-agent.log` |
| Router / L3 agent | `neutron_l3_agent` | `sudo docker exec neutron_l3_agent tail -n 40 /var/log/kolla/neutron/neutron-l3-agent.log` |
| DHCP agent | `neutron_dhcp_agent` | `sudo docker exec neutron_dhcp_agent tail -n 40 /var/log/kolla/neutron/neutron-dhcp-agent.log` |
| Metadata agent | `neutron_metadata_agent` | `sudo docker exec neutron_metadata_agent tail -n 40 /var/log/kolla/neutron/neutron-metadata-agent.log` |

If the container is stopped, `docker exec` cannot run. Read `sudo docker logs --tail 40 "$lab_service"` and its journal instead. Log excerpts can contain private information; review before sharing.

For **Nova compute**, require a working libvirt connection before choosing a compute restart. This read-only query checks the configured local libvirt service can answer:

```bash
sudo docker exec --user root nova_libvirt virsh --readonly -c qemu:///system list --all
```

This does not prove Nova's separate authenticated libvirt connection works; use the compute log for its connection errors. For **Neutron agents**, require healthy RabbitMQ/control services and Open vSwitch first. A failed libvirt, OVS or broker dependency needs its own repair; repeatedly restarting its client is not a fix.

#### C. Restart the selected unit once

After reviewing that node/component and its dependencies, run on the affected node:

```bash
sudo systemctl restart "$lab_unit"
sudo systemctl is-active "$lab_unit"
sudo docker inspect --format 'State={{.State.Status}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$lab_service"
```

Example: `lab_service=nova_compute` restarts **`kolla-nova_compute-container.service`**. Selecting `neutron_l3_agent` restarts **`kolla-neutron_l3_agent-container.service`**. Underscores matter.

A successful restart command means systemd accepted the operation; startup and heartbeats still need verification. Allow startup to finish. If it exits again, remains unhealthy or still has a DOWN heartbeat, keep the new errors and stop repeating restarts.

**A disabled compute needs a separate decision.** Return to R2d2 with `exit`, then read its reason:

```bash
/usr/bin/python3 scripts/lab-view.py compute-services --table
```

First recover the heartbeat to UP. If it remains **UP/disabled**, review the reason before enabling scheduling; it may be deliberately in maintenance. An unknown reason is not permission to enable it. Enabling scheduling does not repair a DOWN service.

#### Other application containers

The same `lab_service`/Kolla-unit procedure applies after selecting the failed component:

| Component | Container names — choose one |
|---|---|
| Placement | `placement_api` |
| Nova control | `nova_api`, `nova_metadata`, `nova_conductor`, `nova_scheduler` |
| Neutron control | `neutron_server`, `neutron_rpc_server`, `neutron_periodic_worker` |
| Cinder control | `cinder_api`, `cinder_scheduler` |
| Identity | `keystone_api`, `keystone_fernet`, `keystone_ssh` |
| Images — node1 only | `glance_api` |
| Dashboard | `horizon` |

**Review infrastructure separately:** MariaDB, ProxySQL, RabbitMQ, HAProxy/Keepalived, `nova_libvirt`, `cinder_volume`, `openvswitch_db` and `openvswitch_vswitchd`. Their restart impact differs from a client/agent restart. Do not restart Docker, every container or every node as a substitute for selecting the failed component.

<a id="6-confirm-service-recovery"></a>
<a id="what-a-healthy-result-means"></a>

### 8. Confirm recovery

If still connected to a node, run `exit` to return to R2d2. Then:

```bash
bash scripts/lab-recover.sh --check
```

| Required result | What it proves |
|---|---|
| Galera: 3/3 Synced/Primary, size 3, same UUID | The three database members are ready and in the same cluster. |
| RabbitMQ: 3 members, no alarms/partitions | The broker cluster checks passed. |
| Containers and HTTPS checks PASS | Expected processes/healthchecks and authenticated API reads passed. |
| Nova: 9/9 UP/enabled; hypervisors: 3/3 UP/enabled | Compute/control heartbeats and scheduling state passed. |
| Cinder: 6/6 UP/enabled | Scheduler/volume service heartbeats passed. |
| Neutron: 12/12 alive/enabled | All expected network agents are reporting. |
| Router: one active/two standby **and all hosting L3 agents UP/enabled** | Router placement and hosting-agent checks passed. |
| Exactly one API VIP owner | `192.168.0.151` is held by one host. |

Finish when the result is **`REBOOT_SERVICES_HEALTHY`**. This covers cluster services; VM startup, guest traffic/data and migration remain separate checks. Horizon's **System Information** page also provides **Compute Services** and **Network Agents** tabs for viewing service state; these views do not restart Kolla containers.

<a id="finish-an-interrupted-network-helper-stop"></a>

## Only if shutdown is interrupted

<details>
<summary>Continue a reviewed interruption in the DHCP/router-helper stage</summary>

Keep the hosts on and retain the TXT report and saved progress file. After reviewing that stage:

```bash
/usr/bin/python3 scripts/lab-shutdown.py --finish
```

This checks the saved stage and current state, asks for confirmation, skips the completed initial stop and continues stopping the remaining services. It is not a general retry. After `READY_FOR_MANUAL_POWEROFF`, use step 2. If it fails, keep the hosts on and return the report; retain progress records.

</details>

<a id="4-supported-api-recovery-helper"></a>
<a id="8-optional-service-and-agent-views"></a>

## Extra views and references

<details>
<summary>Individual components, full tables and saved reports</summary>

Run on R2d2:

```bash
/usr/bin/python3 scripts/lab-view.py compute-services --table
/usr/bin/python3 scripts/lab-view.py hypervisors --table
/usr/bin/python3 scripts/lab-view.py agents --table
/usr/bin/python3 scripts/lab-view.py cinder-services --table
/usr/bin/python3 scripts/lab-view.py router
```

For full node/container tables, use `/usr/bin/python3 scripts/lab-reboot-verify.py --check --details`. `--report` with the actual saved JSON path redisplays a **saved snapshot**, without checking the lab again. `--with-workload` adds separate read-only VM/guest/migration checks; it performs no recovery.

The older `lab-recover-api.py` is a separate fixed 14-container helper. Use `lab-recover.sh` for this runbook; keep existing receipts and VM/migration records.

</details>

<details>
<summary>Reboot just one host</summary>

Check the cluster and review that node's VMs and local storage first. Cleanly stop its guests, or use a separately verified migration. For the selected **node1**, from R2d2:

```bash
ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200 \
  'test "$(hostname -s)" = node1 && sudo reboot'
```

Wait for the host to return, then run the dashboard before touching another node. The whole-lab shutdown procedure is separate.

</details>

Local VM root disks, Cinder LVM and node1's Glance storage are not replicated. Cluster service HA does not provide storage HA or automatic VM recovery.

[Operating Kolla](https://docs.openstack.org/kolla-ansible/2026.2/user/operating-kolla.html) · [Pinned Kolla systemd units](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/module_utils/kolla_systemd_worker.py) · [Pinned MariaDB recovery](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/mariadb/tasks/recover_cluster.yml)
