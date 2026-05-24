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

function compactText(value, limit = 96) {
  const text = String(value || "");
  return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
}

function GraphExplorer({ data, tenant }) {
  const initialParams = useMemoGX(() => {
    try { return new URLSearchParams(location.search); } catch { return new URLSearchParams(); }
  }, []);
  const tenantId = tenant ? tenant.id : "default";
  const [centerType, setCenterType] = useStateGX(initialParams.get("type") || "");
  const [centerNodeId, setCenterNodeId] = useStateGX(initialParams.get("id") || "");
  const [depth, setDepth] = useStateGX(Math.max(1, Math.min(Number(initialParams.get("depth") || 1), 3)));
  const [limit, setLimit] = useStateGX(Math.max(1, Number(initialParams.get("limit") || 200)));
  const [hoverId, setHoverId] = useStateGX(null);

  const typesQ = useApiData("instanceTypes", [tenantId, { includeDraft: true }], { fallback: [] });
  const centerTypes = Array.isArray(typesQ.data) ? typesQ.data : [];
  const centerTypeNames = centerTypes.map(t => t.type);
  const activeType = centerTypes.find(t => t.type === centerType) || null;
  const typesLoaded = typesQ.source !== "loading";
  const candidatesQ = useApiData(
    "instanceSearch",
    [tenantId, centerType, "", 25, { includeDraft: true }],
    { enabled: typesLoaded && !!activeType, fallback: [] }
  );
  const candidates = Array.isArray(candidatesQ.data) ? candidatesQ.data : [];
  const proposedQ = useApiData("graphProposedElements", [tenantId, { limit: 100 }], {
    fallback: { runs: [], elements: [] },
  });
  const proposed = proposedQ.data || { runs: [], elements: [] };

  useEffectGX(() => {
    if (typesQ.source === "loading") return;
    if (centerTypes.length === 0) {
      if (centerType) setCenterType("");
      if (centerNodeId) setCenterNodeId("");
      return;
    }
    if (!centerType || !centerTypes.some(t => t.type === centerType)) {
      setCenterType(centerTypes[0].type);
      setCenterNodeId("");
    }
  }, [tenantId, typesQ.source, JSON.stringify(centerTypeNames)]);

  useEffectGX(() => {
    if (!centerType || candidatesQ.source === "loading") return;
    if (candidates.length === 0) {
      if (centerNodeId) setCenterNodeId("");
      return;
    }
    const expectedId = `${centerType}:${centerNodeId}`;
    const match = centerNodeId && candidates.some(c => c.id === expectedId || String(c.id || "").endsWith(`:${centerNodeId}`));
    if (!match) {
      const first = candidates[0];
      setCenterNodeId(String(first.id || "").split(":").slice(1).join(":"));
    }
  }, [tenantId, centerType, candidatesQ.source, JSON.stringify(candidates.map(c => c.id))]);

  useEffectGX(() => {
    try {
      const url = new URL(location.href);
      url.searchParams.set("screen", "graph");
      url.searchParams.set("tenant", tenantId);
      if (centerType) url.searchParams.set("type", centerType); else url.searchParams.delete("type");
      if (centerNodeId) url.searchParams.set("id", centerNodeId); else url.searchParams.delete("id");
      url.searchParams.set("depth", String(depth));
      url.searchParams.set("limit", String(limit));
      history.replaceState(null, "", url.toString());
    } catch {}
  }, [tenantId, centerType, centerNodeId, depth, limit]);

  const graphQ = useApiData(
    "graphContext",
    [tenantId, { type: centerType, id: centerNodeId, depth, limit }],
    { enabled: typesLoaded && !!activeType && !!centerNodeId, fallback: null }
  );
  const isStaleG = graphQ.source === "live-stale";
  const isMockG  = graphQ.source === "mock";
  const graph = useMemoGX(() => normalizeGraph(graphQ.data, { nodes: [], edges: [] }), [graphQ.data]);

  const [selected, setSelected] = useStateGX(null);
  useEffectGX(() => {
    if (!graph.nodes.length) {
      if (selected) setSelected(null);
      return;
    }
    setSelected(prev => graph.nodes.find(n => prev && n.id === prev.id) || graph.nodes[0]);
  }, [graph]);

  const map = Object.fromEntries(graph.nodes.map(n => [n.id, n]));
  const palette = ["var(--accent)", "var(--changes)", "var(--proposed)", "var(--approved)", "var(--dim)"];
  const graphTypes = Array.from(new Set(graph.nodes.map(n => n.type).filter(Boolean)));
  const typeColors = Object.fromEntries(graphTypes.map((t, i) => [t, palette[i % palette.length]]));
  const edgeCounts = graph.edges.reduce((acc, e) => {
    const key = e.kind || "edge";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const centerLabel = centerType && centerNodeId ? `${centerType}:${centerNodeId}` : "No tenant center";

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
              tenant <span style={{ color: "var(--accent)" }}>{tenant ? tenant.id : "default"}</span> · tenant-scoped center
            </div>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Center</div>
              <div style={{ display: "flex", gap: 6 }}>
                <select className="select" style={{ width: 110 }} value={centerType} onChange={e => setCenterType(e.target.value)}>
                  {centerTypes.length === 0 && <option value="">No tenant types</option>}
                  {centerTypes.map(t => <option key={t.type} value={t.type}>{t.label || t.type}{t.approved ? "" : " · draft"}</option>)}
                </select>
                <select className="select" value={centerNodeId} onChange={e => setCenterNodeId(e.target.value)} disabled={!centerType || candidates.length === 0}>
                  {candidates.length === 0 && <option value="">No center nodes</option>}
                  {candidates.map(c => {
                    const id = String(c.id || "").split(":").slice(1).join(":");
                    return <option key={c.id} value={id}>{c.label || c.id}</option>;
                  })}
                </select>
              </div>
              <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                {activeType ? `${activeType.table} · ${activeType.ontology_artifact} · ${activeType.artifact_status || "unknown"}` : "No tenant graph center types for this tenant."}
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
            <button className="btn primary" disabled={!centerType || !centerNodeId} onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>Load graph</button>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Current scope</div>
            <dl className="kv">
              <dt>Center</dt><dd>{centerLabel}</dd>
              <dt>Nodes</dt><dd>{graph.nodes.length}</dd>
              <dt>Edges</dt><dd>{graph.edges.length}</dd>
              <dt>Depth</dt><dd>{depth}</dd>
              <dt>Limit</dt><dd>{limit}</dd>
            </dl>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Edge types</div>
            <div className="chip-row">
              {Object.keys(edgeCounts).length === 0 && <Chip count={0}>none</Chip>}
              {Object.entries(edgeCounts).map(([kind, count]) => <Chip key={kind} active count={count}>{kind}</Chip>)}
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <div style={{ padding: "var(--pad-3) var(--pad-4)" }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>Expand history</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                <div>current — load {centerLabel} · depth {depth} · {graph.nodes.length} nodes</div>
                <div>tenant — {tenantId} · {tenant?.graph || "graph db unknown"}</div>
                <div>source — tenant center list from `/api/instances/types?include_draft=1`</div>
              </div>
            </div>
          </div>
        </div>

        {/* CENTER — canvas */}
        <div className="col" style={{ overflow: "hidden" }}>
          <div className="graph-canvas">
            {!centerType || !centerNodeId ? (
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                No tenant center nodes for tenant {tenantId}.
              </div>
            ) : graphQ.source === "error" || graphQ.source === "loading" ? (
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
                <ApiStatus q={graphQ} what="graph context" />
              </div>
            ) : graph.nodes.length === 0 || !selected ? (
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
          <ProposedGraphPanel proposed={proposed} loading={proposedQ.loading} source={proposedQ.source} />
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

function ProposedGraphPanel({ proposed, loading, source }) {
  const runs = proposed?.runs || [];
  const elements = proposed?.elements || [];
  const counts = elements.reduce((acc, item) => {
    acc[item.element_type] = (acc[item.element_type] || 0) + 1;
    return acc;
  }, {});
  const latestRun = runs[0] || null;
  const findings = elements.filter(item => item.element_type === "finding");
  return (
    <div className="section">
      <div className="section-head">
        <span>Proposed graph space</span>
        <span className="ct">{loading ? "loading" : `${elements.length} draft`}</span>
      </div>
      <div className="section-body">
        <div className="chip-row" style={{ marginBottom: 10 }}>
          <Chip active count={counts.node || 0}>nodes</Chip>
          <Chip active count={counts.edge || 0}>edges</Chip>
          <Chip active count={counts.finding || 0}>findings</Chip>
        </div>
        {latestRun ? (
          <dl className="kv" style={{ marginBottom: 12 }}>
            <dt>Run</dt><dd>{latestRun.run_key}</dd>
            <dt>Status</dt><dd>{latestRun.status} · canonical writes disabled</dd>
            <dt>Skipped</dt><dd>{(latestRun.skipped_sources || []).length}</dd>
          </dl>
        ) : (
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
            No proposed graph elements for this tenant.
          </div>
        )}
        {findings.map(item => {
          const profile = item.payload?.deep_graph_profile || {};
          return (
            <div key={item.element_key} style={{ border: "1px solid var(--line)", padding: 10, marginBottom: 10, background: "var(--bg-2)" }}>
              <div className="eyebrow accent">deep graph finding · draft</div>
              <div style={{ color: "var(--text)", fontWeight: 600, marginTop: 4 }}>{item.name}</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                {profile.path_label || compactText(item.payload?.conclusion, 140)}
              </div>
              <dl className="kv" style={{ marginTop: 8 }}>
                <dt>Confidence</dt><dd>{Math.round((item.confidence || 0) * 100)}%</dd>
                <dt>Evidence</dt><dd>{(item.evidence_refs || []).join(", ") || item.source_url || "—"}</dd>
              </dl>
            </div>
          );
        })}
        <div style={{ maxHeight: 220, overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {elements.map(item => (
            <div key={item.element_key} style={{ borderBottom: "1px solid var(--line-soft)", paddingBottom: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: "var(--text)", fontSize: 12 }}>{item.name}</span>
                <span className="ct">{item.element_type}</span>
              </div>
              <div style={{ fontFamily: "var(--font-mono)", color: "var(--muted)", fontSize: 10 }}>
                {item.status} · {item.source_url || "source unknown"}
              </div>
            </div>
          ))}
        </div>
        {source === "error" && (
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--rejected)" }}>
            Proposed graph API unavailable.
          </div>
        )}
      </div>
    </div>
  );
}

function BigGraph({ data, selected, onSelect, hoverId, setHoverId }) {
  const map = Object.fromEntries(data.nodes.map(n => [n.id, n]));
  const palette = ["var(--accent)", "var(--changes)", "var(--proposed)", "var(--approved)", "var(--dim)"];
  const graphTypes = Array.from(new Set(data.nodes.map(n => n.type).filter(Boolean)));
  const typeColors = Object.fromEntries(graphTypes.map((t, i) => [t, palette[i % palette.length]]));
  const sel = selected ? selected.id : null;
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
