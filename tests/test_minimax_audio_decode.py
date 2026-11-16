"""Exercise the real codec so compressed bytes cannot masquerade as speaker PCM."""

import math
import shutil
import struct
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from test_mcp_server import FakeFastMCP
from test_minimax_tts import Response, config, payload

from mcp_server import mcp_tools, minimax_tts
from mcp_server.elevenlabs_tts import TtsSynthesisError


@pytest.fixture(scope="module", params=[(24000, 1), (44100, 2)])
def mp3_tone(request):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("real audio codec regression requires ffmpeg")
    rate, channels = request.param
    samples = [round(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate)]
    raw = b"".join(struct.pack("<h", sample) * channels for sample in samples)
    result = subprocess.run(  # noqa: S603 - fixed fixture encoder, no shell.
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(rate),
         "-ac", str(channels), "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "128k",
         "-f", "mp3", "pipe:1"],
        input=raw, capture_output=True, timeout=10, check=True,
    )
    return result.stdout


def assert_speaker_tone(pcm):
    # One second plus bounded MP3 encoder padding, interpreted as little-endian
    # signed 16-bit mono at 24 kHz. Wrong width/endian/rate fails this signal test.
    assert 24000 <= len(pcm) // 2 <= 27000
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)[3000:21000]
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    assert 4000 < rms < 6500
    real = sum(s * math.cos(2 * math.pi * 440 * i / 24000) for i, s in enumerate(samples))
    imag = sum(s * math.sin(2 * math.pi * 440 * i / 24000) for i, s in enumerate(samples))
    fundamental_power = 2 * (real * real + imag * imag) / len(samples) ** 2
    assert fundamental_power / rms ** 2 > 0.98


def test_real_minimax_audio_is_decoded_before_relay(monkeypatch, tmp_path, mp3_tone):
    monkeypatch.setenv("STACKCHAN_OTEL_LOG", str(tmp_path / "otel.jsonl"))
    response = Response(payload(mp3_tone))
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    received = []

    def accept(_client, chunks, *_args):
        received.append(b"".join(chunks))
        return {"success": True}

    monkeypatch.setattr(mcp_tools, "post_pcm_stream", accept)
    mcp = FakeFastMCP()
    mcp_tools.register_tools(mcp, SimpleNamespace(), config(transport="relay"), Mock())
    assert "Stack-chan is saying" in mcp.tools["stackchan_say"]("test")
    assert post.call_count == 1
    assert response.closed
    assert len(received) == 1
    assert received[0] != mp3_tone
    assert_speaker_tone(received[0])


def test_decoded_limit_rejects_speech_instead_of_truncating(mp3_tone):
    # Compressed input fits; decoded output does not. No partial playback.
    assert len(mp3_tone) < 24000
    with pytest.raises(TtsSynthesisError, match="byte limit"):
        minimax_tts.decode_response(payload(mp3_tone), config(max_pcm_payload_bytes=24000),
                                    time.monotonic() + 5)


@pytest.mark.parametrize("encoded", [b"not an MP3", b"RIFF" + b"\0" * 1000, b"\0\0" * 1000])
def test_real_decoder_rejects_non_mp3_before_playback(monkeypatch, encoded):
    if not shutil.which("ffmpeg"):
        pytest.skip("real audio codec regression requires ffmpeg")
    monkeypatch.setattr(requests, "post", Mock(return_value=Response(payload(encoded))))
    play = Mock()
    monkeypatch.setattr(mcp_tools, "post_pcm_stream", play)
    with pytest.raises(TtsSynthesisError, match="decoded as MP3"):
        mcp_tools.post_preferred_pcm_stream(Mock(), "test", "zh", config(transport="relay"))
    play.assert_not_called()


def test_missing_decoder_fails_before_paid_request(monkeypatch):
    monkeypatch.setattr(minimax_tts.shutil, "which", lambda _name: None)
    post = Mock(side_effect=AssertionError("must not call paid API"))
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TtsSynthesisError, match="requires ffmpeg"):
        minimax_tts.synthesize_pcm("test", config())
    post.assert_not_called()


def test_expired_deadline_never_starts_decoder(monkeypatch):
    monkeypatch.setattr(minimax_tts.shutil, "which", lambda _name: "/test/ffmpeg")
    run = Mock(side_effect=AssertionError("decoder must not start"))
    monkeypatch.setattr(minimax_tts.subprocess, "run", run)
    with pytest.raises(TtsSynthesisError, match="deadline"):
        minimax_tts.decode_response(payload(), config(), time.monotonic() - 1)
    run.assert_not_called()


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("private-source", 1, stderr=b"private-source"),
    OSError("private-source"),
    subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"private-source"),
])
def test_decoder_failure_never_exposes_diagnostics(monkeypatch, failure, caplog):
    monkeypatch.setattr(minimax_tts.shutil, "which", lambda _name: "/test/ffmpeg")
    run = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    monkeypatch.setattr(minimax_tts.subprocess, "run", run)
    with pytest.raises(TtsSynthesisError) as exc:
        minimax_tts.decode_response(payload(), config(), time.monotonic() + 5)
    assert "private-source" not in str(exc.value) + caplog.text
