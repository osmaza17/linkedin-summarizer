import React, { useEffect, useRef, useState } from "react";

// Feed de newsletters de LinkedIn dentro del dashboard unificado. Lee del backend
// **independiente** de LinkedIn (puerto 3002) por fetch cross-origin; el pipeline de
// LinkedIn no comparte código con el de YouTube. Porta la lógica del antiguo
// linkedin-prototype/web/feed.html (búsqueda, chips de grupo, agrupación por día,
// marcar leído), con degradación limpia si el servidor de LinkedIn no responde.

function esc(s) {
  return s || "";
}
function norm(s) {
  return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}
function dayKey(n) {
  return (n.published_at || n.processed_at || "").slice(0, 10);
}
function dayLabel(k) {
  if (!k) return "Sin fecha";
  const d = new Date(k + "T00:00:00");
  return d.toLocaleDateString("es-ES", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export default function LinkedInFeed({ apiBase }) {
  const [nl, setNl] = useState([]);
  const [groups, setGroups] = useState([]);
  const [active, setActive] = useState([]); // grupos activos (OR)
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [harvestMsg, setHarvestMsg] = useState("");
  const pollRef = useRef(null);
  const harvestTimer = useRef(null);

  const load = async () => {
    try {
      const r = await fetch(`${apiBase}/api/newsletters`);
      const d = await r.json();
      setNl(d.newsletters || []);
      setGroups(d.groups || []);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoaded(true);
    }
  };

  const pollStatus = async () => {
    try {
      const r = await fetch(`${apiBase}/api/status`);
      const d = await r.json();
      if (d.busy) setStatus(`⏳ Analizando… (${d.queue} en cola)`);
      else if (d.last_run && d.last_run.error) setStatus(`⚠ ${d.last_run.error.slice(0, 60)}`);
      else if (d.last_run && d.last_run.at)
        setStatus(`✓ ${d.last_run.count} seleccionados (${d.last_run.at.slice(11, 16)})`);
      if (d.busy) setTimeout(load, 4000);
    } catch {
      /* servidor de LinkedIn no disponible: el feed degrada solo */
    }
  };

  useEffect(() => {
    load();
    pollStatus();
    pollRef.current = setInterval(pollStatus, 8000);
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  // Dispara la cosecha pidiéndoselo a la extensión de Chrome vía window.postMessage
  // (content_feed.js, inyectado en este origen, lo reenvía al background). Sin pestaña
  // extra. La extensión responde con LINKEDIN_HARVEST_STARTED; si no hay respuesta en
  // unos segundos, casi seguro la extensión no está cargada/recargada.
  const startHarvest = () => {
    setHarvestMsg("⏳ Iniciando cosecha…");
    window.postMessage({ type: "LINKEDIN_HARVEST_REQUEST" }, window.location.origin);
    clearTimeout(harvestTimer.current);
    harvestTimer.current = setTimeout(() => {
      setHarvestMsg(
        "⚠ La extensión no respondió. ¿Está cargada en Chrome y la recargaste en chrome://extensions tras la última actualización?"
      );
    }, 4000);
  };

  // Escucha la respuesta de la extensión al disparar la cosecha.
  useEffect(() => {
    const onMsg = (ev) => {
      if (ev.source !== window || !ev.data) return;
      if (ev.data.type !== "LINKEDIN_HARVEST_STARTED") return;
      clearTimeout(harvestTimer.current);
      if (ev.data.ok) {
        setHarvestMsg(
          "▶ Cosecha en marcha. El detalle persona a persona se ve en el popup de la extensión; los resúmenes irán apareciendo aquí."
        );
        pollStatus();
      } else {
        setHarvestMsg("⚠ " + (ev.data.error || "No se pudo iniciar la cosecha."));
      }
    };
    window.addEventListener("message", onMsg);
    return () => {
      window.removeEventListener("message", onMsg);
      clearTimeout(harvestTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleGroup = (name) =>
    setActive((a) => (a.includes(name) ? a.filter((x) => x !== name) : [...a, name]));

  const toggleRead = async (ev, pid) => {
    ev.preventDefault();
    ev.stopPropagation();
    const n = nl.find((x) => x.post_id === pid);
    if (!n) return;
    const next = !n.read;
    setNl((list) => list.map((x) => (x.post_id === pid ? { ...x, read: next } : x)));
    try {
      await fetch(`${apiBase}/api/newsletters/${encodeURIComponent(pid)}/read`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ read: next }),
      });
    } catch {
      setNl((list) => list.map((x) => (x.post_id === pid ? { ...x, read: !next } : x)));
    }
  };

  const q = norm(query);
  const items = nl.filter((n) => {
    if (active.length && !active.includes(n.group_name)) return false;
    if (q && !norm(n.title).includes(q) && !norm(n.author).includes(q)) return false;
    return true;
  });

  // Agrupar por día (desc) y dentro de cada día por recencia de resumen.
  const byDay = {};
  items.forEach((n) => {
    (byDay[dayKey(n)] = byDay[dayKey(n)] || []).push(n);
  });
  const days = Object.keys(byDay).sort().reverse();
  days.forEach((k) =>
    byDay[k].sort((a, b) => (b.processed_at || "").localeCompare(a.processed_at || ""))
  );

  if (error && !nl.length) {
    return (
      <div className="dashboard feed-dashboard li-dashboard">
        <div className="li-feed">
          <div className="li-inner">
          <div className="li-empty">
            <div className="li-empty-big">No se pudo cargar el feed de LinkedIn</div>
            ¿Está el servidor de LinkedIn (puerto 3002) en marcha? El resto del
            dashboard sigue funcionando.
            <div style={{ marginTop: 14 }}>
              <button className="btn btn-small" onClick={load}>
                ↻ Reintentar
              </button>
            </div>
          </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard feed-dashboard li-dashboard">
      <div className="li-feed">
        <div className="li-inner">
        <div className="li-toolbar">
          <div className="li-search">
            🔎
            <input
              placeholder="Buscar por título o autor…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <button className="btn btn-small btn-harvest" onClick={startHarvest}>
            ▶ Cosechar LinkedIn
          </button>
          <button className="btn btn-small" onClick={load}>
            ↻ Recargar
          </button>
          {status && <span className="li-status">{status}</span>}
        </div>

        {harvestMsg && <div className="li-harvest-msg">{harvestMsg}</div>}

        {groups.length > 0 && (
          <div className="li-chips">
            {groups.map((g) => (
              <button
                key={g.name}
                className={`li-chip${active.includes(g.name) ? " on" : ""}`}
                style={{ "--c": g.color }}
                onClick={() => toggleGroup(g.name)}
              >
                <span className="li-chip-dot" />
                {g.name}
              </button>
            ))}
          </div>
        )}

        {!items.length ? (
          <div className="li-empty">
            <div className="li-empty-big">
              {nl.length ? "Sin resultados" : "Aún no hay posts seleccionados"}
            </div>
            {nl.length
              ? "Prueba a quitar filtros."
              : "Cosecha posts con la extensión de Chrome de LinkedIn."}
          </div>
        ) : (
          days.map((k) => (
            <section className="li-day" key={k}>
              <div className="li-day-head">
                <h2>{dayLabel(k)}</h2>
                <span className="li-day-count">{byDay[k].length}</span>
              </div>
              <div className="li-grid">
                {byDay[k].map((n) => {
                  const init = esc((n.author || "?").trim()[0] || "?").toUpperCase();
                  return (
                    <a
                      key={n.post_id}
                      className={`li-card${n.read ? " read" : ""}`}
                      style={{ "--gc": n.group_color }}
                      href={`${apiBase}/reports/${esc(n.html)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <div className="li-card-body">
                        <div className="li-card-group">{esc(n.group_name || "LinkedIn")}</div>
                        <div className="li-card-author">
                          {n.author_avatar ? (
                            <img src={n.author_avatar} alt="" />
                          ) : (
                            <span className="li-ph">{init}</span>
                          )}
                          <span className="li-nm">{esc(n.author || "")}</span>
                        </div>
                        <div className="li-card-title">{esc(n.title || "")}</div>
                        <div className="li-card-meta">
                          <span>{esc((n.published_at || "").slice(0, 10))}</span>
                          <button
                            className={`li-read-btn${n.read ? " on" : ""}`}
                            onClick={(e) => toggleRead(e, n.post_id)}
                          >
                            {n.read ? "✓ leído" : "marcar leído"}
                          </button>
                        </div>
                      </div>
                    </a>
                  );
                })}
              </div>
            </section>
          ))
        )}
        {!loaded && <div className="li-empty">Cargando…</div>}
        </div>
      </div>
    </div>
  );
}
