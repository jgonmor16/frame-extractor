"""Extract every frame from a video.

Usage: python3 extract_frames.py VIDEO OUTPUT_DIR
"""

import argparse
import subprocess
from pathlib import Path


def extract_frames(video_path: Path, output_dir: Path) -> list[Path]:
    """Extract every frame of a video as PNG image.

    Args:
        video_path: Path to the source video file.
        output_dir: Directory to write frames to.

    Returns:
        Sorted list of the extracted frames.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vsync",
            "0",
            str(output_dir / "frame_%06d.png"),
        ],
        check=True,
    )
    return sorted(output_dir.glob("frame_*.png"))


def main() -> None:
    """Parse command-line arguments and run the extraction."""
    parser = argparse.ArgumentParser(
        description="Extract every frame from a video into PNG images."
    )
    parser.add_argument("video", type=Path, help="Path to the input video file.")
    parser.add_argument(
        "output_dir", type=Path, help="Directory to write extracted frames to."
    )
    args = parser.parse_args()

    frames = extract_frames(args.video, args.output_dir)
    print(f"Extracted {len(frames)} frame(s) to '{args.output_dir}/'")

if __name__ == "__main__":
    main()
