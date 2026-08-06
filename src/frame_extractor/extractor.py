"""Extract every frame from a video within a time range, as individual images.

Usage: python3 -m frame_extractor.extractor VIDEO OUTPUT_DIR
            [--start SECONDS] [--end SECONDS] [--format {png,jpg}]
            [--jpeg-quality N] [--overwrite]
"""

import subprocess
from pathlib import Path

from frame_extractor.exceptions import (
    FFmpegExecutionError,
    InvalidOutputOptionError,
    InvalidTimeRangeError,
    OutputDirectoryError,
    VideoFileError,
)
from frame_extractor.ffmpeg_utils import probe_duration, require_binaries

SUPPORTED_FORMATS = ("png", "jpg")

# ffmpeg's -q:v scale for the mjpeg encoder: 2 is best, 31 is worst. Values
# outside this range are silently clamped rather than rejected, so they are
# checked instead of being passed through.
MIN_JPEG_QUALITY = 2
MAX_JPEG_QUALITY = 31


def _validate_output_options(image_format: str, jpeg_quality: int) -> None:
    """Check the requested image format and quality.

    Raises:
        InvalidOutputOptionError: If the format is unsupported, or the quality
        falls outside ffmpeg's usable range
    """
    if image_format not in SUPPORTED_FORMATS:
        raise InvalidOutputOptionError(
            f"Unsupported format {image_format!r}; expected one of "
            f"{', '.join(SUPPORTED_FORMATS)}"
        )

    if not MIN_JPEG_QUALITY <= jpeg_quality <= MAX_JPEG_QUALITY:
        raise InvalidOutputOptionError(
            f"--jpeg-quality must be between {MIN_JPEG_QUALITY} (best) and "
            f"{MAX_JPEG_QUALITY} (worst), got {jpeg_quality}"
        )


def _validate_sampling(fps: float | None) -> None:
    """Check the requested sampling rate.

    ffmpeg rejects a non-positive rate with "The encoder timebase is not
    set", which says nothing about the flag that caused it.

    Raises:
        InvalidOutputOptionError: If fps is zero or negative.
    """
    if fps is not None and fps <= 0:
        raise InvalidOutputOptionError(
            f"--fps must be greater than 0, got {fps}"
        )


def _validate_request(
    video_path: Path, start_time: float, end_time: float | None
) -> None:
    """Check the request before spawning the subprocess.

    Raises:
        VideoFileError: If the input video does not exist.
        InvalidTimeRangeError: If the range is negative or inverted.
    """
    if not video_path.is_file():
        raise VideoFileError(f"Video file not found: {video_path}")

    if start_time < 0:
        raise InvalidTimeRangeError(
            f"--start must be greater or equal to 0, got {start_time}"
        )

    if end_time is not None and end_time <= start_time:
        raise InvalidTimeRangeError(
            f"--end ({end_time}) must be greater than --start ({start_time})"
        )


def _prepare_output_directory(
    output_dir: Path,
    image_format: str,
    overwrite: bool,
) -> None:
    """Make output directory ready.

    Frames from an earlier run are removed rather than written over. ffmpeg
    numbers its output from 1 each time, so a shorter second run would leave
    the tail of the first behind and the returned list would report frames
    this extraction never produced.

    Raises:
        OutputDirectoryError: If frames are present and overwrite is False.
    """
    pattern = f"frame_*.{image_format}"
    existing = sorted(output_dir.glob(pattern)) if output_dir.is_dir() else []

    if existing and not overwrite:
        raise OutputDirectoryError(
            f"'{output_dir}' already holds {len(existing)} file(s) matching "
            f"'{pattern}'. Pass --overwrite to replace them, or choose an "
            "empty directory."
        )

    for frame in existing:
        frame.unlink()

    output_dir.mkdir(parents=True, exist_ok=True)


def build_ffmpeg_command(
    ffmpeg_path: str,
    video_path: Path,
    output_dir: Path,
    start_time: float = 0.0,
    end_time: float | None = None,
    image_format: str = "png",
    jpeg_quality: int = MIN_JPEG_QUALITY,
    fps: float | None = None,
) -> list[str]:
    """Build the ffmpeg argument list for one extraction.

    ``-ss`` precedes ``-i`` so ffmpeg seeks on the input rather than
    decoding and discarding everything before ``start_time``. The clip
    length is a duration (``-t``), not an end timestamp (``-to``), which
    ffmpeg would read relative to the seek position. ``-vsync 0`` passes
    every decoded frame through, so none are duplicated or dropped

    Returns:
        The complete argument list, ready for ``subprocess.run``
    """
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_time:.6f}",
        "-i",
        str(video_path),
    ]

    if end_time is not None:
        command += ["-t", f"{end_time - start_time:.6f}"]
    if fps is not None:
        command += ["-vf", f"fps={fps}"]
    if image_format == "jpg":
        command += ["-q:v", str(jpeg_quality)]
    command += [
        "-y",
        "-vsync",
        "0",
        str(output_dir / f"frame_%06d.{image_format}"),
    ]

    return command


def extract_frames(
    video_path: Path,
    output_dir: Path,
    start_time: float = 0.0,
    end_time: float | None = None,
    *,
    image_format: str = "png",
    jpeg_quality: int = MIN_JPEG_QUALITY,
    overwrite: bool = False,
    fps: float | None = None,
) -> list[Path]:
    """Extract every frame of a video as an image within [start_time, end_time).

    Args:
        video_path: Path to the source video file.
        output_dir: Directory to write frames to.
        start_time: Time from when the extraction will begin, inclusive.
        end_time: Time when the frame extraction will end, exclusive.
        image_format: Output image format, either "png" or "jpg".
        jpeg_quality: ffmpeg ``-q:v`` value for JPEG output, from 2 (best) to
            31 (worst). Ignored for PNG, which is lossless.
        overwrite: Whether to replace frames from an earlier extraction in
            ``output_dir``. When False, their presence is an error.
        fps: Frames to extract per second of video. ``None`` extracts every
            frame. A rate above the source's own duplicates frames rather than
            failing, which is rarely wanted.

    Returns:
        Sorted list of the extracted frames.

    Raises:
        VideoFileError: If the input video does not exist.
        InvalidTimeRangeError: If the requested range is negative or inverted.
        InvalidOutputOptionError: If the format or JPEG quality is unusable.
        OutputDirectoryError: If output_dir holds frames and overwrite is False.
        FFmpegNotFoundError: If ffmpeg or ffprobe are not installed.
        FFmpegExecutionError: If ffmpeg exits non-zero.
    """
    _validate_request(video_path, start_time, end_time)
    _validate_output_options(image_format, jpeg_quality)
    _validate_sampling(fps)
    ffmpeg_path, ffprobe_path = require_binaries()

    duration = probe_duration(video_path, ffprobe_path)
    if start_time >= duration:
        raise InvalidTimeRangeError(
            f"--start ({start_time}s) is at or past the end of the video "
            f"({duration:.3f}s), so there is nothing to extract"
        )

    _prepare_output_directory(output_dir, image_format, overwrite)

    command = build_ffmpeg_command(
        ffmpeg_path,
        video_path,
        output_dir,
        start_time,
        end_time,
        image_format=image_format,
        jpeg_quality=jpeg_quality,
        fps=fps,
    )

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegExecutionError(
            f"ffmpeg could not extract frames from '{video_path}'",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
    return sorted(output_dir.glob(f"frame_*.{image_format}"))
