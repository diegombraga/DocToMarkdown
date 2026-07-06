"""DocToMarkdown MCP server.

Exposes the file-conversion and video-processing pipelines as MCP tools that
any Claude product (Claude Desktop, Claude Code, claude.ai with connectors)
can invoke natively via tool-use — no HTTP calls, no shell wrangling.

Long-running tools (convert_file, process_video) are async and report
progress via the MCP Context so clients can display a live progress bar
instead of a spinning cursor for minutes. Blocking calls (subprocess,
Whisper, yt-dlp, vision APIs) are dispatched via asyncio.to_thread so the
progress notifications actually reach the client.

Run standalone:
    python mcp_server.py

Or register with Claude Desktop by adding to the config file:

    {
      "mcpServers": {
        "doctomarkdown": {
          "command": "/full/path/to/.venv/bin/python",
          "args": ["/full/path/to/mcp_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

# Ensure the video/ subpackage (with env config) is loadable regardless of cwd
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from video import config as video_config  # noqa: E402 — path setup above
from video import downloader as vdl  # noqa: E402
from video import frames as vframes  # noqa: E402
from video import merger as vmerger  # noqa: E402
from video import transcriber as vtrans  # noqa: E402
from video import vision as vvision  # noqa: E402


mcp = FastMCP("doctomarkdown")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MARKITDOWN = shutil.which("markitdown")
OCRMYPDF = shutil.which("ocrmypdf")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Tools — file conversion
# ---------------------------------------------------------------------------


@mcp.tool()
async def convert_file(
    ctx: Context,
    path: Annotated[str, Field(description="Absolute path to the input file.")],
    use_ocr: Annotated[
        bool,
        Field(
            description=(
                "Run OCR when the input is a PDF. Applies ocrmypdf before "
                "markitdown. Ignored for non-PDF."
            )
        ),
    ] = False,
    force_ocr: Annotated[
        bool,
        Field(
            description=(
                "If use_ocr is True, re-run OCR even on pages that already have "
                "a text layer. Use when the embedded text is garbage."
            )
        ),
    ] = False,
    ocr_langs: Annotated[
        str,
        Field(
            description=(
                "Tesseract language codes joined with '+' (e.g. 'por+eng'). "
                "See https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html"
            )
        ),
    ] = "por+eng",
    save_alongside: Annotated[
        bool,
        Field(
            description=(
                "Write the Markdown to '<original_stem>.md' in the same directory "
                "as the input. Default True."
            )
        ),
    ] = True,
    return_content: Annotated[
        bool,
        Field(
            description=(
                "Return the full Markdown text in the response (costs conversation "
                "tokens). Set False to get only saved_to + a short preview — much "
                "cheaper for large outputs; the caller can read the .md file when "
                "and if needed."
            )
        ),
    ] = False,
) -> dict:
    """Convert any supported file to Markdown, saving it next to the source by default.

    Supports PDF (with optional OCR for scans), DOCX, PPTX, XLSX, images, HTML,
    EPUB, audio (via transcription), CSV/JSON/XML, notebooks, ZIP archives.

    Returns { saved_to, chars, did_ocr, preview } and — when return_content=True —
    a `markdown` field with the full text. Prefer return_content=False for big
    documents to keep the conversation context small; the saved .md file is
    always available on disk.
    """
    await ctx.report_progress(0, 100, "Iniciando conversão…")

    if MARKITDOWN is None:
        return {"error": "markitdown is not installed on PATH."}

    src = Path(path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        return {"error": f"file not found: {src}"}

    did_ocr = False
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        target = src

        if use_ocr and src.suffix.lower() == ".pdf":
            if OCRMYPDF is None:
                return {"error": "OCR requested but ocrmypdf is not installed."}
            await ctx.report_progress(10, 100, "Executando OCR…")
            ocred = tmpdir / f"ocr_{src.name}"
            cmd = [OCRMYPDF, "-l", ocr_langs, "--optimize", "1"]
            cmd.append("--force-ocr" if force_ocr else "--skip-text")
            cmd += [str(src), str(ocred)]
            proc = await asyncio.to_thread(_run, cmd)
            if proc.returncode != 0:
                return {"error": f"ocrmypdf failed: {proc.stderr or proc.stdout}"}
            target = ocred
            did_ocr = True
            await ctx.report_progress(70, 100, "OCR concluído")

        await ctx.report_progress(80, 100, "Convertendo para Markdown…")
        proc = await asyncio.to_thread(_run, [MARKITDOWN, str(target)])
        if proc.returncode != 0:
            return {"error": f"markitdown failed: {proc.stderr or proc.stdout}"}

    md = proc.stdout
    saved_to: str | None = None
    if save_alongside:
        await ctx.report_progress(95, 100, "Salvando arquivo…")
        try:
            out_path = src.with_suffix(".md")
            # Avoid overwriting a source file that happens to be .md itself
            if out_path.resolve() == src.resolve():
                out_path = src.parent / (src.stem + ".converted.md")
            out_path.write_text(md, encoding="utf-8")
            saved_to = str(out_path)
        except OSError as e:
            saved_to = None
            # Not fatal — still return the markdown to the caller.
            md = f"[warning: could not save alongside source: {e}]\n\n{md}"

    result: dict = {
        "saved_to": saved_to,
        "chars": len(md),
        "did_ocr": did_ocr,
        "preview": md[:500] + ("…" if len(md) > 500 else ""),
    }
    if return_content:
        result["markdown"] = md
    await ctx.report_progress(100, 100, "Concluído")
    return result


@mcp.tool()
def list_supported_formats() -> str:
    """Return the list of file formats supported by convert_file."""
    return (
        "PDF (with optional OCR for scans), DOCX, PPTX, XLSX, XLS, PNG, JPG, "
        "JPEG, GIF, BMP, HTML, HTM, EPUB, MP3, WAV, M4A, MP4 (audio only), "
        "ZIP (recurses), IPYNB, CSV, TSV, JSON, XML."
    )


# ---------------------------------------------------------------------------
# Tools — video pipeline
# ---------------------------------------------------------------------------


@mcp.tool()
def preview_video(
    url: Annotated[
        str,
        Field(description="Video URL. YouTube, Vimeo, and 1000+ other sites via yt-dlp."),
    ],
) -> dict:
    """Fetch video metadata without downloading. Returns title, uploader,
    duration, thumbnail, description, chapters, and available subtitle langs.
    Use before process_video to decide whether it's worth processing."""
    try:
        meta = vdl.probe(url)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    return meta.to_dict()


def _default_video_output_dir() -> Path:
    """Sensible default: ~/Documents/DocToMarkdown/ (created on first use)."""
    p = Path.home() / "Documents" / "DocToMarkdown"
    p.mkdir(parents=True, exist_ok=True)
    return p


@mcp.tool()
async def process_video(
    ctx: Context,
    url: Annotated[str, Field(description="Video URL.")],
    output_dir: Annotated[
        str,
        Field(
            description=(
                "Directory to save the .md (and MP4/MP3 if requested). "
                "Default: ~/Documents/DocToMarkdown/. Created if missing."
            )
        ),
    ] = "",
    save_video: Annotated[
        bool,
        Field(description="Also save the source MP4 in output_dir."),
    ] = False,
    save_audio: Annotated[
        bool,
        Field(description="Also save the extracted MP3 in output_dir."),
    ] = False,
    transcript_mode: Annotated[
        str,
        Field(
            description=(
                "'auto' (use subtitles if available, else Whisper), "
                "'subs' (subtitles only; fails if none), "
                "'whisper' (always transcribe locally)."
            )
        ),
    ] = "auto",
    whisper_model: Annotated[
        str,
        Field(description="Whisper model size: tiny|base|small|medium."),
    ] = "base",
    vision_provider: Annotated[
        str,
        Field(
            description=(
                "Vision LLM for scene descriptions: 'anthropic', 'openai', "
                "'gemini', or 'none'. Requires the matching API key configured."
            )
        ),
    ] = "none",
    return_content: Annotated[
        bool,
        Field(
            description=(
                "Return the full Markdown text in the response (costs conversation "
                "tokens). Set False (default) to get only saved_to + a short "
                "preview — the saved .md file has the full content."
            )
        ),
    ] = False,
) -> dict:
    """Process a video URL end-to-end and save the full-context Markdown.

    Downloads the audio (and video for frames), transcribes it, extracts key
    frames on scene cuts, OCRs each frame, optionally describes each scene
    with a vision LLM, and merges everything into a timeline Markdown file
    saved in output_dir. Long-running (30s–15min depending on length +
    Whisper model).

    Returns { saved_to, chars, transcript_source, vision_provider, artifacts,
    preview } — plus `markdown` when return_content=True.
    """
    await ctx.report_progress(0, 100, "Consultando informações do vídeo…")
    try:
        meta = await asyncio.to_thread(vdl.probe, url)
    except Exception as e:  # noqa: BLE001
        return {"error": f"could not probe URL: {e}"}

    if meta.duration_s > 4 * 3600:
        return {"error": f"video is {meta.duration_s // 60}min — exceeds 4h limit."}

    out_dir = Path(output_dir).expanduser() if output_dir else _default_video_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = (meta.id or "video") + (f"__{meta.uploader.replace(' ', '_')}" if meta.uploader else "")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        await ctx.report_progress(10, 100, "Baixando áudio…")
        try:
            audio = await asyncio.to_thread(vdl.download_audio, url, work)
        except Exception as e:  # noqa: BLE001
            return {"error": f"downloading audio failed: {e}"}

        await ctx.report_progress(20, 100, "Baixando vídeo…")
        try:
            video_path = await asyncio.to_thread(vdl.download_video, url, work)
        except Exception as e:  # noqa: BLE001
            return {"error": f"downloading video failed: {e}"}

        # Transcript
        await ctx.report_progress(35, 100, "Obtendo transcrição…")
        transcript: list[vtrans.TranscriptSegment] = []
        transcript_source = "whisper (local)"
        srt_path, is_manual = (None, False)
        if transcript_mode in ("auto", "subs"):
            srt_path, is_manual = await asyncio.to_thread(vdl.fetch_subtitles, url, work)

        if srt_path is not None and transcript_mode != "whisper":
            transcript = vtrans.parse_srt(srt_path)
            transcript_source = (
                "legenda manual" if is_manual else "legenda auto-gerada"
            )
        elif transcript_mode == "subs":
            return {"error": "transcript_mode='subs' but no subtitles available."}
        else:
            await ctx.report_progress(40, 100, f"Transcrevendo com Whisper ({whisper_model})…")
            try:
                transcript = await asyncio.to_thread(
                    vtrans.transcribe_whisper, audio, model_name=whisper_model
                )
                transcript_source = f"Whisper local ({whisper_model})"
            except Exception as e:  # noqa: BLE001
                return {"error": f"Whisper transcription failed: {e}"}

        # Frames + OCR
        await ctx.report_progress(65, 100, "Extraindo e lendo quadros-chave…")
        extracted = await asyncio.to_thread(vframes.extract_scenes, video_path, work / "frames")
        await asyncio.to_thread(vframes.ocr_frames, extracted)

        # Vision
        do_vision = vision_provider and vision_provider != "none"
        if do_vision:
            available = vvision.available_providers()
            if not available.get(vision_provider):
                return {
                    "error": (
                        f"vision_provider='{vision_provider}' but no API key "
                        f"configured. Use set_api_key first."
                    )
                }
            await ctx.report_progress(80, 100, f"Descrevendo cenas com {vision_provider}…")
            descs = await asyncio.to_thread(
                vvision.describe_frames_parallel,
                [f.image_path for f in extracted],
                provider=vision_provider,
                max_workers=5,
            )
            for f, d in zip(extracted, descs):
                if d:
                    f.vision_description = d

        await ctx.report_progress(90, 100, "Montando Markdown final…")
        md = vmerger.build_full(
            meta=meta,
            transcript=transcript,
            frames=extracted,
            transcript_source=transcript_source,
            vision_provider=vision_provider if do_vision else None,
            artifacts=None,
        )

        artifacts_saved: dict[str, str] = {}
        if save_video:
            dest = out_dir / f"{slug}.mp4"
            shutil.copy2(video_path, dest)
            artifacts_saved["video"] = str(dest)
        if save_audio:
            dest = out_dir / f"{slug}.mp3"
            shutil.copy2(audio, dest)
            artifacts_saved["audio"] = str(dest)

    md_path = out_dir / f"{slug}.md"
    md_path.write_text(md, encoding="utf-8")

    result: dict = {
        "saved_to": str(md_path),
        "chars": len(md),
        "transcript_source": transcript_source,
        "vision_provider": vision_provider if do_vision else None,
        "artifacts": artifacts_saved or None,
        "preview": md[:500] + ("…" if len(md) > 500 else ""),
    }
    if return_content:
        result["markdown"] = md
    await ctx.report_progress(100, 100, "Concluído")
    return result


# ---------------------------------------------------------------------------
# Tools — vision provider keys
# ---------------------------------------------------------------------------


@mcp.tool()
def get_provider_status() -> dict:
    """Return which vision LLM providers have an API key configured."""
    snap = video_config.snapshot()
    return {
        p: {
            "configured": info["configured"],
            "source": info["source"],  # "env" | "file" | "none"
        }
        for p, info in snap.items()
    }


@mcp.tool()
def set_api_key(
    provider: Annotated[
        str, Field(description="One of: 'anthropic', 'openai', 'gemini'.")
    ],
    key: Annotated[str, Field(description="The API key to save.")],
) -> str:
    """Save a vision LLM API key to ~/.config/DocToMarkdown/keys.json (chmod 0600).
    Does not overwrite keys set via the launch environment.
    """
    try:
        video_config.set_key(provider, key)
    except (ValueError, PermissionError) as e:
        return f"ERROR: {e}"
    return f"OK - {provider} key saved."


@mcp.tool()
def delete_api_key(
    provider: Annotated[
        str, Field(description="One of: 'anthropic', 'openai', 'gemini'.")
    ],
) -> str:
    """Remove a saved vision LLM API key."""
    try:
        video_config.delete_key(provider)
    except (ValueError, PermissionError) as e:
        return f"ERROR: {e}"
    return f"OK - {provider} key removed."


# ---------------------------------------------------------------------------


def _claude_desktop_config_path() -> Path:
    """Locate Claude Desktop's config file per OS."""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if sys.platform == "win32":
        import os as _os

        appdata = _os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    # Linux (Claude Desktop launched for Linux late 2025)
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _install_into_claude_desktop() -> int:
    """Register this MCP server with Claude Desktop's config.

    Idempotent — running twice leaves the same entry. Preserves any other
    server entries the user already has.
    """
    import json

    cfg_path = _claude_desktop_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(
                f"[!] Config file at {cfg_path} is malformed; refusing to overwrite.",
                file=sys.stderr,
            )
            return 1
    else:
        cfg = {}

    servers = cfg.setdefault("mcpServers", {})
    # Prefer the current interpreter (typically the venv's python) so the server
    # always has its dependencies available.
    servers["doctomarkdown"] = {
        "command": sys.executable,
        "args": [str(Path(__file__).resolve())],
    }
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    # Use ASCII-safe status glyphs — some Windows consoles are cp1252 and choke
    # on the U+2713 check mark, which would crash the installer even though
    # the config file has already been written above.
    print(f"[ok] Registered DocToMarkdown in {cfg_path}")
    print("     Restart Claude Desktop to pick up the change.")
    return 0


def _uninstall_from_claude_desktop() -> int:
    import json

    cfg_path = _claude_desktop_config_path()
    if not cfg_path.exists():
        print(f"[!] No config file at {cfg_path}", file=sys.stderr)
        return 0
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers") or {}
    if "doctomarkdown" not in servers:
        print("Nothing to remove.")
        return 0
    del servers["doctomarkdown"]
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"[ok] Removed DocToMarkdown from {cfg_path}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("--install-claude-desktop", "install"):
        raise SystemExit(_install_into_claude_desktop())
    if args and args[0] in ("--uninstall-claude-desktop", "uninstall"):
        raise SystemExit(_uninstall_from_claude_desktop())
    mcp.run()
