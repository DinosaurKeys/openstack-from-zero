# E — Move the VM for planned maintenance

[Phase 5](README.md) · [Previous: virtual router](D-router.md)

**Goal:** move the existing running `lab-vm-01` between healthy compute hosts, similar to VMware vMotion. No new VM is needed.

**Status: read-only inspection script ready and locally tested; migration pending.** First inspect the actual hosts and migration settings. Choose the destination and review the exact migration plan afterward.

| Component | Intended result of the later migration |
|---|---|
| VM memory and execution | Move to the chosen compute host |
| Local root disk | Copy to the destination as part of block live migration |
| Existing Cinder data volume | Stay on its current storage backend; connect from the destination compute host |
| Private and floating IPs | Stay associated with the same VM port |

Both compute hosts must remain working. This exercise does not test recovery after a host dies, automatic VM restart or storage HA.

## 1. Collect the facts — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase5-migration.py --inspect
```

Unlock the existing guest SSH key if asked, then enter each node's sudo password in the terminal. Typing is hidden; passwords are not written to the report.

The script checks the existing VM, SSH identity, network traffic, attached volume and its data checksum. It reads all three nodes and both possible destinations; it does not assume the VM still runs on its earlier host.

| Check | Plain meaning |
|---|---|
| Available RAM and instance-disk space | Is there room for this small guest? |
| Source guest CPU comparison | Can the destination hypervisor provide the VM's current CPU definition? |
| Nova services and Placement inventory/usage | What capacity and service state does OpenStack report? |
| Nova configuration and libvirt version | Which migration settings and virtualization software are deployed? |
| Authenticated libvirt peer read | Can the source query the destination's libvirt service using the existing configuration? |

Only selected configuration fields, CPU details and digests are printed. Full Nova configuration, domain authentication details and libvirt credential files are not printed.

Expected collection result:

```text
RESULT: VM_MIGRATION_INSPECTION_COLLECTED
```

The terminal and `.txt` report show a short table: available RAM/disk per node, CPU comparison, libvirt connection and Nova/Placement checks. `NOT CHECKED` means an earlier required observation is missing; it does not mean incompatibility. `NOT CONFIRMED` means the query did not confirm the requirement. A failed command is identified with its stage, command and a classified reason; raw error text is withheld. Other host facts already collected are retained.

Full technical observations are saved in a separate private `.json` file. Share it only when requested.

At the end, use the printed **Read short summary** `cat` command and return the short report. If it says `NEEDS_REVIEW`, return that summary too. Collection is not approval to migrate.

This script has **no `--apply` mode**. It does not migrate, create a VM, attach/detach a volume, format a disk, stop services or change configuration. Normal SSH/sudo audit logs and the private report may be written.

## 2. Review and choose

After reviewing the report:

1. Choose a destination with sufficient physical and OpenStack capacity and a compatible CPU.
2. Review the actual migration transport, disk/device support and remaining prerequisites.
3. Prepare the exact one-VM migration request, completion checks and handling for an uncertain result.
4. Ask for approval before issuing the migration.
5. Migrate once, then verify the new compute host, unchanged VM/IP identity, working guest network and original volume checksum.

The summary uses 1 GiB of RAM and 2 GiB of disk as small lab review margins beyond the guest requirement. These are not reservations or a Nova scheduling guarantee. An authenticated libvirt read does not prove that the later QEMU memory/disk stream ports work. Its TCP/TLS label describes the libvirt control connection, not proof of encryption for every migration stream.

The helper reads the running VM's ordinary XML on a read-only libvirt connection. Libvirt 10 requires a normal connection for the hypervisor CPU-comparison query; the helper permits only explicit inspection commands and exposes no VM mutation command. The comparison is independent of Nova's configured CPU-comparison workaround; a bypass setting does not establish compatibility. An absent configuration value is reported as absent, not silently presented as an observed runtime default.

## 3. Completion and recovery

A successful later test must show the same VM on the chosen destination with guest access, networking and the existing mounted data intact. A local-root block migration copies the root disk; it does not request migration or replication of the Cinder backend.

If the migration request has an uncertain outcome, inspect Nova's real VM and migration state before another action. Do not repeat the request automatically, force-complete, delete source files, power off a host or promise an automatic rollback. Moving back is a separate decision.

[Inspection script](../../scripts/phase5-migration.py) · [Node helper](../../scripts/phase5-migration-node.py) · [Nova live-migration guide](https://docs.openstack.org/nova/latest/admin/configuring-migrations.html) · [Kolla libvirt guide](https://docs.openstack.org/kolla-ansible/latest/reference/compute/libvirt-guide.html) · [Libvirt CPU comparison](https://libvirt.org/manpages/virsh.html#hypervisor-cpu-compare)
