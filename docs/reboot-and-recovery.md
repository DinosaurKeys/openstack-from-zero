# Shutdown, power-on and recovery

[Home](../README.md) · [Command glossary](command-glossary.md)

Use this page for a **whole-lab shutdown and startup**. Run commands on **R2d2 as joe**, from `~/openstack-from-zero`. Keep R2d2 running.

| Order | Action | Command / result |
|---|---|---|
| 1 | Stop OpenStack services | `python3 scripts/lab-shutdown.py --apply` → wait for `READY_FOR_MANUAL_POWEROFF` |
| 2 | Power off the three hosts | `bash scripts/lab-poweroff.sh --apply` |
| 3 | Power the hosts on | node3 first, then node2 and node1; let all three boot |
| 4 | Read the live dashboard | `bash scripts/lab-recover.sh --check` |
| 5 | Only if MariaDB needs recovery | Use the coordinated database command in step 5; skip it when Galera is healthy |
| 6 | Recover failed control services | `bash scripts/lab-recover.sh --apply` → review, confirm, repair, final check |

- [Shutdown: stop services, then power off](#normal-shutdown-two-steps)
- [Power on and check the cluster](#after-power-on-check-the-cluster)
- [Recover only what needs repair](#recover-only-what-needs-repair)
- [What a healthy result means](#what-a-healthy-result-means)
- [If shutdown is interrupted](#only-if-shutdown-is-interrupted)
- [Manual commands and extra views](#manual-reference)

Get the current scripts before starting:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

<a id="7-future-reboot-and-shutdown"></a>
<a id="full-shutdown--node1-node2-and-node3"></a>

## Normal shutdown: two steps

Finish active cloud/storage operations and **cleanly shut down running VMs first**. These scripts do not shut down guests. The service-stop check blocks active VM domains, including paused VMs. Leave the hosts powered on while services are stopping.

### 1. Stop OpenStack services

```bash
/usr/bin/python3 scripts/lab-shutdown.py --apply
```

Enter joe's sudo passwords. The script checks all three nodes before making changes. If readiness fails, it does not begin stopping services. Type **`STOP ALL CLOUD SERVICES`** to approve.

| Stage | What stops | Why this order |
|---|---|---|
| 1 | OpenStack applications, workers, agents and remaining DHCP/router helpers | Stop the users of the database and messaging first. |
| 2 | Proxies/API VIP services, then RabbitMQ | Applications have stopped using these services. |
| 3 | MariaDB on node1, then node2, then node3 | Stop the database last and record each member's clean exit and position. |

This can take several minutes. Progress and a private report are printed. **Ubuntu is still running at this point.** Wait for:

```text
RESULT: READY_FOR_MANUAL_POWEROFF
```

This means the service-stop and database-position checks passed. **Node3 was the last database/broker member stopped.** It does not mean the hosts are powered off.

If you get `SHUTDOWN_NEEDS_REVIEW`, leave the hosts on and keep the report. Use [the interrupted-shutdown section](#only-if-shutdown-is-interrupted); do not repeat the normal apply or proceed to poweroff.

### 2. Power off Ubuntu

Only after `READY_FOR_MANUAL_POWEROFF`:

```bash
bash scripts/lab-poweroff.sh --apply
```

Type **`POWER OFF ALL NODES`** and enter joe's sudo passwords. The script checks the completed service-stop record, verifies that containers and VM processes are stopped, then schedules normal Ubuntu poweroff in **one minute** on each node.

Wait until **all three physical hosts are off** before removing electricity. Acknowledged shutdown requests are not proof that the machines have finished powering off. If a request fails, earlier hosts may already have poweroff scheduled; keep the output and review it.

A separate `lab-shutdown.py --check` is optional: `--apply` already checks readiness. **`--finish` is not part of a normal successful shutdown.**

<a id="1-check-after-power-on"></a>

## After power-on: check the cluster

### 3. Start the hosts

1. Power on **node3** first: it was the last database/broker member stopped by the procedure above.
2. Power on **node2 and node1**. Do not wait for a healthy database before starting them.
3. Let all three Ubuntu hosts boot, then run the check from R2d2.

Starting node3 first does **not** itself bootstrap Galera. A complete database shutdown can require coordinated recovery to form a Primary cluster again. This is different from losing just one member while the others remain online.

### 4. Read the live dashboard

```bash
bash scripts/lab-recover.sh --check
```

This collects the existing service/agent checks and displays a short summary. It changes no services. Enter joe's sudo password for each node. Healthy groups show one PASS line; failed groups name the node and container. The full by-node tables remain available in the optional reference.

| Output | Plain meaning |
|---|---|
| `PASS` | That check passed. |
| `FAIL` | The observed check did not pass. The affected node/container is shown. |
| `BLOCKED` | The check could not finish or its dependency was unavailable. It does not prove the service is down. |
| `EXITED exit=0` | The container stopped without reporting an error; it is not currently serving requests. |
| `running/unhealthy` | A process is running, but its configured healthcheck is failing. |
| `running/starting` | Startup has not finished yet. |

Read **MariaDB/Galera first**, then APIs and service/agent heartbeats. If the database is unavailable, several API checks can fail together; that alone does not establish a separate fault in each service. `Horizon` showing a login page is not enough to prove the cloud is working.

- **`REBOOT_SERVICES_HEALTHY`:** stop here; no repair is needed.
- **`REBOOT_SERVICES_NEED_REVIEW`:** use the recovery step below for supported failures.
- **`REBOOT_RECOVERY_NEEDS_REVIEW`:** the checker could not finish; read its error first.

The report path is printed under `reports/`. Share the short **TXT** report when review is needed; you do not need to read the JSON.

## Recover only what needs repair

Follow the same dependency order as the [previous lab's runbook](https://github.com/DinosaurKeys/openstack-zero-to-hero/blob/main/docs/openstack-reboot-and-recovery-runbook.md): **MariaDB → ProxySQL → Placement → control services → API and agent checks**.

<a id="5-mariadb-cold-boot-recovery"></a>

### 5. Recover the database — only if needed

| MariaDB/Galera result | Action |
|---|---|
| All three Synced/Primary, connected/ready, size 3 and same UUID | **Skip this step.** Leave the database running. |
| All three database containers confirmed stopped, with exit 0, after a whole-lab shutdown | Review this state, then run coordinated Kolla recovery below. |
| A Primary survives, a node is unreachable, or the state is partial/starting/unclear | Return the report for review. Do not select a bootstrap member or restart all database members blindly. |

For the reviewed complete database outage, run once from R2d2:

```bash
source /home/joe/venvs/kolla/bin/activate
ANSIBLE_BECOME_ASK_PASS=True kolla-ansible mariadb-recovery \
  --configdir "$HOME/openstack-from-zero/private/phase2/kolla" \
  -i "$HOME/openstack-from-zero/private/phase2/inventory.ini" \
  --limit baremetal --become \
  -e ansible_python_interpreter=/usr/bin/python3
```

Kolla checks the recovery positions, chooses the starting member and rejoins the others. **No separate manual MariaDB restart is needed first.** Do not edit `grastate.dat`, force `safe_to_bootstrap`, or run `galera_new_cluster` yourself.

Require `failed=0` and `unreachable=0` for all three nodes, then check:

```bash
bash scripts/lab-recover.sh --check
```

Continue only when Galera is **3/3 Synced/Primary, size 3, connected/ready, same UUID**, and ProxySQL/RabbitMQ checks pass. If recovery fails, keep the output and return it; do not repeat recovery or delete database volumes.

### 6. Recover failed control services

When the database is healthy but control services remain stopped or unhealthy:

```bash
bash scripts/lab-recover.sh --apply
```

The command checks all three nodes again, leaves healthy MariaDB running, and prints a small plan table. Each cell shows the observed state and proposed action:

| Cell | What it will do |
|---|---|
| `healthy / skip` | Leave that service running. |
| `unhealthy / restart` or `stopped / restart` | Request one restart of that exact Kolla service, then wait for it to become healthy. |
| `starting / wait` | Observe this service when its turn comes. If it becomes healthy, skip it. If it stops or becomes unhealthy, it is eligible for one approved restart. |

Review the named containers and nodes, then type **`RESTART LISTED SERVICES`** to approve. For a preview without changes, `bash scripts/lab-recover.sh --plan` is optional; a separate plan run is not required.

The supported containers are **Placement; Nova API, metadata, conductor and scheduler; Neutron server, RPC server and periodic worker; Cinder API**. There are at most 27 candidates across the three nodes; healthy services are skipped. Placement and the failed APIs are handled before the background workers.

A starting control worker does not block unrelated approved repairs. The command waits up to 90 seconds for a starting target at its turn. If it is still starting, it prints **DEFER**, sends no restart for that target, and continues the other approved repairs. The root helper checks again immediately before restarting, so a newly starting process is also left alone.

The final dashboard still reports deferred or failed services as needing attention. **DEFER does not mean repaired.** Missing observations, changed identities or failed database/broker/proxy dependencies stop the procedure. A failed restart or unknown outcome stops further repairs and preserves its progress record; return the report instead of repeatedly applying or deleting that record.

This step does not restart ProxySQL, RabbitMQ, compute/libvirt, network agents or Cinder volume services, and does not enable disabled computes. Those require separate review if their checks fail. VM and migration records remain unchanged.

## What a healthy result means

Successful verification ends with:

```text
RESULT: REBOOT_SERVICES_HEALTHY
```

| Component | Required result |
|---|---|
| Hosts | node1/node2/node3 accessible; expected clock and USB/br-ex checks pass |
| MariaDB/Galera | 3 Synced/Primary members, connected/ready, size 3, same UUID |
| RabbitMQ | 3 running members, no alarms or partitions |
| API VIP | Exactly one owner of `192.168.0.151` |
| Core containers | Running and healthy where a healthcheck exists; Glance on node1 only |
| HTTPS | Authentication, Horizon and the image/compute/network/volume/placement reads pass |
| Nova and hypervisors | 9 expected Nova services and 3 hypervisors up/enabled |
| Cinder | 6 expected scheduler/volume services up/enabled |
| Neutron | 12 expected agents alive/enabled; router one active and two standby |

This confirms **cluster service recovery**. It does not start a VM, repair a migration, prove guest traffic, or verify guest data. Those remain separate exercises. If APIs pass but an agent heartbeat is down, the final result still needs review; the helper does not silently restart that agent.

<a id="finish-an-interrupted-network-helper-stop"></a>

## Only if shutdown is interrupted

<details>
<summary>Open only for a reviewed interruption in the DHCP/router-helper shutdown stage</summary>

Keep the private TXT report and saved progress file. Some services may already be stopped.

```bash
/usr/bin/python3 scripts/lab-shutdown.py --finish
```

`--finish` checks the saved stage and current state on all three nodes, then asks for confirmation. It skips the completed first service-stop stage and continues with the remaining helpers, proxies, RabbitMQ and MariaDB. Ubuntu stays on.

It is not a general retry: another failure stage, unhealthy dependencies, active VMs/clients or unclear progress blocks it. Do not delete the saved progress file. After `READY_FOR_MANUAL_POWEROFF`, use **step 2**. If it fails, keep the hosts on and return the report.

</details>

<a id="2-read-the-result-and-choose-the-action"></a>
<a id="if-a-cluster-check-fails"></a>

## Manual reference

Use these commands only for a separately reviewed component. The normal recovery command above already checks and verifies its supported repairs.

- A healthy container with a `DOWN` service heartbeat needs application/dependency review.
- `disabled` needs its disable reason reviewed; restarting does not authorize enabling it.
- `running/no-check` means process-only evidence, not a successful application healthcheck.
- Recent Galera log views are labelled with their log time; they are not live SQL status.

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

<a id="4-supported-api-recovery-helper"></a>

### Fixed API recovery profile

<details>
<summary>Special-purpose helper; use lab-recover.sh for this runbook</summary>

Use **lab-recover.sh** for the reboot procedure on this page. `lab-recover-api.py` has a separate fixed 14-container scope and does not include all Nova control workers. It retains its original scope and progress file; do not delete that file or rerun a completed apply to force another repair.

The reboot helper keeps its own progress records and preserves existing API and VM/migration records.

</details>

<a id="6-confirm-service-recovery"></a>

The recovery command already performs a final service/agent dashboard. For a later independent snapshot, run `bash scripts/lab-recover.sh --check`. A healthy container alone does not prove application operation.

<a id="8-optional-service-and-agent-views"></a>

### Extra checks and component views

<details>
<summary>Extra checks, saved reports and individual component views</summary>

| Optional command | Purpose |
|---|---|
| `/usr/bin/python3 scripts/lab-reboot-verify.py --check --details` | Show the full by-node tables instead of the grouped summary. Also works with `--report FILE --details` to redisplay saved tables. |
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
