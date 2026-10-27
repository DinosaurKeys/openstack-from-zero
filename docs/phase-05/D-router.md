# D — Virtual-router takeover

[Phase 5](README.md) · [Previous: MariaDB](C-mariadb.md) · [Next: VM migration](E-vm-migration.md)

**Purpose:** briefly interrupt the active router's virtual HA link, check a standby takes over the existing VM's traffic, then restore the original link.

**Status: complete — reviewed live report passed `ROUTER_HA_FAILOVER_VERIFIED`.** The active router moved from node3 to node2 after node3's virtual HA interface was lowered. The original interface was restored UP; all three HA links were UP with one active/two standby, and final guest traffic/data, host/API/database checks passed. Node2 remained active. The existing volume retained its filesystem UUID and file checksum. No additional restoration is required by this report.

Takeover was observed 11.67 seconds after the down request, including command and observation delays; this is not an exact outage duration. The single background network sample did not complete successfully within its request window. Separate recovery/final probes passed; uninterrupted connectivity is not claimed. Continue to [E — VM migration](E-vm-migration.md); the procedure below is a reference, not a request to repeat D.

## 1. Understand the target

| Item | Meaning |
|---|---|
| `qrouter-…` namespace | The router's separate networking area on a node |
| `ha-…` interface | A virtual interface watched by the router's Keepalived |
| `qr-…` interface | Private network connection; gateway `10.10.10.1` |
| `qg-…` interface | External connection; router IP and VM floating IP on the active replica |

The test changes only the selected **virtual HA interface inside this router's namespace**. It does not lower a physical management/USB NIC, stop the L3 container, kill a process, change API Keepalived, stop MariaDB, migrate the VM or operate on its disks.

## 2. Prepare — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase5-router.py --prepare
```

Enter node sudo passwords and unlock the existing guest key if asked; typing is hidden. The script checks:

1. Healthy hosts, three synchronized database members, API VIP, APIs and Horizon's HTTPS login page.
2. The existing VM, attached volume, SSH identity, network traffic and saved data checksum.
3. Three router replicas with one active/two standby; API roles agree with actual forwarding addresses.
4. Each HA interface's observed Neutron port MAC, name, namespace, container and tracked Keepalived configuration.

It saves a private plan and prints the exact management-SSH **restoration command**. No link is changed.

Expected result:

```text
RESULT: ROUTER_HA_TEST_PREPARED
```

**Return the report before applying.** Review the target and restoration command, then approve the interruption. The plan expires after 30 minutes; a failed preparation cannot be applied.

## 3. Run the approved test

Only after reviewing and approving the prepared report:

```bash
/usr/bin/python3 scripts/phase5-router.py --apply
```

Keep the terminal open and the printed restoration command in a second terminal. The script:

1. Rechecks the same router, current owner, guest traffic and data.
2. Records restoration responsibility before lowering only the selected virtual HA link.
3. Observes a different active replica holding the gateway/external addresses; probes floating-IP ping, guest gateway ping and outbound HTTPS.
4. Brings the original HA link UP in `finally`, including after an error or Ctrl+C.
5. Confirms three UP HA links, one active/two standby, guest traffic/data, host/API/Horizon health and the original database cluster UUID. Protected host configuration and database container identities must remain unchanged.

Expected success:

```text
RESULT: ROUTER_HA_FAILOVER_VERIFIED
```

Return the report before VM migration. Restoring the original link does not require moving the active role back to its original node.

## 4. Restoration reference

The printed direct command reaches the original node through its **management IP** and brings only the recorded virtual HA interface UP.

If script-managed restoration is not confirmed, on R2d2:

```bash
/usr/bin/python3 scripts/phase5-router.py --restore
```

`ROUTER_HA_LINK_RESTORED` confirms link UP only. Run Chapter A and inspect current router roles; return the reports before another fault. A forcibly killed script or loss of R2d2 cannot execute automatic restoration, so retain the direct command. Do not clear private state to repeat a fault.

## Limits

The 90-second role-observation window includes polling; individual bounded SSH/API requests can extend elapsed time. Network probes are samples, not continuous packet capture or exact downtime measurements. This test does not prove uninterrupted TCP sessions, process-crash recovery, whole-node recovery, storage HA or automatic VM recovery.

For read-only interface/configuration facts at any time:

```bash
/usr/bin/python3 scripts/phase5-router-inspect.py --run
```

`ROUTER_INSPECTION_COLLECTED` means collection succeeded, not that failover passed. No configuration or authentication text is printed; only selected facts and configuration digests are recorded.

[Script](../../scripts/phase5-router.py) · [Node helper](../../scripts/phase5-router-node.py)
