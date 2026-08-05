"""Tests for extract_frames."""

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

import frame_extractor.extractor as ef
from frame_extractor import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    InvalidOutputOptionError,
    InvalidTimeRangeError,
    OutputDirectoryError,
    VideoFileError,
    extract_frames,
)
from frame_extractor.cli import main
from frame_extractor.extractor import build_ffmpeg_command


def _digest(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("start_time", "end_time", "expected_count"),
    [
        pytest.param(0.0, None, 20, id="whole-video"),
        pytest.param(1.0, None, 10, id="start-to-end"),
        pytest.param(0.0, 1.0, 10, id="start-of-video"),
        pytest.param(0.5, 1.5, 10, id="middle-of-video"),
        pytest.param(1.5, 2.0, 5, id="end-of-video"),
        pytest.param(0.0, 0.3, 3, id="very-short-range"),
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
    frames = extract_frames(
        sample_video, tmp_path / "out", start_time, end_time
    )
    assert len(frames) == expected_count


def test_range_is_half_open(
    sample_video: Path, tmp_path: Path, sample_frame_count: int
) -> None:
    """The frame at exactly end_time is excluded, so adjacent ranges do not
    overlap"""
    first = extract_frames(sample_video, tmp_path / "first", 0.0, 1.0)
    second = extract_frames(sample_video, tmp_path / "second", 1.0, 2.0)
    assert len(first) + len(second) == sample_frame_count


def test_seek_is_frame_accurate(sample_video: Path, tmp_path: Path) -> None:
    """Seeking to 1.0s yields the same image as frame 11 of a full extraction.

    This is the important one: a keyframe-only seek would return a nearby but
    different frame, which a count-based assertion would not catch.
    """
    everything = extract_frames(sample_video, tmp_path / "all")
    seeked = extract_frames(sample_video, tmp_path / "seeked", 1.0, None)
    assert _digest(seeked[0]) == _digest(everything[10])


def test_creates_missing_output_directory(
    sample_video: Path, tmp_path: Path
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
        with pytest.raises(VideoFileError, match="ffprobe could not read"):
            extract_frames(broken, tmp_path / "out")

    def test_ffmpeg_failure_after_successful_probe(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the probe succeeds but ffmpeg still fails, the error carries its
        stderr.
        """
        broken = tmp_path / "broken.mp4"
        broken.write_text("this is definitely not a video")
        monkeypatch.setattr(ef, "probe_duration", lambda _path, _ffprobe: 10.0)

        with pytest.raises(FFmpegExecutionError) as excinfo:
            extract_frames(broken, tmp_path / "out")

        assert excinfo.value.returncode != 0
        assert excinfo.value.stderr, "ffmpeg's diagnosis should be captured"

    @pytest.mark.parametrize(
        "start_time",
        [
            pytest.param(2.0, id="exactly-at-duration"),
            pytest.param(99.0, id="far-past-end"),
        ],
    )
    def test_start_past_duration_raises(
        self, sample_video: Path, tmp_path: Path, start_time: float
    ) -> None:
        """Last silent failure: this reported success with zero frames."""

        with pytest.raises(
            InvalidTimeRangeError, match="past the end of the video"
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
            lambda name: None if name == "ffprobe" else "/usr/bin/ffmpeg",
        )

        with pytest.raises(FFmpegNotFoundError, match="ffprobe was not found"):
            extract_frames(placeholder, tmp_path / "out")

    def test_no_output_directory_left_behind_on_invalid_range(
        self, tmp_path: Path
    ) -> None:
        """Validation runs before mkdir, so a rejection creates nothing."""
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
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(tmp_path / "nope.mp4"),
                str(tmp_path / "out"),
            ],
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
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                "--end",
                "0.5",
            ],
        )
        exit_code = main()
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "Extracted 5 frame(s)" in captured.out


class TestOutputFormats:
    """Format selection, quality control, and their validation."""

    def test_png_is_the_default(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", 0.0, 0.5)
        assert len(frames) == 5
        assert all(f.suffix == ".png" for f in frames)

    def test_jpeg_output_uses_jpg_extension(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(
            sample_video, tmp_path / "out", 0.0, 0.5, image_format="jpg"
        )
        assert len(frames) == 5
        assert all(f.suffix == ".jpg" for f in frames)
        assert frames[0].name == "frame_000001.jpg"

    def test_lower_quality_produces_smaller_files(
        self, sample_video: Path, tmp_path: Path
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
            jpeg_quality=2,
        )
        worst = extract_frames(
            sample_video,
            tmp_path / "worst",
            0.0,
            0.5,
            image_format="jpg",
            jpeg_quality=31,
        )
        worst_bytes = sum(f.stat().st_size for f in worst)
        best_bytes = sum(f.stat().st_size for f in best)
        assert worst_bytes < best_bytes

    def test_quality_does_not_affect_png(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        default = extract_frames(sample_video, tmp_path / "a", 0.0, 0.5)
        explicit = extract_frames(
            sample_video,
            tmp_path / "b",
            0.0,
            0.5,
            image_format="png",
            jpeg_quality=31,
        )
        assert [f.read_bytes() for f in default] == [
            f.read_bytes() for f in explicit
        ]

    @pytest.mark.parametrize(
        "image_format",
        [
            pytest.param("bmp", id="unsupported-format"),
            pytest.param("jpeg", id="jpg-spelled-out"),
            pytest.param("PNG", id="wrong-case"),
            pytest.param("", id="empty"),
        ],
    )
    def test_invalid_format_raises(
        self, tmp_path: Path, image_format: str
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(
            InvalidOutputOptionError, match="Unsupported format"
        ):
            extract_frames(
                placeholder, tmp_path / "out", image_format=image_format
            )

    @pytest.mark.parametrize(
        "jpeg_quality",
        [
            pytest.param(1, id="just-below-min"),
            pytest.param(0, id="zero"),
            pytest.param(-5, id="negative"),
            pytest.param(32, id="just-above-max"),
            pytest.param(100, id="far-above-max"),
        ],
    )
    def test_invalid_quality_raises(
        self, tmp_path: Path, jpeg_quality: int
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(InvalidOutputOptionError, match="jpeg-quality"):
            extract_frames(
                placeholder,
                tmp_path / "out",
                image_format="jpg",
                jpeg_quality=jpeg_quality,
            )

    def test_cli_accepts_format_and_quality(
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
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                "--end",
                "0.5",
                "--format",
                "jpg",
                "--jpeg-quality",
                "10",
            ],
        )

        assert main() == 0
        assert "Extracted 5 frame(s)" in capsys.readouterr().out
        assert sorted((tmp_path / "out").glob("*.jpg"))

    def test_cli_rejects_unknown_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """argparse handles this one, exiting 2 before extract_frames is
        reached.
        """
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(tmp_path / "x.mp4"),
                str(tmp_path / "out"),
                "--format",
                "gif",
            ],
        )

        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2


class TestOverwriteGuard:
    """Re-running into a populated directory is an error unless asked for."""

    def test_rerun_without_overwrite_raises(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"
        extract_frames(sample_video, output_dir, 0.0, 0.5)
        with pytest.raises(OutputDirectoryError, match="--overwrite"):
            extract_frames(sample_video, output_dir, 0.0, 0.5)

    def test_rerun_with_overwrite_succeeds(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"
        extract_frames(sample_video, output_dir, 0.0, 0.5)
        frames = extract_frames(
            sample_video, output_dir, 0.0, 0.5, overwrite=True
        )
        assert len(frames) == 5

    def test_overwrite_removes_stale_frames(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """A shorter second run must not leave the tail of the first behind."""
        output_dir = tmp_path / "out"
        first = extract_frames(sample_video, output_dir, 0.0, 2.0)
        assert len(first) == 20

        second = extract_frames(
            sample_video, output_dir, 0.0, 1.0, overwrite=True
        )

        assert len(second) == 10
        assert len(sorted(output_dir.glob("frame_*.png"))) == 10

    def test_rejected_rerun_leaves_the_directory_untouched(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "out"
        original = extract_frames(sample_video, output_dir, 0.0, 0.5)
        digests_before = [f.read_bytes() for f in original]

        with pytest.raises(OutputDirectoryError):
            extract_frames(sample_video, output_dir, 1.0, 1.5)

        assert [
            f.read_bytes() for f in sorted(output_dir.glob("frame_*.png"))
        ] == digests_before

    def test_a_different_format_is_not_blocked(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """PNG frames aren't overwritten by a JPEG run, so it isn't an error."""
        output_dir = tmp_path / "out"
        extract_frames(sample_video, output_dir, 0.0, 0.5)
        frames = extract_frames(
            sample_video, output_dir, 0.0, 0.5, image_format="jpg"
        )

        assert len(frames) == 5
        assert len(sorted(output_dir.glob("frame_*.png"))) == 5

    def test_unrelated_files_are_preserved(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """Only this run's own frame_*.<format> files are removed."""
        output_dir = tmp_path / "out"
        extract_frames(sample_video, output_dir, 0.0, 0.5)
        notes = output_dir / "notes.txt"
        notes.write_text("keep me")

        extract_frames(sample_video, output_dir, 0.0, 0.5, overwrite=True)

        assert notes.read_text() == "keep me"

    def test_no_directory_created_when_rejected(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """A rejected request must not leave an empty directory behind."""
        output_dir = tmp_path / "never"
        with pytest.raises(InvalidTimeRangeError):
            extract_frames(sample_video, output_dir, 99.0)
        assert not output_dir.exists()

    def test_cli_overwrite_flag(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output_dir = tmp_path / "out"
        extract_frames(sample_video, output_dir, 0.0, 0.5)

        base = [
            "frame-extractor",
            str(sample_video),
            str(output_dir),
            "--end",
            "0.5",
        ]

        monkeypatch.setattr(sys, "argv", base)
        assert main() == 1
        assert "--overwrite" in capsys.readouterr().err

        monkeypatch.setattr(sys, "argv", [*base, "--overwrite"])
        assert main() == 0
        assert "Extracted 5 frame(s)" in capsys.readouterr().out


class TestBuildFfmpegCommand:
    """The command builder is pure, so these run withoout ffme installed."""

    def test_seeks_on_the_input_not_the_output(self) -> None:
        """-ss must precede -i, or the seek stops being fast and
        frame-accurate.
        """
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), 1.5
        )
        assert command.index("-ss") < command.index("-i")
        assert command[command.index("-ss") + 1] == "1.500000"

    def test_clip_length_is_a_duration_not_an_end_timestamp(self) -> None:
        """-to would be read relative to the seek position; -t is
        unambiguous.
        """
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), 1.5, 4.0
        )
        assert "-to" not in command
        assert "-t" in command
        assert command[command.index("-t") + 1] == "2.500000"

    def test_open_ended_range_omits_the_duration_flag(self) -> None:
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), 1.0, None
        )
        assert "-t" not in command

    @pytest.mark.parametrize(
        ("image_format", "expected"),
        [
            pytest.param("png", False, id="png"),
            pytest.param("jpg", True, id="jpg"),
        ],
    )
    def test_quality_flag_is_jpeg_only(
        self, image_format: str, expected: bool
    ) -> None:
        command = build_ffmpeg_command(
            "ffmpeg",
            Path("in.mp4"),
            Path("out"),
            image_format=image_format,
            jpeg_quality=7,
        )
        assert ("-q:v" in command) is expected

    def test_output_pattern_carries_the_chosen_extension(self) -> None:
        command = build_ffmpeg_command(
            "ffmpeg",
            Path("in.mp4"),
            Path("out"),
            image_format="jpg",
            jpeg_quality=7,
        )
        assert command[-1] == str(Path("out") / "frame_%06d.jpg")

    def test_frames_are_passed_through_unresampled(self) -> None:
        """-vsync 0 is what keeps the frame count equal to the source's."""
        command = build_ffmpeg_command("ffmpeg", Path("in.mp4"), Path("out"))
        assert command[command.index("-vsync") + 1] == "0"

    def test_never_prompts_on_an_existing_file(self) -> None:
        command = build_ffmpeg_command("ffmpeg", Path("in.mp4"), Path("out"))
        assert "-y" in command
        assert "-n" not in command

    def test_uses_the_resolved_binary_path(self) -> None:
        command = build_ffmpeg_command(
            "/opt/bin/ffmpeg", Path("in.mp4"), Path("out")
        )
        assert command[0] == "/opt/bin/ffmpeg"


class TestFrameSampling:
    """--fps resamples instead of extracting every frame."""

    @pytest.mark.parametrize(
        ("fps", "expected_count"),
        [
            pytest.param(1.0, 2, id="one-per-second"),
            pytest.param(2.0, 4, id="two-per-second"),
            pytest.param(0.5, 1, id="one-per-two-seconds"),
            pytest.param(10.0, 20, id="matching-source-rate"),
        ],
    )

    def test_sampled_frame_count(
        self,
        sample_video: Path,
        tmp_path: Path,
        fps: float,
        expected_count: int,
    ) -> None:
        """The 2s fixture yields fps * 2 frames."""
        frames = extract_frames(sample_video, tmp_path / "out", fps=fps)
        assert len(frames) == expected_count

    def test_sampling_applies_within_the_range(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """The rate is per second of the extracted window, not the file."""
        frames = extract_frames(
        sample_video, tmp_path / "out", 0.5, 1.5, fps=2.0
    )
        assert len(frames) == 2

    def test_default_extracts_every_frame(
        self, sample_video: Path, tmp_path: Path, sample_frame_count: int
    ) -> None:
        """Omitting fps leaves the existing behaviour untouched."""
        frames = extract_frames(sample_video, tmp_path / "out")
        assert len(frames) == sample_frame_count

    def test_sampling_combines_with_jpeg(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(
            sample_video, tmp_path / "out", fps=1.0, image_format="jpg"
        )
        assert len(frames) == 2
        assert all(f.suffix == ".jpg" for f in frames)

    @pytest.mark.parametrize(
        "fps",
        [
            pytest.param(0.0, id="zero"),
            pytest.param(-1.0, id="negative"),
        ],
    )

    def test_non_positive_fps_raises(
        self, tmp_path: Path, fps: float
    ) -> None:
        """ffmpeg's own error names a timebase, not the flag at fault."""
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(InvalidOutputOptionError, match="--fps"):
            extract_frames(placeholder, tmp_path / "out", fps=fps)

    def test_filter_is_absent_by_default(self) -> None:
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out")
        )
        assert "-vf" not in command

    def test_filter_carries_the_requested_rate(self) -> None:
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), fps=0.5
        )
        assert command[command.index("-vf") + 1] == "fps=0.5"

    def test_cli_accepts_fps(
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
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                "--fps",
                "1",
            ],
        )
        assert main() == 0
        assert "Extracted 2 frame(s)" in capsys.readouterr().out

