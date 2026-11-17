import io
import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from test_mcp_server import make_config, stackchan_client_double

from mcp_server import audio_processing, mcp_tools, minimax_asr, stackchan_config
from mcp_server.listening import capture_ready_recording, format_listen_result
from scripts import stackchan_voice_upload_server


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "post", Mock(side_effect=AssertionError("unexpected network")))
    monkeypatch.setenv("STACKCHAN_OTEL_LOG", str(tmp_path / "otel.jsonl"))


def minimax_config(**changes):
    return make_config(**{
        "tts_engine": "minimax", "fish_audio_key": "", "minimax_api_key": "private-test-key",
        **changes,
    })


def ready_client():
    return Mock(
        audio_status=Mock(return_value={"ready": True, "mode": "mcp"}),
        get_audio=Mock(return_value=b"RIFF-test-wav"),
    )


def test_existing_minimax_environment_listens_without_fish_or_asr_settings(monkeypatch, tmp_path):
    for name in os.environ:
        if name.startswith(("STACKCHAN_", "FISH_AUDIO_", "MINIMAX_")) or name == "TTS_ENGINE":
            monkeypatch.delenv(name)
    monkeypatch.setattr(stackchan_config, "load_dotenv", lambda: None)
    monkeypatch.setenv("STACKCHAN_TRANSPORT", "relay")
    monkeypatch.setenv("TTS_ENGINE", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "private-existing-minimax-key")
    config = stackchan_config.load_config()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\x01\x00" * 20000)
    audio_bytes = buffer.getvalue()
    response = Mock(
        status_code=200,
        headers={"Content-Type": "application/json"},
        iter_content=Mock(return_value=[json.dumps({"text": "你好，我在这里", "duration": 1.25}).encode()]),
    )
    post = Mock(return_value=Mock(
        __enter__=Mock(return_value=response), __exit__=Mock(return_value=False),
    ))
    monkeypatch.setattr(requests, "post", post)
    client = ready_client()
    client.get_audio.return_value = audio_bytes

    result = capture_ready_recording(stackchan_client_double(client), config, audio_dir=tmp_path)

    assert result["ready"] is True and result["consumed"] is True
    assert result["text"] == "你好，我在这里"
    assert result["duration"] == 1.25
    assert result["language"] == "?"  # ASR does not report detected language.
    assert "你好，我在这里" in format_listen_result(result)
    wav_path = Path(result["wav_path"])
    assert wav_path.read_bytes() == audio_bytes
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args == ("https://api.minimaxi.com/v1/speech_to_text",)
    assert kwargs["headers"]["Authorization"] == "Bearer private-existing-minimax-key"
    assert kwargs["headers"]["language"] == "zh"
    assert kwargs["data"] == {"model": "asr-1.0", "response_format": "json", "stream": "false"}
    assert kwargs["files"] == {"file": ("recording.wav", audio_bytes, "audio/wav")}
    assert kwargs["allow_redirects"] is False
    client.get_audio.assert_called_once_with()


@pytest.mark.parametrize("text", ["heard words", ""])
def test_listen_display_omits_unknown_language_but_preserves_known_language(text):
    result = {"ready": True, "text": text, "duration": 1.25, "audio_bytes": 40044, "language": "?"}

    unknown = format_listen_result(result)
    known = format_listen_result({**result, "language": "zh"})

    assert "?" not in unknown
    assert "Detected language" not in unknown
    if text:
        assert unknown == '👂 Heard (1.2s): "heard words"'
        assert known == '👂 Heard (1.2s, zh): "heard words"'
    else:
        assert unknown == (
            "🎤 Recording captured (40044 bytes, 1.2s) "
            "but ASR returned empty text. Audio may be too quiet."
        )
        assert "Detected language: zh." in known


@pytest.mark.parametrize("changes,lang", [
    ({"minimax_api_key": ""}, "zh"),
    ({"minimax_api_host": "https://private-key@evil.example"}, "zh"),
    ({"minimax_asr_timeout": 0}, "zh"),
    ({"minimax_asr_timeout": float("nan")}, "zh"),
    ({"asr_engine": "private-invalid-provider"}, "zh"),
    ({}, "zh\r\nAuthorization: private-value"),
])
def test_bad_asr_config_preserves_ready_recording(monkeypatch, tmp_path, changes, lang):
    client = ready_client()
    transcribe = Mock(side_effect=AssertionError("ASR must not run"))
    monkeypatch.setattr(minimax_asr, "transcribe_audio", transcribe)

    result = capture_ready_recording(
        stackchan_client_double(client), minimax_config(**changes), lang=lang, audio_dir=tmp_path,
    )

    assert result["ready"] is True and result["consumed"] is False
    assert result["error"]
    assert "private-" not in result["error"]
    client.get_audio.assert_not_called()
    transcribe.assert_not_called()
    requests.post.assert_not_called()
    assert not list(tmp_path.glob("rec_*.wav"))


def test_not_ready_never_checks_provider_or_consumes(monkeypatch, tmp_path):
    client = ready_client()
    client.audio_status.return_value = {"ready": False}
    preflight = Mock(side_effect=AssertionError("no recording to transcribe"))
    monkeypatch.setattr(minimax_asr, "configuration_error", preflight)

    result = capture_ready_recording(
        stackchan_client_double(client), minimax_config(), audio_dir=tmp_path,
    )

    assert result == {"ready": False, "consumed": False, "status": {"ready": False}}
    client.get_audio.assert_not_called()
    preflight.assert_not_called()


def test_explicit_fish_still_requires_fish_key_before_consume(tmp_path):
    client = ready_client()

    result = capture_ready_recording(
        stackchan_client_double(client), minimax_config(asr_engine="fish-audio"), audio_dir=tmp_path,
    )

    assert result["consumed"] is False
    assert result["error"] == (
        "Fish Audio key is not configured; set FISH_AUDIO_KEY before consuming audio."
    )
    client.get_audio.assert_not_called()


@pytest.mark.parametrize("tts,asr", [("fish-audio", "auto"), ("minimax", "fish-audio")])
def test_fish_asr_request_contract_is_unchanged(monkeypatch, tmp_path, tts, asr):
    config = make_config(tts_engine=tts, asr_engine=asr)
    path = tmp_path / "recording.wav"
    path.write_bytes(b"RIFF-fish-wav")

    def post(url, **kwargs):
        assert url == "https://api.fish.audio/v1/asr"
        assert kwargs["headers"] == {"Authorization": "Bearer test-key"}
        assert kwargs["files"]["audio"].read() == b"RIFF-fish-wav"
        assert kwargs["data"] == {"language": "en"}
        assert kwargs["timeout"] == 15.0
        return Mock(json=Mock(return_value={"text": "fish transcript", "duration": 2.0}))

    monkeypatch.setattr(requests, "post", post)
    assert audio_processing.transcribe_audio(path, "en", config)["text"] == "fish transcript"


def test_explicit_minimax_works_with_fish_tts(monkeypatch, tmp_path):
    config = minimax_config(tts_engine="fish-audio", asr_engine="minimax")
    transcribe = Mock(return_value={"text": "independent ASR", "duration": 0.5})
    monkeypatch.setattr(minimax_asr, "transcribe_audio", transcribe)
    path = tmp_path / "recording.wav"

    assert audio_processing.transcribe_audio(path, "en", config)["text"] == "independent ASR"
    transcribe.assert_called_once_with(path, "en", config)
    assert config.tts_engine == "fish-audio"


def test_uploaded_voice_uses_same_minimax_route_without_fish(monkeypatch, tmp_path):
    config = minimax_config()
    transcribe = Mock(return_value={"text": "uploaded transcript", "duration": 0.5})
    monkeypatch.setattr(minimax_asr, "transcribe_audio", transcribe)

    event = stackchan_voice_upload_server.process_uploaded_wav(
        b"RIFF-upload-wav", config, lang="en", audio_dir=tmp_path,
    )

    assert event["text"] == "uploaded transcript"
    assert event["source"] == "voice_upload"
    path = Path(event["wav_path"])
    assert path.read_bytes() == b"RIFF-upload-wav"
    transcribe.assert_called_once_with(path, "en", config)
    requests.post.assert_not_called()


def test_invalid_minimax_upload_config_is_rejected_before_saving(tmp_path):
    with pytest.raises(RuntimeError, match="MiniMax"):
        stackchan_voice_upload_server.process_uploaded_wav(
            b"RIFF-upload-wav", minimax_config(minimax_api_key=""), audio_dir=tmp_path,
        )
    assert not list(tmp_path.glob("*.wav"))


@pytest.mark.parametrize("key,status", [("private-test-key", 400), ("", 503)])
def test_upload_http_preflight_uses_selected_provider_before_body(monkeypatch, key, status):
    handler = stackchan_voice_upload_server.VoiceUploadHandler.__new__(
        stackchan_voice_upload_server.VoiceUploadHandler,
    )
    handler.path = "/voice/upload"
    handler.client_address = ("127.0.0.1", 1234)
    handler.headers = {"Content-Length": "0"}
    handler.rfile = Mock(read=Mock(side_effect=AssertionError("must not read upload body")))
    handler.server = SimpleNamespace(
        config=minimax_config(minimax_api_key=key),
        rate_limiter=SimpleNamespace(allow=lambda _: True),
        options=SimpleNamespace(lang="zh", upload_token=""),
    )
    reply = Mock()
    monkeypatch.setattr(stackchan_voice_upload_server, "write_json", reply)

    handler.do_POST()

    assert reply.call_args.args[1] == status
    if key:
        assert reply.call_args.args[2]["error"] == "empty audio body"
    else:
        assert "MiniMax API key is not configured" in reply.call_args.args[2]["error"]
    handler.rfile.read.assert_not_called()
    requests.post.assert_not_called()


def test_asr_override_does_not_change_minimax_tts_route(monkeypatch, tmp_path):
    config = minimax_config(asr_engine="fish-audio")
    synthesize = Mock(return_value=b"\x00\x00" * 100)
    monkeypatch.setattr(audio_processing, "synthesize_minimax_pcm", synthesize)
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(audio_processing, "TEMP_AUDIO_DIR", staging)
    monkeypatch.setattr(audio_processing, "AUDIO_DIR", tmp_path)
    details = {}

    path = audio_processing.generate_tts("hello", "en", config, details=details)

    synthesize.assert_called_once_with("hello", config)
    assert details == {"engine": "minimax"}
    assert path.exists()
    requests.post.assert_not_called()


def test_health_reports_selected_asr_without_exposing_keys(monkeypatch):
    config = minimax_config(transport="relay")
    client = SimpleNamespace(base_url="http://127.0.0.1/device", relay_status=lambda **_: {"online": True})
    monkeypatch.setattr(mcp_tools, "check_audio_dir", lambda: {"ok": True})

    report = mcp_tools.build_health_report(client, config)

    assert report["config"]["asr"] == {
        "engine": "minimax", "fish_audio_key_configured": False, "minimax_api_key_configured": True,
    }
    assert report["dependencies"]["asr_configured"] is True
    assert "private-test-key" not in json.dumps(report)
    requests.post.assert_not_called()
