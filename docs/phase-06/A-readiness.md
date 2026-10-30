# Chapter A — check before Terraform

[Phase 6](README.md) · [Home](../../README.md)

**Purpose:** confirm that the cloud can accept `lab1` and `lab2`. This is a read-only checkpoint before installation and provisioning.

## 1. Run on R2d2 as joe

Download the new chapter once:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase6-check.py --check
```

Enter the existing SSH-key passphrase if requested, then each node's sudo password. The script selects the existing Kolla environment and private HTTPS profile; CLI activation is not required.

## 2. Read the short report

| Check | What it tells you |
|---|---|
| R2d2 | OS/architecture, tool disk space and whether Terraform is already installed |
| Cluster | Current container, database, messaging, API, service and agent checks |
| Image | Actual minimum RAM and OS-disk size required to boot |
| Network/key | Existing resource identities, available addresses and matching guest public key |
| Old VM | Nova status and whether its domain is still active on any node |
| Quotas | How much the project can still allocate, after used and reserved resources |
| Capacity | Available host RAM, actual Nova instance-disk space and Placement allocations |
| Cinder | Available LVM pools, default volume type and quota for one new 5 GiB volume |
| Saved records | Existing inputs and exercise checkpoints are unchanged |

**PASS** = this check passed. **REVIEW** = an observed value needs a decision. **BLOCKED** = the evidence could not be read; it does not prove a service is down.

Two checks matter before provisioning:

- The approved size is now **2 GiB RAM per VM**. The reviewed Glance image requires 2048 MiB, and the existing `lab.small` flavor matches 1 vCPU, 2048 MiB RAM and a 10 GiB OS disk. If a future image requires more, the checker reports a size review; it does not edit the image or silently raise the approved size.
- `lab-vm-01` should be cleanly shut down and retained, as requested. If it is still active, the report identifies it; this checker sends no stop request. A powered-off VM still retains cloud allocations and counts against quota.

The capacity review leaves a 1 GiB host-RAM and 2 GiB instance-disk margin. These are lab review margins, not resource reservations or a guarantee of Nova scheduling. The network-rule budget is six explicit IPv4 rules across the two new groups; the later Terraform definition will manage only those new groups and their generated rules.

## 3. Return the result

The last lines print the exact command to read the private text report. Return that text. Its companion JSON stays under `reports/` for detailed review; neither file belongs in Git.

Expected result:

```text
RESULT: PHASE6_READINESS_PASSED
```

or:

```text
RESULT: PHASE6_READINESS_NEEDS_REVIEW
```

`NEEDS_REVIEW` can simply mean the image requires more RAM or the old VM is still running. Review the named rows before moving on. No Terraform install, image edit, VM creation/shutdown, formatting or checkpoint update is performed by this command.

**Next:** [Chapter B — install Terraform](B-install-terraform.md) prepares the management tool on R2d2. Settle any sizing/power-state items from this report before proceeding to VM provisioning.

**Reviewed lab snapshot — 30 October 2026, 19:36 UTC:** cluster services, capacity, quotas and resource references passed. The original 1 GiB proposal was reviewed and Joe approved 2 GiB for each new VM. `lab-vm-01` was ACTIVE on node2; no shutdown result has been returned. That power-state check remains open. [Chapter C](C-write-configuration.md) can prepare local files before its shutdown; provisioning requires fresh readiness.

[Glance image RAM minimum](https://github.com/openstack/glance/blob/stable/2026.2/api-ref/source/v2/images-parameters.yaml) · [Nova quota details](https://github.com/openstack/nova/blob/stable/2026.2/api-ref/source/os-quota-sets.inc) · [Neutron quota details](https://github.com/openstack/neutron-lib/blob/master/api-ref/source/v2/quota_details.inc)
