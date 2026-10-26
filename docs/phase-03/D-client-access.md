# Chapter D — Client access and API verification

[Phase 3](README.md) · [Previous: deploy OpenStack](C-deploy-openstack.md)

**Status: complete — live HTTPS/API checks passed, and Joe's screenshot confirms a successful admin browser login.** Chapter C's live observations passed on all three nodes.

## 1. Purpose

Prove that R2d2 can log in to OpenStack over verified HTTPS and read the service APIs. Then sign in to Horizon from your browser.

The last chapter checked the machinery: containers, database cluster, VIP and bridges. This chapter checks the front door: authentication, API addresses and the dashboard.

## 2. What changes

Run on **R2d2 as joe**. The script reuses the existing private inventory, globals, passwords and lab CA under `~/openstack-from-zero/private/phase2/`.

Kolla's standard `post-deploy` command generates these private files in `private/phase2/kolla/` on **R2d2 only**:

| File | Purpose |
|---|---|
| `clouds.yaml` | Connection profiles containing the API address, existing admin credentials, region and CA path |
| `admin-openrc.sh`, `admin-openrc-system.sh` | Shell settings for the internal project/system admin connection |
| `public-openrc.sh`, `public-openrc-system.sh` | Shell settings for the public project/system admin connection |

The files have mode `0600`, so only joe can read/write them. Repeating the command refreshes these generated files from the existing configuration. They contain passwords: keep them private and out of Git.

The script also saves one private report. It checks that the existing deployment inputs stay unchanged. No software installation, node sudo prompt, service restart, certificate rotation or cloud-resource creation is part of this chapter. Authentication creates a normal short-lived token held in memory; its value is never printed in the report.

No OpenStack CLI installation is needed for these checks: the script uses Python's HTTPS client with the prepared credentials and CA. The generated profiles can be used by OpenStack clients later.

## 3. What the script checks

1. Validate the existing local configuration, certificates and pinned Kolla source.
2. Run Kolla `post-deploy` limited to `localhost` to prepare client files.
3. Validate the live API certificate against the existing lab CA and VIP address while requesting an admin project token from Keystone.
4. Read Keystone's service catalog and registrations. Require enabled public endpoints for Identity, Glance, Nova, Neutron, Cinder and Placement in the configured region. Every selected endpoint must use HTTPS on `192.168.0.151`.
5. Use the token for read-only API requests: images, servers, networks, volumes and Placement resource providers. Empty lists are valid; this chapter creates none of those resources. These reads prove API access, not successful scheduling, image upload or volume attachment.
6. Request Horizon over verified HTTPS and check that its login form is present. **This is not a browser login.** Complete that separately below.

The Cinder service type in the pinned release is `block-storage`. Glance uses node1's local file storage; this chapter does not change its placement or storage.

## 4. Run — on R2d2 as joe

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-access.py --run
```

The script selects the existing `/home/joe/venvs/kolla` environment. No node sudo passwords or SSH connections are needed. Do not enter passwords into chat.

Without `--run`, it prints its plan and performs no checks or file creation.

## 5. Return the API result

When the command returns, use the exact `cat` command printed at the end and send that report. It is compact and does not include passwords, bearer tokens, response bodies or credential-file contents.

Expected marker:

```text
RESULT: OPENSTACK_API_ACCESS_PASSED
```

If it reports `OPENSTACK_API_ACCESS_NEEDS_REVIEW`, send the report before retrying or changing settings. The script performs no automatic repair.

## 6. Sign in to Horizon — in your chosen browser

After the API checks pass:

1. On R2d2, display your existing login details privately:

   ```bash
   /usr/bin/python3 scripts/phase3-access.py --show-login
   ```

   This reads the generated profile and displays the username, domain and password **only in your terminal**. It creates no report and changes no credentials. Do not paste this output into chat or GitHub.

2. In the Windows Chrome or Linux Firefox browser where you previously imported the lab CA, open **https://192.168.0.151/**.
3. Check that the browser accepts the HTTPS certificate. If it shows a certificate warning, stop and report the warning text; do not bypass it.
4. Use the displayed username and password. Enter the displayed domain if the login form asks for it. Click **Sign In**.
5. Confirm that the dashboard loads. Send only **“Horizon login works”**, or the visible error text without credentials.

The scripted API pass and your browser login confirmation are two separate finish checks.

## 7. Finish check and short lesson

- [x] Chapter C's live host/container/network observations reviewed: passed on all three nodes.
- [x] Chapter D's actual HTTPS/API report reviewed.
- [x] Horizon HTTPS certificate and login-page checks passed from R2d2.
- [x] Joe's screenshot confirms the admin session and loaded dashboard at the API VIP.

Joe's `phase3-access-20261026T141651Z-f89_jnuf.txt` reports `OPENSTACK_API_ACCESS_PASSED` with no issues. Keystone authentication, all six required public service registrations, authenticated reads from Glance/Nova/Neutron/Cinder/Placement, and Horizon's verified HTTPS login page passed. Browser sign-in was confirmed separately by Joe's admin dashboard screenshot. The script's browser-login field is intentionally not an automated browser sign-in assertion.

The reviewed public endpoints use VIP `192.168.0.151`: Keystone 5000, Glance 9292, Nova 8774/v2.1, Neutron 9696, Cinder 8776/v3 and Placement 8780, all over HTTPS. VM, volume, virtual-network creation and failover remain untested.

Short lesson: **Keystone checks who you are; its catalog gives clients the service addresses; the token authorizes later API requests; the CA verifies the server. Horizon is a browser client of those APIs.**

## 8. USB virtual networking — Phase 4

The physical USB NICs are already connected to `br-ex` and have no host IP addresses. The API VIP and node management addresses use the built-in NICs.

**Phase 4 will document and test the actual virtual networking on GitHub**, before Joe runs the configuration steps. That guide will explain the external/provider network, tenant network, subnet, virtual router and floating IP, followed by the observed packet path from the VM through OpenStack and `br-ex` to the USB NIC, physical switch and home network. It will show which addresses belong to hosts, router ports and VMs. Names, interfaces and allocations will be checked against the live configuration rather than guessed.

The approved proposed VM external allocation range remains `.152–.199`; this chapter does not allocate those addresses or create a network. VM traffic, VM creation, Cinder volume use and failover remain later tests.

## Local validation and limits

Local tests cover command scope, credential privacy, TLS failures, endpoint selection, response validation, redirects and unchanged deployment inputs. The pinned Kolla CLI task selection and credential templates are checked with an isolated inventory and temporary credentials. In-memory TLS handshakes test trusted certificates and certificate rejection. Full post-deploy execution on R2d2, real API responses and browser login remain live checks.

[Kolla quickstart and post-deploy](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart.html) · [Identity API](https://docs.openstack.org/api-ref/identity/v3/) · [Compute API](https://docs.openstack.org/api-ref/compute/) · [Block Storage API](https://docs.openstack.org/api-ref/block-storage/v3/)
