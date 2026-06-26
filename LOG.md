# LOG.md — Bitácora de pruebas del prototipo

Registro honesto de lo que se ha probado, qué funcionó y qué no. Orden cronológico.
Fecha de la sesión: 2026-06-20.

---

## 0. Punto de partida y decisiones

- Objetivo: replicar la idea del resumidor de YouTube pero para **posts de LinkedIn**.
- Realidad honesta asumida desde el principio: **no hay API oficial** de LinkedIn para
  leer posts de personas arbitrarias → hay que **cosechar el DOM de la sesión logueada**
  con una extensión (zona gris de ToS, bajo riesgo de cuenta). Descartado el scraping
  headless (alto riesgo de baneo).
- Decisión del usuario: hacerlo en **carpeta aparte** (`linkedin-prototype/`), modular e
  independiente del proyecto de YouTube. Servidor en **puerto 3002**.
- Ventaja sobre YouTube: un post **ya es texto** → **sin transcripción** (sin WhisperX).

---

## 1. Pipeline backend (resumen + newsletter + índice)  ✅ FUNCIONA

- Construido: `summarizer.py`, `report_writer.py`, `pipeline.py`.
- **Prueba**: `pipeline.py --file` con 2 posts sintéticos (uno tipo clickbait sobre RAG).
- **Resultado**: 2 newsletters HTML + `index.json` generados. El abstract **respondía el
  gancho** del título ("¿RAG ha muerto?" → "sigue siendo relevante, con matices"). Un post
  corto degradó con elegancia (avisó de que faltaba el hilo completo).
- **Dedup**: segunda ejecución → "No hay posts nuevos que procesar". ✅

## 2. Servidor Flask (API + cola + feed)  ✅ FUNCIONA

- Construido `server.py` (3002) + `web/feed.html` (vanilla, sin build).
- **Prueba con curl**: `/api/config`, `/api/linkedin-people`, `/api/newsletters`, `/` (200),
  y el flujo de escritura `POST /api/summarize-post` → cola → worker por lotes → resumen →
  archivado → aparece en el feed. ✅
- **Incidencia menor (no es bug del sistema)**: al mandar JSON con acentos por curl desde
  la consola de Windows, el `text` llegaba vacío por **encoding del shell** (cp1252). Con
  un archivo UTF-8 (`--data-binary @file`) funcionó perfecto. Artefacto de prueba, no del
  servidor.

## 3. Primera cosecha REAL con la extensión (1 persona: hguichane)  ✅ FUNCIONA

- Cargada la extensión en Chrome, sesión de LinkedIn iniciada, días=5.
- **Resultado**: **5 posts cosechados** de Henri Guichané, los 5 resumidos y archivados.
  - Nombre real capturado ("Henri Guichané", no el handle). ✅
  - Avatar capturado en los 5. ✅
  - Texto completo (144-221 palabras/post, sin truncar). ✅
  - Solo posts originales (el filtro de comentarios/reacciones aguantó con este autor). ✅
- **Mejor de lo esperado**: los selectores del DOM funcionaron a la primera.

## 4. Fechas de los posts  ⚠️ FALLO → ✅ ARREGLADO

- **Problema detectado**: solo **1 de 5** posts tenía fecha. El parser de fechas relativas
  del DOM ("2 d", "1 sem") solo acertaba en uno → rompía el agrupado por día y el filtro
  de ventana de días.
- **Solución**: los **ids de actividad de LinkedIn codifican el timestamp** en sus bits
  altos (`id >> 22` = Unix ms). Se deriva la fecha del `post_id`, 100% fiable y sin DOM.
  - Implementado en `pipeline.date_from_post_id` (servidor) y `dateFromId` (extensión).
  - **Verificado** contra los 5 ids reales: fechas 16-19 jun, todas dentro de la ventana. ✅
  - Backfill aplicado al `index.json` existente.

## 5. Revisión de bugs / robustez  ✅ FIXES APLICADOS

- **Carrera en `index.json`** (worker al archivar vs `set_read` al marcar leído):
  read-modify-write concurrente podía perder datos/corromper → añadido `_index_lock`. ✅
- **Resúmenes placeholder** (`_(Error…)_`) se escribían como newsletter → ahora se
  descartan. ✅
- **Filtro de comentarios/reacciones solo ES/EN**: no pillaba francés ("a aimé", "a
  commenté"); como los autores objetivo postean en francés, riesgo real → regex ampliada
  a ES/EN/FR. ✅ (Sigue siendo heurístico; ver limitaciones.)
- Código muerto en `feed.html` eliminado. ✅

## 6. Lista de 30 cuentas para pruebas masivas  ✅ HECHO (con matiz honesto)

- **No se inventaron** handles (los public_id no son deducibles; inventarlos = 404s).
- Se obtuvieron **~30 perfiles reales verificados por búsqueda web** (Andrew Ng, Yann
  LeCun, Paul Iusztin, Dhravya Shah, Rakesh Gohel, etc.). Relevancia mixta; no garantizado
  que todos publiquen seguido.
- Cargados en `config.json` en el grupo "Pruebas masivas" (30) + hguichane.
- Se documentó el método más fiable para el usuario: extraer su propia lista de "siguiendo"
  con un snippet de consola.

## 7. Atajos `.bat`  ✅ FUNCIONA

- `Iniciar servidor.bat` (solo servidor + feed) y `Iniciar y cosechar.bat` (servidor +
  dispara la cosecha vía `?harvest=1` que detecta `content_feed.js`).
- Matiz honesto: un `.bat` **no puede pulsar la extensión**; el truco del `?harvest=1` es
  la forma de dispararla desde fuera.

## 8. Extracción en PARALELO (todas las pestañas a la vez)  ❌ NO FUNCIONA → revertido

- Cambio probado: abrir las 31 pestañas a la vez (`Promise.allSettled`).
- **Resultado**: "se abrieron muchas pestañas pero **0 posts**".
- **Causa**: Chrome **estrangula las pestañas en segundo plano**; con 31 a la vez, el
  scroll de carga perezosa no llega a renderizar el DOM → no hay posts que extraer.
- **Decisión**: **revertido a SECUENCIAL** (una pestaña cada vez), que es como funcionó con
  Henri. Es lento (~10-20 min/30 personas) pero fiable. (Era el riesgo que se había avisado.)

## 9. Límite de 5 llamadas concurrentes a Anthropic  ✅ FUNCIONA (verificado)

- Requisito del usuario: ≤5 llamadas concurrentes a Claude.
- Implementado techo duro `MAX_PARALLEL_ANTHROPIC = 5` en `summarizer.summarize_batch`
  (`workers = min(workers, 5)`), independiente de la config.
- **Verificado con test**: 15 trabajos + workers=20 → concurrencia máxima observada = **5**. ✅

---

## Estado actual

### Verificado que funciona
- Pipeline (resumen + newsletter + índice + dedup).
- Servidor + cola + worker por lotes + feed.
- Cosecha real **secuencial** de 1 persona (5 posts).
- Fecha fiable por `post_id`.
- Cap de 5 concurrentes a Anthropic.
- `.bat` de arranque y de cosecha.

### Pendiente de validar
- Cosecha **masiva secuencial** de las ~30 personas de una tirada (volumen, estabilidad,
  posts/persona). Solo se ha validado 1 persona.
- Filtro original-vs-repost/comentario con autores que reposteen/comenten mucho.

### Limitaciones conocidas (no resueltas)
- Service worker MV3 puede suspenderse en cosechas largas → re-lanzar (es reanudable por
  dedup). Posible mejora: keep-alive con `chrome.alarms`.
- Selectores del DOM frágiles (riesgo general de LinkedIn).
- Tiempos de scroll fijos (1,5 s) → conexión lenta o autor muy prolífico podría cortar.
- `_post_status` crece sin límite (memoria; trivial).
- Sin export a Obsidian ni navegación ◄/► (a diferencia del padre).
```
