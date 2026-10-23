# Chapter C — Install management tools

[Phase 2](README.md) · [Previous: select versions](B-select-versions.md)

**Goal:** install the agreed management tools in an isolated Python environment on R2d2.

**Status: draft.** Package versions, environment path and installation commands await Chapters A and B. Nothing in this chapter should be run yet.

## 1. Purpose

A Python virtual environment is a separate folder for this project's Python tools. It helps keep Kolla's dependencies separate from Ubuntu's system Python.

## 2. Planned steps

1. Show the exact Ubuntu prerequisite packages needed by the selected release and which are already installed.
2. Explain the proposed environment location; preserve any existing environment unless its replacement is agreed.
3. Publish the installation script and explain its sudo and package-manager prompts.
4. Joe pulls and runs it on R2d2.
5. Verify the Python, Ansible and Kolla-Ansible versions from that environment.
6. Save one private report and review the result before inventory work.

## 3. Script and commands

The installation script is planned, not published as runnable work yet. It will follow the agreed version set and avoid changing the nodes or overwriting unknown configuration.

System package changes may require **joe's R2d2 sudo password**. Tool installation inside the agreed environment will run as joe. The executable guide will explain both before its commands.

## 4. Finish check

The selected tools run from the intended environment and report the agreed versions. Tool installation alone does not deploy OpenStack.

## 5. Short lecture — after review

The lesson will explain the created environment and how to use its tools after installation succeeds.

[Next planned chapter: create the inventory](D-create-inventory.md).
