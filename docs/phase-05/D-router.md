# D — Virtual-router takeover

[Phase 5](README.md) · [Previous: MariaDB](C-mariadb.md) · [Next: VM migration](E-vm-migration.md)

**Simple idea:** the private network has one active router and two standby copies. We will test whether a standby takes over forwarding the existing VM's traffic.

## 1. Inspect first — no changes

On **R2d2**, run:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase5-router-inspect.py --run
```

Enter each node's sudo password in the terminal. The script reads the router ID from your existing private VM record. It checks all three nodes and records:

| Item | Plain meaning |
|---|---|
| Router namespace | The router's separate networking area on that node |
| `ha-` interface | The virtual interface used by the router's HA mechanism |
| `qr-` / `qg-` addresses | Private and external router interface addresses |
| Tracked interface | The interface Keepalived watches for failure |
| Configuration digest | A fingerprint of the configuration; no password text is printed |

At the end, use the printed `cat` command and return the report. `ROUTER_INSPECTION_COLLECTED` means the facts were collected; it does **not** mean failover passed. An incomplete report is also useful: return it before trying another action.

This inspection does not stop a service, change an interface, create a VM, or operate on the volume. Normal SSH/sudo audit logs may be written.

## 2. Review the proposed test

After reviewing the actual interfaces and settings, prepare a separate test that briefly lowers **only the active router's virtual HA interface**, checks whether a standby takes over, and restores the original interface UP through management SSH. Review the prepared report and restoration command before applying the fault.

The test must verify one active/two standby replicas, guest traffic and the existing volume checksum before and after. It must record network probes while the original HA interface is down.

The fault script is not released by this inspection step. Do not run an improvised interface-down, process-kill or L3-container restart command.

**Pass for the later test:** the active role moves and tested guest traffic recovers, followed by a healthy restored setup. A virtual HA-link test does not prove whole-node recovery, uninterrupted connections or storage HA. Return the report before E.
