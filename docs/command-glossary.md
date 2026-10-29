# Lab command glossary

[Home](../README.md) · [Phase 4](phase-04/README.md) · [Reboot and recovery](reboot-and-recovery.md) · [Restart a failed service/agent](reboot-and-recovery.md#3-restart-one-failed-service-or-agent)

The viewing sections inspect the lab without creating/deleting resources or restarting services. The manual API VIP exercise in section 6 explicitly includes service stop/start commands and requires an approved test. The API helper authenticates normally, then sends GET requests; it uses the existing private profile and CA, so no OpenStack CLI installation is required.

## Router versus API VIP

| Name | Meaning | Last reviewed value |
|---|---|---|
| API VIP | Shared address for Horizon and OpenStack APIs; hosted by one controller at a time | `192.168.0.151` |
| Virtual router | Software router joining the private and external networks | `lab-router`, one active and two standby replicas |
| Router private gateway | The VM's private gateway | `10.10.10.1` |
| Router external IP | The router port facing the home LAN; used for outbound source NAT | Allocated during creation; read `lab-view.py router` |
| Floating IP | An external address mapped to a VM's private address | Allocated in Chapter D; read `lab-view.py floating-ips` |

The router is **not the API VIP**. It has private and external sides. In this course it is HA: one active replica owns the forwarding addresses, and standby replicas can take over. Neutron manages per-router Keepalived separately from the API VIP. Neither router address is assigned to the USB host interface.

## 1. OpenStack objects — run on R2d2 as joe

First update the course:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

| What to see | Command | What to read |
|---|---|---|
| Router, ports, active and standby agents | `/usr/bin/python3 scripts/lab-view.py router` | Router HA flag, private/external IPs and all `hosting_agents`/`ha_state` values |
| Network agents | `/usr/bin/python3 scripts/lab-view.py agents --table` | Twelve expected agents; Alive and Enabled both `yes` |
| Networks | `/usr/bin/python3 scripts/lab-view.py networks` | Flat external versus VXLAN private; network MTU |
| Subnets | `/usr/bin/python3 scripts/lab-view.py subnets` | CIDR, gateway, DHCP, pool and DNS |
| Images | `/usr/bin/python3 scripts/lab-view.py images` | `active` means available for boot, not that a VM booted |
| VM sizes | `/usr/bin/python3 scripts/lab-view.py flavors` | RAM in MB, disk in GB, vCPUs |
| Guest firewall rules | `/usr/bin/python3 scripts/lab-view.py security-groups` | Direction, protocol, ports and allowed source CIDR |
| VMs across projects | `/usr/bin/python3 scripts/lab-view.py servers --table --all-projects` | Name, Nova status, compute host and VM ID; requires admin policy access |
| Floating IPs | `/usr/bin/python3 scripts/lab-view.py floating-ips` | External IP, mapped private IP and status |
| Volumes across projects | `/usr/bin/python3 scripts/lab-view.py volumes --table --all-projects` | Size, status, storage backend, attached VM ID and volume ID; local VM root disks are not listed here |
| Nova services | `/usr/bin/python3 scripts/lab-view.py compute-services --table` | Service host, heartbeat `up` and administrative state `enabled` |
| Hypervisors | `/usr/bin/python3 scripts/lab-view.py hypervisors --table` | Three compute hosts, each enabled/up |
| Cinder services | `/usr/bin/python3 scripts/lab-view.py cinder-services --table` | Three scheduler and three volume services, each enabled/up |

These are fresh API reads; a list command does not test end-to-end operation. Keep credentials private: do not print `clouds.yaml`, openrc files or token values.

Omit `--all-projects` to use the current project. Omit `--table` for the existing JSON view, including VM addresses. `ACTIVE` is Nova's reported state; the [migration preview](phase-05/E-vm-migration.md) also checks actual runtime before a move.

For all container names/states on the three nodes, run **on R2d2**:

```bash
bash scripts/lab-containers.sh
```

Add `node1`, `node2` or `node3` to view only that node. This is a read-only SSH/Docker view, including stopped containers. VMs are guests; Docker containers are the OpenStack services. The individual host commands below remain available.

<a id="vm-power-controls"></a>

### Start or shut down a VM — use Horizon

No OpenStack CLI installation is required for these browser controls:

1. Open **https://192.168.0.151/** and sign in.
2. Select the project containing the VM, then open **Project → Compute → Instances**.
3. Find the VM by name and ID. Open its action menu on the right.

| Current state | Action | Expected result |
|---|---|---|
| `SHUTOFF` | **Start Instance** | Wait for `ACTIVE`, then check console/SSH. |
| `ACTIVE` | **Shut Off Instance**, then confirm | Wait for `SHUTOFF`. The VM and its disks are retained. |
| `ERROR` | Inspect the VM before selecting recovery | A normal Nova start requires `SHUTOFF`; the actual running state must be established first. |

After a power operation, refresh the inventory on R2d2:

```bash
/usr/bin/python3 scripts/lab-view.py servers --table
```

`openstack: command not found` means the CLI did not run and sent no power request. Kolla-Ansible and the OpenStack CLI are separate programs; the course views already use the private HTTPS profile. Use Horizon for routine power controls.

For the tracked course VM in ERROR, run this existing **read-only** check on R2d2 before recovery:

```bash
/usr/bin/python3 scripts/lab-reboot-verify.py --check --with-workload
```

It checks Nova state, the VM UUID's runtime on all three hypervisors, the volume attachment and saved migration record. If it finds a single running VM, it also attempts read-only guest network/data checks. It sends no start, stop, reboot, reset, mount, migration or checkpoint-clear action. Return the printed TXT report for review before choosing a recovery action. A volume marked `in-use` records an attachment; it is not proof that its VM is powered on.

[Horizon instance guide](https://docs.openstack.org/horizon/latest/user/launch-instances.html) · [Nova power-action reference](https://docs.openstack.org/api-ref/compute/)

## 2. Host components — connect from R2d2

For node1:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200
```

Use `.201` for node2 or `.202` for node3. Enter any SSH-key passphrase locally. The following commands run **on the selected node**, with its sudo password when requested.

| What to see | Command | Meaning |
|---|---|---|
| All containers, including stopped ones | `sudo docker ps -a --format 'table {{.Names}}\t{{.Status}}'` | `Up` means running; `(healthy)` is the configured container health check |
| MariaDB container | `sudo docker ps -a --filter name=mariadb --format 'table {{.Names}}\t{{.Status}}'` | Container state only; not a complete Galera cluster check |
| Neutron containers | `sudo docker ps -a --filter name=neutron --format 'table {{.Names}}\t{{.Status}}'` | Container state; API agent liveness is a separate view above |
| NIC/host addresses | `ip -br address` | Management IP, USB link and any current API VIP |
| Host IPv4 routes | `ip -4 route` | Host routes, not the virtual router's private routing table |
| Network namespaces | `sudo ip netns list` | Isolated network contexts used by Neutron |
| OVS bridges and ports | `sudo docker exec openvswitch_vswitchd ovs-vsctl show` | `br-ex`, `br-int`, `br-tun`, their ports and tunnel options |
| Host memory | `free -h` | RAM and available memory |
| Host filesystem space | `df -h / /var/lib/docker` | Space on the relevant filesystems |
| Cinder volume groups | `sudo vgs -o vg_name,vg_size,vg_free,pv_count,lv_count` | Cinder pool capacity; do not use remove/format commands to inspect it |

Exit back to R2d2:

```bash
exit
```

## 3. See inside lab-router

First run `lab-view.py router` on R2d2. Note its **new router ID** and each host's `ha_state`; choose the active host for forwarding-address inspection. SSH to that node using section 2.

```bash
sudo ip netns list
```

Find `qrouter-<the-router-ID-from-your-report>`. Replace `ROUTER_ID` below with that actual ID; this is a placeholder, not a literal namespace:

```bash
sudo ip netns exec qrouter-ROUTER_ID ip -br address
sudo ip netns exec qrouter-ROUTER_ID ip -4 route
```

The active replica should show the private gateway and external IP from the fresh report. Standby replicas can have different address realization; absence of forwarding addresses on a standby is not by itself a failure. `qr-…` is a private router interface, `qg-…` an external gateway interface, and `ha-…` the router HA interface. Namespace/port details must be read from the live host.

If the namespace is absent or the API roles are unknown, return the observations. Do not create namespaces or change routes manually.

## 4. Check the MariaDB cluster — from R2d2

Use the post-reboot health check rather than interpreting container health as full database health:

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/lab-health.py --check
```

It reads all three nodes, asks for their sudo passwords and writes a private report. Look for each member: `Synced`, `Primary`, cluster size `3`, `wsrep_connected: ON`, `wsrep_ready: ON`, and one shared cluster UUID. The wrapper also checks containers, host layout and VIP ownership; no repair is performed.

## 5. Verify the tracked first VM — from R2d2

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/phase4-boot-vm.py --verify
```

This requires Chapter D's tracked VM/port/floating IP. It creates no cloud resources and repeats the identity/guest connectivity checks. Its SSH and HTTP probes produce normal access logs. See [Chapter D](phase-04/D-boot-and-test-vm.md) for the creation scope and interactive SSH command. Keep VM state and the private known-hosts file; do not disable host-key checks to make login work.

## Small glossary

| Term | Meaning |
|---|---|
| Neutron | OpenStack networking service |
| Network agent | A worker process realizing network configuration on a node |
| Glance | Image catalog and image storage service |
| Nova | VM scheduling and compute service |
| Cinder | Block-volume service |
| MariaDB / Galera | OpenStack's relational database / its synchronous replication system |
| Keystone | Authentication and service catalog |
| Horizon | Browser dashboard |
| OVS | Open vSwitch: software switching on the nodes |
| `br-ex` | OVS connection to the external/provider network and USB NIC |
| `br-int` | OVS integration bridge joining VM/service interfaces |
| `br-tun` | OVS tunnel bridge for cross-node private networks |
| VXLAN | Carries private Ethernet traffic inside IP/UDP packets between nodes |
| Namespace | A separate Linux network stack with its own interfaces and routing table |
| Security group | Stateful VM-port firewall rules in this lab |

[Neutron router implementation](https://docs.openstack.org/neutron/latest/contributor/internals/layer3.html) · [OpenStack network-agent command reference](https://docs.openstack.org/python-openstackclient/2026.1/cli/command-objects/network-agent.html)

## Cinder data-volume checks

[Chapter E](phase-04/E-cinder-volume.md) explains disk identification, initialization and persistence. On R2d2, after the exercise completes:

```bash
/usr/bin/python3 scripts/phase4-volume.py --verify
```

Inside the guest, inspect the data disk and mount:

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,SERIAL
findmnt --mountpoint /mnt/lab-data
sudo cat /mnt/lab-data/persistence-test.txt
sudo sha256sum /mnt/lab-data/persistence-test.txt
```

This is a separate data volume, not the VM's local root disk. A mount or persistent file does not prove storage HA. No format command is needed to view a disk.

## 6. Manual API VIP failover and restoration

[Full exercise and automated script: Phase 5 B](phase-05/B-api-vip.md)

**Reference for an approved test, not an instruction to repeat it now.** Unlike the viewing commands above, `systemctl stop/start` changes a service. Use this sequence instead of the automated fault-injection command, never alongside it.

### Step 1 — Find the current owner, from R2d2

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/phase3-verify.py --run
```

Require a passing report and exactly one entry under `vip_owners`. That node currently owns `192.168.0.151`. Router `ha_state` identifies a different component; it does not tell you the API VIP owner.

| Node | Management IP |
|---|---|
| node1 | `192.168.0.200` |
| node2 | `192.168.0.201` |
| node3 | `192.168.0.202` |

### Step 2 — Connect to that owner and keep restoration ready

On R2d2, replace `CURRENT_OWNER_IP` with the management IP you just confirmed:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@CURRENT_OWNER_IP
```

Now **on the owner node**, inspect:

```bash
hostname
ip -4 -o address show dev enp0s31f6
sudo systemctl status kolla-keepalived-container.service --no-pager
sudo systemctl cat kolla-keepalived-container.service --no-pager
```

Confirm the hostname, `.151` on the management interface, an active service, and a unit managing the `keepalived` container. Stop if any detail differs. Keep this SSH terminal open. Have the restoration command ready **before** the stop:

```bash
sudo systemctl start kolla-keepalived-container.service
```

### Step 3 — Stop only API Keepalived, after approval

**On that same original owner node:**

```bash
sudo systemctl stop kolla-keepalived-container.service
sudo systemctl is-active kolla-keepalived-container.service
```

Expected state: `inactive`. `is-active` returns a nonzero exit code for an inactive service; that is expected here. Kolla supervises the container through systemd, so use this service command rather than stopping the container with Docker. The unit's normal stop grace can take up to 60 seconds.

### Step 4 — Check takeover, from a second R2d2 terminal

Read each node's addresses:

```bash
# node1
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@node1 ip -4 -o address show dev enp0s31f6
# node2
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@node2 ip -4 -o address show dev enp0s31f6
# node3
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@node3 ip -4 -o address show dev enp0s31f6
```

Look for the exact address `192.168.0.151` on **one different node**. Then check authentication and the existing VM's API listing:

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/lab-view.py servers
```

Check that `lab-vm-01` is still reported ACTIVE. Open `https://192.168.0.151/` to check dashboard access separately. A working VM API listing is not itself a guest SSH or full browser-login test.

These manual checks are snapshots. Use the automated Chapter B script for timed authentication/API samples; do not infer an exact outage duration from how long manual commands take.

### Step 5 — Restore the original owner, even if a check failed

In the SSH terminal still connected to the node you stopped:

```bash
sudo systemctl start kolla-keepalived-container.service
sudo systemctl is-active kolla-keepalived-container.service
sudo docker ps --filter 'name=^/keepalived$' --format 'table {{.Names}}\t{{.Status}}'
```

Require `active` and a running `keepalived` container. If the SSH session closed, reconnect directly to that original node's **management IP** and run the same start command. Do not choose the new VIP owner as the restoration target.

### Step 6 — Check full health, from R2d2

```bash
/usr/bin/python3 scripts/phase3-verify.py --run
/usr/bin/python3 scripts/lab-view.py servers
```

Require the service/Galera/VIP report to pass, exactly one VIP owner, and successful authenticated VM API access. The VIP may stay on the new owner. Keep the report and review it before another exercise. Do not reinstall services, edit configuration or create another VM for this test.

### Reviewed test result — 27 October 2026

| Item | Observed result |
|---|---|
| Original API VIP owner | node2 |
| Takeover / final owner | node3 / node3 |
| Original service restoration | Confirmed; final host, Galera, API and Horizon checks passed |
| Takeover observed after stop request | 7.3 seconds, including SSH/collection delays |
| Timed API samples | 2 failed out of 3; this is not an exact continuous outage measurement |
| Result | `API_VIP_FAILOVER_VERIFIED` |

This records a service-level API VIP test. It does not prove host-failure recovery, router failover, storage HA or VM migration. Discover the current owner again before any future test.
