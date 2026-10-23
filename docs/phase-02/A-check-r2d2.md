# Chapter A — Check R2d2

[Phase 2](README.md) · [Previous: Phase 1 result](../phase-01/J-cinder-lvm.md#6-reviewed-creation-result)

**Goal:** see what is already available on R2d2 before choosing and installing management tools.

**Status: collection and review complete.** Joe's report returned `R2D2_STATE_COLLECTED` on 2026-10-23 at 14:22:10 UTC. The original procedure stays below for rebuilding. This chapter collects facts; it does not install OpenStack.

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

## 5. Reviewed result

Joe returned `phase2-r2d2-20261023T142210Z-cgyer25u.txt`.

| Item | Observed result |
|---|---|
| Machine | R2d2, Ubuntu 24.04.5 LTS, x86_64 |
| Resources | 2 logical CPUs; 7.71 GiB total RAM, 6.40 GiB available at collection |
| Filesystem | 38.09 GiB total; 25.36 GiB free for both home and repository (the same filesystem) |
| Python | System Python 3.12.3; no active virtual environment |
| Python environment support | `venv` module found; `ensurepip` and `pip` not found in the inspected interpreter |
| Existing tools | Git and SSH available; Ansible/Kolla commands not found on the current PATH |
| Potential Ubuntu prerequisites | Git/Python installed; the inspected development packages, `python3-pip` and `python3-venv` were absent |
| Known environment folders | The three inspected candidate folders were not present |
| Kolla configuration paths | `/etc/kolla`, `globals.yml` and `passwords.yml` were not present or inaccessible; contents were not read |

**Checkpoint: collection complete.** This records the starting point for the version and installation plan. It does not establish node readiness or approve installation. Missing packages are expected observations; they do not require a troubleshooting step here.

The next chapter will check compatible releases before deciding which packages to install. Existing SSH keys, node networks and LVM pools do not need to be recreated.

## 6. Short lecture — after review

**Python** runs the management software. **pip** installs Python packages. A **virtual environment** is a separate folder for those tools and their dependencies, keeping them separate from Ubuntu's system Python.

The `venv` module being visible does not mean everything needed to create a working environment is installed: this report also shows missing `ensurepip` and the Ubuntu `python3-venv` package. Chapter C will cover the prerequisites after versions are agreed.

**Ansible** carries out administrative tasks on the nodes. **Kolla-Ansible** supplies the OpenStack deployment tasks. We choose compatible versions in Chapter B, then install the agreed tools on R2d2 in Chapter C.

No further command is needed for this completed inspection.

[Next planned chapter: select compatible versions](B-select-versions.md).
