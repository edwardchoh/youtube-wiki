# Claude YouTube Skill

A [Claude Code](https://claude.ai/code) skill that fetches YouTube video transcripts and compiles them into detailed reference articles with embedded video player and clickable timestamps.

Works with any project — standalone notes folder, Obsidian vault, or an [LLM knowledge base](https://github.com/zerowing113/llm-knowledge-base-template).

## What It Does

- **Single video** → one markdown article with iframe embed + timestamped sections
- **Full playlist / course** → course overview article + one file per lecture
- **No-captions fallback** → transcribes the audio channel locally with mlxscribe (MLX-VLM on Apple Silicon)
- **Reference mode by default** — exhaustive detail (1,500–5,000 words), not summaries
- **Clickable timestamps** — every key moment links back to that exact second in the video

## Install

Requirements: [uv](https://docs.astral.sh/uv/) (Python ≥ 3.9).

```bash
brew install uv          # or see https://docs.astral.sh/uv/getting-started/installation/
git clone https://github.com/edwardchoh/youtube-wiki.git
cd youtube-wiki
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
/youtube-wiki https://youtube.com/@channel/videos      # a channel's uploads
/youtube-wiki <playlist_url> --limit 10                # first 10 videos only
```

Videos without captions: transcribe the audio locally (requires ffmpeg + an
Apple Silicon Mac; downloads the Gemma 4 model on first run):

```bash
uv run scripts/youtube_fetch.py https://youtube.com/watch?v=... --transcribe --out-dir <project-root>
uv run scripts/youtube_fetch.py <url> --transcribe --transcribe-lang english --out-dir <project-root>   # + translation
```

The fetch script ships with the skill and is run via uv. Always pass
`--out-dir <project-root>` so files land in the consuming project, never in the
skill's own directory:

```bash
uv run scripts/youtube_fetch.py https://youtube.com/watch?v=... --out-dir <project-root>   # single video
uv run scripts/youtube_fetch.py https://youtube.com/playlist?list=... --out-dir <project-root>  # full playlist
uv run scripts/youtube_fetch.py https://youtube.com/@channel/videos --out-dir <project-root>    # channel uploads
uv run scripts/youtube_fetch.py <playlist_url> --limit 10 --out-dir <project-root>  # first 10 videos
uv run scripts/youtube_fetch.py <playlist_url> --skip-existing --out-dir <project-root>  # only new videos
uv run scripts/youtube_fetch.py <playlist_url> --after 2026-01-01 --out-dir <project-root>  # uploads on/after date
uv run scripts/youtube_fetch.py <playlist_url> --before 2026-06-30 --out-dir <project-root>  # uploads on/before date
uv run scripts/youtube_fetch.py <url> --transcribe --out-dir <project-root>  # ASR when no captions
```

**Output location.** The script writes into `{out-dir}/raw/youtube/`. `--out-dir`
is a project root (where `wiki/`, `notes/`, or `.git` live) — never the skill
directory. If `--out-dir` is omitted, the location is resolved as: `--out-dir`
flag → `YOUTUBE_WIKI_OUT` env var → nearest project marker walked up from cwd
(`.git`, `opencode.json`, or an existing `raw/youtube/`) → cwd. Prefer passing
`--out-dir` explicitly.

- **Watch a channel over time** — re-run `--skip-existing` periodically; it skips any
  `video_id` already in `raw/youtube/`, so only new uploads are fetched, and the playlist
  index re-lists the rest as already fetched.
- **Date filtering** — `--after` / `--before` (inclusive, `YYYY-MM-DD`) restrict to a window
  of upload dates. Combine with `--skip-existing` for incremental catches up to a date.
- **No captions?** — add `--transcribe` to download the audio track and transcribe it locally
  with mlxscribe (`--transcribe-lang <lang>` also produces a translation). Override the
  invocation with the `MLXSCRIBE_CMD` env var.

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

All raw files are written under `{out-dir}/raw/youtube/`:

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

Videos must have captions (auto-generated or manual) — or run with
`--transcribe` to transcribe the audio locally. That path additionally needs:
ffmpeg (`brew install ffmpeg`), an Apple Silicon Mac, and Python 3.12+ (for
mlxscribe, installed automatically via `uvx`).

## Security

`scripts/youtube_fetch.py` accepts only YouTube URLs (`youtube.com` / `youtu.be`), fetches
only metadata, captions, and (with `--transcribe`) the audio track, and writes only to
`{out-dir}/raw/youtube/` — nothing is written outside the `--out-dir` project root. It
never reads config files, browser cookies, or local files, and never uses
yt-dlp's `--exec`/output options. With `--transcribe`, the audio stream is downloaded to an
ephemeral temp dir and removed afterward; mlxscribe runs with `--no-mux`, so the source file
is never modified. Only the YouTube URL/video ID ever goes over the network.

## Tips

- **Large playlists** — use `--limit 10` to test before fetching a 50-video course
- **Channels** — point the skill at `https://youtube.com/@handle/videos` (or `/shorts`, `/streams`) to process a channel's uploads like a playlist
- **Incremental** — `--skip-existing` skips videos already in `raw/youtube/`; pair with `--after`/`--before` for dated catch-ups
- **No captions?** — add `--transcribe` to the fetch; it downloads the audio and transcribes it locally, and Claude compiles the article from the real transcript instead of metadata only
- **Works great with Obsidian** — the iframe renders the video inline; timestamps open the video at that moment
- **LLM knowledge base users** — the fetch script is bundled with the skill, so no `tools/` setup is needed

## Credits

- Inspired by [Andrej Karpathy's LLM Knowledge Base workflow](https://x.com/karpathy/status/2039805659525644595)
- Part of the [LLM Knowledge Base Template](https://github.com/zerowing113/llm-knowledge-base-template)
