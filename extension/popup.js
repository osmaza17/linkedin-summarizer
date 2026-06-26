const DEFAULT_SERVER = 'http://localhost:3002';
const logEl = document.getElementById('log');
const goBtn = document.getElementById('go');
const serverInput = document.getElementById('server');

function log(line) {
  logEl.textContent += (logEl.textContent ? '\n' : '') + line;
  logEl.scrollTop = logEl.scrollHeight;
}

chrome.storage.sync.get(['serverUrl'], ({ serverUrl }) => {
  serverInput.value = serverUrl || DEFAULT_SERVER;
});
serverInput.addEventListener('change', () => {
  chrome.storage.sync.set({ serverUrl: serverInput.value.trim() || DEFAULT_SERVER });
});

document.getElementById('openfeed').addEventListener('click', (e) => {
  e.preventDefault();
  chrome.tabs.create({ url: (serverInput.value.trim() || DEFAULT_SERVER) });
});

// Progreso enviado por el background durante la cosecha.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action !== 'harvestProgress') return;
  if (msg.stage === 'person') log(`(${msg.index}/${msg.total}) Cosechando: ${msg.name}…`);
  else if (msg.stage === 'personDone') log(`(${msg.index}/${msg.total}) ✓ ${msg.name}: ${msg.found} post(s)`);
  else if (msg.stage === 'error') log(`(${msg.index}/${msg.total}) ⚠ ${msg.name}: ${msg.error}`);
  else if (msg.stage === 'done') {
    log(`\n✓ Hecho. ${msg.totalPosts} posts encontrados, ${msg.totalSent} enviados${msg.errors ? `, ${msg.errors} con error` : ''}.`);
    goBtn.disabled = false;
    goBtn.textContent = 'Cosechar ahora';
  }
});

goBtn.addEventListener('click', () => {
  goBtn.disabled = true;
  goBtn.textContent = 'Cosechando…';
  logEl.textContent = '';
  log('Iniciando cosecha…');
  chrome.runtime.sendMessage({ action: 'startHarvest' }, (resp) => {
    if (chrome.runtime.lastError) {
      log('⚠ ' + chrome.runtime.lastError.message);
      goBtn.disabled = false; goBtn.textContent = 'Cosechar ahora';
      return;
    }
    if (resp && !resp.ok) {
      log('⚠ ' + (resp.error || 'error desconocido'));
      goBtn.disabled = false; goBtn.textContent = 'Cosechar ahora';
    }
  });
});
