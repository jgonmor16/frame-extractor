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
    InvalidOutputOptionError,
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

class TestOutputFormats:
    """Format selection, quality control, and their validation."""

    def test_png_is_the_default(
        self,
        sample_video: Path,
        tmp_path: Path
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", 0.0, 0.5)
        assert len(frames) == 5
        assert all(f.suffix == ".png" for f in frames)

    def test_jpeg_output_uses_jpg_extension(
        self,
        sample_video: Path,
        tmp_path: Path
    ) -> None:
        frames = extract_frames(
            sample_video,
            tmp_path / "out",
            0.0,
            0.5,
            image_format="jpg")
        assert len(frames) == 5
        assert all(f.suffix == ".jpg" for f in frames)
        assert frames[0].name == "frame_000001.jpg"

    def test_lower_quality_produces_smaller_files(
        self,
        sample_video: Path,
        tmp_path: Path
    ) -> None:
        """A higher -q:v means worse quality, so the bytes on disk should
        shrink.
        """
        best = extract_frames(
            sample_video,
            tmp_path / "best",
            0.0,
            0.5,
            image_format="jpg",
            jpeg_quality=2
        )
        worst = extract_frames(
            sample_video,
            tmp_path / "worst",
            0.0,
            0.5,
            image_format="jpg",
            jpeg_quality=31
        )
        assert sum(f.stat().st_size for f in worst) < sum(f.stat().st_size for f in best)

    def test_quality_does_not_affect_png(
        self,
        sample_video: Path,
        tmp_path: Path
    ) -> None:
        default = extract_frames(sample_video, tmp_path / "a", 0.0, 0.5)
        explicit = extract_frames(
            sample_video,
            tmp_path / "b",
            0.0,
            0.5,
            image_format="png",
            jpeg_quality=31
        )
        assert [f.read_bytes() for f in default] == [f.read_bytes() for f in explicit]

    @pytest.mark.parametrize(
        "image_format",
        [
            pytest.param("bmp", id="unsupported-format"),
            pytest.param("jpeg", id="jpg-spelled-out"),
            pytest.param("PNG", id="wrong-case"),
            pytest.param("", id="empty"),
        ]
    )

    def test_invalid_format_raises(
        self,
        tmp_path: Path,
        image_format: str
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(
            InvalidOutputOptionError,
            match="Unsupported format"
        ):
            extract_frames(
                placeholder,
                tmp_path / "out",
                image_format=image_format
            )

    @pytest.mark.parametrize(
        "jpeg_quality",
        [
            pytest.param(1, id="just-below-min"),
            pytest.param(0, id="zero"),
            pytest.param(-5, id="negative"),
            pytest.param(32, id="just-above-max"),
            pytest.param(100, id="far-above-max"),
        ]
    )

    def test_invalid_quality_raises(
        self,
        tmp_path: Path,
        jpeg_quality: int
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(
            InvalidOutputOptionError,
            match="jpeg-quality"
        ):
            extract_frames(
                placeholder,
                tmp_path / "out",
                image_format="jpg",
                jpeg_quality=jpeg_quality
            )

    def test_cli_accepts_format_and_quality(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "extract_frames.py",
                str(sample_video),
                str(tmp_path / "out"),
                "--end", "0.5",
                "--format", "jpg",
                "--jpeg-quality", "10"
            ]
        )

        assert main() == 0
        assert "Extracted 5 frame(s)" in capsys.readouterr().out
        assert sorted((tmp_path / "out").glob("*.jpg"))

    def test_cli_rejects_unknown_format(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """argparse handles this one, exiting 2 before extract_frames is
        reached.
        """
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "extract_frames.py",
                str(tmp_path / "x.mp4"),
                str(tmp_path / "out"),
                "--format",
                "gif"
            ]
        )

        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2

