# Project instructions

Repository: DinosaurKeys/openstack-from-zero. R2d2 is Joe's management VM. The assistant prepares, tests and publishes code/documentation; Joe runs lab commands. This repository does not grant access to the lab or authorize a new disruptive operation. Follow the current conversation's scope and approvals.

## Writing and course structure

- Use short, precise, step-by-step lessons: purpose, where to run, command, expected result, next action.
- Maintain one final HA design. Remove obsolete conversion instructions and troubleshooting chronology from the course.
- Keep the main README as the current progress pointer. Label reviewed reports as dated observations; do not treat them as current health or a new installation's success.
- Keep one shutdown/reboot/recovery guide: `docs/reboot-and-recovery.md`. The obsolete `docs/reboot-recovery-reference.md` is removed.
- Git updates are a setup/maintenance task, not a routine reboot or recovery prerequisite. The normal checker selects its own tools/profile; no CLI activation is required for it.
- Keep useful reference commands, diagrams and existing section anchors where possible. Backups remain a deferred lesson; do not introduce a backup requirement.

## Confirmed design

| Item | Course configuration |
|---|---|
| Management host | Ubuntu 24.04, `r2d2`, account `joe`; project `/home/joe/openstack-from-zero` |
| Nodes | Ubuntu 24.04, account `joe`; node1 `.200`, node2 `.201`, node3 `.202` on `192.168.0.0/24` |
| Roles | All three nodes: controller, network, compute and independent local Cinder LVM |
| Management NIC | `enp0s31f6`; gateway/DNS `192.168.0.1`; management, APIs, service traffic and VXLAN underlay |
| USB NICs | node1 `enx00e04c557e58`; node2 `enx00e04c2c1678`; node3 `enx00e04c304688`; unnumbered, connected through `br-ex` |
| Physical LAN | Both NICs share the same ordinary home switch/router; no physical isolation or redundant switch |
| Addresses | API VIP `.151`; home DHCP `.2–.150`; external cloud pool `.152–.199`; nodes `.200–.202` excluded |
| Private VM subnet | `10.10.10.0/24`; gateway `10.10.10.1`; DHCP on; DNS `192.168.0.1` |
| Installer | Kolla-Ansible 23.0.0, commit `4b493ef36ace2b90257ecf94feb00e2c6e8014c9`; Ansible Core 2.21.5; OpenStack 2026.2 |
| Tool environments | `/home/joe/venvs/kolla`; CLI 10.3.1 in separate `/home/joe/venvs/openstack-client` |
| Private config | `private/phase2/kolla`; inventory `private/phase2/inventory.ini` |

Management SSH uses the existing `~/.ssh/openstack-from-zero` key. Guest SSH uses the separate `~/.ssh/openstack-from-zero-vm` key. Preserve host-key verification, credentials and per-node sudo prompts; passwordless sudo is not part of the current setup.

The SATA targets are exactly 240057409536 bytes: node1 serial `50026B7766070422`, node2 `4P3521354988`, node3 `50026B768317883A`. Each backs its own `cinder-volumes` VG. Ubuntu/NVMe and system LVM are protected. Retain the approved empty `cinder_cluster_name` and specific `cinder_cluster_skip_precheck: true` exception; other prechecks remain enabled.

## HA and evidence

- Explicit API Keepalived/HAProxy, three-member MariaDB/Galera and Neutron agent/router HA. API VIP Keepalived and per-router Keepalived are different components.
- Require Galera Synced/Primary, size 3, connected/ready, same valid cluster UUID; exactly one API VIP owner.
- Create the router explicitly with `ha=true`, non-distributed mode; require one active/two standby replicas with live/enabled L3 agents and two DHCP hosts.
- Glance file storage is on node1 only. Cinder pools and VM local root disks are not replicated. Ceph, storage HA and automatic VM recovery remain deferred.
- Preserve sampled-outage and restoration evidence. Healthy containers, API reads, reported HA roles, browser sign-in, guest traffic, data persistence and failover are separate claims.
- Source guards and tests must remain meaningful. Only the exact reviewed fresh-MariaDB discovery timeout may be accepted as an ignored deployment task; no blanket ignored-error bypass.

## Certificates and prerequisites

Chrony must be installed, enabled and synchronized on R2d2 and all three nodes before readiness validation. Document node preparation explicitly. LVM tools are needed before inspecting/creating the node storage pools.

A new CA requires explicit review of the SHA256 printed by Phase 2 E. Successful Phase 2 F with `--expected-ca` saves `private/phase2/reviewed-ca.json`; later tools use that approval. Never approve a discovered CA automatically, overwrite a different approval, rotate the existing CA or bypass TLS validation. Preserve compatibility with the existing reviewed CA when no saved approval file exists. Include the approval record in protected input hashes.

## Reboot and recovery

- Shutdown is service stop followed by the separately invoked poweroff script. The service-stop script leaves Ubuntu and R2d2 running. Keep receipt/identity/database-position guards and serial database shutdown.
- After power-on, `bash scripts/lab-recover.sh --check` performs read-only cluster checks and displays all 9 Nova, 12 Neutron and 6 Cinder registrations, including healthy rows. Reuse the existing API snapshot; do not claim to execute CLI commands internally.
- Distinguish process/healthcheck, heartbeat and administrative scheduling state. BLOCKED is unavailable evidence, not an invented DOWN state. Retain raw Cinder backend hosts for display and normalized-node membership checks.
- MariaDB complete-outage recovery is coordinated through Kolla after reviewing the outage evidence. Do not restart every database or choose a bootstrap member manually.
- Control recovery has an explicit allowlist and typed approval; healthy targets are skipped and uncertain outcomes stop. Manual selected service starts/restarts run on the affected node, not R2d2. No automatic compute enable or infrastructure restart.
- Saved snapshot display sends no new lab commands. Default cluster checks leave VM/migration state out of their health result; workload checks are explicit and read-only.

## VM, volume and migration safeguards

- Read current UUIDs, addresses, compute/storage locations and actual hypervisor runtime. Never infer a storage backend from an attachment's compute host.
- Preserve all pending checkpoints and restoration responsibility. Do not clear a marker, force-complete a move, reset ERROR, start/reboot a VM, format a disk or retry an uncertain mutation without the relevant reviewed action.
- Migration order: healthy services, inventory, read-only preview, approval of the exact move, one request, API/host verification, then guest/data checks. Compute/root-disk moves and Cinder backend moves are separate; storage migration adds no replication.
- Normal CLI is installed as part of the course. Activate its environment and source the private public-openrc once per terminal for independent `openstack` commands. Support both CLI and Horizon.
- The course data disk has no fstab entry. After a guest reboot/cold move, identify the original volume and filesystem UUID before an explicit mount of that existing filesystem. Never format it to restore a mount. Verify the saved checksum afterward.
- Keep exact operation allowlists, ownership/symlink guards, shared locks, pending-before-mutation records, no automatic retry and input/checkpoint preservation.

The explicit `phase5-reconcile-migration.py --apply --migration-id UUID` action can acknowledge only the original fixed-route attempt after its reviewed Nova status is `error` and the same VM is ACTIVE/idle on its original source. Validate authenticated identity, unchanged deployment inputs, bounded unambiguous history, two matching GET snapshots and the original local record. Preserve that record byte for byte in private migration-history before updating only its pending/stage/resolution metadata. Completed reconciliation is idempotent; it does not establish VM/guest health, migration success or resolution of the old timeout. The menu's fresh readiness/approval gates remain required. Never reconcile automatically from `--plan` or send a cloud mutation from this action.

## Working and publishing

Read relevant current scripts/docs, validate technical commands against pinned source or primary documentation, and run appropriate regression checks. Local fixtures/simulations are not live lab evidence. Do not send SSH, power, service or cloud mutations to Joe's lab from this environment.

Keep `private/`, `reports/`, `build/`, credentials, keys and private profiles out of GitHub. Publish only selected, reviewed changes through the GitHub connector; never push the unrelated staged/untracked working tree. Verify the expected main head before publication and read back the published result.
