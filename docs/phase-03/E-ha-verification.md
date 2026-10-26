# Chapter E — Verify the HA features

[Phase 3](README.md) · [Architecture](../architecture.md) · [Viewing commands](../command-glossary.md)

Configuration, observed healthy replicas and a successful failure test are three different levels of evidence. This chapter keeps them separate.

## 1. Observe before injecting any failure

After Chapters C and D pass:

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/phase3-verify.py --run
```

Require the expected containers on each node, exactly one API VIP owner, and three Galera members reporting Synced/Primary/size3/connectedON/readyON with a shared UUID. The node collector also requires deployed Neutron HA settings. Preserve the report as the baseline.

After Phase 4 B creates the router:

```bash
/usr/bin/python3 scripts/lab-view.py router
/usr/bin/python3 scripts/lab-view.py agents
```

Require `ha: true`, three alive/enabled hosting L3 agents and one active/two standby HA states. DHCP placement is checked separately by the creation script. Missing/unknown HA roles are incomplete evidence, not a pass.

## 2. Controlled tests after a working VM exists

Prepare each exact fault and restoration procedure before running it. No failure-injection script or power-off command is included at this checkpoint. The fresh installation and a baseline guest must work first.

| Test | What must be observed |
|---|---|
| API VIP takeover | Loss of the current API VIP owner, a different node acquiring `.151`, and successful verified HTTPS authentication/API reads after recovery |
| One Galera member unavailable | Two surviving members remain Primary/ready with size2; application/API database operations continue; restored member returns Synced/size3 |
| HA virtual-router takeover | Previous active replica loses the relevant HA heartbeats, one standby becomes active, router IPs remain the same, and traffic from a guest on a surviving compute node recovers |

Capture times before/after and measure interruption. Restore the tested component and verify the full healthy baseline before another fault. Do not stop two database nodes together as the first exercise. Stopping only `neutron_l3_agent` is not sufficient proof: per-router Keepalived may continue running and forwarding traffic.

A whole-node power-off combines several failures: API/database/network services and any guests on that node. Test those mechanisms separately first. Use a guest on another compute node when measuring router failover; a VM powered off with its host cannot generate useful test traffic.

## 3. Resource checks

The reviewed initial snapshot showed ~15GiB usable RAM/four cores per node and 6.3/6.6/7.1GiB available, without swap-in/out during the sample. Recheck after router creation and guest boot. Do not infer failure capacity or workload performance from that short snapshot.

The view commands and this checklist do not inject a failure. A failover checkpoint is complete only after its actual result is reviewed. Local Cinder disks, Glance image storage, VM recovery and the home switch/router remain outside the three HA guarantees.
