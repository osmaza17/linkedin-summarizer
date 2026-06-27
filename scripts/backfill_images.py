"""Backfill de la imagen de los posts YA recopilados (sin re-cosechar).

Rellena el campo `post_image` de las entradas de `reports/index.json` que aún no lo
tienen, descargando la imagen de contenido desde el **embed público** de LinkedIn
(`pipeline.resolve_post_image`, la misma fuente que el iframe del report; sin login).

Pensado para que los posts antiguos (cosechados antes de existir `post_image`) muestren
también la imagen del post en el feed, en vez del banner del autor. Idempotente: vuelve a
ejecutarse sin problema (solo toca las entradas con `post_image` vacío). Escribe el índice
de forma atómica preservando el resto de campos (`read`, `keep`, `motivo`, …).

Uso:
    venv\\Scripts\\python.exe scripts/backfill_images.py
    venv\\Scripts\\python.exe scripts/backfill_images.py --all     # incluye descartados
    venv\\Scripts\\python.exe scripts/backfill_images.py --delay 0.6
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import pipeline  # noqa: E402  (tras ajustar sys.path)


def _reports_dir() -> Path:
    config = pipeline.load_config()
    settings = config.get("settings", {})
    rd = Path(settings.get("reports_dir", "reports"))
    return rd if rd.is_absolute() else (BASE_DIR / rd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rellena post_image de posts ya guardados")
    parser.add_argument("--all", action="store_true",
                        help="También las entradas descartadas (keep:false); por defecto solo las visibles")
    parser.add_argument("--delay", type=float, default=0.6,
                        help="Pausa en segundos entre peticiones al embed (por defecto 0.6)")
    args = parser.parse_args()

    index_path = _reports_dir() / "index.json"
    if not index_path.exists():
        print(f"No existe {index_path}", file=sys.stderr)
        return 1
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("index.json no es una lista", file=sys.stderr)
        return 1

    # Candidatas: con post_id, sin post_image, y (salvo --all) no descartadas.
    todo = [e for e in data
            if e.get("post_id") and not (e.get("post_image") or "").strip()
            and (args.all or e.get("keep", True))]
    print(f"{len(todo)} entrada(s) sin imagen a procesar (de {len(data)} en el índice).")

    filled = empty = 0
    for i, e in enumerate(todo, 1):
        pid = e["post_id"]
        img = pipeline.resolve_post_image(pid)
        if img:
            e["post_image"] = img
            filled += 1
            print(f"  [{i}/{len(todo)}] {pid}  OK  {img[:80]}")
        else:
            empty += 1
            print(f"  [{i}/{len(todo)}] {pid}  --  (sin imagen de contenido)")
        if i < len(todo) and args.delay > 0:
            time.sleep(args.delay)

    # Escritura atómica (mismo esquema/orden que report_writer.update_index).
    data.sort(key=lambda e: e.get("published_at", ""), reverse=True)
    tmp = index_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(index_path)

    print(f"\nListo: {filled} con imagen, {empty} sin imagen (caen al banner del autor).")
    print(f"Índice actualizado: {index_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
