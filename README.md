# OpenStack from zero

Build a cloud you understand. Deploy an application. Monitor it.

**Start here: [Phase 1 — Prerequisites](docs/phase-01/README.md).**

![Planned lab with R2d2 and three OpenStack nodes](docs/images/lab-layout.svg)

| Phase | Goal | Guide |
|---|---|---|
| **1. Prerequisites** | Ubuntu, management access, NIC and disk identification | **[Read Phase 1](docs/phase-01/README.md)** |
| 2. Management environment | Prepare Ansible, Kolla-Ansible and their configuration on R2d2 | Planned |
| 3. Install OpenStack | Deploy and check cloud services | Planned |
| 4. First VM | Test VM networking and volumes, then introduce Terraform | Planned |
| 5. Deploy an app | PostgreSQL, Flask and Nginx | Planned |
| 6. Monitoring | Prometheus, Grafana and alerts | Planned |

[Goal and learning method](docs/00-goal-and-plan.md)

Each step: **purpose → explain the script → run → review the result → short lecture**.

**Current checkpoint:** SSH-key setup passed on all three nodes with fresh key-only logins. Read **[Chapter E — the successful result and short lesson](docs/phase-01/03b-ssh-keys.md#7-reviewed-result)**. The complete setup steps remain visible. **[Chapter F — node names on R2d2](docs/phase-01/03c-r2d2-hosts.md#5-reviewed-result)** also passed: all three names resolve correctly. Its result and short lesson are published. **[Chapter G — Chrony on R2d2](docs/phase-01/03d-r2d2-chrony.md#6-reviewed-result)** is also complete: active, enabled and initially synchronized. Its result and short lesson are published. **[Chapter I — SATA inspection result and lesson](docs/phase-01/05-cinder-storage.md#5-reviewed-inspection-result):** collection and review completed on all three nodes. **[Chapter J — LVM pools created and verified](docs/phase-01/06-cinder-lvm.md#6-reviewed-creation-result)** on all three nodes: one SATA PV and zero LVs per pool; Ubuntu/system LVM unchanged. The setup commands, reviewed result, short lesson and read-only viewing command remain available. Phase 2 has not started.
