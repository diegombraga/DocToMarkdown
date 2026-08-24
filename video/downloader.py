"""yt-dlp wrapper — metadata probe, MP3 audio, MP4 video, subtitle extraction."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yt_dlp


@dataclass
class VideoMeta:
    url: str
    title: str
    uploader: str
    duration_s: int
    upload_date: str
    thumbnail_url: str
    description: str
    chapters: list[dict[str, Any]]
    has_manual_subs: bool
    has_auto_subs: bool
    available_sub_langs: list[str] = field(default_factory=list)
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "uploader": self.uploader,
            "duration_s": self.duration_s,
            "upload_date": self.upload_date,
            "thumbnail_url": self.thumbnail_url,
            "description": self.description,
            "chapters": self.chapters,
            "has_manual_subs": self.has_manual_subs,
            "has_auto_subs": self.has_auto_subs,
            "available_sub_langs": self.available_sub_langs,
            "id": self.id,
        }


class StaleExtractorError(RuntimeError):
    """yt-dlp is too old for the site's current delivery scheme.

    YouTube rotates the signature/throttling scheme every few weeks; an
    aging yt-dlp keeps resolving metadata fine and then gets 403 on the
    media URLs. The raw "HTTP Error 403: Forbidden" tells the user
    nothing, so we translate it into the actual fix.
    """


_STALE_MARKERS = (
    "http error 403",
    "unable to download video data",
    "unable to download webpage: http error 403",
    "sign in to confirm you're not a bot",
    "requested format is not available",
    "nsig extraction failed",
    "unable to extract",
)


def explain_download_error(err: Exception) -> str:
    """Turn a yt-dlp failure into something the user can act on."""
    raw = str(err)
    low = raw.lower()
    if any(m in low for m in _STALE_MARKERS):
        try:
            current = yt_dlp.version.__version__
        except Exception:  # noqa: BLE001
            current = "desconhecida"
        return (
            "O YouTube recusou o download (HTTP 403). Isso quase sempre significa "
            f"que o yt-dlp está desatualizado — a versão instalada é {current}, e o "
            "YouTube muda o esquema de entrega a cada poucas semanas.\n\n"
            "Atualize em ⚙ Configurações › 'Atualizar yt-dlp', ou pelo terminal:\n"
            '  "$HOME/Library/Application Support/DocToMarkdown/.venv/bin/pip" '
            "install --upgrade yt-dlp\n\n"
            "Se já estiver na versão mais recente, o vídeo pode exigir login "
            "(privado, restrito por idade ou regional).\n\n"
            f"Erro original: {raw[:300]}"
        )
    return raw


def probe(url: str) -> VideoMeta:
    """Fast metadata probe — does not download the media."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    subtitles = info.get("subtitles") or {}
    auto_subs = info.get("automatic_captions") or {}
    return VideoMeta(
        url=url,
        title=info.get("title") or "(sem título)",
        uploader=info.get("uploader") or info.get("channel") or "",
        duration_s=int(info.get("duration") or 0),
        upload_date=info.get("upload_date") or "",
        thumbnail_url=info.get("thumbnail") or "",
        description=info.get("description") or "",
        chapters=list(info.get("chapters") or []),
        has_manual_subs=bool(subtitles),
        has_auto_subs=bool(auto_subs),
        available_sub_langs=sorted(set(list(subtitles.keys()) + list(auto_subs.keys()))),
        id=info.get("id") or "",
    )


def _progress_bridge(
    on_progress: Callable[[str, int, str], None] | None, stage: str
) -> Callable[[dict[str, Any]], None]:
    def hook(d: dict[str, Any]) -> None:
        if on_progress is None:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = int((done / total) * 100) if total else 0
            mb_done = done / 1_000_000
            mb_total = total / 1_000_000 if total else 0
            msg = (
                f"Baixando {stage} ({mb_done:.1f}"
                + (f"/{mb_total:.1f}" if mb_total else "")
                + " MB)"
            )
            on_progress(stage, pct, msg)
        elif d.get("status") == "finished":
            on_progress(stage, 100, f"{stage} baixado")

    return hook


def download_audio(
    url: str,
    out_dir: Path,
    on_progress: Callable[[str, int, str], None] | None = None,
) -> Path:
    """Extract best-quality audio as MP3 (128k)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "audio.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
        "progress_hooks": [_progress_bridge(on_progress, "áudio")],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    result = out_dir / "audio.mp3"
    if not result.exists():
        raise FileNotFoundError(f"Áudio não foi baixado: {result}")
    return result


def download_video(
    url: str,
    out_dir: Path,
    on_progress: Callable[[str, int, str], None] | None = None,
    max_height: int | None = None,
) -> Path:
    """Download video+audio muxed to MP4.

    `max_height` caps the vertical resolution. Pass it when the file is
    only an intermediate for frame extraction: uncapped, `bestvideo`
    happily picks a 4K AV1 stream — over a gigabyte for a 40-minute talk
    — when 720p is already more than enough to OCR on-screen text and
    describe scenes. Leave it None when the user asked to keep the MP4.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(out_dir / "video.%(ext)s")
    if max_height:
        h = int(max_height)
        fmt = (
            f"bestvideo[ext=mp4][height<={h}]+bestaudio[ext=m4a]/"
            f"best[ext=mp4][height<={h}]/best[height<={h}]/best"
        )
    else:
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    opts = {
        "format": fmt,
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_progress_bridge(on_progress, "vídeo")],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    # yt-dlp may produce video.mp4 or video.mkv; find whatever came out
    for ext in ("mp4", "mkv", "webm"):
        p = out_dir / f"video.{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(f"Vídeo não encontrado após download em {out_dir}")


def fetch_subtitles(
    url: str,
    out_dir: Path,
    prefer_langs: list[str] | None = None,
) -> tuple[Path | None, bool]:
    """Fetch the best available subtitle track as SRT.

    Returns (path_to_srt, is_manual). None if none available.
    Prefers manual over auto-generated. Prefers the first matching language
    from `prefer_langs` if provided (default: pt, pt-BR, en, en-US).
    """
    prefer_langs = prefer_langs or ["pt", "pt-BR", "en", "en-US"]
    out_dir.mkdir(parents=True, exist_ok=True)

    for want_manual in (True, False):
        opts = {
            "writesubtitles": want_manual,
            "writeautomaticsub": not want_manual,
            "subtitleslangs": prefer_langs,
            "subtitlesformat": "srt/best",
            "skip_download": True,
            "outtmpl": str(out_dir / "sub.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                ydl.download([url])
            except yt_dlp.utils.DownloadError:
                continue
        for f in out_dir.glob("sub.*.srt"):
            return (f, want_manual)
        for f in out_dir.glob("sub.*.vtt"):
            # convert VTT to SRT via ffmpeg for uniform downstream parsing
            srt = f.with_suffix(".srt")
            _convert_vtt_to_srt(f, srt)
            return (srt, want_manual)
    return (None, False)


def _convert_vtt_to_srt(vtt: Path, srt: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg não encontrado no PATH")
    subprocess.run(
        [ffmpeg, "-y", "-i", str(vtt), str(srt)],
        capture_output=True,
        check=True,
    )
