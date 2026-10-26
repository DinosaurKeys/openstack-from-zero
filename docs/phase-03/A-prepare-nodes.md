# Chapter A — Prepare the nodes

[Phase 3](README.md) · [Previous phase](../phase-02/README.md)

**Procedure:** use the pinned tools and explicit HA configuration. On already prepared hosts, follow the [fresh-install route](../fresh-install.md). New deployment evidence is collected after the hosts are empty.

## 1. Purpose

OpenStack services will run in containers. First, the hosts need the container engine and the supporting packages/settings expected by our selected Kolla release.

Kolla calls this preparation **bootstrap**. It prepares hosts; deploying OpenStack services is a later step.

## 2. What is already complete

- R2d2 has the agreed Kolla-Ansible 23.0.0 and Ansible-core 2.21.5 environment.
- The private inventory assigns node1, node2 and node3 as controller, network, compute and storage hosts.
- Chapter F's live identity, NIC, clock, storage and local configuration checks passed.
- Each existing Cinder pool has one confirmed SATA PV and zero LVs.
- R2d2 CA trust passed. Windows Chrome and Linux Firefox imports are user-confirmed; live Horizon HTTPS remains a deployment-stage check.

## 3. Proposed steps

1. Review the **installed, pinned release's** bootstrap tasks and effective settings.
2. Publish a short list of the exact packages, services and configuration files it would change on our three nodes. Include any effects on hosts-file entries, time synchronization, firewall or access settings before asking Joe to run it.
3. Resolve any new choices with Joe. Keep the existing roles, NIC selection, storage pools and approved Cinder cluster-check exception.
4. Prepare and test one wrapper using the existing private inventory and configuration directory.
5. Publish the finished guide and script, with passwords/prompts and changes explained.
6. After agreement on that concrete scope, Joe pulls and runs it on R2d2.
7. Review one private combined report before moving to Chapter B.

The selected release and public quick-start establish the order: host bootstrap, full prechecks, then service deployment. The public quick-start uses example paths; our eventual wrapper must use the already prepared lab paths.

## 4. Understand the wrapper

[phase3-bootstrap.py](../../scripts/phase3-bootstrap.py) calls Kolla's existing bootstrap automation. It does not replace Kolla's installation tasks.

| Question | Answer |
|---|---|
| **Purpose** | Prepare the hosts to run OpenStack containers. |
| **Scope** | node1, node2 and node3. Install/configure Docker and supporting packages; apply the explained standard host settings. |
| **Inputs** | Existing private inventory, `globals.yml`, `passwords.yml`, per-node USB settings and reviewed certificates from Phase 2. |
| **Result** | One private combined report with before/after observations and Kolla task output. Verify each node before moving to Chapter B. |

**Read [the host-change table](A-host-changes.md) before execution.** It explains packages, removals, UFW, hosts-file/cloud-init handling, Docker configuration and the conditional libvirt AppArmor adjustment. This is host preparation; full prechecks and OpenStack service deployment follow later.

The wrapper checks the existing private files and all three nodes **before any bootstrap starts**. It stops on source drift or recorded existing container/VM/iSCSI workloads instead of guessing that a host can be changed.

It reuses `joe`, your SSH key and your existing configuration. Your independent `cinder-volumes` pools are verified, not recreated. No credential or certificate regeneration is included.

## 5. Download and run

**Where:** R2d2, logged in as joe. Read Section 4 and the linked change table first.

```bash
cd ~/openstack-from-zero
git pull --ff-only
/usr/bin/python3 scripts/phase3-bootstrap.py --apply
```

The script automatically uses `/home/joe/venvs/kolla`. It works whether your prompt currently shows `(kolla)` or not; no extra activation command is needed.

| Prompt | What to enter |
|---|---|
| Existing SSH-key passphrase, if requested | The passphrase that unlocks your R2d2-to-nodes key. |
| `Joe sudo password on node1:` | Joe's sudo password on node1. |
| Equivalent prompt for node2/node3 | The password for that named node; they need not be identical. |

Passwords are not displayed or saved in reports. The wrapper holds them in memory for its child processes, then exits after the checks.

While it runs, Kolla shows task output. `ok` means the requested task state was already satisfied, `changed` means a change was reported, and `skipped` means a condition excluded a task. Some tasks gather observations; those results are not an installation by themselves.

Running without `--apply` only prints the plan. It does not contact the nodes or change anything.

### Return the report

At the end, the script prints its exact report path and the `cat` command to read it. Run that printed command and return the report.

Expected successful checkpoint:

```text
NODE_BOOTSTRAP_READY node1
NODE_BOOTSTRAP_READY node2
NODE_BOOTSTRAP_READY node3
RESULT: HOST_BOOTSTRAP_READY for all
```

The post-checks verify Docker's service/API, expected packages, host-name resolution, NICs, clock and empty Cinder pools. They compare recorded protected configuration and management DNS observations.

If it reports `HOST_BOOTSTRAP_NEEDS_REVIEW`, return the report before another command. Earlier Kolla tasks may already have changed hosts; the wrapper does not perform automatic rollback or rerun the whole bootstrap. Completed reports stay private under `reports/`.

For a deliberate fresh rebuild with a new CA, use the newly reviewed fingerprint through `--expected-ca`, as explained in [Phase 2 Chapter F](../phase-02/F-validate-preparation.md#rebuilding-later).

## 6. Finish check — future result

- [ ] Standard bootstrap scope agreed; concrete changes published for reading before execution.
- [ ] Wrapper tested locally and published.
- [ ] Joe's run completed on all three nodes.
- [ ] Docker service/API, fresh node access and the recorded host checks passed.
- [ ] Combined report reviewed; Chapter B is next.

## 7. Development checks

The wrapper and pinned CLI are tested locally. The real host report is still required. For already prepared hosts, the fresh-install route reuses their existing Docker/bootstrap prerequisites; no package reinstall is needed.

## 8. Short lecture — after result review

**Docker is the container engine.** Its successful API response means Ansible's Python support can communicate with the running engine on each node. That is a useful host-preparation check; it does not mean OpenStack services exist yet.

**Some tasks needed changes; others did not.** The report shows required host changes while already satisfied requirements returned `ok`. A skipped task was excluded by its conditions. The recap counts tasks, not individual packages or files.

**Host packages and cloud services are separate.** The old host `open-iscsi` package was removed as approved. That does not delete the existing LVM storage pools; their readiness checks passed. The selected OpenStack storage services are deployed later.

**Next is the deployment gate.** Chapter B will run the full Kolla prechecks against the prepared hosts before we deploy OpenStack. We review that result before Chapter C.

All run commands and the host-change guide remain above for study and rebuilding.

[Official Kolla 2026.2 quick-start — deployment order](https://docs.openstack.org/kolla-ansible/2026.2/user/quickstart)


## Development verification

Nine regression tests passed: default-plan isolation, explicit configuration paths, source-drift rejection, all-node preflight ordering, per-node password privacy, readiness checks and early stopping. With real Kolla-Ansible 23.0.0 / Ansible-core 2.21.5, both check playbooks passed syntax checks, the actual bootstrap CLI parsed its task list, and the per-host password lookup selected the correct simulated value. Pinned Git source matched the tested host playbook and common/Nova settings.

These checks did not perform a live Ubuntu APT/Docker installation or SSH/sudo run against Joe's nodes. A live host result must be reviewed separately. This chapter does not claim that full Kolla prechecks, live Horizon HTTPS or deployment have passed.


[Next: Chapter B — Full Kolla prechecks](B-full-prechecks.md). The wrapper is ready to run; its live result is pending. All Chapter A run commands remain above for rebuilding.
