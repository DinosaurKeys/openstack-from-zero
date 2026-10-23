# Chapter A — Prepare the nodes

[Phase 3](README.md) · [Previous phase](../phase-02/README.md)

**Status: draft for review. No script or command to run yet.**

## 1. Purpose

OpenStack services will run in containers. First, the hosts need the container engine and the supporting packages/settings expected by our selected Kolla release.

Kolla calls this preparation **bootstrap**. It prepares hosts; deploying OpenStack services is a later step.

## 2. What is already complete

- R2d2 has the agreed Kolla-Ansible 23.0.0 and Ansible-core 2.21.5 environment.
- The private inventory assigns node1, node2 and node3 as controller, network, compute and storage hosts.
- Chapter F's live identity, NIC, clock, storage and local configuration checks passed.
- Each existing Cinder pool has one confirmed SATA PV and zero LVs.
- R2d2 CA trust passed. Windows Chrome and Linux Firefox imports are user-confirmed; live Horizon HTTPS remains a deployment-stage check.

## 3. Proposed steps

1. Review the **installed, pinned release's** bootstrap tasks and effective settings.
2. Publish a short list of the exact packages, services and configuration files it would change on our three nodes. Include any effects on hosts-file entries, time synchronization, firewall or access settings before asking Joe to run it.
3. Resolve any new choices with Joe. Keep the existing roles, NIC selection, storage pools and approved Cinder cluster-check exception.
4. Prepare and test one wrapper using the existing private inventory and configuration directory.
5. Publish the finished guide and script, with passwords/prompts and changes explained.
6. After agreement on that concrete scope, Joe pulls and runs it on R2d2.
7. Review one private combined report before moving to Chapter B.

The selected release and public quick-start establish the order: host bootstrap, full prechecks, then service deployment. The public quick-start uses example paths; our eventual wrapper must use the already prepared lab paths.

## 4. Script explanation — to be completed before publication as runnable

The eventual wrapper will run from **R2d2 as joe** and target **node1, node2 and node3** through the existing Ansible inventory.

This is a **configuration-changing step**. The exact bootstrap changes and their validation must be reviewed before the runnable script is published. We have not approved guessed firewall, time-service, SSH or network changes. Package/service changes can affect running workloads; this remains an undeployed lab, but current host state still needs checking.

No disk erasure, PV/VG recreation, credential regeneration or certificate rotation is part of this plan.

## 5. Download and run — pending

There are no Chapter A execution commands yet. A draft is not a request to run raw Kolla commands copied from external documentation.

The finished version will include the explained script, exact command and private report location. No bootstrap, full Kolla prechecks or deployment has run under this draft.

## 6. Finish check — future result

- [ ] Exact bootstrap changes reviewed and agreed.
- [ ] Wrapper tested and published.
- [ ] Joe's run completed on all three nodes.
- [ ] Required host services and existing management access verified.
- [ ] Combined report reviewed before Chapter B.

## 7. Short lecture — after result review

After the actual run, explain which host components were prepared and why they are needed for OpenStack containers. Preserve the commands so this chapter can be followed on a rebuild.

[Official Kolla 2026.2 quick-start — deployment order](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart)


## Next review checkpoint

The installed development copy of the `openstack.kolla` collection's `baremetal` role was inspected. It imports host-file and journal configuration, package and container-engine roles, SDK support and libvirt/AppArmor preparation. It also contains conditional firewall changes. This confirms that bootstrap needs an explained change review; it is not merely installing one application.

This is source inspection of the development copy, not proof of the exact current node state or a completed review of every effective option on R2d2. The next implementation must check the collection used by the existing lab environment and publish the concrete changes before Joe runs it.

**Proposed next deliverable:** one tested Chapter A wrapper and a simple guide for preparing all three nodes from R2d2, with one private combined report. Ask Joe before moving from this draft to that configuration-changing procedure. Full prechecks and deployment stay in later chapters.
