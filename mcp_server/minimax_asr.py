"""Bounded MiniMax speech recognition; never retry or expose provider diagnostics."""

import json
import math
import re
import struct
import time
from pathlib import Path
from typing import Any

import requests

from .stackchan_config import StackchanConfig

API_HOSTS = {"https://api.minimaxi.com", "https://api.minimax.io", "https://api.minimax.cn"}
MAX_AUDIO_BYTES = 50_000_000
MAX_AUDIO_SECONDS = 500
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TEXT_CHARACTERS = 32000


def configuration_error(config: StackchanConfig, lang: str) -> str | None:
    """Validate locally before a caller consumes the device's pending recording."""
    key = config.minimax_api_key
    if not isinstance(key, str) or not key.strip():
        return "MiniMax API key is not configured; set MINIMAX_API_KEY before consuming audio."
    if not all(33 <= ord(char) <= 126 for char in key):
        return "MiniMax API key contains invalid characters."
    if not isinstance(config.minimax_api_host, str) or config.minimax_api_host not in API_HOSTS:
        return "MiniMax ASR requires an official HTTPS API host."
    if not isinstance(lang, str) or (lang.lower() not in {"", "auto"} and (
        len(lang) > 63 or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", lang)
    )):
        return "MiniMax ASR language must be a language tag, auto, or empty."
    timeout = config.minimax_asr_timeout
    try:
        valid_timeout = type(timeout) in {int, float} and math.isfinite(timeout) and timeout > 0
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        return "MiniMax ASR timeout must be a finite positive number."
    return None


def _read_wav(wav_path: Path) -> bytes:
    """Check a complete PCM WAV, including chunk lengths, before paid upload."""
    try:
        if wav_path.stat().st_size > MAX_AUDIO_BYTES:
            raise RuntimeError("MiniMax ASR recording exceeds the 50 MB limit.")
        with wav_path.open("rb") as source:
            audio = source.read(MAX_AUDIO_BYTES + 1)
    except OSError:
        raise RuntimeError("MiniMax ASR recording could not be read.") from None
    if len(audio) > MAX_AUDIO_BYTES:
        raise RuntimeError("MiniMax ASR recording exceeds the 50 MB limit.")
    invalid = "MiniMax ASR requires a complete, non-empty PCM WAV recording."
    if (len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE"
            or struct.unpack_from("<I", audio, 4)[0] + 8 != len(audio)):
        raise RuntimeError(invalid)

    offset = 12
    pcm_format = None
    data_size = None
    while offset < len(audio):
        if len(audio) - offset < 8:
            raise RuntimeError(invalid)
        chunk_id, chunk_size = struct.unpack_from("<4sI", audio, offset)
        start = offset + 8
        end = start + chunk_size
        offset = end + (chunk_size % 2)
        if offset > len(audio):
            raise RuntimeError(invalid)
        if chunk_id == b"fmt ":
            if pcm_format is not None or chunk_size < 16:
                raise RuntimeError(invalid)
            pcm_format = struct.unpack_from("<HHIIHH", audio, start)
        elif chunk_id == b"data":
            if data_size is not None:
                raise RuntimeError(invalid)
            data_size = chunk_size

    if pcm_format is None or not data_size:
        raise RuntimeError(invalid)
    codec, channels, rate, byte_rate, block_align, bits = pcm_format
    if (codec != 1 or channels not in {1, 2} or not 8000 <= rate <= 48000
            or bits not in {8, 16, 24, 32} or block_align != channels * (bits // 8)
            or byte_rate != rate * block_align or data_size % block_align):
        raise RuntimeError(invalid)
    if data_size / byte_rate > MAX_AUDIO_SECONDS:
        raise RuntimeError("MiniMax ASR recording exceeds the 500 second limit.")
    return audio


def _decode_response(body: bytes) -> dict[str, Any]:
    invalid = "MiniMax ASR returned an invalid transcription response."
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeError(invalid) from None
    if not isinstance(result, dict):
        raise RuntimeError(invalid)
    if "base_resp" in result:
        base = result["base_resp"]
        if not isinstance(base, dict) or type(base.get("status_code")) is not int:
            raise RuntimeError(invalid)
        if base["status_code"] != 0:
            raise RuntimeError("MiniMax ASR API returned an error.")
    text, duration = result.get("text"), result.get("duration")
    if (not isinstance(text, str) or len(text) > MAX_TEXT_CHARACTERS or "\x00" in text
            or not isinstance(duration, (int, float)) or isinstance(duration, bool)):
        raise RuntimeError(invalid)
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise RuntimeError(invalid) from None
    try:
        valid_duration = math.isfinite(duration) and 0 <= duration <= MAX_AUDIO_SECONDS
    except OverflowError:
        valid_duration = False
    if not valid_duration:
        raise RuntimeError(invalid)
    # The API does not report detected language; do not invent one from the hint.
    return {"text": text, "duration": duration}


def transcribe_audio(wav_path: Path, lang: str, config: StackchanConfig) -> dict[str, Any]:
    error = configuration_error(config, lang)
    if error:
        raise RuntimeError(error)
    deadline = time.monotonic() + config.minimax_asr_timeout
    audio = _read_wav(wav_path)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("MiniMax ASR deadline exceeded.")
    headers = {"Authorization": f"Bearer {config.minimax_api_key}", "Accept": "application/json"}
    if lang.lower() not in {"", "auto"}:
        headers["language"] = lang
    try:
        with requests.post(
            f"{config.minimax_api_host}/v1/speech_to_text",
            headers=headers,
            data={"model": "asr-1.0", "response_format": "json", "stream": "false"},
            files={"file": ("recording.wav", audio, "audio/wav")},
            stream=True,
            allow_redirects=False,
            timeout=(min(10.0, remaining), remaining),
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"MiniMax ASR HTTP {response.status_code}.")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise RuntimeError("MiniMax ASR returned a non-JSON response.")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                if time.monotonic() > deadline:
                    raise RuntimeError("MiniMax ASR deadline exceeded.")
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("MiniMax ASR response exceeds the size limit.")
                body.extend(chunk)
            if time.monotonic() > deadline:
                raise RuntimeError("MiniMax ASR deadline exceeded.")
            return _decode_response(bytes(body))
    except requests.Timeout:
        raise RuntimeError("MiniMax ASR request timed out.") from None
    except requests.RequestException:
        raise RuntimeError("MiniMax ASR transport failed.") from None
