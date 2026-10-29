# Chapter F — Validate preparation

[Phase 2](README.md) · [Previous: configure the lab](E-configure-lab.md)

**Goal:** check that the files on R2d2 still match the three nodes before installation.

**Status: preparation checks passed and reviewed.** R2d2 CA trust is verified. Windows 11 Chrome and Linux Firefox imports are user-confirmed. Phase 2 preparation is complete for those chosen browsers. The complete run procedure stays below for rebuilding. Horizon remains enabled in the plan so Joe can learn through its web dashboard.

## 1. Purpose

We prepared the installer and its instructions. Now we check that those instructions describe the actual machines correctly.

This checkpoint does **not install OpenStack**. Full Kolla prechecks belong in Phase 3, after Kolla bootstrap prepares the host dependencies.

## 2. What the script does

Run [phase2-validate.py](../../scripts/phase2-validate.py) **on R2d2 as joe**. It uses the existing Kolla environment automatically, including when your prompt already shows `(kolla)`.

| Check | Plain explanation |
|---|---|
| Tools and inventory | Correct versions; all three nodes have controller, network, compute and storage roles. |
| Saved configuration | Network, service and per-node USB settings still match Chapter E. Passwords are checked without printing their values. |
| Certificates | The existing CA and server certificate match, are in date and cover `192.168.0.151`. No new keys or certificates are generated. |
| Kolla syntax | The installed playbook can read the configuration. This is a syntax check, not full deployment prechecks. |
| Each node | Confirm hostname, Ubuntu, management IP, sudo access, Chrony and clock synchronization. |
| NICs | Check the expected management route and USB MAC/link, with no host addresses on the USB NIC. |
| Storage | Confirm the recorded SATA identity and the existing `cinder-volumes` pool: one PV, zero LVs. It does not create or format storage. |
| Virtualization | Check CPU virtualization flags and report whether `/dev/kvm` exists. Usable KVM acceleration is checked in Phase 3; a missing device can mean a module has not loaded. |
| Planned VIP | Send three short ARP probes from each node's physical LAN. This asks whether another device is using `.151`; no address is assigned. A silent/offline device cannot be ruled out. |

The only approved Cinder exception remains `cinder_cluster_skip_precheck: true`, with an empty cluster name, for our independent LVM pools. The script rejects changes to the agreed globals instead of disabling other checks.

Ansible copies a temporary helper to each node and runs it through sudo. The helper reads information and sends the explained ARP probes. No node package installation, service changes, NIC configuration or disk writes are performed. Reports and Ansible result files stay private on R2d2.

### The R2d2 certificate-trust option

`--trust-r2d2` makes **one explained local configuration change after the checks pass**: it adds the verified **public lab CA certificate** to R2d2's Ubuntu trust store. This lets tools using that store accept server certificates signed by our lab CA.

It copies only `certificates/ca/lab-root.crt` to `/usr/local/share/ca-certificates/openstack-from-zero-lab.crt`, runs `update-ca-certificates`, then verifies the saved server certificate against that store. An existing different file is preserved and stops the operation. Private keys are not copied. No packages are installed.

Trust on your Windows/Linux browser computer is separate; see [the browser guide](F-browser-certificates.md). A successful offline certificate check is not a live HTTPS test.

## 3. Download and run

**On R2d2, as joe:** the following downloads the published guide/script, runs the checks and trusts the verified public CA on R2d2. Read the explanation above before running it.

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase2-validate.py --run --trust-r2d2
```

To perform the checks without adding CA trust, omit `--trust-r2d2`. Running the script without `--run` only shows its plan.

Prompts you may see:

| Prompt | What to enter |
|---|---|
| SSH-key passphrase | The passphrase you chose in Phase 1. It unlocks the existing key. |
| `BECOME password` for a named node | Joe's sudo password on **that node**. Each node is asked separately. |
| R2d2 `[sudo] password for joe` | Joe's password on **R2d2**, for the optional CA-trust change. |

The script selects the existing environment; there is no need to type another activation path. It reuses the SSH key and does not change the sudo policy.

## 4. Read and return the report

The script prints the **exact** report path and a ready-to-run `cat` command at the end. Run that printed command and return the report.

Expected successful checkpoints:

```text
RESULT: PREPARATION_CHECKS_PASSED
RESULT: R2D2_CA_TRUST_READY (offline certificate verification; browser trust is separate)
```

The second appears only when `--trust-r2d2` was requested. `PREPARATION_NEEDS_REVIEW` means stop and return the report; it is not an instruction to run repairs or deployment.

The report contains observations, not passwords or private keys. Keep the report and everything under `private/` out of GitHub.

### Rebuilding later

A clean Phase 2 E generates a new CA. **Review the public SHA256 fingerprint printed by E before approving it.** On R2d2, enter that exact value once:

```bash
read -r -p "Reviewed CA SHA256 from Chapter E: " lab_ca_sha256
/usr/bin/python3 scripts/phase2-validate.py --run --trust-r2d2 --expected-ca "$lab_ca_sha256"
```

After all preparation checks pass, the script saves that explicit approval in `private/phase2/reviewed-ca.json`. Installation, client access, networking, health checks, shutdown and recovery all use it automatically. Read-only checks do not create or change this file.

For the existing lab, the normal command remains valid without this extra step: its original CA is already pinned. Retain that CA and its private keys. A conflicting saved approval or existing system-trust certificate is preserved and requires a separate reviewed replacement; nothing is overwritten automatically.

## 5. Browser preparation — Windows and Linux

The reviewed browser choices are **Chrome on Windows 11** and **Firefox on Linux**, accessed from outside R2d2. R2d2 itself is currently accessed using the **VMware console**.

Read [F — Browser certificate guide](F-browser-certificates.md). It covers both browsers without assuming a Linux distribution. The Windows 11 host reaches R2d2 at `192.168.162.131:22` through VMnet8; [the Windows transfer steps are now published](F-browser-certificates.md#windows-11-laptop--confirmed-route-ready-to-run-transfer). R2d2's checks passed. Joe reported the Windows verification marker and confirmed the Chrome import. Joe also confirmed completing [the separate Linux Firefox guide](F-linux-firefox.md). Its exact local file path and transfer route were not supplied and are not inferred.

This chapter prepares trust before deployment. [Phase 3 D](../phase-03/D-client-access.md) verifies live HTTPS/API access and explains the separate browser sign-in check.

## 6. Finish check and review

- [x] Review the actual report from this chapter.
- [x] Review the reported virtualization, NIC, storage, clock and VIP observations.
- [x] Verify R2d2 CA trust.
- [x] Public CA copied to Windows; Chrome import confirmed by Joe.
- [x] Linux Firefox setup reported complete by Joe; import user-confirmed.
- [ ] For a rebuild, review this preparation report before Phase 3.

**Development verification:** regression tests cover read-only probes, wrong-node rejection, ARP packet handling, certificate/configuration preservation, CA mismatch, private-file guards and trust destination protection. Real Kolla-Ansible 23.0.0 / Ansible-core 2.21.5 inventory parsing, playbook syntax, ad-hoc argument parsing and OpenSSL certificate verification also passed in an isolated development folder. These development checks are separate from Joe's live results below. Windows Chrome and Linux Firefox imports are user-confirmed. Live HTTPS/API checks belong in Phase 3 D; browser sign-in is a separate confirmation.

## 7. Reviewed result

Joe's report `phase2-validation-20261023T200411Z-e73cu3l0.txt` returned:

```text
RESULT: PREPARATION_CHECKS_PASSED
RESULT: R2D2_CA_TRUST_READY (offline certificate verification; browser trust is separate)
```

| Checkpoint | Observed result |
|---|---|
| Local preparation | Agreed tool versions, three-node roles, globals, USB mappings and credential completeness passed. |
| Certificate | Existing CA fingerprint matched Chapter E; certificate chain, dates, keys and VIP SAN passed. |
| Kolla syntax | Passed. Bootstrap, full Kolla prechecks and deployment did not run. |
| All three nodes | Correct identity, Ubuntu 24.04, management address/route and sudo access. Chrony active and clocks reported synchronized. |
| USB NICs | Expected MACs, links up and no host addresses, on all three nodes. |
| Cinder pools | Each recorded SATA identity matched; each `cinder-volumes` pool contained one PV and zero LVs. |
| Virtualization | CPU flags and `/dev/kvm` present on all three nodes. Each reported four CPU threads and approximately 15.5 GiB RAM. Usable KVM acceleration remains a Phase 3 check. |
| Planned VIP | No active conflict observed from any node during the bounded LAN probes. Silent/offline devices cannot be excluded. |
| R2d2 trust | Public CA installed; the saved server certificate verified successfully against Ubuntu's system trust bundle. |

No node configuration or storage contents were changed. The Windows host's TCP route to R2d2 is now confirmed. Joe subsequently reported the Windows certificate verification marker and confirmed the Chrome import. Joe then confirmed completing Linux Firefox setup. Both chosen browser imports were user-confirmed. Phase 3 D records later live HTTPS/API evidence; browser sign-in remains a separate confirmation.

## 8. Short lecture — after review

**The instructions match the machines.** Our inventory and saved settings agree with the current node identities, NICs and empty storage pools. That gives us a checked starting point for installation.

**The pool is ready; a cloud volume does not exist yet.** One PV means the selected SATA disk backs the pool. Zero LVs means Cinder has not created any volumes in it.

**Certificate trust is local.** R2d2 now recognizes our lab CA. Joe has confirmed the import in Chrome on Windows 11. Joe has also confirmed completing the Firefox import on Linux. Trusting a certificate does not make the future dashboard reachable.

**Syntax is only one checkpoint.** We checked that the playbook can be read, not that OpenStack is deployed. Phase 3 must prepare host dependencies, run full Kolla prechecks and then deploy and test the services. We agree on that work before starting it.

The run commands remain above for study and rebuilding.

[Ubuntu CA trust documentation](https://documentation.ubuntu.com/server/how-to/security/install-a-root-ca-certificate-in-the-trust-store/index.html)


[Next phase — draft plan only](../phase-03/README.md). Joe authorized drafting Phase 3 for review; no bootstrap or deployment is authorized by publishing that draft.
