"""Video → Markdown pipeline for DocToMarkdown.

Orchestrates yt-dlp (download + metadata), Whisper (transcript fallback),
PySceneDetect + Tesseract (frame OCR), and vision LLMs (Anthropic/OpenAI/Gemini)
into a single Markdown document that captures the video's full context.
"""

from .job import JobManager, JobStatus, get_manager, process_url  # noqa: F401
from .vision import available_providers  # noqa: F401
from . import vision, downloader, transcriber, frames, merger, job  # noqa: F401
