/* Aletheia shared components — exposed to window for cross-script use */

const { useState, useEffect, useRef, useMemo } = React;

function isZhUI(language) {
  return String(language || "").toLowerCase().startsWith("zh");
}

function tUI(language, en, zh) {
  return isZhUI(language) ? zh : en;
}

const COUNTRY_NAMES_UI = {
  ARE: "United Arab Emirates",
  CHN: "China",
  DEU: "Germany",
  EGY: "Egypt",
  FRA: "France",
  GBR: "United Kingdom",
  GMB: "Gambia",
  IND: "India",
  IRN: "Iran",
  JPN: "Japan",
  KOR: "South Korea",
  RUS: "Russia",
  SAU: "Saudi Arabia",
  SGP: "Singapore",
  USA: "United States",
};

const COMMON_RESULT_ZH_UI = {
  "Bab el-Mandeb risk propagation identifies countries for immediate review": "Bab el-Mandeb 风险传播识别需立即复核的国家",
  "Hazard-adjusted chokepoint risk should drive review priority": "咽喉点复核优先级应纳入风险因子调整",
  "Single chokepoint dependency creates concentrated country exposure": "单一咽喉点依赖造成国家风险集中暴露",
  "Single-chokepoint dependency can create concentrated country exposure": "单一咽喉点依赖可能造成国家暴露集中",
  "Hazard severity should be joined to dependent trade value before ranking chokepoints": "咽喉点排序前应把风险严重度与依赖贸易额关联",
  "Red Sea / Bab el-Mandeb escalation should prioritize dependent countries by systemic risk": "红海 / Bab el-Mandeb 升级风险应按系统性风险确定国家优先级",
  "Card-not-present transactions concentrate fraud risk": "非面对面交易集中欺诈风险",
  "Card-not-present transactions carry elevated fraud risk": "非面对面交易具有更高欺诈风险",
  "Verification mismatch transactions have elevated fraud rate": "验证不匹配交易欺诈率更高",
  "Missing POS entry mode may identify a weak-control channel": "缺失 POS 录入模式可能识别弱控制渠道",
  "Merchant categories concentrate fraud exposure": "商户类别集中欺诈暴露",
  "Same account/merchant/amount/day duplicate clusters indicate multi-swipe risk": "同账户/商户/金额/日期重复簇提示多次刷卡风险",
};

function countryNameUI(value) {
  const code = String(value || "").split(":").pop().trim().toUpperCase();
  const name = COUNTRY_NAMES_UI[code];
  return name ? `${name} (${code})` : value;
}

function displayCountryCodesUI(text, language) {
  if (!isZhUI(language) || text == null) return text;
  return String(text).replace(/\b(ARE|CHN|DEU|EGY|FRA|GBR|GMB|IND|IRN|JPN|KOR|RUS|SAU|SGP|USA)\b/g, code => COUNTRY_NAMES_UI[code] ? `${COUNTRY_NAMES_UI[code]} (${code})` : code);
}

function displayLabelUI(text, language) {
  if (!isZhUI(language)) return text;
  if (COMMON_RESULT_ZH_UI[text]) return COMMON_RESULT_ZH_UI[text];
  let value = displayCountryCodesUI(text, language);
  if (value == null) return value;
  value = String(value);
  const exact = {
    WebEnrichmentAgent: "网页信息增益 Agent",
    "ontology pipeline": "本体流水线",
    "proposed graph": "候选图谱",
    "review required before canonical ontology": "进入正式本体前需要审核",
    "Ontology review gate": "本体审核入口",
    "Graph proposal review gate": "图候选审核入口",
    "Finding review gate": "发现审核入口",
    "candidate finding; no automatic approval": "候选发现；不会自动批准",
    "formal graph write disabled": "正式图写入已禁用",
    "canonical ontology write disabled": "正式本体写入已禁用",
  };
  if (exact[value]) return exact[value];
  value = value.replace(/^Chokepoint enrichment · /, "咽喉点信息增益 · ");
  value = value.replace(
    "If Red Sea / Bab el-Mandeb risk rises, the first review queue should include China (CHN) ($15.1B at risk), India (IND) ($7.1B at risk), United States (USA) ($6.6B at risk). The graph path is hazard at Bab el-Mandeb -> chokepoint -> dependent country -> systemic risk metric -> analyst action.",
    "如果 Red Sea / Bab el-Mandeb 风险上升，第一批复核队列应包括 China (CHN)（$15.1B 风险暴露）、India (IND)（$7.1B 风险暴露）和 United States (USA)（$6.6B 风险暴露）。图谱路径为 Bab el-Mandeb 风险因子 -> 咽喉点 -> 依赖国家 -> 系统性风险指标 -> 分析师行动。"
  );
  if (/[:_]/.test(value)) return value;
  value = value.replace(/\bWeb enrichment\b/g, "网页信息增益");
  value = value.replace(/\bweb enrichment\b/g, "网页信息增益");
  value = value.replace(/\benrichment proposals\b/g, "信息增益候选");
  value = value.replace(/\benrichment proposal\b/g, "信息增益候选");
  return value;
}

/* ---------- TopBar ---------- */
function TopBar({ screen, tenant, role, tenants, onTenant, onTenantSelect, onRole, language, onLanguageSelect, onConn }) {
  const labelByScreen = {
    workbench: { kicker: tUI(language, "Case", "工作"),  now: tUI(language, "Workspace", "工作台") },
    reasoning: { kicker: tUI(language, "Process", "推理"), now: tUI(language, "Reasoning", "推理") },
    ontology:  { kicker: tUI(language, "Catalog", "目录"), now: tUI(language, "Ontology", "本体") },
    graph:     { kicker: tUI(language, "Explore", "探索"), now: tUI(language, "Graph", "图谱") },
    quality:   { kicker: tUI(language, "Triage", "质量"),  now: tUI(language, "Quality", "质量") },
    runtime:   { kicker: tUI(language, "Config", "配置"),  now: tUI(language, "Runtime", "运行时") },
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
        <label className="chip" title={tUI(language, "Select tenant / dataset", "选择租户 / 数据集")}>
          <span className="label">{tUI(language, "Tenant", "租户")}</span>
          <select
            value={tenant.id}
            onChange={e => onTenantSelect ? onTenantSelect(e.target.value) : onTenant && onTenant()}
            style={{
              appearance: "none",
              WebkitAppearance: "none",
              border: 0,
              outline: "none",
              background: "transparent",
              color: "var(--text)",
              font: "inherit",
              fontSize: 11,
              maxWidth: 230,
              cursor: "pointer",
              padding: 0,
            }}>
            {(tenants || [tenant]).map(t => (
              <option key={t.id} value={t.id}>{t.name || t.id}</option>
            ))}
          </select>
          <span className="caret">▾</span>
        </label>
        <div className="chip" onClick={onRole}>
          <span className="label">{tUI(language, "Role", "角色")}</span>
          <span className="val">{role}</span>
          <span className="caret">▾</span>
        </div>
        <label className="chip" title={tUI(language, "Display language", "显示语言")}>
          <span className="label">{tUI(language, "Lang", "语言")}</span>
          <select
            value={language || "en"}
            onChange={e => onLanguageSelect && onLanguageSelect(e.target.value)}
            style={{
              appearance: "none",
              WebkitAppearance: "none",
              border: 0,
              outline: "none",
              background: "transparent",
              color: "var(--text)",
              font: "inherit",
              fontSize: 11,
              cursor: "pointer",
              padding: 0,
            }}>
            <option value="en">English</option>
            <option value="zh">中文</option>
          </select>
          <span className="caret">▾</span>
        </label>
        <div className="user">
          <div className="avatar">MA</div>
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.2 }}>
            <span style={{ fontSize: 11, color: "var(--text)" }}>M. Aoki</span>
            <span style={{ fontSize: 9, color: "var(--dim)", letterSpacing: "0.08em", textTransform: "uppercase" }}>{tUI(language, "reviewer", "审核员")}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------- Left Rail ---------- */
function Rail({ screen, onNav, language }) {
  const items = [
    { id: "workbench", g: "▤", l: tUI(language, "Wbk", "工作") },
    { id: "reasoning", g: "⚡", l: tUI(language, "Rsn", "推理") },
    { id: "ontology",  g: "◇", l: tUI(language, "Ont", "本体") },
    { id: "graph",     g: "✺", l: tUI(language, "Grph", "图谱") },
    { id: "quality",   g: "△", l: tUI(language, "Qly", "质量") },
    { id: "runtime",   g: "▣", l: tUI(language, "Rtm", "运行") },
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
        <div className="nav-item"><div className="glyph">⌕</div><div>{tUI(language, "Find", "查找")}</div></div>
        <div className="nav-item"><div className="glyph">?</div><div>{tUI(language, "Help", "帮助")}</div></div>
      </div>
    </div>
  );
}

/* ---------- Status Bar (bottom) ---------- */
function StatusBar({ tenant, language }) {
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
        <span className="label">{tUI(language, "Conn", "连接")}</span>
        <span className="val">{isLive ? window.AL_API.getBaseUrl().replace(/^https?:\/\//, "") : isDown ? tUI(language, "mock fallback", "模拟回退") : tUI(language, "connecting…", "连接中…")}</span>
      </div>
      <div className="seg">
        <span className="label">{tUI(language, "Graph", "图谱")}</span>
        <span className="val">{tenant.graph}</span>
      </div>
      <div className="seg">
        <span className="label">{tUI(language, "Namespace", "命名空间")}</span>
        <span className="val">{tenant.namespace}</span>
      </div>
      <div className="seg">
        <span className="label">{tUI(language, "Last sync", "最近同步")}</span>
        <span className="val">02:11:08</span>
      </div>
      <div className="spacer" />
      <div className="seg alert">
        <span className="label">{tUI(language, "Attention", "待处理")}</span>
        <span className="val">7</span>
      </div>
      <div className="seg">
        <span className="label">{tUI(language, "Latency", "延迟")}</span>
        <span className="val">{latency}ms</span>
      </div>
      <div className="seg">
        <span className="label">{tUI(language, "Lang", "语言")}</span>
        <span className="val">{language === "zh" ? "中文" : "English"}</span>
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
