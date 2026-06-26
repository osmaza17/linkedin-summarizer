"""Escribe una newsletter HTML autocontenida por post + un índice (prototipo).

Independiente del `report_writer` del proyecto padre (aunque comparte estilo). Cada
post **seleccionado por el filtro** genera `reports/<slug>.html`: página que muestra
**solo el embed oficial de LinkedIn** del post, con una **altura proporcional a la
longitud del texto** del post (para que quepa entero). Sin resumen, sin abstract, sin
texto original aparte ni etiqueta de motivo (el `motivo`/`categoria` se siguen guardando
en el índice para auditar, pero no se pintan). El paso de IA ahora **clasifica**, no
resume — ver `classifier.py`.

Todas las fichas se catalogan en `reports/index.json` (upsert por `post_id`, nunca se
borra → historial permanente). El índice guarda **tanto los guardados** (`keep:true`,
con `html`) **como los descartados** (`keep:false`, sin `html`): estos últimos solo
sirven para deduplicar (no reevaluarlos) y el feed los oculta.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("linkedin_summarizer")

INDEX_NAME = "index.json"
DEFAULT_COLOR = "#0A66C2"  # azul LinkedIn


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Estilo (autocontenido, claro/oscuro) ───────────────────────────────────────

_STYLE = """
:root,[data-theme="light"]{
  --bg:#FBFBF9;--surface:#FFFFFF;--ink:#16181D;--muted:#6B7079;--faint:#9AA0A8;
  --line:#ECEAE4;--accent:#0A66C2;
  --accent-soft:color-mix(in srgb,var(--accent) 13%,var(--surface));
  --accent-ink:color-mix(in srgb,var(--accent) 76%,var(--ink));
}
[data-theme="dark"]{
  --bg:#0D1117;--surface:#161B22;--ink:#E6EDF3;--muted:#9198A1;--faint:#656D76;
  --line:#2A313C;--accent:#4DA3FF;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{min-height:100%}
body{font-family:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);
  color:var(--ink);-webkit-font-smoothing:antialiased;transition:background .25s,color .25s}
.page{max-width:820px;margin:0 auto;padding:clamp(36px,7vh,84px) clamp(20px,6vw,56px) 64px}
.embed-wrap{margin:0;border:1px solid var(--line);border-radius:16px;overflow:hidden;
  background:var(--surface)}
.embed-wrap iframe{display:block;width:100%;border:0}
.back-btn{position:fixed;top:22px;left:22px;z-index:30;display:inline-flex;align-items:center;gap:6px;
  font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);
  background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:6px 12px;
  cursor:pointer;text-decoration:none;font-family:inherit}
.back-btn:hover{color:var(--accent-ink);background:var(--accent-soft)}
.theme-btn{position:fixed;top:22px;right:22px;z-index:30;width:32px;height:32px;border-radius:50%;
  border:1px solid var(--line);background:var(--surface);color:var(--ink);cursor:pointer;font-size:14px;
  display:grid;place-items:center}
.theme-btn:hover{background:var(--accent-soft)}
@media print{.back-btn,.theme-btn{display:none!important}}
"""

_THEME_INIT = """<script>(function(){var t=localStorage.getItem('li-theme')||
(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
document.documentElement.setAttribute('data-theme',t);})();</script>"""

_JS = """<script>
function toggleTheme(){var d=document.documentElement;
var n=d.getAttribute('data-theme')==='dark'?'light':'dark';
d.setAttribute('data-theme',n);localStorage.setItem('li-theme',n);
document.getElementById('theme-btn').textContent=n==='dark'?'☀':'🌙';}
document.getElementById('theme-btn').textContent=
document.documentElement.getAttribute('data-theme')==='dark'?'☀':'🌙';
</script>"""


def _build_html(post: dict[str, Any]) -> str:
    title = _escape_html(post.get("title", "(post)"))
    color = post.get("group_color") or DEFAULT_COLOR
    pid = _escape_html(post.get("post_id", ""))

    # Altura del embed PROPORCIONAL a la longitud del post original. Antes se mostraba el
    # texto del post debajo; ahora la página es SOLO el embed, así que lo dimensionamos
    # según cuánto texto tiene el post para que quepa entero sin recortarse. Se estiman
    # "líneas visuales" (~80 caracteres por línea + los saltos de línea explícitos) y se
    # acota con un mínimo y un máximo de cordura.
    text = post.get("text", "") or ""
    vlines = sum(max(1, -(-len(seg) // 80)) for seg in text.split("\n")) if text else 1
    embed_h = max(360, min(200 + vlines * 24, 1600))

    if pid:
        embed_html = (
            f'<div class="embed-wrap"><iframe src="https://www.linkedin.com/embed/feed/update/'
            f'urn:li:activity:{pid}" style="height:{embed_h}px" allowfullscreen '
            f'title="Post de LinkedIn" '
            f'sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox">'
            f'</iframe></div>'
        )
    else:
        embed_html = '<p style="color:var(--muted)">No se pudo construir el embed (sin id de post).</p>'

    return f"""<!doctype html>
<html lang="es" style="--accent:{color}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — LinkedIn</title>
{_THEME_INIT}
<style>{_STYLE}</style>
</head>
<body data-pid="{pid}">
<a class="back-btn" href="/" onclick="if(location.protocol==='file:'){{location.href='http://localhost:3002/';return false;}}">‹ Feed</a>
<button class="theme-btn" id="theme-btn" onclick="toggleTheme()" title="Cambiar tema">🌙</button>
<main class="page">
{embed_html}
</main>
{_JS}
</body>
</html>
"""


# ── Índice + escritura ──────────────────────────────────────────────────────────

def _index_path(reports_dir: str) -> Path:
    return Path(reports_dir) / INDEX_NAME


def _load_index(reports_dir: str) -> list[dict]:
    path = _index_path(reports_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        logger.warning("index.json ilegible; se reconstruye")
        return []


def load_index_map(reports_dir: str) -> dict[str, dict]:
    return {e["post_id"]: e for e in _load_index(reports_dir) if e.get("post_id")}


def _slugify(title: str, fallback: str = "") -> str:
    t = unicodedata.normalize("NFKD", title or "")
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:80].strip("-")
    return t or fallback


def resolve_filename(post: dict, used_by: dict[str, str], existing: dict[str, dict]) -> str:
    pid = post.get("post_id", "")
    base = _slugify(post.get("title", ""), fallback=pid or "post")
    prev = (existing.get(pid) or {}).get("html")
    if prev and prev.endswith(".html"):
        prev_base = re.sub(r"-\d+$", "", prev[:-5])
        if prev_base == base:
            used_by[prev] = pid
            return prev
    cand = f"{base}.html"
    n = 1
    while cand in used_by and used_by[cand] != pid:
        n += 1
        cand = f"{base}-{n}.html"
    used_by[cand] = pid
    return cand


def write_post_newsletter(post: dict, reports_dir: str, filename: str) -> Path:
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(_build_html(post), encoding="utf-8")
    logger.info("Newsletter escrita en %s", out_path.resolve())
    return out_path


def index_entry(post: dict, filename: str, keep: bool = True) -> dict:
    """Ficha de un post **guardado** (keep:true) para el índice, con su `html`."""
    return {
        "post_id": post.get("post_id", ""),
        "title": post.get("title", ""),
        "author": post.get("author", ""),
        "author_url": post.get("author_url", ""),
        "author_avatar": post.get("author_avatar", ""),
        "group_name": post.get("group_name") or "LinkedIn",
        "group_color": post.get("group_color") or DEFAULT_COLOR,
        "published_at": post.get("published_at", ""),
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "url": post.get("url", ""),
        "html": filename,
        "keep": keep,
        "motivo": post.get("motivo", ""),
        "categoria": post.get("categoria", ""),
    }


def rejected_entry(post: dict) -> dict:
    """Ficha mínima de un post **descartado** por el filtro (keep:false, sin html).

    Se guarda en el índice solo para deduplicar (no reevaluar/re-pagar Haiku); el feed
    la oculta. Conserva el motivo por si el usuario quiere auditar los descartes."""
    return {
        "post_id": post.get("post_id", ""),
        "title": post.get("title", ""),
        "author": post.get("author", ""),
        "group_name": post.get("group_name") or "LinkedIn",
        "group_color": post.get("group_color") or DEFAULT_COLOR,
        "published_at": post.get("published_at", ""),
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "url": post.get("url", ""),
        "keep": False,
        "motivo": post.get("motivo", ""),
        "categoria": post.get("categoria", ""),
    }


def update_index(reports_dir: str, entries: list[dict]) -> Path:
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_id: dict[str, dict] = {e["post_id"]: e for e in _load_index(reports_dir) if e.get("post_id")}
    for entry in entries:
        pid = entry.get("post_id")
        if pid:
            prev = by_id.get(pid)
            if prev and "read" in prev and "read" not in entry:
                entry["read"] = prev["read"]
            by_id[pid] = entry
    merged = sorted(by_id.values(), key=lambda e: e.get("published_at", ""), reverse=True)
    path = _index_path(reports_dir)
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Índice actualizado (%d posts) en %s", len(merged), path.resolve())
    return path
