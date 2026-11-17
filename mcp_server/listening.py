import time
from pathlib import Path
from typing import Any

from . import audio_processing
from .audio_server import AUDIO_DIR
from .stackchan_client import StackchanClient
from .stackchan_config import StackchanConfig


def capture_ready_recording(
    client: StackchanClient,
    config: StackchanConfig,
    *,
    lang: str = "zh",
    audio_dir: Path = AUDIO_DIR,
) -> dict[str, Any]:
    """Consume and transcribe the current recording only when the device says it is ready."""
    status = client.audio_status()
    if not status.get("ready"):
        return {
            "ready": False,
            "consumed": False,
            "status": status,
        }

    if error := audio_processing.asr_configuration_error(config, lang):
        return {
            "ready": True,
            "consumed": False,
            "status": status,
            "error": error,
        }

    audio_data = client.get_audio()
    if audio_data is None:
        return {
            "ready": True,
            "consumed": False,
            "status": status,
            "error": "Failed to fetch audio from Stack-chan",
        }

    wav_path = audio_dir / f"rec_{int(time.time() * 1000)}.wav"
    wav_path.write_bytes(audio_data)

    asr_result = audio_processing.transcribe_audio(wav_path, lang, config)
    return {
        "ready": True,
        "consumed": True,
        "status": status,
        "wav_path": str(wav_path),
        "audio_bytes": len(audio_data),
        "text": asr_result.get("text", ""),
        "duration": asr_result.get("duration", 0),
        "language": asr_result.get("language", "?"),
    }


def format_listen_result(result: dict[str, Any]) -> str:
    if not result.get("ready"):
        return "🎤 No recording ready. Stack-chan is listening... (speak to it and try again)"

    if result.get("error"):
        return f"❌ {result['error']}"

    text = result.get("text", "")
    duration = result.get("duration", 0)
    language = result.get("language", "?")
    language_label = f", {language}" if language and language != "?" else ""
    if text:
        return f"👂 Heard ({duration:.1f}s{language_label}): \"{text}\""

    audio_bytes = result.get("audio_bytes", 0)
    detected_language = f" Detected language: {language}." if language_label else ""
    return (
        f"🎤 Recording captured ({audio_bytes} bytes, {duration:.1f}s) "
        f"but ASR returned empty text.{detected_language} Audio may be too quiet."
    )
