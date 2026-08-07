"""Extract frames from a video within a time range, using ffmpeg.

The whole public interface is re-exported here, so callers import from the
package root rather than reaching into its modules::

    from pathlib import Path
    from frame_extractor import extract_frames, FrameExtractorError

    try:
        frames = extract_frames(
            Path("input.mp4"), Path("frames"), start_time=10.0, end_time=15.0
        )
    except FrameExtractorError as exc:
        print(f"extraction failed: {exc}")

Anything not listed in ``__all__`` is an implementation detail and may move
or change without a major version bump.
"""

from frame_extractor.exceptions import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    FrameExtractorError,
    InvalidOutputOptionError,
    InvalidTimeRangeError,
    OutputDirectoryError,
    VideoFileError,
)
from frame_extractor.extractor import (
    MAX_JPEG_QUALITY,
    MIN_JPEG_QUALITY,
    SUPPORTED_FORMATS,
    extract_frames,
)

__version__ = "0.5.0"

__all__ = [
    # The one thing most callers need.
    "extract_frames",
    # Catch the base class to handle any expected failure; the rest are for
    # telling them apart.
    "FrameExtractorError",
    "FFmpegExecutionError",
    "FFmpegNotFoundError",
    "InvalidOutputOptionError",
    "InvalidTimeRangeError",
    "OutputDirectoryError",
    "VideoFileError",
    # Useful for validating input before calling, or for building a UI.
    "SUPPORTED_FORMATS",
    "MIN_JPEG_QUALITY",
    "MAX_JPEG_QUALITY",
]
