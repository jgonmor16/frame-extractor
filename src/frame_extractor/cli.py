"""Command-line interface for frame_extractor.

Kept apart from the extraction logic so the library has no argparse or
stdout in it, and this module can be pointed at by the console script.
"""

import argparse
import sys
from pathlib import Path

from frame_extractor import (
    MAX_JPEG_QUALITY,
    MIN_JPEG_QUALITY,
    SUPPORTED_FORMATS,
    FFmpegExecutionError,
    FrameExtractorError,
    Progress,
    extract_frames,
)


def _report(progress: Progress) -> None:
    """Draw a one-line progress indicator on stderr.

    stderr rather than stdout, so the summary line stays pipeable, and a
    carriage return rather than a newline so the line is rewritten in
    place instead of scrolling.
    """
    fraction = progress.fraction
    share = f"{fraction:>4.0%}" if fraction is not None else "   -"
    print(
        f"\r  {share}  {progress.frames_written} frame(s)",
        end="",
        file=sys.stderr,
        flush=True,
    )


def _clear_report(drawn: bool) -> None:
    """Wipe the progress line so it does not linger above the summary.

    A no-op when nothing was drawn, so the escape sequence never reaches
    a stderr that is being piped somewhere.
    """
    if drawn:
        print("\r\033[K", end="", file=sys.stderr, flush=True)


def main() -> int:
    """Parse command-line arguments and run the extraction

    Returns:
        Process exit code: 0 on success, 1 on any expected failure.
    """
    parser = argparse.ArgumentParser(
        description="Extract frames from a video within a time range."
    )
    parser.add_argument(
        "video",
        type=Path,
        help="Path to the input video file.",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory to write extracted frames to.",
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
    parser.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default="png",
        help="Output image format (default: png).",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=MIN_JPEG_QUALITY,
        help=(
            f"JPEG quality from {MIN_JPEG_QUALITY} (best) to {MAX_JPEG_QUALITY}"
            f" (worst); ignored for PNG (default: {MIN_JPEG_QUALITY})."
        ),
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        metavar="N",
        help=(
            "Frames to extract per second; fractional values are allowed, "
            "so 0.5 gives one frame every two seconds (default: every frame)."
        ),
    )
    parser.add_argument(
        "--scale",
        default=None,
        metavar="W:H",
        help=(
            "Resize output to WIDTH:HEIGHT, for example 640:480. Use "
            "'auto' for either side to derive it from the other and the "
            "source aspect ratio, as in 640:auto "
            "(default: the source size)."
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help=(
            "Do not draw the progress indicator. It is drawn only when "
            "stderr is a terminal, so piped output is unaffected either way"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace frames from an earlier extraction in OUTPUT_DIR.",
    )
    args = parser.parse_args()

    show_progress = not args.no_progress and sys.stderr.isatty()

    try:
        frames = extract_frames(
            args.video,
            args.output_dir,
            start_time=args.start,
            end_time=args.end,
            image_format=args.format,
            jpeg_quality=args.jpeg_quality,
            overwrite=args.overwrite,
            fps=args.fps,
            scale=args.scale,
            on_progress=_report if show_progress else None,
        )

    except FFmpegExecutionError as exc:
        _clear_report(show_progress)
        print(f"error: {exc}", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1

    except FrameExtractorError as exc:
        _clear_report(show_progress)
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _clear_report(show_progress)
    print(f"Extracted {len(frames)} frame(s) to '{args.output_dir}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
