"""Servidor local de LinkedIn Summarizer (Flask, puerto 3002).

App **independiente** (ya no es subproyecto de YouTube). Sirve:
- El **dashboard React** (web/dist) para leer las newsletters y configurar.
- La API que usa la **extensión de cosecha**: lista de personas a cosechar y
  recepción de posts (`/api/summarize-post`), que un worker en proceso **clasifica**
  (¿es un proyecto que interesa?) y archiva con el pipeline.

Solo uso local: se enlaza a 127.0.0.1:3002 y rechaza Host/Origin no locales (salvo
el origen `chrome-extension://…` de la extensión de cosecha).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import pipeline
from classifier import FatalAPIError

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
WEB_DIR = BASE_DIR / "web"
DIST_DIR = WEB_DIR / "dist"

HOST = "127.0.0.1"
PORT = 3002
_ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
# Orígenes permitidos: solo el propio dashboard (mismo origen, 3002). La app es
# independiente; ya no hay dashboard externo que consuma esta API (antes el de YouTube
# en 3000). La extensión de cosecha (`chrome-extension://…`) se permite aparte. El Host
# sigue ligado a 127.0.0.1, así que la superficie expuesta es solo local.
_ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

CLAUDE_MODELS = [
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (rápido y barato)"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (más calidad)"},
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 (máxima calidad)"},
]

# Motor de resumen: por suscripción (Claude Code headless) o por API directa.
SUMMARY_BACKENDS = [
    {"id": "claude_code", "label": "Claude Code (suscripción)"},
    {"id": "api", "label": "API de Anthropic"},
]
_VALID_BACKENDS = {b["id"] for b in SUMMARY_BACKENDS}
DEFAULT_BACKEND = "claude_code"

logger = logging.getLogger("linkedin_summarizer")

app = Flask(__name__, static_folder=None)

# Cola de posts cosechados pendientes de resumir. El worker los procesa en lotes:
# al recibir uno, espera una ventana corta para agrupar la ráfaga de una cosecha y
# resume todos juntos (una sola tanda con Claude). Estado por post para sondeo.
_post_queue: "queue.Queue[dict]" = queue.Queue()
_post_status: dict[str, str] = {}  # post_id -> queued|processing|done|error|skipped
_status_lock = threading.Lock()
_worker_started = False
_worker_lock = threading.Lock()
# Serializa TODO acceso de escritura a index.json (el worker al archivar un lote y el
# endpoint set_read). Sin esto, un "marcar leído" durante un archivado podían pisarse
# (read-modify-write concurrente → actualización perdida o JSON corrupto).
_index_lock = threading.RLock()
_busy = False  # True mientras el worker está resumiendo un lote
_last_run: dict = {"count": 0, "at": "", "error": ""}

BATCH_WINDOW_SECONDS = 4.0  # espera para agrupar una ráfaga de cosecha


# ── Config ──────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"linkedin_groups": [], "settings": {}}


def _save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def _reports_path() -> Path:
    settings = _load_config().get("settings", {})
    return pipeline._reports_path(settings)


# ── Seguridad: solo local (+ extensión) ──────────────────────────────────────────

@app.before_request
def _guard_local_only():
    host = (request.host or "").lower()
    if host not in _ALLOWED_HOSTS:
        return jsonify({"error": "forbidden host"}), 403
    origin = request.headers.get("Origin")
    if (origin and origin.lower() not in _ALLOWED_ORIGINS
            and not origin.startswith("chrome-extension://")):
        return jsonify({"error": "forbidden origin"}), 403
    if request.method == "OPTIONS":
        return ("", 204)
    return None


@app.after_request
def _cors(resp):
    origin = request.headers.get("Origin")
    # CORS solo para la extensión de cosecha (chrome-extension://…). El dashboard es
    # mismo origen (3002), así que no necesita cabeceras CORS.
    if origin and origin.startswith("chrome-extension://"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ── API: config ──────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    config = _load_config()
    settings = config.get("settings", {})
    return jsonify({
        "linkedin_groups": config.get("linkedin_groups", []),
        "settings": {
            "claude_model": settings.get("claude_model", CLAUDE_MODELS[0]["id"]),
            "summary_parallel_workers": settings.get("summary_parallel_workers", 3),
            "days": settings.get("days", 1),
            "summary_backend": settings.get("summary_backend", DEFAULT_BACKEND),
        },
        "meta": {"claude_models": CLAUDE_MODELS, "summary_backends": SUMMARY_BACKENDS},
    })


@app.post("/api/config")
def save_config():
    data = request.get_json(force=True, silent=True) or {}
    s = data.get("settings", {})
    try:
        workers = max(1, int(s.get("summary_parallel_workers", 3)))
        days = max(1, int(s.get("days", 1)))
    except (ValueError, TypeError):
        return jsonify({"error": "Workers y días deben ser enteros."}), 400

    groups = []
    for g in data.get("linkedin_groups", []):
        gname = (g.get("name") or "").strip()
        gcolor = (g.get("color") or "#0A66C2").strip()
        people = []
        for p in g.get("people", []):
            name = (p.get("name") or "").strip()
            url = (p.get("profile_url") or "").strip()
            pid = (p.get("public_id") or "").strip() or _public_id_from_url(url)
            if name or pid or url:
                people.append({
                    "name": name or pid,
                    "public_id": pid,
                    "profile_url": url or (f"https://www.linkedin.com/in/{pid}/" if pid else ""),
                    "avatar": (p.get("avatar") or "").strip(),
                })
        if gname:
            groups.append({"name": gname, "color": gcolor, "people": people})

    backend = (s.get("summary_backend") or DEFAULT_BACKEND).strip()
    if backend not in _VALID_BACKENDS:
        backend = DEFAULT_BACKEND

    config = _load_config()
    config["linkedin_groups"] = groups
    config["settings"] = {
        "claude_model": (s.get("claude_model") or CLAUDE_MODELS[0]["id"]).strip(),
        "summary_parallel_workers": workers,
        "days": days,
        "summary_backend": backend,
        "reports_dir": config.get("settings", {}).get("reports_dir", "reports"),
    }
    _save_config(config)
    return jsonify({"ok": True})


def _public_id_from_url(url: str) -> str:
    m = re.search(r"/in/([^/?#]+)", url or "")
    return m.group(1) if m else ""


@app.get("/api/linkedin-people")
def linkedin_people():
    """Lista aplanada de personas a cosechar (la consume la extensión).

    Cada persona lleva su grupo y color para etiquetar los posts cosechados, y la URL
    de actividad reciente que la extensión abrirá."""
    config = _load_config()
    settings = config.get("settings", {})
    people = []
    for g in config.get("linkedin_groups", []):
        for p in g.get("people", []):
            pid = (p.get("public_id") or "").strip() or _public_id_from_url(p.get("profile_url", ""))
            if not pid:
                continue
            people.append({
                "name": p.get("name") or pid,
                "public_id": pid,
                "profile_url": p.get("profile_url") or f"https://www.linkedin.com/in/{pid}/",
                "activity_url": f"https://www.linkedin.com/in/{pid}/recent-activity/all/",
                "group_name": g.get("name", "LinkedIn"),
                "group_color": g.get("color", "#0A66C2"),
            })
    return jsonify({"people": people, "days": settings.get("days", 1)})


# ── API: recepción y procesado de posts ──────────────────────────────────────────

def _client_model():
    settings = _load_config().get("settings", {})
    return pipeline.get_client_and_model(settings)


def _post_worker() -> None:
    """Drena la cola de posts por lotes y los resume con el pipeline (en proceso)."""
    global _busy, _last_run
    pipeline.load_env()
    while True:
        first = _post_queue.get()
        batch = [first]
        # Agrupa la ráfaga de una cosecha: recoge lo que llegue durante una ventana.
        deadline = time.time() + BATCH_WINDOW_SECONDS
        while time.time() < deadline:
            try:
                batch.append(_post_queue.get(timeout=max(0.1, deadline - time.time())))
            except queue.Empty:
                break
        ids = [p.get("post_id") for p in batch if p.get("post_id")]
        with _status_lock:
            _busy = True
            for pid in ids:
                _post_status[pid] = "processing"
        try:
            config = _load_config()
            settings = config.get("settings", {})
            client, model = pipeline.get_client_and_model(settings)
            with _index_lock:
                n = pipeline.process_posts(batch, settings, client, model, _reports_path())
            with _status_lock:
                for pid in ids:
                    _post_status[pid] = "done"
                _last_run = {"count": n, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "error": ""}
        except FatalAPIError as exc:
            logger.error("[EJECUCION_DETENIDA] %s", exc.reason)
            with _status_lock:
                for pid in ids:
                    _post_status[pid] = "error"
                _last_run = {"count": 0, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "error": exc.reason}
        except Exception as exc:  # noqa: BLE001 - un lote no debe tumbar el worker
            logger.warning("Worker: error procesando lote: %s", exc)
            with _status_lock:
                for pid in ids:
                    _post_status[pid] = "error"
                _last_run = {"count": 0, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "error": str(exc)}
        finally:
            with _status_lock:
                _busy = False
            for _ in batch:
                _post_queue.task_done()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_post_worker, daemon=True, name="post-worker").start()


@app.post("/api/summarize-post")
def summarize_post():
    """Encola un post cosechado. La extensión lo manda durante la cosecha.

    Body: {post_id?, url, author, author_url?, author_avatar?, text, published_at?,
    group_name?, group_color?}. Deduplica contra el índice y contra la cola."""
    _ensure_worker()
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    post_id = (data.get("post_id") or "").strip() \
        or (pipeline.extract_activity_id(data.get("url", "")) or "")
    if not text:
        return jsonify({"error": "Falta 'text'."}), 400
    if not post_id:
        return jsonify({"error": "No se pudo identificar el post (sin id ni URL)."}), 400

    existing = pipeline.load_index_map(str(_reports_path()))
    if post_id in existing:
        with _status_lock:
            _post_status[post_id] = "skipped"
        return jsonify({"queued": False, "duplicate": True, "post_id": post_id})
    with _status_lock:
        if _post_status.get(post_id) in ("queued", "processing"):
            return jsonify({"queued": True, "duplicate": True, "post_id": post_id})
        _post_status[post_id] = "queued"
    data["post_id"] = post_id
    _post_queue.put(data)
    return jsonify({"queued": True, "post_id": post_id, "position": _post_queue.qsize()})


@app.get("/api/summarize-post")
def summarize_post_status():
    post_id = (request.args.get("post_id") or "").strip()
    with _status_lock:
        status = _post_status.get(post_id)
        busy = _busy
    return jsonify({"post_id": post_id, "status": status, "busy": busy, "last_run": _last_run})


@app.get("/api/status")
def status():
    with _status_lock:
        return jsonify({"busy": _busy, "queue": _post_queue.qsize(), "last_run": _last_run})


# ── API: feed (newsletters) ───────────────────────────────────────────────────────

@app.get("/api/newsletters")
def list_newsletters():
    index_path = _reports_path() / "index.json"
    entries: list = []
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # Oculta los descartados por el filtro (keep:false). Las entradas
                # antiguas sin campo `keep` se siguen mostrando (keep ausente → True).
                entries = [e for e in data if e.get("keep", True)]
        except (OSError, ValueError):
            logger.warning("index.json ilegible")
    groups: dict[str, str] = {}
    for e in entries:
        name = e.get("group_name") or "LinkedIn"
        if name not in groups:
            groups[name] = e.get("group_color", "#0A66C2")
    return jsonify({
        "newsletters": entries,
        "groups": [{"name": n, "color": c} for n, c in sorted(groups.items())],
    })


@app.post("/api/newsletters/<post_id>/read")
def set_read(post_id: str):
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", post_id):
        return jsonify({"error": "post_id no válido."}), 400
    read = bool((request.get_json(silent=True) or {}).get("read", True))
    index_path = _reports_path() / "index.json"
    with _index_lock:  # evita pisar una escritura del worker (read-modify-write)
        try:
            entries = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError
        except (OSError, ValueError):
            return jsonify({"error": "index.json no disponible."}), 500
        entry = next((e for e in entries if e.get("post_id") == post_id), None)
        if entry is None:
            return jsonify({"error": "No encontrada."}), 404
        entry["read"] = read
        index_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True, "read": read})


@app.get("/reports/<path:name>")
def serve_report(name: str):
    if not name.endswith(".html"):
        return ("Not found", 404)
    rdir = _reports_path()
    if not (rdir / name).is_file():
        return ("Not found", 404)
    return send_from_directory(rdir, name)


@app.post("/api/shutdown")
def shutdown():
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


# ── Dashboard React (web/dist) ──────────────────────────────────────────────────

@app.get("/")
def index():
    if not (DIST_DIR / "index.html").exists():
        return ("Falta web/dist. Compila el dashboard con: cd web && npm run build", 500)
    return send_from_directory(DIST_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path: str):
    target = DIST_DIR / path
    if target.is_file():
        return send_from_directory(DIST_DIR, path)
    return send_from_directory(DIST_DIR, "index.html")  # fallback SPA


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    _ensure_worker()
    print(f"\n  Feed de LinkedIn en: http://localhost:{PORT}")
    print("  (Ctrl+C para detener)\n")
    app.run(host=HOST, port=PORT, threaded=True)


if __name__ == "__main__":
    main()
