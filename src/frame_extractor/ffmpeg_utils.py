"""Helpers for locating the ffmpeg toolchain and reading video metadata.

Kept separate from the extraction logic so that talking to the external
binaries can be tested and replaced independently of how frames are produced.
"""

import re
import shutil
import subprocess
from collections.abc import Callable
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
    """What one ffprobe call provides about a video.

    Attributes:
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
            "stream."
        ) from exc

    return VideoInfo(
        duration=duration,
        frame_rate=_parse_frame_rate(fields.get("r_frame_rate", "")),
    )


class Progress(NamedTuple):
    """How far through the requested range ffmpeg has decoded.

    Attributes:
        seconds_done: Position within the requested range, in seconds.
        seconds_total: Length of the requested range, or None when it was
            not known ahead of time.
        frames_written: Images written so far.
    """

    seconds_done: float
    seconds_total: float | None
    frames_written: int

    @property
    def fraction(self) -> float | None:
        """Completion from 0.0 to 1.0, or None if the total is unknown.

        Clamped, because ffmpeg's reported position can overshoot the
        range slightly on the final frame.
        """
        if not self.seconds_total:
            return None
        return min(self.seconds_done / self.seconds_total, 1.0)


def _parse_progress_block(
    block: dict[str, str], seconds_total: float | None
) -> Progress | None:
    """Turn one of ffmpeg's key=value progress blocks into a Progress.

    ffmpeg reports the position twice, as out_time_us and out_time_ms.
    Despite the name, out_time_ms is in microseconds too, so out_time_us
    is the one to read.

    Returns:
        The parsed progress, or None if the block carried no position.
    """
    reported = block.get("out_time_us")
    if reported is None:
        return None
    try:
        seconds_done = int(reported) / 1_000_000
    except ValueError:
        return None

    # The final block's position is the last frame's, which sits short of
    # the range's end by up to one frame interval. Report the range as
    # covered instead, so a caller driving a progress bar reaches 100%.
    if block.get("progress") == "end" and seconds_total is not None:
        seconds_done = seconds_total

    try:
        frames_written = int(block.get("frame", 0))
    except ValueError:
        frames_written = 0

    return Progress(
        seconds_done=seconds_done,
        seconds_total=seconds_total,
        frames_written=frames_written,
    )


def run_ffmpeg(
    command: list[str],
    on_progress: Callable[[Progress], None] | None = None,
    seconds_total: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg, optionally reporting progress as it decodes.

    Without a callback this is a plain blocking run. With one, ffmpeg's
    progress stream is read from stdout as it arrives and each complete
    block is handed to the callback.

    Args:
        command: The ffmpeg argument list.
        on_progress: Called with each progress update, if given.
        seconds_total: Length of the requested range, used to compute a
        fraction. None leaves Progress.fraction as None.

    Returns:
        The completed process, with stderr captured either way.
    """
    if on_progress is None:
        return subprocess.run(command, capture_output=True, text=True)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Popen with pipes always gives us these; assert for the type checker.
    assert process.stdout is not None

    block: dict[str, str] = {}
    for line in process.stdout:
        key, _, value = line.strip().partition("=")
        block[key] = value
        if key != "progress":
            continue
        update = _parse_progress_block(block, seconds_total)
        if update is not None:
            on_progress(update)
        block = {}

    _, stderr = process.communicate()
    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout="",
        stderr=stderr,
    )


_SHOWINFO_TIME = re.compile(r"pts_time:([\d.]+)")


def parse_frame_times(
    stderr: str, written: int, start_time: float
) -> list[float]:
    """Recover the timestamp of each written frame from showinfo output.

    The filter sits upstream of the muxer, so when a duration bounds the
    range it reports the frames it saw rather than the ones that survived
    truncation. The surplus is always at the end, so the list is trimmed
    to the number of files actually written.

    Times are relative to the seek position, so start_time is added back.

    Args:
        stderr: Captured ffmpeg output, at info level or louder.
        written: How many files the extraction produced.
        start_time: The seek offset to add to each reported time.

    Returns:
        One absolute timestamp per written frame, in extraction order.
        Shorter than ``written`` if ffmpeg reported fewer than it wrote,
        which should not happen but is not worth crashing over.
    """
    reported = [float(match) for match in _SHOWINFO_TIME.findall(stderr)]
    return [round(t + start_time, 6) for t in reported[:written]]


def strip_showinfo(stderr: str) -> str:
    """Remove showinfo's per-frame chatter from captured ffmpeg output.

    Requesting timestamps means running at info level, which buries any
    real diagnosis under one line per frame.
    """
    return "\n".join(
        line for line in stderr.splitlines() if "Parsed_showinfo" not in line
    )
