# Chapter B — Select compatible versions

[Phase 2](README.md) · [Previous: check R2d2](A-check-r2d2.md)

**Goal:** agree on one compatible set of OpenStack, Kolla-Ansible, Ansible and Python versions.

**Status: draft.** Read the plan; no installation command is ready in this chapter yet.

## 1. Purpose

These tools work together. Installing arbitrary versions separately can produce an incompatible combination. We choose the set before writing the installation script.

## 2. Planned steps

1. Review Chapter A's actual OS, Python and existing-tool information.
2. Check release-specific official support documentation for Ubuntu 24.04 and its Python version.
3. Read that release's Ansible requirements and select a compatible installer version.
4. Recheck its Cinder configuration against our agreed three independent LVM backends, empty cluster name and specific cluster-check exception.
5. Explain the proposed version set and get Joe's agreement before installation.
6. Record exact versions/references so a rebuild can use the same planned set.

## 3. Script and commands

This is a documentation/review step. It needs no lab mutation script. The chosen version set will become inputs to Chapter C's installation script.

**Do not copy installation commands from the development `latest` quickstart.** We will use the selected release's instructions and requirements.

## 4. Finish check

A short version table records each selected component, compatibility evidence and Joe's agreement. Versions are **not selected yet**. Earlier use of 2026.1 documentation for the storage design is a source review, not a claim that this release is installed on R2d2.

## 5. Short lecture — after review

The lesson will explain why these particular versions work together after the selection is agreed.

References to consult: [Kolla release selector and support matrix](https://docs.openstack.org/kolla-ansible/latest/user/support-matrix.html) · [Kolla installation sequence](https://docs.openstack.org/kolla-ansible/latest/user/quickstart.html). Use the matching release selector when making the final choice.

[Next planned chapter: install management tools](C-install-tools.md).
