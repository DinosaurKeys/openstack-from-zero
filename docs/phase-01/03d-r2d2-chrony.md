# Chapter G — Install Chrony on R2d2

[Phase 1](README.md) · [Previous: node names](03c-r2d2-hosts.md)

**Goal:** use Chrony to keep R2d2's clock synchronized.

**Status: complete.** Joe's report from 2026-10-23 ends with `R2D2_CHRONY_READY`. The full installation steps remain below for study and rebuilding.

## 1. Purpose

Your inspection confirmed that R2d2's clock was already synchronized through `systemd-timesyncd`. Chrony is an alternative time-sync service. This agreed step switches R2d2 to Chrony, the service already working on all three nodes.

Accurate clocks help you compare logs and support time-sensitive authentication. This step installs a time client on R2d2; it does not configure R2d2 as the nodes' time server.

## 2. What the script runs and why

Read [phase1-chrony.py](../../scripts/phase1-chrony.py). Run it **on R2d2 as joe**, without putting sudo before it.

| Command inside the script | Purpose |
|---|---|
| `timedatectl status` | Show the current clock and time-service state |
| `dpkg-query` | Check whether Chrony is already installed |
| `apt-get update` | Refresh package lists if installation is needed; this does not run a system upgrade |
| `apt-get --simulate ... install chrony` | Show the package plan; stop if it proposes removing anything other than `systemd-timesyncd` |
| `apt-get ... install chrony` | Install Chrony and required dependencies; APT may replace the previous time client |
| `systemctl enable --now chrony.service` | Start Chrony and enable it for future boots |
| `systemctl is-active` / `is-enabled` | Check Chrony is active and enabled, and the previous time client is inactive |
| `chronyc waitsync 24 0.1 0 2` | Wait up to about 48 seconds for synchronization and less than 0.1 second remaining clock correction |
| `chronyc tracking` / `chronyc -n sources -v` | Show the clock estimate and selected time source |

The script uses Ubuntu's supplied Chrony configuration for a fresh installation. It does not choose custom time-server addresses or edit the source configuration. If Chrony configuration already exists, the installation keeps it. If the package is already installed, the script skips package installation and checks the service.

All work is local to R2d2. The script leaves the nodes, SSH keys, hosts files, NICs and timezone unchanged. It does not issue a manual `makestep` command; Chrony follows its installed clock-correction policy.

## 3. Understand the prompts

- **Sudo password:** enter joe's Ubuntu login password **on R2d2**. This is not the SSH-key passphrase.
- **APT `Do you want to continue? [Y/n]`:** review the displayed package plan. Installing Chrony can remove `systemd-timesyncd`, because they perform the same job. Enter `Y` to proceed with the explained replacement. The script does not automatically answer this prompt.

The private report records command output, not your password. Package installation is a real change: if installation or verification stops, keep the report for review. The script does not automatically undo package changes.

## 4. Download and run on R2d2

These commands enter your course folder, download this update and run the explained installation. `--apply` allows local package and service changes.

```bash
cd ~/openstack-from-zero
git pull --ff-only
python3 scripts/phase1-chrony.py --apply
```

Without `--apply`, the script only describes the planned work. Internet access to Ubuntu repositories and the configured time sources is needed for a fresh installation and synchronization.

## 5. Check and return the result

Success requires all these checks:

- Chrony is active and enabled for boot.
- `systemd-timesyncd` is inactive or absent.
- Chrony's synchronization wait succeeds.
- Tracking shows `Leap status : Normal`.
- The sources table has a selected source marked with `*`, commonly `^*`.

The script must finish with:

```text
RESULT: R2D2_CHRONY_READY
```

It prints the private report's path and its exact `cat` command. Run that printed command and send back the output. **Stop here for review before the next configuration step.**

## 6. Reviewed result

| Check | Result from Joe's run |
|---|---|
| Installation | Chrony 4.5 installed, with the `tzdata-legacy` dependency |
| Previous time client | `systemd-timesyncd` package removed; service inactive |
| Chrony service | Active and enabled for boot |
| Synchronization wait | Passed on the second check |
| Tracking | `Leap status : Normal` |
| Selected source | `^* 185.125.190.56` in this report |
| System clock | `timedatectl` reports synchronized |
| Timezone | Remains `Etc/UTC` |

The final marker was **`RESULT: R2D2_CHRONY_READY`**. This run worked only on R2d2. No node connection was made.

## 7. Short lecture — what your result means

Chrony now runs in the background, comparing R2d2's clock with network time sources and correcting it. Enabling the service means it is configured to start on future boots; this run did not perform a reboot test.

In the sources table, `^*` marks the selected server and `^+` marks a source combined with it. A `^-` source is not being combined; that symbol alone is not a failure. The selected server can change later.

`Leap status : Normal` and the successful wait confirm initial synchronization. This first snapshot does not prove long-term clock accuracy. The script checks remaining clock correction below 0.1 second; it does not impose a frequency-skew limit.

The former time client was replaced, so Chrony now handles this job. R2d2 was not configured as a time server for the nodes.

**Checkpoint complete.** Keep all setup commands above. The next step will be agreed with Joe before proceeding.

References: [Ubuntu Chrony installation](https://ubuntu.com/server/docs/how-to/networking/serve-ntp-with-chrony/) · [Ubuntu 24.04 chronyc commands](https://manpages.ubuntu.com/manpages/noble/man1/chronyc.1.html).
