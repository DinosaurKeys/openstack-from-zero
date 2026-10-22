# Goal and learning plan

[Home](../README.md) · [Start Phase 1](phase-01/README.md)

## Goal

Build and understand your OpenStack lab, deploy an application, and monitor it. Automation should make an understood procedure repeatable.

## The lab

| Machine | Address | Planned job |
|---|---|---|
| R2d2 | NAT address — to observe | Management: Git, Ansible, Kolla-Ansible and later Terraform |
| node1 | 192.168.0.200 | Controller |
| node2 | 192.168.0.201 | Controller and compute |
| node3 | 192.168.0.202 | Controller, compute and planned Cinder LVM storage |

Nodes use Ubuntu Server 24.04 LTS. Each uses its built-in NIC for management and its USB NIC for external VM traffic.

A **controller** manages cloud requests. A **compute** runs VMs. **Cinder LVM** supplies persistent volumes from local storage. Three controllers do not replicate that local storage.

## Course

| Phase | Finished when |
|---|---|
| 1. Prerequisites | Ubuntu and SSH access work; actual network/disk details are reviewed and the remaining host preparation is verified |
| 2. Management environment | R2d2 has the selected installer tools and reviewed configuration |
| 3. Install OpenStack | Deployment, prechecks and cloud service/API checks pass |
| 4. First VM | VM access and a volume data test pass; the working setup is reproduced with Terraform |
| 5. Deploy an app | Application works and retains its database data |
| 6. Monitoring | Dashboards show the lab and a test alert works |

## How we learn

1. Explain the goal of the current step.
2. Explain the script: where it runs, what it changes, what its output means.
3. You download it from GitHub and run the explained command.
4. We review your actual result.
5. Read a short lecture connecting the result to the concept.
6. Mark the checkpoint complete and continue.

Each phase has its own guide. Work through one chapter at a time.

## Starting point

You are rebuilding from zero and reinstalling R2d2. This course carries no completed checkpoints from the previous repository.

The new NAT address, hypervisor, LAN DHCP server, gateway/DNS, current NIC identities and current disk state still need observation. Phase 1 gathers that information before exact network or storage changes are prescribed.
