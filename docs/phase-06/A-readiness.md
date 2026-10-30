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

Two known items need confirmation:

- The draft's starting size is **1 GiB RAM per VM**. The original image-creation code set `min_ram=2048` MiB. If Glance still reports that value, the script assesses **2 GiB per VM** and clearly marks the size change for review. It does not change the approved plan or image.
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

[Glance image RAM minimum](https://github.com/openstack/glance/blob/stable/2026.2/api-ref/source/v2/images-parameters.yaml) · [Nova quota details](https://github.com/openstack/nova/blob/stable/2026.2/api-ref/source/os-quota-sets.inc) · [Neutron quota details](https://github.com/openstack/neutron-lib/blob/master/api-ref/source/v2/quota_details.inc)
