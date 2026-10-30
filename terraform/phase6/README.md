# Phase 6 Terraform configuration

Start with [Chapter C — write the configuration](../../docs/phase-06/C-write-configuration.md).

This configuration describes `lab1` and `lab2`: **1 vCPU, 2 GiB RAM and a 10 GiB local OS disk each**, plus one new 5 GiB Cinder data volume for `lab2`. It reuses the existing verified image, flavor, key and networks through read-only data sources.

Run management commands on **R2d2 as joe**. Chapter C prepares settings only. Chapter D will initialize using `private/phase6/backend.tfbackend` and create a reviewed plan; Chapter E will apply that saved plan after approval. Do not use a plain `terraform init` that falls back to a state file in this public directory.

Keep the real variables, Terraform state and saved plans under `private/phase6/`. Keep `.terraform.lock.hcl` in Git. Ansible installs the application/database in Phase 7; Terraform does not run guest commands or format disks.

The [mock plan tests](tests/plan.tftest.hcl) use Terraform's test framework and a mocked OpenStack provider. They require an initialized provider schema but no lab login. They are separate from live cloud validation.
