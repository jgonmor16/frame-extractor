"""Tests for extract_frames."""

import hashlib
import sys
import shutil
from pathlib import Path

import pytest

import extract_frames as ef
from extract_frames import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    InvalidTimeRangeError,
    VideoFileError,
    extract_frames,
    main,
)

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
    
def test_seek_is_frame_accurate(
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

class TestFailureModes:
    """Every expected failure raises a FrameExtractorError, not a traceback."""

    def test_missing_video_raises(self, tmp_path: Path) -> None:
        with pytest.raises(VideoFileError, match="not found"):
            extract_frames(tmp_path / "nope.mp4", tmp_path / "out")

    @pytest.mark.parametrize(
        ("start_time", "end_time"),
        [
            pytest.param(5.0, 1.0, id="end-before-start"),
            pytest.param(1.0, 1.0, id="end-equals-start"),
            pytest.param(-1.0, 1.0, id="negative-start"),
        ],
    )

    def test_invalid_range_raises(
        self, tmp_path: Path, start_time: float, end_time: float
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(InvalidTimeRangeError):
            extract_frames(placeholder, tmp_path / "out", start_time, end_time)

    def test_missing_ffmpeg_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        with pytest.raises(FFmpegNotFoundError, match="not found on PATH"):
            extract_frames(placeholder, tmp_path / "out")

    def test_unreadable_video_raises(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        broken = tmp_path / "broken.mp4"
        broken.write_text("this is definitely not a video")
        with pytest.raises(
            VideoFileError,
            match="ffprobe could not read"
        ):
            extract_frames(broken, tmp_path / "out")

    def test_ffmpeg_failure_after_successful_probe(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the probe successd buf ffmpeg still fails, the error carries its
        stderr.
        """
        broken = tmp_path / "broken.mp4"
        broken.write_text("this is definitely not a video")
        monkeypatch.setattr(
            ef,
            "_probe_duration",
            lambda _path,
            _ffprobe: 10.0)

        with pytest.raises(FFmpegExecutionError) as excinfo:
            extract_frames(broken, tmp_path / "out")

        assert excinfo.value.returncode != 0
        assert excinfo.value.stderr, "ffmpeg's diagnosis should be captured"

    @pytest.mark.parametrize(
        "start_time",
        [
            pytest.param(2.0, id="exactly-at-duration"),
            pytest.param(99.0, id="far-past-end")
        ]
    )

    def test_start_past_duration_raises(
        self, sample_video: Path, tmp_path: Path, start_time: float
    ) -> None:
        """Last silent failure: this reported success with zero frames."""

        with pytest.raises(
            InvalidTimeRangeError,
            match="past the end of the video"
        ):
            extract_frames(sample_video, tmp_path / "out", start_time)

    def test_missing_ffprobe_reported_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A partial install names the binary that is actually missing."""
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name: None if name == "ffprobe" else "/usr/bin/ffmpeg"
        )

        with pytest.raises(FFmpegNotFoundError, match="ffprobe was not found"):
            extract_frames(placeholder, tmp_path / "out")

    def test_no_output_directory_left_behind_on_invalid_range(
        self, tmp_path: Path
    ) -> None:
        """Validation happens before mkdir, so a rejected request creates nothing."""
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        output_dir = tmp_path / "out"
        with pytest.raises(InvalidTimeRangeError):
            extract_frames(placeholder, output_dir, 5.0, 1.0)
        assert not output_dir.exists()

class TestCommandLineInterface:
    """main() turns exceptions into exit codes and stderr messages."""

    def test_reports_error_without_traceback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "extract_frames.py",
                str(tmp_path / "nope.mp4"),
                str(tmp_path / "out")
            ]
        )
        exit_code = main()
        captured = capsys.readouterr()

        assert exit_code == 1
        assert captured.err.startswith("error: ")
        assert "Traceback" not in captured.err

    def test_success_returns_zero(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "extract_frames.py",
                str(sample_video),
                str(tmp_path / "out"),
                "--end", "0.5"
            ]
        )
        exit_code = main()
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "Extracted 5 frame(s)" in captured.out
