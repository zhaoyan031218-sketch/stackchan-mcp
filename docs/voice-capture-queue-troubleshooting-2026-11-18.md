# Voice capture and deferred playback recovery

## Symptoms and investigation

The user reported unreliable speech on a CoreS3 connected to a cloud MCP
service. Review found two independent firmware defects that can affect voice
capture or queued speech. These findings are source and native-test evidence;
the physical microphone and speaker were unavailable for listening tests.

The pinned M5Unified 0.2.15 implementation of `Mic_Class::_rec_raw()` places a
destination pointer in an asynchronous recording queue and returns before the
samples are complete. `updateMicrophone()` previously read that destination
immediately and submitted the same buffer again on the next main-loop pass.
This allowed stale/partial samples to enter VAD and recordings, with a possible
blocking queue wait. The test stub had previously filled samples immediately,
which hid the asynchronous ownership problem.

Separately, WAV requests accepted while TCP/UDP PCM was running were queued,
but the playback service did not drain them when that external stream ended.
The pending queue could also prevent microphone resume. Calling the queue
processor during an active download popped and discarded later WAV requests;
a camera-busy result similarly discarded a queued PCM segment. Three native
regression tests reproduced these scheduling failures before the fix.

## Changes

- Keep one microphone frame pending. Poll `M5.Mic.isRecording()` without
  blocking, process the complete frame once, then submit its buffer again.
- Clear pending microphone state on an actual restart or completed suspension,
  and avoid queuing capture while the streaming audio path owns the hardware.
- Drain deferred playback from the main-loop playback update after stream or
  camera use ends. Retain queued items while download/audio hardware is busy.
- Wait for streams to finish before starting a completed WAV. A completed
  download waiting in its result queue does not trip the download watchdog.

No TTS model, voice settings, WAV/PCM format, network endpoint, or live-device
configuration was changed. Railway speech uses staged PCM, so the TCP/UDP
queue recovery is a separate compatibility fix, not proof of the reported
cloud symptom's exact cause.

## Verification

Native tests run against the real service implementation with hardware stubs:

- `test_mic_capture`: 4 passed; deferred completion, stop/resume, failed capture
  retry, and exclusion during PCM streams.
- `test_pcm_ownership`: 15 passed, including the three reproduced scheduling
  regressions and the existing disconnect/buffer-ownership cases.
- `test_booth_mode`: 21 passed; microphone privacy/suspension behavior retained.

These were compiled with host `g++ -std=gnu++17`, public configuration, the
repository test support headers, and Unity 2.6.1 downloaded into `work/unity`.
The normal project command is `cd firmware && pio test -e native`.
The final CoreS3 build and broader checks are recorded in the delivery report.

References: `firmware/src/mic_service.cpp`,
`firmware/src/playback_service.cpp`, the native suites above, and M5Unified
0.2.15 `src/utility/Mic_Class.cpp` / `Mic_Class.hpp`.
