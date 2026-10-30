# Goal and learning plan

[Home](../README.md) · [Start Phase 1](phase-01/README.md)

## Goal

Build and understand a three-node OpenStack HA lab: API Keepalived/HAProxy, MariaDB/Galera and Neutron virtual-router HA. Provision two VMs with Terraform, configure their application/database with Ansible, use Hermes to help prepare reviewed IaC files, and add monitoring. Automation should make an understood procedure repeatable.

## The lab

| Machine | Address | Planned job |
|---|---|---|
| R2d2 | 192.168.162.131/24 — DHCP on the NAT network | Management: Git, Ansible, Kolla-Ansible and later Terraform |
| node1 | 192.168.0.200 | Controller, network, compute and local Cinder LVM storage |
| node2 | 192.168.0.201 | Controller, network, compute and local Cinder LVM storage |
| node3 | 192.168.0.202 | Controller, network, compute and local Cinder LVM storage |

Nodes use Ubuntu Server 24.04 LTS. Each uses its built-in NIC for management and its USB NIC for external VM traffic.

A **controller** manages cloud requests. A **compute** runs VMs. **Cinder LVM** supplies persistent volumes from local storage. Three controllers do not replicate that local storage.

## Course

| Phase | Finished when |
|---|---|
| [1. Prerequisites](phase-01/README.md) | Ubuntu and SSH access work; actual network/disk details are reviewed and the remaining host preparation is verified |
| [2. Management environment](phase-02/README.md) | R2d2 has the selected installer tools and reviewed configuration |
| [3. Install OpenStack](phase-03/README.md) | API HA/database cluster/Neutron HA capability deployed; prechecks and cloud service/API checks pass |
| [4. VM, networking and volumes](phase-04/README.md) | VM networking and Cinder persistence pass reviewed live reports |
| [5. HA validation and VM migration](phase-05/README.md) | API VIP, database and router tests pass; planned VM migration is verified |
| [6. Terraform — Chapter C ready](phase-06/README.md) | Terraform provisions the two approved VMs and their supporting resources; verification passes and a second plan shows no changes |
| 7. Application with Ansible | One application VM and one PostgreSQL VM serve the shopping list and retain its database data |
| [8. Hermes with OpenRouter](phase-08/README.md) | The selected host runs Hermes with OpenRouter, the reviewed tools and a public working copy; Joe reviews and executes IaC |
| 9. Monitoring | Dashboards show the lab and a test alert works |

## How we learn

1. Explain the goal of the current step.
2. Explain the script: where it runs, what it changes, what its output means.
3. You download it from GitHub and run the explained command.
4. We review your actual result.
5. Read a short lecture connecting the result to the concept.
6. Mark the checkpoint complete and continue.

Each phase has its own guide. Work through one chapter at a time.

Chapters use letters: **Chapter A, Chapter B, Chapter C**, and so on. Phase 1 runs A–J. Phase 2 starts again at Chapter A. Numbered steps inside each chapter remain in order.

Completed chapters keep their full steps and explanations for study or a future rebuild. We add results and lessons while preserving the procedure.

## Starting point

R2d2 is installed: Ubuntu 24.04.5 LTS on VMware, hostname `r2d2`, account `joe`, interface `ens33`. Its observed DHCP address is `192.168.162.131/24`. All three node addresses replied to its pings.

The combined SSH inspection passed on all three nodes: Ubuntu 24.04, correct hostnames, management IPs and login as `joe`. Their configured gateway and DNS are `192.168.0.1`; R2d2 uses `192.168.162.2`. USB preparation also passed on all three, preserving management access. The NIC result and short lesson are in [Chapter H](phase-01/H-nics-and-storage.md); the separate Cinder disk step is [Chapter I](phase-01/I-cinder-storage.md).

The nodes share an untagged mini switch and TP-Link home router. Home DHCP allocates `.2–.150`. The initial cloud-address proposal is API VIP `.151` and external pool `.152–.199`, preserving nodes `.200–.202`. Start a rebuild with [Phase 1](phase-01/README.md). The [main README](../README.md#current-learning-step) identifies the current learning checkpoint.

Ceph/HA storage and automatic VM recovery are deferred. Complete one reviewed exercise at a time; obtain approval before service interruptions or VM migration.
