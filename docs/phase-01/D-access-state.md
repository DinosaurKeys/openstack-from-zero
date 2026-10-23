# Chapter D — Check SSH keys, time and host names

[Phase 1](README.md) · [Previous: connection check](C-access-check.md)

**Status: inspection complete.** Joe's report ended with `ACCESS_STATE_COLLECTED`. The procedure below remains available for study.

**Purpose:** find out what already exists on R2d2 and the nodes before creating keys or configuring anything.

## 1. Understand the check

Run [phase1-access-state.py](../../scripts/phase1-access-state.py) on **R2d2**, as your normal `joe` account, **without sudo**.

| It checks | Plain meaning |
|---|---|
| R2d2's public-key files and SSH agent | Which public-key fingerprints and loaded keys are visible |
| A fresh key-only login to each node | Whether the current SSH setup can log in without asking for the node password or a key passphrase |
| Chrony package, service, tracking and sources | Whether Chrony is installed/running and what time information it reports |
| `/etc/hosts` and name lookups on all four machines | What their address books contain and which IPv4 addresses the names resolve to |

The script connects to node1 `.200`, node2 `.201` and node3 `.202` on `192.168.0.0/24`, using the existing SSH settings. If the key-only attempt cannot complete, it can ask for that node's `joe` password to read its settings.

It creates no keys, copies no public keys, installs no packages, and changes no host, network or time configuration. It records key filenames and public fingerprints, not private-key contents. No sudo password is needed.

**Before running:** the SSH connections from [Chapter C](C-access-check.md) must already work and their host identities must be trusted. This check keeps host-key verification enabled and does not add or replace trusted host keys.

## 2. Download the update on R2d2

Enter the course folder and download the latest files:

```bash
cd ~/openstack-from-zero
git pull --ff-only
```

## 3. Run the inspection on R2d2

`--user joe` selects the node login account. Allow a few minutes for the four-machine inspection and any node password prompts.

```bash
python3 scripts/phase1-access-state.py --user joe
```

## 4. Read and return the report

The script saves **one private report** under `reports/` on R2d2 and prints the exact `cat` command to display it. `cat` reads the saved file; it does not repeat the inspection.

**Run that printed `cat` command and send back its output.** Reports stay out of GitHub.

| Report label | Meaning |
|---|---|
| `key-only login: CONFIRMED` | A fresh key-only connection collected the expected node identity |
| `key-only login: NOT CONFIRMED` | That attempt did not establish key-only access; it does not prove no key exists |
| `UNAVAILABLE` | That item could not be read; no replacement or installation was attempted |
| `ACCESS_STATE_COLLECTED` | All four expected machines returned identity-checked observations; this is not a configuration-ready verdict |
| `ACCESS_STATE_PARTIAL` | Some machine observations could not be confirmed; send the report too |

The R2d2 address and name answers are observations. The script does not assume a fixed NAT address or that the nodes can connect back to it.

## 5. Review before the next change

Stop after returning the report. We review what exists, add the short lesson from your actual result, and ask before proceeding with configuration changes. The completed SSH and NIC procedures remain available for study.

## Reviewed result from this lab

| Item | Observed result |
|---|---|
| Node access | Password login worked on all three; key-only login was not confirmed |
| R2d2 keys | No regular public-key files found; agent had no loaded identities |
| Node time | Chrony installed, active and enabled on all three; clocks reported synchronized |
| R2d2 time | Clock reported synchronized through systemd-timesyncd; Chrony was not installed |
| Node name lookups | All three nodes resolved node1–node3 to the correct management addresses |
| R2d2 node lookups | node1–node3 did not resolve |
| Reverse name lookup | The nodes did not resolve r2d2; no reverse-connectivity requirement was assumed |

## Short lecture — what your report means

A connection by IP does not need a hostname mapping. `/etc/hosts` is each machine's local address book, letting it look up a name such as `node1`.

A synchronized clock does not identify the time service. Your nodes use Chrony; R2d2 currently uses systemd-timesyncd.

Finding no public-key files or loaded keys does not prove no private key exists elsewhere. The next script uses a dedicated filename and checks for existing files before creating its pair.

**Next, authorized by Joe:** [Chapter E — Create SSH keys and copy the public key](E-ssh-keys.md).

References: [OpenSSH authentication and connection options](https://man.openbsd.org/ssh_config) · [Chrony monitoring commands](https://chrony-project.org/doc/4.4/chronyc.html).
