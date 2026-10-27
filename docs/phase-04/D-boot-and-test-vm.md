# Chapter D — Boot one VM and test its network

[Phase 4](README.md) · [Previous: VM ingredients](C-prepare-vm.md) · [Viewing commands](../command-glossary.md)

**Status: complete — reviewed live report `phase4-boot-vm-20261027T124644Z-dl3xem1r.txt` passed.** Chapter B's report verified one active/two standby router replicas and two DHCP agents. Chapter C's report verified the Ubuntu image, flavor, guest key and firewall group.

## 1. What we are building

Think of the image as a template, the flavor as a hardware size, and the private network as the VM's virtual switch.

| Item | This chapter |
|---|---|
| VM name | `lab-vm-01` |
| Operating system | The verified Ubuntu Server 24.04 cloud image from Chapter C |
| Hardware size | `lab.small`: 1 vCPU, 2048 MiB RAM, 10 GiB local root disk |
| Compute host | Nova chooses from the lab's available compute nodes; the report identifies it |
| Network | One port on `lab-private`, using the prepared `lab-vm-access` security group |
| Private address | Neutron allocates it from the existing subnet; no address is guessed |
| Floating address | One address allocated from the tracked external subnet/pool |
| Guest login | `ubuntu`, using `~/.ssh/openstack-from-zero-vm` on R2d2 |

Creating a VM consumes resources. Its root disk resides on the selected compute node. It is not a Cinder volume, and router HA does not restart this VM if that compute node fails.

A **floating IP** gives the VM an address reachable from your home LAN. Neutron maps that address to the VM's private IP. It is not a second IP configured directly inside the VM, and it is separate from the router's external IP and API VIP `.151`.

## 2. What the script does

Run **on R2d2 as joe**. It uses the existing private client profile, trusted lab CA and tracked IDs from Chapters B/C. No OpenStack CLI or SDK installation is required.

1. Check that the tracked networks, HA router, DHCP placement, image, flavor, key and security group still match the plan. Check the three compute services and Nova project quotas. Quota availability is not a measurement of physical free memory.
2. Unlock the existing guest SSH key in the local agent if needed. It creates no new SSH key. A key added by this command has a one-hour agent lifetime; an already loaded key is reused.
3. Create one private network port with the intended firewall group. Ask Nova to boot exactly one VM on that port, with the prepared image/flavor/key. Nova selects its host.
4. Wait for the VM and its port to become ACTIVE. Allocate and associate one floating IP, then verify that association.
5. Verify the VM's SSH host identity using public host-key information from its console, retrieved through the authenticated HTTPS Nova API. Record that key in a private known-hosts file. No blind host-key acceptance is used; raw console logs and Nova-generated password fields are not put in the report.
6. Log in using the guest key and run the bounded checks below. Save one private report with the actual VM host, addresses and observed router roles.

The small cloud-init user-data setting only enables public SSH host-key output to the VM console. It does not install applications, set a login password or configure static guest addresses. The VM uses the HTTP metadata service, with no config-drive fallback.

## 3. Inspect the plan first

On R2d2:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase4-boot-vm.py
```

Default mode prints the plan. It makes no network connections and creates no files or cloud resources.

## 4. Create and verify the VM

After reviewing the scope above:

```bash
/usr/bin/python3 scripts/phase4-boot-vm.py --apply
```

If prompted, enter the **guest SSH-key passphrase** from Chapter C. Typing is hidden. This is not joe's node sudo password or an Ubuntu guest password. No physical-node sudo prompt is expected.

The script waits for Nova/Neutron state, initial guest boot and SSH readiness. Each wait is bounded; keep the terminal open. The checks inside the guest use `sudo -n` to read its networkd lease and system state. If the image's expected guest sudo access is unavailable, the script reports it; it does not change sudo policy.

Normal boot, cloud-init, SSH and network probes generate ordinary guest logs. The guest helper installs no package and edits no network/service configuration. The VM and floating IP remain allocated after the command finishes.

## 5. What a successful check proves

| Check | Evidence collected |
|---|---|
| VM boot | Nova ACTIVE, known compute host, correct project/image/flavor/key and local root disk |
| Private port | Neutron ACTIVE, attached to this VM, correct subnet/security group/MAC |
| Guest identity | SSH host key matches the console; metadata UUID matches Nova's VM ID |
| Guest setup | Ubuntu 24.04 and completed cloud-init without reported errors |
| DHCP | Guest lease address/server/DNS/gateway compared with the tracked subnet and observed Neutron DHCP service addresses |
| Interface and routes | Find the guest NIC by its actual MAC, not an assumed name; verify IP/prefix, MTU and default route |
| Private gateway | Ping `10.10.10.1` from inside the VM |
| DNS | Resolve `cloud-images.ubuntu.com` inside the VM |
| Outbound connection | Make a bounded HTTPS request to `https://cloud-images.ubuntu.com/` with certificate verification |
| Floating-IP access | SSH and ping from R2d2 to the allocated floating IP |
| Router snapshot | Read current active/standby roles and identify the active node's known USB NIC |

R2d2 runs behind VMware NAT. These checks exercise that actual path and the existing home-LAN-only security group; working API access alone does not prove guest access. The script does not widen firewall rules automatically.

The Ubuntu guest uses systemd-networkd. The helper reads selected fields from its runtime lease snapshot; missing or unexpected lease evidence remains a review item. A configured subnet with DHCP enabled is not, by itself, proof that the guest obtained a lease.

The report combines guest routes, successful connections, current placement and the already inspected bridge mapping to explain the expected USB/VXLAN path. **It does not capture packets or test failover.**

## 6. Read and return the result

Use the exact `cat` command printed at the end. Expected marker:

```text
RESULT: FIRST_VM_NETWORKING_VERIFIED
```

Return that report before starting a volume or failover exercise. A successful API boot alone is not enough; all the required guest checks must pass.

On `FIRST_VM_NEEDS_REVIEW`, return the report. Confirmed resources remain in place. No deletion, rebuild, reboot, firewall change or automatic mutation retry is performed.

The private state is `private/phase4/vm.json`; guest SSH trust is `private/phase4/lab-vm-01-known-hosts`. Keep those files and the existing key. A confirmed later rerun validates/reuses its own resources. An unconfirmed request persists a pending marker and stops for review; do not delete that marker or state file to force another creation. Unknown same-name resources are not adopted.

## 7. View and recheck

On R2d2:

```bash
/usr/bin/python3 scripts/lab-view.py servers
/usr/bin/python3 scripts/lab-view.py floating-ips
/usr/bin/python3 scripts/lab-view.py router
```

In Horizon: **Project → Compute → Instances** and **Project → Network → Network Topology**. Browser sign-in remains a separate user-confirmed check.

After reviewing a report, use this mode to repeat the checks on the existing tracked VM without creating cloud resources. Once Chapter E completes, this check permits its validated tracked data volume; unknown attachments still stop verification. It does not test the volume's filesystem or data persistence:

```bash
/usr/bin/python3 scripts/phase4-boot-vm.py --verify
```

For an interactive guest shell, replace `FLOATING_IP_FROM_REPORT` with the actual address:

```bash
ssh -i ~/.ssh/openstack-from-zero-vm \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$HOME/openstack-from-zero/private/phase4/lab-vm-01-known-hosts" \
  ubuntu@FLOATING_IP_FROM_REPORT
```

Run `exit` to return from the guest to R2d2. The private key stays on R2d2; it is not copied into the VM or GitHub.

## 8. Short lesson and next checkpoint

The VM's compute host and active router host can differ. When they differ, private traffic can cross VXLAN over management NICs before reaching the active router's external USB connection. Floating-IP access is a NAT mapping, not another physical NIC in the guest.

Continue with [Chapter E — one Cinder data volume](E-cinder-volume.md), then controlled API/database/router failure tests, then planned VM migration. HA storage/Ceph and automatic VM recovery remain deferred. Keep the test guest on a surviving compute node when measuring router failover. No failure injection is performed in Chapter D.

## Local validation and limits

Tests cover exact one-VM/port/floating-IP scope, quotas, conflicts, drift, partial and uncertain requests, repeat verification, restricted API actions, secret-safe errors, DHCP/route/identity checks and guest result handling. Real temporary Ed25519 keys are used to compare fingerprints with `ssh-keygen`; host-key mismatch cases block login. API/guest/agent responses are fixtures. These tests are not a live Nova boot, actual DHCP lease, SSH login, USB packet capture or failover result. Joe's reviewed live report supplies VM/guest-network evidence; failover and migration remain untested.

[Nova server/console API](https://docs.openstack.org/api-ref/compute/) · [Neutron ports/floating IPs](https://docs.openstack.org/api-ref/network/v2/) · [cloud-init keys-to-console](https://docs.cloud-init.io/en/latest/reference/modules.html#keys-to-console)
