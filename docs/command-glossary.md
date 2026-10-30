# Lab command glossary

[Home](../README.md) · [Phase 4](phase-04/README.md) · [Reboot and recovery](reboot-and-recovery.md) · [Restart a failed service/agent](reboot-and-recovery.md#3-restart-one-failed-service-or-agent)

Use this page as a command reference. Run OpenStack commands **on R2d2 as joe**. VM power commands and the manual API VIP exercise change state; choose only the action you intend. The other viewing commands inspect existing resources. Standard CLI setup is part of [Phase 3 D](phase-03/D-client-access.md).

## OpenStack CLI commands

[Load CLI](#load-the-cli-once-per-terminal) · [View VMs](#view-vms-and-their-locations) · [VM power](#cli-vm-power) · [CPU/RAM/resources](#cpu-ram-and-resource-capacity) · [Disks](#volumes-and-storage-locations) · [Networking](#networks-ips-and-firewall-rules) · [Services/agents](#cli-service-checks--run-on-r2d2) · [Guest data check](#cinder-data-volume-checks)

Examples use `lab-vm-01`, `lab-data-01`, `lab.small` and `node2`. Replace these with the names shown by your list commands. Use the full UUID when names are duplicated or the object belongs to another project. Cross-project views, host fields and infrastructure checks require admin policy access.

### Load the CLI once per terminal

After [installing/connecting the CLI](openstack-cli.md), run:

```bash
source "$HOME/venvs/openstack-client/bin/activate"
source "$HOME/openstack-from-zero/private/phase2/kolla/public-openrc.sh"
```

Repeat those two lines in each new terminal. They load the installed client and existing credentials/CA settings. No routine Git pull or reinstallation is needed. Keep openrc files and tokens private.

To check the connection without printing a token:

```bash
openstack token issue -f value -c expires
```

Expected: an expiry date/time. A command error needs review; it is not evidence of a successful login.

### View VMs and their locations

| What to see | Command | What to read |
|---|---|---|
| VMs in the current project | `openstack server list` | Name, ID, status, network addresses and image/flavor |
| All projects, including compute host | `openstack server list --all-projects --long -c Name -c Status -c Host -c ID` | `Host` is the VM's compute node |
| VMs assigned to node2 | `openstack server list --all-projects --host node2 --long -c Name -c Status -c Host -c ID` | Current Nova assignment, not a physical runtime check |
| One VM's power and current task | `openstack server show lab-vm-01 -c name -c status -c OS-EXT-SRV-ATTR:host -c OS-EXT-STS:task_state -c OS-EXT-STS:power_state` | `task_state=None` means Nova reports no current task |
| VM addresses and configured size | `openstack server show lab-vm-01 -c name -c addresses -c flavor` | Floating/private addresses and flavor |
| Attached Cinder disks | `openstack server volume list lab-vm-01` | Volume UUID and guest device attachment; use `volume show` below for storage location |
| Recent VM actions | `openstack server event list lab-vm-01 --long --max-items 5` | Action, time, request ID and result |
| Recent console boot text | `openstack console log show --lines 40 lab-vm-01` | Guest boot messages; keep private logs out of public issues |

`ACTIVE` is Nova's reported state. SSH/console access and guest data are separate checks. `SHUTOFF` means powered off; `ERROR` needs investigation before a new power or migration action.

<a id="cli-vm-power"></a>

### Start, shut down and restart VMs

**These commands change VM power. Choose one action for one VM, then check the result.**

| Action | Command | Starting state / expected result |
|---|---|---|
| Start | `openstack server start lab-vm-01` | From `SHUTOFF`; wait for `ACTIVE` |
| Shut down | `openstack server stop lab-vm-01` | From `ACTIVE`; wait for `SHUTOFF`. Retains the VM and its disks |
| Request a normal guest restart | `openstack server reboot --soft lab-vm-01` | From `ACTIVE`; allow the guest to restart, then check console/SSH |

Stopping asks the guest to shut down; Nova can power it off after the configured shutdown timeout. Finish application writes before choosing it. A soft reboot requests a guest restart; an `ACTIVE` status alone does not prove the guest finished booting.

A successful power request may return **no text**. Recheck with:

```bash
openstack server show lab-vm-01 -c name -c status -c OS-EXT-STS:task_state
```

For `ERROR`, use the [actual-runtime/recovery check](#vm-power-controls) first. This reference does not prescribe a state reset, hard reboot or migration retry. After a guest restart, check the course data disk's [existing mount and file](#cinder-data-volume-checks).

### CPU, RAM and resource capacity

| Question | Command | Meaning |
|---|---|---|
| What hardware size is assigned to my VM? | `openstack server show lab-vm-01 -c flavor` | Find its flavor name/ID |
| What sizes can I choose? | `openstack flavor list` | vCPUs, RAM in MiB and root-disk size in GiB |
| How many vCPUs, RAM and root-disk GiB does this flavor assign? | `openstack flavor show lab.small -c name -c vcpus -c ram -c disk` | Configured VM resources, not live consumption |
| What is my project's quota and current allocation? | `openstack quota show --usage` | `Limit`, `In Use` and `Reserved`; for example, vCPU cores, RAM in MiB and volume GiB |
| Which compute hosts are registered/up? | `openstack hypervisor list` | Host identity and reported state |
| What does Nova report for one hypervisor? | `openstack hypervisor show node2` | Host/service details; available fields depend on API version |
| How much capacity do the Cinder pools report? | `openstack volume backend pool list --long` | Storage backend names and reported capacity |

**Flavor = configured VM hardware. Quota = project allowance/allocation. Host usage = what Ubuntu is actually using.** Quota headroom is not free physical RAM. A pool's capacity report is not a storage-HA test.

For actual host CPU/RAM/filesystem usage, [SSH to the selected node](#2-host-components--connect-from-r2d2) and use `lscpu`, `free -h`, `top` and `df -h`. Inside a guest, the same commands show that VM's view. Press `q` to leave `top`.

Some Nova API versions omit old hypervisor CPU/RAM/disk counters because those counters could be misleading. Use the host tools for actual usage. For the lab's migration readiness checks, use [the preview menu](phase-05/E-vm-migration.md), which separately reads host resources and Placement.

### Volumes and storage locations

| What to see | Command | What to read |
|---|---|---|
| Cinder disks in the current project | `openstack volume list` | Name, size, status and attachments |
| Cinder disks across projects | `openstack volume list --all-projects -c Name -c Status -c Size -c ID` | Admin view; use UUIDs for cross-project inspection |
| One disk's identity, size and location | `openstack volume show lab-data-01 -c id -c name -c status -c size -c os-vol-host-attr:host` | Actual Cinder storage backend |
| Which VM uses that disk? | `openstack volume show lab-data-01 -c id -c attachments` | Attached VM UUID and device; an attachment's host is the compute host |
| Full disk details, including migration fields | `openstack volume show lab-data-01` | Read current backend, attachment and migration state together |
| All available storage pools | `openstack volume backend pool list --long` | For example, `node2@lvm-1#lvm-1`; inspect before selecting a storage destination |

Read a backend such as **`node2@lvm-1#lvm-1`** as **storage on node2, backend/pool lvm-1**. `in-use` means attached; it does not prove that a VM is powered on or that its filesystem is mounted. `available` means detached and available for attachment.

The course VM's local Ubuntu OS disk is on its compute node; it does not appear as a Cinder volume. To see both disks inside the guest, use [the disk/mount/file check below](#cinder-data-volume-checks). Disk viewing requires no format, detach or mount command.

### Networks, IPs and firewall rules

| What to see | Command | What to read |
|---|---|---|
| Networks | `openstack network list` | Private and external network IDs/names |
| Subnets | `openstack subnet list` | Network IDs and CIDRs |
| Private subnet settings | `openstack subnet show lab-private-subnet` | Gateway, DHCP, allocation pool and DNS servers |
| Floating-IP mappings | `openstack floating ip list` | External address, mapped fixed address and port |
| The VM's ports | `openstack port list --server lab-vm-01` | IP, MAC, port ID and status |
| Routers | `openstack router list` | Router names, IDs and status |
| Router configuration | `openstack router show lab-router` | HA setting and external gateway; `ACTIVE` alone does not show its forwarding host |
| Router active/standby hosts | `openstack network agent list --router lab-router -c Host -c "HA State" -c Alive -c State` | Read all router replicas; a healthy lab has one active and two standby, with agents alive/enabled |
| Security groups | `openstack security group list` | Guest firewall groups |
| Rules in the course VM's group | `openstack security group rule list lab-vm-access` | Direction, protocol, port range and allowed source |

The router's HA role differs from the API VIP owner. These commands read API state; use guest checks to test actual traffic. The existing `/usr/bin/python3 scripts/lab-view.py router` helper also shows router ports and replicas when run from `~/openstack-from-zero`.

### Images and SSH key registrations

| What to see | Command | Meaning |
|---|---|---|
| Boot images | `openstack image list -c ID -c Name -c Status` | An `active` image is ready for use, not proof that a VM booted |
| Registered guest public keys | `openstack keypair list` | Keypair names/fingerprints; the private key remains on R2d2 |

### CLI service checks — run on R2d2

For the routine reboot check, run **`bash scripts/lab-recover.sh --check`** from `~/openstack-from-zero`. It displays all three service tables using its existing API snapshot. The CLI commands below are optional independent views after loading the CLI:

```bash
openstack compute service list -c Host -c Binary -c Status -c State
openstack network agent list -c Host -c "Agent Type" -c Alive -c State
openstack volume service list -c Host -c Binary -c Status -c State
```

These inspect registrations; they do not restart anything. For **Nova/Cinder**, `State=up` is the heartbeat and `Status=enabled` is administrative permission to accept work. For **Neutron**, `Alive=:-)` is the heartbeat and `State=UP` is administrative enablement; `Alive=XXX` indicates a missing heartbeat. Use the [check → selected restart → check again guide](reboot-and-recovery.md#cli-service-checks) for recovery, or [Chapter E](phase-05/E-vm-migration.md) to review VM and storage locations before migration.

### Find help for a command

```bash
openstack help server list
openstack help server start
openstack help volume show
```

`help` shows syntax without sending the corresponding cloud action. `-c` selects output columns. `--all-projects` changes the scope of supported list commands; it does not give a name lookup cross-project scope. Use the object's UUID when needed.

[Compute commands](https://docs.openstack.org/python-openstackclient/2026.2/cli/command-objects/compute/v2/index.html) · [Volume commands](https://docs.openstack.org/python-openstackclient/2026.2/cli/command-objects/volume/v3/index.html) · [Network commands](https://docs.openstack.org/python-openstackclient/2026.2/cli/command-objects/network/v2/index.html) · [Quota commands](https://docs.openstack.org/python-openstackclient/2026.2/cli/command-objects/common/index.html#quota-show) · [Nova API history, section 2.88](https://docs.openstack.org/nova/2026.2/reference/api-microversion-history.html)

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

Enter the project folder:

```bash
cd ~/openstack-from-zero
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

### Migration menu — on R2d2

| Command | Meaning |
|---|---|
| `bash scripts/lab-migrate.sh` | Select a move, check it and display its plan; requests it only after `MIGRATE` approval |
| `bash scripts/lab-migrate.sh --plan` | Preview selections and readiness without requesting a move |
| `bash scripts/lab-migrate.sh --observe` | Read current VM/storage locations and migration history; change no tracking records |
| `bash scripts/lab-migrate.sh --reconcile` | Review and acknowledge one old failed local task; no VM power, migration or disk change |

`PENDING` is a saved local task awaiting review. It is separate from Nova's current operation. `COLD VM move (migration)` and `LIVE VM move (live-migration)` are different operations; success of one does not verify the other. [Step-by-step migration guide](phase-05/E-vm-migration.md).

For the diagnosed oslo.messaging 18.3.0 bug, [the Nova messaging repair](phase-05/nova-messaging-repair.md) explains `/usr/bin/python3 scripts/lab-nova-messaging.py --apply` (review and approve six service changes) and `--check` (read settings/health only). These do not request a migration or recover a VM.

<a id="vm-power-controls"></a>

<a id="start-or-shut-down-a-vm--use-horizon"></a>

### Start or shut down a VM — CLI or Horizon

For CLI controls, use [the VM power commands above](#cli-vm-power): start from `SHUTOFF`, stop from `ACTIVE`, or request a soft guest restart. Read the selected VM's current state and recheck after one action. VM power control differs from restarting an OpenStack service container.

For Horizon, sign in at **https://192.168.0.151/**, choose the project, then open **Project → Compute → Instances**. Find the VM by name/ID and choose **Start Instance** or **Shut Off Instance** for the corresponding state above.

The [CLI reference](openstack-cli.md) covers installation, activation and normal power commands. VM power control is separate from restarting an OpenStack service container.

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
| Host CPU layout | `lscpu` | Logical CPUs, cores/threads and CPU model |
| Current CPU/process usage | `top` | Live host usage; press `q` to exit |
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

[Chapter E](phase-04/E-cinder-volume.md) explains disk identification, initialization and persistence. [CLI volume commands above](#volumes-and-storage-locations) show the current Cinder backend and attachment.

For the original Phase 4 exercise, before any storage-backend move, this verifies its saved checkpoint on R2d2:

```bash
/usr/bin/python3 scripts/phase4-volume.py --verify
```

That verifier checks the original saved backend. After an approved Phase 5 storage move, use the fresh CLI location/attachment view and the guest check below. Preserve the original checkpoint; do not edit it to match a new backend.

**Inside the Ubuntu guest**, these commands show its disks, mount and file:

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,SERIAL
findmnt --mountpoint /mnt/lab-data
sudo cat /mnt/lab-data/persistence-test.txt
sudo sha256sum /mnt/lab-data/persistence-test.txt
```

This is a separate data volume, not the VM's local root disk. A mount or persistent file does not prove storage HA. No format command is needed to view a disk. If the original filesystem is unmounted after a guest reboot, follow [the existing-filesystem mount procedure](phase-04/E-cinder-volume.md#after-a-guest-reboot-or-cold-move).

### Course VM: automatic file comparison from R2d2

Use this for the tracked `lab-vm-01` and its original test file, before and after a migration. First confirm the VM's current floating IP with `openstack server show lab-vm-01 -c addresses`. The command below uses its reviewed `192.168.0.194` address and pinned SSH host identity.

**Run on R2d2**; SSH runs the enclosed checks inside the guest. It mounts nothing and changes no disks:

```bash
ssh -T -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$HOME/openstack-from-zero/private/phase4/lab-vm-01-known-hosts" \
  -i "$HOME/.ssh/openstack-from-zero-vm" \
  ubuntu@192.168.0.194 'sudo -n bash -s' <<'GUEST'
set -eu
echo "VM: $(hostname)"
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
if ! findmnt --mountpoint /mnt/lab-data --output TARGET,SOURCE,FSTYPE,UUID; then
  echo "FAIL: data filesystem is not mounted"
  exit 1
fi
if [ "$(findmnt -n --mountpoint /mnt/lab-data -o UUID)" != f1568275-f734-4bbc-867e-26a822d2cb0d ]; then
  echo "FAIL: mounted filesystem UUID differs from the saved course volume"
  exit 1
fi
echo "PASS: original data filesystem is mounted"
printf '%s  %s\n' \
  '5e0db26dd46d13816be7b9afbd038b101b062b88b08031692a12c8d8c9e060c4' \
  '/mnt/lab-data/persistence-test.txt' | sha256sum --check
GUEST
```

Expected results:

```text
PASS: original data filesystem is mounted
/mnt/lab-data/persistence-test.txt: OK
```

`OK` means this file matches the original saved checksum. A reported mismatch or missing mount needs review before another move. These checks do not prove storage HA or uninterrupted application traffic. Another VM needs its own verified SSH identity, filesystem UUID and saved checksum; do not reuse this course baseline for an unrelated disk.

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
