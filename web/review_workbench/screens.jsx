/* Aletheia — Ontology browse, Quality dashboard, Runtime */

const { useState: useStateXS } = React;

/* ---------------- ONTOLOGY ---------------- */
function Ontology({ data }) {
  const [active, setActive] = useStateXS("ObjectType");
  const grouped = {
    ObjectType: data.ARTIFACTS.filter(a => a.type === "ObjectType"),
    LinkType:   data.ARTIFACTS.filter(a => a.type === "LinkType"),
    Property:   data.ARTIFACTS.filter(a => a.type === "Property"),
  };

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className={"tab" + (active === "ObjectType" ? " active" : "")} onClick={() => setActive("ObjectType")}>Object Types <span className="ct">{grouped.ObjectType.length}</span></div>
          <div className={"tab" + (active === "LinkType" ? " active" : "")} onClick={() => setActive("LinkType")}>Link Types <span className="ct">{grouped.LinkType.length}</span></div>
          <div className={"tab" + (active === "Property" ? " active" : "")} onClick={() => setActive("Property")}>Properties <span className="ct">{grouped.Property.length}</span></div>
        </div>
        <div className="spacer" />
        <button className="tool">⤓ Export schema</button>
        <button className="tool primary">+ Propose type</button>
      </div>

      <div className="ontology-cols" style={{ flex: 1, minHeight: 0 }}>
        {/* tree */}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Catalog · {active}s</div>
            <input className="input" placeholder="filter by name or key" style={{ marginTop: 8 }} />
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            <div className="artifact-list">
              {grouped[active].map(a => (
                <div key={a.id} className={"artifact-row " + a.status + (a.id === "LT-RPT-014" ? " selected" : "")}>
                  <div className="ar-bar" />
                  <div className="ar-main">
                    <div className="ar-top">
                      <span className="type">{a.type === "ObjectType" ? "OBJ" : a.type === "LinkType" ? "LINK" : "PROP"}</span>
                      <span>·</span>
                      <span className="key">{a.id}</span>
                    </div>
                    <div className="ar-title">{a.title}</div>
                    <div className="ar-meta">
                      <span>v{a.version}</span>
                      <span>{a.agent}</span>
                      <span>conf {Math.round(a.confidence * 100)}%</span>
                    </div>
                  </div>
                  <div className="ar-right">{a.status}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* schema diagram */}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Schema diagram</div>
            <div style={{ marginTop: 4, fontSize: 13, color: "var(--text)" }}>Approved + proposed types (tenant scope)</div>
          </div>
          <div style={{ flex: 1, padding: 20, overflow: "auto" }}>
            <SchemaDiagram />
          </div>
        </div>

        {/* coverage table */}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Coverage</div>
            <div style={{ marginTop: 4, fontSize: 13, color: "var(--text)" }}>Where each type is referenced</div>
          </div>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", overflow: "auto" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Reasoning templates referencing Employee</div>
            <div className="hbar"><span className="lbl">workload-bal</span><span className="track"><i style={{ width: "94%" }} /></span><span className="num">94</span></div>
            <div className="hbar"><span className="lbl">concentration</span><span className="track"><i style={{ width: "88%" }} /></span><span className="num">88</span></div>
            <div className="hbar"><span className="lbl">tenure-bands</span><span className="track"><i style={{ width: "62%" }} /></span><span className="num">62</span></div>
            <div className="hbar"><span className="lbl">span-of-ctrl</span><span className="track"><i style={{ width: "44%" }} /></span><span className="num">44</span></div>
            <div className="hbar"><span className="lbl">attrition-risk</span><span className="track"><i style={{ width: "31%" }} /></span><span className="num">31</span></div>

            <div className="eyebrow" style={{ marginBottom: 8, marginTop: 18 }}>Source tables</div>
            <dl className="kv">
              <dt>hr.employees</dt><dd>218 rows · daily</dd>
              <dt>hr.orgchart</dt><dd>14 rows · weekly</dd>
              <dt>cal.events</dt><dd>9.4k rows · streaming</dd>
              <dt>sf.opportunities</dt><dd>1.2k rows · hourly</dd>
            </dl>

            <div className="eyebrow" style={{ marginBottom: 8, marginTop: 18 }}>Issues</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: "var(--font-mono)", fontSize: 11 }}>
              <div style={{ display: "flex", gap: 8, color: "var(--changes)" }}><span>●</span><span>2 properties below 0.65 confidence</span></div>
              <div style={{ display: "flex", gap: 8, color: "var(--rejected)" }}><span>●</span><span>1 unresolved conflict in ReportsTo</span></div>
              <div style={{ display: "flex", gap: 8, color: "var(--proposed)" }}><span>●</span><span>3 proposed types awaiting review</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SchemaDiagram() {
  // simple ER-style boxes
  const boxes = [
    { id: "Employee", x: 200, y: 60,  w: 200, h: 130, status: "approved", props: ["employee_id","first_name","last_name","title","manager_id","tenure_band"] },
    { id: "Order",    x: 540, y: 60,  w: 200, h: 130, status: "approved", props: ["order_id","value","status","value_band"] },
    { id: "Customer", x: 540, y: 270, w: 200, h: 110, status: "proposed", props: ["customer_id","name","segment"] },
    { id: "Region",   x: 200, y: 280, w: 200, h: 90,  status: "rejected", props: ["code","name"] },
  ];
  const links = [
    { from: "Employee", to: "Employee", label: "ReportsTo", curve: "self", status: "proposed" },
    { from: "Order",    to: "Employee", label: "OwnedBy",   status: "approved" },
    { from: "Order",    to: "Customer", label: "PlacedBy",  status: "proposed" },
    { from: "Customer", to: "Region",   label: "InRegion",  status: "rejected" },
  ];

  const statusColor = {
    approved: "var(--approved)",
    proposed: "var(--proposed)",
    rejected: "var(--rejected)",
    changes:  "var(--changes)",
  };
  const m = Object.fromEntries(boxes.map(b => [b.id, b]));

  function anchor(a, b) {
    // centers
    const ax = a.x + a.w/2, ay = a.y + a.h/2;
    const bx = b.x + b.w/2, by = b.y + b.h/2;
    // pick nearest edge points
    const dx = bx - ax, dy = by - ay;
    let sx, sy, tx, ty;
    if (Math.abs(dx) > Math.abs(dy)) {
      sx = dx > 0 ? a.x + a.w : a.x;
      sy = ay;
      tx = dx > 0 ? b.x : b.x + b.w;
      ty = by;
    } else {
      sx = ax;
      sy = dy > 0 ? a.y + a.h : a.y;
      tx = bx;
      ty = dy > 0 ? b.y : b.y + b.h;
    }
    return { sx, sy, tx, ty };
  }

  return (
    <svg viewBox="0 0 940 420" style={{ width: "100%", height: "100%", maxHeight: 420 }}>
      <defs>
        <pattern id="grid-bg" width="20" height="20" patternUnits="userSpaceOnUse">
          <path d="M 20 0 L 0 0 0 20" fill="none" stroke="var(--line-soft)" strokeWidth="0.5" />
        </pattern>
        <marker id="er-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" />
        </marker>
      </defs>
      <rect width="940" height="420" fill="url(#grid-bg)" />

      {/* links */}
      {links.map((l, i) => {
        if (l.curve === "self") {
          const b = m[l.from];
          const cx = b.x + b.w + 30;
          const cy = b.y + b.h / 2;
          return (
            <g key={i}>
              <path d={`M ${b.x + b.w} ${b.y + 30} C ${cx + 30} ${b.y + 30}, ${cx + 30} ${b.y + b.h - 30}, ${b.x + b.w} ${b.y + b.h - 30}`}
                    fill="none" stroke={statusColor[l.status]} strokeDasharray={l.status === "proposed" ? "4 3" : ""} strokeWidth="1.4" markerEnd="url(#er-arrow)" />
              <text x={cx + 36} y={cy + 4} fontSize="10" fontFamily="var(--font-mono)"
                    fill={statusColor[l.status]}
                    style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
                {l.label}
              </text>
            </g>
          );
        }
        const a = m[l.from], b = m[l.to];
        const { sx, sy, tx, ty } = anchor(a, b);
        const mx = (sx + tx) / 2, my = (sy + ty) / 2;
        return (
          <g key={i}>
            <line x1={sx} y1={sy} x2={tx} y2={ty}
                  stroke={statusColor[l.status]}
                  strokeDasharray={l.status === "proposed" ? "4 3" : l.status === "rejected" ? "2 4" : ""}
                  strokeWidth="1.4" markerEnd="url(#er-arrow)" />
            <rect x={mx - 38} y={my - 9} width="76" height="18" fill="var(--bg-1)" stroke={statusColor[l.status]} />
            <text x={mx} y={my + 4} textAnchor="middle" fontSize="10" fontFamily="var(--font-mono)"
                  fill={statusColor[l.status]}
                  style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
              {l.label}
            </text>
          </g>
        );
      })}

      {/* boxes */}
      {boxes.map(b => (
        <g key={b.id}>
          <rect x={b.x} y={b.y} width={b.w} height={b.h}
                fill="var(--bg-2)" stroke={statusColor[b.status]} strokeWidth="1.5" />
          <rect x={b.x} y={b.y} width={b.w} height={26} fill="var(--bg-3)" stroke={statusColor[b.status]} strokeWidth="1.5" />
          <text x={b.x + 10} y={b.y + 17} fontSize="12" fontFamily="var(--font-sans)" fill="var(--text)" fontWeight="600">{b.id}</text>
          <text x={b.x + b.w - 10} y={b.y + 17} textAnchor="end" fontSize="9" fontFamily="var(--font-mono)"
                fill={statusColor[b.status]}
                style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
            {b.status}
          </text>
          {b.props.map((p, j) => (
            <text key={j} x={b.x + 10} y={b.y + 44 + j * 14}
                  fontSize="11" fontFamily="var(--font-mono)" fill="var(--text-dim)">
              {p}
            </text>
          ))}
        </g>
      ))}
    </svg>
  );
}

/* ---------------- QUALITY ---------------- */
function Quality({ data }) {
  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className="tab active">Attention Queue <span className="ct">{data.ATTENTION.length}</span></div>
          <div className="tab">Sandbox Gaps <span className="ct">4</span></div>
          <div className="tab">Agents <span className="ct">5</span></div>
          <div className="tab">Trends</div>
        </div>
        <div className="spacer" />
        <button className="tool">⤓ Triage report</button>
      </div>

      <div className="metric-grid">
        <div className="metric-card">
          <div className="label">Draft findings</div>
          <div className="val">14</div>
          <div className="sub">awaiting review</div>
          <Sparkline data={data.SPARK} />
        </div>
        <div className="metric-card">
          <div className="label">Low confidence</div>
          <div className="val warn">6</div>
          <div className="sub">below 0.65</div>
          <Sparkline data={[2,3,2,4,3,5,4,6,5,7,6,6]} color="oklch(0.78 0.14 75)" />
        </div>
        <div className="metric-card">
          <div className="label">Blocked runs</div>
          <div className="val crit">2</div>
          <div className="sub">approved-only / gaps</div>
          <Sparkline data={[1,1,2,1,2,3,2,1,2,3,2,2]} color="oklch(0.66 0.18 25)" />
        </div>
        <div className="metric-card">
          <div className="label">Agent policy</div>
          <div className="val warn">1</div>
          <div className="sub">violation flagged</div>
          <Sparkline data={[0,0,0,1,1,2,1,1,1,1,1,1]} color="oklch(0.78 0.14 75)" />
        </div>
        <div className="metric-card">
          <div className="label">Sandbox gate</div>
          <div className="val ok">OPEN</div>
          <div className="sub">negative control</div>
          <svg className="spark" viewBox="0 0 60 22"><line x1="0" y1="11" x2="60" y2="11" stroke="oklch(0.74 0.13 165)" strokeDasharray="3 2" /></svg>
        </div>
      </div>

      <div className="quality-grid">
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)", display: "flex", alignItems: "center" }}>
            <div className="eyebrow accent">Attention queue</div>
            <span className="spacer" />
            <div className="chip-row">
              <Chip active count={3}>Crit</Chip>
              <Chip active count={3}>Warn</Chip>
              <Chip count={1}>Info</Chip>
            </div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {data.ATTENTION.map((a, i) => (
              <div key={i} className={"attention-row " + a.sev}>
                <div className="a-bar" />
                <div className="reason">{a.reason}</div>
                <div className="body-x">
                  <div className="title">{a.title}</div>
                  <div className="meta">{a.meta}</div>
                </div>
                <div className="conf">{a.conf}</div>
                <div className="age">{a.age}</div>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Approved-only gate · missing artifacts</div>
            <div style={{ marginTop: 4, fontSize: 12, color: "var(--muted)" }}>Templates that cannot run because dependent types are not yet approved</div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {[
              { tpl: "concentration-risk",  needs: "LinkType.ReportsTo",         status: "proposed" },
              { tpl: "customer-segmentation",needs: "ObjectType.Customer",       status: "proposed" },
              { tpl: "territorial-analysis", needs: "ObjectType.Region",         status: "rejected" },
              { tpl: "value-banded-orders",  needs: "Property.Order.value_band", status: "changes" },
            ].map((row, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "3px 1fr auto", borderBottom: "1px solid var(--line-soft)", alignItems: "stretch" }}>
                <div style={{ background: row.status === "rejected" ? "var(--rejected)" : row.status === "changes" ? "var(--changes)" : "var(--proposed)" }} />
                <div style={{ padding: "10px 14px" }}>
                  <div style={{ fontSize: 13, color: "var(--text)" }}>{row.tpl}</div>
                  <div style={{ marginTop: 3, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)" }}>
                    needs <span style={{ color: "var(--accent)" }}>{row.needs}</span>
                  </div>
                </div>
                <div style={{ padding: "10px 14px", display: "flex", alignItems: "center" }}>
                  <Pill kind={row.status}>{row.status}</Pill>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- RUNTIME ---------------- */
function Runtime({ data }) {
  const [sel, setSel] = useStateXS(data.RUNTIMES[0]);
  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className="tab active">CLI Agents <span className="ct">{data.RUNTIMES.length}</span></div>
          <div className="tab">Policies <span className="ct">3</span></div>
          <div className="tab">Audit Log</div>
          <div className="tab">Tenants</div>
        </div>
        <div className="spacer" />
        <button className="tool">⟲ Refresh</button>
        <button className="tool primary">+ Register agent</button>
      </div>

      <div className="wb">
        {/* list */}
        <div className="col" style={{ borderRight: "1px solid var(--line)" }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">AI Runtime</div>
            <div style={{ fontSize: 13, marginTop: 4, color: "var(--text)" }}>Allowlisted CLI agents</div>
            <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              ⚠ default-deny · secrets not forwarded
            </div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {data.RUNTIMES.map(r => (
              <div key={r.id} className={"runtime-row " + r.status + (sel.id === r.id ? " selected" : "")} onClick={() => setSel(r)}>
                <div className="r-bar" />
                <div className="r-body">
                  <div className="r-name">
                    <strong>{r.name}</strong>
                    {!r.enabled && <span className="pill" style={{ fontSize: 9 }}>disabled</span>}
                  </div>
                  <div className="r-meta">
                    <span className={r.status}>● {r.status === "ok" ? "healthy" : r.status === "warn" ? "degraded" : "down"}</span>
                    <span> · last {r.lastRun}</span>
                    <span> · {r.runs24h} runs / 24h</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* detail */}
        <div className="col" style={{ display: "flex", flexDirection: "column" }}>
          <div className="art-header">
            <div className="crumb">
              <span className="type">CLI Agent</span>
              <span className="sep">/</span>
              <span>{sel.id}</span>
              <span className="sep">·</span>
              <span>template {sel.template}</span>
              <span style={{ marginLeft: "auto" }}>
                <Pill kind={sel.status === "ok" ? "approved" : sel.status === "warn" ? "changes" : "rejected"}>
                  {sel.status === "ok" ? "healthy" : sel.status === "warn" ? "degraded" : "down"}
                </Pill>
              </span>
            </div>
            <h1>{sel.name}</h1>
            <p className="desc">
              {sel.name === "calendar-ingest" ? "Calendar-derived agent that proposes soft links (MentorOf, CoWorkerOf). Runs in sandbox until evidence quality clears the 0.65 threshold." :
               sel.name === "tableau.exporter" ? "Outbound exporter for approved findings. Currently disabled — credentials rotation in progress." :
               "Generative reasoning agent. Allowed templates execute against approved-only scope by default."}
            </p>
            <div className="row">
              <div className="stat"><span className="label">Binary</span><span className="val mono" style={{ fontSize: 12 }}>{sel.binary}</span></div>
              <div className="stat"><span className="label">Template</span><span className="val mono">{sel.template}</span></div>
              <div className="stat"><span className="label">Runs / 24h</span><span className="val mono">{sel.runs24h}</span></div>
              <div className="stat"><span className="label">Last invocation</span><span className="val mono">{sel.lastRun}</span></div>
              <div className="stat"><span className="label">Enabled</span><span className="val mono" style={{ color: sel.enabled ? "var(--approved)" : "var(--rejected)" }}>{sel.enabled ? "true" : "false"}</span></div>
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)", display: "flex", flexDirection: "column", gap: 16 }}>
            <Panel eyebrow="Health" title="Status checks" count="3 checks">
              <div style={{ display: "flex", flexDirection: "column" }}>
                {[
                  { ck: "binary present",        ok: true,  detail: "/usr/local/bin/anthropic-cli · 0o755" },
                  { ck: "policy resolves",       ok: true,  detail: "default_cli_policy v4 · 11 directives" },
                  { ck: "smoke-run (safe demo)", ok: sel.status === "ok", detail: sel.status === "ok" ? "round-trip 42ms · ✓ allowlisted template" : "timeout · last 03:14" },
                ].map((c, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "20px 200px 1fr", padding: "8px 0", borderBottom: i < 2 ? "1px solid var(--line-soft)" : "none", alignItems: "center", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    <span style={{ color: c.ok ? "var(--approved)" : "var(--rejected)" }}>{c.ok ? "✓" : "✕"}</span>
                    <span style={{ color: "var(--text-dim)" }}>{c.ck}</span>
                    <span style={{ color: "var(--dim)" }}>{c.detail}</span>
                  </div>
                ))}
                <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
                  <button className="btn">Run health check</button>
                  <button className="btn">Run readiness check</button>
                  <button className="btn">Safe demo</button>
                </div>
              </div>
            </Panel>

            <Panel eyebrow="Policy" title={sel.template} count="11 directives">
              <pre className="code">{`{
  "id":           "default_cli_policy",
  "version":      4,
  "default":      "deny",
  "secrets":      "never_forwarded",
  "templates":    ["safe_demo", "evidence_pack", "scoped_question"],
  "tenants":      ["acme-prod", "acme-staging"],
  "max_runs_5m":  20,
  "max_runtime_s":120,
  "evidence_required": true,
  "approved_only": true,
  "audit_log":    "/var/log/aletheia/agent.log"
}`}</pre>
            </Panel>

            <Panel eyebrow="Runs" title="Recent invocations" count={`${sel.runs24h} in 24h`} nopad>
              <div className="audit-list">
                {["02:11 invoked scoped_question — 198 tok in · 412 tok out · 38ms",
                  "02:09 invoked evidence_pack — 412 tok in · 1.1k tok out · 412ms",
                  "01:58 invoked scoped_question — 220 tok in · 388 tok out · 41ms",
                  "01:44 ⚠ template denied — caller=portal · template=raw_sql · 0ms",
                  "01:30 invoked safe_demo — 88 tok in · 142 tok out · 22ms"].map((line, i) => (
                  <div key={i} className="audit-item">
                    <span className="ts">{line.split(" ")[0]}</span>
                    <span className={"act " + (line.includes("denied") ? "rejected" : "approved")}>{line.includes("denied") ? "denied" : "ok"}</span>
                    <span className="det" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{line.slice(6)}</span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </div>

        {/* inspector */}
        <div className="col inspector">
          <div className="section">
            <div className="section-head"><span>Tenant scope</span></div>
            <div className="section-body">
              <dl className="kv">
                <dt>Tenants</dt><dd>acme-prod, acme-staging</dd>
                <dt>Graphs</dt><dd>neo4j://acme-prod</dd>
                <dt>Read</dt><dd style={{ color: "var(--approved)" }}>approved-only</dd>
                <dt>Write</dt><dd style={{ color: "var(--changes)" }}>proposals → review</dd>
              </dl>
            </div>
          </div>
          <div className="section">
            <div className="section-head"><span>Token usage · 24h</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">input</span><span className="track"><i style={{ width: "62%" }} /></span><span className="num">62k</span></div>
              <div className="hbar"><span className="lbl">output</span><span className="track"><i style={{ width: "38%" }} /></span><span className="num">38k</span></div>
              <div className="hbar"><span className="lbl">cache</span><span className="track"><i style={{ width: "84%" }} /></span><span className="num">84%</span></div>
              <Sparkline data={[3,4,3,5,4,6,5,7,6,8,7,9,8,10,9,11,10,12,11,14]} width={260} height={50} />
            </div>
          </div>
          <div className="section">
            <div className="section-head"><span>Rate budget</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">5m window</span><span className="track"><i style={{ width: "34%" }} /></span><span className="num">7/20</span></div>
              <div className="hbar"><span className="lbl">1h window</span><span className="track"><i style={{ width: "58%" }} /></span><span className="num">58/100</span></div>
              <div className="hbar"><span className="lbl">24h cap</span><span className="track"><i style={{ width: "31%" }} /></span><span className="num">312/1k</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Ontology, Quality, Runtime });
