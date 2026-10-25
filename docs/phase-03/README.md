# Phase 3 — Install OpenStack

[Home](../../README.md) · [Phase 2](../phase-02/README.md)

**Current: Chapter A is complete and its live report is reviewed.** Joe approved standard host preparation. Chapters B–D remain planned.

**Goal:** prepare the three hosts, check deployment requirements, install OpenStack and verify access.

| Chapter | Purpose | Status |
|---|---|---|
| [A — Prepare the nodes](A-prepare-nodes.md) | Review and then prepare the host dependencies Kolla needs | Complete — all three nodes passed |
| B — Full Kolla prechecks | Check the prepared hosts before deploying services | Planned |
| C — Deploy OpenStack | Deploy the agreed services using the existing private configuration | Planned |
| D — Verify cloud access | Check services, live HTTPS and the Horizon login page | Planned |

Each chapter follows **purpose → explain the script → publish → Joe pulls and runs → review one report → short lecture**. Letters restart at A in this phase.

The selected versions, three-node roles, NIC mappings, independent Cinder LVM pools and HTTPS plan stay as agreed in Phase 2. We do not recreate the disks, passwords or certificates.

**Current action:** Phase 2 and the chosen Windows Chrome/Linux Firefox imports are complete (browser imports user-confirmed). Read [Chapter A's reviewed result and short lesson](A-prepare-nodes.md#7-reviewed-live-result). The original wrapper explanation, host changes and run command remain available. Next is preparing Chapter B's full-precheck guide and wrapper; no Chapter B command has been published or executed yet.
