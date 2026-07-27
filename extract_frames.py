"""Extract every frame from a video. Usage: python3 extract_frames.py"""

import subprocess
from pathlib import Path

VIDEO_PATH = Path("input.mp4")
OUTPUT_DIR = Path("frames")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
            ["ffmpeg", "-i", str(VIDEO_PATH), "-vsync", "0", str(OUTPUT_DIR / "frame_%06d.png")],
            check=True,
    )


if __name__ == "__main__":
    main()
