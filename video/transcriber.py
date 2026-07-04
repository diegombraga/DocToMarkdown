"""Transcript extraction — subtitle (fast, native) or Whisper (local, slower)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str


def parse_srt(srt_path: Path) -> list[TranscriptSegment]:
    """Parse an SRT file into timed segments."""
    content = srt_path.read_text(encoding="utf-8", errors="replace")
    # Blocks separated by blank lines
    blocks = re.split(r"\n\s*\n", content.strip())
    out: list[TranscriptSegment] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # First line may be an index or already the timing line
        first_is_timing = re.search(r"-->", lines[0]) is not None
        timing_line = lines[0] if first_is_timing else lines[1]
        m = re.search(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            timing_line,
        )
        if not m:
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        text_start = 1 if first_is_timing else 2
        text = " ".join(lines[text_start:]).strip()
        # Strip HTML-ish tags common in subtitles
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            out.append(TranscriptSegment(start_s=start, end_s=end, text=text))
    return out


def transcribe_whisper(
    audio_path: Path,
    model_name: str = "base",
    language: str | None = None,
    on_progress: Callable[[str, int, str], None] | None = None,
) -> list[TranscriptSegment]:
    """Transcribe audio locally with openai-whisper.

    Model is downloaded on first call (cached in ~/.cache/whisper/).
    """
    import whisper  # heavy import — lazy

    if on_progress:
        on_progress("transcrevendo", 0, f"Carregando modelo Whisper '{model_name}'…")
    model = whisper.load_model(model_name)
    if on_progress:
        on_progress("transcrevendo", 10, "Modelo pronto, transcrevendo áudio…")

    result = model.transcribe(
        str(audio_path),
        language=language,
        fp16=False,  # keep CPU-safe
        verbose=False,
    )

    if on_progress:
        on_progress("transcrevendo", 100, "Transcrição concluída")

    segs: list[TranscriptSegment] = []
    for s in result.get("segments") or []:
        segs.append(
            TranscriptSegment(
                start_s=float(s["start"]),
                end_s=float(s["end"]),
                text=str(s["text"]).strip(),
            )
        )
    return segs
