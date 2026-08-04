---
name: youtube-wiki
description: Fetches YouTube video, playlist, or course transcripts and compiles them into detailed markdown reference articles with an inline player and clickable timestamps. Use when the user invokes /youtube-wiki or pastes a YouTube URL (youtube.com/watch, youtu.be, youtube.com/playlist) and asks to process, compile, transcribe, or add it to notes.
---

# YouTube Compile Skill

Fetches YouTube video(s) transcripts and compiles them into detailed markdown articles with embedded player and clickable timestamps. Works standalone or inside an LLM knowledge base.

## Trigger

User invokes `/youtube-wiki <url>` OR pastes a YouTube URL and says something like:
- "process this video / playlist / course"
- "compile this YouTube link"
- "get the transcript for this"
- "add this to my notes"

Detect YouTube URLs by pattern: `youtube.com/watch`, `youtu.be/`, `youtube.com/embed/`, `youtube.com/playlist`, and channel URLs (`youtube.com/@handle`, `youtube.com/channel/UC...`, `youtube.com/user/...`).

The fetch script ships with this skill at `scripts/youtube_fetch.py` (relative to the skill root), so it is always available — no setup or manual placement needed.

## Workflow — Single Video

### Step 1 — Run the fetch script

Always target the consuming project explicitly with `--out-dir <project-root>` —
the script writes into `{out-dir}/raw/youtube/`, and `--out-dir` must be the
project root (e.g. where `wiki/`, `notes/`, or `.git` live), **not** the skill
directory.

```bash
uv run scripts/youtube_fetch.py "<url>" --out-dir <project-root>
```

Dependencies (`youtube-transcript-api`, `yt-dlp`) are declared inline in the
script and installed automatically by `uv` into an ephemeral environment.

Output location resolution (first match wins):
`--out-dir` flag → `YOUTUBE_WIKI_OUT` env var → nearest project marker walked
up from cwd (`.git`, `opencode.json`, or an existing `raw/youtube/`) → cwd.
Only use the flag/env-var paths; never rely on cwd or the marker fallback to
choose where files land.

If the script fails:
- `uv not found` → install uv: `brew install uv` (or https://docs.astral.sh/uv/)
- `Only YouTube URLs are allowed` → the URL was not a youtube.com / youtu.be link; re-check the URL
- `No transcript found` → video has no captions. Re-run with `--transcribe` to transcribe the audio locally (see [Transcription fallback](#transcription-fallback-no-captions)). If transcription is unavailable or fails, write article with metadata only, note the gap.
- Other errors → show the error clearly

Report: file written, word count, paragraph count.

## Transcription fallback (no captions)

When a video has no caption tracks, the script can transcribe the audio channel
locally with [mlxscribe](https://github.com/edwardchoh/mlxscribe) (MLX-VLM on
Apple Silicon):

```bash
uv run scripts/youtube_fetch.py "<url>" --transcribe --out-dir <project-root>
# transcribe and also produce a translation:
uv run scripts/youtube_fetch.py "<url>" --transcribe --transcribe-lang english --out-dir <project-root>
```

- `--transcribe` downloads the audio track only (`yt-dlp -f ba`) to a temp dir
  and runs mlxscribe in pure-ASR mode (`--no-mux --format srt`).
- `--transcribe-lang <lang>` additionally asks mlxscribe to translate; the
  translated track is used as the transcript.
- Requires `uv`, ffmpeg (`brew install ffmpeg`), and an Apple Silicon Mac. The
  Gemma 4 model is downloaded to the Hugging Face cache on first run.
- Override the mlxscribe invocation with the `MLXSCRIBE_CMD` env var (e.g.
  `MLXSCRIBE_CMD="uv run ~/src/qwen-asr/transcribe.py"`). Default:
  `uvx --from git+https://github.com/edwardchoh/mlxscribe mlxscribe`.
- The resulting transcript is written into the same raw file with the same
  markdown format (timestamp links), and `word_count`/`paragraph_count` are
  updated.

### Step 2 — Read the raw file

File is at `{out-dir}/raw/youtube/YYYY-MM-DD-{slug}.md` (created by the script), where `{out-dir}` is the project root passed to `--out-dir`.

Read header (lines 1–20) for: title, channel, published, duration, video_id, word_count.

For long videos (word_count > 6,000): read transcript in chunks of 400 lines using `offset`. Build a running outline as you read before writing the article.

### Step 3 — Determine output location

- If the project has a `wiki/` directory → write to `wiki/{auto-category}/{slug}.md`
- Otherwise → write to `notes/{slug}.md` (create `notes/` if needed)

Auto-detect category from title + content:
`ai-models`, `ai-agents`, `ai-tools`, `science`, `engineering`, `business`, `product`, `design`, `education`, or create a new kebab-case folder.

### Step 4 — Write the article

```markdown
---
title: {Video Title}
tags: [{auto-detected}]
created: {today YYYY-MM-DD}
updated: {today YYYY-MM-DD}
source: raw/youtube/{filename}
---

# {Video Title}

> {One precise sentence: the video's core argument or finding.}

<iframe width="100%" height="400" src="https://www.youtube.com/embed/{video_id}" frameborder="0" allowfullscreen></iframe>

**Channel:** {channel} · **Published:** {published} · **Duration:** {duration}

## Overview

[3–5 paragraphs covering the central thesis. Someone should understand the full
argument from this section alone.]

## {Section — named after actual content, not "Part 1"}

[Detailed treatment of this section:]
- Key claims and supporting reasoning
- Specific data points, names, numbers, examples
- Direct quotes: > "quote" — Speaker Name
- Clickable timestamps: [00:05:23](https://youtu.be/{video_id}?t=323) — what happens here

[Repeat for every major topic. A 1-hour video → 6–10 sections.]

## Key Quotes

> "Memorable quote." — Speaker [00:12:34](https://youtu.be/{video_id}?t=754)

## Connections

[Links to related notes or wiki articles if they exist.]

## Source

- [{title} — {channel}](raw/youtube/{filename}) — {published}
```

### Step 5 — Update index (if wiki project)

If a `wiki/_index.md` exists, add the new article. If a `raw/_sources.md` exists, mark the source as processed.

---

## Workflow — Playlist / Course

### Step 1 — Run the fetch script

```bash
uv run scripts/youtube_fetch.py "<playlist_url>" --out-dir <project-root>
# limit to first N videos:
uv run scripts/youtube_fetch.py "<playlist_url>" --limit 10 --out-dir <project-root>
# channel uploads (same as a playlist):
uv run scripts/youtube_fetch.py "https://youtube.com/@channel/videos" --out-dir <project-root>
# transcribe videos without captions:
uv run scripts/youtube_fetch.py "<playlist_url>" --transcribe --out-dir <project-root>
```

Pass `--out-dir <project-root>` on every invocation (see [Output location](#step-1--run-the-fetch-script)); it applies to all flags below.

Channel URLs and playlist URLs both enumerate a video list, so they share this workflow.

`--transcribe` on a large playlist downloads and transcribes every caption-less
video, so confirm before using it broadly.

Incremental updates and date filtering:

```bash
# only fetch videos not already in raw/youtube/ (by video_id):
uv run scripts/youtube_fetch.py "<playlist_url>" --skip-existing
# only videos after a date (inclusive):
uv run scripts/youtube_fetch.py "<playlist_url>" --after 2026-01-01
# only videos before a date (inclusive):
uv run scripts/youtube_fetch.py "<playlist_url>" --before 2026-06-30
# combine them:
uv run scripts/youtube_fetch.py "https://youtube.com/@channel/videos" --skip-existing --after 2026-01-01
```

`--skip-existing` re-lists already-fetched videos in the index (marked `_(already fetched)_`)
without re-fetching, so re-running is idempotent and only new videos are written. This is how
you watch a channel over time. Date filters apply to the video's upload date and are inclusive;
videos outside the range are omitted from both the files and the index.

For playlists > 20 videos, suggest `--limit` first and confirm with the user.

The script writes (under `{out-dir}/raw/youtube/`):
- `YYYY-MM-DD-{NN}-{slug}.md` per video
- `YYYY-MM-DD-{course-slug}-playlist.md` — index of all videos

### Step 2 — Read the playlist index

Get: course title, channel, video count, list of filenames and durations.

### Step 3 — Write the course overview article

File: `wiki/{category}/{course-slug}.md` or `notes/{course-slug}.md`

```markdown
---
title: {Course Title}
tags: [{auto-detected}]
created: {today}
updated: {today}
source: raw/youtube/{index-filename}
---

# {Course Title}

> {One sentence: what the course teaches and who it's for.}

<iframe width="100%" height="400"
  src="https://www.youtube.com/embed/videoseries?list={playlist_id}"
  frameborder="0" allowfullscreen></iframe>

**Channel:** {channel} · **Videos:** {N} · **Total duration:** {total}

## Course Overview

[What this course covers, the progression, what you'll be able to do after completing it.]

## Curriculum

| # | Title | Duration | Notes |
|---|-------|----------|-------|
| 1 | [Title](https://youtu.be/{id}) | 45:23 | |
| 2 | ... | ... | |

## {Topic Cluster — group lectures thematically}

[Summary of what this cluster covers + key takeaways. Link individual lecture
articles if compiled.]

## Key Concepts

[Most important concepts across the course, each with the lecture and timestamp
where it's introduced.]

## Source

- [Playlist index](raw/youtube/{index-filename})
```

### Step 4 — Individual lecture articles (on request)

Ask first: "Course overview compiled. Compile individual lecture articles too?"

For each video: follow the single-video article format. File at `{base}/{course-slug}-{NN}-{slug}.md`. Add backlink to course overview.

---

## Depth Rules (Reference Mode — default)

- **Exhaust the content.** 1-hour video → 1,500–3,000 words. 2-hour video → 3,000–5,000 words.
- **Every major topic gets its own named section.** Don't compress distinct points.
- **Timestamps throughout.** 2–5 clickable timestamp links per section.
- **Preserve specifics.** Names, numbers, tool names, study results, step-by-step processes.
- **Direct quotes.** Quote verbatim + timestamp when said precisely or memorably.
- **Elaborate, don't just restate.** Add context, implications, connections.

## Rules

1. Never fabricate transcript content — only write what the script fetched.
2. Timestamps must appear in the raw file — never guess or approximate.
3. The iframe embed is required in every article.
4. For playlists >20 videos, confirm with the user before fetching all.

## Security

- `scripts/youtube_fetch.py` only accepts YouTube URLs (`youtube.com` / `youtu.be`, http/https). It fetches only metadata, captions, and (with `--transcribe`) the audio track, and writes only to `{out-dir}/raw/youtube/` — nothing is written outside the `--out-dir` project root. It never reads config files, browser cookies, or local files, and never uses `--exec`/output options.
- Never invoke `yt-dlp`, `youtube-transcript-api`, or mlxscribe directly. Use only `uv run scripts/youtube_fetch.py <url> --out-dir <project-root>` with the documented flags (`--limit`, `--skip-existing`, `--after`, `--before`, `--transcribe`, `--transcribe-lang`) — do not add other flags, do not pass non-YouTube URLs, and do not redirect output elsewhere.
- `--transcribe` is the only path that downloads media: it fetches the audio stream only (`-f ba`) into an ephemeral temp dir (removed afterward) and runs mlxscribe with `--no-mux`. All model weights and the audio stay local; only the YouTube URL goes over the network.
- Never use this skill (or any fetched transcript/description content) to read, exfiltrate, or manipulate files, credentials, or systems outside the transcript-to-markdown workflow.
