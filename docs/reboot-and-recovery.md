# Shutdown, power-on and recovery

[Home](../README.md) · [Command glossary](command-glossary.md)

Start on **R2d2 as joe**, from `~/openstack-from-zero`. Manual service commands run **on the affected node**, as labelled below. Keep R2d2 running.

**Shutdown:** stop services → power off hosts. **After power-on:** check the cluster → identify the failed row → repair that component → check again.

| Where to run commands | What runs there |
|---|---|
| **R2d2** | `openstack` viewing commands and the lab scripts |
| **Affected node, inside SSH** | `sudo systemctl start/restart` for its Kolla services |

| Need | Go to |
|---|---|
| Shut down the whole lab | [1. Stop services](#normal-shutdown-two-steps), then [2. Power off](#2-power-off-ubuntu) |
| Start after a shutdown | [3. Power on](#after-power-on-check-the-cluster), then [4. Check](#4-read-the-live-dashboard) |
| Read Nova, Neutron and Cinder status | Included in [the single check command](#4-read-the-live-dashboard) |
| Database unavailable | [5. MariaDB recovery](#5-mariadb-cold-boot-recovery) |
| Failed API or control containers | [6. Control-service recovery](#6-recover-failed-control-services) |
| A service is stopped | [Start Nova compute](#b-start-a-stopped-nova-compute-service) or choose a component in [the restart quick reference](#c-restart-a-selected-running-service) |
| Compute or network agent still DOWN | [7. Manual service and agent restarts](#manual-service-and-agent-restarts) |
| Check the final result | [8. Confirm recovery](#what-a-healthy-result-means) |

Enter the project folder on R2d2:

```bash
cd ~/openstack-from-zero
```

<a id="7-future-reboot-and-shutdown"></a>
<a id="full-shutdown--node1-node2-and-node3"></a>

## Normal shutdown: two steps

Finish cloud/storage operations and **cleanly shut down running VMs first** using the [VM power controls](command-glossary.md#vm-power-controls). The scripts check for active VM processes but do not shut down guests. Leave the hosts on while services stop.

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

This is read-only. Enter joe's sudo password for each node. The command checks the foundation and **automatically prints all three service tables**:

| Included table | Same service list as | Expected rows |
|---|---|---|
| Compute services / Nova | `openstack compute service list` | 9 |
| Network agents / Neutron | `openstack network agent list` | 12 |
| Volume services / Cinder | `openstack volume service list` | 6 |

Every row identifies the **host, service/agent, heartbeat and admin setting**, including healthy rows. Cinder rows retain the reported backend host. The checker already reads these API lists; you do not need to run three extra commands or activate/source the CLI for this check.

Read **Galera and RabbitMQ first**, then containers, APIs and these tables.

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

If the result is **`REBOOT_SERVICES_HEALTHY`**, no service repair is needed. Continue to the [VM migration review](phase-05/E-vm-migration.md) when ready. Otherwise, follow the named failure below. The printed TXT report is the readable summary; JSON is extra detail.

<a id="cli-service-checks"></a>
<a id="read-service-and-agent-status-with-the-cli--on-r2d2"></a>

#### Read the service tables

| Column or marker | Meaning |
|---|---|
| `Heartbeat=UP` | OpenStack received a current heartbeat |
| `Heartbeat=DOWN` | No current heartbeat; inspect that component and its dependencies |
| `Admin=enabled/disabled` | Whether OpenStack permits scheduling/work; this does not start a process |
| `Review=-` | That row reports UP/enabled with one expected registration |
| `MISSING` / `NOT LISTED` | An expected registration is absent from a returned list |
| `DUPLICATE` / `UNEXPECTED` | Registration layout needs review |
| `BLOCKED` | Authentication/API reading was unavailable; no DOWN state is invented |

**Check → selected repair → check again:**

1. Find the failed **host and service** in this output.
2. Follow the matching repair below. Use [step 7](#manual-service-and-agent-restarts) for a selected compute/agent process after checking its dependencies.
3. Run `bash scripts/lab-recover.sh --check` again. It prints the service tables again and verifies the wider cluster.

The result `REBOOT_SERVICES_HEALTHY` ends service recovery. VM startup, guest data and migration are separate. [Manual CLI views](command-glossary.md#cli-service-checks--run-on-r2d2) remain available when you want to query a service yourself.

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

### 7. Start or restart one failed service or agent

Use the latest dashboard to select **one affected node and one component**. Galera, RabbitMQ and the relevant dependencies must be ready before repairing their clients. Keep VM/migration/storage operations paused.

**These are different operations:**

| Command | What it does |
|---|---|
| `systemctl status`, `docker inspect`, `journalctl` | Inspect only. They do not start or restart anything. |
| `sudo systemctl start` | Start a stopped service now. |
| `sudo systemctl restart` | Restart a running service; also start it if it is stopped. |
| `sudo systemctl enable` | Configure startup at boot. By itself, it does not start the service now. |

#### A. Connect to the affected node

Choose **one** connection from R2d2:

| Node | Command |
|---|---|
| node1 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200` |
| node2 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.201` |
| node3 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.202` |

Run `hostname -s` to confirm the node. The commands below run **inside that node's SSH session**.

#### B. Start a stopped Nova compute service

If the dashboard shows `nova_compute=EXITED` or systemd shows `inactive (dead)`, run:

```bash
sudo systemctl start kolla-nova_compute-container.service
```

Use the full Kolla unit name. `nova_compute.service` is not the unit in this deployment. You do not need to enable the unit again to start it.

#### C. Restart a selected running service

If a running Nova compute service needs a restart after reviewing its errors/dependencies, run **this action instead**:

```bash
sudo systemctl restart kolla-nova_compute-container.service
```

Both commands above normally return to the prompt without a success message. **Do not run a separate stop first.** Verify the result below.

For another failed component, choose its exact restart command from this quick reference. Each command also starts that unit if it is stopped. **Run only the selected row.**

| Component | Command on the affected node |
|---|---|
| Nova compute | `sudo systemctl restart kolla-nova_compute-container.service` |
| Nova conductor | `sudo systemctl restart kolla-nova_conductor-container.service` |
| Nova scheduler | `sudo systemctl restart kolla-nova_scheduler-container.service` |
| Placement API | `sudo systemctl restart kolla-placement_api-container.service` |
| Neutron Open vSwitch agent | `sudo systemctl restart kolla-neutron_openvswitch_agent-container.service` |
| Neutron router / L3 agent | `sudo systemctl restart kolla-neutron_l3_agent-container.service` |
| Neutron DHCP agent | `sudo systemctl restart kolla-neutron_dhcp_agent-container.service` |
| Neutron metadata agent | `sudo systemctl restart kolla-neutron_metadata_agent-container.service` |
| Cinder scheduler | `sudo systemctl restart kolla-cinder_scheduler-container.service` |

The Open vSwitch **agent** is separate from `openvswitch_db` and `openvswitch_vswitchd`. Nova **compute** is separate from `nova_libvirt`. The commands above do not restart those dependencies.

For API/control containers, step 6 remains the normal automated route. Other application container names include `nova_api`, `nova_metadata`, `neutron_server`, `neutron_rpc_server`, `neutron_periodic_worker`, `cinder_api`, `cinder_scheduler`, `keystone_api`, `keystone_fernet`, `keystone_ssh` and `horizon`. Their unit pattern is `kolla-CONTAINER_NAME-container.service`, preserving underscores. `glance_api` is on node1 only.

**Infrastructure needs a separate procedure/impact review:** MariaDB, ProxySQL, RabbitMQ, HAProxy/Keepalived, libvirt, Cinder volume and Open vSwitch database/switch processes. Starting an agent is not permission to restart all of its dependencies.

#### D. Verify that it actually started

For Nova compute, run on the same node:

```bash
sudo systemctl status kolla-nova_compute-container.service --no-pager --full
sudo docker inspect --format 'State={{.State.Status}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' nova_compute
```

For another component, use its exact unit and container name from the quick reference.

| Output | Meaning |
|---|---|
| `active (running)` plus `State=running Health=healthy` | The unit/container checks passed. Verify the OpenStack heartbeat in step 8. |
| `Health=starting` | Startup verification is in progress; allow it to finish and read the status again. |
| `inactive (dead)`, `failed`, or container `exited` | It is not running. Read the current errors below. |
| Container healthy but OpenStack service `DOWN` | The container check passed, but the service heartbeat did not. Read its application log. |
| OpenStack `UP/disabled` | The heartbeat is working; scheduling permission is still disabled. Read its reason separately. |

Do not report recovery from the restart command alone. If startup or the heartbeat still fails, keep the new error instead of repeating restarts.

<details>
<summary>If start/restart fails, or the heartbeat stays DOWN</summary>

**Read the service manager error** on the affected node. Nova compute example:

```bash
sudo systemctl status kolla-nova_compute-container.service --no-pager --full
sudo journalctl -u kolla-nova_compute-container.service -b -n 40 --no-pager
```

**Read the application log.** Most Kolla application logs are stored in the shared `kolla_logs` volume; the journal or `docker logs` may contain only the startup wrapper.

This finds the volume's actual location, then reads Nova's log even when `nova_compute` is stopped:

```bash
lab_logs=$(sudo docker volume inspect --format '{{.Mountpoint}}' kolla_logs)
if [ -n "$lab_logs" ]; then
  sudo tail -n 60 "$lab_logs/nova/nova-compute.log"
fi
```

For a running Neutron container, use its matching log read:

| Component | Read-only log command |
|---|---|
| Open vSwitch agent | `sudo docker exec neutron_openvswitch_agent tail -n 40 /var/log/kolla/neutron/neutron-openvswitch-agent.log` |
| Router / L3 agent | `sudo docker exec neutron_l3_agent tail -n 40 /var/log/kolla/neutron/neutron-l3-agent.log` |
| DHCP agent | `sudo docker exec neutron_dhcp_agent tail -n 40 /var/log/kolla/neutron/neutron-dhcp-agent.log` |
| Metadata agent | `sudo docker exec neutron_metadata_agent tail -n 40 /var/log/kolla/neutron/neutron-metadata-agent.log` |

Review log excerpts for private information before sharing.

| Error/check | Next action |
|---|---|
| `Unit ... could not be found` | Check the full Kolla unit name and selected host. Do not create a replacement unit. |
| `Access denied` / authentication failure | Use `sudo` and joe's sudo password. |
| Libvirt, RabbitMQ or database connection errors | Check that named dependency; a client restart alone does not establish a fix. |
| `Start request repeated too quickly` / `start-limit-hit` | Correct the underlying failure first; then clear only that unit's rate-limit state as below. |

Only for the last condition, after the cause is corrected:

```bash
sudo systemctl reset-failed kolla-nova_compute-container.service
sudo systemctl start kolla-nova_compute-container.service
```

This is not a routine recovery step. Kolla accepts exit 143 as a normal service termination; that exit code alone does not establish a crash.

For a read-only local libvirt connection check:

```bash
sudo docker exec --user root nova_libvirt virsh --readonly -c qemu:///system list --all
```

This does not prove Nova's separate authenticated connection works. Its application log is still required for that diagnosis.

</details>

**Return to R2d2** after the node checks:

```bash
exit
```

Continue to [step 8](#what-a-healthy-result-means) and run the single check again. Its tables show whether the heartbeat returned; a container can become healthy before OpenStack receives its heartbeat.

**Linux startup and OpenStack scheduling are separate.** `systemctl enable` controls boot startup. Nova's `enabled/disabled` controls scheduling. If a service reports UP/disabled, review its reason before allowing new work; it may be deliberately in maintenance. [Optional service views below](#8-optional-service-and-agent-views) show the reason.

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

Finish when the result is **`REBOOT_SERVICES_HEALTHY`**. Next, [Chapter E](phase-05/E-vm-migration.md) reviews VM and volume locations before a migration.

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

The routine command is `lab-recover.sh --check`; these extra views are optional. Keep existing receipts and VM/migration records.

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

[Ubuntu 24.04 systemctl manual](https://manpages.ubuntu.com/manpages/noble/man1/systemctl.1.html) · [Kolla troubleshooting and logs](https://docs.openstack.org/kolla-ansible/2026.2/user/troubleshooting.html) · [Pinned Kolla systemd units](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/module_utils/kolla_systemd_worker.py) · [Pinned MariaDB recovery](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/mariadb/tasks/recover_cluster.yml)

[CLI compute service list](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/compute/v2/index.html#compute-service-list) · [CLI network agent list](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/network/v2/index.html#network-agent-list) · [Cinder service verification](https://docs.openstack.org/cinder/latest/install/cinder-verify.html)
