# Claude YouTube Skill

A [Claude Code](https://claude.ai/code) skill that fetches YouTube video transcripts and compiles them into detailed reference articles with embedded video player and clickable timestamps.

Works with any project — standalone notes folder, Obsidian vault, or an [LLM knowledge base](https://github.com/zerowing113/llm-knowledge-base-template).

## What It Does

- **Single video** → one markdown article with iframe embed + timestamped sections
- **Full playlist / course** → course overview article + one file per lecture
- **Reference mode by default** — exhaustive detail (1,500–5,000 words), not summaries
- **Clickable timestamps** — every key moment links back to that exact second in the video

## Install

Requirements: [uv](https://docs.astral.sh/uv/) (Python ≥ 3.9).

```bash
brew install uv          # or see https://docs.astral.sh/uv/getting-started/installation/
git clone https://github.com/zerowing113/claude-youtube-skill.git
cd claude-youtube-skill
```

Install the skill folder however you manage skills (e.g. `npx skills`, or copy the
folder into your agent's skills directory). The skill ships `scripts/youtube_fetch.py`
bundled with it, referenced by relative path from the skill root — nothing to copy
into your project. Dependencies (`youtube-transcript-api`, `yt-dlp`) are declared
inline in the script and installed automatically by `uv` on first run — no separate
install step.

## Usage

Open your project in Claude Code, then:

```
/youtube-wiki https://youtube.com/watch?v=...          # single video
/youtube-wiki https://youtube.com/playlist?list=...    # full playlist or course
/youtube-wiki <playlist_url> --limit 10                # first 10 videos only
```

The fetch script ships with the skill and is run via uv:

```bash
uv run scripts/youtube_fetch.py https://youtube.com/watch?v=...          # single video
uv run scripts/youtube_fetch.py https://youtube.com/playlist?list=...    # full playlist
uv run scripts/youtube_fetch.py <playlist_url> --limit 10                # first 10 videos
```

Or natural language: *"compile this video"*, *"process this playlist"*, *"get the transcript for this"* — paste the URL and Claude picks it up.

## Testing / Evals

`evals/evals.json` defines test prompts plus assertions for verifying the skill
end-to-end (Anthropic skill-creator format). It covers:

- **Single video compile** — a TED talk is fetched and compiled into a detailed article
- **Playlist / course compile** — a playlist becomes raw files + a course overview
- **No-transcript handling** — a caption-less video produces a metadata-only article, nothing fabricated
- **Non-YouTube URL rejected** — the skill refuses invalid URLs and writes nothing

Run them with a skill-eval harness (e.g. Anthropic's [skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator)):
spawn an agent with access to this skill per test prompt, then grade each output
against the assertions in `evals/evals.json`.

## Output

```
raw/youtube/
  2026-04-04-video-title.md              # single video transcript + metadata
  2026-04-04-01-lecture-one.md           # playlist: one per video (numbered)
  2026-04-04-course-name-playlist.md     # playlist: index of all videos

notes/ or wiki/{category}/
  video-title.md                         # compiled article
  course-name.md                         # course overview article
```

### Article Format

Every compiled article includes:

- **Embedded video player** (iframe, works in Obsidian)
- **One-sentence summary** in frontmatter
- **Named sections** for each major topic
- **Clickable timestamps** linking to exact moments: `[00:05:23](https://youtu.be/...?t=323)`
- **Direct quotes** from the speaker
- **Key Concepts / Connections** sections

### Course / Playlist Format

- Course overview with `videoseries` iframe (plays the full playlist)
- Curriculum table with links to each lecture
- Thematic topic clusters grouping related lectures
- Individual lecture articles compiled on request

## Requirements

- [Claude Code](https://claude.ai/code)
- [uv](https://docs.astral.sh/uv/) — runs the script and auto-installs its dependencies
- Python 3.9+
- `youtube-transcript-api` — fetches captions (auto-installed by `uv`)
- `yt-dlp` — fetches metadata: title, channel, duration (auto-installed by `uv`)

Videos must have captions (auto-generated or manual). Most YouTube videos do.

## Security

`scripts/youtube_fetch.py` accepts only YouTube URLs (`youtube.com` / `youtu.be`), fetches
only metadata and captions, and writes only to `raw/youtube/`. It never reads
config files, browser cookies, or local files, and never uses yt-dlp's
`--exec`/output options. Only the YouTube URL/video ID ever goes over the network.

## Tips

- **Large playlists** — use `--limit 10` to test before fetching a 50-video course
- **No captions?** — Claude will still write an article using title, channel, and description
- **Works great with Obsidian** — the iframe renders the video inline; timestamps open the video at that moment
- **LLM knowledge base users** — the fetch script is bundled with the skill, so no `tools/` setup is needed

## Credits

- Inspired by [Andrej Karpathy's LLM Knowledge Base workflow](https://x.com/karpathy/status/2039805659525644595)
- Part of the [LLM Knowledge Base Template](https://github.com/zerowing113/llm-knowledge-base-template)
