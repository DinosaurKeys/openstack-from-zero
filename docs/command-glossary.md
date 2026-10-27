# Lab viewing commands

[Home](../README.md) · [Phase 4](phase-04/README.md)

These commands inspect the lab. They do not create/delete resources or restart services. The API helper authenticates normally, then sends GET requests; it uses the existing private profile and CA, so no OpenStack CLI installation is required.

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
| Network agents | `/usr/bin/python3 scripts/lab-view.py agents` | `alive: true` and `admin_state_up: true`; host and agent role |
| Networks | `/usr/bin/python3 scripts/lab-view.py networks` | Flat external versus VXLAN private; network MTU |
| Subnets | `/usr/bin/python3 scripts/lab-view.py subnets` | CIDR, gateway, DHCP, pool and DNS |
| Images | `/usr/bin/python3 scripts/lab-view.py images` | `active` means available for boot, not that a VM booted |
| VM sizes | `/usr/bin/python3 scripts/lab-view.py flavors` | RAM in MB, disk in GB, vCPUs |
| Guest firewall rules | `/usr/bin/python3 scripts/lab-view.py security-groups` | Direction, protocol, ports and allowed source CIDR |
| VMs | `/usr/bin/python3 scripts/lab-view.py servers` | Name, status and assigned addresses; read actual placement and addresses |
| Floating IPs | `/usr/bin/python3 scripts/lab-view.py floating-ips` | External IP, mapped private IP and status |
| Volumes | `/usr/bin/python3 scripts/lab-view.py volumes` | Size, status and attachments; a root disk on local compute storage is not listed here |
| Nova services | `/usr/bin/python3 scripts/lab-view.py compute-services` | Service host, `state: up` and `status: enabled` |

These are fresh API reads; a list command does not test end-to-end operation. Keep credentials private: do not print `clouds.yaml`, openrc files or token values.

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

Use the existing combined verification rather than interpreting container health as full database health:

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/phase3-verify.py --run
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
