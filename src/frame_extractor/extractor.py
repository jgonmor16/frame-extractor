"""Extract every frame from a video within a time range, as individual images.

Usage: python3 -m frame_extractor.extractor VIDEO OUTPUT_DIR
            [--start SECONDS] [--end SECONDS] [--format {png,jpg}]
            [--jpeg-quality N] [--fps N | --keyframes | --scenes THRESHOLD]
            [--scale W:H] [--overwrite]
"""

import re
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from frame_extractor.exceptions import (
    FFmpegExecutionError,
    IncompleteExtractionWarning,
    InvalidOutputOptionError,
    InvalidTimeRangeError,
    OutputDirectoryError,
    VideoFileError,
)
from frame_extractor.ffmpeg_utils import (
    Progress,
    decode_problems,
    parse_frame_times,
    probe_video_info,
    require_binaries,
    run_ffmpeg,
    strip_showinfo,
)


class Frame(NamedTuple):
    """One extracted frame.

    Attributes:
        path: Where the image was written.
        index: Position in this extraction, counting from zero.
        timestamp: Seconds into the source video, or None when
            timestamps were not requested. Recovering them costs an
            extra pass over the range, so it is opt-in.
    """

    path: Path
    # Shadows tuple.index, which is meaningless on a Frame. Losing the
    # method costs nothing; renaming the field to avoid it would make
    # every read site worse.
    index: int  # type: ignore[assignment]
    timestamp: float | None


SUPPORTED_FORMATS = ("png", "jpg")

# ffmpeg's -q:v scale for the mjpeg encoder: 2 is best, 31 is worst. Values
# outside this range are silently clamped rather than rejected, so they are
# checked instead of being passed through.
MIN_JPEG_QUALITY = 2
MAX_JPEG_QUALITY = 31

# WIDTH:HEIGHT, where either side may be a positive integer or a marker
# meaning "derive me from the other and the source aspect ratio". Strict by
# design: the value is interpolated into ffmpeg's -vf argument, so an
# unchecked comma would append filters the caller never asked for.
_SCALE_DIMENSION = r"(auto|-1|-2|[1-9]\d*)"
SCALE_PATTERN = re.compile(rf"^{_SCALE_DIMENSION}:{_SCALE_DIMENSION}$")

# ffmpeg spells "derive this one" as -1, or -2 to round to an even number.
# "auto" is accepted as a readable alias, and is the form the CLI documents:
# a value starting with a dash cannot be passed as `--scale -1:240`.
_DERIVED = {"auto": "-1", "-1": "-1", "-2": "-2"}

# ffmpeg's scene score runs from 0 to 1. Anything above about 0.4 is a
# clear cut; below 0.1 catches gradual change and a lot of noise.
MIN_SCENE_THRESHOLD = 0.0
MAX_SCENE_THRESHOLD = 1.0

# A request within this fraction of the source rate is treated as equal to
# it, so 30 against NTSC's 29.97 is a rounding slip rather than an error.
FPS_TOLERANCE = 0.01


def _validate_output_options(image_format: str, jpeg_quality: int) -> None:
    """Check the requested image format and quality.

    Raises:
        InvalidOutputOptionError: If the format is unsupported, or the quality
        falls outside ffmpeg's usable range
    """
    if image_format not in SUPPORTED_FORMATS:
        raise InvalidOutputOptionError(
            f"Unsupported format {image_format!r}; expected one of "
            f"{', '.join(SUPPORTED_FORMATS)}"
        )

    if not MIN_JPEG_QUALITY <= jpeg_quality <= MAX_JPEG_QUALITY:
        raise InvalidOutputOptionError(
            f"--jpeg-quality must be between {MIN_JPEG_QUALITY} (best) and "
            f"{MAX_JPEG_QUALITY} (worst), got {jpeg_quality}"
        )


def _validate_scale(scale: str | None) -> None:
    """Check the requested output size.

    Raises:
        InvalidOutputOptionError: If scale is not WIDTH:HEIGHT, or if both
        dimensions are derived, which resizes nothing.
    """
    if scale is None:
        return

    match = SCALE_PATTERN.match(scale)
    if match is None:
        raise InvalidOutputOptionError(
            f"--scale must be WIDTH:HEIGHT, got {scale!r}. Use 'auto' for "
            "either dimension to derive it from the other and the source "
            "aspect ratio, for example '640:auto'."
        )

    if match.group(1) in _DERIVED and match.group(2) in _DERIVED:
        raise InvalidOutputOptionError(
            f"--scale {scale!r} derives both dimensions from each other, "
            "which ffmpeg accepts but silently leaves the size unchanged. "
            "Give a number for at least one of them."
        )


def _validate_selection(
    fps: float | None, keyframes: bool, scene_threshold: float | None
) -> None:
    """Check that at most one way of choosing frames was asked for.

    Each mode answers "which frames?" differently, and ffmpeg would apply
    whichever combination it was handed without saying that the request
    was contradictory.

    Raises:
        InvalidOutputOptionError: If more than one mode is requested, or
            the scene threshold falls outside 0 to 1.
    """
    requested = [
        name
        for name, given in (
            ("--fps", fps is not None),
            ("--keyframes", keyframes),
            ("--scenes", scene_threshold is not None),
        )
        if given
    ]
    if len(requested) > 1:
        raise InvalidOutputOptionError(
            f"{' and '.join(requested)} each choose frames a different "
            "way, so only one of them can be used at a time."
        )

    if scene_threshold is None:
        return

    if not MIN_SCENE_THRESHOLD <= scene_threshold <= MAX_SCENE_THRESHOLD:
        raise InvalidOutputOptionError(
            f"--scenes must be between {MIN_SCENE_THRESHOLD} and "
            f"{MAX_SCENE_THRESHOLD}, got {scene_threshold}"
        )


def _validate_sampling(fps: float | None) -> None:
    """Check the requested sampling rate.

    ffmpeg rejects a non-positive rate with "The encoder timebase is not
    set", which says nothing about the flag that caused it.

    Raises:
        InvalidOutputOptionError: If fps is zero or negative.
    """
    if fps is not None and fps <= 0:
        raise InvalidOutputOptionError(
            f"--fps must be greater than 0, got {fps}"
        )


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
            f"--end ({end_time}) must be greater than --start ({start_time})"
        )


def _frame_number(path: Path) -> int:
    """Return the number ffmpeg gave a frame file.

    Sorting the names directly breaks past 999999: the %06d pattern is a
    minimum width, not a maximum, so ffmpeg widens the field rather than
    wrapping and frame_1000000 sorts before frame_999999.
    """
    return int(path.stem.rsplit("_", 1)[1])


def _sorted_frames(output_dir: Path, image_format: str) -> list[Path]:
    """Return this run's frame files in playback order."""
    return sorted(output_dir.glob(f"frame_*.{image_format}"), key=_frame_number)


def _prepare_output_directory(
    output_dir: Path,
    image_format: str,
    overwrite: bool,
) -> None:
    """Make output directory ready.

    Frames from an earlier run are removed rather than written over. ffmpeg
    numbers its output from 1 each time, so a shorter second run would leave
    the tail of the first behind and the returned list would report frames
    this extraction never produced.

    Raises:
        OutputDirectoryError: If frames are present and overwrite is False.
    """
    pattern = f"frame_*.{image_format}"
    existing = sorted(output_dir.glob(pattern)) if output_dir.is_dir() else []

    if existing and not overwrite:
        raise OutputDirectoryError(
            f"'{output_dir}' already holds {len(existing)} file(s) matching "
            f"'{pattern}'. Pass --overwrite to replace them, or choose an "
            "empty directory."
        )

    for frame in existing:
        frame.unlink()

    output_dir.mkdir(parents=True, exist_ok=True)


def build_ffmpeg_command(
    ffmpeg_path: str,
    video_path: Path,
    output_dir: Path,
    start_time: float = 0.0,
    end_time: float | None = None,
    *,
    image_format: str = "png",
    jpeg_quality: int = MIN_JPEG_QUALITY,
    fps: float | None = None,
    keyframes: bool = False,
    scene_threshold: float | None = None,
    scale: str | None = None,
    report_progress: bool = False,
    report_times: bool = False,
) -> list[str]:
    """Build the ffmpeg argument list for one extraction.

    ``-ss`` precedes ``-i`` so ffmpeg seeks on the input rather than
    decoding and discarding everything before ``start_time``. The clip
    length is a duration (``-t``), not an end timestamp (``-to``), which
    ffmpeg would read relative to the seek position. ``-vsync 0`` passes
    every decoded frame through, so none are duplicated or dropped

    Returns:
        The complete argument list, ready for ``subprocess.run``
    """
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        # showinfo logs at info level, so asking for timestamps means accepting
        # the rest of ffmpeg's chatter and filtering it out.
        "info" if report_times else "error",
    ]
    if report_progress:
        # Machine-readable updates on stdout; -nostats silences the
        # human-readable ones that would otherwise go to stderr.
        command += ["-progress", "pipe:1", "-nostats"]
    if keyframes:
        # An input option: it tells the decoder to skip non-key frames
        # rather than filtering them out afterwards, which is why it is
        # so much faster than selecting on key_frame downstream.
        command += ["-skip_frame", "nokey"]
    command += [
        "-ss",
        f"{start_time:.6f}",
        "-i",
        str(video_path),
    ]

    if end_time is not None:
        command += ["-t", f"{end_time - start_time:.6f}"]
    filters = []
    if scene_threshold is not None:
        filters.append(f"select='gt(scene,{scene_threshold})'")
    if fps is not None:
        filters.append(f"fps={fps}")
    if scale is not None:
        width, height = scale.split(":")
        filters.append(
            f"scale={_DERIVED.get(width, width)}:{_DERIVED.get(height, height)}"
        )
    if report_times:
        # Last in the chain, so it sees exactly the frames that reach
        # the muxer rather than the ones an earlier filter discarded.
        filters.append("showinfo")
    if filters:
        command += ["-vf", ",".join(filters)]
    if image_format == "jpg":
        command += ["-q:v", str(jpeg_quality)]
    command += [
        "-y",
        "-vsync",
        "0",
        str(output_dir / f"frame_%06d.{image_format}"),
    ]

    return command


def extract_frames(
    video_path: Path,
    output_dir: Path,
    start_time: float = 0.0,
    end_time: float | None = None,
    *,
    image_format: str = "png",
    jpeg_quality: int = MIN_JPEG_QUALITY,
    overwrite: bool = False,
    fps: float | None = None,
    keyframes: bool = False,
    scene_threshold: float | None = None,
    scale: str | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    timestamps: bool = False,
) -> list[Frame]:
    """Extract every frame of a video as an image within [start_time, end_time).

    Args:
        video_path: Path to the source video file.
        output_dir: Directory to write frames to.
        start_time: Time from when the extraction will begin, inclusive.
        end_time: Time when the frame extraction will end, exclusive.
        image_format: Output image format, either "png" or "jpg".
        jpeg_quality: ffmpeg ``-q:v`` value for JPEG output, from 2 (best) to
            31 (worst). Ignored for PNG, which is lossless.
        overwrite: Whether to replace frames from an earlier extraction in
            ``output_dir``. When False, their presence is an error.
        fps: Frames to extract per second of video. ``None`` extracts every
            frame. A rate above the source's own is rejected, since ffmpeg
            would duplicate frames rather than find new ones.
        keyframes: Extract only the video's key frames. Far faster than
            decoding everything, since non-key frames are never decoded,
            but their spacing is decided by the encoder rather than by you.
        scene_threshold: Extract only frames where the picture changes by
            more than this, from 0 to 1. Around 0.4 catches clear cuts.
            Detection is a heuristic and misses gradual transitions.
        scale: Output size as ``"WIDTH:HEIGHT"``. Either side may be
            ``"auto"`` to derive it from the other and the source aspect
            ratio, as in ``"640:auto"``. ``None`` keeps the source size.
        on_progress: Called with a :class:`Progress` as ffmpeg decodes.
            The library never prints; reporting is the caller's to do.
        timestamps: Record where each frame sits in the source. Costs an
            extra pass over the range, so each Frame's timestamp is None
            unless this is set.

    Returns:
        The extracted frames in playback order, each carrying its path, its
        index and its timestamp when one was requested.

    Raises:
        VideoFileError: If the input video does not exist.
        InvalidTimeRangeError: If the requested range is negative or inverted.
        InvalidOutputOptionError: If the format or JPEG quality is unusable.
        OutputDirectoryError: If output_dir holds frames and overwrite is False.
        FFmpegNotFoundError: If ffmpeg or ffprobe are not installed.
        FFmpegExecutionError: If ffmpeg exits non-zero.
    """
    _validate_request(video_path, start_time, end_time)
    _validate_output_options(image_format, jpeg_quality)
    _validate_sampling(fps)
    _validate_selection(fps, keyframes, scene_threshold)
    _validate_scale(scale)
    ffmpeg_path, ffprobe_path = require_binaries()

    info = probe_video_info(video_path, ffprobe_path)
    if (
        fps is not None
        and info.frame_rate is not None
        and fps > info.frame_rate * (1 + FPS_TOLERANCE)
    ):
        raise InvalidOutputOptionError(
            f"--fps ({fps}) is above the video's own rate of "
            f"{info.frame_rate:.3f}, so ffmpeg would duplicate frames "
            "rather than find new ones. Ask for that rate or less."
        )

    duration = info.duration
    if start_time >= duration:
        raise InvalidTimeRangeError(
            f"--start ({start_time}s) is at or past the end of the video "
            f"({duration:.3f}s), so there is nothing to extract"
        )

    _prepare_output_directory(output_dir, image_format, overwrite)

    command = build_ffmpeg_command(
        ffmpeg_path,
        video_path,
        output_dir,
        start_time,
        end_time,
        image_format=image_format,
        jpeg_quality=jpeg_quality,
        fps=fps,
        keyframes=keyframes,
        scene_threshold=scene_threshold,
        scale=scale,
        report_progress=on_progress is not None,
        report_times=timestamps,
    )

    requested = min(end_time, duration) if end_time else duration

    result = run_ffmpeg(
        command,
        on_progress=on_progress,
        seconds_total=max(requested - start_time, 0.0),
    )
    if result.returncode != 0:
        # Whatever landed before the failure is a partial run
        for partial in _sorted_frames(output_dir, image_format):
            partial.unlink()
        raise FFmpegExecutionError(
            f"ffmpeg could not extract frames from '{video_path}'",
            returncode=result.returncode,
            stderr=strip_showinfo(result.stderr).strip(),
        )

    paths = _sorted_frames(output_dir, image_format)

    problems = decode_problems(result.stderr)
    if problems:
        warnings.warn(
            f"ffmpeg reported {len(problems)} decode problem(s) and "
            f"wrote {len(paths)} frame(s) anyway; the source may be "
            f"damaged. First: {problems[0]}",
            IncompleteExtractionWarning,
            stacklevel=2,
        )
    times: list[float] = (
        parse_frame_times(result.stderr, len(paths), start_time)
        if timestamps
        else []
    )
    return [
        Frame(
            path=path,
            index=index,
            timestamp=times[index] if index < len(times) else None,
        )
        for index, path in enumerate(paths)
    ]
