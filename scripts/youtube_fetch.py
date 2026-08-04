#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "youtube-transcript-api>=0.6.0",
#     "yt-dlp",
# ]
# ///
"""
youtube_fetch.py — Fetch YouTube metadata and transcript to markdown files.

Works standalone or inside an LLM knowledge base.
Output goes to {root}/raw/youtube/, where root is resolved by (highest wins):
--out-dir flag, then YOUTUBE_WIKI_OUT env var, then the nearest project marker
walked up from cwd (.git, opencode.json, or an existing raw/youtube/), then cwd.
Pass --out-dir <project-root> to target the consuming project explicitly.

Run with uv (dependencies are declared inline above and installed
automatically into an ephemeral environment):

Usage:
    Single video:      uv run youtube_fetch.py <youtube_url> [--out-dir <project-root>]
    Playlist/Channel:  uv run youtube_fetch.py <playlist_or_channel_url> [--out-dir <project-root>]
    Limit videos:      uv run youtube_fetch.py <playlist_url> --limit 10
    Skip fetched:      uv run youtube_fetch.py <url> --skip-existing
    Date range:        uv run youtube_fetch.py <playlist_url> --after 2026-01-01 --before 2026-06-30
    Transcribe audio:  uv run youtube_fetch.py <url> --transcribe [--transcribe-lang english]

Flags (playlist/channel fetches):
    --limit N              only the first N videos
    --skip-existing        skip videos whose video_id is already in raw/youtube/
    --after YYYY-MM-DD     only videos uploaded on/after this date (inclusive)
    --before YYYY-MM-DD    only videos uploaded on/before this date (inclusive)

Flags (all fetches):
    --out-dir DIR          project root to write into (files go to DIR/raw/youtube/);
                           overrides YOUTUBE_WIKI_OUT env var
    --transcribe           if a video has no captions, download the audio track
                           and transcribe it locally with mlxscribe
    --transcribe-lang LANG transcribe and translate to LANG (e.g. english)

If YouTube rate-limits the caption endpoint, the audio track is downloaded and
transcribed automatically (no --transcribe flag needed).

Security: only YouTube URLs (youtube.com / youtu.be) are accepted.
Only metadata, captions, and (with --transcribe) the audio track are fetched;
nothing is written outside {out-dir}/raw/youtube/.
"""

import os
import shlex
import shutil
import sys
import json
import re
import subprocess
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

OUT_DIR = Path.cwd() / "raw" / "youtube"

VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

PROJECT_MARKERS = (".git", "opencode.json")


def resolve_out_dir(out_dir_flag: str | None = None) -> Path:
    """Return the directory that raw/youtube/ lives under.

    Resolution order (first match wins):
      1. --out-dir flag
      2. YOUTUBE_WIKI_OUT env var
      3. nearest project marker walked up from cwd
         (.git, opencode.json, or an existing raw/youtube/)
      4. cwd
    """
    root = None
    if out_dir_flag:
        root = Path(out_dir_flag)
    elif os.environ.get("YOUTUBE_WIKI_OUT"):
        root = Path(os.environ["YOUTUBE_WIKI_OUT"])
    else:
        cwd = Path.cwd()
        for cand in (cwd, *cwd.parents):
            if any((cand / marker).exists() for marker in PROJECT_MARKERS) \
                    or (cand / "raw" / "youtube").is_dir():
                root = cand
                break
        if root is None:
            root = cwd
    return (root / "raw" / "youtube").resolve()


def show(path: Path) -> str:
    """Render a path relative to cwd when possible (absolute otherwise)."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

def assert_youtube_url(url: str) -> None:
    """Reject anything that is not a YouTube URL (http/https, youtube.com/youtu.be)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) YouTube URLs are allowed: {url}")
    host = (parsed.hostname or "").lower()
    if host != "youtu.be" and host != "youtube.com" and not host.endswith(".youtube.com"):
        raise ValueError(f"Only YouTube URLs are allowed: {url}")


def assert_valid_video_id(video_id: str) -> None:
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError(f"Invalid YouTube video ID: {video_id!r}")


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

def is_playlist_url(url: str) -> bool:
    has_list = bool(re.search(r"[?&]list=([A-Za-z0-9_-]+)", url))
    is_playlist_page = "youtube.com/playlist" in url
    is_watch_with_list = "watch?v=" in url and has_list
    return is_playlist_page or (has_list and not is_watch_with_list)


def is_channel_url(url: str) -> bool:
    """Match channel handles/IDs that yt-dlp can enumerate as a playlist:
    youtube.com/@handle[/videos], /channel/UC..., /user/..., /c/name."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        return False
    path = parsed.path
    return (path.startswith("/@") or path.startswith("/channel/")
            or path.startswith("/user/") or path.startswith("/c/"))


def extract_playlist_id(url: str) -> str:
    match = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract playlist ID from: {url}")


def extract_channel_id(url: str) -> str:
    """Return a stable identifier for a channel URL: handle, channel/user/c ID, or slug."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/")
    for prefix in ("/@", "/channel/", "/user/", "/c/"):
        if path.startswith(prefix):
            seg = path[len(prefix):].split("/")[0]
            if seg:
                return seg
    raise ValueError(f"Could not extract channel identifier from: {url}")


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=)([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"embed/([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from: {url}")


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def fetch_metadata(url: str) -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp",
             "--dump-json", "--no-download", "--no-playlist",
             "--no-config", "--no-cookies-from-browser", url],
            capture_output=True, text=True, check=True, timeout=30
        )
        first_line = result.stdout.strip().splitlines()[0]
        data = json.loads(first_line)
        return {
            "title": data.get("title", "Unknown Title"),
            "channel": data.get("uploader", data.get("channel", "Unknown Channel")),
            "published": data.get("upload_date", ""),
            "duration": int(data.get("duration", 0)),
            "view_count": data.get("view_count", 0),
            "description": (data.get("description", "") or "")[:600],
        }
    except FileNotFoundError:
        print("WARNING: yt-dlp not found. Run: uv run youtube_fetch.py <url>",
              file=sys.stderr)
        return _empty_meta()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"WARNING: yt-dlp failed: {e}", file=sys.stderr)
        return _empty_meta()


def fetch_playlist_videos(playlist_url: str, limit: int = None) -> list:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist", "--dump-json", "--no-warnings",
        "--no-config", "--no-cookies-from-browser",
        playlist_url,
    ]
    if limit:
        cmd += ["--playlist-end", str(limit)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    except FileNotFoundError:
        print("ERROR: yt-dlp not found. Run: uv run youtube_fetch.py <url>", file=sys.stderr)
        sys.exit(1)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        print(f"ERROR: yt-dlp failed: {e}", file=sys.stderr)
        sys.exit(1)

    videos = []
    for line in result.stdout.strip().splitlines():
        try:
            data = json.loads(line)
            vid_id = data.get("id", "")
            if not vid_id or len(vid_id) != 11:
                continue
            videos.append({
                "id": vid_id,
                "title": data.get("title", f"Video {len(videos)+1}"),
                "duration": int(data.get("duration", 0)),
                "channel": data.get("uploader", data.get("channel", "")),
                "upload_date": data.get("upload_date", ""),
                "url": f"https://www.youtube.com/watch?v={vid_id}",
            })
        except (json.JSONDecodeError, ValueError):
            continue
    return videos


def fetch_playlist_metadata(playlist_url: str) -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp",
             "--flat-playlist", "--dump-single-json", "--no-warnings",
             "--no-config", "--no-cookies-from-browser", playlist_url],
            capture_output=True, text=True, check=True, timeout=60
        )
        data = json.loads(result.stdout)
        return {
            "title": data.get("title", "Unknown Playlist"),
            "channel": data.get("uploader", data.get("channel", "Unknown Channel")),
            "description": (data.get("description", "") or "")[:600],
            "playlist_id": data.get("id", ""),
        }
    except Exception as e:
        print(f"WARNING: Could not fetch playlist metadata: {e}", file=sys.stderr)
        return {"title": "Unknown Playlist", "channel": "Unknown Channel",
                "description": "", "playlist_id": ""}


def _empty_meta() -> dict:
    return {"title": "Unknown Title", "channel": "Unknown Channel",
            "published": "", "duration": 0, "view_count": 0, "description": ""}


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

class TranscriptRateLimited(Exception):
    """Raised when youtube-transcript-api reports YouTube rate-limiting/IP blocking."""


def fetch_transcript(video_id: str) -> list:
    assert_valid_video_id(video_id)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, RequestBlocked, IpBlocked
    except ImportError:
        print("ERROR: Run: uv run youtube_fetch.py <url> "
              "(installs youtube-transcript-api automatically)", file=sys.stderr)
        sys.exit(1)

    rate_limited = (RequestBlocked, IpBlocked)
    api = YouTubeTranscriptApi()
    try:
        tl = api.list(video_id)
        for getter in [
            lambda t: t.find_manually_created_transcript(["en", "en-US", "en-GB"]),
            lambda t: t.find_generated_transcript(["en", "en-US", "en-GB"]),
            lambda t: next(iter(t)),
        ]:
            try:
                return getter(tl).fetch()
            except rate_limited:
                raise TranscriptRateLimited(video_id)
            except Exception:
                continue
    except TranscriptRateLimited:
        raise
    except rate_limited:
        raise TranscriptRateLimited(video_id)
    except Exception:
        pass

    try:
        return api.fetch(video_id)
    except rate_limited:
        raise TranscriptRateLimited(video_id)
    except Exception:
        return []


def fetch_transcript_with_fallback(video_id: str, url: str,
                                   transcribe_lang: str | None = None,
                                   transcribe: bool = False) -> tuple:
    """Return (transcript, source) where source is "captions", "audio", or "".

    Automatically falls back to audio transcription when YouTube rate-limits
    the caption endpoint (regardless of --transcribe), and also when
    --transcribe is set and the video has no caption track.
    """
    try:
        transcript = fetch_transcript(video_id)
        if transcript:
            return transcript, "captions"
    except TranscriptRateLimited:
        print("  WARNING: Transcript endpoint rate-limited by YouTube; "
              "falling back to audio transcription.", file=sys.stderr)
        transcript = transcribe_audio(url, transcribe_lang)
        return (transcript, "audio") if transcript else ([], "")
    if not transcribe:
        return [], ""
    transcript = transcribe_audio(url, transcribe_lang)
    return (transcript, "audio") if transcript else ([], "")


# ---------------------------------------------------------------------------
# Audio transcription (mlxscribe fallback)
# ---------------------------------------------------------------------------

def require_ffmpeg() -> bool:
    if shutil.which("ffmpeg") is None:
        print("WARNING: ffmpeg not found on PATH (needed for --transcribe). "
              "Install with: brew install ffmpeg", file=sys.stderr)
        return False
    return True


def parse_srt_to_segments(content: str) -> list:
    """Parse SRT content into transcript-like segments (with .start/.text)."""
    srt_timing_re = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
        r"\d{1,2}:\d{2}:\d{2},\d{3}"
    )
    segments = []
    for block in content.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        m = srt_timing_re.match(lines[1])
        if not m:
            continue
        h, mi, s, ms = (int(x) for x in m.groups())
        start = h * 3600 + mi * 60 + s + ms / 1000.0
        text = " ".join(" ".join(lines[2:]).split())
        if not text or text == "[inaudible]":
            continue
        segments.append(SimpleNamespace(start=start, end=None, text=text))
    return segments


def transcribe_audio(url: str, transcribe_lang: str | None = None) -> list:
    """Download the audio track and transcribe it with mlxscribe.

    Returns transcript-like segments (with .start/.text), or [] on failure.
    Invocation honors the MLXSCRIBE_CMD env var; the default runs the published
    mlxscribe package through uv.
    """
    if not require_ffmpeg():
        return []
    tmpdir = tempfile.mkdtemp(prefix="yt-audio-")
    try:
        print("  Downloading audio track...")
        subprocess.run(
            [sys.executable, "-m", "yt_dlp", "-f", "ba",
             "--no-config", "--no-cookies-from-browser",
             "-o", str(Path(tmpdir) / "audio.%(ext)s"), url],
            check=True, capture_output=True, text=True, timeout=600,
        )
        audio_files = sorted(Path(tmpdir).glob("audio.*"))
        if not audio_files:
            print("  WARNING: no audio track downloaded.", file=sys.stderr)
            return []
        audio_path = audio_files[0]

        mlx_cmd = os.environ.get("MLXSCRIBE_CMD")
        if not mlx_cmd:
            mlx_cmd = "uvx --from git+https://github.com/edwardchoh/mlxscribe mlxscribe"
        cmd = shlex.split(mlx_cmd)
        cmd += ["--no-mux", "--output-dir", tmpdir, "--format", "srt", str(audio_path)]
        if transcribe_lang:
            cmd += ["--translate-to", transcribe_lang]

        print("  Transcribing audio with mlxscribe (this can take a while)...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            if result.stderr.strip():
                print(result.stderr.strip(), file=sys.stderr)
            print("  WARNING: mlxscribe transcription failed.", file=sys.stderr)
            return []

        srt_files = sorted(Path(tmpdir).glob("audio*.srt"))
        if not srt_files:
            print("  WARNING: mlxscribe produced no transcript files.", file=sys.stderr)
            return []
        # Prefer the translated track when a target language was requested.
        pick = srt_files[0]
        if transcribe_lang:
            translated = [p for p in srt_files if p.name != "audio.srt"]
            if translated:
                pick = translated[0]
        return parse_srt_to_segments(pick.read_text(encoding="utf-8"))
    except subprocess.CalledProcessError as e:
        print(f"  WARNING: audio download failed: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  WARNING: audio transcription failed: {e}", file=sys.stderr)
        return []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_duration(seconds: int) -> str:
    if not seconds:
        return "Unknown"
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ts_link(seconds: float, video_id: str) -> str:
    s = int(seconds)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    label = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
    return f"[{label}](https://youtu.be/{video_id}?t={s})"


def slugify(title: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower())
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:max_len]


def fmt_date(date_str: str) -> str:
    if date_str and len(date_str) == 8:
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    return date_str or datetime.now().strftime("%Y-%m-%d")


def seg_text(seg) -> str:
    return (seg.text if hasattr(seg, "text") else seg["text"]).replace("\n", " ").strip()


def seg_start(seg) -> float:
    return seg.start if hasattr(seg, "start") else seg["start"]


def build_paragraphs(transcript: list, video_id: str, chunk_sec: int = 60) -> str:
    """Group transcript into ~60-second paragraphs, each starting with a timestamp link."""
    if not transcript:
        return ""
    paras, texts, cur_start, next_b = [], [], None, chunk_sec
    for seg in transcript:
        start, text = seg_start(seg), seg_text(seg)
        if not text:
            continue
        if cur_start is None:
            cur_start = start
        if start >= next_b and texts:
            paras.append(f"{ts_link(cur_start, video_id)} {' '.join(texts)}")
            texts, cur_start, next_b = [text], start, start + chunk_sec
        else:
            texts.append(text)
    if texts:
        paras.append(f"{ts_link(cur_start, video_id)} {' '.join(texts)}")
    return "\n\n".join(paras)


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def write_video_file(video_id: str, meta: dict, transcript: list,
                     today: str, prefix: str = "", out_dir: Path = None) -> str:
    published = fmt_date(meta["published"])
    duration_str = format_duration(meta["duration"])
    slug = slugify(meta["title"])
    filename = f"{today}-{prefix}{slug}.md"
    clean_url = f"https://www.youtube.com/watch?v={video_id}"

    transcript_text = build_paragraphs(transcript, video_id)
    word_count = sum(len(seg_text(s).split()) for s in transcript)
    paragraph_count = transcript_text.count("\n\n") + 1 if transcript_text else 0

    content = f"""---
title: "{meta['title']}"
channel: "{meta['channel']}"
published: {published}
duration: "{duration_str}"
duration_seconds: {meta['duration']}
video_id: {video_id}
url: {clean_url}
fetched: {today}
word_count: {word_count}
paragraph_count: {paragraph_count}
---

# {meta['title']}

**Channel:** {meta['channel']}
**Published:** {published}
**Duration:** {duration_str}
**Words:** ~{word_count:,}
**URL:** {clean_url}

## Description

{meta['description']}

## Transcript

{transcript_text if transcript_text else "_No transcript available for this video._"}
"""

    out_dir = out_dir or resolve_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / filename).write_text(content, encoding="utf-8")
    return filename


def write_playlist_index(playlist_meta: dict, playlist_url: str, playlist_id: str,
                         entries: list, today: str, after: str = None,
                         before: str = None, out_dir: Path = None) -> str:
    slug = slugify(playlist_meta["title"])
    filename = f"{today}-{slug}-playlist.md"
    total_dur = sum(e.get("duration_seconds", 0) for e in entries)
    with_transcript = sum(1 for e in entries if e.get("has_transcript"))
    already_fetched = sum(1 for e in entries if e.get("existing"))

    rows = []
    for i, e in enumerate(entries):
        date_part = f" — {fmt_date(e['upload_date'])}" if e.get("upload_date") else ""
        marker = " _(already fetched)_" if e.get("existing") else \
                 (" _(no transcript)_" if not e.get("has_transcript") else "")
        rows.append(f"{i+1}. [{e['title']}](../../raw/youtube/{e['filename']}) — "
                    f"{e['duration_str']}{date_part}{marker}")
    video_list = "\n".join(rows)

    range_part = f"\n**Date range:** {fmt_range(after, before)}" if (after or before) else ""
    existing_part = f"\n**Already fetched:** {already_fetched}" if already_fetched else ""

    content = f"""---
title: "{playlist_meta['title']} (Playlist)"
channel: "{playlist_meta['channel']}"
playlist_id: {playlist_id}
playlist_url: {playlist_url}
video_count: {len(entries)}
fetched: {today}
type: playlist-index
---

# {playlist_meta['title']}

**Channel:** {playlist_meta['channel']}
**Videos:** {len(entries)} · **Total duration:** {format_duration(total_dur)}
**Transcripts fetched:** {with_transcript} of {len(entries)}
**Playlist URL:** {playlist_url}{range_part}{existing_part}

## Description

{playlist_meta['description']}

## Videos

{video_list}
"""

    out_dir = out_dir or resolve_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / filename).write_text(content, encoding="utf-8")
    return filename


# ---------------------------------------------------------------------------
# Options / filtering
# ---------------------------------------------------------------------------

def parse_date_flag(name: str, value: str) -> str:
    """Accept YYYY-MM-DD or YYYYMMDD; return normalized YYYYMMDD."""
    s = value.replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"{name} must be a date like 2026-01-01 (got {value!r})")
    return s


def in_date_range(upload_date: str, after: str = None, before: str = None) -> bool:
    """upload_date is YYYYMMDD (yt-dlp format). Filters are inclusive."""
    if after is None and before is None:
        return True
    if not upload_date:
        return False
    if after and upload_date < after:
        return False
    if before and upload_date > before:
        return False
    return True


def existing_files_by_id(out_dir: Path = None) -> dict:
    """video_id -> filename for every raw/youtube/*.md with a video_id frontmatter."""
    out_dir = out_dir or resolve_out_dir()
    found = {}
    if not out_dir.exists():
        return found
    for f in out_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = re.search(r"^video_id:\s*([a-zA-Z0-9_-]{11})\s*$", text, re.MULTILINE)
        if m:
            found.setdefault(m.group(1), f.name)
    return found


def parse_args(argv: list):
    if not argv:
        print("Usage: uv run youtube_fetch.py <url> [--limit N] [--skip-existing] "
              "[--after YYYY-MM-DD] [--before YYYY-MM-DD] [--out-dir DIR] "
              "[--transcribe [--transcribe-lang LANG]]")
        sys.exit(1)

    url = argv[0].strip()
    limit = None
    skip_existing = False
    after = None
    before = None
    transcribe = False
    transcribe_lang = None
    out_dir = None

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--limit":
            i += 1
            if i >= len(argv):
                raise ValueError("--limit requires an integer")
            try:
                limit = int(argv[i])
            except ValueError:
                raise ValueError("--limit requires an integer")
        elif arg == "--skip-existing":
            skip_existing = True
        elif arg == "--out-dir":
            i += 1
            if i >= len(argv):
                raise ValueError("--out-dir requires a path")
            out_dir = argv[i]
        elif arg == "--transcribe":
            transcribe = True
        elif arg == "--transcribe-lang":
            i += 1
            if i >= len(argv):
                raise ValueError("--transcribe-lang requires a language (e.g. english)")
            transcribe_lang = argv[i]
        elif arg in ("--after", "--before"):
            i += 1
            if i >= len(argv):
                raise ValueError(f"{arg} requires a date (YYYY-MM-DD)")
            parsed = parse_date_flag(arg, argv[i])
            if arg == "--after":
                after = parsed
            else:
                before = parsed
        else:
            raise ValueError(f"Unknown argument: {arg}")
        i += 1

    return url, limit, skip_existing, after, before, transcribe, transcribe_lang, out_dir


def fmt_range(after: str, before: str) -> str:
    lo = fmt_date(after) if after else "any"
    hi = fmt_date(before) if before else "any"
    return f"{lo} → {hi}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        url, limit, skip_existing, after, before, transcribe, transcribe_lang, out_dir_flag = \
            parse_args(sys.argv[1:])
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        assert_youtube_url(url)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = resolve_out_dir(out_dir_flag)
    print(f"Output dir: {show(out_dir)}")

    # ---- PLAYLIST / CHANNEL ----
    if is_playlist_url(url) or is_channel_url(url):
        is_channel = is_channel_url(url)
        playlist_id = extract_channel_id(url) if is_channel else extract_playlist_id(url)
        kind = "Channel" if is_channel else "Playlist"
        print(f"{kind} detected: {playlist_id}")
        if limit:
            print(f"  Limit: first {limit} videos")
        if skip_existing:
            print("  Skip-existing: videos already in raw/youtube/ will be skipped")
        if after or before:
            print(f"  Date range: {fmt_range(after, before)}")

        print("Fetching playlist metadata...")
        pl_meta = fetch_playlist_metadata(url)
        print(f"  Title:   {pl_meta['title']}")
        print(f"  Channel: {pl_meta['channel']}")

        print("Fetching video list...")
        videos = fetch_playlist_videos(url, limit=limit)
        print(f"  Found {len(videos)} videos")

        existing = existing_files_by_id(out_dir) if skip_existing else {}

        entries = []
        fetched = 0
        skipped_existing = 0
        skipped_date = 0
        for i, vid in enumerate(videos):
            prefix = f"{i+1:02d}-"

            if skip_existing and vid["id"] in existing:
                print(f"\n[{i+1}/{len(videos)}] {vid['title']} — already fetched "
                      f"({show(out_dir / existing[vid['id']])}), skipping")
                skipped_existing += 1
                entries.append({
                    "title": vid["title"],
                    "filename": existing[vid["id"]],
                    "duration_str": format_duration(vid["duration"]),
                    "duration_seconds": vid["duration"],
                    "has_transcript": False,
                    "upload_date": vid["upload_date"],
                    "existing": True,
                })
                continue

            print(f"\n[{i+1}/{len(videos)}] {vid['title']}")
            meta = fetch_metadata(vid["url"])
            if meta["title"] == "Unknown Title": meta["title"] = vid["title"]
            if meta["channel"] == "Unknown Channel" and vid["channel"]: meta["channel"] = vid["channel"]
            if meta["duration"] == 0 and vid["duration"]: meta["duration"] = vid["duration"]

            if not in_date_range(meta["published"], after, before):
                print(f"  Skipping — upload {fmt_date(meta['published']) or 'unknown'} "
                      f"outside {fmt_range(after, before)}")
                skipped_date += 1
                continue

            print(f"  Fetching transcript...")
            transcript, source = fetch_transcript_with_fallback(
                vid["id"], vid["url"], transcribe_lang, transcribe)
            has_transcript = bool(transcript)
            if has_transcript:
                wc = sum(len(seg_text(s).split()) for s in transcript)
                if source == "audio":
                    print(f"  Transcribed from audio: ~{wc:,} words.")
                else:
                    print(f"  Words: ~{wc:,}")
            else:
                print(f"  No transcript available")
                if transcribe:
                    print("  WARNING: Audio transcription unavailable.")

            filename = write_video_file(vid["id"], meta, transcript, today,
                                        prefix=prefix, out_dir=out_dir)
            fetched += 1
            entries.append({
                "title": meta["title"],
                "filename": filename,
                "duration_str": format_duration(meta["duration"]),
                "duration_seconds": meta["duration"],
                "has_transcript": has_transcript,
                "upload_date": meta["published"],
            })
            print(f"  Saved: {show(out_dir / filename)}")
            if i < len(videos) - 1:
                time.sleep(0.5)

        index_filename = write_playlist_index(pl_meta, url, playlist_id, entries, today,
                                              after=after, before=before, out_dir=out_dir)
        with_t = sum(1 for e in entries if e["has_transcript"])

        print(f"\n{'='*60}")
        print(f"Done.")
        print(f"  Playlist index: {show(out_dir / index_filename)}")
        print(f"  Videos fetched: {fetched} (new)")
        print(f"  Already present: {skipped_existing}")
        if after or before:
            print(f"  Skipped by date: {skipped_date}")
        print(f"  Transcripts:    {with_t} of {len(entries)}")
        print(f"\nNext: ask Claude to compile {show(out_dir / index_filename)}")

    # ---- SINGLE VIDEO ----
    else:
        video_id = extract_video_id(url)
        print(f"Single video: {video_id}")

        if skip_existing:
            existing = existing_files_by_id(out_dir)
            if video_id in existing:
                print(f"  Already fetched: {show(out_dir / existing[video_id])} — skipping")
                sys.exit(0)
        if after or before:
            print("  Note: --after/--before only apply to playlist/channel fetches")

        print("Fetching metadata...")
        meta = fetch_metadata(url)
        print(f"  Title:    {meta['title']}")
        print(f"  Channel:  {meta['channel']}")
        print(f"  Duration: {format_duration(meta['duration'])}")

        print("Fetching transcript...")
        transcript, source = fetch_transcript_with_fallback(
            video_id, url, transcribe_lang, transcribe)
        if not transcript:
            print("  WARNING: No transcript found.")
            if transcribe:
                print("  WARNING: Audio transcription unavailable; writing metadata only.")
        else:
            wc = sum(len(seg_text(s).split()) for s in transcript)
            if source == "audio":
                print(f"  Transcribed from audio: ~{wc:,} words.")
            else:
                print(f"  Words: ~{wc:,}")

        filename = write_video_file(video_id, meta, transcript, today, out_dir=out_dir)
        print(f"\nDone.")
        print(f"  Raw file: {show(out_dir / filename)}")
        print(f"\nNext: ask Claude to compile {show(out_dir / filename)}")


if __name__ == "__main__":
    main()
