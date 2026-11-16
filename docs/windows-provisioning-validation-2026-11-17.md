# Windows provisioning helper validation — 2026-11-17

The CoreS3 requires a provisioned outbound hostname, device token and PEM trust
anchor. Guessing a hosting provider's certificate issuer can select the wrong
CA; copying credentials manually into C++ can also break quoting or overwrite
an existing private configuration.

The helper resolves these problems on the user's Windows computer. A .NET
`SslStream` authenticates the requested hostname using the operating system's
trust store. Its callback accepts only `SslPolicyErrors.None` and exports the
last certificate in the verified chain. Python parses that PEM, validates the
Wi-Fi/token inputs, escapes C++ string literals and exclusively creates the
ignored `firmware/src/config.h`. Existing settings are never overwritten. No
TLS validation bypass or device token is stored in source control.

Validation results:

- CoreS3 public firmware build: passed with PlatformIO 6.1.18, `-j 4`;
  RAM 103028/327680 bytes, flash 1476329/6553600 bytes. This image intentionally
  ignores private settings and is not the image to upload to a configured body.
- Full Python suite: 552 passed, eight dependency deprecation warnings.
- The 14 provisioning tests cover existing-file preservation, invalid inputs,
  changed templates, invalid PEM, and compiled C++ literals containing Unicode,
  quotes, backslashes and certificate newlines.
- Ruff and Pyright: passed.
- The PowerShell/C# helper was exercised in PowerShell 7.4.6 on Linux against an
  isolated local TLS server: a trusted matching certificate returned exactly
  the expected root; wrong-hostname and untrusted-chain handshakes failed.
- The PowerShell runtime was downloaded from its official GitHub release and
  verified against the release's SHA-256 manifest. The MCR test-image download
  was unavailable, so the verified runtime was used in a disposable container.

The helper's Windows trust-store integration, the actual Railway certificate,
local USB upload and physical device behavior still require execution on the
user's Windows computer. No device was flashed from the cloud environment.

See [Windows robot setup](windows-robot-setup.md) for the exact local workflow.
