# Chapter D — Create the inventory

[Phase 2](README.md) · [Previous: install tools](C-install-tools.md)

**Goal:** tell Ansible which machines belong to this lab and which roles each machine has.

**Status: draft.** Inventory generation and commands await the installed Kolla release.

## 1. Purpose

The inventory is the address book and role list used by Ansible. It must use our confirmed machines and that release's group structure.

| Machine | Management address | Agreed cloud roles |
|---|---|---|
| node1 | `192.168.0.200` | Controller and local LVM storage |
| node2 | `192.168.0.201` | Controller, compute and local LVM storage |
| node3 | `192.168.0.202` | Controller, compute and local LVM storage |

R2d2 runs the management tools; it is not an additional OpenStack node.

## 2. Planned steps

1. Start from the installed release's multinode inventory structure.
2. Assign the agreed controller, compute and storage roles above.
3. Select account `joe` and the existing R2d2 SSH key; generate no new key pair.
4. Explain the inventory file and show the resulting host/group list.
5. Test Ansible access and the required privilege escalation, with prompts explained.
6. Review the report before creating deployment settings.

## 3. Script and commands

The generation/validation script will be published after the selected tool environment exists. It will not bootstrap servers or install OpenStack. Passwords will not be embedded in the inventory or committed to GitHub.

## 4. Finish check

Ansible lists the expected machines in the correct groups and reaches them as the intended user. Remote Ansible checks may transfer temporary modules; their exact actions will be explained in the runnable guide.

## 5. Short lecture — after review

The lesson will connect the reviewed inventory groups to the services they control.

[Next planned chapter: configure the lab](E-configure-lab.md).
