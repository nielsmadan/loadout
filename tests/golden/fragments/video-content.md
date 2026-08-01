## Video Content

Use the shared video helpers in `~/rc/bin` (installed on `PATH`) instead of assembling one-off `yt-dlp` or `ffmpeg` commands:

- `yt-transcript <url> [--lang <code>] [--timestamps] [--bucket <seconds>]` gets metadata and cleaned captions from any URL supported by `yt-dlp`. Start here when captions are likely.
- `transcribe <source> [--model <name>] [--lang <code>] [--timestamps] [--bucket <seconds>]` runs local Whisper on a URL or local audio/video file. Use it when `yt-transcript` reports `NO_CAPTIONS`, or for podcasts, recorded streams, meetings, screen recordings, and voice memos.
- `video-frames --sheet <source> <sheet.jpg>` makes a one-frame-per-minute contact sheet. Follow with `video-frames --scan <source> <start_s> <end_s> [fps]` to locate a transition or motion onset, then `video-frames <source> <timestamp> <frame.jpg>` to extract a frame.

Transcript and downloaded-media caches live under `TMPDIR` and are reused. Add `--clean <source>` to `transcribe` or `video-frames` when the cached files are no longer needed. Run any command with `--help` for its complete usage.
