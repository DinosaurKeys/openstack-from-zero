# Chapter C — Install management tools

[Phase 2](README.md) · [Previous: select versions](B-select-versions.md)

**Goal:** install the agreed management tools in an isolated Python environment on R2d2.

**Status: ready for Joe to run; live installation report pending.** Joe agreed to Chapter B's version set and environment location. The script has local simulated-command checks; R2d2's actual installation must still be verified.

## 1. Purpose

A Python virtual environment is a separate folder for this project's Python tools. It helps keep Kolla's dependencies separate from Ubuntu's system Python.

## 2. Planned steps

1. Show the exact Ubuntu prerequisite packages needed by the selected release and which are already installed.
2. Explain the proposed environment location; preserve any existing environment unless its replacement is agreed.
3. Publish the installation script and explain its sudo and package-manager prompts.
4. Joe pulls and runs it on R2d2.
5. Verify the Python, Ansible and Kolla-Ansible versions from that environment.
6. Save one private report and review the result before inventory work.

## 3. Script and commands

Read [phase2-install-tools.py](../../scripts/phase2-install-tools.py). Run it **on R2d2 as joe, using system Python, without putting sudo before the script**.

### What will happen

| Step | Action and purpose |
|---|---|
| Confirm the machine | Require R2d2, joe, Ubuntu 24.04 and system Python 3.12 |
| Inspect the environment path | Use only `/home/joe/venvs/kolla`; stop if an existing folder is not marked as this script's agreed setup |
| Refresh package information | `sudo apt-get update` downloads the current Ubuntu package list |
| Show prerequisites | Simulate the package installation first; stop if it proposes removals |
| Install prerequisites | Install the seven packages listed in Chapter B, with APT's normal confirmation |
| Create the environment | `python3 -m venv` prepares a separate folder for the management tools |
| Install tools | Update pip inside that folder, then install Ansible Core **2.21.5** and Kolla-Ansible **23.0.0** at the agreed source commit |
| Verify tools | Check the installed versions, source commit, environment and Python dependency consistency |
| Download collections | `kolla-ansible install-deps` downloads reusable Ansible tasks; it does not execute node tasks |
| Record the result | List installed collections, save private dependency records and create an activation helper |

APT prerequisites: `git python3-dev libffi-dev gcc libssl-dev libdbus-glib-1-dev python3-venv`. Git was already installed in Chapter A. The package manager handles required dependencies; this is not a general system upgrade.

All Python package installation runs **as joe inside the virtual environment**. Ansible collections go under `/home/joe/venvs/kolla/collections`. Existing collections elsewhere are not the target of this installation. Kolla's collection requirements include a stable-branch dependency; the script records the resolved collection versions, but does not claim a complete offline rebuild archive.

No SSH connection, node package installation, Docker setup or deployment is part of this script. It does not create `/etc/kolla`, an inventory or passwords.

### Password and confirmation prompts

- **Sudo password:** enter joe's Ubuntu login password on **R2d2**. Linux does not display the characters while you type.
- **APT `Do you want to continue? [Y/n]`:** review the packages, then enter **Y** to install. If everything is already present, APT may not ask.
- **SSH-key passphrase and node passwords:** this step does not use SSH and will not request them.

Internet access is needed for Ubuntu, Python package, Git and Ansible Galaxy downloads. Download output may appear after a command finishes; let that command complete.

### Download and run

The first two commands enter the repository and download the published script. The final command installs the agreed tools; `--apply` enables the changes described above.

```bash
cd ~/openstack-from-zero
git pull --ff-only
python3 scripts/phase2-install-tools.py --apply
```

Without `--apply`, the script only prints its plan and makes no changes. You do not need to run both modes for this agreed installation.

If an installation stops partway, it keeps the packages and environment already created and prints a report path. It does not delete or automatically roll back the environment. Return that report for review before proceeding.

### Using these tools later

After the installation succeeds, this single command activates the tools **in the current Bash terminal**:

```bash
source /home/joe/venvs/kolla/activate-lab.sh
```

The helper activates the Python environment, selects its collection folder and Ansible working folder, and uses an empty Ansible configuration (`/dev/null`) so an unrelated existing `ansible.cfg` is not picked up. Later inventory commands will supply their settings explicitly. A new terminal needs this activation again. The installation script uses absolute tool paths and does not need prior activation.

To leave the tools in that terminal:

```bash
deactivate
unset ANSIBLE_COLLECTIONS_PATH ANSIBLE_HOME ANSIBLE_CONFIG
```

These commands change terminal variables; they do not uninstall anything.

## 4. Finish check

Expected final marker:

```text
RESULT: R2D2_TOOLS_READY
```

This means the pinned tool versions, source commit, Python dependency check and required collection presence checks passed. It does not mean OpenStack has been deployed.

The script prints a private report path such as `reports/phase2-tools-<UTC>-<random>.txt` and the exact `cat` command to read it. **Run that printed command and send the report back. Stop before Chapter D.** APT/sudo prompts and installation output stay in your terminal; their command and exit status are recorded in the report.

Private dependency records also remain inside the environment:

- `installed-python-packages.txt`: installed Python package versions.
- `installed-ansible-collections.json`: installed collection names, versions and locations.

These records are not uploaded to GitHub. If the marker is `R2D2_TOOLS_NEEDS_REVIEW`, return that report without advancing.

Local validation uses simulated commands and temporary folders. It covers preservation of existing folders, package-removal rejection, exact version/source checks, required collection checks, interrupted installation and the completed record/helper flow. It does not prove R2d2's APT, download or live tool behavior.

## 5. Short lecture — after review

The lesson will explain the created environment and how to use its tools after installation succeeds.

[Next planned chapter: create the inventory](D-create-inventory.md).
