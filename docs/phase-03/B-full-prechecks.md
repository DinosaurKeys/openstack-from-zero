# Chapter B — Full Kolla prechecks

[Phase 3](README.md) · [Previous: host preparation](A-prepare-nodes.md)

**Procedure:** use the pinned tools and explicit HA configuration. On already prepared hosts, follow the [fresh-install route](../fresh-install.md). New deployment evidence is collected after the hosts are empty.

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

- [ ] Chapter A's live bootstrap result reviewed.
- [ ] Default lab image-source acceptance agreed.
- [ ] Wrapper tested locally and published.
- [ ] Local ProxySQL TLS files verified and ready.
- [ ] Full precheck result from Joe's lab reviewed.
- [ ] Joe authorized preparing and publishing Chapter C. Actual deployment remains Joe's execution step.

**Limits:** a successful run does not prove image tags can be pulled, service startup, live HTTPS, usable guest acceleration or VM/volume operation. Those need later deployment and functional checks. For a new CA, first save its explicit approval in Phase 2 F. Prechecks and later tools use that saved fingerprint.

## 6. Short lecture — after review

After the actual report, explain which deployment requirements passed and how that differs from Chapter A's host preparation. The run instructions above remain for study and rebuilding.

