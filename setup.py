#!/usr/bin/env python3
"""
setup.py — Install the YouTube Claude Code skill.

What it does:
  1. Copies SKILL.md to ~/.claude/skills/youtube/
  2. Installs Python dependencies (youtube-transcript-api, yt-dlp)
  3. Prints usage instructions

Run once after cloning:
    python setup.py
"""

import sys
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent
SKILL_SRC = REPO_ROOT / "SKILL.md"
SKILL_DEST = Path.home() / ".claude" / "skills" / "youtube" / "SKILL.md"

G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"; B = "\033[1m"; E = "\033[0m"

def ok(m):     print(f"  {G}✓{E} {m}")
def warn(m):   print(f"  {Y}!{E} {m}")
def header(m): print(f"\n{B}{m}{E}")


def install_skill():
    header("Installing Claude Code skill")
    SKILL_DEST.parent.mkdir(parents=True, exist_ok=True)

    if SKILL_DEST.exists():
        warn(f"Already installed at {SKILL_DEST}")
        r = input("    Overwrite? [y/N] ").strip().lower()
        if r != "y":
            ok("Skipped — existing skill kept")
            return

    shutil.copy2(SKILL_SRC, SKILL_DEST)
    ok(f"Skill installed → {SKILL_DEST}")


def install_deps():
    header("Installing Python dependencies")
    for pkg in ["youtube-transcript-api", "yt-dlp"]:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            ok(pkg)
        else:
            warn(f"{pkg} — failed. Install manually: pip install {pkg}")


def print_usage():
    header("Setup complete!")
    print(f"""
  Place youtube_fetch.py in your project directory (or tools/ subfolder),
  then open that project in Claude Code and run:

    /youtube https://youtube.com/watch?v=...          # single video
    /youtube https://youtube.com/playlist?list=...    # full playlist/course
    /youtube <playlist_url> --limit 10                # first 10 videos only

  Claude will fetch the transcript, embed the video, and compile a
  detailed reference article with clickable timestamps.

  Source: https://github.com/zerowing113/claude-youtube-skill
""")


if __name__ == "__main__":
    print(f"\n{B}Claude YouTube Skill — Setup{E}")
    print("=" * 40)
    install_skill()
    install_deps()
    print_usage()
