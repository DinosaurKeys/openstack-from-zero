# Chapter F — Validate preparation

[Phase 2](README.md) · [Previous: configure the lab](E-configure-lab.md)

**Goal:** check that the files on R2d2 still match the three nodes before installation.

**Status: ready to run.** Chapter E passed. This chapter's live report is pending. Horizon remains enabled in the plan so Joe can learn through its web dashboard.

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

Keep Chapters A–E in order, then run this chapter. A fresh Chapter E creates a **different CA fingerprint**. After reviewing that new result, supply its exact 64-character lowercase SHA256 value with `--expected-ca`. The normal command is pinned to the CA already reviewed for this lab:

```text
278a4e6a84b761f954b9bbf1f8051c3560b63f0eb073efc9e95d8d1c69df3cbb
```

A different CA already installed under our trust filename requires a separate reviewed replacement; the script will not overwrite it. Do not copy an old fingerprint onto a new lab or regenerate certificates to make a check pass.

## 5. Browser preparation — Windows and Linux

Joe confirmed **Chrome and Firefox on Windows and Linux**, accessed from outside R2d2. R2d2 itself is currently accessed using the **VMware console**.

Read [F — Browser certificate guide](F-browser-certificates.md). It covers both browsers without assuming a Linux distribution. The transfer route from R2d2 to those computers is still pending; no guessed NAT address or file-copy command is provided. **Run the R2d2 checks first.** After report review, confirm the transfer route before importing on the browser computers.

No Horizon connection test is expected yet: the VIP, HTTPS listener and dashboard will be deployed in Phase 3.

## 6. Finish check and review

- [ ] Review the actual report from this chapter.
- [ ] Review any virtualization, NIC, storage, clock or VIP findings.
- [ ] Verify R2d2 CA trust if requested.
- [ ] Confirm public-CA transfer and import on the intended browser computers.
- [ ] Agree on Phase 3 before running bootstrap or deployment.

**Development verification:** ten regression tests cover read-only probes, wrong-node rejection, ARP packet handling, certificate/configuration preservation, CA mismatch, private-file guards and trust destination protection. Real Kolla-Ansible 23.0.0 / Ansible-core 2.21.5 inventory parsing, playbook syntax, ad-hoc argument parsing and OpenSSL certificate verification also passed in an isolated development folder. These are not live SSH/sudo/ARP or browser results from Joe's lab. No live completion is claimed yet.

## 7. Short lecture — after review

After the report is reviewed, this section will explain the observed result: what matched, what CA trust proves, and what Phase 3 must still verify. The commands above will remain for study and rebuilding.

[Ubuntu CA trust documentation](https://documentation.ubuntu.com/server/how-to/security/install-a-root-ca-certificate-in-the-trust-store/index.html)
