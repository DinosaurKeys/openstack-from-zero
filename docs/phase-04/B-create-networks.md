# Chapter B — Create the virtual networks

[Phase 4](README.md) · [Previous: inspected network layout](A-network-inspection.md)

**Status: complete — live creation report reviewed.** Joe ran this procedure from R2d2. The retained commands below reproduce the agreed procedure; no rerun is needed for this successful checkpoint.

## 1. Which traffic uses each NIC?

Both NICs connect to the same home switch. We use them for different traffic roles; they are not physically isolated LANs.

| Traffic in this lab | Path |
|---|---|
| R2d2 SSH to nodes; browser/API access to the API VIP | Built-in `enp0s31f6` and the node management addresses |
| Host package/image downloads; OpenStack database, messaging and service communication | Built-in management NIC |
| Nova downloads a VM image from Glance | Host/service connection over the management network |
| Cinder LVM volume traffic between different nodes when volumes are attached later | Host storage connection over the management network in this two-NIC design; actual volume test remains later |
| Two VMs on the same node and same private network | Local virtual switching; no physical NIC is needed for that VM-to-VM exchange |
| Two VMs on different nodes on the same private network | VXLAN encapsulation across the configured management IPs `.200/.201/.202`, using the built-in NICs |
| A VM reaches the home LAN or internet | Private network → virtual router → external/provider network → `br-ex` → USB NIC on the router's node → switch/home router |
| A LAN client reaches a VM's future floating IP | Switch → router node's USB NIC → `br-ex` → virtual router/NAT → private network → VM; VXLAN over the built-in NIC may also be needed if the VM is elsewhere |

The management NIC therefore also carries **encapsulated private VM traffic between nodes**. The USB NIC is the physical external connection for VM traffic. A VM's compute node and its virtual router's node need not be the same.

These are configured/design paths, not measured VM traffic results. Chapter A confirmed the bridge/tunnel configuration; Chapter D will test a real VM's actual path.

## 2. What we create

| Object | Exact plan |
|---|---|
| External network | `lab-external`: flat, external, physical network `physnet1`, using the already deployed `physnet1:br-ex` mapping |
| External subnet | `lab-external-subnet`: `192.168.0.0/24`; home gateway `192.168.0.1`; pool `192.168.0.152–199`; **Neutron DHCP disabled** |
| Private network | `lab-private`: VXLAN; Neutron chooses its segment ID |
| Private subnet | `lab-private-subnet`: `10.10.10.0/24`; virtual gateway `10.10.10.1`; pool `10.10.10.2–254`; DHCP enabled; DNS `192.168.0.1` |
| Virtual router | `lab-router`: private subnet attached; external gateway on `lab-external`; IPv4 source NAT enabled |

The names are labels for this course. The two networks belong to the existing admin project and are not marked as shared tenant networks in this initial lesson.

**External DHCP stays off** so Neutron does not offer DHCP on your home LAN. The existing home DHCP range remains `.2–.150`. The API VIP `.151` and node management addresses `.200–.202` are outside the OpenStack allocation pool.

One address from `.152–.199` is allocated to the virtual router's external gateway port. The report gives the actual address; do not assume it is always `.152`. That address belongs to a Neutron router port, not the USB host interface. The USB NIC remains unnumbered.

The private DHCP server may itself consume an address from `10.10.10.2–254`. The first VM address is not predetermined. Using the home router as the VM DNS resolver is a configuration choice; successful VM DNS queries will be tested later.

### Address and router scope before applying

Use the approved private subnet only if it does not overlap home, work or VPN destinations you need to reach. The script checks non-default IPv4 routes visible inside R2d2 and existing Neutron subnets. It cannot enumerate hidden Windows/VPN routes or prove every LAN address is unused. The external pool must remain reserved for OpenStack and outside all other DHCP/manual allocations; this command does not reconfigure your home router or perform an ARP address-availability test.

The router is explicitly **legacy, non-distributed and non-HA**, matching the currently inspected routing mode. Neutron schedules it on one of the three live L3 agents; the script reports that host. We do not enable router HA or change global Kolla settings in this chapter. Three controller nodes alone do not make this individual router highly available.

## 3. What changes on the lab?

The script sends authenticated creation requests to Neutron. Its agents realize the networks, DHCP service, virtual-router interfaces, namespaces and forwarding/NAT state on the selected nodes. Network ports and namespaces can therefore appear as a normal consequence of these API operations.

There is no manual Netplan edit, NIC renaming, host-IP assignment, new `br-mgmt`/veth arrangement, Docker reinstall, Kolla reconfigure or service restart. No VM, floating IP or storage volume is created. Security-group configuration remains a later step.

R2d2 stores the plan and created IDs privately at `private/phase4/networks.json`, plus one report under `reports/`. Keep that state file: it lets the script distinguish its own resources on a later rerun. It contains IDs/addresses, not authentication tokens or passwords, and stays out of Git with the existing private folder.

## 4. How the script avoids overwriting existing work

1. Reuse the pinned tools, existing admin profile, HTTPS CA and reviewed configuration. No node SSH or sudo password is needed.
2. Check local visible route overlap, Neutron API extensions, existing names/subnets/flat physical networks and all 12 required network agents.
3. Refuse an existing object with the intended name unless its exact ID is already tracked by this script. Do not adopt, delete or rewrite unrelated resources.
4. Create two networks, two subnets and the router, recording each returned ID. Attach the private subnet to that router.
5. Read the objects back, check their properties and wait up to three minutes for both router ports to report ACTIVE. Record the scheduled router host, external IP and actual network MTUs.
6. Verify the original private deployment inputs stayed unchanged and save the combined result.

A normal later rerun reuses unchanged tracked objects and creates no duplicates. If an API request times out, the outcome may be uncertain: the state records that pending request and the script stops for review instead of repeating it. A confirmed partial run without an uncertain request can continue from the tracked IDs. Do not remove the state file to force a rerun.

No automatic rollback, resource deletion or request retry is performed. Returned API/port status is not a VM packet-flow or failover test.

## 5. Run — on R2d2 as joe

First inspect the plan, which makes no connections or files:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase4-create-networks.py
```

After reviewing sections 2 and 3, apply that exact plan:

```bash
/usr/bin/python3 scripts/phase4-create-networks.py --apply
```

The script selects the existing Kolla environment and private client profile. No new password prompts are expected. Keep the terminal open until the command returns.

## 6. Return the result

Use the exact `cat` command printed at the end and send the report.

Expected success marker:

```text
RESULT: LAB_NETWORKS_CREATED_AND_VERIFIED
```

This means the planned objects, their properties, active router ports and scheduled L3 agent passed the script's checks. It does not prove VM internet access, DNS operation or failover.

If it reports `LAB_NETWORKS_NEED_REVIEW`, return the report before rerunning, removing state or changing settings. Some objects may already exist; do not redeploy OpenStack to fix a network-creation checkpoint.

You can also view the result in Horizon: **Project → Network → Networks**, **Routers** and **Network Topology**. View the objects; keep changes in this lesson's script so the tracked plan stays consistent.

## 7. Finish check and short lesson

- [x] Chapter A live network inventory reviewed.
- [x] Chapter B plan, script and local tests prepared.
- [x] Joe's actual network-creation report reviewed.
- [ ] Move to image/flavor/key/security-group preparation by agreement.

Joe's report `phase4-create-networks-20261026T151115Z-iu5its4h.txt` ended `LAB_NETWORKS_CREATED_AND_VERIFIED`.

| Observed object | Live result |
|---|---|
| `lab-external` | Flat network on `physnet1`; MTU 1500 |
| `lab-private` | VXLAN segment 565; MTU 1450 |
| `lab-router` | Scheduled on **node1** |
| Router private port | **10.10.10.1**, ACTIVE |
| Router external port | **192.168.0.192**, ACTIVE |

The two networks, two subnets, router and attached private interface passed the API/property checks. No VM, floating IP or volume was created.

**Short lesson:** `192.168.0.200` is node1's management address. `192.168.0.151` is the shared API VIP. **`192.168.0.192` belongs to the virtual router's external port**, not to node1's USB host interface. `10.10.10.1` is the private network's router gateway. A future floating IP will be a separate VM NAT mapping.

For a future VM, the configured outbound path is private network → `lab-router` on node1 → `br-ex` → node1 USB `enx00e04c557e58` → home switch/router. If the VM runs on node2 or node3, its private traffic must also reach node1 through VXLAN over the management network. The VM placement and packet path have not yet been tested.

ACTIVE ports confirm Neutron's reported port state. They do not prove DHCP leases, working DNS, internet access or failover. Keep `private/phase4/networks.json`; no rerun is needed now.

## Local validation and scope

Local tests simulate the documented Neutron request/response shapes, exact creation order, tracked reruns, conflicts, address overlap, down agents, active-port checks, uncertain outcomes, private state and restricted write operations. They do not simulate OVS packet forwarding, DHCP leases, a real Neutron server or your home router. Joe's reviewed live report additionally confirms network creation and API/port state. Packet forwarding, guest DHCP/DNS, internet access and failover remain later tests.

[Neutron API reference](https://docs.openstack.org/api-ref/network/v2/) · [OVS self-service architecture](https://docs.openstack.org/neutron/latest/admin/deploy-ovs-selfservice.html) · [Inspected two-NIC diagram](../images/phase4-usb-network.svg)
