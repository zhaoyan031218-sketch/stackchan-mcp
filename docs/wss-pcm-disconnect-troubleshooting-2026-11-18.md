# First PCM upload disconnect: receive-idle timeout and bounded fix

## Reported behavior

After flashing `recovery-20261118.1`, a face command succeeded. The first staged
PCM request (`seq=0`, `final=0`) then returned relay HTTP 503 with “Device
disconnected; command was not retried”. An immediately following sensor request
also returned 503. A separate sensor request after reboot returned 500 and is
an independent sensor-read failure; the firmware's `/env` handler does not
produce 503.

No Railway endpoint was accessed during this investigation. The first revision
added diagnostics because the original 503 alone did not identify the cause.
The user's subsequent `recovery-20261118.2` trace identifies the early-close
mechanism. The source of the underlying network/TLS pause remains unknown.

## Clarified working baseline and rollback behavior

The user subsequently clarified that the cloud commit `9f4cad9` was unchanged:
MiniMax speech worked before reflashing, then failed after the firmware changes.
Camera and listening had not worked previously. Sensor, camera and audio-status
503 results were collected consecutively after the speech disconnect, so those
results do not establish three independent failures. Preserve the cloud version
while investigating the firmware regression. A proposed cloud small-segment
experiment was stopped before any source edit, commit, push or deployment.

The likely older download name is
`stackchan-mcp-codex-railway-deployment.zip`. That is a GitHub branch source
archive, not an immutable firmware-image version. Its filename alone does not
identify the compiled binary, local configuration or complete dependency set.
The source at `385c690` used private compile-time `src/config.h` for outbound
credentials and the `wifi` NVS namespace for portal Wi-Fi settings. The recovery
runtime image instead uses provisioned `sc-cloud/config`; old firmware does not
read that blob. Rebuilding an old archive without its private configuration, or
erasing device configuration, can therefore leave it offline. Deleting files on
the computer alone does not erase device NVS. The user's previous offline
rollback has no boot trace, so its exact cause remains unconfirmed.

Source comparison found that the two-second receive timeout, PSRAM allocation
and frame cap predate the recovery firmware. The receive patch is the same git
blob in `385c690`, `1093695` and `ced673c`. Main-loop ordering, WSS task core,
priority and stack, TLS settings and direct dependency pins also remain the
same. The new binary writer affects outgoing camera/audio replies, not this
incoming first PCM segment. Microphone handling now waits for asynchronous
completion and introduces no new busy loop; playback changes occur after the
receive stage that fails in the supplied trace.

Runtime provisioning does add roughly 24 KiB of persistent internal buffers
and changes configuration selection. Those are real differences, but the
observed allocation succeeds and remaining heap is substantial. They are not
proven causes. Release .2 diagnostics also cannot explain the first failure,
which already occurred on .1. Thus .3 targets the recorded timeout; it is not
proof that the earlier firmware introduced that timeout or that the complete
regression cause has been identified.

## Device evidence from recovery-20261118.2

| Event | Command age | Expected / received bytes | Deadline remaining |
| --- | ---: | ---: | ---: |
| `rx_envelope` | 0 ms | 49,152 PCM bytes / 235 metadata bytes | 19,800 ms |
| `frame_begin` | 4,713 ms | 49,188 / 0 | 15,088 ms |
| `rx_timeout` | 7,015 ms | 49,188 / 13,900 | 12,785 ms |
| `library_close` | 7,016 ms | close code 1002 | 12,784 ms |
| `channel_disconnected` | 7,021 ms | — | 12,779 ms |

`validity=current` persists throughout. Heap is about 101 KB, PSRAM about
7.8 MB and stack watermark 6,036 at the timeout. There is no allocation failure,
dispatch or reboot event in the supplied trace. The channel reconnects after
the local close. The generic host/token troubleshooting message therefore
does not diagnose an authentication failure here.

The library aborts its synchronous receive after two seconds without positive
application-byte progress. It has received only part of the first staged PCM
frame, so the playback handler has not run. The relay then correctly reports
503 without retrying the command. The 20-second command deadline has not expired.
The old outer five-second binary-wait check is another too-short bound: it can
reject a later-starting frame or continuation even if the library wait improves.

`WiFiClientSecure.available()` exposes decrypted TLS-record bytes. These logs
do not prove that no underlying TCP packets arrived during the pause. Server
code buffers the segment before dispatch and sends metadata then binary with
no intentional delay; synthesis has already happened. This evidence does not
distinguish proxy, routing, radio, TLS buffering or scheduling delays. No Wi-Fi
power-save or server deployment setting was changed speculatively.

## Focused fix in recovery-20261118.3

- While a valid command awaits binary input, allow up to eight seconds without
  positive receive progress, including its frame headers and continuations.
- Start this idle budget with the accepted envelope and refresh it only when
  bytes arrive. Use it for the outer binary wait as well as the library read.
- Check existing command validity throughout synchronous reads and before
  accepting the completed read: the monotonic 20-second limit, relay expiry,
  connection and generation all remain binding. Drip-fed traffic cannot extend
  those limits.
- Retain original connection, handshake, text-only and write timeout behavior.
  Text-fragment assembly retains its existing bound.
- Keep fixed, numeric diagnostics and do not replay failed commands.

The wire format and 48 KiB segment size remain compatible with the deployed
`9f4cad9` server. This fix needs a firmware upgrade only. Eight seconds is a
bounded tolerance for the observed pause, not a guarantee that any connection
will complete within the unchanged command deadline.

## Evidence and excluded explanations

- The pinned WebSockets 2.7.2 source and its compiled runtime object both use
  the project's PSRAM receive allocation. A 48 KiB PCM segment plus its
  36-byte `SCB1` header is 49,188 bytes, below the 131,108-byte receive limit.
- The receive path places the payload on the heap, not on the task stack.
  The reviewed receive callback stack frames are small. This does not measure
  the complete runtime TLS call stack or rule out memory exhaustion.
- The original library's 2-second receive timeout resets after positive
  progress; it is an inactivity timeout, not a whole-frame transfer deadline.
- First staged PCM does not operate the speaker. It allocates the staging
  buffer, copies PCM, and returns 202. A host ASan/UBSan probe of the actual
  playback implementation passed first 48 KiB staging and 32 successive
  segments (1.5 MiB), including growth and final ownership transfer.
- Installed Arduino board settings explicitly use CoreS3 `qio_qspi`, matching
  the build. There is no evidence here to change the PSRAM mode.

The pre-diagnostic firmware closed its channel for multiple validation,
allocation and timeout branches with only a generic disconnect log. The .2
diagnostics exposed the timeout; .3 changes the receive policy as described above.

## New evidence recorded

The firmware records fixed event labels and numeric values only. It does not
enable WebSockets debug output or log authorization, request paths, IDs,
sessions, audio, JSON bodies, or peer-supplied reason text.

| Event | Interpretation |
| --- | --- |
| `rx_envelope` | Request metadata parsed; `expected` is PCM bytes without the 36-byte header |
| `frame_begin` / `frame_complete` | Binary/continuation or large WebSocket frame reception started/completed |
| `rx_timeout`, `rx_no_tcp`, `rx_disconnected` | Library receive stopped; expected and completed byte counts identify progress |
| `rx_deadline` | Binary receive stopped because the command expired or its connection/generation became invalid |
| `rx_alloc_failed`, `binary_allocation_failed` | Library frame or application PCM allocation failed |
| `binary_wrong_phase`, `binary_not_current`, `binary_invalid_envelope` | Application rejected a complete binary message |
| `library_close`, `peer_close` | Numeric WebSocket close code, including 1002, 1009 or 1011; no reason text |
| `rx_binary_accepted` | Complete application frame passed validation and was copied for main-loop dispatch |
| `dispatch_begin` / `dispatch_end` | Main-loop handler entered/returned; end `code` is the HTTP result such as 202 |
| `command_not_current`, `binary_wait_timeout`, `fragment_wait_timeout` | Application lifetime check rejected pending work |

Each transport event includes heap/PSRAM availability, largest internal/PSRAM
allocation blocks, the current task's raw stack watermark, command age and
time remaining before its absolute deadline. `validity` separates connection,
generation, monotonic-age and wall-clock failures. Phase values are 0 idle,
1 receiving binary, 2 queued, 3 running and 4 ready to reply.

The pinned middleware validates the dependency version and all patch anchors
before writing either local library file. Receive allocation and frame-size
limits remain unchanged.

## User-confirmed result on recovery-20261118.3

With the cloud version unchanged, the user reported audible success and supplied
a complete staged-playback trace. Three segments of 49,152, 49,152 and 33,118
PCM bytes were received and accepted. The first two returned 202; the final one
returned 200 after `Speaker started`, followed by `Session complete`. Total
audio was 131,422 bytes. No timeout or reconnect appears in this supplied run.

Envelope-to-frame-complete ages were 5,317, 7,170 and 3,299 ms. These total times
must not be confused with the old two-second no-progress timeout. The result
confirms that the bounded receive change restored this speech test, while the
underlying source of network/TLS pauses and the complete historical regression
provenance remain unresolved. The following separate camera test also completed;
see `VALIDATION.zh-CN.md`. No additional firmware or cloud change was made for
that test.

## Verification and remaining work

The new native diagnostic tests pass, including a boundary matrix that compares
classified admission with the previous `commandIsCurrent()` rules across
timer wrap, generation changes, missing clocks and deadline limits. The two
large staged-PCM scratch probes pass under ASan and UBSan. The release build,
middleware tests and broader gates are recorded with the final delivery.

Final checks and reproducible commands are recorded in `VALIDATION.zh-CN.md`.
On hardware, run the read-only monitor on COM7 and request one short phrase.
Successful transport should reach `frame_complete`, `rx_binary_accepted` and
`dispatch_end` code 202 for a non-final staged segment. A later speaker problem
would require separate evidence. Software regression and a successful build
do not prove that the user's actual Wi-Fi/TLS path now completes in time.
