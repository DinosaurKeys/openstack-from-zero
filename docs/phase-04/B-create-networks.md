# Chapter B — Create the HA virtual networks

[Phase 4](README.md) · [Previous: inspection](A-network-inspection.md) · [Architecture](../architecture.md)

**Goal:** create the approved networks and one HA virtual router, then verify its reported replica roles. The fresh deployment must supply a successful report; no previous cloud's IDs or active node are reused.

## 1. Addressing and objects

| Object | Plan |
|---|---|
| `lab-external` | External flat network on `physnet1` → `br-ex` → USB |
| External subnet | `192.168.0.0/24`; gateway `.1`; allocation pool `.152–.199`; Neutron DHCP off |
| `lab-private` | VXLAN network; segment/MTU assigned by Neutron and reported |
| Private subnet | `10.10.10.0/24`; gateway `10.10.10.1`; pool `.2–.254`; DHCP on; DNS `192.168.0.1` |
| `lab-router` | **HA=true**, non-distributed, IPv4 source NAT enabled; private subnet attached |
| Router placement | All three L3 agents: exactly one active and two standby |
| DHCP placement | Two alive/enabled DHCP agents hosting the private network |

The existing home DHCP range is `.2–.150`; API VIP `.151` and host addresses `.200–.202` are outside the cloud allocation pool. The external pool must remain reserved for OpenStack. The script cannot prove every home-LAN address is unused or discover hidden Windows/VPN routes.

One external address is allocated to the router's gateway port. The report gives the actual address; it is not placed on a USB host interface. The future VM address is also not predetermined; DHCP service ports can consume private addresses.

## 2. What router HA means

The logical router has replicas on node1, node2 and node3. Neutron's per-router Keepalived/VRRP elects one active replica. Standbys can take over the router addresses after the relevant HA heartbeats are lost. The active node is selected dynamically; API VIP ownership is a separate election.

The script explicitly creates `ha=true`; it does not rely only on a server default. It checks HA roles through `/routers/<ID>/l3-agents`. Three agent records without one active/two standby states do not pass. The hidden HA network/ports created by Neutron are expected additional service objects, not extra user-requested tenant networks.

## 3. Which traffic uses each NIC?

| Traffic | Path |
|---|---|
| SSH, API/Horizon, database/messaging, host/image transfers | Built-in management NIC `enp0s31f6` |
| Private VM traffic across nodes, including VM to router on another node | VXLAN over management addresses `.200/.201/.202` |
| Neutron router HA overlay | Hidden HA network over the configured VXLAN underlay |
| Same-node/same-network VM traffic | Can remain in local virtual switching |
| VM to home LAN/internet | Private network → active router replica → `br-ex` → that node's USB → switch/home router |
| LAN client to a future floating IP | Active router node's USB → external network/router NAT → private VM network |

Both physical NICs share the same ordinary home switch/LAN. These roles do not provide independent physical networks. Guest packet flow remains a later test.

## 4. Changes and guards

Run on R2d2 as joe using the existing private HTTPS profile/CA. No node SSH or sudo prompt is needed.

The script creates two user networks, two subnets and one HA router, attaches the private subnet, and lets Neutron agents realize DHCP, HA replicas, namespaces, ports and NAT. It changes no Netplan/Kolla configuration and creates no VM, floating IP or volume.

Before writes, it checks extensions, all 12 expected agents, conflicting names, overlapping existing subnets, another flat network on physnet1 and visible non-default R2d2 route overlap. It records exact IDs/plan/project/API in `private/phase4/networks.json`. Existing untracked or changed resources are not adopted, deleted or rewritten.

It reads back the objects, waits for ACTIVE router ports and one-active/two-standby placement on the three known nodes, and verifies two DHCP agents. Shared HA router interface owners differ from classic router interfaces; the collector accepts the correct HA owner type.

Confirmed tracked partial runs can resume. An uncertain request persists a pending marker and stops for review instead of repeating creation. Keep the state file. No automatic deletion, rollback, repair or request retry is performed.

## 5. Run

Inspect the plan first:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase4-create-networks.py
```

Then apply:

```bash
/usr/bin/python3 scripts/phase4-create-networks.py --apply
```

Default mode makes no connections or changes. Apply obtains a short-lived token in memory and saves one private report. No password/token values are printed.

## 6. Return and understand the result

Use the printed `cat` command. Expected marker:

```text
RESULT: LAB_NETWORKS_CREATED_AND_VERIFIED
```

The report includes the new resource IDs, actual external router IP, private gateway, network segment/MTUs, router ports, all three router hosting agents/HA roles, and two DHCP hosts. Check the **active** role to identify the current external traffic node. The standby nodes are not separate independent routers.

If it reports `LAB_NETWORKS_NEED_REVIEW`, return the report before rerunning or changing state. Some resources may exist. Do not redeploy OpenStack to fix an unreviewed network checkpoint.

View the current router:

```bash
/usr/bin/python3 scripts/lab-view.py router
```

Horizon: **Project → Network → Networks**, **Routers**, **Network Topology**.

**Lesson:** API VIP `.151`, a router's allocated external IP, private gateway `10.10.10.1` and a future floating IP belong to different objects. An HA router has one logical identity and multiple replicas. ACTIVE ports and reported HA roles are not proof of guest DNS/internet access or successful takeover. [Controlled tests](../phase-03/E-ha-verification.md) follow a working guest.

[Neutron API](https://docs.openstack.org/api-ref/network/v2/) · [OVS router HA](https://docs.openstack.org/neutron/latest/admin/deploy-ovs-ha-vrrp.html)
