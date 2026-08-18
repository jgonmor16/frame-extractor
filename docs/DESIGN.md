# Design notes

Why the ffmpeg invocation looks the way it does. None of this is needed to
use the tool; it is here so that a change which looks like a tidy-up does
not silently alter which frames come out.

## The ffmpeg command

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
- **`--scale` is matched against a strict `WIDTH:HEIGHT` pattern**, because the
  value is interpolated into ffmpeg's `-vf` argument and an unchecked comma
  would append filters the caller never asked for.
- **The filter chain runs scene selection, then `fps`, then `scale`**, so
  frames about to be discarded are never resampled or resized.
- **`--keyframes` is passed as an input option (`-skip_frame`)**, so the
  decoder skips the other frames rather than producing them for a filter to
  discard. Moving it after `-i` would still work and would lose the entire
  speed advantage.

Omitting `--end` simply leaves `-t` off the command, so ffmpeg runs to the end
of the file. An `--end` beyond the real duration needs no special handling
either — ffmpeg stops at the end of the input.

## Layout

```
src/frame_extractor/
├── __init__.py       public API re-exports
├── cli.py            argument parsing, progress, exit codes
├── extractor.py      extraction logic
├── ffmpeg_utils.py   binary discovery, probing, the subprocess call
└── exceptions.py     the FrameExtractorError hierarchy
```

The library holds no argparse, no stdout, and no exit codes — those belong
to `cli.py`, which is itself just another consumer of the public API.

## Things ffmpeg does that this guards against

Each of these exits 0 and produces plausible output:

- A start time past the end of the file writes nothing.
- `-q:v` outside 2–31 is clamped rather than rejected.
- Re-extracting a shorter range leaves the previous run's surplus frames.
- `scale=-1:-1` resizes nothing.
- An fps above the source rate duplicates frames.

They are caught by probing the duration and frame rate before extracting,
range-checking the options, and clearing the output directory rather than
writing over it.
