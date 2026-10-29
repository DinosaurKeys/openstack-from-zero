# Chapter D — OpenStack CLI, HTTPS and Horizon access

[Phase 3](README.md) · [Previous: deploy OpenStack](C-deploy-openstack.md)

**Goal:** finish the installation with a working `openstack` command and browser access. Complete Chapter C's deployment/database/VIP checks first. Run terminal commands **on R2d2 as joe**.

## 1. Install or verify the CLI

The OpenStack CLI is included in [Phase 2 C](../phase-02/C-install-tools.md). This command installs it if missing and verifies an existing matching installation:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase2-install-tools.py --client-only --apply
```

Expected: `RESULT: OPENSTACK_CLI_READY`. The client is installed in `/home/joe/venvs/openstack-client`; this command leaves the existing Kolla/Ansible installation unchanged.

## 2. Prepare connection settings and verify the APIs

```bash
/usr/bin/python3 scripts/phase3-access.py --run
```

This runs standard Kolla `post-deploy` on R2d2 only. It prepares private client files from the existing credentials and lab CA, then checks HTTPS authentication, service registration, API reads and Horizon's login page.

| Private file under `private/phase2/kolla/` | Purpose |
|---|---|
| `public-openrc.sh` | Connection settings for the CLI using the public HTTPS endpoints |
| `clouds.yaml` | Connection profiles used by the course helpers |
| Other generated `*-openrc*.sh` files | Internal or system-scoped admin connections |

These files contain credentials and are restricted to joe. Keep them private. The script's report excludes passwords and token values. This step creates no VM, network or volume and restarts no node services.

Expected:

```text
RESULT: OPENSTACK_API_ACCESS_PASSED
```

Read the private report using the printed `cat` command. It must confirm HTTPS access to Identity, Images, Compute, Network, Volumes and Placement. If it needs review, return the report before continuing.

## 3. Activate the CLI and connect

```bash
source "$HOME/venvs/openstack-client/bin/activate"
source "$HOME/openstack-from-zero/private/phase2/kolla/public-openrc.sh"
openstack --version
openstack token issue -f value -c expires
```

The first command selects the installed CLI. The second loads the existing API address, admin credentials, region and CA certificate. The final command checks authentication and prints **only the token's expiry time**.

In each new terminal, repeat the two `source` commands. Do not print or share the connection file or use `--insecure`.

## 4. View VMs and services

List VMs across projects, including their compute hosts:

```bash
openstack server list --all-projects --long \
  -c ID -c Name -c Status -c Host
```

An empty list is valid before creating the first VM. `Host` is the node running the VM; a data volume can be stored on a different node. All-project/host visibility uses the prepared admin account's permissions.

For service and agent lists:

```bash
openstack compute service list
openstack network agent list
openstack volume service list
```

These are viewing commands. Later, use the [CLI VM reference](../openstack-cli.md#4-start-or-shut-down-a-vm) for normal start/shutdown commands after checking the selected VM's state.

## 5. Sign in to Horizon

1. Display your existing login details privately on R2d2:

   ```bash
   /usr/bin/python3 scripts/phase3-access.py --show-login
   ```

2. In the browser where you imported the lab CA, open **https://192.168.0.151/**.
3. Confirm that the browser accepts the certificate. Resolve any certificate warning before signing in.
4. Enter the displayed username/password and domain if requested, then click **Sign In**.
5. Confirm that the dashboard loads. Return only **“Horizon login works”** or the visible error text without credentials.

The scripted login-page check and a successful browser sign-in are separate checks.

## 6. Finish check

- [ ] CLI version is `openstack 10.3.1`.
- [ ] API access report passed.
- [ ] CLI authentication and VM list succeeded.
- [ ] Horizon opened over trusted HTTPS and browser sign-in succeeded.

This completes client access. VM boot, guest traffic, Cinder persistence and controlled HA failover have their own tests. Glance remains on node1's local storage; client setup does not change service placement.

**Next:** [Chapter E — HA verification](E-ha-verification.md), then [Phase 4 — networking and first VM](../phase-04/README.md). Phase 4 explains and tests the external USB NIC → `br-ex` → virtual router → VM connection. Node management and API VIP traffic use the built-in NICs.

[CLI reference](../openstack-cli.md) · [Kolla post-deploy](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart.html) · [OpenStack CLI configuration](https://docs.openstack.org/python-openstackclient/latest/configuration/index.html)
