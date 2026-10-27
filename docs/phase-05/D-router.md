# D — Virtual-router takeover

[Phase 5](README.md) · [Previous: MariaDB](C-mariadb.md) · [Next: VM migration](E-vm-migration.md)

**Simple idea:** the private network has one active router and two standby copies. We test whether a standby takes over forwarding the VM's traffic.

**Status: test plan.** The exact per-router Keepalived process and a supported restoration method must be identified and reviewed before interruption. Do not improvise a process-kill command.

| Step | What we do | What we check |
|---|---|---|
| 1 | Read current router roles and VM placement | One active, two standby; working guest network |
| 2 | Identify the active router's Keepalived and restoration method; ask approval | Correct router/process; management access available |
| 3 | Interrupt only that router's active HA process | A standby becomes the active router |
| 4 | Probe the VM's network during takeover | Same gateway/floating IP; measure interruption and recovery |
| 5 | Restore the original router replica | Three healthy replicas, one active/two standby; guest traffic works |

Stopping only the Neutron L3 agent may leave its router Keepalived running. That alone is not a reliable takeover test. API Keepalived and router Keepalived are separate components.

**Pass:** the active role moves and tested guest traffic recovers, followed by a healthy restored setup. This does not test a whole-node failure or guarantee uninterrupted connections. Return the report before E.
