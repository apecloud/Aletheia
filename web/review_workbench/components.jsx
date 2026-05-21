/* Aletheia shared components — exposed to window for cross-script use */

const { useState, useEffect, useRef, useMemo } = React;

/* ---------- TopBar ---------- */
function TopBar({ screen, tenant, role, onTenant, onRole, onConn }) {
  const labelByScreen = {
    workbench: { kicker: "Case",  now: "Workspace" },
    reasoning: { kicker: "Process", now: "Reasoning" },
    ontology:  { kicker: "Catalog", now: "Ontology" },
    graph:     { kicker: "Explore", now: "Graph" },
    quality:   { kicker: "Triage",  now: "Quality" },
    runtime:   { kicker: "Config",  now: "Runtime" },
  };
  const b = labelByScreen[screen] || labelByScreen.workbench;
  return (
    <div className="topbar">
      <div className="brand">
        <div className="mark">A</div>
      </div>
      <div className="breadcrumb">
        <span className="kicker">{b.kicker}</span>
        <span className="sep">/</span>
        <span className="now">{b.now}</span>
      </div>
      <div></div>
      <div className="right">
        <ConnectionChip onClick={onConn} />
        <div className="chip" onClick={onTenant}>
          <span className="label">Tenant</span>
          <span className="val">{tenant.name}</span>
          <span className="caret">▾</span>
        </div>
        <div className="chip" onClick={onRole}>
          <span className="label">Role</span>
          <span className="val">{role}</span>
          <span className="caret">▾</span>
        </div>
        <div className="user">
          <div className="avatar">MA</div>
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.2 }}>
            <span style={{ fontSize: 11, color: "var(--text)" }}>M. Aoki</span>
            <span style={{ fontSize: 9, color: "var(--dim)", letterSpacing: "0.08em", textTransform: "uppercase" }}>reviewer</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------- Left Rail ---------- */
function Rail({ screen, onNav }) {
  const items = [
    { id: "workbench", g: "▤", l: "Wbk" },
    { id: "reasoning", g: "⚡", l: "Rsn" },
    { id: "ontology",  g: "◇", l: "Ont" },
    { id: "graph",     g: "✺", l: "Grph" },
    { id: "quality",   g: "△", l: "Qly" },
    { id: "runtime",   g: "▣", l: "Rtm" },
  ];
  return (
    <div className="rail">
      <div className="nav">
        {items.map(it => (
          <div key={it.id}
               className={"nav-item" + (screen === it.id ? " active" : "")}
               onClick={() => onNav(it.id)}>
            <div className="glyph">{it.g}</div>
            <div>{it.l}</div>
          </div>
        ))}
      </div>
      <div className="rail-foot">
        <div className="nav-item"><div className="glyph">⌕</div><div>Find</div></div>
        <div className="nav-item"><div className="glyph">?</div><div>Help</div></div>
      </div>
    </div>
  );
}

/* ---------- Status Bar (bottom) ---------- */
function StatusBar({ tenant }) {
  const [clock, setClock] = useState(new Date());
  const [latency, setLatency] = useState(38);
  const conn = useConnectionState ? useConnectionState() : { status: "unknown" };
  useEffect(() => {
    const i = setInterval(() => {
      setClock(new Date());
      setLatency(30 + Math.floor(Math.random() * 22));
    }, 1500);
    return () => clearInterval(i);
  }, []);
  const t = clock.toISOString().slice(11, 19);
  const isLive = conn.status === "live";
  const isDown = conn.status === "down";
  return (
    <div className="statusbar">
      <div className={"seg " + (isLive ? "ok" : isDown ? "alert" : "")}>
        <span className="pulse" style={{ background: isLive ? "var(--approved)" : isDown ? "var(--rejected)" : "var(--changes)" }}></span>
        <span className="label">Conn</span>
        <span className="val">{isLive ? window.AL_API.getBaseUrl().replace(/^https?:\/\//, "") : isDown ? "mock fallback" : "connecting…"}</span>
      </div>
      <div className="seg">
        <span className="label">Graph</span>
        <span className="val">{tenant.graph}</span>
      </div>
      <div className="seg">
        <span className="label">Namespace</span>
        <span className="val">{tenant.namespace}</span>
      </div>
      <div className="seg">
        <span className="label">Last sync</span>
        <span className="val">02:11:08</span>
      </div>
      <div className="spacer" />
      <div className="seg alert">
        <span className="label">Attention</span>
        <span className="val">7</span>
      </div>
      <div className="seg">
        <span className="label">Latency</span>
        <span className="val">{latency}ms</span>
      </div>
      <div className="seg">
        <span className="label">UTC</span>
        <span className="val tnum">{t}</span>
      </div>
      <div className="seg">
        <span className="label">Build</span>
        <span className="val">v2.4.0-rc.3</span>
      </div>
    </div>
  );
}

/* ---------- Panel ---------- */
function Panel({ eyebrow, title, count, actions, children, nopad, style }) {
  return (
    <div className="panel" style={style}>
      <div className="panel-head">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        {title && <span className="title">{title}</span>}
        {count != null && <span className="ct">{count}</span>}
        {actions && <div className="actions">{actions}</div>}
      </div>
      <div className={"panel-body" + (nopad ? " nopad" : "")}>{children}</div>
    </div>
  );
}

/* ---------- Pill ---------- */
function Pill({ kind, children }) {
  return <span className={"pill " + (kind || "")}><span className="dot" />{children}</span>;
}

/* ---------- Confidence Bar ---------- */
function ConfBar({ value }) {
  if (value == null) return <span className="faint mono">—</span>;
  const pct = Math.round(value * 100);
  return (
    <span className="conf">
      <span className="bar-mini"><i style={{ width: pct + "%" }} /></span>
      <span>{pct}%</span>
    </span>
  );
}

/* ---------- Filter Chip ---------- */
function Chip({ active, onClick, count, children }) {
  return (
    <span className={"chip" + (active ? " active" : "")} onClick={onClick}>
      {children}
      {count != null && <span className="ct">{count}</span>}
    </span>
  );
}

/* ---------- JSON renderer (lightweight) ---------- */
function JsonView({ data }) {
  function render(v, indent) {
    const pad = "  ".repeat(indent);
    if (v === null) return <span className="nul">null</span>;
    if (typeof v === "string") return <span className="str">"{v}"</span>;
    if (typeof v === "number") return <span className="num">{v}</span>;
    if (typeof v === "boolean") return <span className="num">{String(v)}</span>;
    if (Array.isArray(v)) {
      return (
        <>
          {"["}
          {v.map((x, i) => (
            <span key={i}>{"\n" + pad + "  "}{render(x, indent + 1)}{i < v.length - 1 ? "," : ""}</span>
          ))}
          {"\n" + pad + "]"}
        </>
      );
    }
    if (typeof v === "object") {
      const entries = Object.entries(v);
      return (
        <>
          {"{"}
          {entries.map(([k, val], i) => (
            <span key={k}>{"\n" + pad + "  "}<span className="key">"{k}"</span>: {render(val, indent + 1)}{i < entries.length - 1 ? "," : ""}</span>
          ))}
          {"\n" + pad + "}"}
        </>
      );
    }
    return String(v);
  }
  return <pre className="code">{render(data, 0)}</pre>;
}

/* ---------- Sparkline ---------- */
function Sparkline({ data, width = 60, height = 22, color }) {
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const pts = data.map((v, i) => `${i * step},${height - ((v - min) / range) * (height - 2) - 1}`).join(" ");
  const areaPts = `0,${height} ${pts} ${width},${height}`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} className="spark">
      <polygon className="spark-area" points={areaPts} style={color ? { fill: color } : null} />
      <polyline className="spark-line" points={pts} style={color ? { stroke: color } : null} />
    </svg>
  );
}

Object.assign(window, { TopBar, Rail, StatusBar, Panel, Pill, ConfBar, Chip, JsonView, Sparkline });
