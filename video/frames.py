"""Frame extraction — scene detection + Tesseract OCR."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Frame:
    timestamp_s: float
    image_path: Path
    ocr_text: str = ""
    vision_description: str = ""


def extract_scenes(
    video_path: Path,
    out_dir: Path,
    max_frames: int = 200,
    on_progress: Callable[[str, int, str], None] | None = None,
) -> list[Frame]:
    """Detect scene cuts with PySceneDetect and extract one keyframe per scene.

    If detection returns nothing (single-scene video), fallback to sampling
    one frame every 15 seconds.
    """
    from scenedetect import detect, ContentDetector, open_video, SceneManager

    out_dir.mkdir(parents=True, exist_ok=True)

    if on_progress:
        on_progress("frames", 5, "Detectando cortes de cena…")

    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=27.0))
    scene_manager.detect_scenes(video, show_progress=False)
    scene_list = scene_manager.get_scene_list()

    duration_s = _probe_duration(video_path)

    timestamps: list[float] = []
    if scene_list:
        for start_tc, _ in scene_list:
            timestamps.append(start_tc.get_seconds())
    if not timestamps or len(timestamps) < 3:
        # Fallback: 1 frame every 15s
        t = 0.0
        while t < duration_s:
            timestamps.append(t)
            t += 15.0

    if len(timestamps) > max_frames:
        # Uniformly downsample
        step = len(timestamps) / max_frames
        timestamps = [timestamps[int(i * step)] for i in range(max_frames)]

    frames: list[Frame] = []
    total = len(timestamps)
    for i, ts in enumerate(timestamps):
        img_path = out_dir / f"frame_{i:04d}_{int(ts):06d}.jpg"
        _extract_frame(video_path, ts, img_path)
        if img_path.exists():
            frames.append(Frame(timestamp_s=ts, image_path=img_path))
        if on_progress:
            pct = int(10 + (i / total) * 40)
            on_progress("frames", pct, f"Extraindo frames ({i + 1}/{total})")

    if on_progress:
        on_progress("frames", 50, f"{len(frames)} frames extraídos")

    return frames


def _probe_duration(video_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _extract_frame(video_path: Path, timestamp_s: float, out_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg não encontrado no PATH")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{timestamp_s:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out_path),
        ],
        capture_output=True,
    )


def ocr_frames(
    frames: list[Frame],
    langs: str = "por+eng",
    on_progress: Callable[[str, int, str], None] | None = None,
) -> None:
    """Run Tesseract OCR on each frame in place, populating .ocr_text."""
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return  # silently skip — tesseract missing shouldn't kill the pipeline

    total = len(frames)
    for i, frame in enumerate(frames):
        proc = subprocess.run(
            [tesseract, str(frame.image_path), "stdout", "-l", langs, "--psm", "6"],
            capture_output=True,
            text=True,
        )
        text = proc.stdout.strip() if proc.returncode == 0 else ""
        # Skip noise: OCR often finds a few garbage chars on decorative frames
        if len(text) < 15:
            text = ""
        frame.ocr_text = text
        if on_progress:
            pct = int(50 + (i / total) * 20)
            on_progress("ocr", pct, f"OCR nos frames ({i + 1}/{total})")

    if on_progress:
        on_progress("ocr", 70, "OCR completo")
