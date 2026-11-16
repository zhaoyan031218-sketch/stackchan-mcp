"""MiniMax custom-voice synthesis to bounded 24 kHz mono PCM, before playback."""

import json
import re
import time

import requests

from .elevenlabs_tts import TtsSynthesisError, collect_pcm
from .stackchan_config import PCM_CHANNELS, PCM_SAMPLE_RATE, StackchanConfig

API_HOSTS = {"https://api.minimaxi.com", "https://api.minimax.io"}


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
    if (not isinstance(audio, str) or not audio or len(audio) % 4
            or len(audio) > config.max_pcm_payload_bytes * 2
            or not re.fullmatch(r"[0-9a-fA-F]+", audio)):
        raise TtsSynthesisError("MiniMax returned invalid or oversized PCM hex audio")
    extra = payload.get("extra_info", {})
    if not isinstance(extra, dict):
        raise TtsSynthesisError("MiniMax returned invalid audio metadata")
    for name, expected in (("audio_sample_rate", PCM_SAMPLE_RATE),
                           ("audio_channel", PCM_CHANNELS), ("audio_format", "pcm")):
        if name in extra and (type(extra[name]) is not type(expected) or extra[name] != expected):
            raise TtsSynthesisError("MiniMax returned an unsupported audio format")
    return collect_pcm((bytes.fromhex(audio),), max_bytes=config.max_pcm_payload_bytes,
                       deadline=deadline)


def synthesize_pcm(text: str, config: StackchanConfig) -> bytes:
    if not config.minimax_api_key or not config.minimax_voice_id:
        raise TtsSynthesisError("MiniMax key or voice is not configured")
    if config.minimax_api_host not in API_HOSTS:
        raise TtsSynthesisError("MiniMax API host must be api.minimaxi.com or api.minimax.io over HTTPS")
    if not text.strip() or not config.minimax_model_id:
        raise TtsSynthesisError("MiniMax requires non-empty text and model ID")
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
                "audio_setting": {"sample_rate": PCM_SAMPLE_RATE, "format": "pcm", "channel": PCM_CHANNELS},
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
