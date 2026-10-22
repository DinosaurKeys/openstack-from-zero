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

**Current checkpoint:** Ubuntu, hostnames, management IPs and SSH login passed on all three nodes. Continue with [1.4 — Prepare the NICs and Cinder storage](docs/phase-01/04-nics-and-storage.md). The reviewed device map is there; USB cabling and the LAN DHCP device/pool are the next inputs.
