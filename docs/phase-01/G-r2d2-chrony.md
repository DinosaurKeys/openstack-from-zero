# Chapter G — Prepare time synchronization

[Phase 1](README.md) · [Previous: node names](F-r2d2-hosts.md) · [Next: USB NICs](H-nics-and-storage.md)

**Goal:** Chrony running, enabled and synchronized on **R2d2 and all three nodes**. Complete this before Phase 2 readiness checks. Each machine uses its configured time sources; R2d2 is not the nodes' time server.

## 1. Why this is needed

Chrony keeps the clocks aligned. This helps authentication and makes logs comparable. A synchronized clock using another time client does not satisfy this course's Chrony service check.

| Machine | Required result |
|---|---|
| R2d2 | Chrony active, enabled and synchronized |
| node1, node2, node3 | The same checks on each node |

The existing lab already reported synchronized node clocks. For a clean Ubuntu installation, prepare the nodes explicitly using step 3 below.

## 2. Prepare R2d2

Run **on R2d2 as joe**, without putting sudo before the script:

```bash
cd ~/openstack-from-zero
/usr/bin/python3 scripts/phase1-chrony.py --apply
```

Enter joe's **R2d2 sudo password**. The script installs Chrony if missing, shows the package transaction, enables it and waits for synchronization. APT can replace `systemd-timesyncd`; it preserves existing Chrony configuration and does not choose a custom time server.

Expected:

```text
RESULT: R2D2_CHRONY_READY
```

Keep the printed private report. This script works on R2d2 only; it does not prepare the nodes.

## 3. Prepare each node

From R2d2, choose **one** connection:

| Node | Connect from R2d2 |
|---|---|
| node1 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.200` |
| node2 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.201` |
| node3 | `ssh -t -o IdentitiesOnly=yes -i ~/.ssh/openstack-from-zero joe@192.168.0.202` |

Inside that node's SSH session, confirm its name and read the current clock:

```bash
hostname -s
systemctl is-active chrony.service
systemctl is-enabled chrony.service
timedatectl show --property=NTPSynchronized --value
```

If the answers are **active**, **enabled** and **yes**, its time preparation is complete; skip the installation block. An absent/inactive service can return a nonzero exit code here.

Otherwise, run **on that node**:

```bash
sudo apt-get update
sudo apt-get --no-upgrade install chrony
sudo systemctl enable --now chrony.service
chronyc waitsync 24 0.1 0 2
```

Enter joe's password **for that node**. Review APT's transaction before answering Y. Replacing `systemd-timesyncd` is the explained time-client change; return any other proposed removal for review. Existing configuration is retained by choosing the existing local version if prompted.

`waitsync` can take about 48 seconds. A failed wait means synchronization has not been established; review the time-source output before continuing.

## 4. Verify and return to R2d2

Still on the node:

```bash
systemctl is-active chrony.service
systemctl is-enabled chrony.service
timedatectl show --property=NTPSynchronized --value
chronyc tracking
chronyc -n sources -v
```

Require **active**, **enabled**, **yes**, `Leap status : Normal`, and a selected source marked `*`, commonly `^*`. The selected time-server address can differ between machines.

Return to R2d2:

```bash
exit
```

Repeat steps 3–4 for the other nodes. These commands change time-service preparation only; they do not configure NICs, storage or OpenStack.

## 5. Finish check

- [ ] R2d2 returned `R2D2_CHRONY_READY`.
- [ ] node1: active, enabled and synchronized.
- [ ] node2: active, enabled and synchronized.
- [ ] node3: active, enabled and synchronized.

Keep these results for review before [Chapter H](H-nics-and-storage.md). Phase 2 F checks node time again before deployment preparation.

## 6. Reviewed result

Joe's R2d2 report from 23 October 2026 returned `R2D2_CHRONY_READY`; the node inspection also reported active, synchronized Chrony on all three nodes. These are recorded observations. A rebuilt machine must pass its own checks above.

## 7. Short lecture — what the result means

**Active** means Chrony runs now. **Enabled** means it is configured to start at boot. **Synchronized** means it has established time synchronization; running alone does not prove this.

[Ubuntu Chrony setup](https://ubuntu.com/server/docs/how-to/networking/serve-ntp-with-chrony/) · [Ubuntu 24.04 chronyc commands](https://manpages.ubuntu.com/manpages/noble/man1/chronyc.1.html)
