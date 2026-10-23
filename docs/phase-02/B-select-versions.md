# Chapter B — Select compatible versions

[Phase 2](README.md) · [Previous: check R2d2](A-check-r2d2.md)

**Goal:** agree on one compatible set of OpenStack, Kolla-Ansible, Ansible and Python versions.

**Status: version set and environment path agreed by Joe on 2026-10-23.** Chapter A's report is reviewed: Ubuntu 24.04.5 LTS and system Python 3.12.3 on R2d2, with no active virtual environment. Read the plan; no installation command is ready in this chapter yet.

## 1. Purpose

These tools work together. Installing arbitrary versions separately can produce an incompatible combination. We choose the set before writing the installation script.

## 2. Planned steps

1. Review Chapter A's actual OS, Python and existing-tool information.
2. Check release-specific official support documentation for Ubuntu 24.04 and its Python version.
3. Read that release's Ansible requirements and select a compatible installer version.
4. Recheck its Cinder configuration against our agreed three independent LVM backends, empty cluster name and specific cluster-check exception.
5. Explain the proposed version set and get Joe's agreement before installation.
6. Record exact versions/references so a rebuild can use the same planned set.

## 3. Reviewed compatibility proposal

Checked on **2026-10-23** against official release records and the released Kolla source. Joe has agreed to the version set below. These are **selected versions**, not yet confirmed installed tools.

| Component | Proposal | Reason |
|---|---|---|
| Host OS | Keep Ubuntu 24.04 LTS | Listed in Kolla 23.0.0's supported operating systems |
| Python | Use Ubuntu's Python 3.12 (observed 3.12.3) | Compatible with the proposed Ansible release; no separate Python installation needed |
| OpenStack series | **2026.2 (Hibiscus)** | A released series supported by Kolla-Ansible 23.0.0 |
| Kolla-Ansible | **23.0.0** | Official final release, with a fixed source commit below |
| Ansible Core | **2.21.5** | Fits Kolla's `>=2.20,<2.22` requirement and supports Python 3.12 |
| Tool environment | **/home/joe/venvs/kolla** | Proposed separate folder on R2d2; Chapter A found this candidate absent |

The Kolla release record identifies source commit `4b493ef36ace2b90257ecf94feb00e2c6e8014c9` for **23.0.0**. The installation plan will use this released reference rather than a moving development branch.

**Why this set:** it uses your existing Ubuntu/Python and a released OpenStack installer. The Ansible version satisfies that installer's declared range. This is a compatibility recommendation, not a claim that our three-node deployment has passed.

The top-level versions are fixed in this proposal. Chapter C must also record the actual installed Python dependencies and Ansible collections in private records; these two version pins alone are not a complete dependency lock or container-image snapshot.

### Storage design recheck

Kolla 23.0.0 still provides the LVM backend template and volume-group existence check. Its multiple-Cinder-volume cluster-name check still has the specific exception already agreed in Phase 1:

```yaml
cinder_cluster_name: ""
cinder_cluster_skip_precheck: true
```

This block explains the later Chapter E configuration; **do not paste or apply it now**. Each node keeps its own `cinder-volumes` pool. No shared storage or replication is implied. All other prechecks remain enabled. This continues Joe's existing storage decision; it does not require a new storage choice.

### What Chapter C would install

Only on **R2d2**, the released quickstart lists these Ubuntu prerequisites:

| Package | Purpose |
|---|---|
| `git` | Download source code; already installed |
| `python3-dev` | Python headers for building dependencies |
| `gcc` | Compile dependencies if needed |
| `libffi-dev` | Support dependencies that call native libraries |
| `libssl-dev` | Encryption-library development files |
| `libdbus-glib-1-dev` | Build support required by the documented prerequisite list |
| `python3-venv` | Create the separate Python environment, including its initial pip |

The script will show the APT plan before installation. Then it will create the proposed environment as joe, install the pinned tools and Kolla's Ansible collection dependencies, and verify their versions. It will preserve existing paths and stop if an unexpected setup would be replaced.

System-wide `python3-pip` is not needed for this plan: pip will run inside the virtual environment. No node connections, Docker installation, inventory/configuration creation, password generation or deployment belong to this R2d2 tool-installation step. Internet access will be required for downloads.

**Decision recorded:** Joe agreed to this version set and `/home/joe/venvs/kolla`. [Chapter C](C-install-tools.md) now contains the explained installation script and run commands.

### Evidence

- [Official Hibiscus release record: Kolla-Ansible 23.0.0 and source commit](https://github.com/openstack/releases/blob/master/deliverables/hibiscus/kolla-ansible.yaml).
- [Kolla 23.0.0: supported operating systems](https://github.com/openstack/kolla-ansible/blob/23.0.0/doc/source/user/support-matrix.rst).
- [Kolla 23.0.0: Ansible version requirements](https://github.com/openstack/kolla-ansible/blob/23.0.0/requirements.txt).
- [Kolla 23.0.0: prerequisite and virtual-environment instructions](https://github.com/openstack/kolla-ansible/blob/23.0.0/doc/source/user/quickstart.rst).
- [Ansible Core 2.21.5: published release and Python requirement](https://pypi.org/project/ansible-core/2.21.5/).
- [Kolla 23.0.0: Cinder prechecks](https://github.com/openstack/kolla-ansible/blob/23.0.0/ansible/roles/cinder/tasks/precheck.yml) and [Cinder configuration template](https://github.com/openstack/kolla-ansible/blob/23.0.0/ansible/roles/cinder/templates/cinder.conf.j2).
- [Cinder 2026.2 driver base](https://github.com/openstack/cinder/blob/stable/2026.2/cinder/volume/driver.py), [LVM driver](https://github.com/openstack/cinder/blob/stable/2026.2/cinder/volume/drivers/lvm.py) and [volume manager](https://github.com/openstack/cinder/blob/stable/2026.2/cinder/volume/manager.py), reviewed for the existing independent-pool design. These Cinder links track a stable branch; this review is not a pinned container-image build.

## 4. Script and commands

This is a documentation/review step. It needs no lab mutation script. The chosen version set will become inputs to Chapter C's installation script.

**Do not copy installation commands from the development `latest` quickstart.** We will use the selected release's instructions and requirements.

## 5. Finish check

The table above records the proposed components and compatibility evidence. Joe's agreement is **recorded**; Chapter C's actual installation report is pending. Earlier use of 2026.1 documentation for the storage design is a source review, not a claim that this release is installed on R2d2.

## 6. Short lecture — after agreement

Ubuntu provides the Python runtime. Ansible Core supplies the automation engine; Kolla-Ansible supplies OpenStack's deployment tasks. The selected Ansible version fits Kolla's allowed range and runs on your Python 3.12. Selecting versions establishes the plan; Chapter C verifies the actual installed tools.

References to consult: [Kolla release selector and support matrix](https://docs.openstack.org/kolla-ansible/latest/user/support-matrix.html) · [Kolla installation sequence](https://docs.openstack.org/kolla-ansible/latest/user/quickstart.html). Use the matching release selector when making the final choice.

[Next planned chapter: install management tools](C-install-tools.md).
