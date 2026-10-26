# Chapter E — Configure the lab

[Phase 2](README.md) · [Previous: create the inventory](D-create-inventory.md)

**Goal:** prepare the reviewed settings that Kolla will use during installation.

**Design: three-node HA from the initial configuration.** A successful local preparation report is required; the fresh cloud deployment and failover tests are later checkpoints.

## 1. Purpose

The inventory says **which machines** to use. Kolla's settings say **how to configure the cloud**: network interfaces, services, storage and access addresses.

## 2. Confirmed inputs

| Setting | Confirmed design |
|---|---|
| Host OS | Ubuntu 24.04 on the three nodes |
| Roles | All three nodes: controller, network, compute and local storage |
| API access address | `192.168.0.151` — one shared internal/public VIP |
| Future VM external pool | `192.168.0.152–192.168.0.199` |
| Management interface | `enp0s31f6` on each node |
| node1 external interface | `enx00e04c557e58` |
| node2 external interface | `enx00e04c2c1678` |
| node3 external interface | `enx00e04c304688` |
| Physical external network | Same untagged LAN through the mini switch and TP-Link router |
| Cinder storage | Separate existing `cinder-volumes` VG on each SATA disk |
| Cinder clustering | Independent backends, empty cluster name; the specific cluster-check exception is approved |

Joe confirmed the VIP and VM pool are free on 2026-10-23. Keep them reserved for this lab. The home DHCP pool is `.2–.150`; nodes use `.200–.202`. This step records the plan; it does not configure router reservations, assign the VIP or create a VM network. A conflict check must still precede deployment.

## 3. Planned steps

1. Review the remaining address and service choices with Joe.
2. Prepare release-compatible inventory host variables and Kolla settings using the confirmed inputs.
3. Explain each setting and any private files that will be created.
4. Preserve existing configuration; show changes before applying them on R2d2.
5. Generate required service credentials privately, following the installed release's procedure.
6. Review the configuration report. Keep passwords, private keys and cloud credentials out of GitHub and shared reports.

## 4. Script and commands

Read [phase2-configure.py](../../scripts/phase2-configure.py). Run it **on R2d2 as joe**. It prepares local files only.

### A. Understand the settings

| Choice | What it means |
|---|---|
| Ubuntu images, OpenStack 2026.2 | Match the selected release and node operating system |
| Management: `enp0s31f6` | Keep the built-in NIC as the management/API path |
| Per-node USB NICs | Store each confirmed USB name in inventory host variables for later external VM traffic |
| Open vSwitch | Retain Kolla 23.0.0's default network implementation |
| API HA, database cluster, router HA capability | Prepare Keepalived/HAProxy, MariaDB and Neutron agent redundancy |
| Core services + Horizon | Prepare login, images, networking, VMs and the web dashboard |
| Cinder LVM | Use the existing independent `cinder-volumes` pools |
| Heat, Cinder backups, monitoring off | Keep the initial baseline; Terraform and monitoring come in later chapters |
| HTTPS on the shared VIP | Prepare encrypted dashboard/API entry points for `192.168.0.151` |

The HA settings are explicit:

```yaml
enable_haproxy: true
enable_keepalived: true
enable_mariadb: true
enable_neutron_agent_ha: true
enable_neutron_dvr: false
max_l3_agents_per_router: 3
dhcp_agents_per_network: 2
```

The three-node inventory supplies all three MariaDB, load-balancer and network hosts. [Phase 3 C](../phase-03/C-deploy-openstack.md) deploys and checks these services. [Phase 4 B](../phase-04/B-create-networks.md) creates the virtual router with HA enabled and verifies its replicas.

The independent storage settings are:

```yaml
cinder_cluster_name: ""
cinder_cluster_skip_precheck: true
```

That is only the agreed cluster-name-check exception for independent LVM backends. Other prechecks remain enabled. It does not replicate disks or create shared storage.

### B. Understand the private files

Everything below is on **R2d2**, under `~/openstack-from-zero/private/phase2/`:

| File or folder | Purpose |
|---|---|
| `kolla/globals.yml` | Kolla's cloud settings |
| `host_vars/node1.yml`, `node2.yml`, `node3.yml` | Each node's external USB NIC name |
| `kolla/lab-network-plan.json` | Record the reserved VIP and future VM address pool |
| `kolla/passwords.yml` | Generated service passwords and internal service keys |
| `kolla/certificates/` | Private lab CA, API certificate, ProxySQL TLS files and private keys |

The configuration stays in the project's private folder rather than replacing an unknown `/etc/kolla` setup on R2d2. Later Kolla commands must use this configuration directory explicitly with `--configdir`. Service configuration on the nodes is created only during later deployment.

Existing, matching files are reused. An unexpected configuration folder or conflicting settings file is preserved and stops the script. Existing generated passwords and certificates are reused; a rerun does not rotate them.

### C. Understand passwords and HTTPS

**No sudo, node password or SSH-key passphrase is requested.** The script uses the tools already installed in Chapter C.

Kolla's `kolla-genpwd` generates OpenStack's service credentials automatically. These are separate from joe's Ubuntu password and SSH key. Their values are not printed in the report. Do not paste `passwords.yml` or private keys into chat or GitHub.

A **certificate authority (CA)** is a certificate issuer. This script creates a private lab CA and uses it to sign the API certificate. The certificate includes the VIP as an IP address, so no invented DNS name is needed.

- `certificates/ca/lab-root.crt` is the **public CA certificate** that clients will need to trust.
- `certificates/private/`, the `haproxy*.pem` files and `certificates/proxysql-key.pem` contain **private keys** and must stay private.
- The API certificate lasts one year; the CA lasts ten years. Renewal is not automatic.
- HTTPS covers client access to the API load balancer. With internal API TLS and ProxySQL enabled, this release also enables database client-to-ProxySQL TLS by default. API backend TLS and ProxySQL-to-MariaDB backend TLS remain disabled under the existing backend setting; this does not claim every internal connection is encrypted.

The same internal/external VIP lets ProxySQL reuse the verified server identity, as Kolla's certificate-generation role does. The script prepares these additional files in `kolla/certificates/`:

| Required ProxySQL filename | Existing source copied privately |
|---|---|
| `proxysql-ca.pem` | `ca/lab-root.crt` |
| `proxysql-cert.pem` | `server.crt` |
| `proxysql-key.pem` | `private/server.key` |

These are copies of the existing verified files, not a new CA or a new key pair. Conflicting existing destinations are preserved and stop preparation. Matching files are reused with private permissions.

For an existing lab whose API certificates are already prepared, [Chapter B's local certificate step](../phase-03/B-full-prechecks.md#local-database-tls-files-on-r2d2) completes this layout without rerunning all of Chapter E.

The script verifies the certificate signature, key matches, dates and VIP. **It does not yet install CA trust on R2d2 or your browser computer.** Those trust steps must be completed before HTTPS use/deployment checks. A private lab CA is not automatically trusted by browsers.

Planned API access after deployment:

```mermaid
flowchart TB
  CA["Private lab CA"] -. "public certificate trusted" .-> Client["Browser or CLI"]
  CA -. "signs certificate" .-> VIP["API VIP: 192.168.0.151"]
  Client -->|HTTPS| VIP
  VIP -->|"HTTP API backends"| Services["Controller API services"]
```

### D. Download and run

These commands enter the repository, download Chapter E and create the explained private files:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase2-configure.py --apply
```

The launcher selects the installed Kolla Python environment automatically. Your terminal can already show `(kolla)`; no extra activation or package installation is needed.

Without `--apply`, the script prints the nonsecret plan and creates no files. You do not need to run both modes.

The script checks the three-node inventory before writing files, generates credentials and certificates privately, then checks that Ansible reads the correct USB NIC for each node. It assigns no addresses, makes no node connections and deploys no services.

## 5. Finish check and lesson

Expected final marker:

```text
RESULT: LAB_CONFIG_PREPARED
```

Use the exact `cat` command printed at the end and send back that report. **Stop before Chapter F.** If the marker is `LAB_CONFIG_NEEDS_REVIEW`, send the report before making further changes. Library error details that might contain credentials are deliberately withheld.

The successful marker means local settings, credential population, certificate checks and inventory NIC mappings passed. It does not mean the VIP is active or HTTPS is available.

Before publication, six regression tests passed. Real Kolla 23.0.0 credential generation, real Ansible 2.21.5 inventory parsing, Kolla playbook syntax checking and OpenSSL certificate-chain/IP verification also passed in an isolated development folder. A second run preserved the generated passwords and keys. No live node or deployed HTTPS test was performed.

