"""DocToMarkdown MCP server.

Exposes the file-conversion and video-processing pipelines as MCP tools that
any Claude product (Claude Desktop, Claude Code, claude.ai with connectors)
can invoke natively via tool-use — no HTTP calls, no shell wrangling.

Run standalone:
    python -m mcp_server        (if imported as package; not applicable here)
    python mcp_server.py        (direct)

Or register with Claude Desktop by adding to ~/Library/Application Support/
Claude/claude_desktop_config.json:

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

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
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
def convert_file(
    path: Annotated[str, Field(description="Absolute path to the input file.")],
    use_ocr: Annotated[
        bool,
        Field(
            description=(
                "Force OCR when the input is a PDF. Auto-detects scanned PDFs "
                "and applies ocrmypdf before markitdown. Ignored for non-PDF."
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
) -> str:
    """Convert any supported file to Markdown.

    Supports PDF (with automatic OCR for scanned PDFs), DOCX, PPTX, XLSX,
    images, HTML, EPUB, audio (via transcription), CSV/JSON/XML, notebooks,
    and archives. Returns clean Markdown suitable for LLM consumption.
    """
    if MARKITDOWN is None:
        return "ERROR: markitdown is not installed on PATH."

    src = Path(path).expanduser().resolve()
    if not src.exists() or not src.is_file():
        return f"ERROR: file not found: {src}"

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        target = src

        if use_ocr and src.suffix.lower() == ".pdf":
            if OCRMYPDF is None:
                return "ERROR: OCR requested but ocrmypdf is not installed."
            ocred = tmpdir / f"ocr_{src.name}"
            cmd = [OCRMYPDF, "-l", ocr_langs, "--optimize", "1"]
            cmd.append("--force-ocr" if force_ocr else "--skip-text")
            cmd += [str(src), str(ocred)]
            proc = _run(cmd)
            if proc.returncode != 0:
                return f"ERROR (ocrmypdf): {proc.stderr or proc.stdout}"
            target = ocred

        proc = _run([MARKITDOWN, str(target)])
        if proc.returncode != 0:
            return f"ERROR (markitdown): {proc.stderr or proc.stdout}"

    return proc.stdout


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


@mcp.tool()
def process_video(
    url: Annotated[str, Field(description="Video URL.")],
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
) -> str:
    """Process a video URL end-to-end and return the full-context Markdown.

    Downloads the audio (and video if needed for frames), transcribes it,
    extracts key frames on scene cuts, OCRs each frame, optionally describes
    each scene with a vision LLM, and merges everything into a timeline
    Markdown document. Long-running (30s-15min depending on length + Whisper).
    """
    try:
        meta = vdl.probe(url)
    except Exception as e:  # noqa: BLE001
        return f"ERROR probing URL: {e}"

    if meta.duration_s > 4 * 3600:
        return f"ERROR: video is {meta.duration_s // 60}min — exceeds 4h limit."

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        try:
            audio = vdl.download_audio(url, work)
        except Exception as e:  # noqa: BLE001
            return f"ERROR downloading audio: {e}"

        # We need video for frame extraction — always download.
        try:
            video_path = vdl.download_video(url, work)
        except Exception as e:  # noqa: BLE001
            return f"ERROR downloading video: {e}"

        # Transcript
        transcript: list[vtrans.TranscriptSegment] = []
        transcript_source = "whisper (local)"
        srt_path, is_manual = (None, False)
        if transcript_mode in ("auto", "subs"):
            srt_path, is_manual = vdl.fetch_subtitles(url, work)

        if srt_path is not None and transcript_mode != "whisper":
            transcript = vtrans.parse_srt(srt_path)
            transcript_source = (
                "legenda manual" if is_manual else "legenda auto-gerada"
            )
        elif transcript_mode == "subs":
            return "ERROR: transcript_mode='subs' but no subtitles available."
        else:
            try:
                transcript = vtrans.transcribe_whisper(
                    audio, model_name=whisper_model
                )
                transcript_source = f"Whisper local ({whisper_model})"
            except Exception as e:  # noqa: BLE001
                return f"ERROR transcribing with Whisper: {e}"

        # Frames + OCR
        extracted = vframes.extract_scenes(video_path, work / "frames")
        vframes.ocr_frames(extracted)

        # Vision
        do_vision = vision_provider and vision_provider != "none"
        if do_vision:
            available = vvision.available_providers()
            if not available.get(vision_provider):
                return (
                    f"ERROR: vision_provider='{vision_provider}' but no API key "
                    f"configured. Use set_api_key first."
                )
            descs = vvision.describe_frames_parallel(
                [f.image_path for f in extracted],
                provider=vision_provider,
                max_workers=5,
            )
            for f, d in zip(extracted, descs):
                if d:
                    f.vision_description = d

        return vmerger.build_full(
            meta=meta,
            transcript=transcript,
            frames=extracted,
            transcript_source=transcript_source,
            vision_provider=vision_provider if do_vision else None,
            artifacts=None,
        )


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
    return f"OK — {provider} key saved."


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
    return f"OK — {provider} key removed."


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
                f"! Config file at {cfg_path} is malformed; refusing to overwrite.",
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
    print(f"✓ Registered DocToMarkdown in {cfg_path}")
    print("  Restart Claude Desktop to pick up the change.")
    return 0


def _uninstall_from_claude_desktop() -> int:
    import json

    cfg_path = _claude_desktop_config_path()
    if not cfg_path.exists():
        print(f"! No config file at {cfg_path}", file=sys.stderr)
        return 0
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    servers = cfg.get("mcpServers") or {}
    if "doctomarkdown" not in servers:
        print("Nothing to remove.")
        return 0
    del servers["doctomarkdown"]
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"✓ Removed DocToMarkdown from {cfg_path}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in ("--install-claude-desktop", "install"):
        raise SystemExit(_install_into_claude_desktop())
    if args and args[0] in ("--uninstall-claude-desktop", "uninstall"):
        raise SystemExit(_uninstall_from_claude_desktop())
    mcp.run()
