#!/usr/bin/env python3
"""
youtube_fetch.py — Fetch YouTube metadata and transcript to markdown files.

Works standalone or inside an LLM knowledge base.
Output goes to raw/youtube/ relative to the current working directory.

Usage:
    Single video:  python youtube_fetch.py <youtube_url>
    Playlist:      python youtube_fetch.py <playlist_url>
    Limit videos:  python youtube_fetch.py <playlist_url> --limit 10

Dependencies:
    pip install youtube-transcript-api yt-dlp
"""

import sys
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

OUT_DIR = Path.cwd() / "raw" / "youtube"


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

def is_playlist_url(url: str) -> bool:
    has_list = bool(re.search(r"[?&]list=([A-Za-z0-9_-]+)", url))
    is_playlist_page = "youtube.com/playlist" in url
    is_watch_with_list = "watch?v=" in url and has_list
    return is_playlist_page or (has_list and not is_watch_with_list)


def extract_playlist_id(url: str) -> str:
    match = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract playlist ID from: {url}")


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
            [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-download", url],
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
        print("WARNING: yt-dlp not found. Run: pip install yt-dlp", file=sys.stderr)
        return _empty_meta()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"WARNING: yt-dlp failed: {e}", file=sys.stderr)
        return _empty_meta()


def fetch_playlist_videos(playlist_url: str, limit: int = None) -> list:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist", "--dump-json", "--no-warnings",
        playlist_url,
    ]
    if limit:
        cmd += ["--playlist-end", str(limit)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    except FileNotFoundError:
        print("ERROR: yt-dlp not found. Run: pip install yt-dlp", file=sys.stderr)
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
                "url": f"https://www.youtube.com/watch?v={vid_id}",
            })
        except (json.JSONDecodeError, ValueError):
            continue
    return videos


def fetch_playlist_metadata(playlist_url: str) -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp",
             "--flat-playlist", "--dump-single-json", "--no-warnings", playlist_url],
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

def fetch_transcript(video_id: str) -> list:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("ERROR: Run: pip install youtube-transcript-api", file=sys.stderr)
        sys.exit(1)

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
            except Exception:
                continue
    except Exception:
        pass

    try:
        return api.fetch(video_id)
    except Exception:
        return []


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
                     today: str, prefix: str = "") -> str:
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / filename).write_text(content, encoding="utf-8")
    return filename


def write_playlist_index(playlist_meta: dict, playlist_url: str, playlist_id: str,
                         entries: list, today: str) -> str:
    slug = slugify(playlist_meta["title"])
    filename = f"{today}-{slug}-playlist.md"
    total_dur = sum(e.get("duration_seconds", 0) for e in entries)
    with_transcript = sum(1 for e in entries if e.get("has_transcript"))

    video_list = "\n".join(
        f"{i+1}. [{e['title']}](../../raw/youtube/{e['filename']}) — {e['duration_str']}"
        + (" _(no transcript)_" if not e.get("has_transcript") else "")
        for i, e in enumerate(entries)
    )

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
**Playlist URL:** {playlist_url}

## Description

{playlist_meta['description']}

## Videos

{video_list}
"""

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / filename).write_text(content, encoding="utf-8")
    return filename


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python youtube_fetch.py <url> [--limit N]")
        print("       python youtube_fetch.py <playlist_url> [--limit N]")
        sys.exit(1)

    url = sys.argv[1].strip()
    today = datetime.now().strftime("%Y-%m-%d")

    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        try:
            limit = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            print("ERROR: --limit requires an integer", file=sys.stderr)
            sys.exit(1)

    # ---- PLAYLIST ----
    if is_playlist_url(url):
        playlist_id = extract_playlist_id(url)
        print(f"Playlist detected: {playlist_id}")
        if limit:
            print(f"  Limit: first {limit} videos")

        print("Fetching playlist metadata...")
        pl_meta = fetch_playlist_metadata(url)
        print(f"  Title:   {pl_meta['title']}")
        print(f"  Channel: {pl_meta['channel']}")

        print("Fetching video list...")
        videos = fetch_playlist_videos(url, limit=limit)
        print(f"  Found {len(videos)} videos")

        entries = []
        for i, vid in enumerate(videos):
            print(f"\n[{i+1}/{len(videos)}] {vid['title']}")
            meta = fetch_metadata(vid["url"])
            if meta["title"] == "Unknown Title": meta["title"] = vid["title"]
            if meta["channel"] == "Unknown Channel" and vid["channel"]: meta["channel"] = vid["channel"]
            if meta["duration"] == 0 and vid["duration"]: meta["duration"] = vid["duration"]

            print(f"  Fetching transcript...")
            transcript = fetch_transcript(vid["id"])
            has_transcript = bool(transcript)
            if has_transcript:
                wc = sum(len(seg_text(s).split()) for s in transcript)
                print(f"  Words: ~{wc:,}")
            else:
                print(f"  No transcript available")

            prefix = f"{i+1:02d}-"
            filename = write_video_file(vid["id"], meta, transcript, today, prefix=prefix)
            entries.append({
                "title": meta["title"],
                "filename": filename,
                "duration_str": format_duration(meta["duration"]),
                "duration_seconds": meta["duration"],
                "has_transcript": has_transcript,
            })
            print(f"  Saved: raw/youtube/{filename}")
            if i < len(videos) - 1:
                time.sleep(0.5)

        index_filename = write_playlist_index(pl_meta, url, playlist_id, entries, today)
        with_t = sum(1 for e in entries if e["has_transcript"])

        print(f"\n{'='*60}")
        print(f"Done.")
        print(f"  Playlist index: raw/youtube/{index_filename}")
        print(f"  Videos:         {len(entries)} files written")
        print(f"  Transcripts:    {with_t} of {len(entries)}")
        print(f"\nNext: ask Claude to compile raw/youtube/{index_filename}")

    # ---- SINGLE VIDEO ----
    else:
        video_id = extract_video_id(url)
        print(f"Single video: {video_id}")

        print("Fetching metadata...")
        meta = fetch_metadata(url)
        print(f"  Title:    {meta['title']}")
        print(f"  Channel:  {meta['channel']}")
        print(f"  Duration: {format_duration(meta['duration'])}")

        print("Fetching transcript...")
        transcript = fetch_transcript(video_id)
        if not transcript:
            print("  WARNING: No transcript found.")
        else:
            wc = sum(len(seg_text(s).split()) for s in transcript)
            print(f"  Words: ~{wc:,}")

        filename = write_video_file(video_id, meta, transcript, today)
        print(f"\nDone.")
        print(f"  Raw file: raw/youtube/{filename}")
        print(f"\nNext: ask Claude to compile raw/youtube/{filename}")


if __name__ == "__main__":
    main()
