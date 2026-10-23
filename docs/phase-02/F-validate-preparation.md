# Chapter F — Validate preparation

[Phase 2](README.md) · [Previous: configure the lab](E-configure-lab.md)

**Goal:** verify the management environment and configuration before starting installation.

**Status: draft.** Validation commands depend on the preceding chapters' actual tools and files.

## 1. Purpose

This checkpoint connects the earlier results: the selected tools, correct inventory, working access and reviewed cloud settings must agree.

## 2. Planned steps

1. Verify the selected tool versions from the agreed environment.
2. Parse the inventory and check the complete host/group placement.
3. Validate configuration syntax and inspect the relevant generated settings without exposing credentials.
4. Confirm Ansible access and required sudo access on all three nodes.
5. Check that per-node NIC settings and LVM group names match our recorded results.
6. Confirm the approved independent-LVM cluster-check exception is the only intended precheck exception.
7. Check the reserved VIP for active conflicts from the LAN and verify hardware virtualization before selecting the deployment procedure.
8. Explain and complete the public CA trust steps on R2d2 and Joe's browser computer before HTTPS deployment/use. Ask which browser computer/OS will be used; do not assume it or disable certificate verification.
9. Save one report, review it, then ask Joe before starting Phase 3.

## 3. Script and commands

The validation script will be published after the exact environment and configuration files exist. It will not deploy services or run Kolla bootstrap.

Full Kolla prechecks run **in Phase 3 after the required host dependencies are prepared**. A local configuration check here is not a claim that full deployment prechecks have passed.

## 4. Finish check

The review confirms that the management environment and configuration are ready for the agreed installation procedure. OpenStack installation and VM/volume tests remain later checkpoints.

## 5. Short lecture — after review

The lesson will explain what this validation proves and what Phase 3 still needs to test.
