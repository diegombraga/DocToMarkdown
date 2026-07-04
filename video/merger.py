"""Merge metadata + transcript + frame OCR + vision descriptions into Markdown."""

from __future__ import annotations

from typing import Iterable

from .downloader import VideoMeta
from .frames import Frame
from .transcriber import TranscriptSegment


def _fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_date(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    return yyyymmdd


def _bucket_events_by_second(
    transcript: list[TranscriptSegment],
    frames: list[Frame],
) -> list[tuple[float, list[str]]]:
    """Interleave transcript segments and frames in chronological order.

    Each output tuple is (timestamp_s, [event_line, ...]) where event lines are
    prefixed with icons: 🎤 (transcript), 🖥 (screen text via OCR), 👁 (vision).
    """
    events: list[tuple[float, str]] = []

    for seg in transcript:
        text = seg.text.strip()
        if text:
            events.append((seg.start_s, f"🎤 {text}"))

    for f in frames:
        if f.ocr_text:
            snippet = " · ".join(f.ocr_text.splitlines()[:4]).strip()
            if snippet:
                events.append((f.timestamp_s, f"🖥 Tela: {snippet}"))
        if f.vision_description:
            events.append((f.timestamp_s, f"👁 {f.vision_description}"))

    events.sort(key=lambda e: e[0])

    # Group by same-second buckets so simultaneous events share a timestamp
    grouped: dict[int, list[str]] = {}
    order: list[int] = []
    for ts, line in events:
        key = int(ts)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(line)
    return [(float(k), grouped[k]) for k in order]


def build_transcript_only(
    meta: VideoMeta, transcript: list[TranscriptSegment]
) -> str:
    """Simple version — just metadata + transcript. No visual layer."""
    lines: list[str] = []
    lines.append(f"# {meta.title}")
    lines.append("")
    lines.append(
        f"**Canal:** {meta.uploader}  ·  **Duração:** {_fmt_ts(meta.duration_s)}  "
        f"·  **Publicado em:** {_fmt_date(meta.upload_date)}  ·  **URL:** {meta.url}"
    )
    lines.append("")
    lines.append("## Transcrição")
    lines.append("")
    for seg in transcript:
        lines.append(f"[{_fmt_ts(seg.start_s)}] {seg.text}")
    return "\n".join(lines)


def build_full(
    meta: VideoMeta,
    transcript: list[TranscriptSegment],
    frames: list[Frame],
    transcript_source: str,
    vision_provider: str | None,
    artifacts: dict[str, str] | None = None,
) -> str:
    """Full Markdown: metadata + chapters + timeline + optional artifacts list."""
    lines: list[str] = []
    lines.append(f"# {meta.title}")
    lines.append("")
    lines.append(
        f"**Canal:** {meta.uploader}  ·  **Duração:** {_fmt_ts(meta.duration_s)}  "
        f"·  **Publicado em:** {_fmt_date(meta.upload_date)}  ·  **URL:** {meta.url}"
    )
    lines.append("")

    # Legend
    legend_bits = ["🎤 fala", "🖥 texto na tela (OCR)"]
    if vision_provider:
        legend_bits.append(f"👁 análise visual ({vision_provider})")
    lines.append(
        f"_Legenda: {' · '.join(legend_bits)}. Transcrição via **{transcript_source}**._"
    )
    lines.append("")

    if meta.description:
        lines.append("## Descrição")
        lines.append("")
        lines.append(meta.description.strip())
        lines.append("")

    if meta.chapters:
        lines.append("## Chapters")
        lines.append("")
        for c in meta.chapters:
            start = float(c.get("start_time") or 0)
            title = c.get("title") or ""
            lines.append(f"- {_fmt_ts(start)} — {title}")
        lines.append("")

    lines.append("## Timeline")
    lines.append("")
    buckets = _bucket_events_by_second(transcript, frames)
    if not buckets:
        lines.append("_Nenhum evento capturado._")
    for ts, event_lines in buckets:
        lines.append(f"### [{_fmt_ts(ts)}]")
        for ln in event_lines:
            lines.append(f"- {ln}")
        lines.append("")

    if artifacts:
        lines.append("## Anexos")
        lines.append("")
        for name, url in artifacts.items():
            lines.append(f"- [{name}]({url})")
        lines.append("")

    return "\n".join(lines)


def coalesce_lines(lines: Iterable[str]) -> str:
    """Utility — collapse triple blank lines."""
    out: list[str] = []
    blanks = 0
    for ln in lines:
        if not ln.strip():
            blanks += 1
            if blanks < 3:
                out.append(ln)
        else:
            blanks = 0
            out.append(ln)
    return "\n".join(out)
