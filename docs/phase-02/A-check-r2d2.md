# Chapter A — Check R2d2

[Phase 2](README.md) · [Previous: Phase 1 result](../phase-01/J-cinder-lvm.md#6-reviewed-creation-result)

**Goal:** see what is already available on R2d2 before choosing and installing management tools.

**Status: ready to run.** The live report is pending. This chapter collects facts; it does not install OpenStack.

## 1. Purpose

Ansible runs administrative tasks on the nodes. Kolla-Ansible uses Ansible to install and configure OpenStack services. R2d2 will run these tools, so we first need its current Python version, free space and existing tool information.

Your completed Phase 1 results remain valid checkpoints. This local check does not repeat disk preparation, SSH-key creation or network changes.

## 2. What the script checks

Read [phase2-check.py](../../scripts/phase2-check.py). Run it **on R2d2 as joe, without sudo**.

| Check | Why it matters |
|---|---|
| Hostname, Ubuntu version and architecture | Confirm the management machine |
| Logical CPU count, RAM and free filesystem space | Record available resources before installation |
| Running Python, virtual-environment state and module availability | Plan a compatible isolated tool environment |
| Local Git, Python and SSH version commands | Record the tools used to download code and manage connections |
| Selected Ubuntu package records | See which potential prerequisites already exist; install nothing |
| Ansible/Kolla executable paths and Python package metadata | Detect existing tools without launching them |
| Known environment paths and `/etc/kolla` file presence | Avoid blindly replacing an existing setup; read no configuration contents |

The script makes no SSH or other network connections, reads no private keys or credentials, and changes no packages or system settings. It saves one private text report under `reports/` on R2d2.

A tool missing from the current PATH or Python environment might exist elsewhere. The report states that limitation; it does not assume the machine has never had that tool installed.

## 3. Download and run

These commands enter the course folder, download Phase 2, then run the local inspection:

```bash
cd ~/openstack-from-zero
git pull --ff-only
python3 scripts/phase2-check.py
```

There is no apply mode and no password prompt for this check. It does not test node logins.

## 4. Check and return the result

Expected collection marker:

```text
RESULT: R2D2_STATE_COLLECTED
```

This means the requested facts were collected, not that the installer is ready. Missing packages are observations for the installation plan. If collection is incomplete, the marker is `R2D2_STATE_NEEDS_REVIEW`; send that report too.

Use the exact `cat` command printed at the end and send back the report. **Stop here before installing tools.** We review it, then agree on Chapter B's version selection.

## 5. Short lecture — after review

The short lesson will be added after Joe's actual report is reviewed. All commands above will remain visible.

[Next planned chapter: select compatible versions](B-select-versions.md).
