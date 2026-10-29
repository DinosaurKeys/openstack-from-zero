# Phase 2 — Management environment and HA configuration

[Home](../../README.md) · [Phase 1](../phase-01/README.md) · [Architecture](../architecture.md)

R2d2 runs the pinned installer tools and stores private inventory, credentials and TLS material. All three nodes are controller/network/compute/storage hosts.

| Chapter | Purpose |
|---|---|
| [A — Check R2d2](A-check-r2d2.md) | Confirm the management machine and connectivity |
| [B — Select versions](B-select-versions.md) | Kolla-Ansible 23.0.0, OpenStack 2026.2 and Ansible-core 2.21.5 |
| [C — Install tools](C-install-tools.md) | Install Kolla, Ansible and the OpenStack CLI |
| [D — Create inventory](D-create-inventory.md) | Map all three roles and node access |
| [E — Configure the lab](E-configure-lab.md) | Explicit API HA, MariaDB and Neutron agent HA; credentials and HTTPS |
| [F — Validate preparation](F-validate-preparation.md) | Readiness, inventory, source, local TLS and VIP conflict checks |

The explicit HA settings are prepared in E and deployed in **Phase 3 C**. The actual HA virtual router is created in **Phase 4 B**. Private files are never published. Existing matching credentials/certificates are reused; unknown or conflicting settings stop for review.

For this lab's existing prepared hosts, start with [Fresh installation](../fresh-install.md). That route retains the installed management tools, trusted CA and reviewed inventory instead of reinstalling prerequisites or rotating secrets. The fresh HA configuration/deployment must produce its own reports; previous host preparation does not prove a fresh cloud works.

[Windows certificate guide](F-browser-certificates.md) · [Linux Firefox guide](F-linux-firefox.md) · [Next: install OpenStack](../phase-03/README.md)
