# Chapter C — Deploy OpenStack

[Phase 3](README.md) · [Previous: full prechecks](B-full-prechecks.md)

**Status: complete — live deployment observations passed on all three nodes.** The original deployment procedure remains below for study and rebuilding. Continue with [Chapter D — client access](D-client-access.md).

## 1. Purpose

Install and start the OpenStack services using the configuration we already prepared.

**Bootstrap prepared the machines. Prechecks checked the requirements. Deployment builds the cloud.**

## 2. What will change

The target is **node1, node2 and node3**. All three keep their controller, network, compute and storage roles.

| Change | Simple explanation |
|---|---|
| Download container images | Each node downloads the required Ubuntu-based OpenStack 2026.2 images from the approved `quay.io/openstack.kolla` source. Internet access and free disk space are needed. |
| Write service configuration | Kolla creates service folders under `/etc/kolla/` on the nodes and copies the necessary configuration, credentials and certificates there. Treat these node files as private too. |
| Create service data | Docker volumes store databases, message queues, logs and other service data. These consume node disk space; container images use the OS disk, not the Cinder SATA pool. |
| Start supporting services | MariaDB/Galera stores service records; RabbitMQ carries messages; ProxySQL/HAProxy route connections; Keepalived manages the API address. Supporting containers include logging, caching and Kolla's toolbox. |
| Start OpenStack services | Keystone, Glance, Placement, Nova, Neutron, Cinder and Horizon start as containers. Kolla creates their service databases, accounts and registrations. |
| Activate `192.168.0.151` | Keepalived places the shared API VIP on one node's management interface. It can move between nodes. This is an additional address; `.200`, `.201` and `.202` stay. |
| Connect USB NICs to Open vSwitch | Kolla creates `br-ex` and attaches each node's selected USB NIC. Other virtual switches support VM networking. The built-in management NIC stays separate from `br-ex`. |
| Prepare the host kernel | Kolla loads/persists required modules, including `ip_vs`, `openvswitch` and `br_netfilter`, and applies networking settings through `/etc/sysctl.conf`. Nova installs `/etc/udev/rules.d/99-kolla-kvm.rules` and masks Ubuntu's `qemu-kvm.service` while containerized libvirt manages virtualization. |
| Start Cinder LVM services | Cinder uses the existing independent `cinder-volumes` pool on each node. Backend initialization can create internal LVM structures, such as a thin pool; that is not a user VM volume. |

The existing inventory, globals, passwords and local certificates are reused. Netplan, SSH and Chrony configuration are checked for preservation. No certificate rotation, disk wipe, PV/VG recreation or change to your sudo policy is part of this wrapper. Standard bootstrap is not run again.

**Agreed settings remain:** HTTPS on the API VIP; independent Cinder pools with an empty cluster name and the approved cluster-check exception; Heat, Cinder backup, Prometheus and Grafana disabled. VM external addresses `.152–.199` remain a future allocation plan. This chapter creates no tenant network, VM, application or Terraform resources.

## 3. What the script does

[phase3-deploy.py](../../scripts/phase3-deploy.py) is a wrapper around Kolla's existing automation. You do not need to write another script or memorize Kolla's internal tasks.

1. Check the pinned tool/source files and existing private configuration on R2d2.
2. Unlock the existing SSH key if needed and ask for each node's sudo password once.
3. Check all three prepared nodes, then run **fresh full prechecks**. A failed precheck stops before deployment.
4. Call Kolla's standard **`deploy`** command for all three nodes, using your existing private inventory and configuration directory.
5. Check selected core containers, their configured healthchecks, USB-to-`br-ex` membership, management access settings and one observed VIP owner. Save one combined report.

The post-deployment checks differ from the earlier preparation checks: the VIP should now exist and Cinder may have initialized its backend. We do not repeat the old requirement that the VIP be unused or the VG contain zero LVs.

The real command uses:

| Input | Location on R2d2 |
|---|---|
| Kolla environment | `/home/joe/venvs/kolla` |
| Inventory | `/home/joe/openstack-from-zero/private/phase2/inventory.ini` |
| Configuration directory | `/home/joe/openstack-from-zero/private/phase2/kolla` |
| Globals | The `globals.yml` inside that configuration directory |
| Node USB selections | `private/phase2/host_vars/node1.yml`, `node2.yml`, `node3.yml` |

The wrapper supplies `--use-test-images` to **prechecks only**, where Kolla 23 accepts that option. Deployment uses the image source already selected by the configuration and release defaults. No extra check bypass is added.

## 4. Download and run — on R2d2 as joe

Read section 2 first. This command performs real deployment changes on the three nodes.

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-deploy.py --apply
```

The script selects `/home/joe/venvs/kolla` automatically. Your prompt may already show `(kolla)`; no manual activation is required.

**Prompts:**

- If asked for the SSH-key passphrase, use the passphrase you chose for `~/.ssh/openstack-from-zero`.
- At each `Joe sudo password on node1:` / `node2:` / `node3:` prompt, enter joe's sudo password for that named node. Press **Enter**; typing is invisible.
- No R2d2 sudo password is requested. Node passwords are held in memory and passed to child processes through environment variables, not saved in command arguments, options files or reports.

Keep the R2d2 terminal open until the script returns to the shell prompt. Image downloads, database initialization and service startup can take time; there is no fixed duration promise. Container observations may wait up to two minutes per node for configured healthchecks.

Without `--apply`, the script prints its plan only and makes no connections or file changes.

## 5. Read and return the result

The report path is printed at the start and at the end. When the shell prompt returns, use the exact `cat` command printed by the script and return that report.

Expected success marker:

```text
RESULT: OPENSTACK_DEPLOYED for all
```

Success requires Kolla's final deployment recap for all three nodes with zero failed, unreachable, rescued or ignored tasks; observed core containers running with healthy configured checks; USB bridge membership; one observed VIP owner; and preserved private inputs and protected configuration.

`changed` is expected during deployment: this time we are installing services. `skipped` means a task's conditions excluded it, including tasks for disabled services.

If it prints `OPENSTACK_DEPLOYMENT_NEEDS_REVIEW`, stop and return the report. Some tasks may already have changed the nodes. The wrapper does not retry, erase containers, roll back or change settings automatically.

Keep the report private. It includes detailed Kolla task output and image references. Do not upload the report, credentials or private keys to GitHub.

## 6. Finish check and short lecture

- [x] Chapter B's live prechecks reviewed: all three nodes passed.
- [x] Chapter C guide and wrapper prepared and tested locally.
- [x] Joe's actual deployment and verification results reviewed.
- [x] Continue to Chapter D by agreement.

**After the report:** review which services started, where their data lives, how the VIP reaches the services and how the USB NIC joins the virtual network. Keep the run procedure above for rebuilding.

Deployment success is not yet proof of a working VM or volume. **Chapter D** will prepare client access and verify service/API registration, live HTTPS and Horizon access. The first VM, external network and volume tests follow in Phase 4.

### Reviewed live result and short lesson

Joe's `phase3-verify-20261026T130558Z-ypl859mq.txt` reports `OPENSTACK_OBSERVATIONS_PASSED for all`, `passed: true` and no issues. Required containers passed their running/configured health checks; MariaDB is Synced and Primary on all three nodes, with three members and one shared cluster identity. The API VIP was observed on node2 only. Management addresses/routes, USB links and USB-to-`br-ex` membership passed. This is a snapshot, not a failover or guest-traffic test.

Glance uses the approved local file backend and Kolla's first-node placement: `glance_api` belongs on node1. The other two nodes do not need their own copy of that container. The three Cinder pools remain independent.

For a fresh observation of the existing installation, run on R2d2:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-verify.py --run
```

Enter each named node's sudo password, then return the printed short summary. This reads the installed state; it does not deploy services again.

**Lesson:** Kolla runs services in containers, MariaDB shares the service database across the three database members, Keepalived owns one API VIP at a time, and Open vSwitch attaches the USB NICs to `br-ex`. A host IP is not required on those USB NICs. Actual VM networking is configured and tested in Phase 4.

## Development verification

**51 relevant tests passed**, including twelve deployment-wrapper tests. The deployment tests cover default-plan isolation, exact command scope, per-node sudo selection/privacy, ordering, failure gates, recap/service evidence, container health and VIP-owner validation. The actual Kolla-Ansible 23.0.0 CLI parses the deployment command/task list and observation playbooks in an isolated environment. Tests verify the selected container names and enabled definitions against the installed release and the all-three-node inventory, and preserve private fixture inputs.

A manifest guards 1,572 release/collection source files. Representative CLI, site, database, load-balancer, OVS, Neutron, Cinder and Nova host tasks match the pinned release commit. These checks do not reproduce your three Ubuntu nodes: image pulls, remote task execution, live container startup, failover and guest operation remain live checks. No deployment has been run by the assistant.

[Official installation order](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart/) · [Pinned deployment CLI](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/kolla_ansible/cli/commands.py) · [USB bridge tasks](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/openvswitch/tasks/post-config.yml) · [Nova host tasks](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/nova-cell/tasks/config-host.yml)
