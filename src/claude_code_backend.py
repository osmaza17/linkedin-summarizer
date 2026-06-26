"""Backend de resumen vía Claude Code en modo *headless* (`claude -p`).

Copia **propia e independiente** del subproyecto de LinkedIn (no importa nada del
proyecto padre de YouTube). En lugar de llamar a la API de Anthropic (que consume
saldo), este backend lanza un subproceso de la **CLI de Claude Code** por cada
resumen, usando la sesión con la que el usuario tiene **suscripción** (login de
`claude`). Así el coste lo cubre la suscripción y no la API.

Lo usa `classifier.classify` cuando `settings.summary_backend == "claude_code"`
(el valor por defecto). Si algo falla aquí (CLI ausente, exit≠0, JSON inválido,
timeout, `is_error`…), se lanza `ClaudeCodeError`; `summarize` lo captura y **cae
al fallback por API**. El resultado (texto del resumen) es exactamente el mismo
que devolvería la API: el prompt es idéntico y solo cambia el transporte.

Puntos clave de la invocación:
- **`ANTHROPIC_API_KEY` se elimina del entorno del subproceso**. `pipeline.load_env`
  carga esa clave con `load_dotenv` en `os.environ`; si el hijo la heredara, Claude
  Code facturaría la **API** en vez de usar la suscripción, anulando el ahorro.
  (También se quita `ANTHROPIC_AUTH_TOKEN` por si acaso.) La clave sigue haciendo
  falta para el fallback por API, así que solo se oculta a este subproceso.
- **Prompt por stdin**, nunca como argumento: el texto del post + el prompt puede
  superar el límite de longitud de la línea de comandos en Windows.
- **cwd temporal y vacío**: evita que Claude Code cargue el `CLAUDE.md` del proyecto
  (ni ningún otro contexto) en la sesión de resumen.
- **Salida JSON** (`--output-format json`): se lee el campo `result` y se valida
  `is_error`, más robusto que el texto plano.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache

logger = logging.getLogger("linkedin_summarizer")

# Tiempo máximo por clasificación (un subproceso `claude -p` por post). Generoso: una
# sesión headless suele tardar segundos, pero dejamos margen.
DEFAULT_TIMEOUT = 600  # segundos


# CREATE_NO_WINDOW: el mismo flag que usa el server de YouTube (`_NO_WINDOW`).
_CREATE_NO_WINDOW = 0x08000000


def _no_window_kwargs() -> dict:
    """kwargs para `subprocess` que impiden CUALQUIER ventana de consola en Windows.

    Comprobado empíricamente: lanzar `claude` **sin** flags desde un proceso sin consola
    abre una ventana por llamada (Windows Terminal / `conhost` como host de consola del
    `claude.exe`). **`CREATE_NO_WINDOW` la suprime del todo** (es lo que ya usa el server
    de YouTube). Se añade además una **consola oculta** (`STARTUPINFO`+`SW_HIDE`) como
    refuerzo. En otros SO devuelve `{}` (sin efecto)."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": _CREATE_NO_WINDOW, "startupinfo": si}


class ClaudeCodeError(Exception):
    """Fallo (recuperable) del backend headless de Claude Code.

    Siempre es recuperable: `classifier.classify` lo captura y recurre al fallback
    por API. No aborta el procesado (a diferencia de `classifier.FatalAPIError`).
    """


@lru_cache(maxsize=1)
def claude_path() -> str | None:
    """Ruta al ejecutable `claude` (cacheada) o None si no está en el PATH.

    En Windows `shutil.which` resuelve el `claude.exe` nativo, invocable
    directamente por `subprocess` sin shell.
    """
    return shutil.which("claude")


def is_available() -> bool:
    """True si la CLI de Claude Code está disponible en el sistema."""
    return claude_path() is not None


def _model_alias(model: str) -> str:
    """Mapea un id de modelo de Anthropic al alias que entiende `claude --model`.

    `claude --model` acepta los alias `haiku`/`sonnet`/`opus` (resuelven a la última
    versión de cada familia). Se reutiliza el mismo `settings.claude_model` que usa
    la API, traduciéndolo al alias. Default: `haiku`.
    """
    m = (model or "").lower()
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    if "opus" in m:
        return "opus"
    return "haiku"


def _clean_env() -> dict:
    """Copia del entorno SIN las credenciales de API (fuerza uso de la suscripción)."""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def summarize(prompt: str, model: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Genera un resumen lanzando `claude -p` (headless, suscripción).

    `prompt` es el prompt ya formateado (idéntico al de la API). `model` es el id de
    Anthropic (p. ej. `claude-haiku-4-5-20251001`), que se mapea a alias de la CLI.
    Devuelve el texto del resumen. Lanza `ClaudeCodeError` ante cualquier fallo
    (para que el caller recurra a la API).
    """
    exe = claude_path()
    if not exe:
        raise ClaudeCodeError("CLI 'claude' no encontrada en el PATH")

    cmd = [exe, "-p", "--model", _model_alias(model), "--output-format", "json"]
    try:
        # cwd temporal y vacío: que Claude Code no cargue el CLAUDE.md del proyecto.
        with tempfile.TemporaryDirectory(prefix="ccsum_") as cwd:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=_clean_env(),
                cwd=cwd,
                timeout=timeout,
                **_no_window_kwargs(),
            )
    except subprocess.TimeoutExpired:
        raise ClaudeCodeError(f"timeout tras {timeout}s")
    except OSError as exc:
        raise ClaudeCodeError(f"no se pudo lanzar la CLI: {exc}")

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise ClaudeCodeError(f"exit {proc.returncode}: {stderr[:300]}")

    out = (proc.stdout or "").strip()
    if not out:
        raise ClaudeCodeError("salida vacía de la CLI")

    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeError(f"salida no es JSON válido: {exc}")

    if data.get("is_error"):
        raise ClaudeCodeError(
            f"Claude Code devolvió error: {data.get('result') or data.get('subtype')}"
        )

    result = (data.get("result") or "").strip()
    if not result:
        raise ClaudeCodeError("la CLI no devolvió texto de resumen")
    return result
