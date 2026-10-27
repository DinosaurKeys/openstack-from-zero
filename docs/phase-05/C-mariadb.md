# C — One database member stops

[Phase 5](README.md) · [Previous: API VIP](B-api-vip.md) · [Next: virtual router](D-router.md)

**Purpose:** stop one MariaDB member, check that the other two still serve the tested requests, then bring the member back.

**Status: script prepared and locally tested; live database test pending.** Chapter B's API VIP test passed. This is a separate exercise.

## 1. Understand the test

| Stage | Required result |
|---|---|
| Before | Three healthy database members: Synced, Primary, ready, size 3 and the same cluster UUID |
| During | Selected member stopped; two survivors Synced, Primary, ready, size 2 and the original UUID; authenticated API reads work |
| Restore | Start the original service and wait for it to synchronize |
| After | All three members healthy again, size 3 and original UUID; host/API/Horizon checks pass |

A cluster UUID identifies the same database cluster. **Synced** means the member has caught up; **Primary** means it belongs to the operational database group.

We use **node2** as the explicit member for this exercise. Preparation checks current health; it does not assume node2 is safe to stop. Only `kolla-mariadb-container.service` is targeted. The node, API Keepalived, router, VM and volume stay outside the deliberate interruption.

## 2. Update and read the plan — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase5-mariadb.py
```

Default mode prints the scope and changes nothing.

## 3. Prepare node2 — stops nothing

```bash
/usr/bin/python3 scripts/phase5-mariadb.py --prepare --node node2
```

Enter joe's sudo passwords when requested; typing is hidden. The existing management SSH-key passphrase may also be requested. Passwords are not stored in reports or command arguments.

Expected result:

```text
RESULT: MARIADB_TEST_PREPARED
```

The report names the selected database member and prints its **direct restoration command**. Keep that command in a second R2d2 terminal.

**Return this report before applying.** Review the target/restoration command and approve the interruption first. The plan expires after 30 minutes. A failed preparation cannot be applied.

## 4. Run the approved test

Only after the preparation report is reviewed and the stop is approved:

```bash
/usr/bin/python3 scripts/phase5-mariadb.py --apply
```

Keep the terminal open. The script:

1. Rechecks the same three database containers and current healthy baseline.
2. Records restoration responsibility, then stops only the selected member through its verified systemd service.
3. Reads database status directly from the two survivors and checks authenticated OpenStack API reads while the selected member remains stopped.
4. Starts the same original service in `finally`, including after a test error or Ctrl+C.
5. Waits for all three members to synchronize, then checks host health, APIs and Horizon's HTTPS login page.

It samples fresh Keystone authentication and the existing VM's Nova API record. These are API reads, not a new VM boot or an application database-write test. Failed samples are reported; sampling cannot prove zero downtime.

Expected success:

```text
RESULT: MARIADB_HA_VERIFIED
```

Return the private report using its printed `cat` command. A prepared plan can be attempted once. No second member is stopped by this script.

## 5. Restoration reference

On the selected **node2**, the normal restoration command is:

```bash
sudo systemctl start kolla-mariadb-container.service
```

Use the exact SSH command printed by preparation to reach that original node through its management IP. The command starts its existing service/container; it does not bootstrap a new cluster.

If script-managed restoration was not confirmed, on R2d2:

```bash
/usr/bin/python3 scripts/phase5-mariadb.py --restore
```

This starts only the recorded member, then reads all three nodes to confirm synchronization with the original cluster UUID. `MARIADB_CLUSTER_RESTORED` confirms that check; rerun Chapter A and return the results before another exercise.

Keep the direct command available: a forcibly killed script or R2d2 power loss cannot execute automatic restoration. Do not stop another database member while one is unavailable. Do not clear private state, reset MariaDB, delete data or run a cluster bootstrap/recovery command for this exercise.

## 6. Timing and limits

Kolla's pinned service configuration uses a 60-second stop grace. The script allows 90 seconds for the service command, a 90-second two-member observation window and a 180-second rejoin observation window. Individual bounded SSH/database requests can extend those elapsed windows. If synchronization is not confirmed, return the report; no automatic rebuild, bootstrap or repeated stop is performed.

The full Chapter A check expects size 3. During this exercise, Chapter C's checks deliberately expect size 2; full healthy checks run again after restoration.

This tests a controlled one-member service stop and API/database reads. It does not test power loss, a network partition, multi-node failure, application write replication, storage HA or VM recovery. Local API/node fixtures and the pinned Kolla unit template are tested; only the live report proves the result on this lab.

[Script](../../scripts/phase5-mariadb.py) · [Node helper](../../scripts/phase5-mariadb-node.py) · [MariaDB quorum](https://mariadb.com/docs/galera-cluster/high-availability/understanding-quorum-monitoring-and-recovery)
