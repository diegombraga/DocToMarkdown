"""DocToMarkdown — Flask backend wrapping markitdown + ocrmypdf + video pipeline.

Cross-platform (macOS, Linux, Windows). Requires the following binaries on
PATH: markitdown (Python package, shipped via requirements.txt), ocrmypdf,
ffmpeg, tesseract (installed system-wide by the platform installer).
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from video import config as video_config
from video import get_manager, vision

MARKITDOWN = shutil.which("markitdown")
OCRMYPDF = shutil.which("ocrmypdf")
FFMPEG = shutil.which("ffmpeg")
TESSERACT = shutil.which("tesseract")

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
            "ffmpeg": bool(FFMPEG),
            "tesseract": bool(TESSERACT),
            "vision_providers": vision.available_providers(),
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


# ---------------------------------------------------------------------------
# Settings — API keys for vision LLM providers
# ---------------------------------------------------------------------------


@app.route("/settings/keys", methods=["GET"])
def settings_keys_get():
    return jsonify(video_config.snapshot())


@app.route("/settings/keys", methods=["POST"])
def settings_keys_post():
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "").strip()
    key = (data.get("key") or "").strip()
    if not provider or not key:
        return jsonify({"error": "provider e key são obrigatórios"}), 400
    try:
        video_config.set_key(provider, key)
    except (ValueError, PermissionError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(video_config.snapshot())


@app.route("/settings/keys/<provider>", methods=["DELETE"])
def settings_keys_delete(provider):
    try:
        video_config.delete_key(provider)
    except (ValueError, PermissionError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(video_config.snapshot())


# ---------------------------------------------------------------------------
# Video pipeline routes
# ---------------------------------------------------------------------------


@app.route("/video/preview")
def video_preview():
    from video import downloader

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "parâmetro 'url' obrigatório"}), 400
    try:
        meta = downloader.probe(url)
    except Exception as e:  # noqa: BLE001
        return (
            jsonify({"error": f"não foi possível obter metadata: {e}"}),
            400,
        )
    return jsonify(meta.to_dict())


@app.route("/video/process", methods=["POST"])
def video_process():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "campo 'url' obrigatório"}), 400
    if not FFMPEG:
        return jsonify({"error": "ffmpeg não está instalado"}), 500

    options = {
        "output_video": bool(data.get("output_video")),
        "output_audio": bool(data.get("output_audio")),
        "transcript_mode": data.get("transcript_mode", "auto"),
        "whisper_model": data.get("whisper_model", "base"),
        "vision_provider": data.get("vision_provider"),
    }
    job_id = get_manager().submit(url, options)
    return jsonify({"job_id": job_id})


@app.route("/video/status/<job_id>")
def video_status(job_id):
    mgr = get_manager()

    def stream():
        for chunk in mgr.events(job_id):
            yield chunk

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/video/result/<job_id>")
def video_result(job_id):
    mgr = get_manager()
    job = mgr.get(job_id)
    if not job:
        return jsonify({"error": "job desconhecido"}), 404
    if job.error:
        return jsonify({"error": job.error}), 500
    if not job.result:
        return jsonify({"error": "job ainda em andamento", "stage": job.stage}), 425
    return jsonify(job.result)


@app.route("/video/artifact/<job_id>/<path:name>")
def video_artifact(job_id, name):
    mgr = get_manager()
    # Defense-in-depth: resolve the candidate path and confirm it stays inside
    # the job's own artifacts dir. This catches URL-encoded traversal, symlink
    # escapes, and any future refactor of artifact_path().
    try:
        base = (mgr.base_dir / job_id / "artifacts").resolve(strict=True)
        candidate = (base / name).resolve(strict=True)
        candidate.relative_to(base)  # raises ValueError if outside
    except (FileNotFoundError, ValueError, OSError):
        return jsonify({"error": "arquivo não encontrado"}), 404
    if not candidate.is_file():
        return jsonify({"error": "arquivo não encontrado"}), 404
    return send_file(
        str(candidate),
        as_attachment=True,
        download_name=Path(name).name,  # strip any dir prefix in the filename
    )


# ---------------------------------------------------------------------------


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
