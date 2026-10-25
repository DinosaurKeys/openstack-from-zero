# Chapter A — Host changes explained

[Run Chapter A](A-prepare-nodes.md) · [Phase 3](README.md)

**Scope:** node1, node2 and node3. Joe approved standard Kolla preparation, including disabling UFW if installed and removing the listed old host packages if present. The wrapper calls the existing Kolla bootstrap playbook; it does not replace its installation tasks.

## Packages

| Requested action | Explicit package names | Purpose |
|---|---|---|
| Ensure installed | `git`, `iputils-ping`, `tzdata` | Source downloads, ping and timezone data |
| Ensure installed | `ca-certificates`, `gnupg` | Verify HTTPS downloads and the Docker repository |
| Ensure installed | `docker-ce`, `apparmor` | Container engine and application-security support |
| Ensure installed on the checked system-Python path | `python3-setuptools`, `python3-pip`, `python3-docker`, `python3-dbus` | Let Ansible/Python manage Docker and related system functions |
| Ensure absent | `lxd`, `lxc`, `open-iscsi`, `libvirt-bin` | Use the selected Kolla container-based deployment path rather than these listed host packages |

The wrapper explicitly selects the libvirt-container path, so `libvirt-bin` is included in the removal list. Multipath remains disabled; this path does not request `sg3-utils-udev`.

APT can install additional dependencies, including Docker's supporting components. Package versions come from the configured repositories and are recorded after installation; this is not a complete package-version lock. Missing packages are installed; already satisfied requirements need not be reinstalled.

The node check confirms that Ubuntu's system Python is externally managed before proceeding. This selects Kolla's Ubuntu package route for the Docker SDK, without creating a new node Python environment or installing SDK packages globally with pip. R2d2's existing Kolla environment is reused separately.

## Files and services

| Area | What the reviewed tasks can change |
|---|---|
| Docker repository | Create `/etc/apt/keyrings/docker.asc` and `/etc/apt/sources.list.d/docker.sources`; remove an old Docker `.list` entry and refresh APT metadata. |
| Docker service | Install, configure, start and enable Docker; service restarts may occur during configuration. |
| Docker configuration | Write `/etc/docker/daemon.json`. Kolla disables Docker's default bridge, automatic iptables rules and automatic IP-forwarding configuration so its cloud-network setup can manage networking later. This does not disable the Linux host's entire networking stack. |
| Containerd | If the engine uses the containerd image store, Kolla can write `/etc/containerd/config.toml` and restart containerd. |
| Firewall | Disable UFW and set its default policy to allow if UFW is installed. This is the explicitly approved standard-bootstrap choice. |
| Node names | Ensure the localhost entry, remove a node's own `127.0.1.1` mapping if present, and add a managed block for the three nodes in `/etc/hosts`. Existing other lines remain. |
| Cloud-init hosts handling | Where cloud-init is installed, write `/etc/cloud/cloud.cfg.d/99-kolla.cfg` with `manage_etc_hosts: false`, so it does not replace the host-name entries later. This does not disable cloud-init networking. |
| Libvirt AppArmor profile | On Ubuntu, unload and mark the host libvirtd profile disabled if that profile exists and is not already disabled. It does not disable AppArmor as a whole. |
| Kolla host directories | Ensure `/etc/kolla` on each node and write `/etc/systemd/system/kolla.target`. This node directory is separate from the private configuration source on R2d2. |
| Time, SSH and account | This reviewed Ubuntu path does not replace Chrony or change SSH/sudo policy. The wrapper selects `create_kolla_user: false`, retaining `joe`. It compares recorded Chrony, Netplan and SSH configuration hashes and management DNS before/after. |
| Journal | The inspected role's journal configuration block is conditional on RedHat; it does not configure the journal on these Ubuntu nodes. |

Docker proxy/registry options remain the reviewed defaults; no private proxy credentials or custom registry CA are added by this chapter.

## What the wrapper checks

1. Agreed tool versions, unchanged private inventory/globals/host variables, complete credentials and the existing reviewed CA.
2. Installed bootstrap source hashes match the reviewed source set. A different collection/task version stops for review before bootstrap.
3. All three node identities, sudo access, NICs, clock synchronization and empty Cinder pools still pass the Phase 2 checks.
4. No recorded Docker/LXD containers, libvirt VM definitions or iSCSI sessions are present. An occupied host stops for review rather than assuming it is disposable.
5. After bootstrap: Docker active/enabled, Python SDK can contact the engine, expected packages/name mappings/configuration present, listed removals absent, and protected configuration observations unchanged.

The existing SATA PVs/VGs are checked, not recreated. No OpenStack API VIP, VM network or guest is created. Hardware acceleration and full deployment requirements remain checks in the later chapters.

## Inputs and execution

The source configuration remains on R2d2 in `private/phase2/kolla`. The wrapper writes a private run-specific options file and temporary check playbooks under `reports/`. Explicit bootstrap options make the reviewed choices visible without editing `globals.yml`.

It runs Kolla with `--configdir`, the existing inventory and `--limit baremetal --become`. This selects the three nodes and excludes R2d2 from host preparation. Fact gathering is confined to those three nodes.

Each node's sudo password is requested once, held in memory and supplied through the child process environment. The private options file contains only a lookup expression, not password values. Passwords are not command arguments, transcript content or saved files. SSH-key unlocking may request your existing key passphrase separately.

[Official deployment order](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart) · [Collection bootstrap roles](https://github.com/openstack/ansible-collection-kolla/tree/stable/2026.2/roles)
