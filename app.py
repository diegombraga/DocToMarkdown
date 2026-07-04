"""DocToMarkdown — Flask backend wrapping markitdown + ocrmypdf.

Cross-platform (macOS, Linux, Windows). Requires the following binaries on
PATH: markitdown (Python package, shipped via requirements.txt) and ocrmypdf
(installed system-wide by the platform installer).
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request

MARKITDOWN = shutil.which("markitdown")
OCRMYPDF = shutil.which("ocrmypdf")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify(
        {
            "markitdown": bool(MARKITDOWN),
            "ocrmypdf": bool(OCRMYPDF),
            "markitdown_path": MARKITDOWN or "",
            "ocrmypdf_path": OCRMYPDF or "",
        }
    )


@app.route("/convert", methods=["POST"])
def convert():
    if not MARKITDOWN:
        return jsonify({"error": "markitdown não está instalado no PATH"}), 500

    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Arquivo sem nome"}), 400

    use_ocr = request.form.get("use_ocr") == "true"
    force_ocr = request.form.get("force_ocr") == "true"
    langs = request.form.get("langs", "por+eng").strip() or "por+eng"

    if use_ocr and not OCRMYPDF:
        return (
            jsonify({"error": "OCR pedido mas ocrmypdf não está instalado no PATH"}),
            500,
        )

    with tempfile.TemporaryDirectory() as raw:
        tmpdir = Path(raw)
        original = tmpdir / Path(f.filename).name
        f.save(str(original))

        target = original
        ocr_log = ""
        did_ocr = False

        if use_ocr and original.suffix.lower() == ".pdf":
            ocred = tmpdir / f"ocr_{original.name}"
            cmd = [OCRMYPDF, "-l", langs, "--optimize", "1"]
            cmd.append("--force-ocr" if force_ocr else "--skip-text")
            cmd += [str(original), str(ocred)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            ocr_log = (proc.stderr or "") + (proc.stdout or "")
            if proc.returncode != 0:
                return jsonify({"error": "OCR falhou", "log": ocr_log}), 500
            target = ocred
            did_ocr = True

        proc = subprocess.run(
            [MARKITDOWN, str(target)], capture_output=True, text=True
        )
        if proc.returncode != 0:
            return (
                jsonify(
                    {"error": "markitdown falhou", "log": proc.stderr or proc.stdout}
                ),
                500,
            )

        return jsonify(
            {
                "markdown": proc.stdout,
                "filename": Path(f.filename).stem + ".md",
                "did_ocr": did_ocr,
                "ocr_log": ocr_log if did_ocr else None,
                "chars": len(proc.stdout),
            }
        )


def _pick_port(preferred: int) -> int:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", preferred))
        return preferred
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    preferred = int(os.environ.get("PORT", "5555"))
    # HOST=127.0.0.1 by default keeps the UI local-only on installed machines.
    # Container images override to 0.0.0.0 so the mapped port is reachable.
    host = os.environ.get("HOST", "127.0.0.1")
    port = _pick_port(preferred) if host == "127.0.0.1" else preferred
    if port != preferred:
        print(
            f"  Porta {preferred} ocupada — usando {port}", flush=True, file=sys.stderr
        )
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(
        f"\n  DocToMarkdown rodando em http://{display_host}:{port}\n", flush=True
    )
    Path(tempfile.gettempdir(), "doctomarkdown.port").write_text(str(port))
    app.run(host=host, port=port, debug=False)
