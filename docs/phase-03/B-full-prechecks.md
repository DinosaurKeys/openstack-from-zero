# Chapter B — Full Kolla prechecks

[Phase 3](README.md) · [Previous: host preparation](A-prepare-nodes.md)

**Status: ready to run — live result pending.** Chapter A passed on all three nodes. Joe approved accepting the default Kolla test-image registry for this learning lab.

## 1. Purpose

Check the prepared hosts and OpenStack settings before deploying services.

| Chapter | Job |
|---|---|
| A — Bootstrap | Prepare the hosts and Docker. **Completed.** |
| B — Prechecks | Check deployment requirements. **This step.** |
| C — Deploy | Install/start the OpenStack services. **Later.** |

Passing prechecks is a deployment gate, not proof that the future cloud is running.

## 2. What the wrapper does

[phase3-prechecks.py](../../scripts/phase3-prechecks.py) runs from **R2d2 as joe** and calls Kolla's existing `prechecks` command.

| Question | Answer |
|---|---|
| **Scope** | node1, node2 and node3, with all three controller/network/compute/storage roles retained. All three are checked together so the load-balancer/VIP checks have the full group. |
| **Inputs** | Existing private inventory, globals, passwords, host variables and certificates from Phase 2. No credential or certificate generation. |
| **Checks** | Host OS, Ansible/Docker/Python requirements, time synchronization, interfaces, VIP/port availability, inventory/service settings, host names, certificate-file requirements and Cinder storage prerequisites, as applicable to enabled services. |
| **Result** | One private combined report containing host observations, the full Kolla task transcript and each node's final recap. |

The wrapper first checks the reviewed source files, unchanged lab configuration and Chapter A host-readiness observations. It then runs full Kolla prechecks and verifies the recorded host/package observations and private inputs afterward.

**This runs checks, not bootstrap or deployment.** The reviewed path does not install packages, change firewall/Netplan settings, create VGs, assign the API VIP or start OpenStack services. It sends network probes, queries Docker and uses temporary Ansible module/helper files on the nodes. Private run files and reports are written on R2d2.

### Two agreed choices remain visible

- **Independent Cinder LVM pools:** retain the already approved `cinder_cluster_skip_precheck: true` with an empty cluster name. Other storage checks remain enabled.
- **Lab image source:** use `--use-test-images` to accept `quay.io/openstack.kolla` for this homelab. Kolla 23 requires explicit acceptance of that test-image namespace. This does not deploy or pull images, and it does not disable the host, time, network or storage checks.

The wrapper supplies no `--skip-tags`, selected-task shortcut or automatic check bypass. OS and NTP checks stay enabled. A failing check produces a report for review, not a configuration repair.

## 3. Download and run

### Local database TLS files on R2d2

**Purpose:** provide the CA, certificate and key filenames that ProxySQL expects for the already enabled database frontend TLS. They are separate files from the API `haproxy*.pem` files.

The normal Chapter E preparation now includes them. On an existing lab, this local-only command verifies the reviewed CA and reuses the existing certificate/key:

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-db-tls.py --apply
```

It prepares only these three files under `private/phase2/kolla/certificates/`:

| Filename | Verified original reused |
|---|---|
| `proxysql-ca.pem` | `ca/lab-root.crt` |
| `proxysql-cert.pem` | `server.crt` |
| `proxysql-key.pem` | `private/server.key` |

The key stays private on R2d2 with mode `0600`. There are no node connections, sudo/password prompts, package installations, new certificates or TLS-setting changes. Existing different files stop for review rather than being overwritten. The command prints a private preparation report.

Continue only when it prints:

```text
RESULT: PROXYSQL_TLS_READY
```

Your existing Windows Chrome and Linux Firefox CA imports remain valid because the CA is unchanged.

### Run full prechecks after local preparation

**On R2d2, logged in as joe:**

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-prechecks.py --run
```

It automatically uses `/home/joe/venvs/kolla`; your prompt may already show `(kolla)`. No manual environment activation or direct Kolla command is needed.

The wrapper prints the report path **at the start**, while the file is being written, and again at the end.

### Password prompts

1. If asked, enter the existing SSH-key passphrase to unlock the R2d2-to-nodes key.
2. At `Joe sudo password on node1:`, type joe's sudo password for node1 and press **Enter**.
3. Repeat for the named node2 and node3 prompts. The passwords need not be the same.

**No characters or dots appear while typing passwords.** The script is waiting for input at these prompts; no visible text is expected.

Passwords are held in memory and supplied through child-process environment variables, not saved in command arguments, options files or reports. The private options file contains a lookup expression only. Local delegated certificate/password-file checks run as joe on R2d2; node tasks use sudo where Kolla requests it. No passwordless-sudo policy is created.

Without `--run`, the script only prints its plan and makes no connections or file changes.

## 4. Read the result

Wait until the wrapper returns to your shell prompt. Then use the exact `cat` command printed at the end and return that report.

Expected final success marker:

```text
RESULT: KOLLA_PRECHECKS_PASSED for all
```

The wrapper requires a final recap for node1, node2 and node3 with zero failed, unreachable, rescued or ignored tasks, plus evidence that the full common precheck role ran. It also checks its recorded before/after observations.

- `ok`: the task completed successfully.
- `skipped`: its condition excluded the task; disabled services and the explicitly accepted test-registry check can produce these entries.
- `changed`: a reported task change; inspect the report rather than treating it as proof of deployment.

If the result is `KOLLA_PRECHECKS_NEEDS_REVIEW`, stop and return the report before another command. The wrapper does not disable additional checks, retry the full run, bootstrap or deploy automatically.

Keep the full report and private files off GitHub. We publish the reviewed summary and short lesson afterward.

## 5. Finish check

- [x] Chapter A's live bootstrap result reviewed.
- [x] Default lab image-source acceptance agreed.
- [x] Wrapper tested locally and published.
- [ ] Local ProxySQL TLS files verified and ready.
- [ ] Full precheck result from Joe's lab reviewed.
- [ ] Agree on Chapter C before deployment.

**Limits:** a successful run does not prove image tags can be pulled, service startup, live HTTPS, usable guest acceleration or VM/volume operation. Those need later deployment and functional checks. For an intentional rebuild with a new CA, use the newly reviewed `--expected-ca` fingerprint as in Phase 2 Chapter F.

## 6. Short lecture — after review

After the actual report, explain which deployment requirements passed and how that differs from Chapter A's host preparation. The run instructions above remain for study and rebuilding.

## Development verification

Eight regression tests passed for plan isolation, exact precheck command scope, source drift, all-node recap validation, host/input preservation, execution ordering, early stopping and password privacy. Real Kolla-Ansible 23.0.0 / Ansible-core 2.21.5 parsed the actual precheck CLI/task list and both observation playbooks; password lookups and the registry-acceptance condition were evaluated with simulated values. Private integration inputs were unchanged. Pinned release source matched the tested site playbook, registry/package checks and load-balancer prechecks.

These local tests did not execute full remote prechecks on Joe's nodes. Their live result remains pending.

[Official Kolla deployment order](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart) · [Pinned registry check](https://github.com/openstack/kolla-ansible/blob/4b493ef36ace2b90257ecf94feb00e2c6e8014c9/ansible/roles/prechecks/tasks/registry_checks.yml)


**TLS preparation verification:** six additional tests cover the missing-file completion, original identity/configuration preservation, repeated runs, conflict/symlink refusal, fingerprint rejection and early layout validation. Together with the affected existing suites, 39 tests passed. In an isolated folder, OpenSSL verified the ProxySQL certificate chain and VIP; the pinned release's CA-check path and all three deployment copy-source paths resolved to the prepared files. Existing credentials and original certificate/key files were unchanged. This is not a claim that the lab's full remote prechecks have passed.
