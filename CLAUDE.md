# CLAUDE.md — LinkedIn Summarizer

> Guía técnica para Claude Code. **Fuente de verdad** de esta aplicación. Documenta
> estructura, flujo, módulos, config, la extensión y las decisiones/limitaciones clave.

---

## 🔵 QUÉ ES ESTO

**Aplicación independiente** que recopila posts de **una lista de personas de LinkedIn**,
los **filtra con IA** (¿es un proyecto que el usuario podría usar?) y genera una newsletter
HTML por post seleccionado (el **embed** del post). Vive en
`C:\Users\oscar\linkedin-summarizer\` (carpeta **hermana** de `youtube-summarizer`).

**Es 100% autónoma** (decisión 2026-06-26: se sacó del repo de YouTube, donde antes era el
subproyecto `linkedin-prototype/`): su **propio repo Git**, su **propio `venv`** y `.env`,
su **propio dashboard React** (servido por su backend Flask en el **puerto 3002**), su
config, su pipeline y su extensión de Chrome. **No comparte nada** con el proyecto de
YouTube (ni código, ni entorno, ni claves). El `.env` propio debe tener `ANTHROPIC_API_KEY`.

Diferencia esencial con YouTube: **un post ya es texto** → **no hay transcripción** (ni
WhisperX ni yt-dlp). La extensión cosecha el texto del DOM de la sesión logueada del
usuario y lo manda al servidor, que lo **clasifica** con Claude y archiva solo los que
interesan.

**Diferencia esencial con YouTube:** un post **ya es texto** → **no hay transcripción**
(ni WhisperX ni yt-dlp). La extensión cosecha el texto del DOM de la sesión logueada
del usuario y lo manda al servidor, que lo **clasifica** con Claude y archiva solo los
que interesan.

**El paso de IA FILTRA, no resume** (decisión 2026-06-26): resumir un post de LinkedIn no
aporta nada (ya es texto corto). En su lugar, por cada post Claude decide si presenta un
**proyecto concreto que el usuario podría usar/probar** (herramienta, librería, framework,
app, producto, open source, modelo, demo) y devuelve un veredicto **estructurado**
`{keep, motivo, categoria}` (JSON). Solo los `keep:true` se guardan y generan HTML; los
`keep:false` se registran como **descartados** (para no reevaluarlos) pero no producen
newsletter; los **indecisos** (`keep is None`, fallo puntual de clasificación) ni se
guardan ni se descartan (se reintentan en la próxima cosecha). Ver `classifier.py`.

**Motor (igual que el padre):** por defecto **no** gasta saldo de la API, sino que usa la
**suscripción de Claude Code** lanzando una sesión **headless** (`claude -p`) por post
(`claude_code_backend.py`, copia propia, agnóstico al prompt: aquí se le pide JSON). La
**API queda como fallback** automático si el headless falla. Configurable con
`settings.summary_backend` (`"claude_code"` por defecto, o `"api"`), con un toggle en la
sección "Ajustes de LinkedIn" del dashboard.

---

## ⚠️ REGLA DE MANTENIMIENTO

Cada cambio en esta app debe actualizar **este `CLAUDE.md`** y, si afecta al uso, el
`README.md`, en el mismo cambio.

---

## Objetivo

Recopilar los posts de **una lista de personas elegidas**, **filtrar con IA** los que
presentan un **proyecto que el usuario podría usar**, y generar **una newsletter HTML por
post seleccionado** (embed del post + texto original de respaldo + etiqueta motivo/
categoría), catalogadas en un feed web agrupado por día. Pensado para leerlo como el feed
del proyecto de YouTube, pero para LinkedIn y centrado en proyectos de interés.

Principio de diseño (heredado del padre): **nunca falla de forma ruidosa**. Un post que
no se puede clasificar se loguea y se reintenta luego; el resto continúa.

---

## Estructura

```
linkedin-summarizer/          # app independiente (hermana de youtube-summarizer)
├── CLAUDE.md                 # este archivo (fuente de verdad)
├── README.md                 # doc orientada al usuario
├── config.json               # linkedin_groups (personas) + settings
├── requirements.txt          # anthropic, flask, python-dotenv (instaladas en su venv/)
├── .env                      # ANTHROPIC_API_KEY (no se sube a git)
├── .env.example              # plantilla del .env
├── .gitignore                # ignora .env, reports/, __pycache__, venv/, web/node_modules/
├── venv/                     # entorno Python PROPIO (no versionado)
├── Iniciar servidor.bat      # arranca el servidor (pythonw, sin ventana) + abre el dashboard
├── Iniciar y cosechar.bat    # lo anterior y dispara la cosecha (?harvest=1)
├── src/
│   ├── classifier.py         # post → veredicto {keep,motivo,categoria} con Claude (NO resume; cap 5 concurrentes; backend headless/API)
│   ├── claude_code_backend.py #  backend headless: ejecuta `claude -p` (suscripción, no API)
│   ├── report_writer.py      # post seleccionado → newsletter HTML (SOLO el embed, alto proporcional) + index.json
│   ├── pipeline.py           # normaliza → clasifica → escribe (solo keep) + dedup (CLI: --file)
│   └── server.py             # Flask 3002: sirve el dashboard React (web/dist) + API + worker de la cola
├── web/                      # dashboard React (Vite), MISMO aspecto que el de YouTube
│   ├── src/App.jsx           #   shell: barra superior + pestañas Newsletters/Ajustes (apiBase="")
│   ├── src/LinkedInFeed.jsx       # feed de newsletters (lee /api, mismo origen) + botón ▶ Cosechar
│   ├── src/LinkedInSettings.jsx   # ajustes: personas/grupos, modelo, días, motor (→ /api/config)
│   ├── src/main.jsx, src/styles.css, src/playfair-variable.woff2
│   ├── package.json, vite.config.js, index.html
│   └── dist/                 #   app compilada (la que sirve server.py) — versionada
├── extension/                # extensión de Chrome MV3 de cosecha
│   ├── manifest.json
│   ├── background.js         # orquesta abrir/cosechar/cerrar pestañas (SECUENCIAL) + POST
│   ├── content_linkedin.js   # scroll + extracción del DOM (FRÁGIL: ajustar aquí)
│   ├── content_feed.js       # en el feed local: ?harvest=1 → dispara la cosecha
│   ├── popup.html / popup.js # botón "Cosechar ahora" + URL del servidor + progreso
├── scripts/
│   ├── loadtest.py           # prueba de carga: N posts sintéticos → /api/summarize-post
│   └── backfill_images.py    # rellena post_image de posts ya guardados (vía embed, sin re-cosechar)
└── reports/                  # generadas: <slug>.html + index.json (gitignored)
```

Rutas relativas a la raíz de la app: cada módulo en `src/` calcula
`BASE_DIR = Path(__file__).resolve().parent.parent`.

---

## Flujo de ejecución

1. **Cosecha (extensión)**: el usuario pulsa "Cosechar ahora" (popup), **o el botón
   "▶ Cosechar LinkedIn" del dashboard** (3002; `content_feed.js` reenvía el `postMessage`
   al background), o abre el dashboard con `?harvest=1` (lo hace `Iniciar y cosechar.bat`;
   `content_feed.js` lo detecta).
   `background.js` pide la lista a `GET /api/linkedin-people` y procesa a las personas
   **una a una** (secuencial): abre `…/in/<id>/recent-activity/all/` en una pestaña en
   segundo plano, le manda `{action:'harvest', days, person}` al content script, recibe
   los posts, los manda con `POST /api/summarize-post` y cierra la pestaña.
2. **Recepción (servidor)**: `/api/summarize-post` valida, deduplica (contra `index.json`
   y la cola) y **encola** el post. Responde al instante.
3. **Clasificación (worker en proceso)**: un hilo drena la cola **por lotes** (espera
   `BATCH_WINDOW_SECONDS`=4 s para agrupar la ráfaga) y llama a `pipeline.process_posts`,
   que **clasifica** cada post con Claude (**≤5 concurrentes**); escribe newsletter solo
   para los `keep:true` y hace upsert en `index.json` de guardados y descartados.
4. **Lectura (dashboard)**: el **dashboard React propio** (mismo servidor, puerto 3002)
   consume `GET /api/newsletters` (que **oculta los descartados**), agrupa por día y muestra
   tarjetas; cada una abre su HTML en `/reports/<slug>.html`.

---

## Módulos

### `classifier.py` (antes `summarizer.py`)
Filtra posts en vez de resumirlos: por cada post, Claude decide si es un **proyecto que
el usuario podría usar** y devuelve un veredicto estructurado.
- `PROMPT_TEMPLATE`: pide a Claude clasificar el post y responder **solo con JSON**
  `{"keep": true|false, "motivo": "<frase>", "categoria": "<corta>"}`. Define el criterio
  de `keep` (proyecto/herramienta/librería/app/producto/open source usable = sí; opinión/
  empleo/motivación/noticia/evento/anécdota = no).
- `classify(post, client, model, backend="api") -> dict`: construye el prompt y lo
  resuelve por **Claude Code headless** (`"claude_code"`, reusa
  `claude_code_backend.summarize(prompt, model)` —agnóstico al prompt, devuelve texto—)
  con **fallback a la API** (`[CLAUDE_CODE_FALLBACK]`) o directo por API. Parsea el JSON
  con `_parse_verdict` (tolerante: quita fences, busca el primer `{...}`). Devuelve
  `{keep: bool|None, motivo, categoria}`. **`keep is None`** = indeciso (JSON ilegible o
  error no fatal) → el pipeline ni guarda ni descarta (reintento futuro). Propaga
  `FatalAPIError`.
- `classify_batch(posts, client, model, workers, backend="api") -> {post_id: dict}`:
  paraleliza si `backend=="claude_code"` **o** Haiku por API. **Techo duro
  `MAX_PARALLEL_ANTHROPIC = 5`**.
- Reintentos transitorios (429/5xx/conexión) con backoff; `FatalAPIError` para 4xx
  definitivos (saldo, key, permiso, modelo). `CLASSIFY_MAX_TOKENS=400` (la respuesta es un
  JSON corto). Mantiene `_create_message`/`_fatal_reason`/`_is_billing_error` del padre.

### `claude_code_backend.py`
Backend headless propio de esta app. Ejecuta `claude -p` usando la **suscripción** (no
gasta API); es **agnóstico al prompt** (devuelve el texto del modelo), así que `classifier`
lo reutiliza pasándole el
prompt de clasificación. Lo invoca `classifier.classify` cuando `backend=="claude_code"`.
- `claude_path()`/`is_available()`: localizan el `claude` del PATH (`shutil.which`).
- `_model_alias(model)`: id de Anthropic → alias `haiku`/`sonnet`/`opus`.
- `summarize(prompt, model, timeout=600)`: ejecuta `claude -p --model <alias>
  --output-format json` con el **prompt por stdin**, valida `is_error` y devuelve
  `result`. Lanza **`ClaudeCodeError`** (recuperable → dispara el fallback por API) ante
  exit≠0, salida vacía, JSON inválido, `is_error` o timeout.
- Cuatro cuidados clave: **elimina `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`** del entorno
  del subproceso (si no, Claude Code facturaría la API en vez de la suscripción); **prompt
  por stdin** (no como argumento, por el límite de la línea de comandos en Windows);
  **cwd temporal vacío** (que no cargue ningún `CLAUDE.md`); y **sin ventanas de consola**
  en Windows (`_no_window_kwargs()` → **`CREATE_NO_WINDOW`** + `STARTUPINFO`/`SW_HIDE`).
  **Probado**: sin `CREATE_NO_WINDOW`, cada `claude` lanzado desde un proceso sin consola
  abre una ventana (Windows Terminal / `conhost`); con el flag no aparece ninguna. **Hay
  que reiniciar el servidor** para que cargue este código.
- Requisito: la CLI `claude` instalada, en el PATH y con **sesión iniciada**. Si no, el
  resumen recurre a la API (que sigue necesitando `ANTHROPIC_API_KEY`).

### `report_writer.py`
- `_escape_html`: escapa `& < >` (lo único que queda de los helpers de texto; se
  retiraron `_md_to_html`/`_inline_md`/`_text_to_html`/`_linkify` porque ya no se
  renderiza ni resumen ni texto).
- `_build_html(post)`: página autocontenida (claro/oscuro, botón "‹ Feed", botón tema)
  que muestra **solo el embed oficial de LinkedIn** del post (`<iframe>` a
  `…/embed/feed/update/urn:li:activity:{post_id}`, `.embed-wrap`). **Sin** eyebrow,
  título, autor, enlace, etiqueta de motivo ni texto original — solo el embed. La
  **altura del iframe es proporcional a la longitud del texto** del post (estima "líneas
  visuales" ~80 chars/línea + saltos; `embed_h = clamp(200 + vlines*24, 360, 1600)`,
  inline en el `style` del iframe) para que el post quepa entero. `--accent` se fija
  inline con `group_color`. **Ya no hay resumen, abstract, motivo ni texto original** (el
  `motivo`/`categoria` se siguen guardando en el índice para auditar, pero no se pintan).
- Índice keyed por **`post_id`**: `load_index_map`, `resolve_filename` (slug del título,
  reusa nombre previo, resuelve colisiones `-2/-3`), `write_post_newsletter`,
  `index_entry(post, filename, keep=True)` (añade `keep`/`motivo`/`categoria`/`post_image`
  a los campos de siempre), **`rejected_entry(post)`** (ficha mínima de descartado:
  `keep:false`, sin
  `html`, con motivo), `update_index` (upsert por post_id, orden fecha desc, **nunca
  borra**, **preserva `read`**).

### `pipeline.py`
- `extract_activity_id(value)`: saca el id numérico de una URL/URN de LinkedIn.
- **`date_from_post_id(post_id)`** (clave): la fecha **fiable** de un post. Los ids de
  actividad de LinkedIn **codifican el timestamp** en los bits altos (`id >> 22` = Unix
  ms). Se prefiere a la fecha relativa del DOM (que es imprecisa). Con cordura de rango.
- **`resolve_post_image(post_id)`**: imagen de **contenido** del post desde el **embed
  público** de LinkedIn (`…/embed/feed/update/urn:li:activity:{id}`, la misma URL que el
  iframe del report; accesible **sin login**). Parsea las URLs `media.licdn.com/dms/image/
  v2/<hash>/<token>/…`: descarta `profile-displayphoto`/`company-logo` (avatar/logos) y
  prefiere la imagen nativa (`feedshare` > `article` > `videocover` > `image`). Devuelve
  '' si el post es solo texto o si la descarga falla (nunca lanza). Es la fuente **más
  fiable** que raspar el DOM; la usa `process_posts` como respaldo y el script de backfill.
- `normalize_post(raw)`: dict canónico interno (`post_id`, `author`, `author_url`,
  `author_avatar`, `post_image` [imagen del **cuerpo** del post, no la foto de perfil;
  '' si el post no lleva imagen], `group_*`, `url`, `published_at` [de
  `date_from_post_id` o respaldo], `text`, `title` [1ª línea recortada]). None si falta
  texto o id.
- `process_posts(raw_posts, settings, client, model, reports_path)`: deduplica contra el
  índice (incluye los **descartados**, así no se reevalúan) y dentro del lote; **clasifica**
  (`classify_batch`, `backend=settings.get("summary_backend", "claude_code")`); por cada
  post según el veredicto: `keep:true` → si no trae `post_image` del DOM lo resuelve con
  `resolve_post_image` (embed), escribe HTML + `index_entry`; `keep:false` →
  `rejected_entry` (sin HTML); `keep is None` → ni guarda ni descarta (reintento futuro).
  `update_index` con guardados + descartados. Devuelve el nº de **guardados**. Propaga
  `FatalAPIError`.
- `load_env()`: carga el `.env` propio (debe tener `ANTHROPIC_API_KEY`).
- CLI: `python pipeline.py --file posts.json` (lista de posts) para probar sin extensión.

### `server.py` (Flask, 127.0.0.1:**3002**)
- **Seguridad** (`_guard_local_only` + `_cors`): rechaza Host/Origin no locales salvo
  `chrome-extension://…` (la extensión de cosecha). El dashboard es **mismo origen**
  (3002), así que no necesita CORS. El Host sigue ligado a 127.0.0.1 → superficie solo
  local. `_cors` solo emite cabeceras CORS para la extensión.
- **Sirve el dashboard React** (`web/dist`) en `/` y los estáticos en `/<path>` (fallback
  SPA a `index.html`).
- **Cola + worker** (`_post_worker`, hilo daemon perezoso): drena por lotes
  (`BATCH_WINDOW_SECONDS`), llama a `process_posts` **bajo `_index_lock`** y guarda
  `_busy`/`_last_run`. `_index_lock` (RLock) **serializa toda escritura de `index.json`**
  (worker y `set_read`) para evitar pisarse (read-modify-write concurrente).
- Endpoints:
  - `GET/POST /api/config`: lee/escribe `linkedin_groups` + `settings` (modelo, workers,
    días, **`summary_backend`**). Deriva `public_id` de la URL si falta. `summary_backend`
    se valida a `{"claude_code","api"}` (default `"claude_code"`) y se expone en `meta`
    (`summary_backends`) para el selector del dashboard. Preserva `reports_dir`.
  - `GET /api/linkedin-people`: lista aplanada de personas (name, public_id, profile_url,
    `activity_url` = `…/recent-activity/all/`, group_*) + `days`. La consume la extensión.
  - `POST /api/summarize-post`: valida, deduplica, encola. `GET` → estado del post.
  - `GET /api/status`: `{busy, queue, last_run}` (lo sondea el feed cada 8 s).
  - `GET /api/newsletters`: índice + grupos derivados (para el feed). **Oculta los
    descartados** (`keep:false`); las entradas antiguas sin campo `keep` se siguen
    mostrando.
  - `POST /api/newsletters/<post_id>/read`: marca leído (upsert en índice, bajo lock).
  - `GET /reports/<f>.html`, `GET /` (dashboard React), `GET /<path>` (estáticos de
    `web/dist`, con fallback SPA a `index.html`), `POST /api/shutdown`.

### `web/` (dashboard React PROPIO, Vite)
La lectura y la configuración viven en el **dashboard React de esta app**, servido por su
propio `server.py` en **`web/dist`** (puerto 3002, **mismo origen**). Reusa el `styles.css`
del dashboard de YouTube para tener **el mismo aspecto**, pero es código separado.
- `src/App.jsx`: shell con barra superior (marca "LinkedIn Summarizer", tema, "Detener
  servidor") y **pestañas Newsletters / Ajustes**. Pasa `apiBase=""` a los componentes
  (mismo origen → fetch a `/api/...`).
- `src/LinkedInFeed.jsx`: feed de newsletters **homogeneizado con el de YouTube** (mismas
  clases/estructura: `.feed-head` + `.feed-searchbar`, `.feed-filterbar` con buscadores
  `FilterSearch` —grupo · autor · día—, `.feed-day-head` en Playfair, `.feed-grid` de
  `.feed-card`). El área `.feed-thumb` muestra la **imagen del propio post** si la tiene
  (campo `post_image`, a sangre completa con `object-fit: cover`, clase `.li-post-img`; si
  la URL falla al cargar cae al banner); si el post **no lleva imagen**, se rellena con un
  **banner del autor** (avatar/inicial + nombre, clase `.li-thumb`). Marcar leído en la
  tarjeta; abre los HTML en `/reports/<slug>.html`. **Incluye el botón "▶ Cosechar
  LinkedIn"** en la cabecera: hace `window.postMessage({type:'LINKEDIN_HARVEST_REQUEST'})`,
  que `content_feed.js` (inyectado en 3002) reenvía al background para arrancar la cosecha
  sin abrir pestaña; escucha `LINKEDIN_HARVEST_STARTED` y, si no hay respuesta en 4 s, avisa
  (`.li-harvest-msg`).
- `src/LinkedInSettings.jsx`: ajustes (personas/grupos, modelo, días, **motor**);
  autoguardado contra `/api/config`.
- **Tras tocar `web/src/*` hay que recompilar**: `cd web && npm run build` (`server.py`
  sirve `web/dist`).

---

## Extensión de Chrome (`extension/`, MV3)

- **`manifest.json`**: `host_permissions` linkedin + localhost:3002. Dos content scripts:
  `content_linkedin.js` en `…/in/*/recent-activity/*` y `content_feed.js` en el dashboard
  (`localhost:3002/*`), para que el botón "▶ Cosechar LinkedIn" pueda disparar la cosecha
  (ver `content_feed.js`).
- **`content_linkedin.js`**: NO actúa solo; solo cosecha al recibir `{action:'harvest'}`.
  `harvest(days, person)` hace scroll progresivo (hasta 25, para por ventana de días o
  3 sin novedades) y `extractPosts` saca de cada update: `post_id` (del data-urn), texto,
  autor, avatar, **imagen del cuerpo del post** (`postImage`, excluye la foto de perfil;
  '' si no hay) y **fecha vía `dateFromId`** (id>>22; respaldo `datePost` por fecha
  relativa). `isReactionOrComment` descarta comentarios/reacciones (heurística por texto,
  multi-idioma ES/EN/FR — imperfecta). **⚠️ TODO lo dependiente del DOM está en la sección
  "EXTRACCIÓN"; si cosecha 0 posts, ahí hay que ajustar los selectores.**
- **`content_feed.js`** (corre en el dashboard, `localhost:3002`): dos formas de
  disparar la cosecha, ambas terminan mandando `{action:'startHarvest'}` al background:
  (1) si la URL trae **`?harvest=1`** (lo hace `Iniciar y cosechar.bat`), arranca al cargar
  y limpia el parámetro (toast); (2) si la **página** manda un
  `window.postMessage({type:'LINKEDIN_HARVEST_REQUEST'})` —el botón "▶ Cosechar LinkedIn"
  del **dashboard**— arranca la cosecha **sin abrir pestaña** y responde a la página
  con `{type:'LINKEDIN_HARVEST_STARTED', ok, error}` para el feedback. El puente
  `window.postMessage` funciona porque el content script (mundo aislado) comparte los
  eventos `message` del `window` con la página. El detalle persona-a-persona se sigue viendo
  en el popup (los broadcast del background no llegan a los content scripts).
- **`background.js`**: `runHarvest` con **guard `harvesting`** (no dos a la vez).
  Procesa a las personas **SECUENCIALMENTE** (abrir → `harvestPerson` → enviar posts →
  cerrar → pausa). `harvestPerson` abre la pestaña en segundo plano, espera carga,
  reintenta el `sendMessage`, recibe posts y **cierra la pestaña en `finally`**. `sendPost`
  hace `POST /api/summarize-post`. Progreso al popup (`person`/`personDone`/`error`/`done`).
- **`popup.html/.js`**: botón "Cosechar ahora", campo de URL del servidor (`serverUrl` en
  storage), log de progreso, enlace al feed.

---

## Configuración (`config.json`)

```json
{
  "linkedin_groups": [
    { "name": "Grupo", "color": "#0A66C2",
      "people": [ { "name": "...", "public_id": "...", "profile_url": "...", "avatar": "" } ] }
  ],
  "settings": {
    "claude_model": "claude-haiku-4-5-20251001",
    "summary_parallel_workers": 5,   // se capa a 5 (MAX_PARALLEL_ANTHROPIC)
    "days": 10,                       // ventana de días hacia atrás de la cosecha
    "summary_backend": "claude_code", // "claude_code" (suscripción, headless) | "api"
    "reports_dir": "reports"
  }
}
```

`summary_backend` gobierna el motor del paso de IA (ahora **clasificación**, ya no
resumen): `"claude_code"` (sesión headless de la CLI → **suscripción**, no gasta API; con
fallback automático a la API si falla) o `"api"` (directo a la API). Toggle en la sección
"Ajustes de LinkedIn" del dashboard. (El **key** se conserva por compatibilidad aunque ya
no resuma; las etiquetas de la UI se renombrarán en la Fase 2.)

---

## Decisiones y gotchas clave

- **Fecha por `post_id`, no por DOM**: la fecha relativa de LinkedIn ("2 d") solo se
  parseaba en ~1/5 casos; el timestamp embebido en el id (`>>22`) es 100% fiable. Se usa
  en `pipeline.date_from_post_id` (servidor) y `dateFromId` (extensión, BigInt).
- **Cosecha SECUENCIAL, no paralela**: abrir 30+ pestañas a la vez → Chrome estrangula
  las de segundo plano → el scroll no carga → **0 posts**. Una pestaña cada vez funciona.
  (El paralelo se probó y se revirtió por esto.) Es lento (~10-20 min/30 personas) pero
  fiable.
- **≤5 llamadas concurrentes a Anthropic** (`MAX_PARALLEL_ANTHROPIC` en `classifier.py`),
  pase lo que pase en la config.
- **Dedup por `post_id`**: re-lanzar una cosecha es **idempotente/reanudable** (lo ya
  clasificado —guardado **o** descartado— se omite, no se re-paga). Útil si el service
  worker MV3 se suspende a mitad.
- **`_index_lock`**: toda escritura de `index.json` (worker + set_read) está serializada.
- **Imagen del post vía embed público**: la miniatura (`post_image`) se resuelve del
  **embed** (`resolve_post_image`), no solo del DOM cosechado. Es más robusto (no depende
  de selectores frágiles) y permite **rellenar posts antiguos** sin re-cosechar
  (`scripts/backfill_images.py`). El embed es público (sin login); si falla o el post es
  solo texto, la tarjeta cae al banner del autor.
- **No hay API oficial de LinkedIn** para esto; la cosecha lee el DOM de la sesión propia
  (zona gris de ToS, bajo riesgo de cuenta). Los selectores del DOM son **frágiles**.

## Limitaciones conocidas / próximos pasos

- El **service worker MV3 puede suspenderse** en cosechas largas → re-lanzar (es
  reanudable). Pendiente: keep-alive con `chrome.alarms`.
- El filtro original-vs-comentario es heurístico por texto. Alternativa más limpia a
  probar: cosechar `…/recent-activity/shares/` (solo publicaciones) en vez de `/all/`.
- Tiempos de scroll fijos (1,5 s); conexión lenta o autor muy prolífico → puede cortarse.
- `_post_status` crece sin límite (memoria; trivial a esta escala).
- Sin navegación ◄/► entre newsletters; añadir si cuaja.

---

## Ejecución

- **Abrir el dashboard**: doble clic en **`Iniciar servidor.bat`** → libera el puerto 3002,
  arranca el servidor con **`pythonw.exe`** (sin ventana, su propio `venv`) y abre
  `http://localhost:3002`. O a mano: `venv\Scripts\python.exe src\server.py`.
- **Servidor + cosecha**: doble clic en `Iniciar y cosechar.bat` (requiere Chrome con la
  extensión cargada, sesión de LinkedIn iniciada y Chrome predeterminado).
- **Probar el pipeline sin la extensión**: `venv\Scripts\python.exe src\pipeline.py --file posts.json`.
- **Prueba de carga**: `venv\Scripts\python.exe scripts\loadtest.py --n 30`.
- **Backfill de imágenes** (rellenar `post_image` de posts ya guardados, sin re-cosechar):
  `venv\Scripts\python.exe scripts\backfill_images.py` (descarga la imagen de cada post
  desde el embed público; idempotente; los posts solo-texto quedan con el banner del autor).
- **Tras tocar la extensión**: recargarla en `chrome://extensions` (↻).
- **Tras tocar `server.py`/`pipeline.py`/`classifier.py`/`claude_code_backend.py`/config**:
  reiniciar el servidor.
- **Tras tocar la UI del dashboard** (`web/src/*`): `cd web && npm run build`.
- **Requisito del backend headless**: la CLI `claude` instalada, en el PATH y con sesión.
```
