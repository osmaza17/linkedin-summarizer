import React, { useEffect, useRef, useState } from "react";

// Feed de newsletters de LinkedIn. Homogeneizado con el feed de YouTube: mismas clases
// y estructura (.feed-head / .feed-searchbar / .feed-filterbar con FilterSearch /
// .feed-day-head / .feed-grid / .feed-card). Como un post no tiene miniatura, el área
// .feed-thumb se rellena con un "banner" del autor (avatar + nombre). Lee del backend
// (mismo origen) y conserva la cosecha vía la extensión (botón ▶ Cosechar LinkedIn).

// ── Buscador de filtro con desplegable + chips (idéntico al del feed de YouTube) ──
function FilterSearch({ label, placeholder, options, selected, onAdd, onRemove }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    const onDoc = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const q = query.trim().toLowerCase();
  const matches = options.filter(
    (o) => !selected.includes(o.value) && (!q || o.label.toLowerCase().includes(q))
  );
  const pick = (val) => {
    onAdd(val);
    setQuery("");
  };
  const onKeyDown = (e) => {
    if (e.key === "Enter" && matches.length > 0) {
      e.preventDefault();
      pick(matches[0].value);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="filter-search" ref={boxRef}>
      <span className="filter-label">{label}</span>
      <div className="filter-field">
        <div className="filter-search-box" onClick={() => setOpen(true)}>
          <input
            className="filter-input"
            value={query}
            placeholder={placeholder}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
          />
        </div>
        {open && matches.length > 0 && (
          <ul className="filter-dropdown">
            {matches.map((o) => (
              <li key={o.value}>
                <button type="button" className="filter-option" onClick={() => pick(o.value)}>
                  {o.color && <span className="chip-dot" style={{ "--gc": o.color }} />}
                  <span className="filter-option-label">{o.label}</span>
                  <span className="chip-count">{o.count}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="filter-chips">
        {selected.map((val) => {
          const opt = options.find((o) => o.value === val);
          return (
            <span
              key={val}
              className="filter-chip"
              style={opt && opt.color ? { "--gc": opt.color } : undefined}
            >
              {opt && opt.color && <span className="chip-dot" />}
              {opt ? opt.label : val}
              <button
                type="button"
                className="filter-chip-x"
                aria-label="Quitar filtro"
                onClick={() => onRemove(val)}
              >
                ×
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
}

const norm = (s) =>
  String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric" });
}
function dayKey(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}
function dayLabel(key) {
  const d = new Date(`${key}T00:00:00`);
  if (isNaN(d)) return key;
  return d.toLocaleDateString("es-ES", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}
function feedDate(n) {
  return n.published_at || n.processed_at || "";
}

export default function LinkedInFeed({ apiBase }) {
  const [nl, setNl] = useState([]);
  const [groups, setGroups] = useState([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [harvestMsg, setHarvestMsg] = useState("");
  // Posts cuya imagen falló al cargar (URL caducada/rota) → caen al banner del autor.
  const [imgFailed, setImgFailed] = useState({});
  // Filtros (mismo modelo que YouTube: OR dentro de dimensión, AND entre dimensiones).
  const [query, setQuery] = useState("");
  const [fGroups, setFGroups] = useState([]);
  const [fAuthors, setFAuthors] = useState([]);
  const [fDays, setFDays] = useState([]);
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
      /* servidor no disponible: el feed degrada solo */
    }
  };

  useEffect(() => {
    load();
    pollStatus();
    pollRef.current = setInterval(pollStatus, 8000);
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  // Dispara la cosecha pidiéndoselo a la extensión vía window.postMessage.
  const startHarvest = () => {
    setHarvestMsg("⏳ Iniciando cosecha…");
    window.postMessage({ type: "LINKEDIN_HARVEST_REQUEST" }, window.location.origin);
    clearTimeout(harvestTimer.current);
    harvestTimer.current = setTimeout(() => {
      setHarvestMsg(
        "⚠ La extensión no respondió. ¿Está cargada en Chrome y la recargaste en chrome://extensions?"
      );
    }, 4000);
  };

  useEffect(() => {
    const onMsg = (ev) => {
      if (ev.source !== window || !ev.data) return;
      if (ev.data.type !== "LINKEDIN_HARVEST_STARTED") return;
      clearTimeout(harvestTimer.current);
      if (ev.data.ok) {
        setHarvestMsg(
          "▶ Cosecha en marcha. El detalle persona a persona se ve en el popup de la extensión; los posts seleccionados irán apareciendo aquí."
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

  // ── Datos derivados: opciones de filtro (grupo · autor · día) ──
  const groupColor = {};
  groups.forEach((g) => {
    if (g.name) groupColor[g.name] = g.color;
  });
  nl.forEach((n) => {
    const g = n.group_name || "LinkedIn";
    if (!(g in groupColor)) groupColor[g] = n.group_color || "var(--accent)";
  });
  const authorColor = {};
  nl.forEach((n) => {
    if (n.author && !(n.author in authorColor)) authorColor[n.author] = n.group_color || "var(--accent)";
  });
  const groupNames = [...new Set([...groups.map((g) => g.name).filter(Boolean), ...nl.map((n) => n.group_name || "LinkedIn")])];
  const authorNames = [...new Set(nl.map((n) => n.author).filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
  const dayKeys = [...new Set(nl.map((n) => dayKey(feedDate(n))).filter(Boolean))].sort().reverse();
  const countBy = (pred) => nl.filter(pred).length;

  const groupOptions = groupNames.map((name) => ({
    value: name, label: name, color: groupColor[name] || "var(--accent)",
    count: countBy((n) => (n.group_name || "LinkedIn") === name),
  }));
  const authorOptions = authorNames.map((name) => ({
    value: name, label: name, color: authorColor[name] || "var(--accent)",
    count: countBy((n) => n.author === name),
  }));
  const dayOptions = dayKeys.map((k) => ({
    value: k, label: formatDate(`${k}T00:00:00`), count: countBy((n) => dayKey(feedDate(n)) === k),
  }));

  const q = norm(query.trim());
  const hasFilters = fGroups.length || fAuthors.length || fDays.length || q;
  const clearFilters = () => {
    setFGroups([]); setFAuthors([]); setFDays([]); setQuery("");
  };
  const toggleIn = (setter) => (val) => setter((p) => (p.includes(val) ? p : [...p, val]));
  const removeFrom = (setter) => (val) => setter((p) => p.filter((v) => v !== val));

  const filtered = nl.filter(
    (n) =>
      (fGroups.length === 0 || fGroups.includes(n.group_name || "LinkedIn")) &&
      (fAuthors.length === 0 || fAuthors.includes(n.author)) &&
      (fDays.length === 0 || fDays.includes(dayKey(feedDate(n)))) &&
      (q === "" || norm(n.title).includes(q) || norm(n.author).includes(q))
  );
  const byDay = [];
  const dayMap = {};
  filtered.forEach((n) => {
    const k = dayKey(feedDate(n)) || "sin-fecha";
    if (!dayMap[k]) {
      dayMap[k] = [];
      byDay.push(k);
    }
    dayMap[k].push(n);
  });
  byDay.sort().reverse();
  const byRecency = (a, b) =>
    String(b.processed_at || b.published_at || "").localeCompare(String(a.processed_at || a.published_at || ""));
  byDay.forEach((k) => dayMap[k].sort(byRecency));

  if (error && !nl.length) {
    return (
      <div className="dashboard feed-dashboard">
        <div className="feed">
          <div className="feed-empty">
            No se pudo cargar el feed de LinkedIn. ¿Está el servidor en marcha?{" "}
            <button className="link-btn" onClick={load}>Reintentar</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard feed-dashboard">
      <div className="feed">
        <div className="feed-head">
          <h2 className="feed-title-h">Newsletters</h2>
          <div className="feed-searchbar">
            <span className="feed-search-icon">🔍</span>
            <input
              type="text"
              className="feed-search-input"
              placeholder="Buscar por título o autor…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button className="feed-search-clear" onClick={() => setQuery("")} title="Limpiar búsqueda">
                ×
              </button>
            )}
          </div>
          <span className="spacer" />
          {status && <span className="feed-li-status">{status}</span>}
          <button className="btn btn-small btn-harvest" onClick={startHarvest}>
            ▶ Cosechar LinkedIn
          </button>
        </div>

        {harvestMsg && <div className="li-harvest-msg">{harvestMsg}</div>}

        <div className="feed-filterbar">
          <FilterSearch
            label="Grupo"
            placeholder="Buscar grupo…"
            options={groupOptions}
            selected={fGroups}
            onAdd={toggleIn(setFGroups)}
            onRemove={removeFrom(setFGroups)}
          />
          <FilterSearch
            label="Autor"
            placeholder="Buscar autor…"
            options={authorOptions}
            selected={fAuthors}
            onAdd={toggleIn(setFAuthors)}
            onRemove={removeFrom(setFAuthors)}
          />
          <FilterSearch
            label="Día"
            placeholder="Buscar día…"
            options={dayOptions}
            selected={fDays}
            onAdd={toggleIn(setFDays)}
            onRemove={removeFrom(setFDays)}
          />
          {hasFilters ? (
            <button className="link-btn feed-clear" onClick={clearFilters}>
              Limpiar filtros
            </button>
          ) : null}
        </div>

        {!nl.length ? (
          <div className="feed-empty">
            Aún no hay posts seleccionados. Cosecha posts con la extensión de Chrome de LinkedIn.
          </div>
        ) : !filtered.length ? (
          <div className="feed-empty">
            Ningún resultado con estos filtros.{" "}
            <button className="link-btn" onClick={clearFilters}>Limpiar filtros</button>
          </div>
        ) : (
          byDay.map((k) => (
            <section className="feed-day" key={k}>
              <h3 className="feed-day-head">
                {k === "sin-fecha" ? "Sin fecha" : dayLabel(k)}
                <span className="feed-day-count">{dayMap[k].length}</span>
              </h3>
              <div className="feed-grid">
                {dayMap[k].map((n) => {
                  const init = ((n.author || "?").trim()[0] || "?").toUpperCase();
                  // Usa la imagen del post si existe y no falló al cargar; si no, banner autor.
                  const showImg = n.post_image && !imgFailed[n.post_id];
                  return (
                    <a
                      key={n.post_id}
                      className={`feed-card${n.read ? " read" : ""}`}
                      href={`${apiBase}/reports/${n.html}`}
                      target="_blank"
                      rel="noreferrer"
                      style={{ "--gc": n.group_color || "var(--accent)" }}
                    >
                      <div className={`feed-thumb${showImg ? "" : " li-thumb"}`}>
                        <button
                          type="button"
                          className="feed-read-btn"
                          title={n.read ? "Marcar como no leído" : "Marcar como leído"}
                          onClick={(e) => toggleRead(e, n.post_id)}
                        >
                          {n.read ? "✓ Leído" : "Marcar leído"}
                        </button>
                        {showImg ? (
                          <img
                            className="li-post-img"
                            src={n.post_image}
                            alt=""
                            loading="lazy"
                            onError={() => setImgFailed((m) => ({ ...m, [n.post_id]: true }))}
                          />
                        ) : (
                          <div className="li-thumb-inner">
                            {n.author_avatar ? (
                              <img className="li-thumb-av" src={n.author_avatar} alt="" loading="lazy" />
                            ) : (
                              <span className="li-thumb-ph">{init}</span>
                            )}
                            <span className="li-thumb-name">{n.author || ""}</span>
                          </div>
                        )}
                      </div>
                      <div className="feed-card-body">
                        <span className="feed-group">{n.group_name || "LinkedIn"}</span>
                        <span className="feed-title">{n.title}</span>
                        <span className="feed-meta">{formatDate(feedDate(n))}</span>
                      </div>
                    </a>
                  );
                })}
              </div>
            </section>
          ))
        )}
        {!loaded && <div className="feed-empty">Cargando…</div>}
      </div>
    </div>
  );
}
