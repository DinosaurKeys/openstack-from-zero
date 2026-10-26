# Chapter C — Prepare the first VM

[Phase 4](README.md) · [Previous: create networks](B-create-networks.md) · [Viewing commands](../command-glossary.md)

**Status: prepared and locally tested; live preparation is pending.** Chapter B must first pass its HA-router and DHCP placement checks for this deployment. This chapter prepares the ingredients; Chapter D will create and test the VM.

## 1. The four ingredients

| OpenStack term | Plain meaning | This lab |
|---|---|---|
| Image (Glance) | A prepared operating-system disk; similar to a VM template | Ubuntu Server 24.04, amd64, QCOW2, released build 20261017; `lab-ubuntu-24.04-20261017` |
| Flavor (Nova) | The VM hardware size; creating it reserves no CPU/RAM | `lab.small`: 1 vCPU, 2048 MB RAM, 10 GB root disk |
| Key pair (Nova) | The public SSH key to inject into a future guest | `lab-vm-key`; separate private key stays on R2d2 |
| Security group (Neutron) | Rules applied to a VM's network port | `lab-vm-access`: inbound SSH/TCP 22 and IPv4 ping from `192.168.0.0/24` only |

Ubuntu is suitable for learning Linux and later installing applications. This is a cloud image, not an ISO: it is already installed and uses cloud-init to configure the future guest. The guest login will be **ubuntu**, while the physical nodes continue to use **joe**. Successful boot/login remains a later test.

The 10 GB root disk will use the compute node's local VM storage when the VM is created. It is separate from a later Cinder LVM volume. The flavor is public (visible to projects across this lab); the image and security group belong to the existing admin project. Nova key pairs belong to the authenticated user.

The new security group retains Neutron's normal unrestricted IPv4/IPv6 outbound rules. It adds no inbound access from the whole internet or from other private VMs. It is not attached to anything yet. `192.168.0.0/24` means your home LAN; do not replace it with `0.0.0.0/0` to troubleshoot access. R2d2 is a VMware NAT guest; whether its translated connection reaches the VM is tested in Chapter D, not inferred here.

## 2. What changes

Run from **R2d2 as joe**. The script:

1. Checks the pinned tools, existing private HTTPS profile and tracked Chapter B networks/agents.
2. Refuses conflicting image/flavor/key/security-group names rather than adopting or overwriting them.
3. Downloads the pinned official Ubuntu released image to `private/phase4/` on R2d2. Its source is approximately 593 MB; allow several minutes and at least 3 GiB free on R2d2.
4. Checks the pinned SHA256, QCOW2 header and virtual disk size. HTTPS uses the system CA for Ubuntu downloads and the existing lab CA for OpenStack. No insecure TLS bypass is used.
5. Creates a separate Ed25519 guest key at `~/.ssh/openstack-from-zero-vm` with `ssh-keygen`. You choose its passphrase interactively. Only its public half is imported into Nova. The existing node-access key is unchanged.
6. Creates the private Glance image record, the flavor, public-key registration and security group. Uploads the verified image to Glance's existing local storage on node1; checks ACTIVE status, uploaded size and Glance's content hash.
7. Adds the two limited ingress rules and reads the preparation objects back. Saves one private report and tracked IDs.

The pinned checksum is checked against Ubuntu's HTTPS-published SHA256SUMS during development. The script pins that value; it does not independently perform GPG verification or follow a changing `current` image. If the dated image is unavailable, it stops instead of silently selecting another build.

No VM, floating IP or volume is created. No OpenStack CLI/SDK installation, node SSH/sudo, Kolla reconfigure, restart or existing network change is needed. Standard HTTPS authentication obtains short-lived tokens; token values and passwords are withheld.

## 3. Inspect the plan

On R2d2:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase4-prepare-vm.py
```

This prints the exact plan without connecting or creating files. Review the flavor and allowed source network above before applying.

## 4. Apply the preparation

```bash
/usr/bin/python3 scripts/phase4-prepare-vm.py --apply
```

During key generation, enter a passphrase when prompted and repeat it. Typing is hidden. The script also verifies that the private/public pair matches; `ssh-keygen` can ask for that passphrase again. Keep it in your password manager, not in GitHub or chat. A later rerun validates/reuses the existing pair; it does not rotate it.

The Ubuntu download can print progress. The Glance upload waits for its HTTPS response and may take several minutes. Keep the terminal open. No node sudo password is expected.

## 5. Return the report

Use the exact `cat` command printed at the end. Expected success:

```text
RESULT: VM_PREPARATION_VERIFIED
```

That means the preparation objects and image content passed their checks. It does not prove Nova can boot a guest, retrieve metadata, inject the key, obtain DHCP/DNS or send traffic through the USB NIC.

If it stops, return the report before retrying. The script records IDs and uncertain requests in `private/phase4/vm-preparation.json`; keep that file and the guest key. A confirmed partial run can reuse tracked objects, but an uncertain request stops for review. No automatic rollback, deletion, overwrite or request retry is performed. No OpenStack redeployment is needed to review a preparation failure.

## 6. View the objects

```bash
/usr/bin/python3 scripts/lab-view.py images
/usr/bin/python3 scripts/lab-view.py flavors
/usr/bin/python3 scripts/lab-view.py security-groups
```

In Horizon, view **Project → Compute → Images**, **Key Pairs**, and **Project → Network → Security Groups**. Flavors are visible through **Admin → Compute → Flavors** for your admin login. Viewing does not launch a VM.

## 7. Finish check and lesson

- [ ] Chapter B fresh HA network/router report reviewed.
- [x] Chapter C procedure and local tests prepared.
- [ ] Joe's live preparation report reviewed.
- [ ] Agree Chapter D VM creation, floating IP and traffic tests.

**Lesson:** the image supplies the OS, the flavor supplies the size, the key supplies the login identity and the security group supplies network access rules. None of those is a running VM. The virtual router is a separate network object; the API VIP is the shared front door for managing OpenStack.

## Local validation

Tests cover preparation request shapes/order, content verification, conflicts, reruns, uncertain outcomes, drift, scoped ingress, read-only views and secret-safe HTTP errors. A real local Ed25519 key is generated in a temporary directory to check public/private matching. These checks are not live Glance upload, scheduling, guest boot or packet-flow tests. The dated source listing and checksum were read from Ubuntu; no live cloud changes were made by the assistant.

[Ubuntu released cloud image](https://cloud-images.ubuntu.com/releases/noble/release-20261017/) · [SHA256SUMS](https://cloud-images.ubuntu.com/releases/noble/release-20261017/SHA256SUMS) · [Glance API](https://docs.openstack.org/api-ref/image/v2/) · [Nova API](https://docs.openstack.org/api-ref/compute/) · [Neutron API](https://docs.openstack.org/api-ref/network/v2/)
