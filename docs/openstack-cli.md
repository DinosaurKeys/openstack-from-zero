# OpenStack CLI on R2d2

[Home](../README.md) · [Command glossary](command-glossary.md) · [Migration](phase-05/E-vm-migration.md)

**Goal:** use `openstack` to list VMs and control their power. Run these steps **on R2d2 as joe**. The client gets its own Python environment.

## 1. Install the CLI — once

```bash
/usr/bin/python3 -m venv "$HOME/venvs/openstack-client"
source "$HOME/venvs/openstack-client/bin/activate"
python -m pip install "python-openstackclient==10.3.1"
openstack --version
```

Expected version: `openstack 10.3.1`. This client version is listed in the official OpenStack 2026.2 requirements. Installation affects the new client environment on R2d2; the Kolla environment remains separate.

## 2. Load your existing connection settings

```bash
source "$HOME/openstack-from-zero/private/phase2/kolla/public-openrc.sh"
```

This generated private file supplies the existing public API address, admin credentials, region and lab CA certificate. Source it; do not print or share its contents. No password needs to be pasted into a command.

In each new terminal, repeat:

```bash
source "$HOME/venvs/openstack-client/bin/activate"
source "$HOME/openstack-from-zero/private/phase2/kolla/public-openrc.sh"
```

## 3. List VMs and their compute hosts — read-only

```bash
openstack server list --all-projects --long \
  -c ID -c Name -c Status -c Host
```

All-project listing and host visibility require the admin policy access provided by the selected account. `Host` is the compute location, not a Cinder storage backend.

For the course VM's details:

```bash
openstack server show lab-vm-01 \
  -c id -c name -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c OS-EXT-STS:task_state \
  -c OS-EXT-STS:power_state
```

Use the VM UUID if a name is ambiguous. `VM` in a command example is a placeholder, not a literal VM name.

## 4. Start or shut down a VM

Choose **one** power action for the selected VM. These commands change its power state.

| Action | Command | Normal starting state |
|---|---|---|
| Start | `openstack server start lab-vm-01` | `SHUTOFF` |
| Shut down | `openstack server stop lab-vm-01` | `ACTIVE` |

A successful request may return without text. Check again with `openstack server list --all-projects --long`; wait for `ACTIVE` after start or `SHUTOFF` after shutdown. Shutdown retains the VM and its disks.

**ERROR needs separate recovery.** A normal start requires SHUTOFF. If the VM is ERROR, return its read-only details and [actual-runtime check](command-glossary.md#vm-power-controls) before choosing recovery. Do not reset a status merely to make a power or migration command available.

The list/show commands do not start, stop or repair anything. Power control is separate from starting a host's `nova_compute` service.

[OpenStack 2026.2 client constraints](https://github.com/openstack/requirements/blob/stable/2026.2/upper-constraints.txt) · [OpenStack CLI configuration](https://docs.openstack.org/python-openstackclient/latest/configuration/index.html) · [Server commands](https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/compute/v2/index.html#server-list)
