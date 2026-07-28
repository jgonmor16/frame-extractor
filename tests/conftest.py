"""Shared pytest fixtures.

The sample video is generated at test time with ffmpeg's ``testsrc`` source
rather than committed to the repository.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

# 2 seconds at 10 fps = exactly 20 frames, at t = 0.0, 0.1, ... 1.9.
# Chosen so every expected frame count in the tests is an exact integer.
SAMPLE_DURATION = 2.0  # Seconds
SAMPLE_FPS = 10
SAMPLE_FRAME_COUNT = int(SAMPLE_DURATION * SAMPLE_FPS)

@pytest.fixture(scope="session")
def sample_frame_count() -> int:
    return SAMPLE_FRAME_COUNT

@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Check if ffmpeg is available

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg must be installed on PATH")

    video_path = tmp_path_factory.mktemp("video") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={SAMPLE_DURATION}:size=64x64:rate={SAMPLE_FPS}",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    return video_path
