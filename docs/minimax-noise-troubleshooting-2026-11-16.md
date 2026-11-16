# MiniMax playback accepted but audible output is noise

## Symptoms

The deployed Railway service accepted `stackchan_say` and returned
`Stack-chan is saying`, while the CoreS3 produced noise instead of speech.
Face commands already worked through the relay. The user confirmed a mainland
MiniMax account and `MINIMAX_MODEL_ID=speech-2.8-hd`. The baseline adapter was
introduced in commit `8021a1b`.

## Investigation and current hypothesis

- Firmware `playback_service.cpp` interprets the staged audio as signed 16-bit
  little-endian samples, mono, 24 kHz, through `M5.Speaker.playRaw`.
- The original adapter requested `format=pcm` and hex-decoded `data.audio`.
  Validation checked byte alignment and optional format/rate/channel metadata,
  but did not establish the actual sample width or sample representation.
- Existing tests supplied synthetic s16le bytes, so they could not detect a
  mismatch between real provider output and the speaker format.
- The official MiniMax MCP implementation supports MP3 in the same `/v1/t2a_v2`
  request and returns hex-encoded audio. Its default model is `speech-2.8-hd`;
  the model name alone is not evidence of a configuration error.
- MCP success proves acceptance of the request, not intelligible sound. Mic
  recording logs also cannot establish the format of synthesized speech.

The leading hypothesis is an audio format mismatch. **The original provider
response was not captured, so its bit depth and the actual cause of the noise
remain unconfirmed.** No paid API request or physical-device playback was made
from the development environment. Speaker configuration or a later playback
stage remains a possibility if the problem persists after this change.

## Change

Request MP3 at 24 kHz, mono, 128 kbps; decode with ffmpeg to explicit
`pcm_s16le`, 24 kHz, mono before any device upload. The Railway image already
contains ffmpeg. No environment-variable or firmware change is required.

The fixed MP3 demuxer and pipe-only protocol list avoid treating the response as
a playlist or URL. Decoding uses the remaining synthesis deadline. Output is
bounded to one second beyond the PCM size cap, then rejected if it exceeds that
cap; long audio is never silently clipped for playback. Missing ffmpeg fails
before contacting the paid API. Decoder errors do not expose source metadata,
trigger another synthesis, or start playback. Informational logs include only
input/output formats and byte counts.

## Verification

```sh
uv run --locked pytest tests/test_minimax_tts.py tests/test_minimax_audio_decode.py -q
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest --cov=mcp_server --cov=scripts --cov-report=term-missing
make test-mcp
```

The focused tests passed (56 tests). Real ffmpeg tests encode a one-second
440 Hz tone as MP3, supply it through a simulated MiniMax HTTP response and the
actual `stackchan_say` preparation path, and inspect the PCM passed to the relay.
They verify duration, RMS amplitude and concentration of signal energy at
440 Hz for both 24 kHz mono and 44.1 kHz stereo input. Invalid media, decoded
size limits, timeouts, and missing decoders are also covered. CI installs
ffmpeg so these codec tests run rather than skip.

The full Python suite passed (608 tests, 8 existing deprecation warnings),
`make test-mcp` passed (123 tests), and ruff, pyright and the repository security
audit passed. The Railway Docker image built successfully. Container smoke
checks passed for MCP authentication, a simulated WebSocket device round-trip
and reconnect, and clean shutdown. A separate real-codec check inside that
non-root image converted 16,763 MP3 bytes into 50,156 s16le bytes; the central
tone RMS was 5,374.2 and the 440 Hz energy ratio was 1.000000 (rounded).

Physical playback with the user's voice still needs confirmation after
deployment. If noise persists, collect that attempt's decode and serial playback
logs before changing model, gain, or firmware settings. Logs should exclude
credentials; do not repeatedly synthesize the same failing request.

## References

- [MiniMax official TTS implementation](https://github.com/MiniMax-AI/MiniMax-MCP/blob/2904f3333474cc020676b6c91676656bc4d03b3b/minimax_mcp/server.py)
- [MiniMax official model defaults](https://github.com/MiniMax-AI/MiniMax-MCP/blob/2904f3333474cc020676b6c91676656bc4d03b3b/minimax_mcp/const.py)
- [Voice setup and behavior](minimax-voice.md)
