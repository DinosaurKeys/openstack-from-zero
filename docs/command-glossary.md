# Lab viewing commands

[Home](../README.md) · [Phase 4](phase-04/README.md)

These commands inspect the lab. They do not create/delete resources or restart services. The API helper authenticates normally, then sends GET requests; it uses the existing private profile and CA, so no OpenStack CLI installation is required.

## Router versus API VIP

| Name | Meaning | Last reviewed value |
|---|---|---|
| API VIP | Shared address for Horizon and OpenStack APIs; hosted by one controller at a time | `192.168.0.151` |
| Virtual router | Software router joining the private and external networks | `lab-router`, scheduled on node1 |
| Router private gateway | The future VM's gateway | `10.10.10.1` |
| Router external IP | The router port facing the home LAN; used for outbound source NAT | `192.168.0.192` |
| Floating IP | An external address mapped to a VM's private address | None created yet |

The router is **not the API VIP**. It has two network sides, like a small router with a LAN and WAN port. This router is explicitly non-HA. Its external address is not a VIP shared by router replicas. Neither router address is assigned to the USB host interface.

## 1. OpenStack objects — run on R2d2 as joe

First update the course:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

| What to see | Command | What to read |
|---|---|---|
| Router, its ports and hosting agent | `/usr/bin/python3 scripts/lab-view.py router` | Router status, private/external IPs and `hosting_agents` |
| Network agents | `/usr/bin/python3 scripts/lab-view.py agents` | `alive: true` and `admin_state_up: true`; host and agent role |
| Networks | `/usr/bin/python3 scripts/lab-view.py networks` | Flat external versus VXLAN private; network MTU |
| Subnets | `/usr/bin/python3 scripts/lab-view.py subnets` | CIDR, gateway, DHCP, pool and DNS |
| Images | `/usr/bin/python3 scripts/lab-view.py images` | `active` means available for boot, not that a VM booted |
| VM sizes | `/usr/bin/python3 scripts/lab-view.py flavors` | RAM in MB, disk in GB, vCPUs |
| Guest firewall rules | `/usr/bin/python3 scripts/lab-view.py security-groups` | Direction, protocol, ports and allowed source CIDR |
| VMs | `/usr/bin/python3 scripts/lab-view.py servers` | Name, status and assigned addresses; empty until a VM is created |
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

## 3. See inside lab-router — on node1

The Chapter B API report placed it on node1. First confirm its current host with `lab-view.py router`. In this legacy non-HA design, Neutron implements a router in a Linux network namespace named `qrouter-<router-ID>`.

After SSH to node1:

```bash
sudo ip netns list
sudo ip netns exec qrouter-268e8f69-cda4-422c-933c-108588bedc8f ip -br address
sudo ip netns exec qrouter-268e8f69-cda4-422c-933c-108588bedc8f ip -4 route
```

The address command should show the private `10.10.10.1` interface and external `192.168.0.192` interface from the reviewed report. This is the command to confirm their realization on the host; its live output has not yet been reviewed. `qr-…` denotes a private router interface; `qg-…` denotes its external gateway interface. Do not expect those addresses on the host USB NIC.

If the namespace is absent or the current API host differs, return the output; do not create a namespace manually. These commands do not send test pings or change routes.

## 4. Check the MariaDB cluster — from R2d2

Use the existing combined verification rather than interpreting container health as full database health:

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/phase3-verify.py --run
```

It reads all three nodes, asks for their sudo passwords and writes a private report. Look for each member: `Synced`, `Primary`, cluster size `3`, `wsrep_connected: ON`, `wsrep_ready: ON`, and one shared cluster UUID. The wrapper also checks containers, host layout and VIP ownership; no repair is performed.

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
