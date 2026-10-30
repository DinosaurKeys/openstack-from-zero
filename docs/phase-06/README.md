# Phase 6 — Terraform: create two VMs from a written plan

[Home](../../README.md) · [Course plan](../00-goal-and-plan.md) · [Resource check](../resource-and-heat-check.md)

**Status: course structure, VM names and starting sizes approved.** [Chapter A — readiness](A-readiness.md) and [Chapter B — install Terraform](B-install-terraform.md) are available; live reports are pending. Chapter B installs only the management tool on R2d2. Readiness decisions must be settled before VM creation. Chapters C–G remain the approved outline; no Terraform installation or VM creation has been reported yet.

## What Terraform does

You describe the VMs, networks and disks in files. Terraform shows the proposed changes, then asks OpenStack to build them. It keeps a **state file**: its record of which cloud objects belong to this configuration.

| Tool | Job in this course |
|---|---|
| Terraform, on R2d2 | Create the VMs, new data volume and their access rules |
| Ansible, run from R2d2 | Install and configure NGINX/application and PostgreSQL in Phase 7 |
| Horizon | View the VMs, addresses, disks and console |

Terraform uses the OpenStack provider to call the cloud APIs. Heat is not required.

## Approved starting VMs

Both guests use Ubuntu 24.04. These are the approved starting sizes, subject to Chapter A's image minimums, allocation and quota checks. The existing image-creation code sets a 2 GiB RAM minimum; Chapter A reads Glance to confirm whether the 1 GiB starting proposal needs revision.

| VM | Later role | vCPU | RAM | OS disk | New data disk |
|---|---|---:|---:|---:|---:|
| `lab1` | NGINX + shopping-list application | 1 | 1 GiB | 10 GiB | None |
| `lab2` | PostgreSQL | 1 | 1 GiB | 10 GiB | 5 GiB Cinder volume |

The request adds **2 VMs, 2 vCPUs, 2 GiB guest RAM, 20 GiB local OS disks and one 5 GiB data volume**, plus their ports, security groups and one floating IP. Guest RAM is separate from the OpenStack services' host memory needs.

Nova chooses the compute hosts; Cinder chooses the new volume's storage backend. We read and record the actual locations after creation. A separate local-LVM data volume is not replicated or storage HA.

## Reuse and create

| Reuse after checking current IDs | Create for this phase |
|---|---|
| Existing `lab-private` network and router | Two VM ports and two security groups |
| Existing `lab-external` floating-IP network | One new floating IP for `lab1` |
| Verified Ubuntu image and guest SSH keypair | `lab1`, `lab2`, one data volume and its attachment |
| A matching flavor, if one exists | A 1-vCPU/1-GiB/10-GiB flavor if needed and approved |

`lab-vm-01` is retained for the completed exercises. Check its current status and cleanly shut it down as already requested; keep its disks and floating IP. It is not imported into this Terraform configuration. Its shutdown result has not yet been returned. Include its retained resource allocations when checking quota.

`lab1` gets a new floating IP for browser/SSH access. `lab2` has a private address only. Ansible reaches `lab2` through an SSH jump via `lab1`; direct R2d2 access to the private subnet is not assumed.

Proposed access rules: SSH and application HTTP from the home LAN to `lab1`; SSH and PostgreSQL from `lab1` to `lab2`. Include the outbound access needed for DNS, metadata and Ubuntu package installation. PostgreSQL is not opened to the home LAN or internet.

## Work through these chapters in order

Run management commands **on R2d2 as joe**. Each execution chapter will contain: purpose, exact command, expected result and the next step.

| Chapter | Step | Finished when |
|---|---|---|
| [A — Check readiness](A-readiness.md) | Check cloud/project identity, cluster health, VM power state, image minimums, allocations, quotas, disk capacity and reusable resource IDs | Live report reviewed; any sizing/power-state decisions settled |
| [B — Install Terraform](B-install-terraform.md) | Check R2d2's OS, architecture and any existing Terraform; install the pinned official release and verify it | The selected Terraform version runs on R2d2 |
| C — Write the configuration | Declare the new resources, existing-resource references, variables, private state location and outputs | The files describe only the approved Phase 6 resources |
| D — Preview | Load the existing OpenStack connection settings; initialize, validate and save a plan | The plan shows the agreed additions, with zero changes/deletions to existing lab objects |
| E — Create | Review the complete saved plan, then explicitly approve and apply it once | Two new VMs are ACTIVE and the new volume is attached to `lab2` |
| F — Verify | Check sizes, compute/storage locations, IPs, SSH identity and access to both guests; run another plan | Expected resources are present and Terraform reports **No changes** |
| G — Repeat independently | Follow the reinstall/rebuild case below using the same documented workflow | You can explain what each command does and reuse the procedure |

The 30 October 2026 resource snapshot showed four CPU threads per node and **5.1 / 5.3 / 5.6 GiB available RAM**, with no swap in use. Heat containers/startup units and Heat-named queues were absent. This is the starting observation; Chapter A still needs OpenStack allocation/quota and VM-state evidence.

## Files planned for Chapter C

Public configuration goes in the proposed `terraform/phase6/` directory:

| File | Purpose |
|---|---|
| `versions.tf` | Pin Terraform and the OpenStack provider; declare the local state backend |
| `provider.tf` | Connect using the existing environment credentials and trusted lab CA |
| `variables.tf` | Define names, sizes and verified existing-resource IDs |
| `main.tf` | Describe the VMs, ports, rules, floating IP, volume and attachment |
| `outputs.tf` | Display VM IDs, addresses and the new volume ID for verification/Ansible |
| `lab.tfvars.example` | Show how to enter settings without real credentials |
| `.terraform.lock.hcl` | Record the selected provider version and package checksums |

Keep real settings, saved plans and `terraform.tfstate` under `private/phase6/`. Keep passwords and SSH private keys out of Terraform configuration and Git. The existing private `public-openrc.sh` supplies the credentials and CA settings for each terminal.

The checked official releases on 30 October 2026 are **Terraform 1.17.1** and **OpenStack provider 3.4.0**. These are proposed pins; installation verification and a real read-only cloud plan remain part of the execution chapters.

## Commands you will learn

This is a command overview, not a sequence to run before the configuration exists. The execution chapters will give the working directory and private file paths.

| Command | Plain meaning |
|---|---|
| `terraform init` | Prepare the directory and install the OpenStack connector |
| `terraform validate` | Check the configuration's structure; this does not prove cloud access |
| `terraform plan` | Show proposed cloud changes; save the reviewed plan for creation |
| `terraform apply` | Perform the changes; passing a saved plan executes it without another confirmation prompt |
| `terraform output` | Display the values selected for the result summary |

After creation, another plan should report **No changes**. Routine checks use `plan`; recreating the lab is a separately reviewed action. The database volume will have a deletion guard. Phase 7's Ansible work will identify that new volume, verify it is blank before formatting, and configure its persistent mount.

## When reinstalling

| What you reinstall | What to do |
|---|---|
| Terraform only; the cloud is unchanged | Reinstall the selected version, keep the same configuration, lock file and private state, then initialize and preview again |
| R2d2; the cloud is unchanged | Recover its private credentials, reviewed CA, SSH keys and Terraform state before managing the existing VMs |
| OpenStack itself | Complete the cloud prerequisite phases, verify the new cloud identity/IDs, then use a separately approved fresh Terraform state for the new deployment |

The state file is Terraform's record of ownership. Do not discard it merely to reinstall the tool. Git stores the infrastructure description; it does not store this private record or restore PostgreSQL data.

Phase 6 finishes with the infrastructure verified. **Phase 7** installs the shopping-list application and PostgreSQL with Ansible. **Phase 8** adds monitoring.

## Source checks

[Terraform release](https://github.com/hashicorp/terraform/releases/tag/v1.17.1) · [Terraform plan command](https://github.com/hashicorp/terraform/blob/v1.17.1/internal/command/plan.go) · [Terraform apply command](https://github.com/hashicorp/terraform/blob/v1.17.1/internal/command/apply.go) · [OpenStack provider release](https://github.com/terraform-provider-openstack/terraform-provider-openstack/releases/tag/v3.4.0) · [Provider authentication](https://github.com/terraform-provider-openstack/terraform-provider-openstack/blob/v3.4.0/docs/index.md) · [Cinder volume attachment](https://github.com/terraform-provider-openstack/terraform-provider-openstack/blob/v3.4.0/docs/resources/compute_volume_attach_v2.md)

These sources were reviewed for this draft. No Terraform installation, provider initialization, cloud plan or VM creation has been tested by this documentation change.
