"""Exception hierarchy for the frame_extractor package.

Every error raised deliberately by this package derives from
:class:`FrameExtractorError`, so a caller that wants to handle any expected
failure can catch that one class.
"""


class FrameExtractorError(Exception):
    """Base class for every error raised by this package."""


class FFmpegNotFoundError(FrameExtractorError):
    """The ffmpeg toolchain is not installed.

    Raised when either the ffmpeg or ffprobe binary is missing from PATH.
    Both ship together, so the remedy is the same either way.
    """


class VideoFileError(FrameExtractorError):
    """The input video is missing, or cannot be read as media."""


class InvalidTimeRangeError(FrameExtractorError):
    """The requested [start, end) range is not usable."""


class InvalidOutputOptionError(FrameExtractorError):
    """The requested image format or JPEG quality is not usable."""


class OutputDirectoryError(FrameExtractorError):
    """The output directory already holds frames from an earlier extraction."""


class FFmpegExecutionError(FrameExtractorError):
    """ffmpeg ran but exited with a non-zero status.

    Attributes:
        returncode: The exit status ffmpeg reported.
        stderr: Captured stderr, which carries ffmpeg's own diagnosis.
    """

    def __init__(self, message: str, *, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
