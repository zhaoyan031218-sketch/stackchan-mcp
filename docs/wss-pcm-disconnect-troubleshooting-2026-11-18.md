# First PCM upload disconnect: bounded device diagnostics

## Reported behavior

After flashing `recovery-20261118.1`, a face command succeeded. The first staged
PCM request (`seq=0`, `final=0`) then returned relay HTTP 503 with “Device
disconnected; command was not retried”. An immediately following sensor request
also returned 503. A separate sensor request after reboot returned 500 and is
an independent sensor-read failure; the firmware's `/env` handler does not
produce 503.

No Railway endpoint was accessed during this investigation. The physical
device's reset reason, receive progress and heap state were not available.
The audio disconnect root cause remains unconfirmed.

## Evidence and excluded explanations

- The pinned WebSockets 2.7.2 source and its compiled runtime object both use
  the project's PSRAM receive allocation. A 48 KiB PCM segment plus its
  36-byte `SCB1` header is 49,188 bytes, below the 131,108-byte receive limit.
- The receive path places the payload on the heap, not on the task stack.
  The reviewed receive callback stack frames are small. This does not measure
  the complete runtime TLS call stack or rule out memory exhaustion.
- The library's 2-second receive timeout resets after positive progress; it is
  an inactivity timeout, not a whole-frame transfer deadline. Application
  command and binary-wait deadlines remain unchanged.
- First staged PCM does not operate the speaker. It allocates the staging
  buffer, copies PCM, and returns 202. A host ASan/UBSan probe of the actual
  playback implementation passed first 48 KiB staging and 32 successive
  segments (1.5 MiB), including growth and final ownership transfer.
- Installed Arduino board settings explicitly use CoreS3 `qio_qspi`, matching
  the build. There is no evidence here to change the PSRAM mode.

The previous firmware closed its channel for multiple validation, allocation,
and timeout branches with only a generic disconnect log. That missing evidence
is what this change addresses; it does not tune frame sizes or timeouts.

## New evidence recorded

The firmware records fixed event labels and numeric values only. It does not
enable WebSockets debug output or log authorization, request paths, IDs,
sessions, audio, JSON bodies, or peer-supplied reason text.

| Event | Interpretation |
| --- | --- |
| `rx_envelope` | Request metadata parsed; `expected` is PCM bytes without the 36-byte header |
| `frame_begin` / `frame_complete` | Binary/continuation or large WebSocket frame reception started/completed |
| `rx_timeout`, `rx_no_tcp`, `rx_disconnected` | Library receive stopped; expected and completed byte counts identify progress |
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
before writing either local library file. Receive allocation/size behavior is
unchanged; its new hooks only emit the bounded diagnostics described above.

## Verification and remaining work

The new native diagnostic tests pass, including a boundary matrix that compares
classified admission with the previous `commandIsCurrent()` rules across
timer wrap, generation changes, missing clocks and deadline limits. The two
large staged-PCM scratch probes pass under ASan and UBSan. The release build,
middleware tests and broader gates are recorded with the final delivery.

On hardware, capture boot/reset output, run one short speech request, then
inspect the first fixed error event. A 202 dispatch result without later
playback differs from a receive failure before dispatch. A new boot/reset log
differs from a socket reconnect. Do not identify a network, memory, speaker or
sensor cause until those device observations support it.
