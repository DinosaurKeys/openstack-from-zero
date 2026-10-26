# Fresh OpenStack HA installation

[Home](../README.md) · [Architecture](architecture.md) · [Phase 3](phase-03/README.md)

This is the installation route for the three **already prepared** Ubuntu hosts. It starts with an empty OpenStack deployment and installs API Keepalived/HAProxy, MariaDB/Galera and HA-capable Neutron agents. Phase 4 creates the actual HA virtual router.

## 1. Understand the destructive scope

The reset permanently removes all Kolla containers, their service data/Docker volumes, cloud network namespaces and the existing OpenStack database records on node1/node2/node3. Images in Glance and any guest data in Kolla volumes are removed. It also removes all logical volumes inside the three reviewed `cinder-volumes` VGs, including thin pools and Cinder volume data. This is not a data backup or an OS reinstall.

It preserves Ubuntu on NVMe, management IPs/routes, SSH, Netplan, Chrony, Docker packages/cached images, and the reviewed SATA PV/VG containers. It retains R2d2's tools, inventory, existing service credentials, certificates and CA trust. The guest SSH key and verified image download cache can be reused. No raw-disk wipe, PV/VG recreation or browser CA reimport is part of this procedure.

The script checks all three nodes before the first destruction: host/IP identity, exact SATA by-id/serial/size, one-PV Cinder ownership, no system-disk ancestry, mounted/configured Cinder storage, active VM processes, unrelated containers and namespaces. Unknown use stops the operation. Running guests must be reviewed separately; this command does not force them off.

## 2. Inspect the plan — R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-reset.py
```

This makes no connections or changes. Read the scope above before applying.

## 3. Empty the OpenStack deployment

```bash
/usr/bin/python3 scripts/phase3-reset.py --apply
```

Enter each named node's existing sudo password in the terminal. Typing is hidden. No R2d2 sudo password is expected.

The script backs up the local private configuration, runs the pinned standard Kolla `destroy` action for each node, verifies its result, and removes only the verified Cinder VG's logical volumes. It checks that OS storage and protected host configuration remain unchanged. It archives obsolete cloud-resource IDs/client files locally and prepares the final HA globals. Service passwords and certificates remain byte-identical.

Expected marker:

```text
RESULT: FRESH_HA_INSTALL_READY
```

**Return the printed report before continuing.** A partial/uncertain reset stops for review; the private receipt prevents accidentally repeating destruction. Do not delete `private/ha-reset.json` to force another run. No automatic deployment, rollback or retry occurs. Archived IDs refer to the removed cloud; they must not be reused in the fresh cloud.

## 4. Full prechecks

After the reset result has been reviewed:

```bash
/usr/bin/python3 scripts/phase3-prechecks.py --run
```

Expected: `KOLLA_PRECHECKS_PASSED for all`. The existing Docker/bootstrap preparation is reused. Do not rerun disk provisioning, create another SSH key or reinstall Ubuntu. Full host preparation remains documented in [Phase 3 A](phase-03/A-prepare-nodes.md) for genuinely new hosts.

## 5. Deploy the HA services

After successful prechecks:

```bash
/usr/bin/python3 scripts/phase3-deploy.py --apply
```

Expected: `OPENSTACK_DEPLOYED for all`. [Phase 3 C](phase-03/C-deploy-openstack.md) explains the checks, including API VIP ownership, Galera status and rendered Neutron HA settings. No virtual router exists simply because the agents are deployed.

## 6. Access and virtual networks

Follow each chapter and review its report before the next:

```bash
/usr/bin/python3 scripts/phase3-access.py --run
/usr/bin/python3 scripts/phase4-network-check.py --run
/usr/bin/python3 scripts/phase4-create-networks.py --apply
```

The first command regenerates private client profiles from the retained credentials. Network creation requires an HA router on all three network nodes with **one active and two standby** replicas, plus two DHCP agents. The report supplies the new IDs, actual external address and elected active node.

Then follow [Phase 4 C](phase-04/C-prepare-vm.md) for the image/flavor/key/security group. Boot, DHCP/DNS, USB packet flow, volume and controlled failover tests remain separate checkpoints. Keep report contents and private files out of GitHub.
