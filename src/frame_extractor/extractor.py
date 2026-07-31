"""Extract every frame from a video within a time range, as individual images.

Usage: python3 -m frame_extractor.extractor VIDEO OUTPUT_DIR
            [--start SECONDS] [--end SECONDS] [--format {png,jpg}]
            [--jpeg-quality N] [--overwrite]
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from frame_extractor.exceptions import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    FrameExtractorError,
    InvalidOutputOptionError,
    InvalidTimeRangeError,
    OutputDirectoryError,
    VideoFileError,
)


SUPPORTED_FORMATS = ("png", "jpg")

# ffmpeg's -q:v scale for the mjpeg encoder: 2 is best, 31 is worst. Values
# outside this range are silently clamped rather than rejected, so they are
# checked instead of being passed through.
MIN_JPEG_QUALITY = 2
MAX_JPEG_QUALITY = 31


def _validate_output_options(image_format: str, jpeg_quality: int) -> None:
    """Check the requestied image for format and quality

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


def _require_binaries() -> tuple[str, str]:
    """Return path to ffmpeg and ffprobe binaries.

    Raises:
        FFmpegNotFoundError: If either binay is missing, with install hints.
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


def _probe_duration(video_path: Path, ffprobe_path: str) -> float:
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


def extract_frames(
    video_path: Path,
    output_dir: Path,
    start_time: float = 0.0,
    end_time: float | None = None,
    image_format: str = "png",
    jpeg_quality: int = MIN_JPEG_QUALITY,
    overwrite: bool = False,
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
    ffmpeg_path, ffprobe_path = _require_binaries()

    duration = _probe_duration(video_path, ffprobe_path)
    if start_time >= duration:
        raise InvalidTimeRangeError(
            f"--start ({start_time}s) is at or past the end of the video "
            f"({duration:.3f}s), so there is nothing to extract"
        )

    _prepare_output_directory(output_dir, image_format, overwrite)

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
    if image_format == "jpg":
        command += ["-q:v", str(jpeg_quality)]
    command += ["-y", "-vsync", "0", str(output_dir / f"frame_%06d.{image_format}")]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegExecutionError(
            f"ffmpeg could not extract frames from '{video_path}'",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
    return sorted(output_dir.glob(f"frame_*.{image_format}"))


def main() -> int:
    """Parse command-line arguments and run the extraction

    Returns:
        Process exit code: 0 on success, 1 on any expected failure.
    """
    parser = argparse.ArgumentParser(
        description="Extract frames from a video within a time range."
    )
    parser.add_argument(
        "video", type=Path, help="Path to the input video file."
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory to write extracted frames to."
    )
    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Start time in seconds, inclusive (default: 0.0).",
    )
    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help="End time in seconds, exclusive (default: end of video).",
    )
    parser.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default="png",
        help="Output image format (default: png).",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=MIN_JPEG_QUALITY,
        help=(
            f"JPEG quality from {MIN_JPEG_QUALITY} (best) to {MAX_JPEG_QUALITY}"
            f" (worst); ignored for PNG (default: {MIN_JPEG_QUALITY})."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace frames from an earlier extraction in OUTPUT_DIR.",
    )
    args = parser.parse_args()

    try:
        frames = extract_frames(
            args.video,
            args.output_dir,
            start_time=args.start,
            end_time=args.end,
            image_format=args.format,
            jpeg_quality=args.jpeg_quality,
            overwrite=args.overwrite,
        )

    except FFmpegExecutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1

    except FrameExtractorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Extracted {len(frames)} frame(s) to '{args.output_dir}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
