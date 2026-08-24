"""DocToMarkdown — Flask backend wrapping markitdown + ocrmypdf + video pipeline.

Cross-platform (macOS, Linux, Windows). Requires the following binaries on
PATH: markitdown (Python package, shipped via requirements.txt), ocrmypdf,
ffmpeg, tesseract (installed system-wide by the platform installer).
"""

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
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


# ---------------------------------------------------------------------------
# /convert as a background job — lets the UI show real OCR-page progress
# instead of blocking on one long request. The old synchronous /convert
# route stays available for scripts that don't want SSE.
# ---------------------------------------------------------------------------

_CONVERT_BASE_DIR = Path(tempfile.gettempdir()) / "dtm-convert-jobs"
_CONVERT_BASE_DIR.mkdir(parents=True, exist_ok=True)
_CONVERT_JOBS: dict[str, dict] = {}
_CONVERT_QUEUES: dict[str, "queue.Queue"] = {}
_CONVERT_LOCK = threading.Lock()
_OCR_PAGE_LINE = re.compile(r"^\s*(\d+)\s")


def _convert_emit(job_id: str, stage: str, pct: int, message: str) -> None:
    with _CONVERT_LOCK:
        job = _CONVERT_JOBS.get(job_id)
        if job is None:
            return
        job["stage"], job["pct"], job["message"] = stage, pct, message
    q = _CONVERT_QUEUES.get(job_id)
    if q:
        q.put({"stage": stage, "pct": pct, "message": message})


def _convert_pipeline(
    job_id: str,
    saved_path: Path,
    orig_filename: str,
    use_ocr: bool,
    force_ocr: bool,
    langs: str,
    source_path: Path | None = None,
) -> None:
    try:
        target = saved_path
        did_ocr = False
        ocr_log = ""

        if use_ocr and saved_path.suffix.lower() == ".pdf":
            # Best-effort page count for a nicer progress bar. Falls back to
            # coarse "OCR em andamento" if pikepdf can't open the file.
            total_pages = None
            try:
                import pikepdf

                with pikepdf.open(saved_path) as pdf:
                    total_pages = len(pdf.pages)
            except Exception:  # noqa: BLE001
                total_pages = None

            _convert_emit(job_id, "ocr", 5, "Iniciando OCR…")
            ocred = saved_path.parent / f"ocr_{saved_path.name}"
            cmd = [OCRMYPDF, "-v", "1", "-l", langs, "--optimize", "1"]
            cmd.append("--force-ocr" if force_ocr else "--skip-text")
            cmd += [str(saved_path), str(ocred)]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            log_lines: list[str] = []
            last_page = 0
            for line in proc.stdout:  # type: ignore[union-attr]
                log_lines.append(line)
                m = _OCR_PAGE_LINE.match(line)
                if m and total_pages:
                    page = int(m.group(1))
                    if last_page < page <= total_pages:
                        last_page = page
                        pct = 5 + int(80 * page / total_pages)
                        _convert_emit(
                            job_id, "ocr", pct, f"OCR: página {page}/{total_pages}"
                        )
                elif "Postprocessing" in line:
                    _convert_emit(job_id, "ocr", 88, "Finalizando OCR…")
            proc.wait()
            ocr_log = "".join(log_lines)
            if proc.returncode != 0:
                raise RuntimeError(f"OCR falhou: {ocr_log[-2000:]}")
            target = ocred
            did_ocr = True

        _convert_emit(job_id, "markitdown", 92, "Convertendo para Markdown…")
        proc = subprocess.run(
            [MARKITDOWN, str(target)], capture_output=True, text=True
        )
        if proc.returncode != 0:
            raise RuntimeError(f"markitdown falhou: {proc.stderr or proc.stdout}")

        md = proc.stdout
        out_name = Path(orig_filename).stem + ".md"
        md_path = saved_path.parent / out_name
        md_path.write_text(md, encoding="utf-8")

        # Auto-save next to the original when we know where it lives (the
        # desktop app passes source_path via the native picker / drag-drop;
        # plain-browser uploads can't reveal it, so saved_to stays None there).
        saved_to = None
        if source_path is not None:
            try:
                dest = source_path.with_suffix(".md")
                if dest.resolve() == source_path.resolve():
                    dest = source_path.parent / (source_path.stem + ".converted.md")
                dest.write_text(md, encoding="utf-8")
                saved_to = str(dest)
            except OSError:
                saved_to = None  # unwritable dir — download button still works

        with _CONVERT_LOCK:
            job = _CONVERT_JOBS[job_id]
            job["md_path"] = str(md_path)
            job["finished_at"] = time.time()
            job["result"] = {
                "markdown": md,
                "filename": out_name,
                "did_ocr": did_ocr,
                "ocr_log": ocr_log if did_ocr else None,
                "chars": len(md),
                "download_url": f"/convert/download/{job_id}",
                "saved_to": saved_to,
            }
        _convert_emit(job_id, "done", 100, "Pronto.")
    except Exception as e:  # noqa: BLE001
        with _CONVERT_LOCK:
            job = _CONVERT_JOBS.get(job_id)
            if job is not None:
                job["error"] = str(e)
                job["finished_at"] = time.time()
        _convert_emit(job_id, "failed", 0, str(e))


def _convert_cleanup_loop() -> None:
    """Delete finished convert jobs (and their temp files) older than 30 min."""
    while True:
        time.sleep(300)
        cutoff = time.time() - 30 * 60
        with _CONVERT_LOCK:
            stale = [
                jid
                for jid, j in _CONVERT_JOBS.items()
                if j.get("finished_at") and j["finished_at"] < cutoff
            ]
            for jid in stale:
                _CONVERT_JOBS.pop(jid, None)
                _CONVERT_QUEUES.pop(jid, None)
        for jid in stale:
            shutil.rmtree(_CONVERT_BASE_DIR / jid, ignore_errors=True)


threading.Thread(target=_convert_cleanup_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Batch conversion — point at a local folder, convert every supported file,
# saving each .md next to its original. Desktop app picks the folder via the
# native dialog; typing an absolute path works too (the server is local).
# ---------------------------------------------------------------------------

SUPPORTED_BATCH_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".html", ".htm", ".epub",
    ".mp3", ".wav", ".m4a",
    ".csv", ".tsv", ".json", ".xml", ".ipynb", ".zip",
}
BATCH_MAX_FILES = 500

_BATCH_JOBS: dict[str, dict] = {}
_BATCH_QUEUES: dict[str, "queue.Queue"] = {}
_BATCH_LOCK = threading.Lock()


def _scan_folder(folder: Path, recursive: bool) -> list[Path]:
    it = folder.rglob("*") if recursive else folder.glob("*")
    out: list[Path] = []
    for p in sorted(it):
        if not p.is_file():
            continue
        # Skip hidden files and Office lock files (~$doc.docx)
        if p.name.startswith(".") or p.name.startswith("~$"):
            continue
        if p.suffix.lower() in SUPPORTED_BATCH_EXTS:
            out.append(p)
    return out


def _batch_emit(job_id: str, stage: str, pct: int, message: str) -> None:
    with _BATCH_LOCK:
        job = _BATCH_JOBS.get(job_id)
        if job is None:
            return
        job["stage"], job["pct"], job["message"] = stage, pct, message
    q = _BATCH_QUEUES.get(job_id)
    if q:
        q.put({"stage": stage, "pct": pct, "message": message})


def _convert_one_file(
    src: Path, use_ocr: bool, force_ocr: bool, langs: str, tmpdir: Path
) -> str:
    """Convert a single file to Markdown text. Raises RuntimeError on failure."""
    target = src
    if use_ocr and src.suffix.lower() == ".pdf":
        if OCRMYPDF is None:
            raise RuntimeError("ocrmypdf não está instalado")
        ocred = tmpdir / f"ocr_{src.name}"
        cmd = [OCRMYPDF, "-l", langs, "--optimize", "1"]
        cmd.append("--force-ocr" if force_ocr else "--skip-text")
        cmd += [str(src), str(ocred)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"OCR falhou: {(proc.stderr or proc.stdout)[-500:]}")
        target = ocred
    proc = subprocess.run([MARKITDOWN, str(target)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"markitdown falhou: {(proc.stderr or proc.stdout)[-500:]}"
        )
    return proc.stdout


def _batch_pipeline(
    job_id: str,
    files: list[Path],
    use_ocr: bool,
    force_ocr: bool,
    langs: str,
    overwrite: bool,
) -> None:
    converted: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    total = len(files)

    try:
        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            for i, src in enumerate(files):
                pct = int((i / total) * 100) if total else 100
                _batch_emit(
                    job_id, "converting", pct, f"({i + 1}/{total}) {src.name}"
                )
                dest = src.with_suffix(".md")
                if dest.exists() and not overwrite:
                    skipped.append(str(src))
                    continue
                try:
                    md = _convert_one_file(src, use_ocr, force_ocr, langs, tmpdir)
                    dest.write_text(md, encoding="utf-8")
                    converted.append(str(dest))
                except Exception as e:  # noqa: BLE001 — one bad file must not kill the batch
                    failed.append({"file": str(src), "error": str(e)})

        with _BATCH_LOCK:
            job = _BATCH_JOBS[job_id]
            job["finished_at"] = time.time()
            job["result"] = {
                "total": total,
                "converted": converted,
                "skipped": skipped,
                "failed": failed,
            }
        _batch_emit(
            job_id,
            "done",
            100,
            f"Concluído: {len(converted)} convertidos, "
            f"{len(skipped)} pulados, {len(failed)} falhas.",
        )
    except Exception as e:  # noqa: BLE001
        with _BATCH_LOCK:
            job = _BATCH_JOBS.get(job_id)
            if job is not None:
                job["error"] = str(e)
                job["finished_at"] = time.time()
        _batch_emit(job_id, "failed", 0, str(e))


@app.route("/batch/scan", methods=["POST"])
def batch_scan():
    data = request.get_json(silent=True) or {}
    raw = (data.get("folder_path") or "").strip()
    if not raw:
        return jsonify({"error": "campo 'folder_path' obrigatório"}), 400
    folder = Path(raw).expanduser()
    if not folder.is_dir():
        return jsonify({"error": f"pasta não encontrada: {folder}"}), 400
    recursive = bool(data.get("recursive"))

    files = _scan_folder(folder, recursive)
    by_ext: dict[str, int] = {}
    for f in files:
        by_ext[f.suffix.lower()] = by_ext.get(f.suffix.lower(), 0) + 1
    return jsonify(
        {
            "folder": str(folder),
            "total": len(files),
            "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
            "over_limit": len(files) > BATCH_MAX_FILES,
            "limit": BATCH_MAX_FILES,
        }
    )


@app.route("/batch/start", methods=["POST"])
def batch_start():
    if not MARKITDOWN:
        return jsonify({"error": "markitdown não está instalado no PATH"}), 500

    data = request.get_json(silent=True) or {}
    raw = (data.get("folder_path") or "").strip()
    if not raw:
        return jsonify({"error": "campo 'folder_path' obrigatório"}), 400
    folder = Path(raw).expanduser()
    if not folder.is_dir():
        return jsonify({"error": f"pasta não encontrada: {folder}"}), 400

    recursive = bool(data.get("recursive"))
    overwrite = bool(data.get("overwrite"))
    use_ocr = bool(data.get("use_ocr"))
    force_ocr = bool(data.get("force_ocr"))
    langs = (data.get("langs") or "por+eng").strip() or "por+eng"

    if use_ocr and not OCRMYPDF:
        return (
            jsonify({"error": "OCR pedido mas ocrmypdf não está instalado no PATH"}),
            500,
        )

    files = _scan_folder(folder, recursive)
    if not files:
        return jsonify({"error": "nenhum arquivo suportado encontrado na pasta"}), 400
    if len(files) > BATCH_MAX_FILES:
        return (
            jsonify(
                {
                    "error": (
                        f"{len(files)} arquivos excedem o limite de "
                        f"{BATCH_MAX_FILES} por lote. Divida em subpastas."
                    )
                }
            ),
            400,
        )

    job_id = uuid.uuid4().hex[:12]
    with _BATCH_LOCK:
        _BATCH_JOBS[job_id] = {
            "stage": "queued",
            "pct": 0,
            "message": "",
            "error": None,
            "result": None,
            "finished_at": None,
        }
        _BATCH_QUEUES[job_id] = queue.Queue()

    threading.Thread(
        target=_batch_pipeline,
        args=(job_id, files, use_ocr, force_ocr, langs, overwrite),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id, "total": len(files)})


@app.route("/batch/status/<job_id>")
def batch_status(job_id):
    def stream():
        q = _BATCH_QUEUES.get(job_id)
        if q is None:
            yield f"data: {json.dumps({'error': 'job desconhecido'})}\n\n"
            return

        job = _BATCH_JOBS.get(job_id)
        if job:
            yield (
                f"data: {json.dumps({'stage': job['stage'], 'pct': job['pct'], 'message': job['message']})}\n\n"
            )

        while True:
            try:
                event = q.get(timeout=60.0)
            except queue.Empty:
                yield ": heartbeat\n\n"
                j = _BATCH_JOBS.get(job_id)
                if not j or j.get("finished_at") is not None:
                    break
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("stage") in ("done", "failed"):
                break

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/batch/result/<job_id>")
def batch_result(job_id):
    job = _BATCH_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job desconhecido"}), 404
    if job.get("error"):
        return jsonify({"error": job["error"]}), 500
    if not job.get("result"):
        return (
            jsonify({"error": "job ainda em andamento", "stage": job.get("stage")}),
            425,
        )
    return jsonify(job["result"])


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


# ---------------------------------------------------------------------------
# Synchronous /convert (legacy — kept for scripts and CI probes)
# ---------------------------------------------------------------------------


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
# Asynchronous /convert/* — used by the UI for real-time progress on large
# files. Same accepted form fields as /convert; returns a job_id and streams
# per-page OCR progress via SSE.
# ---------------------------------------------------------------------------


@app.route("/convert/start", methods=["POST"])
def convert_start():
    if not MARKITDOWN:
        return jsonify({"error": "markitdown não está instalado no PATH"}), 500

    # Optional absolute path of the original file on this machine. Sent by
    # the desktop app (native picker or drag-drop full path); enables saving
    # the .md next to the original. Two accepted shapes:
    #   - upload + source_path  -> convert the upload, save alongside source
    #   - source_path only      -> read the file straight from disk
    source_path: Path | None = None
    raw_source = (request.form.get("source_path") or "").strip()
    if raw_source:
        candidate = Path(raw_source).expanduser()
        if candidate.is_file():
            source_path = candidate

    f = request.files.get("file")
    has_upload = f is not None and bool(f.filename)
    if not has_upload and source_path is None:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    use_ocr = request.form.get("use_ocr") == "true"
    force_ocr = request.form.get("force_ocr") == "true"
    langs = request.form.get("langs", "por+eng").strip() or "por+eng"

    if use_ocr and not OCRMYPDF:
        return (
            jsonify({"error": "OCR pedido mas ocrmypdf não está instalado no PATH"}),
            500,
        )

    job_id = uuid.uuid4().hex[:12]
    job_dir = _CONVERT_BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    if has_upload:
        # Sanity: only honour source_path when it matches the uploaded name,
        # so a stale path from a previous selection can't hijack the save.
        if source_path is not None and source_path.name != Path(f.filename).name:
            source_path = None
        orig_filename = f.filename
        saved_path = job_dir / Path(f.filename).name
        f.save(str(saved_path))
    else:
        orig_filename = source_path.name
        saved_path = job_dir / source_path.name
        shutil.copy2(source_path, saved_path)

    with _CONVERT_LOCK:
        _CONVERT_JOBS[job_id] = {
            "stage": "queued",
            "pct": 0,
            "message": "",
            "error": None,
            "result": None,
            "md_path": None,
            "finished_at": None,
        }
        _CONVERT_QUEUES[job_id] = queue.Queue()

    threading.Thread(
        target=_convert_pipeline,
        args=(job_id, saved_path, orig_filename, use_ocr, force_ocr, langs, source_path),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/convert/status/<job_id>")
def convert_status(job_id):
    def stream():
        q = _CONVERT_QUEUES.get(job_id)
        if q is None:
            yield f"data: {json.dumps({'error': 'job desconhecido'})}\n\n"
            return

        job = _CONVERT_JOBS.get(job_id)
        if job:
            yield (
                f"data: {json.dumps({'stage': job['stage'], 'pct': job['pct'], 'message': job['message']})}\n\n"
            )

        while True:
            try:
                event = q.get(timeout=60.0)
            except queue.Empty:
                yield ": heartbeat\n\n"
                j = _CONVERT_JOBS.get(job_id)
                if not j or j.get("finished_at") is not None:
                    break
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("stage") in ("done", "failed"):
                break

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/convert/result/<job_id>")
def convert_result(job_id):
    job = _CONVERT_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job desconhecido"}), 404
    if job.get("error"):
        return jsonify({"error": job["error"]}), 500
    if not job.get("result"):
        return (
            jsonify({"error": "job ainda em andamento", "stage": job.get("stage")}),
            425,
        )
    return jsonify(job["result"])


@app.route("/convert/download/<job_id>")
def convert_download(job_id):
    """Server-backed download URL — needed because some webviews (WebView2)
    silently drop <a download> clicks pointing to a blob: URL. Serving the
    file with Content-Disposition: attachment triggers the native save
    dialog reliably across pywebview backends and normal browsers."""
    job = _CONVERT_JOBS.get(job_id)
    if not job or not job.get("md_path"):
        return jsonify({"error": "arquivo não encontrado"}), 404
    p = Path(job["md_path"])
    if not p.is_file():
        return jsonify({"error": "arquivo não encontrado"}), 404
    return send_file(
        str(p), as_attachment=True, download_name=p.name, mimetype="text/markdown"
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
# Maintenance — yt-dlp goes stale fast. YouTube rotates its delivery scheme
# every few weeks and an aging yt-dlp starts returning 403 on media URLs
# while metadata still resolves fine. Expose version + one-click upgrade so
# users don't have to re-run the whole installer for it.
# ---------------------------------------------------------------------------


@app.route("/settings/ytdlp", methods=["GET"])
def settings_ytdlp_get():
    try:
        import yt_dlp

        installed = yt_dlp.version.__version__
    except Exception:  # noqa: BLE001
        installed = None
    return jsonify({"installed": installed})


@app.route("/settings/ytdlp/update", methods=["POST"])
def settings_ytdlp_update():
    """Upgrade yt-dlp inside this app's own interpreter environment."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout ao atualizar (5 min)."}), 500
    if proc.returncode != 0:
        return (
            jsonify(
                {
                    "error": "pip falhou ao atualizar yt-dlp",
                    "log": (proc.stderr or proc.stdout)[-1500:],
                }
            ),
            500,
        )

    # Read the new version from a fresh interpreter: the running process
    # still holds the old module in sys.modules.
    version = None
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import yt_dlp; print(yt_dlp.version.__version__)"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if probe.returncode == 0:
            version = probe.stdout.strip()
    except Exception:  # noqa: BLE001
        pass

    return jsonify(
        {
            "ok": True,
            "version": version,
            "restart_required": True,
            "message": (
                "yt-dlp atualizado"
                + (f" para {version}" if version else "")
                + ". Feche e reabra o app para usar a versão nova."
            ),
        }
    )


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
