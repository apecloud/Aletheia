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
  const requestedGraphTab = initialParams.get("graph_tab") || "approved";
  const agentRunsRequested = requestedGraphTab === "runs";
  const normalizeGraphTab = (tab) => ["approved", "proposed", "saved"].includes(tab) ? tab : (tab === "runs" ? "proposed" : "approved");
  const graphView = "all";
  const requestedTenantId = initialParams.get("tenant") || "";
  const requestedCenterType = initialParams.get("type") || "";
  const requestedCenterNodeId = initialParams.get("id") || "";
  const [centerType, setCenterType] = useStateGX(initialParams.get("type") || "");
  const [centerNodeId, setCenterNodeId] = useStateGX(initialParams.get("id") || "");
  const [depth, setDepth] = useStateGX(Math.max(1, Math.min(Number(initialParams.get("depth") || 1), 3)));
  const [limit, setLimit] = useStateGX(Math.max(1, Number(initialParams.get("limit") || 200)));
  const [hoverId, setHoverId] = useStateGX(null);
  const [leftTab, setLeftTab] = useStateGX(normalizeGraphTab(requestedGraphTab));
  const [showAgentRunsMoved, setShowAgentRunsMoved] = useStateGX(agentRunsRequested);
  const [focusElementKey, setFocusElementKey] = useStateGX(initialParams.get("proposed_key") || "");
  const [initialScopeApplied, setInitialScopeApplied] = useStateGX(false);

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
  const selectGraphTab = (tab) => {
    setShowAgentRunsMoved(false);
    setLeftTab(normalizeGraphTab(tab));
  };

  useEffectGX(() => {
    if (typesQ.source === "loading") return;
    if (
      !initialScopeApplied &&
      requestedCenterType &&
      requestedCenterNodeId &&
      (!requestedTenantId || requestedTenantId === tenantId) &&
      centerTypes.some(t => t.type === requestedCenterType)
    ) {
      setCenterType(requestedCenterType);
      setCenterNodeId(requestedCenterNodeId);
      setInitialScopeApplied(true);
      return;
    }
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
    if (
      !centerNodeId &&
      requestedCenterType === centerType &&
      requestedCenterNodeId &&
      (!requestedTenantId || requestedTenantId === tenantId)
    ) {
      setCenterNodeId(requestedCenterNodeId);
      return;
    }
    if (!centerNodeId) {
      const first = candidates[0];
      setCenterNodeId(String(first.id || "").split(":").slice(1).join(":"));
    } else if (!match) {
      setFocusMessage(`${expectedId} is outside the visible center list; Load full graph will still include it if it exists.`);
    }
  }, [tenantId, centerType, candidatesQ.source, JSON.stringify(candidates.map(c => c.id))]);

  useEffectGX(() => {
    try {
      const url = new URL(location.href);
      url.searchParams.set("screen", "graph");
      url.searchParams.set("tenant", tenantId);
      if (centerType) url.searchParams.set("type", centerType); else url.searchParams.delete("type");
      if (centerNodeId) url.searchParams.set("id", centerNodeId); else url.searchParams.delete("id");
      url.searchParams.set("view", graphView);
      url.searchParams.set("depth", String(depth));
      url.searchParams.set("limit", String(limit));
      url.searchParams.set("graph_tab", leftTab);
      if (focusElementKey) url.searchParams.set("proposed_key", focusElementKey); else url.searchParams.delete("proposed_key");
      history.replaceState(null, "", url.toString());
    } catch {}
  }, [tenantId, centerType, centerNodeId, depth, limit, leftTab, focusElementKey, graphView]);

  const graphQ = useApiData(
    "graphContext",
    [tenantId, { type: centerType, id: centerNodeId, depth, limit, view: graphView }],
    { enabled: typesLoaded, fallback: null }
  );
  const isStaleG = graphQ.source === "live-stale";
  const isMockG  = graphQ.source === "mock";
  const graph = useMemoGX(() => normalizeGraph(graphQ.data, { nodes: [], edges: [] }), [graphQ.data]);

  const [selected, setSelected] = useStateGX(null);
  const [pendingCenterFocus, setPendingCenterFocus] = useStateGX("");
  const [focusMessage, setFocusMessage] = useStateGX("");
  useEffectGX(() => {
    setSelected(null);
    setHoverId(null);
    setPendingCenterFocus("");
    setFocusMessage("");
  }, [tenantId]);
  useEffectGX(() => {
    if (!graph.nodes.length) {
      if (selected) setSelected(null);
      return;
    }
    setSelected(prev => graph.nodes.find(n => prev && n.id === prev.id) || null);
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
  const centerKey = centerType && centerNodeId ? `${centerType}:${centerNodeId}` : "";
  const focusCenterNode = () => {
    if (!centerKey) return false;
    const match = graph.nodes.find(node => node.id === centerKey);
    if (match) {
      setSelected(match);
      setFocusMessage(`Focused ${centerKey}`);
      return true;
    }
    setFocusMessage(`${centerKey} is not in the current graph sample yet.`);
    return false;
  };
  const loadAndFocusCenter = () => {
    if (!centerKey) {
      setFocusMessage("Select a center node first.");
      return;
    }
    setPendingCenterFocus(centerKey);
    setFocusMessage(`Loading full graph for ${centerKey}…`);
    window.dispatchEvent(new CustomEvent("aletheia:retry"));
  };
  useEffectGX(() => {
    if (!pendingCenterFocus || graphQ.loading) return;
    const match = graph.nodes.find(node => node.id === pendingCenterFocus);
    if (match) {
      setSelected(match);
      setFocusMessage(`Focused ${pendingCenterFocus}`);
      setPendingCenterFocus("");
    } else if (graphQ.source !== "loading") {
      setFocusMessage(`${pendingCenterFocus} is not in the loaded full graph.`);
      setPendingCenterFocus("");
    }
  }, [pendingCenterFocus, graphQ.loading, graphQ.source, graph.nodes.map(n => n.id).join("|")]);

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="eyebrow accent">Graph Explorer</div>
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
            <div className="eyebrow accent">Graph Catalog</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)", marginTop: 4 }}>
              tenant <span style={{ color: "var(--accent)" }}>{tenant ? tenant.id : "default"}</span> · graph spaces
            </div>
            <div className="side-tabs" style={{ marginTop: 10 }}>
              <button className={"side-tab" + (leftTab === "approved" ? " active" : "")} onClick={() => selectGraphTab("approved")}>
                Approved graph <span className="ct">{graph.nodes.length}</span>
              </button>
              <button className={"side-tab" + (leftTab === "proposed" ? " active" : "")} onClick={() => selectGraphTab("proposed")}>
                Proposed graph <span className="ct">{(proposed.elements || []).length}</span>
              </button>
              <button className={"side-tab" + (leftTab === "saved" ? " active" : "")} onClick={() => selectGraphTab("saved")}>
                Saved views <span className="ct">0</span>
              </button>
            </div>
            {showAgentRunsMoved && (
              <div style={{ marginTop: 10, border: "1px solid var(--accent-line)", background: "var(--accent-bg)", padding: 10 }}>
                <div className="eyebrow accent">Automatic runs moved</div>
                <div style={{ marginTop: 5, fontSize: 12, color: "var(--text-dim)", lineHeight: 1.45 }}>
                  Crawl, enrichment, and reasoning runs are managed from Workspace.
                </div>
                <a className="btn ghost" style={{ marginTop: 8 }} href={`/?screen=workbench&tenant=${encodeURIComponent(tenantId)}&workspace_tab=agents`}>
                  Open Workspace agents
                </a>
              </div>
            )}
          </div>

          {leftTab === "approved" && <>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Center</div>
              <div style={{ display: "flex", gap: 6 }}>
                <select className="select" style={{ width: 110 }} value={centerType} onChange={e => { setCenterType(e.target.value); setCenterNodeId(""); setSelected(null); setFocusMessage(""); }}>
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
              <button className="btn ghost" style={{ marginTop: 8, width: "100%" }} disabled={!centerKey || !graph.nodes.some(node => node.id === centerKey)} onClick={focusCenterNode}>
                Focus center in full graph
              </button>
              <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
                Default view shows all approved tenant graph nodes. Selecting a node only changes focus contrast.
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
            <button className="btn primary" disabled={!centerKey || graphQ.loading} onClick={loadAndFocusCenter}>Load full graph</button>
            {focusMessage && (
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: focusMessage.includes("not in") ? "var(--changes)" : "var(--muted)", lineHeight: 1.4 }}>
                {focusMessage}
              </div>
            )}
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Current scope</div>
            <dl className="kv">
              <dt>Center</dt><dd>{centerLabel}</dd>
              <dt>Nodes</dt><dd>{graph.nodes.length}</dd>
              <dt>Edges</dt><dd>{graph.edges.length}</dd>
              <dt>View</dt><dd>all approved nodes</dd>
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
                <div>current — all approved tenant graph · {graph.nodes.length} nodes</div>
                <div>tenant — {tenantId} · {tenant?.graph || "graph db unknown"}</div>
                <div>focus — {selected ? selected.id : "none; full graph contrast"}</div>
              </div>
            </div>
          </div>
          </>}

          {leftTab === "proposed" && (
            <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <ProposedGraphPanel tenantId={tenantId} proposed={proposed} loading={proposedQ.loading} source={proposedQ.source} focusElementKey={focusElementKey} compact />
            </div>
          )}

          {leftTab === "saved" && (
            <div style={{ flex: 1, padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
              No saved graph views for tenant {tenantId}.
            </div>
          )}
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
                <div><span style={{ color: "var(--dim)" }}>FOCUS</span><span className="v">{selected ? "ON" : "ALL"}</span></div>
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
              <button className="icon-btn" title="Clear focus" disabled={!selected} onClick={() => setSelected(null)}>◎</button>
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
              All approved tenant graph nodes are visible. Select a node to highlight its local context and dim unrelated nodes and edges.
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
                  <dt>Status</dt><dd>{selected._raw?.status || "approved"}</dd>
                  <dt>Source row</dt><dd>{selected._raw?.source_table || "source"}#{selected._raw?.source_pk || selected.id.split(":").slice(1).join(":")}</dd>
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

function ProposedGraphPanel({ tenantId, proposed, loading, source, focusElementKey }) {
  const [selectedElement, setSelectedElement] = useStateGX(null);
  const [kindFilter, setKindFilter] = useStateGX("all");
  const [selectedKeys, setSelectedKeys] = useStateGX([]);
  const [reviewReason, setReviewReason] = useStateGX("");
  const [reviewBusy, setReviewBusy] = useStateGX(false);
  const [reviewMessage, setReviewMessage] = useStateGX(null);
  const runs = proposed?.runs || [];
  const elements = proposed?.elements || [];
  const counts = elements.reduce((acc, item) => {
    acc[item.element_type] = (acc[item.element_type] || 0) + 1;
    return acc;
  }, {});
  const latestRun = runs[0] || null;
  const filteredElements = kindFilter === "all"
    ? elements
    : elements.filter(item => item.element_type === kindFilter);
  const findings = filteredElements.filter(item => item.element_type === "finding");
  const selectedSet = new Set(selectedKeys);
  const selectedInFilter = filteredElements.filter(item => selectedSet.has(item.element_key));
  useEffectGX(() => {
    if (!selectedElement) return;
    const latest = elements.find(item => item.element_key === selectedElement.element_key);
    if (latest && latest !== selectedElement) setSelectedElement(latest);
  }, [JSON.stringify(elements.map(item => `${item.element_key}:${item.status}`))]);
  useEffectGX(() => {
    const valid = new Set(elements.map(item => item.element_key));
    const nextKeys = selectedKeys.filter(key => valid.has(key));
    if (nextKeys.length !== selectedKeys.length) setSelectedKeys(nextKeys);
  }, [JSON.stringify(elements.map(item => item.element_key))]);
  useEffectGX(() => {
    if (!focusElementKey) return;
    const match = elements.find(item => item.element_key === focusElementKey);
    if (!match) return;
    setSelectedElement(match);
    setKindFilter(match.element_type || "all");
    setReviewMessage(null);
  }, [focusElementKey, JSON.stringify(elements.map(item => item.element_key))]);
  function selectKind(nextKind) {
    setKindFilter(nextKind);
    setReviewMessage(null);
    const nextItems = nextKind === "all"
      ? elements
      : elements.filter(item => item.element_type === nextKind);
    if (!nextItems.some(item => item.element_key === selectedElement?.element_key)) {
      setSelectedElement(nextItems[0] || null);
    }
  }
  function toggleSelection(key, checked) {
    setSelectedKeys(keys => {
      if (checked) return keys.includes(key) ? keys : [...keys, key];
      return keys.filter(item => item !== key);
    });
  }
  function selectVisible() {
    setSelectedKeys(Array.from(new Set([...selectedKeys, ...filteredElements.map(item => item.element_key)])));
    if (!selectedElement && filteredElements[0]) setSelectedElement(filteredElements[0]);
    setReviewMessage(null);
  }
  function clearSelection() {
    setSelectedKeys([]);
    setReviewMessage(null);
  }
  async function reviewSelected(action) {
    const reason = reviewReason.trim();
    if (selectedKeys.length === 0) {
      setReviewMessage({ kind: "error", text: "Select at least one proposed graph element." });
      return;
    }
    if ((action === "reject" || action === "needs-evidence") && !reason) {
      setReviewMessage({ kind: "error", text: "Review reason is required for batch reject / needs evidence." });
      return;
    }
    setReviewBusy(true);
    setReviewMessage(null);
    try {
      const result = await window.AL_API.reviewGraphProposedElementsBatch(tenantId, selectedKeys, action, {
        reason,
        reviewer: "Itachi",
      });
      const failed = result?.failed_count || 0;
      const ok = result?.ok_count || 0;
      const failedItems = (result?.results || []).filter(item => !item.ok);
      const selectedResult = (result?.results || []).find(item => item.ok && item.element?.element_key === selectedElement?.element_key);
      if (selectedResult?.element) setSelectedElement(selectedResult.element);
      if (!failed) {
        setSelectedKeys([]);
        setReviewReason("");
      }
      setReviewMessage({
        kind: failed ? "error" : "ok",
        text: failed
          ? `${ok} recorded, ${failed} failed · ${failedItems.map(item => item.element_key || item.error).slice(0, 2).join(", ")}`
          : `${ok} graph proposal review decisions recorded · formal graph unchanged`,
      });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setReviewMessage({ kind: "error", text: err.message || String(err) });
    } finally {
      setReviewBusy(false);
    }
  }
  async function reviewElement(action) {
    if (!selectedElement) return;
    const reason = reviewReason.trim();
    if ((action === "reject" || action === "needs-evidence") && !reason) {
      setReviewMessage({ kind: "error", text: "Review reason is required for reject / needs evidence." });
      return;
    }
    setReviewBusy(true);
    setReviewMessage(null);
    try {
      const result = await window.AL_API.reviewGraphProposedElement(tenantId, selectedElement.element_key, action, {
        reason,
        reviewer: "Saskue",
      });
      if (result?.element) setSelectedElement(result.element);
      setReviewReason("");
      setReviewMessage({
        kind: "ok",
        text: `${action} recorded · canonical/formal graph unchanged`,
      });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setReviewMessage({ kind: "error", text: err.message || String(err) });
    } finally {
      setReviewBusy(false);
    }
  }
  return (
    <div className="section">
      <div className="section-head">
        <span>Proposed graph space</span>
        <span className="ct">{loading ? "loading" : `${elements.length} draft`}</span>
      </div>
      <div className="section-body">
        <div className="chip-row" style={{ marginBottom: 10 }}>
          <Chip active={kindFilter === "all"} onClick={() => selectKind("all")} count={elements.length}>all</Chip>
          <Chip active={kindFilter === "node"} onClick={() => selectKind("node")} count={counts.node || 0}>nodes</Chip>
          <Chip active={kindFilter === "edge"} onClick={() => selectKind("edge")} count={counts.edge || 0}>edges</Chip>
          <Chip active={kindFilter === "finding"} onClick={() => selectKind("finding")} count={counts.finding || 0}>findings</Chip>
        </div>
        <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: 8, marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", marginBottom: 8 }}>
            <span className="eyebrow">Batch review</span>
            <span className="ct">{selectedKeys.length} selected</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <button className="btn xs" disabled={!filteredElements.length || reviewBusy} onClick={selectVisible}>Select visible</button>
            <button className="btn xs" disabled={!selectedKeys.length || reviewBusy} onClick={clearSelection}>Clear</button>
            <button className="btn xs approve" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("approve")}>Approve selected</button>
            <button className="btn xs changes" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("needs-evidence")}>Needs evidence</button>
            <button className="btn xs reject" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("reject")}>Reject</button>
            <button className="btn xs ghost" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("comment")}>Comment</button>
          </div>
          <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
            Scope: selected proposed graph elements only · review decision only · formal graph write disabled.
            {selectedInFilter.length > 0 ? ` Current filter selected: ${selectedInFilter.length}.` : ""}
          </div>
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
            <button key={item.element_key} type="button" onClick={() => { setSelectedElement(item); setReviewMessage(null); }}
                    style={{ border: selectedElement?.element_key === item.element_key ? "1px solid var(--accent)" : "1px solid var(--line)", padding: 10, marginBottom: 10, background: "var(--bg-2)", width: "100%", textAlign: "left", cursor: "pointer" }}>
              <div className="eyebrow accent">deep graph finding · draft</div>
              <div style={{ color: "var(--text)", fontWeight: 600, marginTop: 4 }}>{item.name}</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                {profile.path_label || compactText(item.payload?.conclusion, 140)}
              </div>
              <dl className="kv" style={{ marginTop: 8 }}>
                <dt>Confidence</dt><dd>{Math.round((item.confidence || 0) * 100)}%</dd>
                <dt>Evidence</dt><dd>{(item.evidence_refs || []).join(", ") || item.source_url || "—"}</dd>
              </dl>
            </button>
          );
        })}
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>
          Showing {kindFilter === "all" ? "all proposed graph elements" : `${kindFilter} proposals`} · click an item to review.
        </div>
        <div style={{ maxHeight: 220, overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {filteredElements.map(item => (
            <div key={item.element_key} role="button" tabIndex={0}
                 onClick={() => { setSelectedElement(item); setReviewMessage(null); }}
                 onKeyDown={e => { if (e.key === "Enter" || e.key === " ") setSelectedElement(item); }}
                 style={{ border: selectedElement?.element_key === item.element_key ? "1px solid var(--accent)" : "1px solid var(--line-soft)", borderLeft: selectedElement?.element_key === item.element_key ? "3px solid var(--accent)" : "1px solid var(--line-soft)", padding: 8, background: selectedElement?.element_key === item.element_key ? "var(--accent-bg)" : "transparent", textAlign: "left", cursor: "pointer" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: "var(--text)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={selectedSet.has(item.element_key)}
                    onClick={e => e.stopPropagation()}
                    onChange={e => toggleSelection(item.element_key, e.target.checked)}
                  />
                  {item.name}
                </span>
                <span className="ct">{item.element_type}</span>
              </div>
              <div style={{ fontFamily: "var(--font-mono)", color: "var(--muted)", fontSize: 10 }}>
                {item.status} · {item.source_url || "source unknown"}
              </div>
            </div>
          ))}
          {filteredElements.length === 0 && (
            <div style={{ fontFamily: "var(--font-mono)", color: "var(--muted)", fontSize: 10, border: "1px solid var(--line-soft)", padding: 8 }}>
              No {kindFilter} proposed graph elements.
            </div>
          )}
        </div>
        {selectedElement && (
          <ProposedGraphDetail
            item={selectedElement}
            reason={reviewReason}
            setReason={setReviewReason}
            busy={reviewBusy}
            message={reviewMessage}
            onReview={reviewElement}
          />
        )}
        {source === "error" && (
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--rejected)" }}>
            Proposed graph API unavailable.
          </div>
        )}
      </div>
    </div>
  );
}

function ProposedGraphDetail({ item, reason, setReason, busy, message, onReview }) {
  const payload = item.payload || {};
  const profile = payload.deep_graph_profile || {};
  const reviewEvents = payload.review_events || [];
  const boundary = payload.review_boundary || payload.write_boundary || payload.governance || {};
  const path = profile.path || payload.path || payload.evidence_path || [];
  const pathLabel = profile.path_label || payload.path_label || "";
  const conclusion = payload.conclusion || payload.summary || payload.description || "";
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <div className="eyebrow accent">Review selected {item.element_type}</div>
      <div style={{ color: "var(--text)", fontWeight: 600, marginTop: 4 }}>{item.name}</div>
      <dl className="kv" style={{ marginTop: 10 }}>
        <dt>Key</dt><dd>{item.element_key}</dd>
        <dt>Status</dt><dd>{item.status}</dd>
        <dt>Run</dt><dd>{item.run_key || "—"}</dd>
        <dt>Confidence</dt><dd>{Math.round((item.confidence || 0) * 100)}%</dd>
        <dt>Source</dt><dd>{item.source_url || "—"}</dd>
        <dt>Evidence</dt><dd>{(item.evidence_refs || []).join(", ") || "—"}</dd>
        <dt>Boundary</dt><dd>{boundary.writes_canonical === false || boundary.canonical_write === false ? "canonical disabled" : "canonical disabled"} · {boundary.writes_formal_graph === false || boundary.formal_graph_write === false ? "formal graph disabled" : "formal graph disabled"}</dd>
      </dl>
      {(pathLabel || conclusion) && (
        <div style={{ marginTop: 10, padding: 10, border: "1px solid var(--line-soft)", background: "var(--bg-2)" }}>
          {pathLabel && <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", marginBottom: 6 }}>{pathLabel}</div>}
          {conclusion && <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5 }}>{conclusion}</div>}
        </div>
      )}
      {Array.isArray(path) && path.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Evidence path</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {path.map((step, index) => (
              <div key={index} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", borderBottom: "1px solid var(--line-soft)", paddingBottom: 4 }}>
                {index + 1}. {typeof step === "string" ? step : (step.label || step.name || step.key || JSON.stringify(step))}
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <div className="eyebrow" style={{ marginBottom: 6 }}>Review note</div>
        <textarea className="input" value={reason} onChange={e => setReason(e.target.value)}
                  placeholder="Optional for approve; required for reject / needs evidence"
                  style={{ minHeight: 64, resize: "vertical" }} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 8 }}>
          <button className="btn approve" disabled={busy || item.status === "approved"} onClick={() => onReview("approve")}>Approve</button>
          <button className="btn changes" disabled={busy} onClick={() => onReview("needs-evidence")}>Needs evidence</button>
          <button className="btn reject" disabled={busy} onClick={() => onReview("reject")}>Reject</button>
          <button className="btn ghost" disabled={busy} onClick={() => onReview("comment")}>Comment</button>
        </div>
        {message && (
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: message.kind === "error" ? "var(--rejected)" : "var(--approved)" }}>
            {message.text}
          </div>
        )}
      </div>
      {reviewEvents.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Review history</div>
          {reviewEvents.slice().reverse().map((event, index) => (
            <div key={index} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", borderTop: "1px solid var(--line-soft)", paddingTop: 5, marginTop: 5 }}>
              {event.decision} · {event.reviewer || "reviewer"} · {event.after_status || item.status}
              {event.reason ? ` · ${event.reason}` : ""}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BigGraph({ data, selected, onSelect, hoverId, setHoverId }) {
  const map = Object.fromEntries(data.nodes.map(n => [n.id, n]));
  const palette = ["var(--accent)", "var(--changes)", "var(--proposed)", "var(--approved)", "var(--dim)"];
  const graphTypes = Array.from(new Set(data.nodes.map(n => n.type).filter(Boolean)));
  const typeColors = Object.fromEntries(graphTypes.map((t, i) => [t, palette[i % palette.length]]));
  const sel = selected ? selected.id : null;
  const focusActive = !!sel;
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
        const dimmed = focusActive && !involved;
        return (
          <g key={i} opacity={dimmed ? 0.16 : 1}>
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
        const dimmed = focusActive && !isSel && !isNeighbor;
        const stroke = n.flag ? "var(--rejected)" : (isSel ? "var(--accent)" : isNeighbor ? "var(--accent-dim)" : (n.muted ? "var(--faint)" : typeColors[n.type] || "var(--text-dim)"));
        return (
          <g key={i} onClick={() => onSelect(n)}
                 onMouseEnter={() => setHoverId(n.id)}
                 onMouseLeave={() => setHoverId(null)}
                 opacity={dimmed ? 0.24 : 1}
                 style={{ cursor: "pointer", transition: "opacity 120ms ease" }}>
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
                  fill={isSel ? "var(--accent)" : dimmed ? "var(--dim)" : "var(--text-dim)"}
                  style={{ pointerEvents: "none" }}>
              {n.id}
            </text>
            <text x={n.x} y={n.y + n.r + 26}
                  textAnchor="middle"
                  fontSize="9.5" fontFamily="var(--font-mono)"
                  fill={dimmed ? "var(--faint)" : "var(--dim)"}
                  style={{ pointerEvents: "none", letterSpacing: "0.04em" }}>
              {n.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

Object.assign(window, { GraphExplorer, BigGraph, ProposedGraphPanel, ProposedGraphDetail });
