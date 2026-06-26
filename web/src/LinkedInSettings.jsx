import React, { useEffect, useRef, useState } from "react";

// Sección "Ajustes de LinkedIn" dentro de la pestaña Ajustes del dashboard unificado.
// Habla con el backend **independiente** de LinkedIn (puerto 3002): lee y guarda su
// PROPIO config.json (linkedin_groups + settings). No comparte código ni config con
// el pipeline de YouTube. Autoguardado con debounce, igual que el dashboard de YouTube.

const PRESET = [
  "#0A66C2", "#4F46E5", "#059669", "#D97706",
  "#DB2777", "#0284C7", "#7C3AED", "#DC2626",
];

function publicId(url) {
  const m = (url || "").match(/\/in\/([^/?#]+)/);
  return m ? m[1] : "";
}

export default function LinkedInSettings({ apiBase }) {
  const [config, setConfig] = useState(null);
  const [meta, setMeta] = useState({ claude_models: [], summary_backends: [] });
  const [saveState, setSaveState] = useState("idle"); // idle|saving|saved|error
  const [error, setError] = useState(false);
  const [urls, setUrls] = useState({}); // gIdx -> {url, name}
  const skipSave = useRef(true);

  // Carga inicial.
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${apiBase}/api/config`);
        const d = await r.json();
        setConfig({
          linkedin_groups: d.linkedin_groups || [],
          settings: d.settings || {},
        });
        setMeta(d.meta || { claude_models: [], summary_backends: [] });
      } catch {
        setError(true);
      }
    })();
  }, [apiBase]);

  // Autoguardado con debounce (evita guardar en la carga inicial con skipSave).
  useEffect(() => {
    if (!config) return;
    if (skipSave.current) {
      skipSave.current = false;
      return;
    }
    setSaveState("saving");
    const t = setTimeout(async () => {
      try {
        await fetch(`${apiBase}/api/config`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            linkedin_groups: config.linkedin_groups,
            settings: config.settings,
          }),
        });
        setSaveState("saved");
      } catch {
        setSaveState("error");
      }
    }, 800);
    return () => clearTimeout(t);
  }, [config, apiBase]);

  if (error) {
    return (
      <section className="card li-settings-card">
        <div className="card-head">
          <h2>Ajustes de LinkedIn</h2>
        </div>
        <p className="hint">
          No se pudo contactar con el servidor de LinkedIn (puerto 3002). Arráncalo
          para editar estos ajustes. El resto del dashboard sigue funcionando.
        </p>
      </section>
    );
  }
  if (!config) {
    return (
      <section className="card li-settings-card">
        <div className="card-head"><h2>Ajustes de LinkedIn</h2></div>
        <p className="hint">Cargando…</p>
      </section>
    );
  }

  const setSetting = (k, v) =>
    setConfig((c) => ({ ...c, settings: { ...c.settings, [k]: v } }));

  const setGroups = (fn) =>
    setConfig((c) => ({ ...c, linkedin_groups: fn(c.linkedin_groups) }));

  const addGroup = () =>
    setGroups((gs) => [
      ...gs,
      { name: "Nuevo grupo", color: PRESET[gs.length % PRESET.length], people: [] },
    ]);
  const removeGroup = (gi) => setGroups((gs) => gs.filter((_, i) => i !== gi));
  const setGroupName = (gi, v) =>
    setGroups((gs) => gs.map((g, i) => (i === gi ? { ...g, name: v } : g)));
  const setGroupColor = (gi, v) =>
    setGroups((gs) => gs.map((g, i) => (i === gi ? { ...g, color: v } : g)));
  const removePerson = (gi, pi) =>
    setGroups((gs) =>
      gs.map((g, i) => (i === gi ? { ...g, people: g.people.filter((_, j) => j !== pi) } : g))
    );
  const addPerson = (gi) => {
    const { url = "", name = "" } = urls[gi] || {};
    const pid = publicId(url.trim());
    if (!pid) return;
    setGroups((gs) =>
      gs.map((g, i) =>
        i === gi
          ? {
              ...g,
              people: [
                ...g.people,
                { name: name.trim() || pid, public_id: pid, profile_url: url.trim(), avatar: "" },
              ],
            }
          : g
      )
    );
    setUrls((u) => ({ ...u, [gi]: { url: "", name: "" } }));
  };

  const s = config.settings;
  const models = meta.claude_models || [];
  const backends = meta.summary_backends || [
    { id: "claude_code", label: "Claude Code (suscripción)" },
    { id: "api", label: "API de Anthropic" },
  ];

  return (
    <section className="card li-settings-card">
      <div className="card-head">
        <h2>Ajustes de LinkedIn</h2>
        <span className={`save-indicator ${saveState}`}>
          {saveState === "saving"
            ? "Guardando…"
            : saveState === "error"
            ? "⚠ Error al guardar"
            : saveState === "saved"
            ? "✓ Guardado"
            : ""}
        </span>
      </div>
      <p className="hint">
        Personas cuyos posts se cosechan con la extensión de Chrome, y el motor de
        resumen. Se guarda en el config propio de LinkedIn (independiente de YouTube).
      </p>

      <div className="row">
        <div className="field">
          <label>Modelo de Claude</label>
          <select value={s.claude_model || ""} onChange={(e) => setSetting("claude_model", e.target.value)}>
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Motor de resumen</label>
          <select
            value={s.summary_backend || "claude_code"}
            onChange={(e) => setSetting("summary_backend", e.target.value)}
          >
            {backends.map((b) => (
              <option key={b.id} value={b.id}>{b.label}</option>
            ))}
          </select>
          <span className="field-hint">
            Claude Code usa tu suscripción (no gasta API); si falla, recurre a la API.
          </span>
        </div>
        <div className="field" style={{ maxWidth: 120 }}>
          <label>Días hacia atrás</label>
          <input
            type="number"
            min="1"
            value={s.days || 1}
            onChange={(e) => setSetting("days", parseInt(e.target.value) || 1)}
          />
        </div>
      </div>

      <h3 className="subhead">Personas a cosechar</h3>
      {config.linkedin_groups.map((g, gi) => (
        <div className="li-group-block" key={gi}>
          <div className="li-group-head">
            <input
              type="color"
              className="li-group-color"
              value={g.color || "#0A66C2"}
              onChange={(e) => setGroupColor(gi, e.target.value)}
            />
            <input
              className="li-group-name"
              value={g.name || ""}
              placeholder="Nombre del grupo"
              onChange={(e) => setGroupName(gi, e.target.value)}
            />
            <span className="spacer" />
            <button className="btn-danger-text" onClick={() => removeGroup(gi)}>
              Eliminar grupo
            </button>
          </div>
          {(g.people || []).map((p, pi) => {
            const init = (p.name || "?").trim()[0]?.toUpperCase() || "?";
            return (
              <div className="li-person" key={pi}>
                {p.avatar ? (
                  <img src={p.avatar} alt="" />
                ) : (
                  <span className="li-ph">{init}</span>
                )}
                <div className="info">
                  <span className="name">{p.name || p.public_id}</span>
                  <span className="cid">{p.public_id || ""}</span>
                </div>
                <span className="spacer" />
                <button className="btn-danger-text" onClick={() => removePerson(gi, pi)}>
                  Eliminar
                </button>
              </div>
            );
          })}
          <div className="add-row" style={{ marginTop: 10 }}>
            <input
              className="li-field-grow"
              placeholder="URL del perfil: https://www.linkedin.com/in/usuario/"
              value={(urls[gi] || {}).url || ""}
              onChange={(e) =>
                setUrls((u) => ({ ...u, [gi]: { ...(u[gi] || {}), url: e.target.value } }))
              }
            />
            <input
              className="li-field"
              placeholder="Nombre a mostrar"
              value={(urls[gi] || {}).name || ""}
              onChange={(e) =>
                setUrls((u) => ({ ...u, [gi]: { ...(u[gi] || {}), name: e.target.value } }))
              }
            />
            <button className="btn btn-small" onClick={() => addPerson(gi)}>
              + Añadir persona
            </button>
          </div>
        </div>
      ))}
      <button className="btn btn-small" onClick={addGroup} style={{ marginTop: 8 }}>
        + Nuevo grupo
      </button>
    </section>
  );
}
