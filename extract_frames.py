"""Extract every frame from a video.

Usage: python3 extract_frames.py VIDEO OUTPUT_DIR  [--start SECONDS]
[--end SECONDS]
"""

import argparse
import subprocess
import shutil
import sys
from pathlib import Path

class FrameExtractorError(Exception):
    """Base class for every error raised by this script."""

class FFmpegNotFoundError(FrameExtractorError):
    """The ffmpeg binary could not be found on PATH"""

class VideoFileError(FrameExtractorError):
    """The input video is missing."""

class InvalidTimeRangeError(FrameExtractorError):
    """The requested [start, end) range is not usable."""

class FFmpegExecutionError(FrameExtractorError):
    """ffmpeg ran but exited with a non-zero status."""

    def __init__(self, message: str, *, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr

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
            f"--end ({end_time}) must be greater than --start ({start_time}"
        )

def _require_ffmpeg() -> str:
    """Return path to ffmpwg binary.

    Raises:
        FFmpegNotFoundError: If ffmpeg is not on PATH, with install hints.
    """
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        raise FFmpegNotFoundError(
            "ffmpeg was not found on PATH. Install it with "
            "`sudo apt install ffmpeg` on Debian/Ubuntu/WSL, or "
            "`brew install ffmpeg` on macOS."
        )
    return ffmpeg_path

def extract_frames(
    video_path: Path,
    output_dir: Path,
    start_time: float = 0.0,
    end_time: float | None = None,
) -> list[Path]:
    """Extract every frame of a video as PNG image within
    [start_time, end_time).

    Args:
        video_path: Path to the source video file.
        output_dir: Directory to write frames to.
        start_time: Time from when the extraction will begin.
        end_time: Time when the frame extraction will end.

    Returns:
        Sorted list of the extracted frames.

    Raises:
    VideoFileError: If the input video does not exist.
    InvalidTimeRangeError: If the requested range is negative or inverted.
    FFmpegNotFoundError: If ffmpeg is not installed.
    FFmpegExecutionError: If ffmpeg exits non-zero.
    """
    _validate_request(video_path, start_time, end_time)
    ffmpeg_path = _require_ffmpeg()

    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
            ffmpeg_path,
            "-hide_banner","-loglevel","error",
            "-ss", f"{start_time:.6f}",
            "-i",str(video_path)
    ]
    if end_time is not None:
        command += ["-t", f"{end_time - start_time:.6f}"]
    command += ["-vsync", "0", str(output_dir / "frame_%06d.png")]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegExecutionError(
            f"ffmpeg could not extract frames from '{video_path}'",
            returncode=result.returncode,
            stderr=result.stderr.strip()
        )
    return sorted(output_dir.glob("frame_*.png"))


def main() -> int:
    """Parse command-line arguments and run the extraction

    Returns:
        Process exit code: 0 on success, 1 on any expected failure.
    ."""
    parser = argparse.ArgumentParser(
        description="Extract every frame from a video into PNG images."
    )
    parser.add_argument(
        "video", type=Path, help="Path to the input video file."
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        default="frames/",
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
    args = parser.parse_args()

    try:
        frames = extract_frames(
            args.video,
            args.output_dir,
            start_time=args.start,
            end_time=args.end
        )

    except FFmpegExecutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1

    except FrameExtractorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Extracted {len(frames)} frame(s) to '{args.output_dir}/'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
