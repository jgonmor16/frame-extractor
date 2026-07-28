# frame-extractor

Extract every frame of a video within a given time range as individual
PNG images, using ffmpeg.

🚧 Work in progress.

## Requirements

- Python 3.10 or newer
- `ffmpeg` available on `PATH`

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
```

| Argument | Required | Default | Meaning |
|---|---|---|---|
| `VIDEO` | yes | — | Path to the input video file |
| `OUTPUT_DIR` | yes | — | Directory for the frames; created if missing |
| `--start` | no | `0.0` | Start of the range in seconds, **inclusive** |
| `--end` | no | end of video | End of the range in seconds, **exclusive** |

### Examples

```bash
# Every frame of the whole video
python3 extract_frames.py input.mp4 frames/

# Five seconds, starting at ten
python3 extract_frames.py input.mp4 frames/ --start 10 --end 15

# From the 30-second mark to the end
python3 extract_frames.py input.mp4 frames/ --start 30

# The first two and a half seconds
python3 extract_frames.py input.mp4 frames/ --end 2.5
```

The range is half-open — a frame landing exactly on `--end` is excluded, so
`--start 0 --end 1` and `--start 1 --end 2` produce no overlap.

## Output

Frames are written as zero-padded PNGs, numbered from 1 in playback order:

```
frames/
├── frame_000001.png
├── frame_000002.png
├── frame_000003.png
└── ...
```

Numbering always restarts at `000001` for each run, regardless of `--start`.

## How it works

The script builds a single ffmpeg invocation and runs it via `subprocess`.
Three details are deliberate:

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

Omitting `--end` simply leaves `-t` off the command, so ffmpeg runs to the end
of the file without the script needing to know the duration.

## Testing

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pytest
pytest
```

The suite generates its own sample clip with ffmpeg's `testsrc` source, so
there's no fixture video in the repository. If ffmpeg isn't installed, every
test skips rather than fails.

## Known limitations

This is an early version. Rough edges, in the order they're being addressed:

- **Failures surface as Python tracebacks.** A missing input file or a missing
ffmpeg binary raises `CalledProcessError` or `FileNotFoundError` rather than a
readable message.
- **An invalid range isn't caught.** `--start 5 --end 1` produces a negative
duration and fails inside ffmpeg.
- **A start time past the end of the video reports success**, having extracted
zero frames — the script doesn't know how long the video is.
- **PNG only.** No JPEG output or quality control.
- **Reruns overwrite silently.** Extracting a shorter range into a directory
that already holds frames leaves the surplus files behind.
- **Not installable.** It's a script to run, not a package to import.

## License

MIT — see [LICENSE](LICENSE).
