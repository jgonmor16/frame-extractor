"""Helpers for locating the ffmpeg toolchain and reading video metadata.

Kept separate from the extraction logic so that talking to the external
binaries can be tested and replaced independently of how frames are produced.
"""

import shutil
import subprocess
from pathlib import Path

from frame_extractor.exceptions import FFmpegNotFoundError, VideoFileError


def require_binaries() -> tuple[str, str]:
    """Return path to ffmpeg and ffprobe binaries.

    Raises:
        FFmpegNotFoundError: If either binary is missing, with install hints.
    """
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    missing = [
        name
        for name, path in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path))
        if path is None
    ]

    if missing:
        raise FFmpegNotFoundError(
            f"{' and '.join(missing)} {'was' if len(missing) == 1 else 'were'}"
            " not found on PATH. Install it with "
            "`sudo apt install ffmpeg` on Debian/Ubuntu/WSL, or "
            "`brew install ffmpeg` on macOS."
        )

    assert ffmpeg_path is not None and ffprobe_path is not None
    return ffmpeg_path, ffprobe_path


def probe_duration(video_path: Path, ffprobe_path: str) -> float:
    """Return the total duration of a video in seconds.

    Raises:
    VideoFileError: If ffprobe cannot read the file, or reports no usable
    duration, which is the case for a corrupt or non-media file.
    """
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VideoFileError(
            f"ffprobe could not read '{video_path}':\n{result.stderr.strip()}"
        )

    reported = result.stdout.strip()
    try:
        return float(reported)
    except ValueError as exc:
        raise VideoFileError(
            f"ffprobe reported no usable duration for '{video_path}' "
            f"(got {reported!r}); the file may be corrupt."
        ) from exc
