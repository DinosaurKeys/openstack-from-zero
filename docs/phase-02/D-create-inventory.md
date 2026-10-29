# Chapter D — Create the inventory

[Phase 2](README.md) · [Previous: install tools](C-install-tools.md)

**Goal:** tell Ansible which machines belong to this lab and which roles each machine has.

**Design:** all three nodes belong to the controller, network, compute and storage roles. This membership supplies the three API, Galera and Neutron HA hosts.

## 1. Purpose

The inventory is the address book and role list used by Ansible. It must use our confirmed machines and that release's group structure.

| Machine | Management address | Agreed cloud roles |
|---|---|---|
| node1 | `192.168.0.200` | Controller, network, compute and local LVM storage |
| node2 | `192.168.0.201` | Controller, network, compute and local LVM storage |
| node3 | `192.168.0.202` | Controller, network, compute and local LVM storage |

R2d2 runs the management tools; it is not an additional OpenStack node. Joe selected **all three nodes for the network role**. This group places cloud networking and API load-balancer services; writing the inventory does not install them.

The monitoring group stays unassigned until the monitoring phase. This step does not choose which optional services to enable.

## 2. Planned steps

1. Start from the installed release's multinode inventory structure.
2. Assign the agreed controller, compute and storage roles above.
3. Select account `joe` and the existing R2d2 SSH key; generate no new key pair.
4. Explain the inventory file and show the resulting host/group list.
5. Test Ansible access and the required privilege escalation, with prompts explained.
6. Review the report before creating deployment settings.

## 3. Script and commands

Read [phase2-inventory.py](../../scripts/phase2-inventory.py). It uses the installed Kolla **23.0.0 multinode template**, replacing its example hosts with our confirmed hosts while retaining its service-group relationships.

### What the script creates on R2d2

| Private file | Purpose |
|---|---|
| `private/phase2/inventory.ini` | Node addresses, role groups, joe account and existing SSH-key path |
| `private/phase2/check-access.yml` | Small Ansible playbook containing only the explained access checks |
| `reports/phase2-inventory-<UTC>-<random>.txt` | One combined result, including Ansible task output |
| Matching `.ansible.log` file in `reports/` | Private task log used to include errors in the combined report |

A fresh inventory assigns all three nodes to all four roles directly. Existing files with identical contents are reused. If either generated working file has different contents, the script stops and preserves it. Passwords and private-key contents are never written into the inventory. These working files and reports stay outside Git.

The important groups will be:

| Inventory group | Hosts | Meaning |
|---|---|---|
| `control` | node1, node2, node3 | OpenStack control services |
| `network` | node1, node2, node3 | Cloud networking and API load balancers |
| `compute` | node1, node2, node3 | Run virtual machines |
| `storage` | node1, node2, node3 | Independent local Cinder LVM backends |
| `deployment` | localhost | R2d2 runs deployment commands locally |

Other service groups come from Kolla's template. A host appearing in a group does not itself enable or install a service.

### What it checks on each node

1. **SSH and Python:** Ansible's `ping` module must run. This is different from a network ICMP ping.
2. **Identity:** read the hostname, login account and IPv4 addresses. They must match the intended node, joe and its management address.
3. **Sudo:** run `id -u` through sudo. Its answer must be `0`, meaning root.

Ansible may create its normal temporary module files on the nodes. These checks do not install packages, bootstrap servers, deploy OpenStack or change NICs, disks, SSH keys or sudo policy.

SSH host-key checking stays enabled. The script selects only the existing `/home/joe/.ssh/openstack-from-zero` key and verifies its public key is available to an SSH agent. It generates no new keys.

### Password prompts

- **SSH-key passphrase:** requested only if the existing key needs unlocking. Use the passphrase chosen in Phase 1.
- **`BECOME password:`:** enter joe's **sudo password for the node named immediately above the prompt**. The script checks node1, then node2, then node3 and asks separately, so their passwords may differ.
- This step does not ask for R2d2's sudo password. Password input is not included in the report.

If it needs a new SSH agent, the script starts one for its child commands and loads the key for four hours. An already loaded key keeps its existing lifetime. This does not export a new agent into your parent terminal.

### Create or verify the three-node inventory

Run these commands **on R2d2 as joe**. They enter the repository, download the guide/script and create/verify the inventory.

`/usr/bin/python3` deliberately uses Ubuntu's system Python to launch the script, even if your prompt already starts with `(kolla)`. The script uses the installed lab tools by their full paths.

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase2-inventory.py --apply
```

Without `--apply`, the script prints its plan and makes no changes. You do not need to run both modes.

To study the generated inventory after the run, this optional command opens it on R2d2. Press **q** to leave the viewer:

```bash
less ~/openstack-from-zero/private/phase2/inventory.ini
```

Kolla's standard service groups contain hyphens, so Ansible may display a group-name warning. The script separately validates the actual group memberships.

## 4. Finish check

The script first reports `INVENTORY_LAYOUT_OK` after local parsing. That alone does not prove node access.

After all three live checks pass, expect:

```text
NODE_ACCESS_OK node1
NODE_ACCESS_OK node2
NODE_ACCESS_OK node3
RESULT: INVENTORY_ACCESS_READY for all
```

Use the exact `cat` command printed at the end and send back that combined report. **Stop before Chapter E.** If it reports `INVENTORY_ACCESS_NEEDS_REVIEW`, return the report before rerunning or changing settings.

Local regression checks cover repeat runs and preservation of unknown edits. Real Ansible Core 2.21.5 parsed the generated inventory, verified its expected host/group mappings and syntax-checked the playbook against Kolla 23.0.0's template. Its template engine also evaluated the hostname/account/IP/root assertions with fixture data and rejected a wrong hostname. This development environment restricts the socket operations needed to run Ansible tasks; live SSH, module execution and sudo on your nodes remain unverified until your report.

## 5. Short lecture — after review

The completed access checks prove that Ansible can reach the intended machines as joe, execute Python modules and use sudo. An inventory group is a role assignment for later deployment; it does not install services by itself.

All three nodes are the selected compute targets. Inventory creation establishes roles; service deployment belongs in Phase 3. Later, removing a deployed compute requires first moving or shutting down its VMs and decommissioning the service; editing the inventory alone does not remove running services.

[Next: configure the lab](E-configure-lab.md).
