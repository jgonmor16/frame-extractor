# frame-extractor

[![tests](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml)

Extract every frame of a video within a given time range as individual PNG or
JPEG images, using ffmpeg. Usable as a command-line tool or as a Python
library.

## Requirements

- Python 3.10 or newer
- `ffmpeg` and `ffprobe` available on `PATH` — both ship together in every
  ffmpeg distribution, so one install covers them

```bash
# Debian / Ubuntu / WSL
sudo apt update && sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

Check the install with `ffmpeg -version`.

> On WSL, a Windows-side ffmpeg installation won't satisfy this — the Linux
> package is what ends up on your `PATH`.

There are no third-party Python dependencies.

## Install

```bash
git clone https://github.com/jgonmor16/frame-extractor.git
cd frame-extractor
pip install .
```

For development, install in editable mode with the test dependencies:

```bash
pip install -e ".[dev]"
```

## Command-line usage

```bash
frame-extractor VIDEO OUTPUT_DIR [--start SECONDS] [--end SECONDS]
                [--format {png,jpg}] [--jpeg-quality N] [--fps N]
                [--overwrite]
```

| Argument | Required | Default | Meaning |
|---|---|---|---|
| `VIDEO` | yes | — | Path to the input video file |
| `OUTPUT_DIR` | yes | — | Directory for the frames; created if missing |
| `--start` | no | `0.0` | Start of the range in seconds, **inclusive** |
| `--end` | no | end of video | End of the range in seconds, **exclusive** |
| `--format` | no | `png` | Output image format: `png` or `jpg` |
| `--jpeg-quality` | no | `2` | JPEG quality, `2` (best) to `31` (worst); ignored for PNG |
| `--fps` | no | every frame | Frames to extract per second of video |
| `--overwrite` | no | off | Replace frames from an earlier extraction |

### Examples

```bash
# Every frame of the whole video
frame-extractor input.mp4 frames/

# Five seconds, starting at ten
frame-extractor input.mp4 frames/ --start 10 --end 15

# From the 30-second mark to the end
frame-extractor input.mp4 frames/ --start 30

# One frame per second instead of all of them
frame-extractor input.mp4 frames/ --fps 1

# One frame every four seconds, for a rough overview
frame-extractor input.mp4 frames/ --fps 0.25

# JPEG instead of PNG, trading fidelity for disk space
frame-extractor input.mp4 frames/ --format jpg --jpeg-quality 10

# Re-extract a different range into a directory already holding frames
frame-extractor input.mp4 frames/ --start 5 --end 8 --overwrite
```

The range is half-open — a frame landing exactly on `--end` is excluded, so
`--start 0 --end 1` and `--start 1 --end 2` produce no overlap.

## Library usage

```python
from pathlib import Path
from frame_extractor import extract_frames, FrameExtractorError

try:
    frames = extract_frames(
        Path("input.mp4"),
        Path("frames"),
        start_time=10.0,
        end_time=15.0,
        image_format="jpg",
        jpeg_quality=10,
        fps=1.0,
    )
except FrameExtractorError as exc:
    print(f"extraction failed: {exc}")
else:
    print(f"wrote {len(frames)} frames, first is {frames[0].name}")
```

`extract_frames` returns a sorted `list[Path]`, so the frames come back in
playback order and can be fed straight into whatever comes next.

### Public API

Everything below is importable from `frame_extractor` directly. Anything not
listed is an implementation detail and may move between versions.

| Name | |
|---|---|
| `extract_frames` | The extraction function |
| `FrameExtractorError` | Base class — catch this to handle any expected failure |
| `VideoFileError` | Input missing, or unreadable as media |
| `InvalidTimeRangeError` | Range negative, inverted, or starting past the end |
| `InvalidOutputOptionError` | Unsupported format, or quality out of range |
| `OutputDirectoryError` | Output directory holds frames and `overwrite` is False |
| `FFmpegNotFoundError` | ffmpeg or ffprobe missing from `PATH` |
| `FFmpegExecutionError` | ffmpeg exited non-zero; carries `returncode` and `stderr` |
| `SUPPORTED_FORMATS` | `("png", "jpg")` |
| `MIN_JPEG_QUALITY` / `MAX_JPEG_QUALITY` | `2` and `31` |

## Output

Frames are written as zero-padded images in the chosen format, numbered from 1
in playback order:

```
frames/
├── frame_000001.png
├── frame_000002.png
├── frame_000003.png
└── ...
```

Numbering always restarts at `000001` for each run, regardless of `--start`.

PNG is the default because it's lossless, which is usually what you want for
frame analysis. Use `--format jpg` when the frame count is large enough that
disk space matters more than fidelity.

### Sampling instead of every frame
 
Extracting every frame is rarely what you want. Thirty seconds of 640x480
footage at 30fps is 900 files and 31 MB; at `--fps 1` it is 30 files and
1.1 MB. For dataset building, thumbnails, or scene overviews, a rate is
usually closer to the real requirement than exhaustive extraction.

Fractional rates work, so `--fps 0.25` gives one frame every four seconds. A
rate above the source's own frame rate is allowed but duplicates frames
rather than inventing new ones, which is rarely useful.

### Re-running into the same directory

By default, extracting into a directory that already holds `frame_*` files of
the same format is an error. Passing `--overwrite` **deletes** those files
before extracting, rather than writing over them — ffmpeg renumbers from 1 on
every run, so a shorter second extraction would otherwise leave the tail of the
first behind. Only files matching this run's own `frame_*.<format>` pattern are
removed; anything else in the directory is left alone.

## Errors

Failures print a single `error:` line to stderr and exit non-zero — no
tracebacks.

| Exit code | Meaning |
|---|---|
| `0` | Frames extracted successfully |
| `1` | The request or the environment was rejected (see below) |
| `2` | Bad command-line usage, reported by `argparse` |

What can go wrong, and what it looks like:

```
error: Video file not found: input.mp4
error: --end (1.0) must be greater than --start (5.0)
error: --start (99.0s) is at or past the end of the video (2.000s) ...
error: Unsupported format 'bmp'; expected one of png, jpg
error: --jpeg-quality must be between 2 (best) and 31 (worst), got 100
error: 'frames' already holds 20 file(s) matching 'frame_*.png'. Pass ...
error: ffprobe could not read 'broken.mp4': ...
error: ffmpeg and ffprobe were not found on PATH. Install ffmpeg with ...
```

When ffmpeg or ffprobe fails, its own diagnosis is printed beneath the summary
line rather than discarded.

All of these are `FrameExtractorError` subclasses, so a single
`except FrameExtractorError` catches every expected failure.

## How it works

`frame_extractor` validates the request, asks `ffprobe` how long the video is,
then builds a single `ffmpeg` invocation and runs it via `subprocess`.
Several details are deliberate:

- **`-ss` is placed before `-i`**, so ffmpeg seeks on the input rather than
  decoding and discarding everything up to the start point. Since ffmpeg 2.1
  this is frame-accurate as well as fast — the test suite verifies it by digest,
  comparing a seeked frame against the same frame from a full extraction.
- **The clip length is passed as a duration (`-t`), not an end TS (`-to`).**
  When `-ss` precedes `-i`, ffmpeg interprets `-to` relative to the seek
  position, which is a common source of clips that end in the wrong place.
- **`-vsync 0`** passes every decoded frame straight through, so nothing is
  duplicated or dropped. The file count matches the source's real frame count
  for the range.
- **`ffprobe` supplies the duration** so a start time past the end of the file
  is rejected outright. Without it, that case ran to completion, wrote nothing,
  and reported success.
- **`--jpeg-quality` is range-checked before ffmpeg runs**, because ffmpeg
  silently clamps values outside 2–31 rather than complaining. Passing `100`
  would otherwise have quietly produced the worst setting.
- **The output directory is prepared in Python, not by ffmpeg's `-n`.** With
  numbered output patterns, `-n` silently skips and exits zero, which would hide
  exactly the situation the overwrite guard exists to report.

Omitting `--end` simply leaves `-t` off the command, so ffmpeg runs to the end
of the file. An `--end` beyond the real duration needs no special handling
either — ffmpeg stops at the end of the input.

### Layout

```
src/frame_extractor/
├── __init__.py       public API re-exports
├── cli.py            argument parsing and exit codes
├── extractor.py      extraction logic; imports only subprocess
├── ffmpeg_utils.py   binary discovery and duration probing
└── exceptions.py     the FrameExtractorError hierarchy
```

The library holds no argparse, no stdout, and no exit codes — those belong to
`cli.py`, which is itself just another consumer of the public API.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The suite generates its own sample clip with ffmpeg's `testsrc` source, so
there's no fixture video in the repository. Tests covering argument validation
and ffmpeg command construction run anywhere; those needing real decoding skip
automatically if ffmpeg isn't installed.

CI runs the same suite against Python 3.10 through 3.14 on every pull request.

## License

MIT — see [LICENSE](LICENSE).
