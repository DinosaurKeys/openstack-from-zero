# Chapter F — Firefox on your Linux computer

[Browser guide](F-browser-certificates.md) · [Phase 2](README.md)

**Goal:** let Firefox on your Linux computer recognize the lab CA.

**Status: Joe confirmed completing this Linux Firefox procedure.** The Windows Chrome import is also user-confirmed. The steps below remain for study and rebuilding. The Linux computer's exact path, distribution and transfer route were not reported and are not assumed. Live Horizon HTTPS is still pending deployment.

## A. Where to work

Work on **the Linux computer where you open Firefox**. Do not run these steps in Windows PowerShell or on R2d2 merely because R2d2 runs Ubuntu.

The Linux computer's name, distribution and transfer route are not yet confirmed. No Linux SCP command or system trust-store change is assumed.

## B. Find the certificate — do this first

1. Open the Linux computer's **file manager**.
2. Search your **Home folder** for the exact filename `lab-root.crt`. The file manager's name and search controls depend on your Linux desktop.
3. If you find it, return its **full path**. If multiple files appear, return the paths before choosing one.
4. If it is not found, stop and tell me **which Linux computer runs Firefox**. We will confirm a way to copy the public file there.

**On a rebuild, stop here if the local file is not yet known.** Verification and import below apply after you have located and checked the correct file. Joe has already reported completing the current setup.

The source public certificate remains on R2d2 at:

```text
/home/joe/openstack-from-zero/private/phase2/kolla/certificates/ca/lab-root.crt
```

There is also the verified Windows copy at `C:\Users\joe\lab-root.crt`. Do not copy private keys, HAProxy PEM files or the whole private directory.

## C. Verify the local certificate — after its location is confirmed

**Purpose:** check that the Linux copy is the same CA reviewed in Chapter E/F.

Open a terminal **in the folder containing the confirmed `lab-root.crt` file**. This matters: the command uses the filename in the terminal's current folder.

If OpenSSL is available, run:

```bash
openssl x509 -in lab-root.crt -noout -fingerprint -sha256
```

It reads the certificate and prints its identity. It changes no settings and does not import trust. If the program is unavailable, return that observation before installing anything.

The expected certificate SHA256 fingerprint is:

```text
27:8A:4E:6A:84:B7:61:F9:54:B9:BB:F1:F8:05:1C:35:60:B6:3F:0E:B0:73:EF:C9:E9:5D:8D:1C:69:DF:3C:BB
```

Upper/lowercase and colon separators do not change the value. Stop if it differs. A fresh rebuilt lab uses its newly reviewed CA fingerprint.

## D. Import into Linux Firefox — after the fingerprint matches

**Change:** this adds trust for the lab CA to the Firefox profile you are using. It does not install the CA into the whole Linux operating system.

1. Open **Firefox on the Linux computer**.
2. Open **Settings**, then search settings for **Certificates**.
3. Click **View Certificates**. It is under **Privacy & Security**; some versions place the certificate controls inside **Advanced settings**.
4. Select the **Authorities** tab.
5. Click **Import** and choose the verified local `lab-root.crt` file from Section B.
6. Select **Trust this CA to identify websites**, then click **OK**. Email trust is not needed for Horizon.
7. Find the imported lab CA under **Authorities**. View its details and compare its SHA256 fingerprint with Section C.
8. Close and reopen Firefox. Report that the CA is listed.

Use **Authorities**, not the **Your Certificates** tab. If labels differ, return the Firefox version and visible options before choosing a different action.

## E. Finish check

**Current lab: Joe reported the procedure completed, so the Firefox import is recorded as user-confirmed.** This is not an independent browser inspection or live HTTPS test. The checklist below remains for a future rebuild.

- [ ] Correct Linux computer and local certificate path confirmed.
- [ ] Local certificate fingerprint matches the reviewed lab CA.
- [ ] CA appears in Linux Firefox's Authorities list with website trust.
- [ ] Live Horizon HTTPS tested later, after deployment.

No Horizon page is expected yet: OpenStack has not been deployed.

## F. Short lecture — after import is confirmed

The Linux file is the public CA certificate. Importing it tells this Firefox profile to recognize website certificates signed by that CA. The private signing key stays private on R2d2. Each computer/browser profile has its own trust settings; importing on Windows Chrome does not import on Linux Firefox.

[Mozilla — manually import a root CA](https://wiki.mozilla.org/CA/Changing_Trust_Settings)
