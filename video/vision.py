"""Vision LLM providers — Anthropic, OpenAI, Gemini. BYOK.

If no API key is present in env, describe_frame() returns None and the
pipeline gracefully skips visual descriptions.
"""

from __future__ import annotations

import base64
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

# Model defaults (overridable via env vars)
ANTHROPIC_MODEL = os.environ.get("DTM_ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENAI_MODEL = os.environ.get("DTM_OPENAI_MODEL", "gpt-4o-mini")
GEMINI_MODEL = os.environ.get("DTM_GEMINI_MODEL", "gemini-2.0-flash")

_PROMPT = (
    "Descreva objetivamente o que se vê neste frame de vídeo em 1-2 frases curtas. "
    "Foque em: pessoas presentes, o que está sendo mostrado na tela (slide, gráfico, texto, "
    "imagem), ações visíveis, cenário. NÃO transcreva texto que já está legível — "
    "assumimos que OCR captura isso. Objetivo, sem julgamento, sem interpretação."
)


def available_providers() -> dict[str, bool]:
    """Return which providers have their API key configured."""
    return {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(os.environ.get("OPENAI_API_KEY")),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
    }


def _autodetect_provider() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def _read_image_b64(image_path: Path) -> tuple[str, str]:
    data = image_path.read_bytes()
    ext = image_path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return base64.b64encode(data).decode("ascii"), mime


def _describe_anthropic(image_path: Path) -> str:
    import anthropic  # lazy

    client = anthropic.Anthropic()
    b64, mime = _read_image_b64(image_path)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return " ".join(parts).strip()


def _describe_openai(image_path: Path) -> str:
    from openai import OpenAI  # lazy

    client = OpenAI()
    b64, mime = _read_image_b64(image_path)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _describe_gemini(image_path: Path) -> str:
    import google.generativeai as genai  # lazy

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(GEMINI_MODEL)
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    mime = "image/jpeg" if image_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    resp = model.generate_content(
        [
            {"mime_type": mime, "data": image_bytes},
            _PROMPT,
        ]
    )
    return (resp.text or "").strip()


def describe_frame(image_path: Path, provider: str | None = None) -> str | None:
    """Describe a single frame. Returns None if no provider is available."""
    provider = provider or _autodetect_provider()
    if provider is None:
        return None
    try:
        if provider == "anthropic":
            return _describe_anthropic(image_path)
        if provider == "openai":
            return _describe_openai(image_path)
        if provider == "gemini":
            return _describe_gemini(image_path)
    except Exception as e:
        # Never crash the pipeline over one bad frame — return marker
        return f"[erro na análise visual: {type(e).__name__}]"
    raise ValueError(f"provider desconhecido: {provider}")


def describe_frames_parallel(
    image_paths: Iterable[Path],
    provider: str | None = None,
    max_workers: int = 5,
    on_each: Callable[[int, str], None] | None = None,
) -> list[str | None]:
    """Describe a batch of frames concurrently. Preserves input order."""
    paths = list(image_paths)
    provider = provider or _autodetect_provider()
    if provider is None:
        return [None] * len(paths)

    results: list[str | None] = [None] * len(paths)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {
            pool.submit(describe_frame, p, provider): i for i, p in enumerate(paths)
        }
        for done_idx, fut in enumerate(as_completed(futs)):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = f"[erro: {type(e).__name__}]"
            if on_each:
                on_each(done_idx + 1, str(paths[i].name))
    return results
