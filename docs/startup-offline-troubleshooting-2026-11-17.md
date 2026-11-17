# CoreS3 offline immediately after boot

## Current evidence

The user confirmed CoreS3, no speech, and a Kelivo tool response of
`Relay HTTP 503: Device outbound channel is offline`. They further confirmed
the device is offline immediately after boot, before a camera request.

The selected workspace was clean at upstream `7673c4c`. The connected repository
also had Railway branch `385c690` and camera branch `1093695`. The latter includes
the earlier MiniMax decode fix and the bounded WebSocket binary writer. Neither
the physical device revision nor the deployed Railway revision was verified.

The reported response means the process handling that request had no live relay
peer. Possible causes include missing/old provisioning, Wi-Fi, clock/TLS,
device-token mismatch, the wrong service, or multiple Railway replicas. It
does not identify which cause applies. Earlier camera-upload history alone
does not explain a device that never connects after boot.

The configured Cloud egress policy blocked the supplied Railway host with
proxy CONNECT HTTP 403, before a request reached `/healthz`. No live service
health, authentication, camera capture, TTS synthesis or device action was tested.

## Recovery changes

- Preserve all existing checkout files and use an isolated copy of `1093695`.
- Add the explicit `m5stack-cores3-railway` target, requiring private configuration
  and nonempty enabled outbound settings. Public and direct-LAN targets remain
  supported. Runtime still validates token syntax and the CA certificate.
- Add a boot release label and bounded waiting-state logs for Wi-Fi, NTP and WSS.
  Do not enable library debug logs or print authorization/credential values.
- Provide a Windows rebuild helper that configures only when no private config
  exists, installs pinned PlatformIO in a separate venv, builds before uploading,
  and requires an explicit serial port for upload. Failed builds stop before
  upload even if an old binary exists.
- Preserve existing photo-transfer and server-side speech-format corrections.

This is a reproducible recovery path and improved diagnosis, not a confirmed
repair of the physical device. No destructive erase, secret rotation, Railway
deployment, or firmware upload was performed.

## Verification

Record actual commands and results in the delivery's `VALIDATION.zh-CN.md`.
The new regression checks compile the real configuration header against missing,
empty, disabled, public, short-token and valid settings, and verify that a failed
build cannot upload a stale binary. Existing camera/relay/audio tests validate
the inherited fixes without calling the physical device or a paid provider.

Live acceptance begins with reboot → sustained `stackchan_status` online → face
→ one photo → short speech. If offline persists, use the new boot serial logs
and matching Railway logs to determine the next fix.

See [clean rebuild instructions](../START-HERE.zh-CN.md),
[camera transport history](camera-relay-troubleshooting-2026-11-17.md), and
[speech format history](minimax-noise-troubleshooting-2026-11-16.md).
