"""Prueba de carga del prototipo SIN depender de LinkedIn.

Genera N posts sintéticos (con post_id que codifica una fecha reciente, como los
reales de LinkedIn) y los manda a `POST /api/summarize-post` del servidor local. Sirve
para estresar la cola, el worker por lotes, la deduplicación, el índice y el feed —
todo menos la cosecha del DOM real.

⚠️ Cada post sintético genera una llamada real a Claude (coste en tokens). Los marca
con el grupo "TEST" para que los distingas y borres luego.

Uso:
    ../venv/Scripts/python.exe scripts/loadtest.py --n 30
    ../venv/Scripts/python.exe scripts/loadtest.py --n 30 --server http://localhost:3002
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request

TOPICS = [
    ("un agente que revisa PRs con IA", "Detecta bugs y sugiere refactors en GitHub."),
    ("un RAG sobre documentación interna", "Responde preguntas citando la fuente exacta."),
    ("fine-tuning barato de un modelo pequeño", "Iguala a uno grande en una tarea concreta."),
    ("una demo de voz en tiempo real", "Latencia por debajo de 300 ms con modelos abiertos."),
    ("automatizar la bandeja de entrada", "Triaje, resúmenes y borradores automáticos."),
    ("evaluación de LLMs con jueces IA", "Un pipeline reproducible para comparar modelos."),
    ("scraping ético con límites de tasa", "Cómo respetar a los sitios y no quemar la IP."),
    ("un copiloto para hojas de cálculo", "Convierte lenguaje natural en fórmulas y gráficos."),
]


def make_post(i: int) -> dict:
    # post_id realista: los bits altos son el timestamp en ms (id >> 22).
    days_ago = random.randint(0, 6)
    ms = int(time.time() * 1000) - days_ago * 86400000 - random.randint(0, 3600000)
    post_id = str((ms << 22) | random.randint(0, (1 << 22) - 1))
    topic, detail = random.choice(TOPICS)
    text = (f"Acabamos de lanzar {topic}. {detail} "
            f"En nuestras pruebas mejoró los resultados un {random.randint(15, 60)}%. "
            f"Es open source y self-hosted. (post de prueba #{i})")
    return {
        "post_id": post_id,
        "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{post_id}/",
        "author": f"Tester {i % 8 + 1}",
        "author_url": "https://www.linkedin.com/in/test/",
        "group_name": "TEST",
        "group_color": "#DC2626",
        "text": text,
    }


def post(server: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{server}/api/summarize-post",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="número de posts sintéticos")
    ap.add_argument("--server", default="http://localhost:3002")
    ap.add_argument("--delay", type=float, default=0.05, help="pausa entre envíos (s)")
    args = ap.parse_args()

    server = args.server.rstrip("/")
    ok = 0
    for i in range(1, args.n + 1):
        try:
            resp = post(server, make_post(i))
            ok += 1
            print(f"[{i}/{args.n}] {resp}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{i}/{args.n}] ERROR: {exc}")
        time.sleep(args.delay)
    print(f"\nEnviados {ok}/{args.n}. Mira el feed (se procesan por lotes) y filtra el grupo TEST.")


if __name__ == "__main__":
    main()
