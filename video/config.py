"""API key storage for the vision LLM providers.

Keys live in a per-user JSON file with restrictive permissions (0600 on
Unix). On import + on every write, we sync the values into os.environ so
the vision SDKs pick them up transparently.

Env-var precedence: if the user launched the app with a real
ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY, we keep that and do
NOT overwrite it. The config file only fills in providers that aren't
already set in the environment.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

_ENV_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Track env vars that came from the process launch environment so we never
# let the config file clobber them.
_LAUNCH_ENV: set[str] = {
    env_name for env_name in _ENV_MAP.values() if os.environ.get(env_name)
}


def config_path() -> Path:
    p = Path.home() / ".config" / "DocToMarkdown" / "keys.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_file() -> dict[str, Any]:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_file(data: dict[str, Any]) -> None:
    p = config_path()
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — user read/write only
    except OSError:
        pass  # Windows may raise on chmod — ignore


def sync_env() -> None:
    """Push saved keys into os.environ.

    Only sets vars that were NOT provided at launch — a runtime edit of the
    config file must never override a value the user set via `export`.
    """
    data = _read_file()
    for provider, env_name in _ENV_MAP.items():
        if env_name in _LAUNCH_ENV:
            continue
        value = data.get(env_name)
        if value:
            os.environ[env_name] = value
        else:
            os.environ.pop(env_name, None)


def snapshot() -> dict[str, Any]:
    """Return a UI-safe status of each provider (no raw keys)."""
    data = _read_file()
    out: dict[str, Any] = {}
    for provider, env_name in _ENV_MAP.items():
        val = os.environ.get(env_name) or data.get(env_name) or ""
        launch = env_name in _LAUNCH_ENV
        masked = _mask(val) if val else ""
        out[provider] = {
            "configured": bool(val),
            "masked": masked,
            "from_launch_env": launch,
            "source": ("env" if launch else ("file" if data.get(env_name) else "none")),
        }
    return out


def set_key(provider: str, key: str) -> None:
    if provider not in _ENV_MAP:
        raise ValueError(f"provider desconhecido: {provider}")
    env_name = _ENV_MAP[provider]
    if env_name in _LAUNCH_ENV:
        raise PermissionError(
            f"{env_name} foi definido no ambiente de lançamento; remova-o antes de configurar via UI"
        )
    data = _read_file()
    data[env_name] = key.strip()
    _write_file(data)
    sync_env()


def delete_key(provider: str) -> None:
    if provider not in _ENV_MAP:
        raise ValueError(f"provider desconhecido: {provider}")
    env_name = _ENV_MAP[provider]
    if env_name in _LAUNCH_ENV:
        raise PermissionError(
            f"{env_name} veio do ambiente de lançamento; feche o app e faça `unset` no shell"
        )
    data = _read_file()
    data.pop(env_name, None)
    _write_file(data)
    sync_env()


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:6]}…{key[-4:]}"


# Load on module import so app.py picks up keys before any /video/process
# request reaches the vision module.
sync_env()
