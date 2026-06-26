// content_feed.js — corre en el feed local standalone (localhost:3002) y en el
// dashboard unificado del padre (localhost:3000). Dos formas de disparar la cosecha,
// ambas terminan mandando {action:'startHarvest'} al background:
//
//   1. ?harvest=1 en la URL (lo hace "Iniciar y cosechar.bat", solo en 3002):
//      arranca automáticamente al cargar la página.
//   2. window.postMessage({type:'LINKEDIN_HARVEST_REQUEST'}) desde la propia página
//      (el botón "▶ Cosechar LinkedIn" del dashboard en 3000): arranca la cosecha
//      sin abrir ninguna pestaña. Responde a la página con
//      {type:'LINKEDIN_HARVEST_STARTED', ok, error} para que muestre feedback.
//
// El puente window.postMessage funciona porque el content script (mundo aislado)
// comparte los eventos 'message' del window con la página. La respuesta detallada
// persona-a-persona se sigue viendo en el popup de la extensión (los mensajes
// runtime broadcast del background no llegan a los content scripts).

(function () {
  // ── 1. Auto-disparo por ?harvest=1 (típicamente en 3002) ──
  try {
    const params = new URLSearchParams(location.search);
    if (params.get('harvest') === '1') {
      params.delete('harvest');
      const clean = location.pathname + (params.toString() ? '?' + params : '');
      history.replaceState(null, '', clean);
      startHarvest((ok, error) => {
        toast(ok ? 'Cosecha iniciada — mira el progreso en el popup de la extensión.'
                 : 'No se pudo iniciar la cosecha: ' + (error || 'desconocido'));
      });
    }
  } catch (e) { /* noop */ }

  // ── 2. Disparo desde la página (dashboard) vía window.postMessage ──
  window.addEventListener('message', (ev) => {
    if (ev.source !== window) return;          // solo mensajes de esta misma página
    const data = ev.data;
    if (!data || data.type !== 'LINKEDIN_HARVEST_REQUEST') return;
    startHarvest((ok, error) => {
      window.postMessage({ type: 'LINKEDIN_HARVEST_STARTED', ok, error: error || null }, '*');
    });
  });

  // Pide al background que arranque la cosecha; cb(ok, error).
  function startHarvest(cb) {
    try {
      chrome.runtime.sendMessage({ action: 'startHarvest' }, (resp) => {
        const err = chrome.runtime.lastError;
        const ok = !err && resp && resp.ok !== false;
        cb(ok, (resp && resp.error) || (err && err.message) || null);
      });
    } catch (e) {
      cb(false, String(e));
    }
  }

  function toast(text) {
    const d = document.createElement('div');
    d.textContent = text;
    d.style.cssText = 'position:fixed;z-index:99999;left:50%;bottom:24px;transform:translateX(-50%);'
      + 'background:#0A66C2;color:#fff;padding:12px 18px;border-radius:10px;font:600 14px system-ui;'
      + 'box-shadow:0 8px 24px rgba(0,0,0,.3);max-width:90vw';
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 6000);
  }
})();
