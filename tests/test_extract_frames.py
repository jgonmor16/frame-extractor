"""Tests for extract_frames."""

import csv
import hashlib
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

import frame_extractor.extractor as ef
from frame_extractor import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    Frame,
    IncompleteExtractionWarning,
    InvalidOutputOptionError,
    InvalidTimeRangeError,
    OutputDirectoryError,
    VideoFileError,
    extract_frames,
)
from frame_extractor.cli import main
from frame_extractor.extractor import (
    _frame_number,
    _sorted_frames,
    build_ffmpeg_command,
)
from frame_extractor.ffmpeg_utils import (
    Progress,
    VideoInfo,
    _parse_frame_rate,
    _parse_progress_block,
    decode_problems,
    parse_frame_times,
    probe_video_info,
    require_binaries,
    run_ffmpeg,
)


def _digest(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dimensions(path: Path) -> tuple[int, int]:
    """Return the pixel size of an image, via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    width, height = result.stdout.strip().split(",")
    return int(width), int(height)


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
    assert _digest(seeked[0].path) == _digest(everything[10].path)


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
    assert [f.path for f in frames] == sorted(f.path for f in frames)
    assert [f.path.name for f in frames[:3]] == [
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
        monkeypatch.setattr(
            ef,
            "probe_video_info",
            lambda _path, _ffprobe: VideoInfo(duration=10.0, frame_rate=None),
        )

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
        assert all(f.path.suffix == ".png" for f in frames)

    def test_jpeg_output_uses_jpg_extension(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(
            sample_video, tmp_path / "out", 0.0, 0.5, image_format="jpg"
        )
        assert len(frames) == 5
        assert all(f.path.suffix == ".jpg" for f in frames)
        assert frames[0].path.name == "frame_000001.jpg"

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
        worst_bytes = sum(f.path.stat().st_size for f in worst)
        best_bytes = sum(f.path.stat().st_size for f in best)
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
        assert [f.path.read_bytes() for f in default] == [
            f.path.read_bytes() for f in explicit
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
        digests_before = [f.path.read_bytes() for f in original]

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
        assert all(f.path.suffix == ".jpg" for f in frames)

    @pytest.mark.parametrize(
        "fps",
        [
            pytest.param(0.0, id="zero"),
            pytest.param(-1.0, id="negative"),
        ],
    )
    def test_non_positive_fps_raises(self, tmp_path: Path, fps: float) -> None:
        """ffmpeg's own error names a timebase, not the flag at fault."""
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(InvalidOutputOptionError, match="--fps"):
            extract_frames(placeholder, tmp_path / "out", fps=fps)

    def test_filter_is_absent_by_default(self) -> None:
        command = build_ffmpeg_command("ffmpeg", Path("in.mp4"), Path("out"))
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


class TestFrameRateCeiling:
    """A rate above the source's own duplicates frames, so it is rejected."""

    @pytest.mark.parametrize(
        "fps",
        [
            pytest.param(1.0, id="well-below"),
            pytest.param(9.0, id="just-below"),
            pytest.param(10.0, id="exactly-the-source-rate"),
        ],
    )
    def test_rate_up_to_the_source_is_allowed(
        self, sample_video: Path, tmp_path: Path, fps: float
    ) -> None:
        """The fixture is 10fps."""
        frames = extract_frames(sample_video, tmp_path / "out", fps=fps)
        assert frames

    @pytest.mark.parametrize(
        "fps",
        [
            pytest.param(11.0, id="just-above"),
            pytest.param(20.0, id="double"),
            pytest.param(120.0, id="far-above"),
        ],
    )
    def test_rate_above_the_source_raises(
        self, sample_video: Path, tmp_path: Path, fps: float
    ) -> None:
        """ffmpeg would accept these and duplicate frames, exiting zero."""
        with pytest.raises(InvalidOutputOptionError, match="above the video"):
            extract_frames(sample_video, tmp_path / "out", fps=fps)

    def test_a_rounding_slip_is_tolerated(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Asking for 30 against NTSC's 29.97 is a slip, not a mistake."""
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        monkeypatch.setattr(
            ef,
            "probe_video_info",
            lambda _path, _ffprobe: VideoInfo(duration=10.0, frame_rate=29.97),
        )
        # Reaches ffmpeg, which fails on the empty placeholder; the point
        # is that the rate check did not reject it first.
        with pytest.raises(FFmpegExecutionError):
            extract_frames(placeholder, tmp_path / "out", fps=30.0)

    def test_an_unknown_source_rate_skips_the_check(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Some containers report no usable rate; not the user's fault."""
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        monkeypatch.setattr(
            ef,
            "probe_video_info",
            lambda _path, _ffprobe: VideoInfo(duration=10.0, frame_rate=None),
        )
        with pytest.raises(FFmpegExecutionError):
            extract_frames(placeholder, tmp_path / "out", fps=1000.0)

    def test_the_check_only_applies_to_sampling(
        self, sample_video: Path, tmp_path: Path, sample_frame_count: int
    ) -> None:
        """Extracting every frame is never a rate request."""
        frames = extract_frames(sample_video, tmp_path / "out")
        assert len(frames) == sample_frame_count


class TestProbeVideoInfo:
    """One ffprobe call returns both the duration and the frame rate."""

    def test_reports_duration_and_rate(self, sample_video: Path) -> None:
        _, ffprobe_path = require_binaries()
        info = probe_video_info(sample_video, ffprobe_path)
        assert info.duration == pytest.approx(2.0, abs=0.05)
        assert info.frame_rate == pytest.approx(10.0)

    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            pytest.param("30/1", 30.0, id="whole"),
            pytest.param("30000/1001", 29.97, id="ntsc"),
            pytest.param("24000/1001", 23.976, id="film-ntsc"),
            pytest.param("0/0", None, id="unknown"),
            pytest.param("", None, id="empty"),
            pytest.param("abc", None, id="unparseable"),
        ],
    )
    def test_fractional_rates_are_parsed(
        self, reported: str, expected: float | None
    ) -> None:
        """ffprobe reports a fraction, so NTSC arrives as 30000/1001."""
        result = _parse_frame_rate(reported)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected, abs=0.001)

    def test_unreadable_file_still_raises(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        _, ffprobe_path = require_binaries()
        broken = tmp_path / "broken.mp4"
        broken.write_text("this is definitely not a video")
        with pytest.raises(VideoFileError, match="ffprobe could not read"):
            probe_video_info(broken, ffprobe_path)


class TestProgress:
    """The Progress value reported to a callback."""

    @pytest.mark.parametrize(
        ("done", "total", "expected"),
        [
            pytest.param(0.0, 20.0, 0.0, id="start"),
            pytest.param(5.0, 20.0, 0.25, id="quarter"),
            pytest.param(20.0, 20.0, 1.0, id="complete"),
            pytest.param(21.0, 20.0, 1.0, id="overshoot-clamped"),
        ],
    )
    def test_fraction(self, done: float, total: float, expected: float) -> None:
        assert Progress(done, total, 0).fraction == pytest.approx(expected)

    @pytest.mark.parametrize(
        "total",
        [pytest.param(None, id="unknown"), pytest.param(0.0, id="zero")],
    )
    def test_fraction_is_none_without_a_usable_total(
        self, total: float | None
    ) -> None:
        """An open-ended or empty range has no meaningful percentage."""
        assert Progress(5.0, total, 0).fraction is None

    def test_out_time_us_is_read_not_out_time_ms(self) -> None:
        """ffmpeg's out_time_ms is microseconds too, despite the name."""
        block = {
            "frame": "42",
            "out_time_us": "2500000",
            "out_time_ms": "2500000",
            "progress": "continue",
        }
        parsed = _parse_progress_block(block, 10.0)
        assert parsed is not None
        assert parsed.seconds_done == pytest.approx(2.5)
        assert parsed.frames_written == 42

    def test_final_block_reports_the_range_as_covered(self) -> None:
        """The last frame sits short of the end by up to one interval."""
        block = {"frame": "50", "out_time_us": "9800000", "progress": "end"}
        parsed = _parse_progress_block(block, 10.0)
        assert parsed is not None
        assert parsed.fraction == 1.0

    @pytest.mark.parametrize(
        "block",
        [
            pytest.param({"progress": "continue"}, id="no-position"),
            pytest.param(
                {"out_time_us": "N/A", "progress": "continue"},
                id="unparseable-position",
            ),
        ],
    )
    def test_unusable_blocks_are_skipped(self, block: dict[str, str]) -> None:
        assert _parse_progress_block(block, 10.0) is None


class TestProgressReporting:
    """extract_frames reports progress without printing anything itself."""

    def test_callback_receives_updates(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        seen: list[Progress] = []
        extract_frames(sample_video, tmp_path / "out", on_progress=seen.append)
        assert seen

    def test_updates_advance_and_finish(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        seen: list[Progress] = []
        extract_frames(
            sample_video, tmp_path / "out", 0.0, 1.0, on_progress=seen.append
        )
        positions = [update.seconds_done for update in seen]
        assert positions == sorted(positions)
        assert seen[-1].fraction == 1.0

    def test_total_is_the_requested_range_not_the_file(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """The 2s fixture, asked for one second, reports one second."""
        seen: list[Progress] = []
        extract_frames(
            sample_video, tmp_path / "out", 0.5, 1.5, on_progress=seen.append
        )
        assert seen[-1].seconds_total == pytest.approx(1.0)

    def test_frame_count_reaches_the_number_written(
        self, sample_video: Path, tmp_path: Path, sample_frame_count: int
    ) -> None:
        seen: list[Progress] = []
        frames = extract_frames(
            sample_video, tmp_path / "out", on_progress=seen.append
        )
        assert len(frames) == sample_frame_count
        assert seen[-1].frames_written == sample_frame_count

    def test_extraction_is_unchanged_by_reporting(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """The same frames come out whether or not a callback is given."""
        plain = extract_frames(sample_video, tmp_path / "plain", 0.0, 0.5)
        watched = extract_frames(
            sample_video,
            tmp_path / "watched",
            0.0,
            0.5,
            on_progress=lambda _: None,
        )
        assert [p.path.name for p in plain] == [p.path.name for p in watched]
        assert [p.path.read_bytes() for p in plain] == [
            p.path.read_bytes() for p in watched
        ]

    def test_errors_still_carry_stderr(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reading stdout for progress must not lose ffmpeg's diagnosis."""
        broken = tmp_path / "broken.mp4"
        broken.write_text("this is definitely not a video")
        monkeypatch.setattr(
            ef,
            "probe_video_info",
            lambda _path, _ffprobe: VideoInfo(duration=10.0, frame_rate=None),
        )
        with pytest.raises(FFmpegExecutionError) as excinfo:
            extract_frames(broken, tmp_path / "out", on_progress=lambda _: None)
        assert excinfo.value.stderr

    def test_flag_is_absent_without_a_callback(self) -> None:
        command = build_ffmpeg_command("ffmpeg", Path("in.mp4"), Path("out"))
        assert "-progress" not in command

    def test_flag_is_present_when_requested(self) -> None:
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), report_progress=True
        )
        assert command[command.index("-progress") + 1] == "pipe:1"
        assert "-nostats" in command


class TestKeyframeSelection:
    """--keyframes decodes only key frames, so the rest cost nothing."""

    def test_extracts_fewer_frames_than_the_whole_range(
        self, sample_video: Path, tmp_path: Path, sample_frame_count: int
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", keyframes=True)
        assert 0 < len(frames) < sample_frame_count

    def test_respects_the_time_range(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        whole = extract_frames(sample_video, tmp_path / "all", keyframes=True)
        part = extract_frames(
            sample_video, tmp_path / "part", 0.0, 1.0, keyframes=True
        )
        assert len(part) <= len(whole)

    def test_combines_with_scaling(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """Selection and resizing answer different questions."""
        frames = extract_frames(
            sample_video, tmp_path / "out", keyframes=True, scale="32:auto"
        )
        assert frames
        assert _dimensions(frames[0].path) == (32, 32)

    def test_flag_is_an_input_option(self) -> None:
        """-skip_frame must precede -i, or the decoder never sees it."""
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), keyframes=True
        )
        assert command[command.index("-skip_frame") + 1] == "nokey"
        assert command.index("-skip_frame") < command.index("-i")

    def test_flag_is_absent_by_default(self) -> None:
        command = build_ffmpeg_command("ffmpeg", Path("in.mp4"), Path("out"))
        assert "-skip_frame" not in command


class TestSceneSelection:
    """--scenes keeps frames where the picture changes."""

    def test_extracts_fewer_frames_than_the_whole_range(
        self, sample_video: Path, tmp_path: Path, sample_frame_count: int
    ) -> None:
        """testsrc changes steadily, so a low threshold still selects some."""
        frames = extract_frames(
            sample_video, tmp_path / "out", scene_threshold=0.01
        )
        assert len(frames) < sample_frame_count

    def test_a_higher_threshold_selects_no_more(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        loose = extract_frames(
            sample_video, tmp_path / "loose", scene_threshold=0.01
        )
        strict = extract_frames(
            sample_video, tmp_path / "strict", scene_threshold=0.9
        )
        assert len(strict) <= len(loose)

    def test_filter_carries_the_threshold(self) -> None:
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), scene_threshold=0.4
        )
        assert command[command.index("-vf") + 1] == "select='gt(scene,0.4)'"

    def test_selection_precedes_scaling_in_the_chain(self) -> None:
        """Discard frames before spending work resizing them."""
        command = build_ffmpeg_command(
            "ffmpeg",
            Path("in.mp4"),
            Path("out"),
            scale="160:auto",
            scene_threshold=0.4,
        )
        assert command[command.index("-vf") + 1] == (
            "select='gt(scene,0.4)',scale=160:-1"
        )

    @pytest.mark.parametrize(
        "threshold",
        [
            pytest.param(1.5, id="above-one"),
            pytest.param(-0.1, id="negative"),
            pytest.param(100.0, id="far-above"),
        ],
    )
    def test_threshold_outside_zero_to_one_raises(
        self, tmp_path: Path, threshold: float
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(InvalidOutputOptionError, match="--scenes"):
            extract_frames(
                placeholder, tmp_path / "out", scene_threshold=threshold
            )

    @pytest.mark.parametrize(
        "threshold",
        [pytest.param(0.0, id="zero"), pytest.param(1.0, id="one")],
    )
    def test_the_bounds_are_allowed(
        self, sample_video: Path, tmp_path: Path, threshold: float
    ) -> None:
        extract_frames(
            sample_video, tmp_path / "out", scene_threshold=threshold
        )


class TestSelectionModesAreExclusive:
    """Each mode answers "which frames?" differently."""

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param(
                {"fps": 1.0, "keyframes": True},
                "--fps and --keyframes",
                id="fps-and-keyframes",
            ),
            pytest.param(
                {"fps": 1.0, "scene_threshold": 0.4},
                "--fps and --scenes",
                id="fps-and-scenes",
            ),
            pytest.param(
                {"keyframes": True, "scene_threshold": 0.4},
                "--keyframes and --scenes",
                id="keyframes-and-scenes",
            ),
        ],
    )
    def test_combining_modes_raises(
        self, tmp_path: Path, kwargs: dict[str, object], expected: str
    ) -> None:
        placeholder = tmp_path / "placeholder.mp4"
        placeholder.touch()
        with pytest.raises(InvalidOutputOptionError, match=expected):
            extract_frames(
                placeholder,
                tmp_path / "out",
                **kwargs,  # type: ignore[arg-type]
            )

    def test_cli_rejects_combined_modes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """argparse catches this before extract_frames is reached."""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(tmp_path / "x.mp4"),
                str(tmp_path / "out"),
                "--fps",
                "1",
                "--keyframes",
            ],
        )
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2

    def test_cli_accepts_keyframes(
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
                "--keyframes",
            ],
        )
        assert main() == 0
        assert "frame(s)" in capsys.readouterr().out


class TestFrameResult:
    """extract_frames returns Frames, not bare paths."""

    def test_returns_frames(self, sample_video: Path, tmp_path: Path) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", 0.0, 0.5)
        assert all(isinstance(frame, Frame) for frame in frames)

    def test_carries_path_and_index(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", 0.0, 0.5)
        assert [frame.index for frame in frames] == list(range(5))
        assert frames[0].path.name == "frame_000001.png"
        assert all(frame.path.exists() for frame in frames)

    def test_timestamp_is_none_unless_requested(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """Recovering them costs a pass, so nobody pays for it by default."""
        frames = extract_frames(sample_video, tmp_path / "out", 0.0, 0.5)
        assert all(frame.timestamp is None for frame in frames)

    def test_frames_stay_in_playback_order(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", 0.0, 1.0)
        assert [f.path for f in frames] == sorted(f.path for f in frames)


class TestTimestamps:
    """Where each frame sits in the source."""

    def test_every_frame_is_timed(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", timestamps=True)
        assert all(frame.timestamp is not None for frame in frames)

    def test_timestamps_are_absolute_not_relative_to_the_seek(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """showinfo reports from the seek point, so the offset is added."""
        frames = extract_frames(
            sample_video, tmp_path / "out", 1.0, 1.5, timestamps=True
        )
        assert frames[0].timestamp == pytest.approx(1.0)
        times = [f.timestamp for f in frames]
        assert all(1.0 <= t < 1.5 for t in times)  # type: ignore[operator]

    def test_timestamps_increase(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", timestamps=True)
        times = [frame.timestamp for frame in frames]
        assert times == sorted(times)  # type: ignore[type-var]

    def test_sampled_frames_land_on_the_requested_rate(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(
            sample_video, tmp_path / "out", fps=2.0, timestamps=True
        )
        assert [f.timestamp for f in frames] == pytest.approx(
            [0.0, 0.5, 1.0, 1.5]
        )

    def test_keyframes_are_timed(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """Their spacing is uneven, so computing from an index cannot work."""
        frames = extract_frames(
            sample_video, tmp_path / "out", keyframes=True, timestamps=True
        )
        assert frames
        assert all(frame.timestamp is not None for frame in frames)

    def test_one_timestamp_per_written_file(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """showinfo runs upstream of the muxer and can over-report."""
        frames = extract_frames(
            sample_video, tmp_path / "out", 0.0, 1.0, fps=3.0, timestamps=True
        )
        assert len(frames) == len(list((tmp_path / "out").glob("*.png")))
        assert all(frame.timestamp is not None for frame in frames)

    def test_flag_is_absent_by_default(self) -> None:
        command = build_ffmpeg_command("ffmpeg", Path("in.mp4"), Path("out"))
        assert "showinfo" not in " ".join(command)
        assert command[command.index("-loglevel") + 1] == "error"

    def test_flag_raises_the_log_level(self) -> None:
        """showinfo logs at info, so error level would discard it."""
        command = build_ffmpeg_command(
            "ffmpeg", Path("in.mp4"), Path("out"), report_times=True
        )
        assert command[command.index("-loglevel") + 1] == "info"
        assert command[command.index("-vf") + 1].endswith("showinfo")

    def test_showinfo_runs_last_in_the_chain(self) -> None:
        """It must see the frames that reach the muxer, not earlier ones."""
        command = build_ffmpeg_command(
            "ffmpeg",
            Path("in.mp4"),
            Path("out"),
            fps=1.0,
            scale="64:auto",
            report_times=True,
        )
        assert command[command.index("-vf") + 1] == (
            "fps=1.0,scale=64:-1,showinfo"
        )


class TestParseFrameTimes:
    """Turning showinfo's output into timestamps."""

    def test_trims_the_surplus_tail(self) -> None:
        """The muxer discards frames the filter already reported."""
        stderr = "pts_time:0 pts_time:1 pts_time:2 pts_time:3 pts_time:4"
        assert parse_frame_times(stderr, 3, 0.0) == [0.0, 1.0, 2.0]

    def test_adds_the_seek_offset(self) -> None:
        stderr = "pts_time:0 pts_time:1"
        assert parse_frame_times(stderr, 2, 5.0) == [5.0, 6.0]

    def test_no_output_gives_no_times(self) -> None:
        assert parse_frame_times("nothing here", 3, 0.0) == []

    def test_fewer_reported_than_written_is_not_an_error(self) -> None:
        """Should not happen, but is not worth crashing over."""
        assert parse_frame_times("pts_time:1", 5, 0.0) == [1.0]


class TestManifest:
    """--manifest writes the mapping out."""

    def test_writes_a_row_per_frame(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        manifest = tmp_path / "frames.csv"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                "--end",
                "0.5",
                "--manifest",
                str(manifest),
            ],
        )
        assert main() == 0

        rows = list(csv.DictReader(manifest.open(newline="")))
        assert len(rows) == 5
        assert rows[0]["index"] == "0"
        assert float(rows[0]["timestamp"]) == pytest.approx(0.0)

    def test_implies_timestamps(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Asking for the mapping without the data would be pointless."""
        manifest = tmp_path / "frames.csv"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                "--end",
                "0.5",
                "--manifest",
                str(manifest),
            ],
        )
        main()
        rows = list(csv.DictReader(manifest.open(newline="")))
        assert all(row["timestamp"] for row in rows)


class TestEmptyResultNote:
    """Writing nothing is a real answer, but should not look like a bug."""

    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            pytest.param(
                ["--scenes", "0.99"], "heuristic", id="scenes-too-strict"
            ),
            pytest.param(
                ["--keyframes", "--start", "1.5"],
                "encoder's choice",
                id="no-keyframe-in-range",
            ),
            pytest.param(
                ["--fps", "0.1", "--start", "1.0", "--end", "1.05"],
                "longer range",
                id="rate-too-low-for-range",
            ),
            pytest.param(
                ["--start", "1.99", "--end", "1.995"],
                "--start and --end",
                id="range-holds-no-frame",
            ),
        ],
    )
    def test_note_names_the_likely_cause(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        extra: list[str],
        expected: str,
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                *extra,
            ],
        )
        assert main() == 0

        captured = capsys.readouterr()
        assert "Extracted 0 frame(s)" in captured.out
        assert expected in captured.err

    def test_an_empty_result_is_not_a_failure(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A threshold nothing meets is a valid request with no matches."""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                "--scenes",
                "0.99",
            ],
        )
        assert main() == 0
        assert "error" not in capsys.readouterr().err

    def test_no_note_when_frames_were_written(
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
        assert main() == 0
        assert "note:" not in capsys.readouterr().err

    def test_the_note_goes_to_stderr(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """So the summary line stays pipeable."""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "frame-extractor",
                str(sample_video),
                str(tmp_path / "out"),
                "--scenes",
                "0.99",
            ],
        )
        main()
        captured = capsys.readouterr()
        assert "note:" not in captured.out
        assert "note:" in captured.err


class TestFrameOrdering:
    """Frames come back in playback order, past six digits included."""

    def test_numbers_beyond_six_digits_sort_after(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        """ffmpeg widens %06d rather than wrapping, so 1000000 follows
        999999 numerically while sorting before it as a string.
        """
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        for number in (999998, 999999, 1000000, 1000001):
            (output_dir / f"frame_{number:06d}.png").touch()

        ordered = _sorted_frames(output_dir, "png")

        assert [_frame_number(path) for path in ordered] == [
            999998,
            999999,
            1000000,
            1000001,
        ]

    def test_sorting_names_directly_would_be_wrong(
        self, tmp_path: Path
    ) -> None:
        """The bug this guards against, stated as a test."""
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        for number in (999999, 1000000):
            (output_dir / f"frame_{number:06d}.png").touch()

        lexicographic = sorted(output_dir.glob("frame_*.png"))
        numeric = _sorted_frames(output_dir, "png")

        assert lexicographic != numeric
        assert numeric[0].name == "frame_999999.png"

    def test_ordinary_numbering_is_unaffected(
        self, sample_video: Path, tmp_path: Path
    ) -> None:
        frames = extract_frames(sample_video, tmp_path / "out", 0.0, 1.0)
        assert [frame.path for frame in frames] == sorted(
            frame.path for frame in frames
        )
        assert [frame.index for frame in frames] == list(range(10))

    def test_frame_number_reads_the_suffix(self, tmp_path: Path) -> None:
        assert _frame_number(tmp_path / "frame_000042.png") == 42
        assert _frame_number(tmp_path / "frame_1000000.jpg") == 1000000


@pytest.fixture
def damaged_video(sample_video: Path, tmp_path: Path) -> Path:
    """A copy of the sample with a chunk of its stream overwritten.

    ffmpeg skips what it cannot decode and exits successfully, so this
    produces fewer frames than the source holds without failing.
    """
    damaged = tmp_path / "damaged.mp4"
    data = bytearray(sample_video.read_bytes())
    midpoint = len(data) // 2
    data[midpoint : midpoint + 400] = b"\xde\xad\xbe\xef" * 100
    damaged.write_bytes(bytes(data))
    return damaged


class TestDecodeProblems:
    """Separating ffmpeg's complaints from its ordinary chatter."""

    def test_a_clean_run_reports_nothing(self) -> None:
        assert decode_problems("") == []

    def test_component_messages_are_collected(self) -> None:
        stderr = (
            "[h264 @ 0x55] Invalid NAL unit size (-1 > 26).\n"
            "[vist#0:0/h264 @ 0x66] Error submitting packet to decoder\n"
        )
        assert len(decode_problems(stderr)) == 2

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param(
                "[out#0/image2 @ 0x55] video:4236kB muxing overhead: unknown",
                id="muxing-summary",
            ),
            pytest.param(
                "[Parsed_showinfo_1 @ 0x55] n:0 pts:0 pts_time:0",
                id="showinfo",
            ),
            pytest.param(
                "[swscaler @ 0x55] deprecated pixel format used",
                id="swscaler-notice",
            ),
        ],
    )
    def test_benign_components_are_ignored(self, line: str) -> None:
        """These appear on a healthy run at info level."""
        assert decode_problems(line) == []

    def test_lines_without_a_component_are_ignored(self) -> None:
        """Stream summaries and deprecation notices carry no prefix."""
        stderr = (
            "-vsync is deprecated. Use -fps_mode\n"
            "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'in.mp4':\n"
            "  Duration: 00:00:10.00, start: 0.000000, bitrate: 37 kb/s\n"
        )
        assert decode_problems(stderr) == []


class TestDamagedSource:
    """Damage makes ffmpeg drop frames and still exit zero."""

    def test_extraction_warns(
        self, damaged_video: Path, tmp_path: Path
    ) -> None:
        with pytest.warns(IncompleteExtractionWarning, match="damaged"):
            extract_frames(damaged_video, tmp_path / "out")

    def test_the_frames_that_survived_are_returned(
        self, damaged_video: Path, tmp_path: Path, sample_frame_count: int
    ) -> None:
        """Extracting what is readable is a legitimate thing to want."""
        with pytest.warns(IncompleteExtractionWarning):
            frames = extract_frames(damaged_video, tmp_path / "out")
        assert 0 < len(frames) < sample_frame_count

    def test_the_warning_can_be_promoted_to_an_error(
        self, damaged_video: Path, tmp_path: Path
    ) -> None:
        """So a pipeline that cannot accept a partial result may stop."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", IncompleteExtractionWarning)
            with pytest.raises(IncompleteExtractionWarning):
                extract_frames(damaged_video, tmp_path / "out")

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({}, id="default"),
            pytest.param({"fps": 5.0}, id="fps"),
            pytest.param({"keyframes": True}, id="keyframes"),
            pytest.param({"scale": "32:auto"}, id="scale"),
            pytest.param({"image_format": "jpg"}, id="jpeg"),
            pytest.param({"timestamps": True}, id="timestamps"),
            pytest.param(
                {"image_format": "jpg", "timestamps": True},
                id="jpeg-and-timestamps",
            ),
        ],
    )
    def test_a_healthy_source_never_warns(
        self, sample_video: Path, tmp_path: Path, kwargs: dict[str, object]
    ) -> None:
        """Info-level logging must not be mistaken for damage."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", IncompleteExtractionWarning)
            extract_frames(
                sample_video,
                tmp_path / "out",
                **kwargs,  # type: ignore[arg-type]
            )


class TestPartialOutputIsCleared:
    """A failed run should leave nothing behind."""

    def test_failure_removes_what_was_written(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Otherwise the overwrite guard blocks the obvious retry."""
        output_dir = tmp_path / "out"
        real_run = run_ffmpeg

        def fail_after_writing(*args: object, **kwargs: object) -> object:
            result = real_run(*args, **kwargs)  # type: ignore[arg-type]
            return subprocess.CompletedProcess(
                args=result.args, returncode=1, stdout="", stderr="boom"
            )

        monkeypatch.setattr(
            "frame_extractor.extractor.run_ffmpeg", fail_after_writing
        )

        with pytest.raises(FFmpegExecutionError):
            extract_frames(sample_video, output_dir, 0.0, 0.5)

        assert not list(output_dir.glob("frame_*.png"))

    def test_a_retry_is_not_blocked(
        self,
        sample_video: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        output_dir = tmp_path / "out"
        real_run = run_ffmpeg

        def fail_once(*args: object, **kwargs: object) -> object:
            result = real_run(*args, **kwargs)  # type: ignore[arg-type]
            return subprocess.CompletedProcess(
                args=result.args, returncode=1, stdout="", stderr="boom"
            )

        monkeypatch.setattr("frame_extractor.extractor.run_ffmpeg", fail_once)
        with pytest.raises(FFmpegExecutionError):
            extract_frames(sample_video, output_dir, 0.0, 0.5)

        monkeypatch.setattr("frame_extractor.extractor.run_ffmpeg", real_run)
        frames = extract_frames(sample_video, output_dir, 0.0, 0.5)
        assert len(frames) == 5
