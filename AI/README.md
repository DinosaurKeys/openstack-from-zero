# Hermes: AI writes, Joe executes

Follow [Phase 8 — Hermes with OpenRouter](../docs/phase-08/README.md) for the full installation and first-use procedure. That is the single setup guide.

| File | Purpose |
|---|---|
| [IaC rules](HERMES-IAC-RULES.md) | Define the assistant's scope and human review boundary |
| [Learning guide](HERMES-LEARNING-GUIDE.md) | Keep explanations short, practical and step by step |
| [First-session prompt](HERMES-START.md) | Load the two guides and wait for a task |
| [Launcher](../scripts/hermes-iac.sh) | Start the reviewed OpenRouter/file-only session in the public working copy |

This adapts the [earlier AI workflow](https://github.com/DinosaurKeys/openstack-zero-to-hero/tree/main/AI). Its past workload results and cloud identities are not evidence for this fresh lab.

Hermes receives a public code working copy. Joe keeps cloud credentials, SSH keys, Terraform state, private reports and infrastructure execution in the management environment.
