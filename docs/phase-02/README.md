# Phase 2 — Management environment

[Home](../../README.md) · [Completed Phase 1](../phase-01/README.md) · [Goal and plan](../00-goal-and-plan.md)

**Goal:** prepare R2d2 with the tools and reviewed configuration needed to install OpenStack in Phase 3.

**Current: Chapter D access checks passed; update the compute group to all three nodes.** Chapters A–C are reviewed/completed. Chapters E–F remain drafts of the planned sequence. We review one actual result before making the next configuration change.

![R2d2 manages the three-node lab; installer tools and cloud services are planned](../images/lab-layout.svg)

## Chapters and progress

| Chapter | Purpose | Status |
|---|---|---|
| [Chapter A — Check R2d2](A-check-r2d2.md) | Collect current resources, Python and tool/package information | **Complete — result and short lesson published** |
| [Chapter B — Select compatible versions](B-select-versions.md) | Agree on the OpenStack, Kolla-Ansible and Ansible versions | **Complete — versions and environment path agreed** |
| [Chapter C — Install management tools](C-install-tools.md) | Build an isolated Python environment on R2d2 | **Complete — installation and activation confirmed** |
| [Chapter D — Create the inventory](D-create-inventory.md) | List machines, roles and connection settings | **Access passed — three-compute inventory update ready** |
| [Chapter E — Configure the lab](E-configure-lab.md) | Prepare network, storage and service settings | Draft — remaining values need confirmation |
| [Chapter F — Validate preparation](F-validate-preparation.md) | Review access, inventory and configuration before installation | Draft — previous chapters must pass |

## How this phase works

Each executable chapter keeps the same structure: **purpose → explain the script → download and run → review the result → short lecture**. Completed instructions stay visible for study and rebuilding.

The script is checked locally before publication. Local tests use simulated machine/package data; they cannot prove R2d2's actual state or network access. Your report supplies that evidence. Working procedures go in the guide; detailed execution evidence stays in private reports.

Read [Chapter A's reviewed result and short lesson](A-check-r2d2.md#5-reviewed-result), and [Chapter B's agreed version set](B-select-versions.md), read [Chapter C's completed lesson](C-install-tools.md#5-short-lecture--after-review), then [run Chapter D](D-create-inventory.md#download-and-run). The Chapter A commands remain available for a rebuild. Draft chapters explain the upcoming work without asking you to run unfinished commands.

## Phase 2 finish check

- [x] R2d2's current management environment is collected and reviewed.
- [x] Compatible versions are agreed and recorded.
- [x] Management tools are installed in the agreed isolated environment.
- [ ] The inventory assigns all three controller/network/compute/storage hosts correctly.
- [ ] Network, storage and service configuration is reviewed; secrets stay private.
- [ ] Ansible access and configuration validation pass.

**Phase 3 boundary:** installing node dependencies with Kolla bootstrap, running full Kolla prechecks and deploying OpenStack happen in Phase 3. This phase prepares the management environment and configuration.
