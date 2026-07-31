# frame-extractor

[![tests](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml/badge.svg)](https://github.com/jgonmor16/frame-extractor/actions/workflows/tests.yml)

Extract every frame of a video within a given time range as individual
PNG or JPEG images, using ffmpeg.

🚧 Work in progress.

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

## Usage

```bash
python3 extract_frames.py VIDEO OUTPUT_DIR [--start SECONDS] [--end SECONDS]
                          [--format {png,jpg}] [--jpeg-quality N] [--overwrite]
```

| Argument | Required | Default | Meaning |
|---|---|---|---|
| `VIDEO` | yes | — | Path to the input video file |
| `OUTPUT_DIR` | yes | — | Directory for the frames; created if missing |
| `--start` | no | `0.0` | Start of the range in seconds, **inclusive** |
| `--end` | no | end of video | End of the range in seconds, **exclusive** |
| `--format` | no | `png` | Output image format: `png` or `jpg` |
| `--jpeg-quality` | no | `2` | JPEG quality, `2` (best) to `31` (worst); ignored for PNG |
| `--overwrite` | no | off | Replace frames from an earlier extraction |

### Examples

```bash
# Every frame of the whole video
python3 extract_frames.py input.mp4 frames/

# Five seconds, starting at ten
python3 extract_frames.py input.mp4 frames/ --start 10 --end 15

# From the 30-second mark to the end
python3 extract_frames.py input.mp4 frames/ --start 30

# JPEG instead of PNG, trading fidelity for disk space
python3 extract_frames.py input.mp4 frames/ --format jpg --jpeg-quality 10

# Re-extract a different range into a directory already holding frames
python3 extract_frames.py input.mp4 frames/ --start 5 --end 8 --overwrite
```

The range is half-open — a frame landing exactly on `--end` is excluded, so
`--start 0 --end 1` and `--start 1 --end 2` produce no overlap.

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

Internally these are `FrameExtractorError` subclasses — `VideoFileError`,
`InvalidTimeRangeError`, `InvalidOutputOptionError`, `OutputDirectoryError`,
`FFmpegNotFoundError`, and `FFmpegExecutionError` — so a single
`except FrameExtractorError` catches every expected failure.

## How it works

The script validates the request, asks `ffprobe` how long the video is, then
builds a single `ffmpeg` invocation and runs it via `subprocess`.
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

## Testing

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pytest
pytest
```

The suite generates its own sample clip with ffmpeg's `testsrc` source, so
there's no fixture video in the repository. Tests covering argument validation
run anywhere; those needing real decoding skip automatically if ffmpeg isn't
installed.

CI runs the same suite against Python 3.10 through 3.14 on every pull request.

## Known limitations

- **Not installable.** It's a script to run, not a package to import.

## License

MIT — see [LICENSE](LICENSE).
