"""Native desktop entrypoint for DocToMarkdown.

Runs the Flask app in a background thread and hosts its UI inside a native
window via pywebview (WKWebView on macOS, WebView2 on Windows, WebKitGTK on
Linux). No browser needed — the app behaves like any other desktop app:
own Dock icon, own window, cmd/alt+Q to quit, native menu bar.

The Flask HTTP API stays intact and reachable at http://127.0.0.1:<port>
for other clients (Claude Code skill, CI, curl automation, etc.) while the
window is open.

Run:
    python desktop_app.py
Or via the .app / .lnk / .desktop launcher installed by platform/*/install.
"""

from __future__ import annotations

import io
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

# When launched via pythonw.exe (no console attached, e.g. the Start Menu
# shortcut on Windows), sys.stdout/stderr are None. Flask/Werkzeug print to
# them on startup, which raises AttributeError and kills the server thread
# silently — the user sees no window and no error dialog. Give the streams
# a sink so any startup print stays alive.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

# Make sure the repo dir is on sys.path (matters when the launcher script
# invokes us from anywhere).
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import webview  # noqa: E402

from app import app  # noqa: E402


class Api:
    """JS-accessible bridge (window.pywebview.api).

    Lets the frontend open the OS-native file picker, which — unlike the
    HTML <input type=file> — returns the file's absolute path. With the
    path in hand the backend can save the converted .md right next to
    the original file, which a browser upload can never do.
    """

    def pick_file(self) -> str | None:
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return None
        try:
            dialog_enum = getattr(webview, "FileDialog", None)
            mode = dialog_enum.OPEN if dialog_enum else webview.OPEN_DIALOG
            result = win.create_file_dialog(mode, allow_multiple=False)
        except Exception:
            return None
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else str(result)


def _pick_free_port(preferred: int = 5555) -> int:
    for candidate in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", candidate))
                _, port = s.getsockname()
                if candidate == 0:
                    return port
                return candidate
        except OSError:
            continue
    raise RuntimeError("Could not bind to any port")


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=0.5
            ) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def _serve(port: int) -> None:
    # `threaded=True` keeps SSE and long jobs responsive alongside static requests.
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


def main() -> int:
    port = _pick_free_port(int(os.environ.get("PORT", "5555")))

    server_thread = threading.Thread(target=_serve, args=(port,), daemon=True)
    server_thread.start()

    if not _wait_for_server(port):
        print(f"! Backend did not come up on port {port}", file=sys.stderr)
        return 1

    # Publish the port so any other tools launched afterwards (mcp_server,
    # scripts, the Claude Code skill) can discover it.
    try:
        import tempfile

        (Path(tempfile.gettempdir()) / "doctomarkdown.port").write_text(str(port))
    except OSError:
        pass

    # Off by default in pywebview — without this, clicking "Baixar .md" (or
    # any <a download>/attachment response) is silently swallowed by WebView2
    # on Windows and other webviews that treat downloads as opt-in.
    webview.settings["ALLOW_DOWNLOADS"] = True

    webview.create_window(
        title="DocToMarkdown",
        url=f"http://127.0.0.1:{port}",
        width=1100,
        height=800,
        min_size=(720, 520),
        text_select=True,
        js_api=Api(),
    )
    # `pywebview` auto-selects the best backend for the current OS.
    webview.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
