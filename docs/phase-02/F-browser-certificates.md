# Chapter F — Browser certificate guide

[Chapter F — run the checks first](F-validate-preparation.md) · [Phase 2](README.md)

**Purpose:** let your browser recognize the lab's HTTPS certificate when Horizon is deployed.

**Reviewed browser preparation:** Joe confirmed the public CA import into **Chrome on Windows 11**. The certificate was copied to `C:\Users\joe\lab-root.crt`, and Joe reported the Step 4 verification marker. Joe subsequently confirmed completing Linux Firefox setup. The chosen browser imports are user-confirmed. The Linux path, distribution and transfer route were not supplied; they are not inferred. Live HTTPS/API evidence belongs in [Phase 3 D](../phase-03/D-client-access.md); browser sign-in requires its own confirmation.

## Choose your browser path

| Where you are working | What to follow |
|---|---|
| **Windows 11 — Chrome** | **Import confirmed by Joe.** Keep the Windows steps below for rebuilding; no repeat is needed now. |
| **Linux — Firefox** | **Import confirmed by Joe.** [The separate Linux Firefox guide](F-linux-firefox.md) remains available for rebuilding. |
| Windows — Firefox | Section D below, if you later choose to use it. Import is unconfirmed. |
| Linux — Chrome | Section C below, if you later choose to use it. Import is unconfirmed. |

**You do not have to complete every browser/OS combination.** Use the browser you want to learn with. The Linux reference command below is not an extra step after Windows verification.

## A. Understand the file

| File on R2d2 | Purpose | Copy to browser computer? |
|---|---|---|
| `~/openstack-from-zero/private/phase2/kolla/certificates/ca/lab-root.crt` | Public CA certificate: tells the browser which lab issuer to trust | **Yes — this one file only** |
| `certificates/private/lab-root.key` | Private CA signing key | Keep private on R2d2 |
| `certificates/private/server.key` | Private server key | Keep private on R2d2 |
| `certificates/haproxy.pem` / `haproxy-internal.pem` | Server certificate combined with its private key | Keep private; do not import into browsers |

Trusting the CA means the browser accepts valid website certificates signed by it. Keep the CA signing key private. Importing a CA does not create the dashboard or assign the VIP.

## B. Transfer and verify the public certificate

1. Chapter F's R2d2 validation and CA trust passed and were reviewed.
2. Use the confirmed Windows route below to copy the public certificate. A route from a separate Linux computer is not yet confirmed.
3. Copy **only** the complete `lab-root.crt` file identified above. Keep that filename. Do not upload the private folder or private keys to GitHub.
4. Verify the certificate fingerprint on the receiving computer before importing it.

### Windows 11 laptop — confirmed route, ready-to-run transfer

The observed route is Windows VMnet8 `192.168.162.1` → R2d2 `192.168.162.131:22`. Joe's PowerShell test returned `TcpTestSucceeded: True`. R2d2's SSH socket is active and port 22 is listening. This confirms reachability, not a completed login or transfer. The VM address is DHCP; reconfirm it on a future rebuild.

**Step 1 — identify R2d2, in its VMware console.** This reads the public SSH host-key fingerprint. It changes nothing:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Keep the displayed `SHA256:...` fingerprint visible. It identifies the SSH server, and is separate from the lab CA certificate fingerprint below.

**Step 2 — check the Windows copy tool, in PowerShell.** This only locates the program:

```powershell
Get-Command scp -ErrorAction Stop
```

Continue if it displays the `scp` application. If it is unavailable, return that output before installing anything.

**Step 3 — copy one public file, in Windows PowerShell.** These commands save it directly in your Windows user folder, regardless of the current `C:\\Windows\\System32` prompt. They stop if that destination already exists, so nothing is overwritten.

```powershell
$labCaFile = Join-Path $env:USERPROFILE 'lab-root.crt'
if (Test-Path -LiteralPath $labCaFile) { throw "Destination already exists: $labCaFile. Stop and review it." }
scp -o HostKeyAlgorithms=ssh-ed25519 joe@192.168.162.131:/home/joe/openstack-from-zero/private/phase2/kolla/certificates/ca/lab-root.crt "$labCaFile"
```

On a first connection, compare the SSH prompt's `SHA256:...` fingerprint with Step 1. Enter `yes` **only when they match**. This saves R2d2's public host identity in Windows' SSH known-hosts file. If the fingerprint differs or a changed-host warning appears, stop and return the message.

If a password is requested, use **joe's Ubuntu login password on R2d2**. It is not your Windows password or the passphrase for the separate R2d2-to-nodes key. Password input is not displayed. This procedure does not copy that private SSH key.

If `scp` reports an error, return it before proceeding. A successful copy normally displays `100%`.

**Step 4 — verify the received certificate, in the same Windows PowerShell window.** These commands read the received file, compare its certificate SHA256 fingerprint to the reviewed lab CA and stop on a mismatch. They do not import trust:

```powershell
$labCertificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($labCaFile)
$labFingerprint = $labCertificate.GetCertHashString([System.Security.Cryptography.HashAlgorithmName]::SHA256)
$labFingerprint
$labExpectedFingerprint = (Read-Host 'Reviewed CA SHA256 from Phase 2 E').Trim()
if ($labExpectedFingerprint -notmatch '^[0-9A-Fa-f]{64}$') { throw 'Expected 64 hexadecimal characters.' }
if ($labFingerprint -ne $labExpectedFingerprint) { throw 'Certificate fingerprint mismatch. Stop before importing.' }
Write-Output "CA_CERTIFICATE_VERIFIED: $labCaFile"
```

Return the copy result and `CA_CERTIFICATE_VERIFIED` line for review. Chrome/Firefox imports in Sections C and D use this file. In a file picker, `%USERPROFILE%\\lab-root.crt` locates it.

For a fresh rebuild, use the new reviewed CA fingerprint in Step 4. For a separate Linux computer, confirm its route before choosing a transfer command.

### Optional reference — not another Windows step

**Windows Step 4 already verifies the Windows copy. If you are now setting up Linux Firefox, follow [the separate guide](F-linux-firefox.md), which places each command in context.**

**On a Linux browser computer with OpenSSL:** run this in the folder containing the downloaded public certificate. It reads the certificate and prints its SHA256 identity; it changes nothing.

```bash
openssl x509 -in lab-root.crt -noout -fingerprint -sha256
```

**On a Windows browser computer:** open PowerShell in the folder containing the downloaded public certificate. This also reads the certificate and prints its SHA256 identity; it does not import it.

```powershell
$labCertificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new((Resolve-Path .\lab-root.crt).Path)
$labCertificate.GetCertHashString([System.Security.Cryptography.HashAlgorithmName]::SHA256)
```

Compare with the current lab CA fingerprint below. Upper/lowercase and colon separators do not change the value. A file hash of the PEM text is a different measurement; compare the **certificate fingerprint** printed by these commands.

```text
27:8A:4E:6A:84:B7:61:F9:54:B9:BB:F1:F8:05:1C:35:60:B6:3F:0E:B0:73:EF:C9:E9:5D:8D:1C:69:DF:3C:BB
```

For an intentional fresh rebuild, use the newly reviewed Chapter E/F fingerprint instead. Stop if the received certificate does not match.

## C. Chrome — Windows and Linux

Perform this on each computer/profile where you intend to use Chrome for the lab. It adds trust in Chrome; it does not configure other applications.

1. Open Chrome and enter `chrome://certificate-manager` in the address bar.
2. Under **Custom**, open **Installed by you**, then the **Trusted Certificates** section.
3. Choose **Import** and select the verified `lab-root.crt`.
4. Check that the lab CA appears in the trusted list. Open its details and compare the SHA256 fingerprint with Section B.
5. Close and reopen Chrome.

This is Chrome's certificate manager for current Chrome (134 and later). Labels can vary by version or policy. If your screen does not offer this import, stop and tell me the Chrome version and visible labels before we choose another method.

## D. Firefox — Windows and Linux

**For the current Linux Firefox task, use [the dedicated guide](F-linux-firefox.md).** These general steps remain available for study and Windows Firefox.

This explicit Firefox import works independently of whether Firefox inherits Windows system trust. It avoids relying on Linux distribution-specific system integration.

1. Open Firefox **Settings**.
2. Search settings for **Certificates**, then choose **View Certificates**. Depending on the version, this is under **Privacy & Security**, sometimes within **Advanced settings**.
3. Select the **Authorities** tab and click **Import**.
4. Select the verified `lab-root.crt`.
5. Select trust to **identify websites**, then confirm. Email trust is not needed for Horizon.
6. Find the imported lab CA under Authorities. View its details and compare the SHA256 fingerprint with Section B.
7. Close and reopen Firefox.

Repeat for each Firefox profile/computer you plan to use. Do not import the private key or choose the **Your Certificates** client-identity tab.

## E. Finish checkpoint

Record which combinations you completed:

| Browser computer | Chrome CA imported | Firefox CA imported |
|---|---|---|
| Windows | **Import confirmed by Joe** | Unconfirmed — optional |
| Linux | Unconfirmed — optional | **Import confirmed by Joe** |

**After Phase 3 deploys Horizon**, we will test `https://192.168.0.151` from the chosen browser computers. We will confirm network reachability, certificate validity and the Horizon login page then. Keep certificate verification enabled; no warning bypass is part of this procedure.

## Short lecture — after the import is confirmed

A CA certificate is a public identity for an issuer. Importing it tells this browser to recognize certificates signed by that issuer. The matching private CA key is what can sign new certificates, so it stays private. Browser trust and network reachability are separate: a trusted certificate does not make a NAT VM or the future VIP reachable.

## Official references

- [Chrome Root Store and Certificate Manager](https://chromium.googlesource.com/chromium/src/+/main/net/data/ssl/chrome_root_store/faq.md)
- [Chrome certificate-manager interface labels](https://chromium.googlesource.com/chromium/src/+/lkgr/chrome/app/certificate_manager.grdp)
- [Mozilla: certificate trust settings and manual import](https://wiki.mozilla.org/CA/Changing_Trust_Settings)
- [Mozilla: operating-system CA integration](https://support.mozilla.org/en-US/kb/setting-certificate-authorities-firefox)

- [Microsoft: OpenSSH key-management and file-transfer tools](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
