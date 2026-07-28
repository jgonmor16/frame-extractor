"""Tests for extract_frames."""

import hashlib
from pathlib import Path

import pytest

from extract_frames import extract_frames

def _digest(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()

@pytest.mark.parametrize(
    ("start_time", "end_time", "expected_count"),
    [
        pytest.param(0.0, None,  20, id="whole-video"),
        pytest.param(1.0, None,  10, id="start-to-end"),
        pytest.param(0.0, 1.0,   10, id="start-of-video"),
        pytest.param(0.5, 1.5,   10, id="middle-of-video"),
        pytest.param(1.5, 2.0,    5, id="end-of-video"),
        pytest.param(0.0, 0.3,    3, id="very-short-range"),
        pytest.param(0.0, 999.0, 20, id="end-beyond-duration"),
    ],
)

def test_frame_count_for_range(
    sample_video: Path,
    tmp_path: Path,
    start_time: float,
    end_time: float | None,
    expected_count: int,
) -> None:
    """The number of extracted frames matches the requested range"""
    frames = extract_frames(sample_video, tmp_path / "out", start_time, end_time)
    assert len(frames) == expected_count

def test_range_is_half_open(
    sample_video: Path,
    tmp_path: Path,
    sample_frame_count: int
) -> None:
    """The frame at exactly end_time is excluded, so adjacent ranges do not
    overlap"""
    first = extract_frames(sample_video, tmp_path / "first", 0.0, 1.0)
    second = extract_frames(sample_video, tmp_path / "second", 1.0, 2.0)
    assert len(first) + len(second) == sample_frame_count
    
def test_seek_is_frame_accuracte(
    sample_video: Path,
    tmp_path: Path
) -> None:
    """Seeking to 1.0s yields the same image as frame 11 of a full extraction.

    This is the important one: a keyframe-only seek would return a nearby but
    different frame, which a count-based assertion would not catch.
    """
    everything = extract_frames(sample_video, tmp_path / "all")
    seeked = extract_frames(sample_video, tmp_path / "seeked", 1.0, None)
    assert _digest(seeked[0]) == _digest(everything[10])

def test_creates_missing_output_directory(
    sample_video: Path,
    tmp_path: Path
) -> None:
    """Nested output directories are created rather than raising"""
    output_dir = tmp_path / "does" / "not" / "exist"
    assert not output_dir.exists()

    frames = extract_frames(sample_video, output_dir, 0.0, 0.5)

    assert output_dir.is_dir()
    assert len(frames) == 5

def test_returns_sorted_zero_padded_paths(
    sample_video: Path,
    tmp_path: Path,
) -> None:
    """Frames are returned in playback order, with names that are sorted"""
    frames = extract_frames(sample_video, tmp_path / "out", 0.0, 0.5)
    assert frames == sorted(frames)
    assert [f.name for f in frames[:3]] == [
        "frame_000001.png",
        "frame_000002.png",
        "frame_000003.png",
    ]
