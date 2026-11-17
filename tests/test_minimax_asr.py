import io
import json
import struct
import wave
from unittest.mock import Mock

import pytest
import requests
from test_mcp_server import make_config

from mcp_server import minimax_asr

SECRET = "private-test-asr-key"


def config(**changes):
    return make_config(**{"minimax_api_key": SECRET, **changes})


def wav_bytes(*, frames=1600, rate=16000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x01" * frames * channels * width)
    return output.getvalue()


class Response:
    def __init__(self, payload=None, *, status=200, content_type="application/json", chunks=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.chunks = chunks if chunks is not None else [json.dumps(
            {"text": "你好，小机器人", "duration": 0.1} if payload is None else payload,
        ).encode()]
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, chunk_size):
        assert chunk_size == 4096
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", Mock(
        side_effect=AssertionError("unexpected network"),
    ))
    monkeypatch.setattr(requests, "post", Mock(side_effect=AssertionError("unexpected ASR call")))
    monkeypatch.setattr(minimax_asr.time, "monotonic", lambda: 1000.0)


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "private-local-recording.wav"
    path.write_bytes(wav_bytes())
    return path


@pytest.mark.parametrize("host", [
    "https://api.minimaxi.com", "https://api.minimax.io", "https://api.minimax.cn",
])
def test_multipart_contract_preserves_recording_and_uses_language_header(monkeypatch, recording, host):
    response = Response()
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    result = minimax_asr.transcribe_audio(recording, "zh", config(minimax_api_host=host))
    assert result == {"text": "你好，小机器人", "duration": 0.1}
    assert "language" not in result
    args, kwargs = post.call_args
    assert args == (host + "/v1/speech_to_text",)
    assert kwargs["headers"] == {
        "Authorization": "Bearer " + SECRET, "Accept": "application/json", "language": "zh",
    }
    assert kwargs["data"] == {"model": "asr-1.0", "response_format": "json", "stream": "false"}
    assert kwargs["files"] == {"file": ("recording.wav", recording.read_bytes(), "audio/wav")}
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == (10.0, 30.0)
    # Let requests generate the multipart boundary; no local path or incorrect
    # JSON content type is forwarded as the uploaded filename.
    prepared = requests.Request("POST", args[0], headers=kwargs["headers"],
                                data=kwargs["data"], files=kwargs["files"]).prepare()
    assert prepared.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'filename="recording.wav"' in prepared.body
    assert b"private-local-recording" not in prepared.body
    post.assert_called_once()
    assert response.closed
    assert recording.exists()


@pytest.mark.parametrize("lang", ["", "auto", "AUTO"])
def test_automatic_language_omits_header(monkeypatch, recording, lang):
    post = Mock(return_value=Response())
    monkeypatch.setattr(requests, "post", post)
    minimax_asr.transcribe_audio(recording, lang, config())
    assert "language" not in post.call_args.kwargs["headers"]
    assert "language" not in post.call_args.kwargs["data"]


@pytest.mark.parametrize("lang", ["en", "zh-CN", "yue", "de-DE"])
def test_clean_language_tags_are_validated_without_network(lang):
    assert minimax_asr.configuration_error(config(), lang) is None
    requests.post.assert_not_called()


@pytest.mark.parametrize(("changes", "lang"), [
    ({"minimax_api_key": ""}, "zh"),
    ({"minimax_api_key": "   "}, "zh"),
    ({"minimax_api_key": SECRET + "\r\nprivate"}, "zh"),
    ({"minimax_api_host": "http://api.minimaxi.com"}, "zh"),
    ({"minimax_api_host": "https://api.minimaxi.com.evil.example"}, "zh"),
    ({"minimax_api_host": "https://private@api.minimaxi.com"}, "zh"),
    ({"minimax_api_host": "https://api.minimaxi.com/v1"}, "zh"),
    ({"minimax_asr_timeout": float("nan")}, "zh"),
    ({"minimax_asr_timeout": float("inf")}, "zh"),
    ({"minimax_asr_timeout": 0}, "zh"),
    ({"minimax_asr_timeout": -1}, "zh"),
    ({"minimax_asr_timeout": True}, "zh"),
    ({"minimax_asr_timeout": 10 ** 1000}, "zh"),
    ({}, "zh\r\nAuthorization: private"),
    ({}, "not_a_tag"),
    ({}, "x" * 64),
])
def test_preflight_failure_precedes_file_access_and_upload(tmp_path, changes, lang):
    cfg = config(**changes)
    error = minimax_asr.configuration_error(cfg, lang)
    assert error
    with pytest.raises(RuntimeError) as exc:
        minimax_asr.transcribe_audio(tmp_path / "missing-private-file.wav", lang, cfg)
    assert str(exc.value) == error
    assert SECRET not in str(exc.value)
    assert "private" not in str(exc.value)
    requests.post.assert_not_called()


def test_missing_recording_error_does_not_disclose_path(tmp_path):
    with pytest.raises(RuntimeError, match="could not be read") as exc:
        minimax_asr.transcribe_audio(tmp_path / SECRET, "zh", config())
    assert SECRET not in str(exc.value)
    requests.post.assert_not_called()


def test_wav_with_padded_metadata_chunk_is_valid(monkeypatch, recording):
    original = wav_bytes()
    audio = original[:12] + b"JUNK" + struct.pack("<I", 3) + b"abc\x00" + original[12:]
    audio = audio[:4] + struct.pack("<I", len(audio) - 8) + audio[8:]
    recording.write_bytes(audio)
    post = Mock(return_value=Response())
    monkeypatch.setattr(requests, "post", post)
    minimax_asr.transcribe_audio(recording, "zh", config())
    assert post.call_args.kwargs["files"]["file"][1] == audio


@pytest.mark.parametrize("audio", [
    b"ID3" + b"private-compressed-data", b"\x00" * 200, wav_bytes()[:-2],
    wav_bytes() + b"private-trailing-data", wav_bytes(frames=0),
    wav_bytes(rate=1), wav_bytes(channels=3),
    wav_bytes()[:20] + struct.pack("<H", 3) + wav_bytes()[22:],  # float codec, not PCM
    wav_bytes()[:32] + struct.pack("<H", 0) + wav_bytes()[34:],  # invalid frame alignment
    wav_bytes()[:28] + struct.pack("<I", 0) + wav_bytes()[32:],  # invalid byte rate
    wav_bytes()[:40] + struct.pack("<I", 3199) + wav_bytes()[44:],  # partial sample
    wav_bytes()[:16] + struct.pack("<I", 2 ** 32 - 1) + wav_bytes()[20:],
])
def test_invalid_or_truncated_media_never_reaches_provider(recording, audio):
    recording.write_bytes(audio)
    with pytest.raises(RuntimeError, match="PCM WAV"):
        minimax_asr.transcribe_audio(recording, "zh", config())
    requests.post.assert_not_called()


def test_provider_file_size_limit_is_checked_before_reading_or_upload(recording):
    with recording.open("wb") as source:
        source.truncate(minimax_asr.MAX_AUDIO_BYTES + 1)
    with pytest.raises(RuntimeError, match="50 MB"):
        minimax_asr.transcribe_audio(recording, "zh", config())
    requests.post.assert_not_called()


def test_provider_duration_limit_rejects_complete_oversized_wav(recording):
    recording.write_bytes(wav_bytes(rate=8000, frames=500 * 8000 + 1))
    with pytest.raises(RuntimeError, match="500 second"):
        minimax_asr.transcribe_audio(recording, "zh", config())
    requests.post.assert_not_called()


@pytest.mark.parametrize("status", [301, 302, 401, 403, 413, 429, 500])
def test_http_errors_are_redacted_closed_and_not_retried(monkeypatch, recording, status):
    response = Response({"message": SECRET}, status=status)
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(RuntimeError, match=f"HTTP {status}") as exc:
        minimax_asr.transcribe_audio(recording, "zh", config())
    assert SECRET not in str(exc.value)
    assert response.closed
    post.assert_called_once()


@pytest.mark.parametrize("payload", [
    [], {}, {"text": "hello"}, {"duration": 1}, {"text": None, "duration": 1},
    {"text": "hello", "duration": "1"}, {"text": "hello", "duration": True},
    {"text": "hello", "duration": -1}, {"text": "hello", "duration": 501},
    {"text": "hello", "duration": float("nan")}, {"text": "hello", "duration": float("inf")},
    {"text": "hello", "duration": 10 ** 1000}, {"text": "hello\x00", "duration": 1},
    {"text": "\ud800", "duration": 1}, {"text": "x" * 32001, "duration": 1},
    {"text": "hello", "duration": 1, "base_resp": None},
    {"text": "hello", "duration": 1, "base_resp": {"status_code": False}},
    {"text": "hello", "duration": 1, "base_resp": {"status_code": "0"}},
])
def test_invalid_response_schema_fails_safely(monkeypatch, recording, payload):
    response = Response(payload)
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="invalid transcription"):
        minimax_asr.transcribe_audio(recording, "zh", config())
    assert response.closed


@pytest.mark.parametrize("base", [None, {"status_code": 0, "status_msg": SECRET}])
def test_empty_transcript_and_optional_success_status_are_accepted(monkeypatch, recording, base):
    payload = {"text": "", "duration": 0.1, "trace_id": SECRET, "language": "invented"}
    if base is not None:
        payload["base_resp"] = base
    monkeypatch.setattr(requests, "post", Mock(return_value=Response(payload)))
    assert minimax_asr.transcribe_audio(recording, "zh", config()) == {"text": "", "duration": 0.1}


def test_provider_api_error_never_leaks_body_or_claims_success(monkeypatch, recording):
    response = Response({"text": "hello", "duration": 1,
                         "base_resp": {"status_code": 1008, "status_msg": SECRET}})
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(RuntimeError, match="API returned an error") as exc:
        minimax_asr.transcribe_audio(recording, "zh", config())
    assert SECRET not in str(exc.value)
    post.assert_called_once()


@pytest.mark.parametrize("response,match", [
    (Response(content_type="text/html"), "non-JSON"),
    (Response(chunks=[b"private invalid JSON"]), "invalid transcription"),
    (Response(chunks=[b"\xff"]), "invalid transcription"),
    (Response(chunks=[b'{"text":"hello","duration":1,"extra":' + b"[" * 16000
                      + b"0" + b"]" * 16000 + b"}"]), "invalid transcription"),
    (Response(chunks=[b"x" * (64 * 1024 + 1)]), "size limit"),
])
def test_bounded_response_validation(monkeypatch, recording, response, match):
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(RuntimeError, match=match) as exc:
        minimax_asr.transcribe_audio(recording, "zh", config())
    assert "private" not in str(exc.value)
    assert response.closed


@pytest.mark.parametrize("failure", [requests.Timeout(SECRET), requests.ConnectionError(SECRET),
                                     requests.exceptions.InvalidHeader(SECRET)])
@pytest.mark.parametrize("during_body", [False, True])
def test_transport_failures_do_not_leak_credentials_or_retry(monkeypatch, recording, failure, during_body):
    response = Response(chunks=[failure])
    post = Mock(return_value=response) if during_body else Mock(side_effect=failure)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(RuntimeError) as exc:
        minimax_asr.transcribe_audio(recording, "zh", config())
    assert SECRET not in str(exc.value)
    assert "timed out" in str(exc.value) or "transport failed" in str(exc.value)
    post.assert_called_once()
    if during_body:
        assert response.closed


@pytest.mark.parametrize("clock", [
    [1000.0, 1031.0],  # local validation consumed the budget: no upload
    [1000.0, 1000.0, 1031.0],  # response chunk arrives after deadline
    [1000.0, 1000.0, 1029.0, 1031.0],  # EOF after an otherwise valid last chunk
])
def test_elapsed_deadline_never_returns_transcription(monkeypatch, recording, clock):
    post = Mock(return_value=Response())
    monkeypatch.setattr(requests, "post", post)
    ticks = iter(clock)
    monkeypatch.setattr(minimax_asr.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="deadline exceeded"):
        minimax_asr.transcribe_audio(recording, "zh", config())
    assert post.call_count == (0 if len(clock) == 2 else 1)
