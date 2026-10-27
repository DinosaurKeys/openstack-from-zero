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

## 2. Continue with Phase 5

The controlled exercises are in [Phase 5 — HA validation and VM migration](../phase-05/README.md), after the VM and Cinder volume work. Follow readiness → API VIP → MariaDB → virtual router → planned VM migration.

No fault is injected by this Phase 3 checklist. Healthy replicas are the baseline; takeover needs a separate measured test. Local storage replication and automatic VM recovery remain outside this lab's current HA guarantees.
