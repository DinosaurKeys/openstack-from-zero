# A — Check readiness

[Phase 5](README.md) · [Next: API VIP](B-api-vip.md)

**Purpose:** record a healthy starting point before deliberately interrupting anything. These steps do not stop services or move the VM.

## 1. Update — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

## 2. Check services, VM and data

Run one command at a time on R2d2:

```bash
/usr/bin/python3 scripts/phase3-verify.py --run
/usr/bin/python3 scripts/phase4-boot-vm.py --verify
/usr/bin/python3 scripts/phase4-volume.py --verify
```

Enter the requested node sudo passwords or guest-key passphrase locally. Each command prints a private report path. Keep those reports; do not post passwords.

| Report | Required starting state |
|---|---|
| Services | Healthy required containers, exactly one API VIP owner, three synchronized MariaDB members |
| VM | SSH, DHCP, metadata, DNS, outbound HTTPS and floating-IP ping pass |
| Volume | Existing volume mounted; test-file checksum unchanged |

## 3. View current placement

On R2d2:

```bash
/usr/bin/python3 scripts/lab-view.py router
/usr/bin/python3 scripts/lab-view.py agents
/usr/bin/python3 scripts/lab-view.py servers
/usr/bin/python3 scripts/lab-view.py volumes
```

Record the current API VIP owner, active router, compute host and volume host from the reports. Owners can change; never choose a test target using an old snapshot.

## 4. Check memory and management access

From R2d2, read each node's current memory:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@node1 free -h
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@node2 free -h
ssh -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@node3 free -h
```

Read **available** RAM, not only **free** RAM. Successful SSH also checks the direct management path needed for restoration. These readings alone do not prove migration capacity; destination CPU, disk and Nova capacity checks belong to E.

## 5. Review before continuing

Return the three verification reports, current placement output and memory readings. Stop here if any check needs review. Next, prepare B's exact target, observation probes and direct restoration command; obtain approval before stopping anything.
