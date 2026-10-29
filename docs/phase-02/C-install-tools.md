# Chapter C — Install Kolla, Ansible and the OpenStack CLI

[Phase 2](README.md) · [Previous: select versions](B-select-versions.md)

**Goal:** install the tools on **R2d2 as joe**. The OpenStack CLI is part of this installation.

| Tool | What it does | Installation folder |
|---|---|---|
| Kolla-Ansible 23.0.0 and Ansible Core 2.21.5 | Install and manage OpenStack services | `/home/joe/venvs/kolla` |
| OpenStack CLI 10.3.1 (`openstack`) | List VMs, start/shut them down and manage cloud objects | `/home/joe/venvs/openstack-client` |

Each folder is a Python virtual environment: a separate place for that tool and its libraries. Activating an environment selects its commands for the current terminal.

## 1. Install the tools

Run **on R2d2 as joe**, using system Python:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase2-install-tools.py --apply
```

The [installer](../../scripts/phase2-install-tools.py):

1. Checks R2d2, joe, Ubuntu 24.04 and Python 3.12.
2. Shows the Ubuntu prerequisite package plan, then installs the required packages.
3. Creates or reuses the project's Kolla environment and installs the pinned Ansible/Kolla versions.
4. Checks versions, Kolla source commit, Python dependencies and required Ansible collections.
5. Installs the pinned OpenStack CLI in its own client environment and checks its version, dependencies and VM command help.
6. Saves private installation records and a report.

Enter joe's **R2d2 sudo password** when asked. APT may ask **Y/n** before installing prerequisites. Downloads can take several minutes.

Prerequisites: `git python3-dev libffi-dev gcc libssl-dev libdbus-glib-1-dev python3-venv`. Python packages are installed as joe inside their environments. This step prepares local tools; node preparation and OpenStack deployment come later.

Without `--apply`, the installer displays its plan and makes no changes. Unexpected existing directories are preserved and require review.

## 2. Check the result

Expected final line:

```text
RESULT: R2D2_TOOLS_READY
```

This confirms **both** the Kolla/Ansible tools and the OpenStack CLI passed their installation checks. API login is checked after deployment in [Phase 3 D](../phase-03/D-client-access.md).

The script prints the exact `cat` command for its private report. Return that report before continuing to inventory preparation. A `NEEDS_REVIEW` result means the installation is incomplete; review the report before continuing.

## 3. Use the tools

Choose the tool you need in the current terminal:

| Task | Activation command |
|---|---|
| Run Kolla/Ansible | `source /home/joe/venvs/kolla/activate-lab.sh` |
| Run `openstack` commands | `source /home/joe/venvs/openstack-client/bin/activate` |

Check the CLI installation:

```bash
source "$HOME/venvs/openstack-client/bin/activate"
openstack --version
```

Expected: `openstack 10.3.1`. A new terminal needs activation again. Activation selects installed tools; it does not log in to the cloud.

After deployment, [Phase 3 D](../phase-03/D-client-access.md) generates the private connection settings, loads them and verifies a CLI VM list over HTTPS. The [CLI reference](../openstack-cli.md) includes normal VM start/shutdown commands.

## Existing R2d2 — install only the CLI

If Kolla and Ansible are already installed, use this command instead of the full installation above:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase2-install-tools.py --client-only --apply
```

`--client-only` installs or verifies the CLI in `/home/joe/venvs/openstack-client`. It uses the `python3-venv` prerequisite from the existing tool setup. It does not run APT or reinstall Kolla/Ansible.

Expected: `RESULT: OPENSTACK_CLI_READY`. Then activate the client and follow [Phase 3 D](../phase-03/D-client-access.md#3-activate-the-cli-and-connect) to connect it.

Private dependency records stay inside each environment. The Kolla activation helper also selects the lab's UTF-8 locale, Ansible configuration and collection paths. Installation checks do not establish live API access or VM readiness.

[Next: create the inventory](D-create-inventory.md) · [Selected versions](B-select-versions.md)
