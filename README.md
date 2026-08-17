# frame-extractor

[![tests](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/frame-extractor-ffmpeg)](https://pypi.org/project/frame-extractor-ffmpeg/)

Extract frames from a video as PNG or JPEG images, from the command line or from
Python. Built on ffmpeg.

## Why not just use ffmpeg?

For a one-off extraction by someone who knows ffmpeg, this adds very little.
The whole job is one command:

```bash
ffmpeg -ss 10 -i in.mp4 -t 5 -vsync 0 frames/f_%06d.png
```

**What it does add is a guard against ffmpeg's silent wrong answers.** Each of
these exits **0** and produces output that looks reasonable:

| What you ask for | ffmpeg | frame-extractor |
|---|---|---|
| `-ss 99` on a 2-second video | writes nothing, exits 0 | rejected, exit 1 |
| `-q:v 100` (valid range is 2–31) | clamps to 31 silently | rejected, exit 1 |
| re-extracting a shorter range | leaves the previous run's surplus frames alongside the new ones | refused unless `--overwrite`, which clears them first |
| `scale=-1:-1` | resizes nothing | rejected, exit 1 |
| `fps=20` on a 10fps source | 40 files, every second one a duplicate | rejected, exit 1 |

That third row is the one that bites: frames from two runs end up in one
directory with nothing marking which is which, and the count you get back is
wrong rather than merely untidy.

**The other half is the Python API.** If you need frames from inside a program,
the alternative is writing the subprocess wrapper yourself — and then
rediscovering the five rows above one at a time.

**Where it doesn't help:** frames as arrays for ML or CV work — use PyAV or
TorchCodec rather than writing PNGs only to read them back. Anything beyond
extraction — trimming, concatenating, re-encoding — is ffmpeg's job.

## Install

```bash
pip install frame-extractor-ffmpeg
```

Requires **Python 3.10+** and **ffmpeg and ffprobe on your `PATH`**. Both
ship together, so one install covers them:

```bash
sudo apt update && sudo apt install ffmpeg  # Debian / Ubuntu / WSL
brew install ffmpeg                         # macOS
```

> On WSL, a Windows-side ffmpeg installation won't satisfy this — the Linux
> package is what ends up on your `PATH`.

The distribution is named `frame-extractor-ffmpeg` because `frame-extractor`
was already taken on PyPI by an unrelated package. It installs the
`frame-extractor` command and the `frame_extractor` module. There are no
third-party Python dependencies.

## Command-line usage

```bash
frame-extractor VIDEO OUTPUT_DIR [--start SECONDS] [--end SECONDS]
                [--format {png,jpg}] [--jpeg-quality N]
                [--fps N | --keyframes | --scenes THRESHOLD]
                [--scale W:H] [--timestamps] [--manifest CSV]
                [--no-progress] [--overwrite]
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
| `--keyframes` | no | off | Extract only the video's key frames |
| `--scenes` | no | off | Extract only frames where the picture changes |
| `--scale` | no | source size | Output size as `WIDTH:HEIGHT` |
| `--no-progress` | no | off | Suppress the progress indicator |
| `--timestamps` | no | off | Record where each frame sits in the source |
| `--manifest` | no | — | Write path, index, and timestamp as CSV |
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

# Only the key frames: fastest way to see what is in a long file
frame-extractor input.mp4 frames/ --keyframes

# Only where the picture changes, to find the cuts
frame-extractor input.mp4 frames/ --scenes 0.4

# Resize to a fixed size, for a model expecting one
frame-extractor input.mp4 frames/ --scale 224:224

# Fixed width, height following the aspect ratio
frame-extractor input.mp4 frames/ --scale 640:auto

# JPEG instead of PNG, trading fidelity for disk space
frame-extractor input.mp4 frames/ --format jpg --jpeg-quality 10

# Record when each frame happened, as a CSV alongside the images
frame-extractor input.mp4 frames/ --fps 1 --manifest frames.csv

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
        fps=1.0,
        timestamps=True,
    )
except FrameExtractorError as exc:
    print(f"extraction failed: {exc}")
else:
    for frame in frames:
        print(f"{frame.index}  {frame.timestamp}s  {frame.path.name}")
```

> **Upgrading from 1.x:** `extract_frames` returns `list[Frame]` rather than
> `list[Path]`. Add `.path` where you were using an item as a path;
> filenames and every argument are unchanged.

The source, destination, and time range are positional; every option after
those is keyword-only, so new options can be added without their position
becoming part of the API.

Pass `on_progress` to follow a long extraction — the library never prints,
so what to do with each update is yours:

```python
extract_frames(video, out, on_progress=lambda p: print(f"{p.fraction:.0%}"))
```

### Public API

Importable from `frame_extractor` directly. Anything not listed is an
implementation detail and may move between versions.

| Name | |
|---|---|
| `extract_frames` | The extraction function |
| `Frame` | One extracted frame: `path`, `index`, `timestamp` |
| `Progress` | What an `on_progress` callback receives |
| `FrameExtractorError` | Base class — catch this to handle any expected failure |
| `VideoFileError` | Input missing, or unreadable as media |
| `InvalidTimeRangeError` | Range negative, inverted, or starting past the end |
| `InvalidOutputOptionError` | Unsupported format, or quality out of range |
| `OutputDirectoryError` | Output directory holds frames and `overwrite` is False |
| `FFmpegNotFoundError` | ffmpeg or ffprobe missing from `PATH` |
| `FFmpegExecutionError` | ffmpeg exited non-zero; carries `returncode` and `stderr` |
| `SUPPORTED_FORMATS` | `("png", "jpg")` |
| `MIN_JPEG_QUALITY` / `MAX_JPEG_QUALITY` | `2` and `31` |

## Choosing frames

Frames are written as `frame_000001.png`, numbered from 1 in playback order,
restarting each run. PNG is the default because it's lossless; use
`--format jpg` when disk space matters more than fidelity.

**How many.** `--fps N` is the predictable option: you know in advance what
you get. `--keyframes` is far faster — the encoder stores those as complete
pictures and everything between them as differences, so the rest are never
decoded — but their spacing is the encoder's choice, not yours. `--scenes`
keeps frames that differ from the one before by more than a threshold.

Only one of the three can be used at a time. Both `--keyframes` and
`--scenes` can return far fewer frames than expected, and that is correct
rather than broken: scene detection is a heuristic on pixel differences, not
a cut list, and in a test clip of four solid colours it found two of the
three changes. The command line says so when a run writes nothing.

**Where they sit in the source.** `--timestamps` records each frame's
position, and `--manifest frames.csv` writes the mapping out:

```
path,index,timestamp
frames/frame_000001.png,0,5.0
frames/frame_000002.png,1,6.0
```

It is off by default because it costs a pass over the range. With
`--keyframes` or `--scenes` the spacing is uneven, so this is the only way
to know when a frame happened.

**Re-running.** Extracting into a directory that already holds `frame_*`
files of the same format is an error. `--overwrite` **deletes** them first
rather than writing over them, because ffmpeg renumbers from 1 each run and
a shorter second extraction would otherwise leave the first's tail behind.
Only this run's own pattern is touched.

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
error: --fps (20.0) is above the video's own rate of 10.000 ...
error: --scale must be WIDTH:HEIGHT, got 'abc'. Use 'auto' ...
error: --scale 'auto:auto' derives both dimensions from each other ...
```

When ffmpeg or ffprobe fails, its own diagnosis follows the summary line.
All of these are `FrameExtractorError` subclasses.

## Development

```bash
git clone https://github.com/jgonmor16/frame-extractor.git
cd frame-extractor
make install
make check       # lint, formatting, types, tests — everything CI runs
```

Run `make` on its own to list the other targets. The test suite generates
its own sample clip with ffmpeg's `testsrc`, so there is no fixture video in
the repository; tests needing real decoding skip if ffmpeg is missing. CI
runs against Python 3.10 through 3.14 on every pull request.

[`docs/DESIGN.md`](docs/DESIGN.md) explains why the ffmpeg invocation looks
the way it does — worth reading before changing any of the flags.

## License

MIT — see [LICENSE](LICENSE).
