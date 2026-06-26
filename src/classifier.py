"""Clasificación de posts de LinkedIn con Claude (prototipo independiente).

**Antes** este módulo resumía los posts. Resumir un post de LinkedIn no aporta valor
(ya es texto corto), así que ahora **filtra**: por cada post, Claude decide si presenta
un **proyecto concreto que el usuario podría usar/probar** (herramienta, librería,
framework, app, producto, open source, modelo, demo). Devuelve un veredicto
**estructurado** `{keep, motivo, categoria}`. Solo los `keep:true` se guardan y generan
HTML; los `keep:false` se registran como descartados (para no reevaluarlos) pero no
producen newsletter.

El motor es el mismo que el padre: **Claude Code headless** (`claude -p`, suscripción)
por defecto, con **fallback a la API** si falla. El backend (`claude_code_backend`) es
agnóstico al prompt (devuelve texto), así que se reutiliza tal cual: aquí pedimos JSON y
lo parseamos.

Reintentos ante errores transitorios (429/5xx/conexión) con backoff, y `FatalAPIError`
para los 4xx definitivos (saldo agotado, key inválida…), igual que el padre.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger("linkedin_summarizer")

RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BACKOFF_CAP = 60.0
RETRIABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

# Techo DURO de llamadas concurrentes a la API de Anthropic. Aunque el ajuste
# summary_parallel_workers pida más, nunca se superan 5 en paralelo (requisito fijo).
MAX_PARALLEL_ANTHROPIC = 5

# La respuesta es un JSON corto: no hace falta un presupuesto de tokens grande.
CLASSIFY_MAX_TOKENS = 400


class FatalAPIError(Exception):
    """Error NO reintentable de la API de Claude: aborta el procesado del lote."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _is_billing_error(exc: Exception) -> bool:
    if getattr(exc, "type", None) == "billing_error":
        return True
    msg = str(getattr(exc, "message", "") or exc).lower()
    return "credit balance" in msg


def _fatal_reason(exc: Exception) -> str:
    if _is_billing_error(exc):
        return (
            "Saldo de créditos de la API de Claude agotado. Recarga en "
            "console.anthropic.com o cambia a un modelo más barato (Haiku)."
        )
    status = getattr(exc, "status_code", None)
    reasons = {
        400: "La API de Claude rechazó la petición (400).",
        401: "API key de Claude inválida o ausente (401).",
        403: "La API key no tiene permiso para el modelo (403).",
        404: "Modelo de Claude no encontrado (404).",
    }
    return reasons.get(status, f"Error no reintentable de la API de Claude ({status}).")


PROMPT_TEMPLATE = """Eres un filtro. Analiza el siguiente post de LinkedIn y decide si \
presenta un PROYECTO CONCRETO que un desarrollador podría USAR o PROBAR.

Cuenta como SÍ (keep=true): el post presenta o anuncia una herramienta, librería, \
framework, app, producto, servicio, modelo, repositorio open source o demo CONCRETOS y \
USABLES (algo que se puede instalar, abrir, probar o consultar).

Cuenta como NO (keep=false): opiniones o reflexiones generales, hilos motivacionales, \
ofertas de empleo, comentario de noticias del sector, anuncios de eventos o charlas, \
logros o anécdotas personales, contenido formativo genérico sin un artefacto concreto, \
o cualquier cosa que no sea un proyecto que se pueda usar.

Responde ÚNICAMENTE con un objeto JSON válido, sin ningún texto adicional ni vallas de \
código:
{{"keep": true|false, "motivo": "<una sola frase en español explicando la decisión>", \
"categoria": "<categoría corta si keep=true (p. ej. Herramienta IA, Librería, Open \
source, Producto, App, Modelo); cadena vacía si keep=false>"}}

Datos del post:
- Autor: {author}
- URL: {url}

Texto del post:
{post_text}"""


def _retry_after_seconds(exc: Exception) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _create_message(client: Any, model: str, prompt: str):
    from anthropic import APIConnectionError, APIStatusError, RateLimitError

    api = client.with_options(max_retries=0)
    attempt = 0
    while True:
        try:
            return api.messages.create(
                model=model,
                max_tokens=CLASSIFY_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        except (RateLimitError, APIStatusError, APIConnectionError) as exc:
            status = getattr(exc, "status_code", None)
            if status is not None and status not in RETRIABLE_STATUS:
                raise FatalAPIError(_fatal_reason(exc))
            attempt += 1
            if attempt > RATE_LIMIT_MAX_RETRIES:
                raise
            retry_after = _retry_after_seconds(exc)
            if retry_after is None:
                retry_after = min(2 ** attempt + random.uniform(0, 1), RATE_LIMIT_BACKOFF_CAP)
            logger.warning(
                "[REINTENTANDO] Error transitorio (%s, intento %d/%d). Reintento en %.1fs",
                status if status is not None else "conexión",
                attempt, RATE_LIMIT_MAX_RETRIES, retry_after,
            )
            time.sleep(retry_after)


def _parse_verdict(raw: str | None) -> dict[str, Any]:
    """Parsea el JSON del veredicto de forma tolerante.

    Devuelve `{"keep": bool|None, "motivo": str, "categoria": str}`. `keep=None`
    significa **indeciso** (no se pudo parsear): el pipeline lo trata como "ni guardar
    ni descartar" para reintentarlo en la próxima cosecha (no perder un post bueno por
    un fallo puntual)."""
    if not raw or not raw.strip():
        return {"keep": None, "motivo": "(respuesta vacía)", "categoria": ""}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"keep": None, "motivo": "(la respuesta no contenía JSON)", "categoria": ""}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {"keep": None, "motivo": "(JSON inválido)", "categoria": ""}
    keep = data.get("keep")
    if isinstance(keep, str):
        keep = keep.strip().lower() in ("true", "sí", "si", "yes", "1")
    elif not isinstance(keep, bool):
        keep = None
    return {
        "keep": keep,
        "motivo": (data.get("motivo") or "").strip(),
        "categoria": (data.get("categoria") or "").strip(),
    }


def classify(post: dict[str, Any], client: Any, model: str, backend: str = "api") -> dict[str, Any]:
    """Clasifica un post de LinkedIn. Devuelve `{keep, motivo, categoria}`.

    `backend` elige el transporte: `"claude_code"` lanza una sesión **headless** de la
    CLI de Claude Code (usa la **suscripción**, no la API → no gasta saldo); ante
    cualquier fallo del headless (`ClaudeCodeError`) se **cae al fallback por API**.
    `"api"` va directo a la API. El prompt es idéntico en ambos. Propaga `FatalAPIError`.
    """
    text = (post.get("text") or "").strip()
    if not text:
        return {"keep": False, "motivo": "El post no tenía texto.", "categoria": ""}
    prompt = PROMPT_TEMPLATE.format(
        author=post.get("author", ""),
        url=post.get("url", ""),
        post_text=text,
    )

    raw: str | None = None
    # Backend por defecto: Claude Code headless (suscripción). Si falla, no aborta:
    # registra el motivo y continúa al fallback por API de abajo.
    if backend == "claude_code":
        import claude_code_backend

        try:
            raw = claude_code_backend.summarize(prompt, model)
        except claude_code_backend.ClaudeCodeError as exc:
            logger.warning(
                "[CLAUDE_CODE_FALLBACK] La sesión headless de Claude Code falló "
                "(%s); recurriendo a la API para el post de '%s'.",
                exc, post.get("author", ""),
            )
            raw = None

    if raw is None:
        try:
            response = _create_message(client, model, prompt)
            parts = [
                block.text for block in response.content
                if getattr(block, "type", None) == "text"
            ]
            raw = "\n".join(parts).strip()
        except FatalAPIError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error al clasificar post: %s", exc)
            return {"keep": None, "motivo": f"(error al clasificar: {exc})", "categoria": ""}

    return _parse_verdict(raw)


def classify_batch(
    posts: list[dict[str, Any]], client: Any, model: str, workers: int = 3,
    backend: str = "api",
) -> dict[str, dict[str, Any]]:
    """Clasifica varios posts y devuelve `{post_id: {keep, motivo, categoria}}`.

    `backend` se propaga a `classify`. Se paraleliza si el backend es Claude Code
    headless (cada `claude -p` es un subproceso independiente) **o** con Haiku por API.
    **Techo duro de 5 concurrentes** (`MAX_PARALLEL_ANTHROPIC`) en ambos casos. Nunca
    lanza salvo `FatalAPIError` (que aborta el lote)."""
    results: dict[str, dict[str, Any]] = {}
    workers = max(1, min(workers, MAX_PARALLEL_ANTHROPIC))  # techo duro de 5 concurrentes
    parallel = (
        (backend == "claude_code" or "haiku" in model.lower())
        and workers > 1
        and len(posts) > 1
    )

    if parallel:
        logger.info("Clasificando %d posts en paralelo (%d workers, backend=%s)",
                    len(posts), workers, backend)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(classify, p, client, model, backend): p for p in posts}
            done = 0
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    results[p["post_id"]] = fut.result()
                except FatalAPIError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    results[p["post_id"]] = {"keep": None, "motivo": f"(error: {exc})", "categoria": ""}
                done += 1
                verdict = results[p["post_id"]].get("keep")
                logger.info("[%d/%d] Clasificado (keep=%s): %s",
                            done, len(posts), verdict, p.get("title", ""))
    else:
        for idx, p in enumerate(posts, start=1):
            logger.info("[%d/%d] Clasificando: %s (%s)", idx, len(posts),
                        p.get("title", ""), p.get("author", ""))
            results[p["post_id"]] = classify(p, client, model, backend)

    return results
