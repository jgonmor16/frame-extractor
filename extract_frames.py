"""Extract every frame from a video.

Usage: python3 extract_frames.py VIDEO OUTPUT_DIR
"""

import argparse
import subprocess
from pathlib import Path


def extract_frames(
    video_path: Path,
    output_dir: Path,
    start_time: float = 0.0,
    end_time: float | None = None,
) -> list[Path]:
    """Extract every frame of a video as PNG image.

    Args:
        video_path: Path to the source video file.
        output_dir: Directory to write frames to.
        start_time: Time from when the extraction will begin.
        end_time: Time when the frame extraction will end.

    Returns:
        Sorted list of the extracted frames.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
            "ffmpeg",
            "-hide_banner","-loglevel","error",
            "-ss", f"{start_time:.6f}",
            "-i",str(video_path)
    ]
    if end_time is not None:
        command += ["-t", f"{end_time - start_time:.6f}"]
    command += ["-vsync", "0", str(output_dir / "frame_%06d.png")]

    subprocess.run(command, check=True)
    return sorted(output_dir.glob("frame_*.png"))


def main() -> None:
    """Parse command-line arguments and run the extraction."""
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

    frames = extract_frames(
        args.video,
        args.output_dir,
        start_time=args.start,
        end_time=args.end
    )
    print(f"Extracted {len(frames)} frame(s) to '{args.output_dir}/'")


if __name__ == "__main__":
    main()
