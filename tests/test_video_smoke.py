"""Smoke tests for the video pipeline — validate parsing/merging without
touching the network. Real end-to-end integration is exercised locally.
"""

from __future__ import annotations

from pathlib import Path

from video import merger
from video.downloader import VideoMeta
from video.frames import Frame
from video.transcriber import TranscriptSegment, parse_srt


SAMPLE_SRT = """1
00:00:01,200 --> 00:00:03,360
Bem-vindos ao vídeo

2
00:00:05,318 --> 00:00:07,974
essa é a segunda linha
com duas linhas

3
00:00:16,881 --> 00:00:18,881
e final
"""


def test_parse_srt_basic(tmp_path: Path) -> None:
    srt = tmp_path / "sub.srt"
    srt.write_text(SAMPLE_SRT, encoding="utf-8")
    segs = parse_srt(srt)
    assert len(segs) == 3
    assert segs[0].start_s == 1.2
    assert segs[0].end_s == 3.36
    assert segs[0].text == "Bem-vindos ao vídeo"
    assert segs[1].text.startswith("essa é a segunda linha")
    assert segs[2].start_s == 16.881


def test_parse_srt_without_index(tmp_path: Path) -> None:
    """Some SRT sources omit the numeric index — we should still parse."""
    import re as _re
    src = "\n".join(
        ln for ln in SAMPLE_SRT.splitlines() if not _re.match(r"^\d+$", ln)
    )
    srt = tmp_path / "no_index.srt"
    srt.write_text(src, encoding="utf-8")
    segs = parse_srt(srt)
    assert len(segs) == 3
    assert segs[0].text == "Bem-vindos ao vídeo"


def test_merger_full_shape() -> None:
    meta = VideoMeta(
        url="https://example.com/watch?v=abc",
        title="Vídeo Teste",
        uploader="Fulano",
        duration_s=125,
        upload_date="20250701",
        thumbnail_url="",
        description="Uma descrição.",
        chapters=[{"start_time": 0.0, "title": "Introdução"}],
        has_manual_subs=True,
        has_auto_subs=False,
        available_sub_langs=["pt"],
        id="abc",
    )
    transcript = [
        TranscriptSegment(0.5, 3.0, "Olá pessoal"),
        TranscriptSegment(3.0, 6.0, "Hoje vamos ver..."),
    ]
    frames = [
        Frame(timestamp_s=1.0, image_path=Path("/dev/null"), ocr_text="TÍTULO SLIDE"),
        Frame(
            timestamp_s=4.5,
            image_path=Path("/dev/null"),
            ocr_text="",
            vision_description="Palestrante em pé, telão azul atrás.",
        ),
    ]
    md = merger.build_full(
        meta=meta,
        transcript=transcript,
        frames=frames,
        transcript_source="legenda manual",
        vision_provider="anthropic",
        artifacts={"audio.mp3": "/x/audio.mp3"},
    )
    assert "# Vídeo Teste" in md
    assert "**Canal:** Fulano" in md
    assert "## Descrição" in md
    assert "## Chapters" in md
    assert "## Timeline" in md
    assert "🎤 Olá pessoal" in md
    assert "🖥 Tela: TÍTULO SLIDE" in md
    assert "👁 Palestrante em pé" in md
    assert "## Anexos" in md
    assert "[audio.mp3](/x/audio.mp3)" in md


def test_merger_transcript_only() -> None:
    meta = VideoMeta(
        url="https://x.com/y",
        title="T",
        uploader="U",
        duration_s=10,
        upload_date="20260101",
        thumbnail_url="",
        description="",
        chapters=[],
        has_manual_subs=False,
        has_auto_subs=False,
    )
    ts = [TranscriptSegment(1, 2, "Um"), TranscriptSegment(3, 4, "Dois")]
    md = merger.build_transcript_only(meta, ts)
    assert md.startswith("# T")
    assert "[00:01] Um" in md
    assert "[00:03] Dois" in md


def test_vision_module_no_key(monkeypatch) -> None:
    """Without any API key set, provider autodetection returns None and
    describe_frame returns None gracefully."""
    from video import vision

    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert vision._autodetect_provider() is None
    assert vision.describe_frame(Path("/tmp/nonexistent.jpg")) is None
    availability = vision.available_providers()
    assert availability == {"anthropic": False, "openai": False, "gemini": False}
