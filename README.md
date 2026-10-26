# OpenStack from zero

Build a cloud you understand. Deploy an application. Monitor it.

**Current: [Phase 4 — Chapter B: create the virtual networks](docs/phase-04/B-create-networks.md). Chapter A's live network inspection is complete. Chapter B explains management versus USB traffic and prepares the approved external/private networks and one virtual router. Its guide and tested script are published; live creation is pending.**

**Completed: [Phase 2 — Management environment](docs/phase-02/README.md). [Chapter A is reviewed](docs/phase-02/A-check-r2d2.md#5-reviewed-result); [Chapter B's versions are agreed](docs/phase-02/B-select-versions.md); Chapter C's installation/activation is complete; Chapter D's inventory/access checks are complete; [Chapter E's preparation is reviewed](docs/phase-02/E-configure-lab.md#reviewed-result); [Chapter F's preparation checks passed](docs/phase-02/F-validate-preparation.md#7-reviewed-result); Windows Chrome and Linux Firefox imports are user-confirmed. Phase 3 began with [Chapter A's retained run guide](docs/phase-03/A-prepare-nodes.md). Read [Chapter F — Validate preparation](docs/phase-02/F-validate-preparation.md).**

**Rebuilding from the beginning? [Phase 1 — Prerequisites](docs/phase-01/README.md)** keeps every completed step and lesson.

![Planned lab with R2d2 and three OpenStack nodes](docs/images/lab-layout.svg)

| Phase | Goal | Guide |
|---|---|---|
| **1. Prerequisites** | Ubuntu, management access, NIC and disk identification | **[Read Phase 1](docs/phase-01/README.md)** |
| **2. Management environment** | Prepare Ansible, Kolla-Ansible and their configuration on R2d2 | **[Read Phase 2](docs/phase-02/README.md)** |
| 3. Install OpenStack | Deploy and check cloud services | [Chapter D — client access and API verification](docs/phase-03/D-client-access.md) |
| 4. First VM | Document USB/provider virtual networking, test VM networking and volumes, then introduce Terraform | [Chapter B — create virtual networks](docs/phase-04/B-create-networks.md) |
| 5. Deploy an app | PostgreSQL, Flask and Nginx | Planned |
| 6. Monitoring | Prometheus, Grafana and alerts | Planned |

[Goal and learning method](docs/00-goal-and-plan.md)

Each step: **purpose → explain the script → run → review the result → short lecture**.

**Current checkpoint:** SSH-key setup passed on all three nodes with fresh key-only logins. Read **[Chapter E — the successful result and short lesson](docs/phase-01/E-ssh-keys.md#7-reviewed-result)**. The complete setup steps remain visible. **[Chapter F — node names on R2d2](docs/phase-01/F-r2d2-hosts.md#5-reviewed-result)** also passed: all three names resolve correctly. Its result and short lesson are published. **[Chapter G — Chrony on R2d2](docs/phase-01/G-r2d2-chrony.md#6-reviewed-result)** is also complete: active, enabled and initially synchronized. Its result and short lesson are published. **[Chapter I — SATA inspection result and lesson](docs/phase-01/I-cinder-storage.md#5-reviewed-inspection-result):** collection and review completed on all three nodes. **[Chapter J — LVM pools created and verified](docs/phase-01/J-cinder-lvm.md#6-reviewed-creation-result)** on all three nodes: one SATA PV and zero LVs per pool; Ubuntu/system LVM unchanged. The setup commands, reviewed result, short lesson and read-only viewing command remain available. **Phase 2 is now published:** Chapter A's read-only inspection is complete, with its result and short lesson published. Chapter B's version set and environment path are agreed. Chapter C's installation and activation are confirmed. Chapter D's access checks passed on all nodes. The update to use all three as computes is verified. Chapter E's private configuration, credentials and HTTPS certificate files are prepared and reviewed. Chapter F's live preparation checks passed on all three nodes, and R2d2 CA trust is verified. Its reviewed result and short lesson are published; all run commands remain available. Joe confirmed the Chrome import on Windows 11. [Linux Firefox has a separate guide](docs/phase-02/F-linux-firefox.md); Joe has confirmed completing it. Phase 2 preparation is complete for the chosen browsers. Live Horizon HTTPS remains a later check. [Phase 3 — Chapter A](docs/phase-03/A-prepare-nodes.md) is complete: the Kolla bootstrap and post-checks passed on all three nodes. Its reviewed result and short lesson are published; the full run procedure remains available. Deployment, host/container observations and Chapter D live API/browser access are now complete.

**Latest Phase 3 checkpoint:** Chapter C's live observations passed on all three nodes: containers, synchronized three-member MariaDB cluster, management layout and USB bridges. The API VIP was on node2. Glance uses local image storage on node1. [Chapter D](docs/phase-03/D-client-access.md) now prepares local client files and checks HTTPS, API authentication/service registration and the Horizon login page. Chapter D's live report confirms HTTPS authentication, all required service registrations and authenticated API reads, and Joe's screenshot confirms browser login. Phase 4 will document the virtual networking through `br-ex` and the USB NICs before VM traffic is configured and tested; VM, volume and failover tests remain pending.

**Phase 4:** [Read the two-NIC diagram and network inspection guide](docs/phase-04/A-network-inspection.md). Its live result and short lesson are published. Joe approved preparing [Chapter B](docs/phase-04/B-create-networks.md) with the proposed private `10.10.10.0/24` and external `.152–.199` pool; read the guide before applying. No network-creation result is claimed yet.
