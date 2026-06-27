
# LinkedIn Summarizer

**Aplicación independiente** (carpeta hermana de `youtube-summarizer`): su propio repo Git,
su propio `venv` y `.env`, su propio servidor Flask (**puerto 3002**) que sirve un
**dashboard React propio**, su backend headless y su extensión de Chrome. **No comparte
nada** con el proyecto de YouTube. El `.env` propio debe tener `ANTHROPIC_API_KEY`.

> **Mismo aspecto que el dashboard de YouTube, pero app separada.** La lectura y la
> configuración se hacen en su **dashboard React** en `http://localhost:3002` (pestañas
> **Newsletters** y **Ajustes**), servido por su propio backend.

> **El paso de IA FILTRA, no resume** (un post ya es texto corto). Por cada post, Claude
> decide si presenta un **proyecto que podrías usar** y guarda solo esos. Usa tu
> **suscripción de Claude Code** por defecto (sesión headless `claude -p`, no gasta saldo
> de API), con **fallback automático a la API** si falla (`settings.summary_backend`).
> Requisito: tener la CLI `claude` instalada, en el PATH y con sesión iniciada.

> 📄 Documentación técnica completa (estructura, módulos, decisiones) en **`CLAUDE.md`**.
> 🧪 Bitácora de pruebas (qué funcionó y qué no) en **`LOG.md`**.

## Qué hace

Recopila los posts de **una lista de personas de LinkedIn** que tú eliges, **filtra con
IA** los que presentan un **proyecto que podrías usar** (herramienta, librería, app, open
source…) y genera **una newsletter HTML por post seleccionado** (embed del post + texto
original de respaldo + etiqueta con el motivo/categoría), catalogadas en un feed web
agrupado por día. Como el feed del proyecto de YouTube pero **sin transcripción** (un post
ya es texto) y **filtrando** en vez de resumir. Los posts que no son proyectos se
descartan (no generan newsletter).

La captura la hace una **extensión de Chrome**: abre **una a una** (secuencial) la
página de *actividad reciente* de cada persona, recoge sus posts del DOM y los manda al
servidor local, que los **clasifica** con Claude (¿es un proyecto que me interesa?) y
archiva solo los que pasan el filtro. El servidor **limita las llamadas a Claude a 5
concurrentes** (techo duro en `classifier.py`).

## ⚠️ Honestidad sobre LinkedIn (léelo)

- **No hay API oficial** para leer posts de personas arbitrarias (esto NO es como
  YouTube). La cosecha lee el DOM de **tu propia sesión** logueada.
- Estrictamente es **zona gris de los Términos de Uso** de LinkedIn, aunque el riesgo
  para tu cuenta es bajo al actuar como un humano en su propia sesión y a ritmo pausado.
- **Los selectores del DOM son frágiles** y cambian. Si una persona cosecha 0 posts,
  casi seguro hay que ajustar la sección "EXTRACCIÓN" de `extension/content_linkedin.js`.
- La cosecha va **una pestaña cada vez** a propósito: en paralelo (todas a la vez) Chrome
  estrangula las pestañas en segundo plano y no carga nada → 0 posts. Es lento pero fiable.

## Puesta en marcha

- **`Iniciar servidor.bat`** → libera el puerto 3002, arranca el servidor (sin ventana, su
  propio `venv`) y abre el **dashboard** en `http://localhost:3002`.
- **`Iniciar y cosechar.bat`** → lo anterior y además **dispara la cosecha** (abre el
  dashboard con `?harvest=1`, que la extensión detecta).

Requisitos para la cosecha automática: **Chrome con la extensión cargada**, **sesión de
LinkedIn iniciada** y **Chrome como navegador predeterminado**.

> Tras tocar la UI del dashboard (`web/src/*`), recompila: `cd web && npm run build`.

> Tras cambiar la extensión, recárgala en `chrome://extensions` (↻). Tras cambiar el
> código Python o la config, reinicia el servidor (los `.bat` ya lo reinician).

## Puesta en marcha manual

```bash
cd linkedin-summarizer
venv/Scripts/python.exe src/server.py      # dashboard en http://localhost:3002
```

1. **Configura a quién cosechar**: dashboard (`localhost:3002`) → pestaña **Ajustes** →
   pega la URL del perfil (`https://www.linkedin.com/in/usuario/`) y un nombre. (O edita
   `config.json`.)
2. **Carga la extensión**: `chrome://extensions` → Modo desarrollador → *Cargar
   descomprimida* → carpeta `extension/`. Ten LinkedIn abierto y logueado.
3. **Cosecha**: tienes tres formas equivalentes de dispararla, todas pasan por la extensión:
   - **Botón "▶ Cosechar LinkedIn"** en el dashboard (pestaña *Newsletters*) — sin abrir
     pestañas; requiere tener la extensión cargada y **recargada** tras actualizarla.
   - Icono de la extensión → **Cosechar ahora**.
   - `Iniciar y cosechar.bat` (abre el dashboard con `?harvest=1`).

   Verás el progreso una a una en el popup de la extensión; los posts seleccionados van
   apareciendo en el feed.

## Probar el pipeline sin la extensión

```bash
venv/Scripts/python.exe src/pipeline.py --file mis_posts.json
```
`mis_posts.json` = lista de `{post_id?, url, author, text, published_at?, group_name?, group_color?}`.

## Prueba de carga (sin LinkedIn)

```bash
venv/Scripts/python.exe scripts/loadtest.py --n 30
```
Genera N posts sintéticos (con fecha real codificada en el id) y los mete por el endpoint
para estresar cola/worker/dedup/feed. Los marca con el grupo **TEST** (cada uno **sí**
llama a Claude).

## Imágenes de los posts en el feed

Cada tarjeta del feed usa como miniatura la **imagen del propio post** (no la foto de
perfil): se obtiene del **embed público** del post (sin login). Los posts sin imagen
(solo texto) muestran un banner con el autor. Para rellenar la imagen de posts **ya
guardados** sin volver a cosechar:

```bash
venv/Scripts/python.exe scripts/backfill_images.py
```
Es idempotente (solo toca las entradas sin imagen) y no llama a Claude.

## Configuración (`config.json`)

- `linkedin_groups`: grupos de personas (`name`, `public_id`, `profile_url`, `avatar`).
- `settings`: `claude_model`, `summary_parallel_workers` (se capa a 5), `days` (ventana de
  días hacia atrás de la cosecha), `summary_backend` (`claude_code` por defecto, o `api`),
  `reports_dir`.

## Limitaciones / próximos pasos

- El service worker de Chrome (MV3) puede suspenderse en cosechas largas → re-lanza (la
  deduplicación lo hace reanudable).
- Filtro original-vs-comentario heurístico; alternativa a probar: `…/recent-activity/shares/`.
- Sin export a Obsidian ni navegación entre newsletters (a diferencia del padre).
- **UI fusionada, pipelines separados**: la lectura/config se unificó en el dashboard del
  padre, pero los pipelines siguen 100% independientes. Pendiente, si cuaja: lanzador
  combinado que arranque ambos servidores de una.
```
