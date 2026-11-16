import json
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from test_mcp_server import FakeFastMCP, make_config

from mcp_server import audio_processing, mcp_tools, minimax_tts, stackchan_config
from mcp_server.elevenlabs_tts import TtsSynthesisError
from mcp_server.stackchan_client import PcmPlaybackError

PCM = b"\x01\x00\x02\x00" * 100
ENCODED = b"ID3-test-fixture-not-real-audio"


def config(**changes):
    return make_config(**{"tts_engine": "minimax", "minimax_api_key": "private-test-key",
                          "minimax_voice_id": "private-test-voice", **changes})


def payload(encoded=ENCODED):
    return {"base_resp": {"status_code": 0}, "data": {"audio": encoded.hex(), "status": 2},
            "extra_info": {"audio_sample_rate": 24000, "audio_channel": 1, "audio_format": "mp3"}}


class Response:
    def __init__(self, data=None, *, status=200, content_type="application/json", chunks=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        body = json.dumps(payload() if data is None else data).encode()
        self.chunks = chunks if chunks is not None else [body[:31], body[31:]]
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
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("STACKCHAN_OTEL_LOG", str(tmp_path / "otel.jsonl"))
    monkeypatch.setattr(requests, "post", Mock(side_effect=AssertionError("unexpected network")))
    monkeypatch.setattr(mcp_tools, "start_audio_server", lambda *_args: None)
    monkeypatch.setattr(mcp_tools, "signal_face_tracking", lambda *_args, **_kwargs: None)
    # Isolate HTTP/transport contract tests. Actual codec tests are in
    # test_minimax_audio_decode.py and never replace the decoder.
    monkeypatch.setattr(minimax_tts, "decode_mp3", Mock(return_value=PCM))
    monkeypatch.setattr(minimax_tts.shutil, "which", lambda _name: "/test/ffmpeg")


@pytest.mark.parametrize("host", ["https://api.minimaxi.com", "https://api.minimax.io"])
def test_request_contract_custom_voice_and_decoded_pcm(monkeypatch, host):
    response = Response()
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    cfg = config(minimax_api_host=host)
    assert minimax_tts.synthesize_pcm("hello", cfg) == PCM
    args, kwargs = post.call_args
    assert args == (host + "/v1/t2a_v2",)
    assert kwargs["headers"]["Authorization"] == "Bearer private-test-key"
    assert kwargs["json"] == {
        "model": "speech-2.8-hd", "text": "hello", "stream": False,
        "voice_setting": {"voice_id": "private-test-voice", "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 24000, "format": "mp3", "bitrate": 128000, "channel": 1},
        "language_boost": "auto",
    }
    assert kwargs["stream"] is True
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (10.0, 30.0)
    assert response.closed


@pytest.mark.parametrize("changes", [
    {"minimax_api_key": ""}, {"minimax_voice_id": ""}, {"minimax_model_id": ""},
    {"minimax_api_host": "http://api.minimaxi.com"},
    {"minimax_api_host": "https://api.minimaxi.com.evil.example"},
])
def test_bad_configuration_never_sends_credentials(changes):
    with pytest.raises(TtsSynthesisError):
        minimax_tts.synthesize_pcm("hello", config(**changes))
    requests.post.assert_not_called()


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_http_errors_and_redirects_are_not_followed(monkeypatch, status):
    response = Response(status=status)
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(TtsSynthesisError, match=f"HTTP {status}"):
        minimax_tts.synthesize_pcm("hello", config())
    assert response.closed


@pytest.mark.parametrize("data", [
    [], {}, {"base_resp": {"status_code": False}},
    {"base_resp": {"status_code": 0}, "data": None},
    {"base_resp": {"status_code": 0}, "data": {"audio": PCM.hex(), "status": 1}},
    payload(b""),
    {"base_resp": {"status_code": 0}, "data": {"audio": "0"}},
    {"base_resp": {"status_code": 0}, "data": {"audio": "https://example.com/audio"}},
    {"base_resp": {"status_code": 0}, "data": {"audio": "0000 bad"}},
    {**payload(), "extra_info": {"audio_format": "pcm"}},
    {**payload(), "extra_info": None},
])
def test_bad_audio_never_reaches_playback(monkeypatch, data):
    monkeypatch.setattr(requests, "post", Mock(return_value=Response(data)))
    play = Mock()
    monkeypatch.setattr(mcp_tools, "post_pcm_stream", play)
    with pytest.raises(TtsSynthesisError):
        mcp_tools.post_preferred_pcm_stream(Mock(), "hello", "zh", config(transport="relay"))
    play.assert_not_called()


@pytest.mark.parametrize("response", [
    Response(content_type="audio/mpeg"), Response(chunks=[b"not JSON"]),
    Response(chunks=[b"\xff"]), Response(chunks=[b"x" * 66000]),
    Response(payload(b"\0\0" * 100)),
])
def test_bounded_json_and_audio(monkeypatch, response):
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(TtsSynthesisError):
        minimax_tts.synthesize_pcm("hello", config(max_pcm_payload_bytes=64))
    assert response.closed


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError,
                                   requests.exceptions.ChunkedEncodingError])
def test_partial_response_is_discarded_without_retry_or_secret_leak(monkeypatch, error, caplog):
    response = Response(chunks=[b'{"data":', error("private-test-key private-test-voice private text")])
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    body = Mock()
    monkeypatch.setattr(mcp_tools, "post_pcm_stream", body)
    mcp = FakeFastMCP()
    mcp_tools.register_tools(mcp, SimpleNamespace(), config(transport="relay"), Mock())
    result = mcp.tools["stackchan_say"]("private text")
    assert "failed" in result
    assert post.call_count == 1
    body.assert_not_called()
    for secret in ("private-test-key", "private-test-voice", "private text"):
        assert secret not in result + caplog.text
    assert response.closed


def test_api_error_code_is_reported_without_provider_message(monkeypatch):
    response = Response({"base_resp": {"status_code": 1004, "status_msg": "private-test-key"}})
    monkeypatch.setattr(requests, "post", Mock(return_value=response))
    with pytest.raises(TtsSynthesisError, match="code 1004") as exc:
        minimax_tts.synthesize_pcm("hello", config())
    assert "private-test-key" not in str(exc.value)


def test_synthesis_deadline(monkeypatch):
    monkeypatch.setattr(requests, "post", Mock(return_value=Response()))
    monkeypatch.setattr(minimax_tts.time, "monotonic", Mock(side_effect=[0, 31]))
    with pytest.raises(TtsSynthesisError, match="deadline"):
        minimax_tts.synthesize_pcm("hello", config())


@pytest.mark.parametrize("mode", ["pcm", "wav"])
def test_custom_voice_reaches_relay_without_fish_or_edge(monkeypatch, tmp_path, mode):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(audio_processing, "TEMP_AUDIO_DIR", temp)
    monkeypatch.setattr(audio_processing, "AUDIO_DIR", tmp_path)
    post = Mock(return_value=Response())
    monkeypatch.setattr(requests, "post", post)
    received = []

    def accept(_client, chunks, *_args):
        received.append(b"".join(chunks))
        return {"success": True}

    monkeypatch.setattr(mcp_tools, "post_pcm_stream", accept)
    mcp = FakeFastMCP()
    mcp_tools.register_tools(mcp, SimpleNamespace(), config(transport="relay", audio_mode=mode), Mock())
    assert "Stack-chan is saying" in mcp.tools["stackchan_say"]("hello")
    assert received == [PCM]
    assert post.call_count == 1
    assert post.call_args.args[0] == "https://api.minimaxi.com/v1/t2a_v2"


def test_transport_fallback_reuses_audio(monkeypatch):
    post = Mock(return_value=Response())
    monkeypatch.setattr(requests, "post", post)
    received = []

    def tcp(_client, chunks, *_args):
        received.append(b"".join(chunks))
        raise PcmPlaybackError("not started", started=False)

    def staged(_client, chunks, *_args):
        received.append(b"".join(chunks))
        return {"success": True}

    monkeypatch.setattr(mcp_tools, "post_pcm_tcp_stream", tcp)
    monkeypatch.setattr(mcp_tools, "post_pcm_stream", staged)
    result = mcp_tools.post_preferred_pcm_stream(Mock(), "hello", "zh", config(pcm_transport="auto"))
    assert result["tts_engine"] == "minimax"
    assert received == [PCM, PCM]
    assert post.call_count == 1


def test_playback_failure_never_triggers_another_paid_synthesis(monkeypatch):
    post = Mock(return_value=Response())
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(mcp_tools, "post_pcm_tcp_stream", Mock(side_effect=RuntimeError("device failed")))
    wav = Mock()
    monkeypatch.setattr(audio_processing, "generate_tts", wav)
    mcp = FakeFastMCP()
    mcp_tools.register_tools(mcp, SimpleNamespace(), config(), Mock())
    assert "failed" in mcp.tools["stackchan_say"]("hello")
    assert post.call_count == 1
    wav.assert_not_called()


def test_direct_wav_format(monkeypatch, tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(audio_processing, "TEMP_AUDIO_DIR", temp)
    monkeypatch.setattr(audio_processing, "AUDIO_DIR", tmp_path)
    monkeypatch.setattr(requests, "post", Mock(return_value=Response()))
    details = {}
    path = audio_processing.generate_tts("hello", "zh", config(), details=details)
    with wave.open(str(path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (24000, 1, 2)
        assert audio.readframes(10000) == PCM
    assert details == {"engine": "minimax"}


def test_configuration_and_diagnostics_hide_credentials(monkeypatch):
    monkeypatch.setattr(stackchan_config, "load_dotenv", lambda: None)
    monkeypatch.setenv("STACKCHAN_TRANSPORT", "relay")
    for name, value in {"TTS_ENGINE": "minimax", "MINIMAX_API_KEY": "private-test-key",
                        "MINIMAX_VOICE_ID": "private-test-voice", "MINIMAX_MODEL_ID": "speech-2.8-hd",
                        "MINIMAX_API_HOST": "https://api.minimaxi.com/",
                        "STACKCHAN_MINIMAX_TTS_TIMEOUT": "12.5"}.items():
        monkeypatch.setenv(name, value)
    cfg = stackchan_config.load_config()
    assert cfg.minimax_api_host == "https://api.minimaxi.com"
    assert cfg.minimax_tts_timeout == 12.5
    assert cfg.tts_engine == "minimax"
    summary = stackchan_config.config_summary(cfg)
    assert summary["tts"]["minimax_api_key_configured"]
    assert summary["tts"]["minimax_voice_id_configured"]
    assert "private-test-key" not in json.dumps(summary) + repr(cfg)
    assert "private-test-voice" not in json.dumps(summary) + repr(cfg)


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "bad"])
def test_invalid_timeout_uses_default(monkeypatch, value):
    monkeypatch.setattr(stackchan_config, "load_dotenv", lambda: None)
    monkeypatch.setenv("STACKCHAN_TRANSPORT", "relay")
    monkeypatch.setenv("STACKCHAN_MINIMAX_TTS_TIMEOUT", value)
    assert stackchan_config.load_config().minimax_tts_timeout == 30.0
