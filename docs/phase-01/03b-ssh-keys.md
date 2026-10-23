# 1.3b — Create SSH keys and copy the public key

[Phase 1](README.md) · [Previous: inspect the existing setup](03a-access-state.md)

**Goal:** let `joe` on R2d2 log in to all three nodes using a dedicated SSH key.

**Status: complete.** Joe's run ended with `SSH_KEYS_READY for all`. The full setup procedure below remains visible for study and rebuilding.

Your earlier inspection confirmed password access to the nodes. It found no regular `.pub` files or loaded agent keys on R2d2, and did not confirm key-only login. The script below checks its own filenames before creating anything.

## 1. Understand the two files

**One key-generation command creates both files.** You do not generate the public key separately.

| File | Location and job |
|---|---|
| `~/.ssh/openstack-from-zero` | Private key. Stays on R2d2 and is used to prove your identity |
| `~/.ssh/openstack-from-zero.pub` | Public key. This is the file copied to each node |
| `/home/joe/.ssh/authorized_keys` on each node | List of public keys allowed to log in as that node's `joe` account |

These files live outside the Git checkout. The script does not upload keys to GitHub.

## 2. What the script runs and why

Run [phase1-ssh-keys.sh](../../scripts/phase1-ssh-keys.sh) on **R2d2**, as **joe**, without sudo.

| Stage | Tool and purpose |
|---|---|
| Create the pair | `ssh-keygen -t ed25519` creates the dedicated private/public files together |
| Prepare the local key | `ssh-add` loads it into the local SSH agent, a helper that can use an unlocked key |
| Check each target | Confirm the expected hostname, account, Ubuntu version and management IP before adding access |
| Copy only the public key | `ssh-copy-id` selects the `.pub` file and adds it to `joe`'s `authorized_keys` on node1, node2 and node3 |
| Prove it works | Open a fresh connection using this exact key, with node-password authentication disabled |
| Save the result | One private report under `reports/` on R2d2 |

The script preserves other keys and existing authorized-key entries. It reuses a matching project pair on repeat runs and refuses incomplete, mismatched or symlinked project files. It leaves Chrony, `/etc/hosts`, NICs, SSH-server settings and sudo policy unchanged.

## 3. Understand the prompts before running

**During key creation:** choose a key passphrase, or press Enter twice to create the key without one. A passphrase protects the private-key file; it is your choice, not an automatically chosen password.

**When `ssh-add` asks:** enter that same key passphrase. If you chose none, there is no key-unlock passphrase to enter.

**When connecting to a node:** enter that node's Ubuntu `joe` password if requested. The first run can ask once to check its identity and again to install the public key. The final key-only check does not allow the node password.

The script uses an existing local agent when available. Otherwise, it starts one for this run and stops it on exit. A key loaded into an existing agent is available there for up to four hours. That timer does not expire the key files or remove the nodes' public-key entries.

## 4. Download and run on R2d2

These commands enter the course folder, download the update, then run the explained setup. `--user joe` selects the account; `--apply` allows creation of the dedicated pair and installation of its public key.

```bash
cd ~/openstack-from-zero
git pull --ff-only
bash scripts/phase1-ssh-keys.sh --user joe --apply
```

Normal host-key verification stays enabled. The node host identities must already be trusted from [Chapter 1.3](03-access-check.md).

## 5. Check and return the result

Each node must show a line beginning with:

```text
KEY_LOGIN_OK node1
KEY_LOGIN_OK node2
KEY_LOGIN_OK node3
```

The final success marker is:

```text
RESULT: SSH_KEYS_READY for all
```

The script prints the report path and its `cat` command. Run that command to display the saved report and **send back the output**. Stop here for review before the next configuration step.

## 6. Login command for later use

This example connects to node1 using the dedicated private key on R2d2:

```bash
ssh -i ~/.ssh/openstack-from-zero joe@192.168.0.200
```

`-i` selects the local identity file. It does not copy the private key to the node. Use `.201` for node2 and `.202` for node3. A protected key may ask for its local passphrase when it is not already available through an agent.

Type `exit` in the node's shell to return to R2d2.

## 7. Reviewed result

| Check | Result from Joe's run |
|---|---|
| Dedicated private/public pair | Created on R2d2; fingerprints matched |
| Local key unlock | Passphrase entered and key loaded into the existing SSH agent |
| Public-key installation | One key added for `joe` on each node |
| node1 `192.168.0.200` | Fresh key-only login passed |
| node2 `192.168.0.201` | Fresh key-only login passed |
| node3 `192.168.0.202` | Fresh key-only login passed |

The final marker was **`RESULT: SSH_KEYS_READY for all`**. Chrony, `/etc/hosts`, NICs and sudo policy were not changed.

## 8. Short lecture — what your result means

Creating the two files alone does not grant access. `ssh-copy-id` added the public key to each node's `authorized_keys`, using that node's account password to authorize the addition.

R2d2 then used its private key locally to prove its identity. Each node checked that proof against the public key it now has. The private key stayed on R2d2.

The final checks used fresh connections with node-password authentication disabled. Therefore, the three `KEY_LOGIN_OK` lines prove that this key pair worked on all three intended nodes.

The key passphrase unlocks your private-key file locally. It is different from the node's login password, and SSH-key access does not change sudo permissions.

Your agent reported a **14,400-second lifetime: four hours**. That is the lifetime of its loaded key, not the key files or the public-key entries on the nodes. When the loaded key expires, the files remain valid; a later login using the explicit `-i` command may ask for the key passphrase again.

**Checkpoint complete.** Keep the steps above for reference. The next configuration change will be agreed with Joe before proceeding.

References: [OpenSSH key generation](https://man.openbsd.org/ssh-keygen) · [Public-key installation](https://manpages.debian.org/bookworm/openssh-client/ssh-copy-id.1.en.html) · [SSH agent key loading](https://man.openbsd.org/ssh-add).
