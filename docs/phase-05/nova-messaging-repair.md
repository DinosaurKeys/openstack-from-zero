# Repair Nova's migration messaging

[Chapter E — migration](E-vm-migration.md) · [Service dashboard](../reboot-and-recovery.md)

**Purpose:** stop Nova replies reaching the wrong worker. The reviewed 30 October diagnostic shows oslo.messaging **18.3.0**, the affected queue manager enabled, and one reply received by a different conductor worker before the requesting worker timed out.

**Repair used here:** turn off the faulty queue-name manager for **Nova scheduler and conductor on all three nodes**. This selects the library's supported random queue names. It is a configuration workaround; it does **not** install the upstream code fix in 18.3.1. Random names create more temporary queues; the script requires a positive existing queue expiry.

## 1. Get the repair — on R2d2 as joe

This Git update installs the new script. It is a one-time maintenance step.

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

## 2. Check, review and apply

Use this one command. It checks first and asks before changing anything:

```bash
/usr/bin/python3 scripts/lab-nova-messaging.py --apply
```

Enter joe's sudo password for each named node. The plan must show the queue manager **ON** in both services on all three nodes, healthy cluster prerequisites, and no active VM/volume/migration operations. An idle VM in `ERROR` can remain in `ERROR` during this service repair. Pause other Horizon/CLI changes until it finishes.

When the scope is correct, type:

```text
APPLY NOVA MESSAGING FIX
```

| Step | What happens |
|---|---|
| Save | Two persistent service overrides are written in the existing private Kolla configuration |
| Apply | Only `use_queue_manager` changes from `true` to `false` in each reviewed rendered configuration |
| Restart | The six existing scheduler/conductor systemd services restart **one at a time** |
| Verify | Each must have a new startup, the setting OFF, a healthy container and a fresh Nova heartbeat |
| Finish | Cluster/API checks pass; VM/volume API state and earlier tracking files remain unchanged |

Expect short lines such as `PASS node2/nova_conductor | queue manager OFF | healthy | fresh heartbeat`. The successful final line is:

```text
RESULT: NOVA_MESSAGING_WORKAROUND_VERIFIED
```

This verifies deployment of the workaround. **It does not prove a successful live migration.** No database, broker queue, compute/libvirt service, VM power state, volume, timeout or old migration record is changed.

## 3. If the repair stops

Keep the printed short report and return it for review. Some approved changes may already have completed. Do not remove its receipt or repeat `--apply`. To inspect the current settings without changing them:

```bash
/usr/bin/python3 scripts/lab-nova-messaging.py --check
```

## 4. Continue after the report is reviewed

First review the VM's current state/runtime and the old failed task. A VM still in `ERROR` needs its own reviewed recovery. Then use [Chapter E](E-vm-migration.md) for one separately approved live move and guest/data verification.

The repair keeps original migration receipts byte for byte. A later explicit `--reconcile` can recognize this completed repair's **two exact override changes**; unrelated configuration drift still blocks it. Reconciliation never retries or fixes the VM.

## Persistence and upstream fix

The script saves `[oslo_messaging_rabbit] use_queue_manager = false` in `private/phase2/kolla/config/nova/nova-scheduler.conf` and `nova-conductor.conf`. It preserves other settings and rejects conflicting host overrides. Kolla reads these files during future reconfiguration; the existing node configurations retain the setting across a reboot.

The permanent package solution is rebuilt service images containing oslo.messaging **18.3.1 or its upstream fix**. That image update is separate; this script installs no packages inside running containers.

Sources: [official 18.3.1 bug fix](https://docs.openstack.org/releasenotes/oslo.messaging/2026.2.html) · [supported queue-manager option](https://docs.openstack.org/oslo.messaging/latest/configuration/opts.html#oslo_messaging_rabbit.use_queue_manager) · [Kolla service overrides](https://docs.openstack.org/kolla-ansible/2026.2/admin/advanced-configuration.html#openstack-service-configuration-in-kolla).
