#!/usr/bin/env python3
"""
prune_meetings.py: Independent auto-cleanup of meeting recordings & summaries.
Treats audio/video files and text summaries/transcripts with separate capacity limits.
Deletes oldest audio/video first to free disk space, while preserving notes and summaries.
"""

import sys
import os
import json
from pathlib import Path


DEFAULT_CONFIG = {
    "audio_video": {
        "max_size_mb": 5000,   # 5 GB max total audio/video storage
        "max_count": 20        # Keep up to 20 raw audio/video files
    },
    "transcripts_summaries": {
        "max_size_mb": 500,    # 500 MB max for text transcripts & summaries
        "max_count": 100       # Keep up to 100 meeting summaries
    }
}

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".mp4", ".mov", ".flac", ".ogg", ".webm"}
TEXT_EXTENSIONS = {".txt", ".md", ".html", ".json"}


def get_config(meetings_dir: Path) -> dict:
    config_file = meetings_dir / "retention.json"
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return {
                    "audio_video": {**DEFAULT_CONFIG["audio_video"], **cfg.get("audio_video", {})},
                    "transcripts_summaries": {**DEFAULT_CONFIG["transcripts_summaries"], **cfg.get("transcripts_summaries", {})}
                }
        except Exception as e:
            print(f"Warning: Could not read retention.json: {e}, using defaults.")
    
    # Save default config if not existing
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
    except Exception:
        pass
    return DEFAULT_CONFIG


def get_file_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except Exception:
        return 0.0


def prune_meetings(meetings_dir_path: str = None) -> dict:
    if not meetings_dir_path:
        home = Path.home()
        meetings_dir = home / "EmptyOne/meetings"
    else:
        meetings_dir = Path(meetings_dir_path).resolve()

    if not meetings_dir.exists():
        return {"status": "skipped", "reason": "Meetings directory does not exist"}

    config = get_config(meetings_dir)
    audio_cfg = config["audio_video"]
    text_cfg = config["transcripts_summaries"]

    max_audio_mb = float(audio_cfg.get("max_size_mb", 5000))
    max_audio_count = int(audio_cfg.get("max_count", 20))
    max_text_mb = float(text_cfg.get("max_size_mb", 500))
    max_text_count = int(text_cfg.get("max_count", 100))

    # Find all Meeting folders
    meeting_folders = [
        f for f in meetings_dir.iterdir()
        if f.is_dir() and f.name.startswith("Meeting_")
    ]
    # Sort folders by mtime (oldest first)
    meeting_folders.sort(key=lambda x: x.stat().st_mtime)

    # --------------------------------------------------------------------------
    # 1. INDEPENDENT AUDIO/VIDEO PRUNING
    # --------------------------------------------------------------------------
    audio_files = []
    for folder in meeting_folders:
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                audio_files.append(f)

    # Sort audio files oldest first
    audio_files.sort(key=lambda x: x.stat().st_mtime)

    total_audio_mb = sum(get_file_size_mb(f) for f in audio_files)
    deleted_audio = []

    for af in list(audio_files):
        over_size = total_audio_mb > max_audio_mb
        over_count = len(audio_files) > max_audio_count
        if over_size or over_count:
            size_mb = get_file_size_mb(af)
            try:
                af.unlink()
                deleted_audio.append(str(af))
                audio_files.remove(af)
                total_audio_mb -= size_mb
                print(f"[Prune Audio] Deleted oldest audio: {af.name} in {af.parent.name} ({size_mb:.2f} MB)")
            except Exception as e:
                print(f"Error deleting {af}: {e}")
        else:
            break

    # --------------------------------------------------------------------------
    # 2. INDEPENDENT TRANSCRIPT / SUMMARY PRUNING
    # --------------------------------------------------------------------------
    # Gather all text files
    text_files = []
    for folder in meeting_folders:
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() in TEXT_EXTENSIONS:
                text_files.append(f)

    total_text_mb = sum(get_file_size_mb(f) for f in text_files)
    deleted_meetings = []

    # If text storage or meeting count overflows, delete the oldest full meeting folder
    for folder in list(meeting_folders):
        over_text_size = total_text_mb > max_text_mb
        over_meeting_count = len(meeting_folders) > max_text_count
        if over_text_size or over_meeting_count:
            folder_text_mb = sum(get_file_size_mb(f) for f in folder.iterdir() if f.is_file())
            try:
                import shutil
                shutil.rmtree(folder)
                deleted_meetings.append(folder.name)
                meeting_folders.remove(folder)
                total_text_mb -= folder_text_mb
                print(f"[Prune Summary] Deleted oldest meeting folder: {folder.name} ({folder_text_mb:.2f} MB)")
            except Exception as e:
                print(f"Error deleting meeting folder {folder}: {e}")
        else:
            break

    result = {
        "audio_deleted_count": len(deleted_audio),
        "meetings_deleted_count": len(deleted_meetings),
        "total_audio_mb": round(total_audio_mb, 2),
        "total_text_mb": round(total_text_mb, 2),
        "remaining_meetings": len(meeting_folders)
    }
    return result


if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else None
    res = prune_meetings(target_dir)
    print(json.dumps(res, indent=2))
