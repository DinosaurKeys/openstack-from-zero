# Chapter C — write the Terraform configuration

[Phase 6](README.md) · [B — Install Terraform](B-install-terraform.md) · [Home](../../README.md)

**Purpose:** describe the two new VMs in files. This chapter prepares local settings on **R2d2 as joe**. It creates no cloud resources and does not shut down the old VM.

## 1. Understand the configuration

The reviewed Ubuntu image requires at least 2 GiB RAM. The approved configuration reuses the existing `lab.small` flavor:

| VM | Later purpose | vCPU | RAM | OS disk | New data disk |
|---|---|---:|---:|---:|---:|
| `lab1` | NGINX + shopping-list application | 1 | 2 GiB | 10 GiB | None |
| `lab2` | PostgreSQL | 1 | 2 GiB | 10 GiB | `lab2-data`, 5 GiB |

Terraform describes the infrastructure. **Ansible installs the application and database in Phase 7.** The new data volume stays blank in Phase 6.

| Existing objects — read and reuse | New objects — create later |
|---|---|
| Ubuntu image and `lab.small` flavor | `lab1` and `lab2` |
| Guest public-key registration | `lab1-port` and `lab2-port` |
| Private network, subnet and router | `lab1-access` and `lab2-access` security groups |
| External network and subnet | Six IPv4 access rules and one new floating IP for `lab1` |
| Verified Cinder volume type | `lab2-data` and its attachment to `lab2` |

The eventual first plan should show **16 additions, 0 changes and 0 deletions**. A volume attachment and floating-IP association each count as a separate Terraform resource. The existing image/flavor/networks/key remain read-only references; the old VM and its disks are outside this configuration.

Nova selects the compute hosts. Cinder selects the new volume's storage node. We check the actual locations after creation; local LVM storage is not replicated.

## 2. Get the files once

Run on **R2d2**, from the repository:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

The configuration is already written in [`terraform/phase6/`](../../terraform/phase6/). You do not need to paste Terraform code into your terminal.

| File | Plain meaning |
|---|---|
| [`versions.tf`](../../terraform/phase6/versions.tf) | Select Terraform 1.17.1 and OpenStack provider 3.4.0; declare local state |
| [`provider.tf`](../../terraform/phase6/provider.tf) | Use the existing OpenStack login and trusted CA; verify HTTPS |
| [`variables.tf`](../../terraform/phase6/variables.tf) | Define the existing-resource references and home LAN |
| [`existing.tf`](../../terraform/phase6/existing.tf) | Read and check the existing project, image, flavor, key and networks |
| [`main.tf`](../../terraform/phase6/main.tf) | Describe the new VMs, ports, rules, address and volume |
| [`outputs.tf`](../../terraform/phase6/outputs.tf) | Display the resulting VM/volume IDs and addresses |
| [`lab.tfvars.example`](../../terraform/phase6/lab.tfvars.example) | Show the settings format, without real credentials |
| [`.terraform.lock.hcl`](../../terraform/phase6/.terraform.lock.hcl) | Record the provider version and verified package checksums |

A **data** block reads an existing object. A **resource** block describes an object Terraform owns. The VM size comes from the existing flavor; the configuration checks that it is exactly 1 vCPU, 2048 MiB RAM and a 10 GiB OS disk.

## 3. Prepare the private settings

```bash
/usr/bin/python3 scripts/phase6-configure.py --prepare
```

This command reads the **newest Chapter A JSON report** and compares its IDs with the existing Phase 4 records. It prints the selected report name. It does not contact OpenStack, SSH to the nodes, run Terraform or request sudo.

It writes only:

| Private file | Purpose |
|---|---|
| `private/phase6/lab.tfvars.json` | Verified project/user, image, flavor, key, network/subnet and volume-type references |
| `private/phase6/backend.tfbackend` | The absolute location for the later private Terraform state |

Matching files are kept. Different existing settings stop for review instead of being overwritten. Original reports/checkpoints and any existing Terraform state are retained.

Expected:

```text
LOCAL FILES ONLY — Chapter C; no SSH, cloud request or Terraform execution.
Readiness snapshot: phase6-check-...json
VM     | vCPU | RAM   | OS disk | New data disk
lab1   | 1    | 2 GiB | 10 GiB  | none
lab2   | 1    | 2 GiB | 10 GiB  | 5 GiB
...
RESULT: TERRAFORM_CONFIGURATION_READY
```

`READY` here means **the configuration files are prepared**. It does not mean the VMs exist or that a live cloud plan has passed. The earlier report's RAM review is compatible with the now-approved 2 GiB size. The old VM may remain running while writing these files; it must be cleanly shut down and verified before provisioning.

To select a particular Chapter A report, add `--report reports/NAME.json`, using the actual filename printed by Chapter A. For a future rebuild, first run Chapter A on that deployment; old cloud IDs are not reusable after reinstalling OpenStack.

## 4. Check the file formatting

```bash
terraform -chdir=terraform/phase6 fmt -check -recursive
```

**Expected: no output and a returned shell prompt.** That means the files are formatted correctly. This command does not download a provider or contact the cloud.

## 5. Finish this chapter

Return the preparation output. Chapter C is complete when it reports `TERRAFORM_CONFIGURATION_READY` and the formatting check passes.

**Next: Chapter D — Preview.** That chapter will initialize the provider with the private backend settings, validate the configuration, then save and review a cloud plan. Before provisioning, shut down and retain `lab-vm-01`, then require fresh Chapter A checks to pass. Chapter D's execution guide follows this checkpoint; do not run `terraform apply` from this chapter.

## Access and data explained

- `lab1`: SSH 22 and HTTP 80 from `192.168.0.0/24`; one newly allocated floating IP. HTTP starts working after Phase 7 installs NGINX.
- `lab2`: private IP only. SSH 22 and PostgreSQL 5432 accept traffic from `lab1`'s security group. R2d2 will reach it through `lab1` as an SSH jump host.
- Both new groups have IPv4 outbound access for DNS, metadata and package installation. Terraform removes generated rules only in these two new groups, then manages the six declared rules.
- The two VMs and new data volume have `prevent_destroy` guards against planned deletion/replacement while their resource blocks remain in the configuration. State is still essential; these guards do not recover lost state or protect against manual cloud deletion.

The later state file will be `private/phase6/terraform.tfstate`. Keep real settings, state and saved plans private. Passwords come from the existing private OpenStack environment; SSH private keys are not copied into Terraform.

## Validation and sources

The [GitHub verification run](https://github.com/DinosaurKeys/openstack-from-zero/actions/runs/41121838628) passed **55 Python tests**, Terraform formatting, provider 3.4.0 initialization/checksum verification, `terraform validate` and **six mock-plan tests** on Ubuntu 24.04 with Terraform 1.17.1. The tests cover the approved plan, wrong image/flavor/login/subnet references and a higher image RAM minimum. The preparation fixtures also check repeat runs and preservation of earlier state/records.

These checks use fake cloud data and no lab credentials. The live validation/plan checkpoint on R2d2 remains Chapter D; no VM creation is claimed.

[Provider 3.4.0 documentation](https://github.com/terraform-provider-openstack/terraform-provider-openstack/tree/v3.4.0/docs) · [Ports and VM security groups](https://github.com/terraform-provider-openstack/terraform-provider-openstack/blob/v3.4.0/docs/resources/compute_instance_v2.md#instances-and-ports) · [Explicit new-group rules](https://github.com/terraform-provider-openstack/terraform-provider-openstack/blob/v3.4.0/docs/resources/networking_secgroup_v2.md#default-security-group-rules) · [Volume attachment](https://github.com/terraform-provider-openstack/terraform-provider-openstack/blob/v3.4.0/docs/resources/compute_volume_attach_v2.md) · [Local backend](https://developer.hashicorp.com/terraform/language/backend/local) · [Dependency lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock)
