# DocToMarkdown

**Any file → Markdown, locally and free.** A desktop app + Claude Code skill that wraps Microsoft's [markitdown](https://github.com/microsoft/markitdown) and [ocrmypdf](https://github.com/ocrmypdf/OCRmyPDF) into a single friction-free experience.

![License](https://img.shields.io/badge/license-MIT-blue) ![macOS](https://img.shields.io/badge/macOS-supported-success) ![Linux](https://img.shields.io/badge/Linux-supported-success) ![Windows](https://img.shields.io/badge/Windows-supported-success) ![Docker](https://img.shields.io/badge/Docker-ready-success)

Drop a PDF (even a scanned one), a DOCX, a spreadsheet, an image, an EPUB, or an audio file — get clean Markdown ready to paste into an LLM, a notes app, or a wiki. Or paste a **YouTube / Vimeo URL** and get a **full-context Markdown**: transcript (via subtitle or local Whisper) + text visible on screen (Tesseract OCR of key frames) + optional AI visual description of every scene. Everything runs on your machine. No API keys required (except for optional visual description). No uploads. No subscriptions.

## Why

LLMs and note-taking systems love Markdown, and yet most documents in real life aren't Markdown — they're PDFs, Word files, decks, spreadsheets, scans, screenshots. Existing conversion tools are either paid SaaS (you upload sensitive material to a random server) or a stack of CLI tools you glue together yourself. This project bundles the best free tools into one experience:

- **markitdown** handles the structured-file conversion (PDF/DOCX/PPTX/XLSX/images/HTML/EPUB/audio)
- **ocrmypdf** adds a text layer to scanned PDFs so they're actually readable
- **tesseract** provides the OCR engine (100+ languages)
- **A small Flask web UI** ties it together with drag-and-drop
- **A Claude Code skill** lets you say "convert this PDF" in natural language

Everything is local, everything is free, everything is MIT-licensed.

## Features

- **Drag-and-drop web UI** with live preview, copy, and `.md` download
- **Automatic OCR** for scanned PDFs (opt-in per file)
- **Multi-language OCR**: Portuguese, English, Spanish, French, Italian, German out of the box — 100+ available
- **Video → full-context Markdown**: paste any YouTube/Vimeo/etc. URL, get transcript + on-screen text + (optional) scene-by-scene visual description
- **Local Whisper transcription** when native subtitles aren't available
- **BYOK vision LLM**: optionally plug your Anthropic / OpenAI / Gemini key for scene descriptions
- **Supports**: PDF, DOCX, PPTX, XLSX, PNG/JPG, HTML, EPUB, MP3/WAV/M4A (transcription), and video URLs from 1000+ sites
- **Cross-platform**: macOS `.app`, Linux `.desktop`, Windows `.lnk`, or Docker
- **Claude Code skill** so any Claude Code user can invoke the pipeline via natural language
- **100% local** — no file leaves your machine (except optional vision LLM calls with your own key)

## Install

### macOS / Linux (one-liner)
```bash
curl -fsSL https://raw.githubusercontent.com/diegombraga/DocToMarkdown/main/install.sh | bash
```
The installer detects your platform (macOS: Homebrew; Linux: apt/dnf/pacman) and installs everything it needs, then builds a native desktop app.

### Windows (one-liner)
```powershell
iwr -useb https://raw.githubusercontent.com/diegombraga/DocToMarkdown/main/install.ps1 | iex
```
Requires Windows 10 21H1+ (for winget). Installs Python 3.12, Tesseract, OCRmyPDF, FFmpeg, then places a shortcut in the Start Menu.

### Docker (any OS)
```bash
docker run --rm -p 5555:5555 ghcr.io/diegombraga/doctomarkdown
```
Then open <http://127.0.0.1:5555>. No local install required.

Or with docker-compose (mounts your `~/Documents` for easier upload):
```bash
docker compose up
```

## Usage

### 1. Desktop app — file mode
Double-click **DocToMarkdown** (macOS `/Applications`, Linux app menu, Windows Start Menu). Your browser opens at `http://127.0.0.1:5555`. On the **📄 Arquivo** tab: drag a file, pick OCR languages if it's a scanned PDF, click **Converter**, copy or download the Markdown.

### 1b. Desktop app — video mode
Switch to the **🎬 Vídeo / URL** tab. Paste a URL, click **Prever** to load the metadata card, choose options (download MP4, download MP3, subtitle vs. Whisper, vision provider), then **Processar**. A live progress bar shows each stage: download → transcript → frame extraction → OCR → optional vision → merge. Result includes the full Markdown plus downloadable artifacts.

**Optional AI vision** (BYOK — bring your own key): set one of these env vars before launching to enable scene-by-scene visual descriptions.

| Provider | Env var | Default model | Approx. cost per 30-min video |
|---|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` | ~$0.30-0.50 |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | ~$0.05-0.10 |
| Google Gemini | `GEMINI_API_KEY` | `gemini-2.0-flash` | ~$0.02-0.05 |

Costs are estimates for typical videos with ~50 scene keyframes. Model overrides via `DTM_ANTHROPIC_MODEL` / `DTM_OPENAI_MODEL` / `DTM_GEMINI_MODEL`.

### 2. Claude Code skill
Install the skill (bundled with the installers, or manually):
```bash
git clone https://github.com/diegombraga/DocToMarkdown
mkdir -p ~/.claude/skills
cp -R DocToMarkdown/skill ~/.claude/skills/DocToMarkdown
```
Then in Claude Code, say:
> Converta `~/Downloads/contrato-escaneado.pdf` pra markdown

Claude detects the file is a scanned PDF, runs OCR (Portuguese + English by default), then converts it to `.md` alongside the original.

### 3. CLI
The installers also add a `doc2md` command on your PATH:
```bash
doc2md contract.pdf              # basic conversion
doc2md --ocr scan.pdf            # force OCR
doc2md --ocr --lang por+eng scan.pdf
```

## How it works

**File pipeline:**
```
   Any file → type detection → [if scanned PDF: ocrmypdf/Tesseract] → markitdown → Markdown
```

**Video pipeline:**
```
   Video URL → yt-dlp (metadata, MP4, MP3, subs)
             → [subs? use them : Whisper local]  ─┐
             → PySceneDetect (scene cuts)         │
             → Tesseract OCR on keyframes         ├─→ merged Markdown
             → [vision LLM? describe each scene]  │       with timeline
                                                   ─┘   (🎤 fala + 🖥 tela + 👁 cena)
```

## Supported formats

Via markitdown:
- Office: `.docx`, `.pptx`, `.xlsx`, `.xls`
- PDF: text-layer PDFs (add `--ocr` for scans)
- Images: `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp` (via magika/OCR)
- Web: `.html`, `.htm`, YouTube URLs
- Books: `.epub`
- Audio: `.mp3`, `.wav`, `.m4a`, `.mp4` (transcription via SpeechRecognition + ffmpeg)
- Archives: `.zip` (recurses)
- Notebooks: `.ipynb`
- CSV/TSV, JSON, XML

## Configuration

`PORT` environment variable overrides the default port (5555):
```bash
PORT=8080 doc2md-server
```

## Uninstall

```bash
# macOS + Linux
curl -fsSL https://raw.githubusercontent.com/diegombraga/DocToMarkdown/main/uninstall.sh | bash
# Windows
iwr -useb https://raw.githubusercontent.com/diegombraga/DocToMarkdown/main/uninstall.ps1 | iex
```
Removes the app + venv + skill. Does not uninstall Homebrew/apt/winget-managed dependencies (markitdown, ocrmypdf, tesseract, ffmpeg) — remove those manually if you want to.

## Roadmap

- [ ] Batch processing (multiple files or a folder)
- [ ] Signed & notarized macOS `.app` (Gatekeeper-friendly)
- [ ] Signed Windows installer (SmartScreen-friendly)
- [ ] Homebrew tap (`brew install diegombraga/tap/doctomarkdown`)
- [ ] Arch AUR + Debian `.deb` packaging
- [ ] Auto-detect scan vs text-layer PDF and OCR without asking

## Credits

Standing on the shoulders of:
- [microsoft/markitdown](https://github.com/microsoft/markitdown) — MIT
- [ocrmypdf/OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) — MPL-2.0
- [tesseract-ocr/tesseract](https://github.com/tesseract-ocr/tesseract) — Apache 2.0
- [pallets/flask](https://github.com/pallets/flask) — BSD-3-Clause

## License

MIT © 2026 Diego Braga
