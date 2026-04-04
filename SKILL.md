# YouTube Compile Skill

Fetches YouTube video(s) transcripts and compiles them into detailed markdown articles with embedded player and clickable timestamps. Works standalone or inside an LLM knowledge base.

## Trigger

User invokes `/youtube <url>` OR pastes a YouTube URL and says something like:
- "process this video / playlist / course"
- "compile this YouTube link"
- "get the transcript for this"
- "add this to my notes"

Detect YouTube URLs by pattern: `youtube.com/watch`, `youtu.be/`, `youtube.com/embed/`, `youtube.com/playlist`

## Setup Check

Before running, verify `youtube_fetch.py` is accessible:
- If the user is inside a project that has `youtube_fetch.py` in its root or a `tools/` subdirectory, use that.
- Otherwise, tell the user: "Place `youtube_fetch.py` in your project directory. Get it from: https://github.com/zerowing113/claude-youtube-skill"

Resolve the script path: check `./youtube_fetch.py` then `./tools/youtube_fetch.py`.

## Workflow — Single Video

### Step 1 — Run the fetch script

```bash
python youtube_fetch.py "<url>"
# or if in tools/ subdirectory:
python tools/youtube_fetch.py "<url>"
```

If the script fails:
- `yt-dlp not found` → `pip install yt-dlp`
- `youtube-transcript-api not found` → `pip install youtube-transcript-api`
- `No transcript found` → video has no captions; write article with metadata only, note the gap
- Other errors → show the error clearly

Report: file written, word count, paragraph count.

### Step 2 — Read the raw file

File is at `raw/youtube/YYYY-MM-DD-{slug}.md` (created by the script).

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
python youtube_fetch.py "<playlist_url>"
# limit to first N videos:
python youtube_fetch.py "<playlist_url>" --limit 10
```

For playlists > 20 videos, suggest `--limit` first and confirm with the user.

The script writes:
- `raw/youtube/YYYY-MM-DD-{NN}-{slug}.md` per video
- `raw/youtube/YYYY-MM-DD-{course-slug}-playlist.md` — index of all videos

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
