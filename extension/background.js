// background.js — orquesta la cosecha.
//
// Flujo (disparado por el popup con {action:'startHarvest'}):
//   1. GET /api/linkedin-people  → lista de personas + ventana de días.
//   2. Por cada persona, SECUENCIALMENTE (footprint bajo, parece navegación humana):
//        a. abre su actividad reciente en una pestaña en segundo plano,
//        b. espera a que cargue y le pide al content script que coseche,
//        c. manda cada post a POST /api/summarize-post,
//        d. cierra la pestaña y pasa a la siguiente.
//   3. Informa del progreso al popup vía runtime messages.
//
// Nota: el worker de MV3 puede suspenderse; mientras hay pestañas abriéndose y
// mensajes en curso se mantiene vivo. Para una lista larga, mejor no cerrar el popup.

const DEFAULT_SERVER = 'http://localhost:3002';

let harvesting = false; // guard: evita dos cosechas simultáneas (p. ej. popup + bat)

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'startHarvest') {
    if (harvesting) { sendResponse({ ok: false, error: 'Ya hay una cosecha en curso.' }); return false; }
    runHarvest().then(r => sendResponse(r)).catch(e => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
});

async function serverBase() {
  const { serverUrl } = await chrome.storage.sync.get(['serverUrl']);
  return (serverUrl || DEFAULT_SERVER).replace(/\/+$/, '');
}

function progress(p) {
  chrome.runtime.sendMessage({ action: 'harvestProgress', ...p }).catch(() => {});
}

async function runHarvest() {
  if (harvesting) return { ok: false, error: 'Ya hay una cosecha en curso.' };
  harvesting = true;
  try {
    return await _runHarvest();
  } finally {
    harvesting = false;
  }
}

async function _runHarvest() {
  const base = await serverBase();
  let people, days;
  try {
    const r = await fetch(`${base}/api/linkedin-people`);
    const d = await r.json();
    people = d.people || [];
    days = d.days || 1;
  } catch (e) {
    return { ok: false, error: 'No se pudo contactar con el servidor (' + base + '). ¿Está arrancado?' };
  }
  if (!people.length) return { ok: false, error: 'No hay personas configuradas. Añádelas en el feed (pestaña Personas).' };

  // Extracción SECUENCIAL: una pestaña cada vez (abrir → cosechar → cerrar → siguiente).
  // En paralelo Chrome estrangula las pestañas en segundo plano y el scroll de carga
  // perezosa no llega a renderizar → cosecha 0 posts. Con una sola pestaña activa-en-
  // segundo-plano sí carga bien. Los envíos al servidor van por su cola, que limita
  // Anthropic a 5 concurrentes.
  let totalPosts = 0, totalSent = 0, errors = 0;
  for (let i = 0; i < people.length; i++) {
    const person = people[i];
    progress({ stage: 'person', index: i + 1, total: people.length, name: person.name });
    try {
      const posts = await harvestPerson(person, days);
      let sent = 0;
      for (const post of posts) { if (await sendPost(base, post)) sent++; else errors++; }
      totalPosts += posts.length;
      totalSent += sent;
      progress({ stage: 'personDone', index: i + 1, total: people.length, name: person.name, found: posts.length });
    } catch (e) {
      errors++;
      progress({ stage: 'error', index: i + 1, total: people.length, name: person.name, error: String(e && e.message || e) });
    }
    await sleep(1000 + Math.random() * 800); // pausa entre personas (ritmo humano)
  }
  progress({ stage: 'done', totalPosts, totalSent, errors });
  return { ok: true, totalPosts, totalSent, errors };
}

// Abre la actividad de una persona, pide la cosecha y cierra la pestaña.
async function harvestPerson(person, days) {
  const tab = await chrome.tabs.create({ url: person.activity_url, active: false });
  try {
    await waitForComplete(tab.id, 25000);
    await sleep(1500); // deja que el feed pinte los primeros posts
    const resp = await sendMessageWithRetry(tab.id, { action: 'harvest', days, person }, 4);
    if (!resp || !resp.ok) throw new Error(resp && resp.error || 'sin respuesta del content script');
    return resp.posts || [];
  } finally {
    try { await chrome.tabs.remove(tab.id); } catch (e) {}
  }
}

async function sendPost(base, post) {
  try {
    const r = await fetch(`${base}/api/summarize-post`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(post),
    });
    return r.ok;
  } catch (e) { return false; }
}

// ── utilidades ──
const sleep = ms => new Promise(r => setTimeout(r, ms));

function waitForComplete(tabId, timeout) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => { chrome.tabs.onUpdated.removeListener(listener); resolve(); }, timeout);
    function listener(id, info) {
      if (id === tabId && info.status === 'complete') {
        clearTimeout(t); chrome.tabs.onUpdated.removeListener(listener); resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

// El content script puede no estar listo justo al cargar: reintenta unas veces.
async function sendMessageWithRetry(tabId, msg, tries) {
  for (let i = 0; i < tries; i++) {
    try {
      return await chrome.tabs.sendMessage(tabId, msg);
    } catch (e) {
      await sleep(1200);
    }
  }
  throw new Error('el content script no respondió (¿cambió el DOM o no cargó?)');
}
