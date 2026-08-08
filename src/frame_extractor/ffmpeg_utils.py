"""Helpers for locating the ffmpeg toolchain and reading video metadata.

Kept separate from the extraction logic so that talking to the external
binaries can be tested and replaced independently of how frames are produced.
"""

import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

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


class VideoInfo(NamedTuple):
    """What ffprobe call provides about a video.

    Atributes:
        duration: Length in seconds
        frame_rate: Frames per second, or None when the container does not
            report a usable rate.
    """

    duration: float
    frame_rate: float | None


def _parse_frame_rate(reported: str) -> float | None:
    """Turn ffprobe's fractional frame rate into a float.

    ffprobe reports the rate as a fraction, so NTSC footage comes back as
    "30000/1001" rather than 29.97. An unknown rate is reported as "0/0".

    Returns:
        The rate in frames per second, or None if it is missing or zero.
    """
    numerator, _, denominator = reported.partition("/")
    try:
        rate = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return rate or None


def probe_video_info(video_path: Path, ffprobe_path: str) -> VideoInfo:
    """Read a video's duration and frame rate in a single ffprobe call.

    Args:
        video_path: Path to the source video file.
        ffprobe_path: Path to the ffprobe executable.

    Returns:
        The duration and, where the container reports one, the frame rate.

    Raises:
        VideoFileError: If ffprobe cannot read the file, or reports no usable
        duration, which is the case for a corrupt or non-media file.
    """
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VideoFileError(
            f"ffprobe could not read '{video_path}':\n{result.stderr.strip()}"
        )

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )

    reported = fields.get("duration", "")
    try:
        duration = float(reported)
    except ValueError as exc:
        raise VideoFileError(
            f"ffprobe reported no usable duration for '{video_path}' "
            f"(got {reported!r}); the file may be corrupt or hold no video "
            "stream.."
        ) from exc

    return VideoInfo(
        duration=duration,
        frame_rate=_parse_frame_rate(fields.get("r_frame_rate", "")),
    )
