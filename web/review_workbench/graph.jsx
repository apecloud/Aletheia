/* Aletheia — Graph Explorer */

const { useState: useStateGX, useRef: useRefGX, useEffect: useEffectGX, useMemo: useMemoGX } = React;

// radial layout for nodes that don't already have x/y
function layoutRadial(nodes, edges, opts = {}) {
  const W = opts.width || 1000, H = opts.height || 600;
  const cx = W / 2, cy = H / 2;
  const deg = {};
  edges.forEach(e => {
    deg[e.source || e.s] = (deg[e.source || e.s] || 0) + 1;
    deg[e.target || e.t] = (deg[e.target || e.t] || 0) + 1;
  });
  const centerId = (nodes.find(n => n.center) ||
                    nodes.slice().sort((a,b) => (deg[b.id]||0) - (deg[a.id]||0))[0] || {}).id;
  const periph = nodes.filter(n => n.id !== centerId);
  const placed = [];
  nodes.forEach(n => {
    if (n.id === centerId) placed.push({ ...n, x: cx, y: cy, r: 18, center: true });
  });
  const step = (Math.PI * 2) / Math.max(periph.length, 1);
  periph.forEach((n, i) => {
    const ring = 180;
    const ang = step * i - Math.PI / 2;
    placed.push({ ...n, x: cx + Math.cos(ang) * ring, y: cy + Math.sin(ang) * ring * 0.78, r: n.r || 11 });
  });
  return placed;
}

// normalize /api/graph/context response into the prototype's graph shape
function normalizeGraph(raw, fallback) {
  if (!raw || !raw.nodes) return fallback;
  const nodes = raw.nodes.map(n => ({
    id: n.id, type: n.type,
    label: n.label || (n.key_properties && (n.key_properties.name || n.key_properties.title)) || n.id,
    x: n.x, y: n.y, r: n.r,
    center: raw.center && raw.center.id === n.id,
    flag: !!n.flag,
    _raw: n,
  }));
  const needsLayout = nodes.some(n => n.x == null || n.y == null);
  const edges = (raw.edges || []).map(e => ({
    s: e.source || e.s,
    t: e.target || e.t,
    kind: e.label || e.kind || e.ontology_link || "",
    flag: e.flag || e.conflict || false,
    muted: e.muted,
    _raw: e,
  }));
  return {
    center: raw.center && raw.center.id,
    nodes: needsLayout ? layoutRadial(nodes, edges) : nodes,
    edges,
  };
}

function GraphExplorer({ data, tenant }) {
  const [centerType, setCenterType] = useStateGX("Employee");
  const [centerNodeId, setCenterNodeId] = useStateGX("4");
  const [depth, setDepth] = useStateGX(1);
  const [limit, setLimit] = useStateGX(200);
  const [hoverId, setHoverId] = useStateGX(null);

  const graphQ = useApiData(
    "graphContext",
    [tenant ? tenant.id : "default", { type: centerType, id: centerNodeId, depth, limit }],
    { fallback: data.GRAPH }
  );
  const isStaleG = graphQ.source === "live-stale";
  const isMockG  = graphQ.source === "mock";
  const graph = useMemoGX(() => normalizeGraph(graphQ.data, { nodes: [], edges: [] }), [graphQ.data]);

  const [selected, setSelected] = useStateGX(graph.nodes[0]);
  useEffectGX(() => { if (graph.nodes[0]) setSelected(graph.nodes[0]); }, [graph]);

  const map = Object.fromEntries(graph.nodes.map(n => [n.id, n]));
  const typeColors = {
    Employee: "var(--accent)",
    Order: "var(--changes)",
    Customer: "var(--proposed)",
    Region: "var(--dim)"
  };

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className="tab active">Approved Scope <span className="ct">218</span></div>
          <div className="tab">Sandbox <span className="ct">12</span></div>
          <div className="tab">Saved Views <span className="ct">5</span></div>
        </div>
        <div className="spacer" />
        {isMockG  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStaleG && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {graphQ.loading && graphQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Reload</button>
        <button className="tool">⤓ Snapshot</button>
        <button className="tool primary">↗ Open reasoning</button>
      </div>

      <div className="gx">
        {/* LEFT — scope */}
        <div className="col">
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow accent">Scope</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)", marginTop: 4 }}>
              tenant <span style={{ color: "var(--accent)" }}>{tenant ? tenant.id : "default"}</span> · approved-only
            </div>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Center</div>
              <div style={{ display: "flex", gap: 6 }}>
                <select className="select" style={{ width: 110 }} value={centerType} onChange={e => setCenterType(e.target.value)}>
                  <option>Employee</option>
                  <option>Order</option>
                  <option>Customer</option>
                </select>
                <input className="input" value={centerNodeId} onChange={e => setCenterNodeId(e.target.value)} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <div style={{ flex: 1 }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Depth</div>
                <input className="input" value={depth} onChange={e => setDepth(+e.target.value)} type="number" min={1} max={3} />
              </div>
              <div style={{ flex: 1 }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Limit</div>
                <input className="input" value={limit} onChange={e => setLimit(+e.target.value)} type="number" />
              </div>
            </div>
            <button className="btn primary" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>Load graph</button>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Current scope</div>
            <dl className="kv">
              <dt>Center</dt><dd>{centerType}:{centerNodeId}</dd>
              <dt>Nodes</dt><dd>{graph.nodes.length}</dd>
              <dt>Edges</dt><dd>{graph.edges.length}</dd>
              <dt>Depth</dt><dd>{depth}</dd>
              <dt>Limit</dt><dd>{limit}</dd>
            </dl>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Edge types</div>
            <div className="chip-row">
              <Chip active count={4}>ReportsTo</Chip>
              <Chip active count={4}>OwnedBy</Chip>
              <Chip active count={2}>PlacedBy</Chip>
              <Chip count={1}>InRegion</Chip>
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <div style={{ padding: "var(--pad-3) var(--pad-4)" }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>Expand history</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                <div>02:11 — load Employee:4 · depth 1 · 12 nodes</div>
                <div>02:09 — expand Employee:9 · +4 nodes</div>
                <div>02:04 — fit to view</div>
                <div>01:58 — load Employee:4 · depth 1 · 8 nodes</div>
              </div>
            </div>
          </div>
        </div>

        {/* CENTER — canvas */}
        <div className="col" style={{ overflow: "hidden" }}>
          <div className="graph-canvas">
            {graphQ.source === "error" || graphQ.source === "loading" ? (
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
                <ApiStatus q={graphQ} what="graph context" />
              </div>
            ) : graph.nodes.length === 0 ? (
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                Empty graph for this scope.
              </div>
            ) : (
            <BigGraph data={graph} selected={selected} onSelect={setSelected} hoverId={hoverId} setHoverId={setHoverId} />
            )}

            <div className="graph-overlay-tl">
              <div className="row">
                <div><span style={{ color: "var(--dim)" }}>NODES</span><span className="v">{graph.nodes.length}</span></div>
                <div><span style={{ color: "var(--dim)" }}>EDGES</span><span className="v">{graph.edges.length}</span></div>
                <div><span style={{ color: "var(--dim)" }}>DEPTH</span><span className="v">{depth}</span></div>
                <div><span style={{ color: "var(--dim)" }}>SOURCE</span><span className="v" style={{ color: graphQ.source === "live" ? "var(--approved)" : graphQ.source === "live-stale" ? "var(--changes)" : "var(--rejected)" }}>{graphQ.source === "live" ? "LIVE" : graphQ.source === "live-stale" ? "STALE" : graphQ.source === "loading" ? "…" : "NONE"}</span></div>
              </div>
            </div>

            <div className="graph-overlay-tr">
              <div style={{ display: "flex", gap: 12 }}>
                {Object.entries(typeColors).map(([k, c]) => (
                  <div key={k} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ width: 8, height: 8, background: c, borderRadius: "50%", display: "inline-block" }} />
                    <span>{k}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="graph-overlay-bl">
              <button className="icon-btn" title="Zoom in">+</button>
              <button className="icon-btn" title="Zoom out">−</button>
              <button className="icon-btn" title="Fit view">⌖</button>
              <button className="icon-btn" title="Focus selected">◎</button>
              <button className="icon-btn" title="Expand">⊕</button>
              <button className="icon-btn" title="Collapse">⊖</button>
            </div>

            <div className="graph-overlay-br" style={{ textTransform: "none", letterSpacing: 0, padding: 8 }}>
              <svg width="60" height="60" viewBox="0 0 60 60">
                <circle cx="30" cy="30" r="22" fill="none" stroke="var(--line-strong)" />
                <circle cx="30" cy="30" r="14" fill="none" stroke="var(--line)" />
                <line x1="30" y1="8"  x2="30" y2="52" stroke="var(--line)" />
                <line x1="8"  y1="30" x2="52" y2="30" stroke="var(--line)" />
                <text x="30" y="6"  textAnchor="middle" fontSize="7" fontFamily="var(--font-mono)" fill="var(--accent)">N</text>
                <text x="30" y="58" textAnchor="middle" fontSize="7" fontFamily="var(--font-mono)" fill="var(--muted)">S</text>
                <text x="2"  y="33" fontSize="7" fontFamily="var(--font-mono)" fill="var(--muted)">W</text>
                <text x="54" y="33" fontSize="7" fontFamily="var(--font-mono)" fill="var(--muted)">E</text>
                <circle cx="30" cy="30" r="2" fill="var(--accent)" />
              </svg>
            </div>
          </div>
        </div>

        {/* RIGHT — inspector */}
        <div className="col inspector">
          {!selected ? (
            <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
              No node selected. Load a graph from the left panel.
            </div>
          ) : (
          <>
          <div className="section">
            <div className="section-head">
              <span>Inspector</span>
              <span className="ct">{selected.type}</span>
            </div>
            <div className="section-body">
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div className="eyebrow accent">{selected.type}</div>
                <div style={{ fontSize: 16, color: "var(--text)", fontWeight: 500 }}>{selected.id}</div>
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{selected.label}</div>
              </div>
              <div style={{ marginTop: 14 }}>
                <dl className="kv">
                  <dt>Approved</dt><dd>v3 · 2026-05-10</dd>
                  <dt>Source row</dt><dd>hr.employees#{selected.id.split(":")[1]}</dd>
                  <dt>Edges in</dt><dd>{graph.edges.filter(e => e.t === selected.id).length}</dd>
                  <dt>Edges out</dt><dd>{graph.edges.filter(e => e.s === selected.id).length}</dd>
                </dl>
              </div>
              {selected.flag && (
                <div style={{ marginTop: 12, padding: 10, border: "1px solid oklch(0.66 0.18 25 / 0.4)", background: "oklch(0.66 0.18 25 / 0.08)", color: "var(--rejected)", fontFamily: "var(--font-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  Flagged · temporal overlap in ReportsTo
                </div>
              )}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Connected edges</span><span className="ct">{graph.edges.filter(e => e.s === selected.id || e.t === selected.id).length}</span></div>
            <div className="section-body" style={{ padding: 0 }}>
              {graph.edges.filter(e => e.s === selected.id || e.t === selected.id).map((e, i) => {
                const other = e.s === selected.id ? e.t : e.s;
                const dir = e.s === selected.id ? "→" : "←";
                return (
                  <div key={i} style={{ padding: "8px 14px", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
                       onClick={() => setSelected(map[other])}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{e.kind}</span>
                    <span style={{ color: "var(--dim)" }}>{dir}</span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>{other}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Scoped reasoning</span><span className="ct">draft-only</span></div>
            <div className="section-body">
              <div className="eyebrow" style={{ marginBottom: 4 }}>Question</div>
              <select className="select" style={{ marginBottom: 8 }}>
                <option>Explain this node's role in the graph</option>
                <option>Find workload / concentration risk</option>
                <option>Explain why this edge exists</option>
                <option>Find unusual neighbors in this scope</option>
              </select>
              <button className="btn primary" style={{ width: "100%" }}>Open scoped reasoning</button>
            </div>
          </div>
          </>
          )}
        </div>
      </div>
    </div>
  );
}

function BigGraph({ data, selected, onSelect, hoverId, setHoverId }) {
  const map = Object.fromEntries(data.nodes.map(n => [n.id, n]));
  const typeColors = {
    Employee: "var(--accent)",
    Order: "var(--changes)",
    Customer: "var(--proposed)",
    Region: "var(--dim)"
  };
  const sel = selected.id;
  const neighborIds = new Set();
  data.edges.forEach(e => {
    if (e.s === sel) neighborIds.add(e.t);
    if (e.t === sel) neighborIds.add(e.s);
  });

  return (
    <svg viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid meet"
         style={{ width: "100%", height: "100%", display: "block" }}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--line-strong)" />
        </marker>
        <marker id="arrow-accent" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" />
        </marker>
      </defs>

      {/* edges */}
      {data.edges.map((e, i) => {
        const s = map[e.s], t = map[e.t];
        if (!s || !t) return null;
        const involved = e.s === sel || e.t === sel;
        return (
          <g key={i}>
            <line x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                  stroke={e.flag ? "oklch(0.66 0.18 25 / 0.7)" : (involved ? "var(--accent)" : e.muted ? "var(--faint)" : "var(--line-strong)")}
                  strokeWidth={involved ? 1.8 : 1}
                  strokeDasharray={e.muted ? "4 3" : ""}
                  markerEnd={involved ? "url(#arrow-accent)" : "url(#arrow)"} />
            {involved && (
              <text x={(s.x + t.x) / 2} y={(s.y + t.y) / 2 - 6}
                    textAnchor="middle" fontSize="10" fontFamily="var(--font-mono)"
                    fill={e.flag ? "var(--rejected)" : "var(--accent)"}
                    style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
                {e.kind}
              </text>
            )}
          </g>
        );
      })}

      {/* nodes */}
      {data.nodes.map((n, i) => {
        const isSel = n.id === sel;
        const isNeighbor = neighborIds.has(n.id);
        const isHover = n.id === hoverId;
        const stroke = n.flag ? "var(--rejected)" : (isSel ? "var(--accent)" : isNeighbor ? "var(--accent-dim)" : (n.muted ? "var(--faint)" : typeColors[n.type] || "var(--text-dim)"));
        return (
          <g key={i} onClick={() => onSelect(n)}
                 onMouseEnter={() => setHoverId(n.id)}
                 onMouseLeave={() => setHoverId(null)}
                 style={{ cursor: "pointer" }}>
            {(isSel || isHover) && (
              <circle cx={n.x} cy={n.y} r={n.r + 10} fill="var(--accent-bg)" stroke="var(--accent-line)" strokeWidth="1" />
            )}
            <circle cx={n.x} cy={n.y} r={n.r}
                    fill={isSel ? "var(--accent)" : "var(--bg-2)"}
                    stroke={stroke} strokeWidth={isSel ? 2 : 1.4} />
            {isSel && <circle cx={n.x} cy={n.y} r={n.r - 7} fill="var(--bg-1)" />}
            <text x={n.x} y={n.y + n.r + 14}
                  textAnchor="middle"
                  fontSize="11" fontFamily="var(--font-mono)"
                  fill={isSel ? "var(--accent)" : "var(--text-dim)"}
                  style={{ pointerEvents: "none" }}>
              {n.id}
            </text>
            <text x={n.x} y={n.y + n.r + 26}
                  textAnchor="middle"
                  fontSize="9.5" fontFamily="var(--font-mono)"
                  fill="var(--dim)"
                  style={{ pointerEvents: "none", letterSpacing: "0.04em" }}>
              {n.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

Object.assign(window, { GraphExplorer, BigGraph });
