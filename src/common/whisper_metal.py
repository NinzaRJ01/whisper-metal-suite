"""
whisper_metal.py: Python wrapper for Metal-accelerated Whisper on Apple Silicon.
Provides an easy-to-use Python interface to transcribe any audio or video file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


MODELS_DIR = Path(os.environ.get("WHISPER_MODELS_DIR", Path.home() / ".whisper-models"))


def get_model_path(model_name: str = "large-v3-turbo") -> Path:
    """Resolve model path or download if missing."""
    aliases = {
        "turbo": "ggml-large-v3-turbo.bin",
        "large-v3-turbo": "ggml-large-v3-turbo.bin",
        "base.en": "ggml-base.en.bin",
        "base": "ggml-base.bin",
        "small": "ggml-small.bin",
        "small.en": "ggml-small.en.bin",
        "medium": "ggml-medium.bin",
        "large": "ggml-large-v3.bin",
        "large-v3": "ggml-large-v3.bin",
        "tiny": "ggml-tiny.bin",
    }
    filename = aliases.get(model_name, f"ggml-{model_name}.bin" if not model_name.startswith("ggml-") else model_name)
    if not filename.endswith(".bin"):
        filename += ".bin"

    model_path = MODELS_DIR / filename
    if not model_path.exists():
        # Fallback to base.en if default turbo isn't downloaded yet
        if model_name in ("turbo", "large-v3-turbo") and (MODELS_DIR / "ggml-base.en.bin").exists():
            return MODELS_DIR / "ggml-base.en.bin"

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{filename}"
        print(f"Downloading model {filename}...")
        subprocess.run(["curl", "-L", "--fail", "-C", "-", "-o", str(model_path), url], check=True)

    return model_path


def transcribe(
    audio_path: str | Path,
    model: str = "large-v3-turbo",
    language: str = "en",
    threads: int = 8,
    output_srt: bool = False,
    output_vtt: bool = False,
) -> Dict[str, Any]:
    """
    Transcribes an audio or video file using local Apple Silicon Metal acceleration.

    Returns:
        dict with keys:
          - 'text': complete transcription text
          - 'segments': list of {'start': '00:00:00.000', 'end': '...', 'text': '...'}
          - 'model': path to model used
          - 'srt': str content if output_srt=True else None
          - 'vtt': str content if output_vtt=True else None
    """
    audio_path = Path(audio_path).expanduser().resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file '{audio_path}' does not exist.")

    model_path = get_model_path(model)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_wav = Path(tmp_dir) / "converted.wav"
        out_prefix = Path(tmp_dir) / "output"

        # Preprocess to 16kHz mono WAV using ffmpeg
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(audio_path),
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            str(tmp_wav),
        ]
        subprocess.run(ffmpeg_cmd, check=True)

        # Build whisper-cli invocation
        whisper_cmd = [
            "whisper-cli",
            "-m", str(model_path),
            "-f", str(tmp_wav),
            "-t", str(threads),
            "-l", language,
            "-otxt",
            "--output-file", str(out_prefix),
        ]
        if output_srt:
            whisper_cmd.append("-osrt")
        if output_vtt:
            whisper_cmd.append("-ovtt")

        result = subprocess.run(
            whisper_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

        txt_file = Path(f"{out_prefix}.txt")
        text = txt_file.read_text(encoding="utf-8").strip() if txt_file.exists() else ""

        # Parse timestamped segments from stdout
        # format: [00:00:00.000 --> 00:00:10.400]   transcribed text
        segments = []
        pattern = re.compile(r"\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s*(.*)")
        for line in result.stdout.splitlines():
            match = pattern.match(line.strip())
            if match:
                start, end, seg_text = match.groups()
                segments.append({
                    "start": start,
                    "end": end,
                    "text": seg_text.strip(),
                })

        srt_content = None
        srt_file = Path(f"{out_prefix}.srt")
        if output_srt and srt_file.exists():
            srt_content = srt_file.read_text(encoding="utf-8")

        vtt_content = None
        vtt_file = Path(f"{out_prefix}.vtt")
        if output_vtt and vtt_file.exists():
            vtt_content = vtt_file.read_text(encoding="utf-8")

        return {
            "text": text,
            "segments": segments,
            "model": str(model_path),
            "srt": srt_content,
            "vtt": vtt_content,
        }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python whisper_metal.py <audio_or_video_file> [model]")
        sys.exit(1)

    target_file = sys.argv[1]
    target_model = sys.argv[2] if len(sys.argv) > 2 else "large-v3-turbo"

    print(f"Transcribing '{target_file}' with {target_model}...")
    res = transcribe(target_file, model=target_model)
    print("\n--- Transcription ---")
    print(res["text"])
