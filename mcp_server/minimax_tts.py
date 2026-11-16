"""MiniMax custom-voice synthesis to bounded 24 kHz mono PCM, before playback."""

import json
import logging
import re
import shutil
import subprocess
import time

import requests

from .elevenlabs_tts import TtsSynthesisError, collect_pcm
from .stackchan_config import PCM_CHANNELS, PCM_SAMPLE_RATE, PCM_SAMPLE_WIDTH, StackchanConfig

API_HOSTS = {"https://api.minimaxi.com", "https://api.minimax.io"}
logger = logging.getLogger(__name__)


def decode_mp3(encoded: bytes, config: StackchanConfig, deadline: float) -> bytes:
    """Decode a self-describing format; never infer raw sample width or endian."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TtsSynthesisError("MiniMax playback requires ffmpeg")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TtsSynthesisError("MiniMax synthesis deadline exceeded")
    # Bound decoded stdout even if a small compressed response expands greatly.
    # Decode one second past the limit so oversize speech is rejected, not clipped.
    max_seconds = config.max_pcm_payload_bytes / (PCM_SAMPLE_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH) + 1
    try:
        result = subprocess.run(  # noqa: S603 - fixed decoder arguments; audio goes to stdin, never a shell.
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
             "-err_detect", "explode", "-protocol_whitelist", "pipe", "-f", "mp3", "-i", "pipe:0",
             "-map", "0:a:0", "-vn", "-t", f"{max_seconds:.9f}",
             "-ac", str(PCM_CHANNELS), "-ar", str(PCM_SAMPLE_RATE),
             "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
            input=encoded, capture_output=True, timeout=remaining, check=False,
        )
    except subprocess.TimeoutExpired:
        raise TtsSynthesisError("MiniMax audio decoding deadline exceeded") from None
    except OSError:
        raise TtsSynthesisError("MiniMax audio decoder could not start") from None
    if result.returncode:
        # Decoder diagnostics can include source metadata. Do not expose them.
        raise TtsSynthesisError("MiniMax returned audio that could not be decoded as MP3")
    pcm = collect_pcm((result.stdout,), max_bytes=config.max_pcm_payload_bytes, deadline=deadline)
    logger.info("MiniMax audio decoded: input=mp3 bytes=%d output=s16le rate=24000 channels=1 bytes=%d",
                len(encoded), len(pcm))
    return pcm


def decode_response(payload: object, config: StackchanConfig, deadline: float) -> bytes:
    if not isinstance(payload, dict):
        raise TtsSynthesisError("MiniMax returned an invalid JSON response")
    base = payload.get("base_resp")
    if not isinstance(base, dict) or type(base.get("status_code")) is not int:
        raise TtsSynthesisError("MiniMax response has no valid API status")
    if base["status_code"] != 0:
        # Provider status_msg may contain text, voice IDs or credentials.
        raise TtsSynthesisError(f"MiniMax API error code {base['status_code']}")
    data = payload.get("data")
    if not isinstance(data, dict) or ("status" in data and data["status"] != 2):
        raise TtsSynthesisError("MiniMax synthesis did not complete")
    audio = data.get("audio")
    if (not isinstance(audio, str) or not audio or len(audio) % 2
            or len(audio) > config.max_pcm_payload_bytes * 2
            or not re.fullmatch(r"[0-9a-fA-F]+", audio)):
        raise TtsSynthesisError("MiniMax returned invalid or oversized encoded hex audio")
    extra = payload.get("extra_info", {})
    if not isinstance(extra, dict):
        raise TtsSynthesisError("MiniMax returned invalid audio metadata")
    if "audio_format" in extra and extra["audio_format"] != "mp3":
        raise TtsSynthesisError("MiniMax did not return the requested MP3 format")
    return decode_mp3(bytes.fromhex(audio), config, deadline)


def synthesize_pcm(text: str, config: StackchanConfig) -> bytes:
    if not config.minimax_api_key or not config.minimax_voice_id:
        raise TtsSynthesisError("MiniMax key or voice is not configured")
    if config.minimax_api_host not in API_HOSTS:
        raise TtsSynthesisError("MiniMax API host must be api.minimaxi.com or api.minimax.io over HTTPS")
    if not text.strip() or not config.minimax_model_id:
        raise TtsSynthesisError("MiniMax requires non-empty text and model ID")
    if not shutil.which("ffmpeg"):
        raise TtsSynthesisError("MiniMax playback requires ffmpeg")
    deadline = time.monotonic() + config.minimax_tts_timeout
    try:
        with requests.post(
            f"{config.minimax_api_host}/v1/t2a_v2",
            headers={"Authorization": f"Bearer {config.minimax_api_key}",
                     "Content-Type": "application/json", "Accept": "application/json"},
            json={
                "model": config.minimax_model_id, "text": text, "stream": False,
                "voice_setting": {"voice_id": config.minimax_voice_id,
                                  "speed": 1.0, "vol": 1.0, "pitch": 0},
                "audio_setting": {"sample_rate": PCM_SAMPLE_RATE, "format": "mp3",
                                  "bitrate": 128000, "channel": PCM_CHANNELS},
                "language_boost": "auto",
            },
            stream=True, allow_redirects=False,
            timeout=(min(10.0, config.minimax_tts_timeout), config.minimax_tts_timeout),
        ) as response:
            if response.status_code != 200:
                raise TtsSynthesisError(f"MiniMax HTTP {response.status_code}")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise TtsSynthesisError("MiniMax returned a non-JSON response")
            body = bytearray()
            # Hex doubles the audio size; allow bounded room for JSON metadata.
            limit = config.max_pcm_payload_bytes * 2 + 64 * 1024
            for chunk in response.iter_content(chunk_size=4096):
                if time.monotonic() > deadline:
                    raise TtsSynthesisError("MiniMax synthesis deadline exceeded")
                if len(body) + len(chunk) > limit:
                    raise TtsSynthesisError("MiniMax response exceeds the configured playback byte limit")
                body.extend(chunk)
            if time.monotonic() > deadline:
                raise TtsSynthesisError("MiniMax synthesis deadline exceeded")
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise TtsSynthesisError("MiniMax returned invalid JSON") from None
            return decode_response(payload, config, deadline)
    except requests.RequestException as exc:
        raise TtsSynthesisError(f"MiniMax transport failed ({type(exc).__name__})") from None
