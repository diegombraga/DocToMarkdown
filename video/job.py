"""In-memory job manager — one background thread per submitted URL.

Progress is emitted through a per-job queue; the Flask SSE endpoint consumes
that queue and streams events to the browser.
"""

from __future__ import annotations

import json
import queue
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import downloader, frames, merger, transcriber, vision


@dataclass
class JobStatus:
    id: str
    url: str
    stage: str = "queued"
    pct: int = 0
    message: str = ""
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "stage": self.stage,
            "pct": self.pct,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "done": self.result is not None,
        }


class JobManager:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(tempfile.gettempdir()) / "dtm-jobs"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobStatus] = {}
        self._queues: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    # --- API ---
    def submit(self, url: str, options: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = JobStatus(id=job_id, url=url)
            self._queues[job_id] = queue.Queue()
        threading.Thread(
            target=self._run,
            args=(job_id, url, options),
            daemon=True,
        ).start()
        return job_id

    def get(self, job_id: str) -> JobStatus | None:
        return self._jobs.get(job_id)

    def events(self, job_id: str, timeout: float = 60.0):
        """Generator yielding SSE-formatted strings until the job ends."""
        q = self._queues.get(job_id)
        if q is None:
            yield f"data: {json.dumps({'error': 'job desconhecido'})}\n\n"
            return

        # Emit current state immediately for reconnecting clients
        job = self._jobs.get(job_id)
        if job:
            yield f"data: {json.dumps(job.to_dict())}\n\n"

        while True:
            try:
                event = q.get(timeout=timeout)
            except queue.Empty:
                # Heartbeat comment (SSE): keeps the connection alive
                yield ": heartbeat\n\n"
                if not self._is_active(job_id):
                    break
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("stage") in ("done", "failed"):
                break

    def _is_active(self, job_id: str) -> bool:
        j = self._jobs.get(job_id)
        return bool(j and j.finished_at is None)

    def artifact_path(self, job_id: str, name: str) -> Path | None:
        p = self.base_dir / job_id / "artifacts" / name
        if p.exists() and p.is_file():
            return p
        return None

    # --- internals ---
    def _emit(self, job_id: str, stage: str, pct: int, message: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.stage = stage
        job.pct = pct
        job.message = message
        self._queues[job_id].put(
            {"stage": stage, "pct": pct, "message": message}
        )

    def _run(self, job_id: str, url: str, options: dict[str, Any]) -> None:
        job_dir = self.base_dir / job_id
        (job_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        work_dir = job_dir / "work"
        work_dir.mkdir(exist_ok=True)

        try:
            self._pipeline(job_id, url, options, job_dir, work_dir)
        except Exception as e:  # noqa: BLE001
            job = self._jobs[job_id]
            job.error = f"{type(e).__name__}: {e}"
            job.finished_at = time.time()
            self._queues[job_id].put(
                {"stage": "failed", "pct": 0, "message": job.error, "error": job.error}
            )
        finally:
            # Keep artifacts, drop scratch
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def _pipeline(
        self,
        job_id: str,
        url: str,
        options: dict[str, Any],
        job_dir: Path,
        work_dir: Path,
    ) -> None:
        emit: Callable[[str, int, str], None] = lambda s, p, m: self._emit(
            job_id, s, p, m
        )
        artifacts_dir = job_dir / "artifacts"

        want_video = bool(options.get("output_video"))
        want_audio = bool(options.get("output_audio"))
        transcript_mode = options.get("transcript_mode", "auto")  # auto|subs|whisper
        whisper_model = options.get("whisper_model", "base")
        vision_provider = options.get("vision_provider")  # anthropic|openai|gemini|None
        do_vision = bool(vision_provider) and vision_provider != "none"

        # -- Phase 1: metadata + downloads
        emit("probing", 2, "Sondando metadata do vídeo…")
        meta = downloader.probe(url)
        emit(
            "probed",
            5,
            f"'{meta.title}' — {meta.uploader} — {meta.duration_s}s",
        )

        if meta.duration_s > 4 * 3600:
            raise ValueError(
                "Vídeo excede 4h — limite v0.2. Corte em partes menores."
            )

        emit("downloading", 8, "Baixando áudio…")
        audio_path = downloader.download_audio(url, work_dir, on_progress=emit)
        if want_audio:
            shutil.copy2(audio_path, artifacts_dir / "audio.mp3")

        video_path: Path | None = None
        if want_video:
            emit("downloading", 20, "Baixando vídeo…")
            video_path = downloader.download_video(url, work_dir, on_progress=emit)
            shutil.copy2(video_path, artifacts_dir / video_path.name)
        else:
            # We still need frames — download a lightweight version
            # (yt-dlp defaults to a reasonable format)
            emit("downloading", 20, "Baixando vídeo (temporário para frames)…")
            video_path = downloader.download_video(url, work_dir, on_progress=emit)

        # -- Phase 2: transcript
        transcript: list[transcriber.TranscriptSegment] = []
        transcript_source = "whisper (local)"

        srt_path: Path | None = None
        is_manual = False
        if transcript_mode in ("auto", "subs"):
            emit("transcript", 32, "Buscando legendas nativas…")
            srt_path, is_manual = downloader.fetch_subtitles(url, work_dir)

        if srt_path is not None and transcript_mode != "whisper":
            transcript = transcriber.parse_srt(srt_path)
            transcript_source = (
                "legenda manual" if is_manual else "legenda auto-gerada"
            )
            emit("transcript", 45, f"Transcrição obtida ({len(transcript)} segmentos)")
        else:
            emit("transcript", 35, "Sem legenda usável — rodando Whisper local…")
            transcript = transcriber.transcribe_whisper(
                audio_path, model_name=whisper_model, on_progress=emit
            )
            transcript_source = f"Whisper local ({whisper_model})"

        # -- Phase 3: frames
        emit("frames", 55, "Extraindo frames por corte de cena…")
        extracted = frames.extract_scenes(video_path, work_dir / "frames", on_progress=emit)

        # -- Phase 4a: OCR
        emit("ocr", 70, "OCR nos frames extraídos…")
        frames.ocr_frames(extracted, on_progress=emit)

        # -- Phase 4b: vision LLM
        if do_vision and vision_provider in vision.available_providers():
            available = vision.available_providers()
            if not available.get(vision_provider):
                emit(
                    "vision_skipped",
                    85,
                    f"Provider '{vision_provider}' sem API key — pulando análise visual",
                )
            else:
                emit(
                    "vision",
                    80,
                    f"Análise visual com {vision_provider} ({len(extracted)} frames)…",
                )
                total = len(extracted)
                done = {"n": 0}

                def _tick(idx: int, _name: str) -> None:
                    done["n"] = idx
                    pct = int(80 + (idx / max(1, total)) * 12)
                    emit("vision", pct, f"Descrevendo frames ({idx}/{total})")

                descs = vision.describe_frames_parallel(
                    [f.image_path for f in extracted],
                    provider=vision_provider,
                    max_workers=5,
                    on_each=_tick,
                )
                for f, d in zip(extracted, descs):
                    if d:
                        f.vision_description = d
        else:
            emit("vision_skipped", 90, "Análise visual desativada")

        # -- Phase 5: merge
        emit("merging", 94, "Montando Markdown final…")
        artifacts_urls: dict[str, str] = {}
        for artifact in artifacts_dir.iterdir():
            artifacts_urls[artifact.name] = (
                f"/video/artifact/{job_id}/{artifact.name}"
            )
        md = merger.build_full(
            meta=meta,
            transcript=transcript,
            frames=extracted,
            transcript_source=transcript_source,
            vision_provider=vision_provider if do_vision else None,
            artifacts=artifacts_urls or None,
        )
        md_path = artifacts_dir / "video.md"
        md_path.write_text(md, encoding="utf-8")

        transcript_only = merger.build_transcript_only(meta, transcript)
        (artifacts_dir / "transcript-only.md").write_text(
            transcript_only, encoding="utf-8"
        )

        result = {
            "markdown": md,
            "filename": f"{meta.id or 'video'}.md",
            "chars": len(md),
            "artifacts": {
                name: f"/video/artifact/{job_id}/{name}"
                for name in ("video.md", "transcript-only.md")
                + tuple(a.name for a in artifacts_dir.iterdir()
                        if a.name not in ("video.md", "transcript-only.md"))
            },
            "meta": meta.to_dict(),
            "transcript_source": transcript_source,
            "vision_provider": vision_provider if do_vision else None,
        }
        job = self._jobs[job_id]
        job.result = result
        job.finished_at = time.time()
        emit("done", 100, "Pronto.")

    def _cleanup_loop(self) -> None:
        """Delete finished jobs older than 30 min every 5 min."""
        while True:
            time.sleep(300)
            cutoff = time.time() - 30 * 60
            to_del: list[str] = []
            with self._lock:
                for jid, j in self._jobs.items():
                    if j.finished_at and j.finished_at < cutoff:
                        to_del.append(jid)
                for jid in to_del:
                    self._jobs.pop(jid, None)
                    self._queues.pop(jid, None)
            for jid in to_del:
                shutil.rmtree(self.base_dir / jid, ignore_errors=True)


# Convenience singleton wired up by app.py
_MANAGER: JobManager | None = None


def process_url(url: str, options: dict[str, Any]) -> str:
    """Submit a job and return the id."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = JobManager()
    return _MANAGER.submit(url, options)


def get_manager() -> JobManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = JobManager()
    return _MANAGER
