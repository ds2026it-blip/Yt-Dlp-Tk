# stream_dl & yt-dlp Studio GUI

This project includes two tools:
1. **`yt_dlp_gui.py` (yt-dlp Studio GUI)** — A modern desktop GUI for [yt-dlp](https://github.com/yt-dlp/yt-dlp) supporting 1000+ sites (YouTube, TikTok, Instagram, X/Twitter, Twitch, SoundCloud, etc.) with video/playlist preview, dynamic resolution selector (4K/1440p/1080p/720p/MP3/M4A/FLAC), real-time download queue, and progress tracking.
2. **`stream_dl.py`** — A fast, zero-re-encode console tool for downloading raw HLS (`.m3u8`) streams and saving them as **MP4** or **MKV** files.

---

## Launching yt-dlp Studio GUI

Double-click **`yt_dlp_gui.bat`** or run from terminal:

```powershell
.venv\Scripts\python.exe yt_dlp_gui.py
```

### GUI Features
- **Video & Playlist Metadata Analyzer**: Displays video thumbnail, title, channel/uploader, duration, view count, and dynamically populates available video resolutions.
- **Format & Quality Selector**: Choose `Best Video + Audio`, `4K (2160p)`, `1440p`, `1080p`, `720p`, `480p`, or Audio-only extraction (`MP3 320kbps/192kbps`, `M4A`, `FLAC`, `WAV`).
- **Container & Post-Processing**: Output as `MP4`, `MKV`, or `WEBM`, with optional **Embed Subtitles**, **Embed Thumbnail**, **Embed Metadata**, and **Browser Cookies** support (`chrome`, `edge`, `firefox`, `brave`, `opera`).
- **Download Queue & Playlist Range**: Queue multiple videos or playlists simultaneously with real-time progress bars, download speed, file size, ETA, and per-item cancellation.

---

## CLI Tool: `stream_dl` (HLS / M3U8 Stream Downloader)

A fast, zero-re-encode console tool for downloading HLS (`.m3u8`) streams and saving them as **MP4** or **MKV** files.

> All audio/video tracks are **remuxed** (stream-copied) directly into the target container — no transcoding, no quality loss, full speed.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.9+** | `python --version` |
| **ffmpeg** | Must be on `PATH`. [Download here](https://www.gyan.dev/ffmpeg/builds/) (Windows) |

---

## Setup

```powershell
# 1. Create the virtual environment (already done if you cloned this repo)
python -m venv .venv

# 2. Activate
.venv\Scripts\activate        # Windows PowerShell / CMD
# source .venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Usage

```
python stream_dl.py <URL> [OPTIONS]
```

Or use the convenience launcher (Windows, activates venv automatically):

```
stream_dl.bat <URL> [OPTIONS]
```

### Options

| Flag | Description |
|------|-------------|
| `-o FILE` / `--output FILE` | Output path. Extension sets the container (`.mp4` or `.mkv`) |
| `-f {mp4,mkv}` / `--format` | Format when `--output` is not given (default: `mp4`) |
| `--headers KEY:VALUE` | Extra HTTP headers (repeatable) |
| `--user-agent UA` | Custom User-Agent string |
| `--timeout N` | Network timeout in seconds (default: `30`) |
| `-v` / `--verbose` | Show full ffmpeg output |
| `-q` / `--quiet` | Suppress all output except errors |

---

## Examples

```powershell
# Basic — save as MP4 (name derived from URL)
python stream_dl.py "https://example.com/stream/index.m3u8"

# Specify output file and format
python stream_dl.py "https://example.com/stream/index.m3u8" -o my_video.mkv

# With authentication headers (e.g. behind a paywall / CDN)
python stream_dl.py "https://cdn.example.com/live/index.m3u8" `
    --headers "Referer:https://example.com" `
    --headers "Origin:https://example.com" `
    --headers "Cookie:session=abc123"

# Quiet mode (useful for scripts / automation)
python stream_dl.py "https://example.com/stream.m3u8" -o output.mp4 -q

# Verbose — see every ffmpeg line
python stream_dl.py "https://example.com/stream.m3u8" -v
```

---

## How it works

1. `ffmpeg` connects directly to the `.m3u8` playlist URL over HTTP/HTTPS.
2. It fetches and downloads all `.ts` / `.fmp4` segments automatically.
3. Segments are **stream-copied** (`-c copy`) into the target container — no decoding or re-encoding.
4. For MP4 output, `-movflags faststart` is applied so the file is playable while still downloading (progressive playback), and `-bsf:a aac_adtstoasc` strips the ADTS header required by the MP4 muxer.
5. MKV output requires no special flags and accepts virtually any codec.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ffmpeg not found` | Install ffmpeg and add it to `PATH` |
| `403 Forbidden` | Add `--headers "Referer:..."` matching the site's origin |
| `Stream not found / 404` | The stream URL may have expired — get a fresh link |
| Corrupted output | Try `-f mkv` instead — MKV is more tolerant of stream errors |
| Audio-video sync issues | Use `-f mkv`; sync issues in MP4 can be caused by ADTS strip edge cases |

---

## Project structure

```
stream_downloader/
├── stream_dl.py       ← main tool
├── stream_dl.bat      ← Windows convenience launcher
├── requirements.txt
├── README.md
└── .venv/             ← Python virtual environment
```
