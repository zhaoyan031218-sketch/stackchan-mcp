# MiniMax transcription for a working CoreS3

## Symptom and evidence

The user verified status, face, four movement commands, audible MiniMax speech
and camera delivery on recovery firmware `.3`. `stackchan_sense` returned no
available environment readings. `stackchan_listen` returned:

```text
Fish Audio key is not configured; set FISH_AUDIO_KEY before consuming audio.
```

In the deployed `9f4cad9` code, that exact result occurs only after the device
reports a ready recording. The tool has not yet fetched `/audio` or called an
ASR provider. The user has a working MiniMax account and requested MiniMax
transcription instead of opening a Fish account. This is a cloud provider
integration task, not a reason to flash another firmware image.

## Official contract checked

The current MiniMax documentation and official CLI support `POST
/v1/speech_to_text` with Bearer authentication. The multipart body contains
`model=asr-1.0`, a containerized audio file and `response_format=json`.
The optional language hint belongs in an HTTP header, not a form field.
The basic JSON result contains `text` and `duration`; a detected-language field
is not promised. WAV input is supported, raw PCM is not. Published limits are
50 MB and 500 seconds. No paid API call was made during implementation.

Earlier MiniMax MCP versions only exposed speech generation. Their older tool
list is not evidence that current MiniMax lacks transcription. The official
ASR documentation and newer CLI were checked directly before implementation.

## Change

`STACKCHAN_ASR_ENGINE=auto` is the default. It selects MiniMax when the configured
TTS engine is MiniMax and Fish otherwise. Explicit `minimax` or `fish-audio`
selects independently. The MiniMax adapter reuses the existing official host
and API key; no additional account, new secret or firmware update is required.
The ASR model is fixed independently of the working TTS model and voice.

The shared recording and upload paths validate the selected provider before
consuming or accepting audio. ASR itself checks the input and provider response,
retains TLS verification and disables redirects. Errors do not return raw
provider response bodies or authorization values. Failed requests do not retry
or switch providers. Once a ready device recording has been fetched, subsequent
provider errors do not restore that recording on the device; this preserves the
existing consuming-read contract.

The existing TTS implementation, PCM transport, camera handling and firmware
are unchanged by this patch. Tests simulate providers and device replies;
account permission, balance, recording quality and a real transcript require
the user's subsequent `stackchan_listen` check after deployment.

## Verification

Targeted tests cover multipart file upload and header language placement,
response validation, timeout/error handling, configuration rejection before
recording consumption, automatic provider selection and explicit overrides.
Existing Fish and MiniMax playback tests continue to pass. Final checks:

- Full Python suite: 753 passed, with nine existing dependency warnings.
- `make test-mcp`: 123 passed; Ruff and Pyright: clean.
- MiniMax adapter tests: 84 passed, including configuration/media/response and
  transport failure boundaries. Integration tests exercise the actual adapter
  with a generated 16 kHz mono WAV and a mocked provider response.
- Container built and ran as non-root UID 10001 with networking disabled. The
  installed package selected MiniMax without a Fish key, preserved all 3,244 WAV
  bytes through the multipart request, and returned the simulated transcript.
- The managed development network's CA was supplied only as a temporary
  BuildKit secret for dependency downloads. The first unmodified local build
  stopped on an unknown certificate issuer; it did not expose an application
  failure. The trusted CA was not copied into the final image, and the tracked
  Dockerfile, dependency pins and TLS verification were unchanged.

Commands used the existing pinned Python environment:

```sh
python -m pytest --cov=mcp_server --cov=scripts --cov-report=term-missing
python -m ruff check .
python -m pyright
make test-mcp
python scripts/ci_security_audit.py
```

The cloud runtime image and automated tests do not establish that this user's
account has ASR permission or that a real recording will transcribe correctly.
That remaining check requires one user-initiated listen call after deployment.

## References

- [MiniMax ASR documentation](https://platform.minimax.cn/docs/api-reference/speech-to-text)
- [MiniMax official API console](https://solutions.minimaxi.com/debug/asr)
- [Official CLI speech client, pinned source](https://github.com/MiniMax-AI/cli/blob/ae29aae57b05c2140c7ea6aba0a852763ffe7859/src/sdk/speech/index.ts)
- [Official CLI response types, pinned source](https://github.com/MiniMax-AI/cli/blob/ae29aae57b05c2140c7ea6aba0a852763ffe7859/src/types/api.ts)
