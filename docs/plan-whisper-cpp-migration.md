# Plan: Use whisper.cpp for English, keep mlxscribe for other languages

Status: implemented (docs + `scripts/youtube_fetch.py`); evals harness pending
Owner: (you)
Scope: `scripts/youtube_fetch.py`, `SKILL.md`, `README.md`, `evals/`

## 1. Motivation

Benchmark on the three eval videos (WER vs the YouTube caption transcripts
already stored in `evals/runs/`, punctuation/case stripped, number words
normalized; peak RSS via `/usr/bin/time -l`):

| Video | Dur | whisper small WER | mlxscribe WER | whisper wall / RT / RSS | mlxscribe wall / RT / RSS |
|---|---:|---:|---:|---:|---:|
| 3B1B LLMs | 7:58 | **0.8%** | 5.5% | 31.5s / 15.1x / 0.96 GiB | 61.6s / 7.8x / 3.81 GiB |
| Sinek TED | 18:35 | **7.0%** | 11.5% | 60.5s / 18.4x / 0.98 GiB | 149.3s / 7.5x / 4.21 GiB |
| 3B1B NN | 18:40 | **1.2%** | 3.3% | 58.9s / 19.0x / 0.98 GiB | 141.0s / 7.9x / 4.24 GiB |

- whisper.cpp small: lower WER, ~2.3x faster, ~4x less memory, cross-platform.
- whisper.cpp can only translate **to English**; mlxscribe can translate to any
  language. Hence the split: English targets use whisper.cpp, other languages
  keep mlxscribe.
- whisper.cpp failure mode found: on the Sinek Cisco outro it **repetition-looped**
  (`"the human network cisco"` x8), adding 50 tokens / 50 errors (7.0% → 5.3% when
  trimmed). Mitigation required (VAD + repetition filter).

## 2. Goals / non-goals

Goals
- `--transcribe` with no target, or `--transcribe-lang english`, uses whisper.cpp.
- Any other `--transcribe-lang <lang>` keeps using mlxscribe (translation).
- Non-English audio with **no** `--transcribe-lang` → whisper.cpp (original-language ASR).
- Reuse the existing SRT parser (`parse_srt_to_segments`) and output format for both.
- Keep documented overrides for both backends.

Non-goals
- Making whisper.cpp translate to arbitrary languages (it can't).
- Streaming / diarization / word-level timestamps.
- Changing the caption path or output layout.

## 3. Router design (D1 — resolved)

Route by the requested target language:

```python
def choose_backend(transcribe_lang: str | None) -> str:
    lang = (transcribe_lang or "").strip().lower()
    if lang in ("", "english", "en"):
        return "whisper"        # ASR (english -> whisper -tr)
    return "mlxscribe"          # translate to any other language
```

| `--transcribe-lang` | Backend | Behavior |
|---|---|---|
| (none) | whisper.cpp | ASR in the audio's own language (`-l auto`) |
| `english` / `en` | whisper.cpp | ASR + `-tr` (translate to English) |
| any other (`french`, `japanese`, …) | mlxscribe | `--translate-to <lang>` |

## 4. Code changes — `scripts/youtube_fetch.py`

### 4.1 Header / docstring
- Update lines 38–40 flag help and the module docstring to describe the split.
- Rename section comment at line 349 to `# Audio transcription (whisper.cpp / mlxscribe fallback)`.

### 4.2 New helpers (near `require_ffmpeg`, ~line 352)
```python
WHISPER_MODEL_DEFAULT = "small"
WHISPER_MODEL_URL = ("https://huggingface.co/ggerganov/whisper.cpp/"
                     "resolve/main/ggml-{name}.bin")

def resolve_whisper_bin() -> str | None:      # whisper-cli / whisper-cpp / main
def ensure_whisper_model() -> Path | None:    # cache dir -> Homebrew share -> download
def require_whisper() -> tuple[str, Path] | None:
def choose_backend(transcribe_lang) -> str:   # see §3
```

### 4.3 Split `transcribe_audio()` (~lines 383–441)
Make it a thin router; move current logic into two functions. Keep the shared
yt-dlp download in a helper (`download_audio(url, tmpdir) -> Path`).

```python
def transcribe_audio(url, transcribe_lang=None) -> list:
    if choose_backend(transcribe_lang) == "whisper":
        return transcribe_with_whisper(url, translate=bool(transcribe_lang))
    return transcribe_with_mlxscribe(url, transcribe_lang)
```

`transcribe_with_whisper()`:
1. `download_audio()` → webm/m4a.
2. Convert: `ffmpeg -y -i audio.* -ar 16000 -ac 1 -c:a pcm_s16le audio.wav`
   (whisper-cli does **not** accept webm/m4a).
3. Build command:
   ```python
   cmd = [whisper_bin, "-m", str(model), "-f", wav,
          "-osrt", "-of", str(Path(tmpdir) / "audio"),
          "-t", str(os.cpu_count() or 4), "-l", "auto", "-np"]
   if vad_model: cmd += ["--vad", "-vm", str(vad_model)]
   if translate: cmd += ["-tr"]
   ```
   If `WHISPER_CLI_CMD` is set, shlex-split it and append the trailing args.
4. Read `audio.srt` → `parse_srt_to_segments()` → `strip_repetition_loops()`.

`transcribe_with_mlxscribe()`: the existing body (MLXSCRIBE_CMD default,
`--no-mux --format srt --translate-to <lang>`, translated-track preference).

### 4.4 Dependency checks
- Keep `require_ffmpeg()` (now also used for WAV conversion).
- `require_whisper()` only for the whisper path; `mlxscribe` only for the
  other-language path. Print `brew install whisper.cpp` / model URL guidance,
  or the existing uvx message, as appropriate.

### 4.5 CLI parsing (~lines 656–706)
- `--transcribe-lang LANG` keeps its name and translation meaning; only the
  backend for `english` changes. Update the help string.
- Model selection is via the `WHISPER_MODEL` env var only (no new CLI flag).
- No signature changes needed: `transcribe_audio(url, transcribe_lang)` keeps
  its shape, so the two call sites are unchanged.

## 5. Hallucination mitigation (whisper path only)

- VAD: download `ggml-silero-v5.1.2.bin` alongside the ASR model; pass
  `--vad -vm <silero>` to trim silence/music where loops start.
- `strip_repetition_loops(segments)`: collapse consecutive segments where an
  n-gram (n>=3) repeats more than K times in a window. Unit-test against the
  Sinek SRT (the `human network` x8 run should collapse to 1).
- If loops persist, try `-mc 0` and verify no WER regression before adopting.

## 6. Docs changes

- **SKILL.md**: lines 58–83 (fallback section: English → whisper.cpp, other
  languages → mlxscribe; model download; flags; both overrides), 165–172,
  281–283 (security: audio converted locally; source unmodified).
- **README.md**: line 11 (feature bullet), 43–49 and 63 (examples),
  78–80 (No-captions tip), 139–142 (requirements: whisper.cpp via brew for
  English; mlxscribe/Apple Silicon/Python 3.12 only for other languages),
  150–152 (security), 159 (tips).
- **Script docstring**: lines 38–40.

## 7. Config / env summary

| Var | Default | Purpose |
|---|---|---|
| `WHISPER_CLI_CMD` | auto-resolved binary | Full whisper command override |
| `WHISPER_MODEL` | `small` | Model name or absolute path |
| `WHISPER_VAD_MODEL` | auto-downloaded silero | VAD model path; empty disables VAD |
| `MLXSCRIBE_CMD` | uvx default | mlxscribe override (non-English targets) |

## 8. Testing / evals

- **Routing:** assert `choose_backend` for `None`/`english`/`en` → whisper and
  `french`/`japanese` → mlxscribe.
- **English regression:** re-run eval-3 (Big Buck Bunny, no speech) — assert
  empty/near-empty output (no music hallucination), article stays metadata-only.
- **Repetition:** Sinek video; assert the Cisco loop collapses and WER ≈ 5.3%.
- **Other language:** one non-English video with `--transcribe-lang <code>`;
  assert it hits mlxscribe and produces the translated track.
- **Model-missing path:** point `WHISPER_MODEL` at a bogus path; assert a clear
  error and `[]` (metadata-only article).
- **Add an ASR eval harness** under `evals/` reusing the benchmark's `eval.py`
  (WER vs `evals/runs/*/raw/*.md`); document in README "Testing / Evals".

## 9. Risks

| Risk | Mitigation |
|---|---|
| Model file absent | Auto-download (cache dir) + clear brew/manual message |
| webm/m4a input | ffmpeg → 16 kHz mono WAV step |
| Repetition/hallucination loops | VAD + repetition filter (§5) |
| Two backends to maintain | Small shared helpers; SRT parser reused by both |
| whisper.cpp flag/version drift | Resolve binary by name; pin documented flags; test on brew 1.9.5 |
| New network calls (model download) | Only HF + YouTube; document in SKILL security section |

## 10. Rollout checklist

1. Add helpers (`resolve_whisper_bin`, `ensure_whisper_model`, `choose_backend`)
   and split `transcribe_audio()` into router + two backends.        [done]
2. Add VAD download + `strip_repetition_loops()`.                    [done]
3. Wire `--transcribe-lang` routing (model via `WHISPER_MODEL` env). [done]
4. Update SKILL.md + README.md + docstring.                          [done]
5. Run §8 tests; confirm Sinek WER ≈ 5.3%, Big Buck Bunny empty.     [verify]
6. Add the ASR eval harness; update `evals/evals.json` if any
   expectation text mentions the fallback backend or translation.    [evals]
7. (Optional) Commit with a migration note.
