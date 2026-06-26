import React, { useEffect, useState } from "react";
import LinkedInFeed from "./LinkedInFeed.jsx";
import LinkedInSettings from "./LinkedInSettings.jsx";

// Dashboard de LinkedIn Summarizer — app INDEPENDIENTE servida por su propio backend
// Flask (puerto 3002). Misma apariencia que el dashboard de YouTube (reusa styles.css)
// pero proceso y código separados. Como la sirve el mismo servidor que su API, las
// llamadas van a mismo origen (apiBase = "").

const API = ""; // mismo origen (3002)

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") || "light");
  const [tab, setTab] = useState(() => localStorage.getItem("li-tab") || "feed"); // feed|config
  const [nlCount, setNlCount] = useState(0);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);
  useEffect(() => localStorage.setItem("li-tab", tab), [tab]);
  useEffect(() => {
    fetch(`${API}/api/newsletters`)
      .then((r) => r.json())
      .then((d) => setNlCount((d.newsletters || []).length))
      .catch(() => {});
  }, []);

  const stopServer = async () => {
    if (!window.confirm("¿Detener el servidor de LinkedIn?")) return;
    try {
      await fetch(`${API}/api/shutdown`, { method: "POST" });
    } catch {
      /* el proceso se cierra antes de responder */
    }
  };

  return (
    <>
      <div className="topbar">
        <div className="brand">
          <h1>LinkedIn Summarizer</h1>
          <p>{tab === "config" ? "Ajustes" : "Newsletters"}</p>
        </div>

        <nav className="tabs">
          <button
            className={`tab${tab === "feed" ? " active" : ""}`}
            onClick={() => setTab("feed")}
          >
            Newsletters
            {nlCount > 0 && <span className="tab-count">{nlCount}</span>}
          </button>
          <button
            className={`tab${tab === "config" ? " active" : ""}`}
            onClick={() => setTab("config")}
          >
            Ajustes
          </button>
        </nav>

        <div className="spacer" />

        <button
          className="theme-toggle"
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          title={theme === "dark" ? "Modo claro" : "Modo oscuro"}
        >
          {theme === "dark" ? "☀" : "🌙"}
        </button>
        <button className="btn btn-ghost" onClick={stopServer} style={{ color: "var(--danger)" }}>
          Detener servidor
        </button>
      </div>

      {tab === "config" ? (
        <div className="dashboard">
          <div className="col" style={{ maxWidth: 940, margin: "0 auto", width: "100%" }}>
            <LinkedInSettings apiBase={API} />
          </div>
        </div>
      ) : (
        <LinkedInFeed apiBase={API} />
      )}
    </>
  );
}
