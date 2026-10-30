# Chapter B — install Terraform on R2d2

[Phase 6](README.md) · [A — Readiness](A-readiness.md) · [Home](../../README.md)

**Purpose:** install the Terraform command-line tool on **R2d2 as joe**. Think of this as preparing the management tool; VM creation comes in Chapter E.

This chapter installs **Terraform 1.17.1**. It uses Python's built-in download/ZIP tools, so it does not require a new Python environment or an OpenStack login.

## 1. Get the chapter — once

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

## 2. Check the installation plan

```bash
/usr/bin/python3 scripts/phase6-install-terraform.py --check
```

The check reads R2d2's Ubuntu version, CPU architecture, available staging space and any existing Terraform installation. It downloads and installs nothing.

Expected: `TERRAFORM_INSTALL_PLAN_READY`, or `TERRAFORM_READY` if the selected version is already present. A different existing version stops for review instead of being overwritten.

## 3. Install the tool

```bash
/usr/bin/python3 scripts/phase6-install-terraform.py --apply
```

Enter **joe's sudo password on R2d2** when requested.

The installer:

1. Downloads the correct Linux archive and SHA256 checksum list from HashiCorp over verified HTTPS.
2. Checks the archive's checksum, executable architecture and reported Terraform version.
3. Installs the executable at `/usr/local/bin/terraform`.
4. Verifies the installed file and version, then prints the report location.

A matching existing installation is verified and retained. Temporary download files are removed; Terraform configuration, state and OpenStack resources are untouched.

Expected:

```text
RESULT: TERRAFORM_READY
```

Here, the script's `--apply` installs the **tool**. The later `terraform apply` command creates or updates the infrastructure described in configuration files.

## 4. Verify the command in your terminal

```bash
hash -r
command -v terraform
terraform version
```

For a new installation, the path should be `/usr/local/bin/terraform`. The version should be:

```text
Terraform v1.17.1
on linux_amd64
```

An ARM64 R2d2 reports `linux_arm64`. Use the course's selected version for repeatability rather than changing versions during a rebuild.

## 5. Finish the checkpoint

Return the installation report or the version output. **Tool installation is complete only after this live verification.**

This installer sends no SSH commands to the nodes and no requests to OpenStack. It creates no VMs, volumes, networks, Terraform configuration or state files. The OpenStack provider is downloaded later during Chapter D's `terraform init`.

Installing the tool is independent of VM RAM sizing. Settle Chapter A's image minimum, quota and old-VM power-state checks before VM provisioning. **Next: [Chapter C — write the Terraform configuration](C-write-configuration.md).** Writing the files does not create VMs or shut down the old VM.

## Reinstalling later

Repeat steps 2–4. The same verified version is retained. Keep the existing private Terraform state and configuration when the cloud is unchanged; reinstalling the executable does not require deleting either.

[Official release](https://github.com/hashicorp/terraform/releases/tag/v1.17.1) · [HashiCorp download/checksum example](https://github.com/hashicorp/web-unified-docs/blob/main/content/validated-designs/docs/docs/terraform/installation-guide/air-gapped-configuration.mdx) · [Terraform version output](https://github.com/hashicorp/terraform/blob/v1.17.1/internal/command/views/version.go)

**Reviewed lab result — 30 October 2026:** Joe's `terraform-install-20261030T193021Z-l5jtkfkm.txt` reported the official archive checksum, architecture and installed version verified at `/usr/local/bin/terraform`. The subsequent `--check` and repeat `--apply` runs returned `TERRAFORM_READY` and retained Terraform 1.17.1. Chapter B passed in that run; future installations still require their own live result. The assistant did not install software on R2d2.
