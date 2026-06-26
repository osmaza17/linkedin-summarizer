"""Pipeline de posts de LinkedIn: normaliza → resume → escribe newsletter + índice.

Equivalente a `process_videos` del padre, pero **sin transcripción** (el texto ya
viene cosechado). Lo usan el servidor (worker de la cola) y la CLI (`python pipeline.py
--file posts.json`) para pruebas.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from classifier import FatalAPIError, classify_batch
from report_writer import (
    index_entry,
    load_index_map,
    rejected_entry,
    resolve_filename,
    update_index,
    write_post_newsletter,
)

logger = logging.getLogger("linkedin_summarizer")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_COLOR = "#0A66C2"

# Patrón del id de actividad de LinkedIn (urn:li:activity:NNNN o activity-NNNN…).
_ACTIVITY_RE = re.compile(r"(\d{10,25})")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"linkedin_groups": [], "settings": {}}


def load_env() -> None:
    """Carga el `.env` propio (app independiente). Debe contener `ANTHROPIC_API_KEY`."""
    local_env = BASE_DIR / ".env"
    if local_env.exists():
        load_dotenv(local_env)


def extract_activity_id(value: str) -> str | None:
    """Saca el id de actividad de una URL/URN de LinkedIn. None si no encuentra."""
    if not value:
        return None
    m = _ACTIVITY_RE.search(value)
    return m.group(1) if m else None


def date_from_post_id(post_id: str) -> str:
    """Fecha local (`YYYY-MM-DD`) derivada del id de actividad de LinkedIn.

    Los ids de actividad de LinkedIn codifican el timestamp de creación en sus bits
    altos: los primeros 41 bits (id >> 22) son el Unix time en milisegundos. Es la
    forma **fiable** de datar un post (no depende del DOM ni de la fecha relativa que
    muestra LinkedIn). Devuelve "" si el id no parece válido."""
    try:
        ts_ms = int(post_id) >> 22
    except (TypeError, ValueError):
        return ""
    if ts_ms < 1_000_000_000_000 or ts_ms > 5_000_000_000_000:  # ~2001..~2128, cordura
        return ""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone().date().isoformat()


def _derive_title(text: str, author: str) -> str:
    """Título legible de un post (no tienen título): 1ª línea/frase, recortada."""
    text = (text or "").strip()
    if not text:
        return f"Post de {author}" if author else "Post de LinkedIn"
    first = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
    # Corta en el primer punto/emoji-final si la línea es muy larga.
    if len(first) > 90:
        cut = first[:90]
        dot = cut.rfind(". ")
        first = (cut[: dot + 1] if dot > 40 else cut).rstrip() + "…"
    return first or (f"Post de {author}" if author else "Post de LinkedIn")


def normalize_post(raw: dict) -> dict | None:
    """Normaliza un post cosechado (de la extensión) al dict canónico interno.

    Requiere al menos texto y un id/URL del que extraer el `post_id`. Devuelve None
    si no se puede identificar el post (sin id ni URL útil)."""
    text = (raw.get("text") or "").strip()
    post_id = (raw.get("post_id") or "").strip() or extract_activity_id(raw.get("url", "")) \
        or extract_activity_id(raw.get("urn", ""))
    if not post_id or not text:
        return None
    url = (raw.get("url") or "").strip() or f"https://www.linkedin.com/feed/update/urn:li:activity:{post_id}/"
    author = (raw.get("author") or "").strip() or "(autor desconocido)"
    # La fecha del id de actividad es fiable (timestamp embebido); la del DOM
    # (fecha relativa) es solo un respaldo si el id no se pudiera datar.
    published_at = date_from_post_id(post_id) or (raw.get("published_at") or "").strip()
    return {
        "post_id": post_id,
        "author": author,
        "author_url": (raw.get("author_url") or "").strip(),
        "author_avatar": (raw.get("author_avatar") or "").strip(),
        "group_name": (raw.get("group_name") or "LinkedIn").strip(),
        "group_color": (raw.get("group_color") or DEFAULT_COLOR).strip(),
        "url": url,
        "published_at": published_at,
        "text": text,
        "title": _derive_title(text, author),
    }


def process_posts(raw_posts: list[dict], settings: dict, client, model: str,
                  reports_path: Path) -> int:
    """Clasifica los posts cosechados y escribe newsletters solo para los que interesan.

    Por cada post, Claude decide si es un **proyecto que el usuario podría usar**
    (`keep`). Los `keep:true` generan HTML (embed + texto + motivo); los `keep:false` se
    registran como **descartados** en el índice (para no reevaluarlos/re-pagarlos) pero
    no producen newsletter; los indecisos (`keep is None`, fallo puntual de
    clasificación) **ni se guardan ni se descartan** (se reintentan en la próxima
    cosecha). Deduplica contra el índice (kept **y** rejected). Devuelve cuántas
    newsletters (kept) se generaron. Propaga `FatalAPIError`."""
    existing = load_index_map(str(reports_path))
    posts: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    for raw in raw_posts:
        p = normalize_post(raw)
        if not p:
            logger.info("Post descartado (sin id o sin texto)")
            continue
        if p["post_id"] in existing or p["post_id"] in seen:
            skipped += 1
            continue
        seen.add(p["post_id"])
        posts.append(p)
    if skipped:
        logger.info("Omitidos %d post(s) ya clasificados (en el índice)", skipped)
    if not posts:
        logger.info("No hay posts nuevos que procesar")
        return 0

    logger.info("Posts nuevos a clasificar: %d", len(posts))
    workers = max(1, int(settings.get("summary_parallel_workers", 3)))
    backend = settings.get("summary_backend", "claude_code")
    verdicts = classify_batch(posts, client, model, workers=workers, backend=backend)

    used_by: dict[str, str] = {
        e["html"]: e["post_id"] for e in existing.values() if e.get("html")
    }
    entries: list[dict] = []
    kept = rejected = undecided = 0
    for p in posts:
        verdict = verdicts.get(p["post_id"]) or {"keep": None, "motivo": "", "categoria": ""}
        keep = verdict.get("keep")
        p["motivo"] = verdict.get("motivo", "")
        p["categoria"] = verdict.get("categoria", "")
        if keep is None:
            # Indeciso (fallo de clasificación): no se persiste → reintento futuro.
            undecided += 1
            logger.warning("Post indeciso (se reintentará): %s", p["title"])
            continue
        if not keep:
            # Descartado: entrada en el índice (sin html) para no reevaluarlo, sin HTML.
            rejected += 1
            entries.append(rejected_entry(p))
            continue
        filename = resolve_filename(p, used_by, existing)
        write_post_newsletter(p, str(reports_path), filename)
        entries.append(index_entry(p, filename))
        kept += 1

    logger.info("Clasificación: %d guardados, %d descartados, %d indecisos",
                kept, rejected, undecided)
    if not entries:
        logger.info("Sin entradas que persistir")
        return kept
    update_index(str(reports_path), entries)
    logger.info("Listo. %d newsletter(s) generada(s).", kept)
    return kept


def get_client_and_model(settings: dict):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Falta ANTHROPIC_API_KEY (.env del prototipo o del proyecto padre).")
    from anthropic import Anthropic
    model = settings.get("claude_model", "claude-haiku-4-5-20251001")
    return Anthropic(api_key=api_key), model


def _reports_path(settings: dict) -> Path:
    reports_dir = settings.get("reports_dir", "reports")
    p = Path(reports_dir)
    return p if p.is_absolute() else (BASE_DIR / p)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    load_env()
    parser = argparse.ArgumentParser(description="Resumen de posts de LinkedIn (prototipo)")
    parser.add_argument("--file", required=True,
                        help="JSON con una lista de posts cosechados (o {posts:[...]})")
    args = parser.parse_args()

    config = load_config()
    settings = config.get("settings", {})
    try:
        client, model = get_client_and_model(settings)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 0

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    raw_posts = data.get("posts", data) if isinstance(data, dict) else data
    if not isinstance(raw_posts, list):
        logger.error("El archivo no contiene una lista de posts")
        return 0

    try:
        process_posts(raw_posts, settings, client, model, _reports_path(settings))
    except FatalAPIError as exc:
        logger.error("[EJECUCION_DETENIDA] %s", exc.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
