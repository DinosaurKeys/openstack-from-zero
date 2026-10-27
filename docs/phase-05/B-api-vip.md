# B — API VIP takeover

[Phase 5](README.md) · [Previous: readiness](A-readiness.md) · [Next: MariaDB](C-mariadb.md)

**Purpose:** check whether another node takes over the cloud's front-door address, `192.168.0.151`.

**Status: script prepared and locally tested; live failover not yet tested.** No new VM or volume is created. The script uses the existing VM's API record for a read check.

## 1. Understand the exercise

| Step | What the script does |
|---|---|
| Prepare | Check all three nodes, MariaDB, HTTPS APIs and Horizon's login page; identify the current VIP owner |
| Stop | Stop only that owner's **API Keepalived systemd service** |
| Check takeover | Observe a different VIP owner while the original container is stopped; check authenticated APIs |
| Restore | Start its service and same original container through its management IP |
| Final check | Require healthy host/service observations, three synchronized database members, one VIP owner and working HTTPS APIs |

Kolla supervises this container through `kolla-keepalived-container.service`. The script verifies that unit manages only `keepalived` and has no extra service overrides, then uses `systemctl stop/start` on that unit. It does not disable or rewrite it.

The VIP need not return to its original node. API Keepalived and the virtual router's Keepalived are separate. This exercise does not stop HAProxy, MariaDB, the router, a VM or a whole node.

## 2. Update and read the plan — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase5-api-vip.py
```

Default mode prints the scope without connections or changes.

## 3. Prepare — no service stops

```bash
/usr/bin/python3 scripts/phase5-api-vip.py --prepare
```

Enter joe's sudo password for each node when requested; typing is hidden. An existing management SSH-key passphrase may also be requested. Passwords stay in process memory and are not saved in the report.

Expected result: `API_VIP_TEST_PREPARED`. The report names the actual target and prints a **direct SSH restoration command**. Keep that command in a second R2d2 terminal.

**Return the preparation report before continuing.** We review the target and restoration command and ask approval before the stop. The prepared plan expires after 30 minutes; if it expires, prepare again. A changed owner/container also requires a new plan.

## 4. Run the approved test

Only after reviewing the preparation report and approving the interruption:

```bash
/usr/bin/python3 scripts/phase5-api-vip.py --apply
```

Enter the node sudo passwords again. Keep the terminal open. Brief API/Horizon interruption is possible.

The script samples fresh Keystone authentication and the existing VM's Nova API record. It separately checks all course APIs before the stop, after takeover with the original container still stopped, and after restoration. It records failed samples and elapsed takeover observation time. A 90-second takeover observation window and bounded SSH/HTTP calls prevent indefinite probing; individual calls can extend the elapsed window.

Expected success:

```text
RESULT: API_VIP_FAILOVER_VERIFIED
```

Return the private report using the printed `cat` command. A prepared plan can be attempted once; repeated stops require a new reviewed preparation.

## 5. Restoration — put the stopped container back

The script attempts restoration in a `finally` block, including after a test error or Ctrl+C. It uses the node's management IP, not the VIP. It records restoration responsibility **before** issuing the stop.

If restoration is not confirmed, use the exact direct SSH command printed in the report. Alternatively, on R2d2:

```bash
/usr/bin/python3 scripts/phase5-api-vip.py --restore
```

This starts only the recorded API Keepalived service/container and asks for that node's sudo password. `API_KEEPALIVED_RESTORED` confirms the container is running; rerun Chapter A to establish full health afterward. No container is recreated and no configuration is rewritten. The pinned Kolla deployment uses a 60-second graceful container-stop timeout; the service command has a 90-second allowance, with a separate bounded SSH timeout.

Automatic restoration cannot run if R2d2 loses power or the script is forcibly killed. Keep the direct command available. Do not start the next exercise until restoration and health are confirmed.

## 6. Read the result

| Report field | Meaning |
|---|---|
| Original owner | Node owning `.151` before the stop |
| Takeover owner | Different node observed owning `.151` during the stop |
| Final owner | Owner after restoration; it may differ from the original |
| Failed probe count | Sampled authentication/VM API checks that failed |
| Restoration | Original container running and final checks passed |

Sampling can miss a short interruption. Zero failed samples does **not** prove zero downtime. Observed takeover time includes SSH and collection delays. This is not a packet capture, browser sign-in test, independent HAProxy-failure test or VM-traffic/failover test.

Local tests use simulated nodes/APIs to check target guards, stop/start scope, uncertain responses, interruptions and restoration. Native `ssh -G` checks command configuration without opening a connection. Only Joe's live report can prove takeover in this lab.

[Script](../../scripts/phase5-api-vip.py) · [Node helper](../../scripts/phase5-api-vip-node.py) · [Kolla API HA](https://docs.openstack.org/kolla-ansible/latest/reference/high-availability/haproxy-guide.html) · [Pinned Kolla service supervision](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/module_utils/kolla_systemd_worker.py)
