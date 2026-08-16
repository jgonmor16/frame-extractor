# frame-extractor

[![tests](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/frame-extractor-ffmpeg)](https://pypi.org/project/frame-extractor-ffmpeg/)

Extract every frame of a video within a given time range as individual PNG or
JPEG images, using ffmpeg. Usable as a command-line tool or as a Python
library.

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

None of these are misconfigurations — it's ffmpeg working as designed, being a
low-level tool that does what it is told. Catching them needs a layer above:
probe the duration first, range-check the quality, clear the directory before
writing. That layer is what this is.

The third row is the one that bites hardest. Frames from two different runs
end up in one directory with nothing marking which is which, and the count you
get back is wrong rather than merely untidy.

**The other half is the Python API.** If you need frames from inside a program,
the alternative is writing the subprocess wrapper yourself — and then
rediscovering the five rows above one at a time.

### Where it doesn't help

- **Frames as arrays for ML or CV work** — use PyAV or TorchCodec. Writing
PNGs only to read them back pays for an encode and a decode you don't need.
- **Anything beyond extraction** — trimming, concatenating, re-encoding — is
ffmpeg's job, and this deliberately doesn't grow into a general wrapper.

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
pip install frame-extractor-ffmpeg
```

The distribution is named `frame-extractor-ffmpeg` because `frame-extractor`
was already taken on PyPI by an unrelated package. It installs the
`frame-extractor` command and the `frame_extractor` module, so only the
`pip install` line carries the longer name.

From a clone instead:

```bash
git clone https://github.com/jgonmor16/frame-extractor.git
cd frame-extractor
make install
```

`make install` is an editable install with the development dependencies.
Run `make` on its own to see the other targets.

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
| `--manifest` | no | — | Write path, index, and timestamp as CSV |
| `--no-progress` | no | off | Suppress the progress indicator |
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
        image_format="jpg",
        jpeg_quality=10,
        fps=1.0,
        scale="640:auto",
        overwrite=False,
    )
except FrameExtractorError as exc:
    print(f"extraction failed: {exc}")
else:
    print(f"wrote {len(frames)} frames, first is {frames[0].name}")
```

`extract_frames` returns a sorted `list[Frame]`, so the frames come back in
playback order and can be fed straight into whatever comes next. Each `Frame`
carries its `path`, its `index`, and its `timestamp`.

> **Upgrading from 1.x:** the result used to be `list[Path]`. Add `.path`
> where you were using an item as a path; filenames and every argument are
> unchanged.

Timestamps are `None` unless you ask for them, since recovering them costs an
extra pass over the range:

```python
for frame in extract_frames(video, out, fps=1.0, timestamps=True):
    print(f"{frame.index}  {frame.timestamp}s  {frame.path.name}")
```

With `--keyframes` or `--scenes` the spacing is uneven, so this is the only
way to know when a frame happened.

Pass `on_progress` to follow a long extraction. The library never prints, so
what to do with each update is yours to decide:

```python
def show(progress):
    print(f"{progress.fraction:.0%} · {progress.frames_written} frames")


extract_frames(video, out, fps=1.0, on_progress=show)
```

Each update carries `seconds_done`, `seconds_total`, `frames_written`, and a
`fraction` property that is `None` when the total isn't known.

### Public API

Everything below is importable from `frame_extractor` directly. Anything not
listed is an implementation detail and may move between versions.

`extract_frames` takes the source, the destination, and the time range
positionally; every option after those is keyword-only. That keeps the common
call short while making longer ones self-describing, and means new options can
be added without their position becoming part of the API.

```python
extract_frames(video, out, 10.0, 15.0, fps=1.0, scale="640:auto")
```

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
| `Frame` | One extracted frame: `path`, `index`, `timestamp` |
| `Progress` | What an `on_progress` callback receives |
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
rate above the source's own frame rate is rejected: ffmpeg would duplicate
frames rather than find new ones. Asking for 30 against 29.97fps footage is
fine, since a small tolerance treats that as a rounding difference.

### Choosing frames another way

`--fps` samples at a rate you pick. Two other modes let the video decide
instead, and only one selection mode can be used at a time.

`--keyframes` extracts the frames the encoder stored as complete pictures.
Everything between them is stored as differences, so skipping them means never
decoding them at all — on a two-minute file that is 0.3 seconds against 27 for
every frame. The trade is that their spacing is the encoder's choice, not
yours: it might be one every two seconds or one every ten, and it varies
within a file.

`--scenes THRESHOLD` keeps frames where the picture differs from the one
before it by more than the threshold, from 0 to 1. Around 0.4 catches clear
cuts.

Both return fewer frames than you might expect, and that is not a failure.
Scene detection especially is a heuristic on pixel differences rather than a
cut list: it misses cross-fades, gradual transitions, and cuts between shots
that happen to be numerically similar. In a test clip of four solid colours it
found two of the three changes. Check the threshold against your own footage
before trusting it, and use `--fps` when you need a predictable number of
frames.

### Resizing

`--scale WIDTH:HEIGHT` resizes during extraction rather than in a second pass
over the files, which for a model expecting a fixed input size saves decoding
and re-encoding every frame.

Either side may be `auto` to derive it from the other and the source aspect
ratio, so `--scale 640:auto` fixes the width and lets the height follow.
Deriving both is rejected: ffmpeg accepts it and silently leaves the size
unchanged.

ffmpeg's own `-1` and `-2` spellings work too, but note that a value starting
with a dash has to be written `--scale=-1:240`, since argparse otherwise reads
it as a flag. `auto` avoids that.

### Progress

A long extraction draws a single line on stderr, rewritten in place, and
cleared before the summary:

```
52%  63 frame(s)
```

It appears only when stderr is a terminal, so piping or redirecting output
suppresses it without a flag. `--no-progress` turns it off in a terminal too.

The percentage is of the requested range, not the whole file, and comes from
the duration already probed — nothing extra is decoded to produce it.

### Timestamps

`--timestamps` records where each frame sits in the source, and `--manifest
frames.csv` writes the mapping out:

```
path,index,timestamp
frames/frame_000001.png,0,5.0
frames/frame_000002.png,1,6.0
```

It is off by default because it forces ffmpeg to report every frame it
handles, which costs a pass over the range. `--manifest` implies it.

The times come from the extraction itself rather than a separate probe, so
they describe the frames that were actually written, including under
`--keyframes` and `--scenes` where nothing else could tell you.

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
error: --fps (20.0) is above the video's own rate of 10.000 ...
error: --scale must be WIDTH:HEIGHT, got 'abc'. Use 'auto' ...
error: --scale 'auto:auto' derives both dimensions from each other ...
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
  for the range, when no rate is requested.
- **`ffprobe` supplies the duration** so a start time past the end of the file
  is rejected outright. Without it, that case ran to completion, wrote nothing,
  and reported success.
- **`--jpeg-quality` is range-checked before ffmpeg runs**, because ffmpeg
  silently clamps values outside 2–31 rather than complaining. Passing `100`
  would otherwise have quietly produced the worst setting.
- **The output directory is prepared in Python, not by ffmpeg's `-n`.** With
  numbered output patterns, `-n` silently skips and exits zero, which would hide
  exactly the situation the overwrite guard exists to report.
- **`fps` precedes `scale` in the filter chain**, so resampling happens first
  and the scaler handles the sampled frames rather than all of them.
- **`--scale` is matched against a strict `WIDTH:HEIGHT` pattern**, because the
  value is interpolated into ffmpeg's `-vf` argument and an unchecked comma
  would append filters the caller never asked for.
- **`--keyframes` is passed as an input option (`-skip_frame`)**, so the
  decoder skips the other frames rather than producing them for a filter to
  discard. Moving it after `-i` would still work and would lose the entire
  speed advantage.
- **Scene selection runs first in the filter chain**, so frames that are about
  to be thrown away are never resized

Omitting `--end` simply leaves `-t` off the command, so ffmpeg runs to the end
of the file. An `--end` beyond the real duration needs no special handling
either — ffmpeg stops at the end of the input.

### Layout

```
src/frame_extractor/
├── __init__.py       public API re-exports
├── cli.py            argument parsing and exit codes
├── extractor.py      extraction logic; no argparse, no stdout
├── ffmpeg_utils.py   binary discovery and video probing
└── exceptions.py     the FrameExtractorError hierarchy
```

The library holds no argparse, no stdout, and no exit codes — those belong to
`cli.py`, which is itself just another consumer of the public API.

## Testing

```bash
make install
make test
```

`make check` runs everything CI does: lint, formatting, types, and tests.

The suite generates its own sample clip with ffmpeg's `testsrc` source, so
there's no fixture video in the repository. Tests covering argument validation
and ffmpeg command construction run anywhere; those needing real decoding skip
automatically if ffmpeg isn't installed.

CI runs the same suite against Python 3.10 through 3.14 on every pull request.

## Development

The Makefile wraps the commands CI runs, so the two cannot drift apart:

| Target | |
|---|---|
| `make install` | Install the package with its development dependencies |
| `make test` | Run the test suite |
| `make lint` | Report lint and formatting problems, changing nothing |
| `make format` | Apply ruff's fixes and formatting |
| `make typecheck` | Run mypy |
| `make check` | Everything CI runs: lint, typecheck, test |
| `make build` | Build the sdist and wheel into `dist/` |
| `make clean` | Remove build artefacts and tool caches |

Run `make check` before pushing. Releases are published to PyPI automatically
when a GitHub release is created, via trusted publishing.

## License

MIT — see [LICENSE](LICENSE).
