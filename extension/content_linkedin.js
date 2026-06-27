// content_linkedin.js — corre en linkedin.com/in/<persona>/recent-activity/*
//
// NO hace nada por sí solo: solo cosecha cuando el background le manda el mensaje
// {action:'harvest', days, person}. Así, visitar un perfil a mano NO dispara nada.
//
// ⚠️ AVISO IMPORTANTE: los selectores del DOM de LinkedIn son FRÁGILES y cambian a
// menudo. Todo lo dependiente del DOM está aislado en la sección "EXTRACCIÓN" de
// abajo, para poder ajustarlo fácilmente contra el HTML real. Si la cosecha
// devuelve 0 posts, casi seguro hay que actualizar los selectores de esa sección.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'harvest') {
    harvest(msg.days || 1, msg.person || {})
      .then(posts => sendResponse({ ok: true, posts }))
      .catch(err => sendResponse({ ok: false, error: String(err && err.message || err) }));
    return true; // respuesta asíncrona (la cosecha tarda: scroll + parseo)
  }
});

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function harvest(days, person) {
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  const seen = new Set();
  const collected = [];
  let stagnant = 0;

  // Scroll progresivo para forzar la carga perezosa, hasta cubrir la ventana de
  // días o agotar el contenido. Tope de seguridad de 25 scrolls.
  for (let i = 0; i < 25; i++) {
    const batch = extractPosts(person);
    let added = 0;
    let oldestInView = Date.now();
    for (const p of batch) {
      if (p.publishedMs) oldestInView = Math.min(oldestInView, p.publishedMs);
      if (seen.has(p.post_id)) continue;
      seen.add(p.post_id);
      // Filtra por ventana de días (si pudimos datar el post).
      if (p.publishedMs && p.publishedMs < cutoff) continue;
      collected.push(p);
      added++;
    }
    stagnant = added === 0 ? stagnant + 1 : 0;
    // Para si ya hemos pasado de la ventana de días o no carga nada nuevo.
    if (oldestInView < cutoff) break;
    if (stagnant >= 3) break;
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(1500);
  }

  // Normaliza al payload que espera el servidor (sin el campo interno publishedMs).
  return collected.map(p => ({
    post_id: p.post_id,
    url: p.url,
    text: p.text,
    author: p.author || person.name || '',
    author_url: person.profile_url || '',
    author_avatar: p.author_avatar || '',
    post_image: p.post_image || '',
    published_at: p.published_at || '',
    group_name: person.group_name || 'LinkedIn',
    group_color: person.group_color || '#0A66C2',
  }));
}

// ══════════════════════════════════════════════════════════════════════════════
// EXTRACCIÓN — TODO lo dependiente del DOM de LinkedIn vive aquí (frágil; ajustar
// contra el HTML real si deja de funcionar).
// ══════════════════════════════════════════════════════════════════════════════

function extractPosts(person) {
  const out = [];
  const containers = document.querySelectorAll(
    'div.feed-shared-update-v2, div.profile-creator-shared-feed-update__container, [data-urn*="urn:li:activity"]'
  );
  containers.forEach(el => {
    try {
      const post_id = activityId(el);
      if (!post_id) return;

      // Saltar lo que NO es un post original del autor (comentarios, reacciones).
      if (isReactionOrComment(el)) return;

      const text = postText(el);
      if (!text || text.length < 1) return;

      // Fecha FIABLE: el id de actividad lleva el timestamp embebido (id >> 22 = ms).
      // Si por lo que sea no se pudiera, cae a la fecha relativa del DOM.
      const byId = dateFromId(post_id);
      const dt = byId.publishedMs ? byId : datePost(el);

      out.push({
        post_id,
        url: `https://www.linkedin.com/feed/update/urn:li:activity:${post_id}/`,
        text,
        author: actorName(el) || person.name || '',
        author_avatar: actorAvatar(el),
        post_image: postImage(el),
        published_at: dt.published_at,
        publishedMs: dt.publishedMs,
      });
    } catch (e) { /* un post mal parseado no rompe el resto */ }
  });
  return out;
}

function activityId(el) {
  // El id de actividad puede estar en el data-urn del propio contenedor o de un hijo.
  const urn = el.getAttribute('data-urn')
    || (el.querySelector('[data-urn*="urn:li:activity"]')?.getAttribute('data-urn'))
    || el.getAttribute('data-id') || '';
  const m = urn.match(/urn:li:(?:activity|ugcPost):(\d+)/) || urn.match(/(\d{10,25})/);
  return m ? m[1] : null;
}

function isReactionOrComment(el) {
  // Cabecera de contexto: "X comentó esto", "A X le gusta esto", "X ha compartido"…
  // Conservamos posts originales y reposts con comentario; descartamos comentarios
  // y reacciones a contenido ajeno.
  const header = (el.querySelector(
    '.update-components-header__text-view, .update-components-actor__description, '
    + '.feed-shared-update-v2__update-content-context, .update-components-text-view'
  )?.innerText || '').toLowerCase();
  // Cobertura multi-idioma (ES/EN/FR): comentarios y reacciones a contenido ajeno.
  // OJO: heurística por texto, imperfecta. Los reposts CON comentario se conservan.
  // Alternativa más limpia a probar: cosechar /recent-activity/all/ → /shares/ (solo
  // publicaciones del autor; sin comentarios ni reacciones).
  return /coment|comment|commenté|le gusta|a aimé|\baime\b|likes this|reacted|celebr|recomien|recommand|f[ée]licit|soutient|supports|loves this|insightful/.test(header);
}

function postText(el) {
  const node = el.querySelector(
    '.update-components-text, .feed-shared-update-v2__description, '
    + '.feed-shared-inline-show-more-text, .update-components-update-v2__commentary'
  );
  let txt = (node?.innerText || '').trim();
  // Quita el "…ver más"/"…see more" del botón de expandir.
  txt = txt.replace(/\s*…?\s*(ver más|see more|mostrar más)\s*$/i, '').trim();
  return txt;
}

function actorName(el) {
  const n = el.querySelector(
    '.update-components-actor__title span[aria-hidden="true"], '
    + '.update-components-actor__name, .update-components-actor__title'
  );
  return (n?.innerText || '').trim().split('\n')[0];
}

function actorAvatar(el) {
  const img = el.querySelector('.update-components-actor__avatar img, img.update-components-actor__avatar-image, .ivm-view-attr__img--centered');
  return img?.src || '';
}

// Imagen DEL CONTENIDO del post (la del cuerpo), NO la foto de perfil del autor.
// Busca en los contenedores de imagen/artículo del update; excluye a propósito los
// selectores del avatar para no volver a coger la foto de perfil. Devuelve '' si el
// post no lleva imagen (solo texto, vídeo o documento) → la tarjeta cae al banner autor.
function postImage(el) {
  const img = el.querySelector(
    '.update-components-image img, img.update-components-image__image, '
    + '.feed-shared-image img, .update-components-article__image img, '
    + '.feed-shared-article__image img'
  );
  if (!img) return '';
  // LinkedIn a veces difiere la URL real en data-delayed-url (lazy-load).
  return img.getAttribute('src') || img.getAttribute('data-delayed-url') || '';
}

// Fecha FIABLE a partir del id de actividad: sus bits altos son el Unix time en ms
// (id >> 22). Usa BigInt porque el id supera 2^53. Devuelve {published_at, publishedMs}.
function dateFromId(post_id) {
  try {
    const ms = Number(BigInt(post_id) >> 22n);
    if (ms < 1e12 || ms > 5e12) return { published_at: '', publishedMs: 0 };
    const d = new Date(ms);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    return { published_at: iso, publishedMs: ms };
  } catch (e) {
    return { published_at: '', publishedMs: 0 };
  }
}

// Parsea la fecha relativa ("2 d", "3 h", "1 sem", "5 min", "1 mes", "2 años", "now").
// Devuelve {published_at:'YYYY-MM-DD', publishedMs:<ms>}; vacío si no se puede datar.
function datePost(el) {
  const sub = el.querySelector(
    '.update-components-actor__sub-description span[aria-hidden="true"], '
    + '.update-components-actor__sub-description, .update-components-actor__sub-description-link'
  );
  const raw = (sub?.innerText || '').toLowerCase();
  const m = raw.match(/(\d+)\s*(año|years?|yr|mes|months?|mo|sem|weeks?|wk|d[ií]a?s?|days?|d|h|hours?|hr|m[ií]n|minutes?|min|m)\b/);
  const now = Date.now();
  let ms = now;
  if (raw.includes('ahora') || raw.includes('now')) {
    ms = now;
  } else if (m) {
    const n = parseInt(m[1], 10);
    const u = m[2];
    const day = 86400000;
    if (/año|year|yr/.test(u)) ms = now - n * 365 * day;
    else if (/mes|month|mo/.test(u)) ms = now - n * 30 * day;
    else if (/sem|week|wk/.test(u)) ms = now - n * 7 * day;
    else if (/d/.test(u)) ms = now - n * day;
    else if (/h|hour|hr/.test(u)) ms = now - n * 3600000;
    else ms = now - n * 60000; // minutos
  } else {
    return { published_at: '', publishedMs: 0 };
  }
  const d = new Date(ms);
  const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return { published_at: iso, publishedMs: ms };
}
