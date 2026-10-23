# Chapter F — Browser certificate guide

[Chapter F — run the checks first](F-validate-preparation.md) · [Phase 2](README.md)

**Purpose:** let your browser recognize the lab's HTTPS certificate when Horizon is deployed.

**Current checkpoint:** Joe uses Chrome and Firefox on Windows and Linux. R2d2 is accessed through the VMware console. The public-certificate transfer route is not yet confirmed. These import steps are prepared for use **after the certificate file has been transferred and verified**. Live Horizon validation comes after deployment.

## A. Understand the file

| File on R2d2 | Purpose | Copy to browser computer? |
|---|---|---|
| `~/openstack-from-zero/private/phase2/kolla/certificates/ca/lab-root.crt` | Public CA certificate: tells the browser which lab issuer to trust | **Yes — this one file only** |
| `certificates/private/lab-root.key` | Private CA signing key | Keep private on R2d2 |
| `certificates/private/server.key` | Private server key | Keep private on R2d2 |
| `certificates/haproxy.pem` / `haproxy-internal.pem` | Server certificate combined with its private key | Keep private; do not import into browsers |

Trusting the CA means the browser accepts valid website certificates signed by it. Keep the CA signing key private. Importing a CA does not create the dashboard or assign the VIP.

## B. Transfer and verify the public certificate

1. Finish Chapter F's R2d2 validation and return its report.
2. Confirm a file-transfer route from R2d2 to your Windows/Linux computer. We have not established SSH access from those computers. The old NAT address is not assumed to be reachable.
3. Through that confirmed route, copy **only** the complete `lab-root.crt` file identified above. Keep that filename. Do not upload the private folder or private keys to GitHub.
4. Verify the certificate fingerprint on the receiving computer before importing it.

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
| Windows | Pending | Pending |
| Linux | Pending | Pending |

**After Phase 3 deploys Horizon**, we will test `https://192.168.0.151` from the chosen browser computers. We will confirm network reachability, certificate validity and the Horizon login page then. Keep certificate verification enabled; no warning bypass is part of this procedure.

## Short lecture — after the import is confirmed

A CA certificate is a public identity for an issuer. Importing it tells this browser to recognize certificates signed by that issuer. The matching private CA key is what can sign new certificates, so it stays private. Browser trust and network reachability are separate: a trusted certificate does not make a NAT VM or the future VIP reachable.

## Official references

- [Chrome Root Store and Certificate Manager](https://chromium.googlesource.com/chromium/src/+/main/net/data/ssl/chrome_root_store/faq.md)
- [Chrome certificate-manager interface labels](https://chromium.googlesource.com/chromium/src/+/lkgr/chrome/app/certificate_manager.grdp)
- [Mozilla: certificate trust settings and manual import](https://wiki.mozilla.org/CA/Changing_Trust_Settings)
- [Mozilla: operating-system CA integration](https://support.mozilla.org/en-US/kb/setting-certificate-authorities-firefox)
