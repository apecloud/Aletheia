/* Aletheia — Graph Explorer */

const { useState: useStateGX, useRef: useRefGX, useEffect: useEffectGX, useMemo: useMemoGX } = React;

function isZhGX(language) {
  return typeof isZhUI === "function" ? isZhUI(language) : String(language || "").startsWith("zh");
}

function tGX(language, en, zh) {
  return typeof tUI === "function" ? tUI(language, en, zh) : (isZhGX(language) ? zh : en);
}

function labelGX(value, language) {
  return typeof displayLabelUI === "function" ? displayLabelUI(value, language) : value;
}

function countryLabelGX(value, language) {
  return typeof countryNameUI === "function" ? countryNameUI(value, language) : value;
}

function statusLabelGraphGX(status, language) {
  if (!isZhGX(language)) return status || "—";
  const map = {
    approved: "已批准",
    blocked: "已阻塞",
    candidate: "候选",
    changes: "需修改",
    done: "已完成",
    draft: "草稿",
    failed: "失败",
    needs_evidence: "需补证据",
    proposed: "待审核",
    rejected: "已拒绝",
    running: "运行中",
  };
  return map[String(status || "").toLowerCase()] || status || "—";
}

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
function normalizeGraph(raw, fallback, language) {
  if (!raw || !raw.nodes) return fallback;
  const nodes = raw.nodes.map(n => ({
    id: n.id, type: n.type,
    label: n.type === "Country"
      ? countryLabelGX(n.label || n.id, language)
      : labelGX(n.label || (n.key_properties && (n.key_properties.name || n.key_properties.title)) || n.id, language),
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

function GraphExplorer({ data, tenant, language }) {
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
  const [centerSearch, setCenterSearch] = useStateGX(initialParams.get("id") || "");

  const typesQ = useApiData("instanceTypes", [tenantId, { includeDraft: true }], { fallback: [] });
  const centerTypes = Array.isArray(typesQ.data) ? typesQ.data : [];
  const centerTypeNames = centerTypes.map(t => t.type);
  const activeType = centerTypes.find(t => t.type === centerType) || null;
  const typesLoaded = typesQ.source !== "loading";
  const candidatesQ = useApiData(
    "instanceSearch",
    [tenantId, centerType, centerSearch, 50, { includeDraft: true }],
    { enabled: typesLoaded && !!activeType, fallback: [] }
  );
  const candidates = Array.isArray(candidatesQ.data) ? candidatesQ.data : [];
  const proposedQ = useApiData("graphProposedElements", [tenantId, { limit: 100 }], {
    fallback: { runs: [], elements: [] },
  });
  const proposed = proposedQ.data || { runs: [], elements: [] };
  const proposedTotalCount = proposed?.total_count ?? (proposed.elements || []).length;
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
      if (centerSearch) setCenterSearch("");
      return;
    }
    if (!centerType || !centerTypes.some(t => t.type === centerType)) {
      setCenterType(centerTypes[0].type);
      setCenterNodeId("");
      setCenterSearch("");
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
  }, [tenantId, centerType, centerSearch, candidatesQ.source, JSON.stringify(candidates.map(c => c.id))]);

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
  const graph = useMemoGX(() => normalizeGraph(graphQ.data, { nodes: [], edges: [] }, language), [graphQ.data, language]);

  const [selected, setSelected] = useStateGX(null);
  const [nodePositions, setNodePositions] = useStateGX({});
  const [hideUnrelated, setHideUnrelated] = useStateGX(false);
  const [pendingCenterFocus, setPendingCenterFocus] = useStateGX("");
  const [focusMessage, setFocusMessage] = useStateGX("");
  useEffectGX(() => {
    setSelected(null);
    setHoverId(null);
    setPendingCenterFocus("");
    setFocusMessage("");
    setNodePositions({});
    setHideUnrelated(false);
  }, [tenantId]);
  const graphWithPositions = useMemoGX(() => {
    const nodes = graph.nodes.map(node => {
      const pos = nodePositions[node.id];
      return pos ? { ...node, x: pos.x, y: pos.y } : node;
    });
    return { ...graph, nodes };
  }, [graph, nodePositions]);
  useEffectGX(() => {
    if (!graphWithPositions.nodes.length) {
      if (selected) setSelected(null);
      return;
    }
    setSelected(prev => graphWithPositions.nodes.find(n => prev && n.id === prev.id) || null);
  }, [graphWithPositions]);
  useEffectGX(() => {
    if (!selected && hideUnrelated) setHideUnrelated(false);
  }, [selected, hideUnrelated]);

  const map = Object.fromEntries(graphWithPositions.nodes.map(n => [n.id, n]));
  const palette = ["var(--accent)", "var(--changes)", "var(--proposed)", "var(--approved)", "var(--dim)"];
  const graphTypes = Array.from(new Set(graphWithPositions.nodes.map(n => n.type).filter(Boolean)));
  const typeColors = Object.fromEntries(graphTypes.map((t, i) => [t, palette[i % palette.length]]));
  const edgeCounts = graphWithPositions.edges.reduce((acc, e) => {
    const key = e.kind || "edge";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const centerLabel = centerType && centerNodeId ? `${centerType}:${centerNodeId}` : "No tenant center";
  const centerKey = centerType && centerNodeId ? `${centerType}:${centerNodeId}` : "";
  const focusCenterNode = () => {
    if (!centerKey) return false;
    const match = graphWithPositions.nodes.find(node => node.id === centerKey);
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
    const match = graphWithPositions.nodes.find(node => node.id === pendingCenterFocus);
    if (match) {
      setSelected(match);
      setFocusMessage(`Focused ${pendingCenterFocus}`);
      setPendingCenterFocus("");
    } else if (graphQ.source !== "loading") {
      setFocusMessage(`${pendingCenterFocus} is not in the loaded full graph.`);
      setPendingCenterFocus("");
    }
  }, [pendingCenterFocus, graphQ.loading, graphQ.source, graphWithPositions.nodes.map(n => n.id).join("|")]);

  const updateNodePosition = (nodeId, point) => {
    const x = Math.max(18, Math.min(982, point.x));
    const y = Math.max(18, Math.min(582, point.y));
    setNodePositions(prev => ({ ...prev, [nodeId]: { x, y } }));
    setSelected(prev => {
      if (!prev || prev.id !== nodeId) return prev;
      return { ...prev, x, y };
    });
  };

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="eyebrow accent">{tGX(language, "Graph Explorer", "图谱探索")}</div>
        <div className="spacer" />
        {isMockG  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tGX(language, "Mock fallback", "模拟回退")}</span>}
        {isStaleG && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tGX(language, "Stale · last fetch failed", "数据陈旧 · 最近拉取失败")}</span>}
        {graphQ.loading && graphQ.data && <span className="pill"><span className="dot" />{tGX(language, "Refreshing…", "刷新中…")}</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ {tGX(language, "Reload", "重新加载")}</button>
        <button className="tool">⤓ {tGX(language, "Snapshot", "快照")}</button>
        <button className="tool primary">↗ {tGX(language, "Open reasoning", "打开推理")}</button>
      </div>

      <div className="gx">
        {/* LEFT — scope */}
        <div className="col">
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow accent">{tGX(language, "Graph Catalog", "图谱目录")}</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)", marginTop: 4 }}>
              {tGX(language, "tenant", "租户")} <span style={{ color: "var(--accent)" }}>{tenant ? tenant.id : "default"}</span> · {tGX(language, "graph spaces", "图空间")}
            </div>
            <div className="side-tabs" style={{ marginTop: 10 }}>
              <button className={"side-tab" + (leftTab === "approved" ? " active" : "")} onClick={() => selectGraphTab("approved")}>
                {tGX(language, "Approved graph", "已批准图谱")} <span className="ct">{graphWithPositions.nodes.length}</span>
              </button>
              <button className={"side-tab" + (leftTab === "proposed" ? " active" : "")} onClick={() => selectGraphTab("proposed")}>
                {tGX(language, "Proposed graph", "候选图谱")} <span className="ct">{proposedTotalCount}</span>
              </button>
              <button className={"side-tab" + (leftTab === "saved" ? " active" : "")} onClick={() => selectGraphTab("saved")}>
                {tGX(language, "Saved views", "保存视图")} <span className="ct">0</span>
              </button>
            </div>
            {showAgentRunsMoved && (
              <div style={{ marginTop: 10, border: "1px solid var(--accent-line)", background: "var(--accent-bg)", padding: 10 }}>
                <div className="eyebrow accent">{tGX(language, "Automatic runs moved", "自动运行已迁移")}</div>
                <div style={{ marginTop: 5, fontSize: 12, color: "var(--text-dim)", lineHeight: 1.45 }}>
                  {tGX(language, "Crawl, enrichment, and reasoning runs are managed from Workspace.", "爬取、信息增益和推理运行已统一由 Workspace 管理。")}
                </div>
                <a className="btn ghost" style={{ marginTop: 8 }} href={`/?screen=workbench&tenant=${encodeURIComponent(tenantId)}&workspace_tab=agents`}>
                  {tGX(language, "Open Workspace agents", "打开 Workspace Agent")}
                </a>
              </div>
            )}
          </div>

          {leftTab === "approved" && <>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>{tGX(language, "Center", "中心")}</div>
              <div style={{ display: "flex", gap: 6 }}>
                <select className="select" style={{ width: 110 }} value={centerType} onChange={e => { setCenterType(e.target.value); setCenterNodeId(""); setCenterSearch(""); setSelected(null); setFocusMessage(""); }}>
                  {centerTypes.length === 0 && <option value="">{tGX(language, "No tenant types", "无租户类型")}</option>}
                  {centerTypes.map(t => <option key={t.type} value={t.type}>{t.label || t.type}{t.approved ? "" : " · draft"}</option>)}
                </select>
                <select className="select" value={centerNodeId} onChange={e => { setCenterNodeId(e.target.value); setCenterSearch(e.target.value); }} disabled={!centerType || candidates.length === 0}>
                  {candidates.length === 0 && <option value="">{tGX(language, "No center nodes", "无中心节点")}</option>}
                  {candidates.map(c => {
                    const id = String(c.id || "").split(":").slice(1).join(":");
                    return <option key={c.id} value={id}>{centerType === "Country" ? countryLabelGX(c.label || c.id, language) : labelGX(c.label || c.id, language)}</option>;
                  })}
                </select>
              </div>
              <input
                className="input"
                value={centerSearch}
                onChange={e => { setCenterSearch(e.target.value); setCenterNodeId(e.target.value.trim()); setFocusMessage(""); }}
                placeholder={centerType === "Country" ? tGX(language, "Search or type country, e.g. China or CHN", "搜索或输入国家，例如 China 或 CHN") : tGX(language, "Search or type center id", "搜索或输入中心 ID")}
                disabled={!centerType}
                style={{ marginTop: 6 }}
              />
              <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                {activeType ? `${activeType.table} · ${activeType.ontology_artifact} · ${activeType.artifact_status || "unknown"} · ${candidates.length} ${tGX(language, "candidates", "候选")}` : tGX(language, "No tenant graph center types for this tenant.", "该租户没有可用的图谱中心类型。")}
              </div>
              <button className="btn ghost" style={{ marginTop: 8, width: "100%" }} disabled={!centerKey || !graphWithPositions.nodes.some(node => node.id === centerKey)} onClick={focusCenterNode}>
                {tGX(language, "Focus center in full graph", "在全图中聚焦中心")}
              </button>
              <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
                {tGX(language, "Default view shows all approved tenant graph nodes. Selecting a node only changes focus contrast.", "默认视图显示该租户所有已批准图节点；选中节点只改变聚焦对比度。")}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <div style={{ flex: 1 }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tGX(language, "Depth", "深度")}</div>
                <input className="input" value={depth} onChange={e => setDepth(+e.target.value)} type="number" min={1} max={3} />
              </div>
              <div style={{ flex: 1 }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tGX(language, "Limit", "上限")}</div>
                <input className="input" value={limit} onChange={e => setLimit(+e.target.value)} type="number" />
              </div>
            </div>
            <button className="btn primary" disabled={!centerKey || graphQ.loading} onClick={loadAndFocusCenter}>{tGX(language, "Load full graph", "加载全图")}</button>
            {focusMessage && (
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: focusMessage.includes("not in") ? "var(--changes)" : "var(--muted)", lineHeight: 1.4 }}>
                {focusMessage}
              </div>
            )}
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>{tGX(language, "Current scope", "当前范围")}</div>
            <dl className="kv">
              <dt>{tGX(language, "Center", "中心")}</dt><dd>{labelGX(centerLabel, language)}</dd>
              <dt>{tGX(language, "Nodes", "节点")}</dt><dd>{graphWithPositions.nodes.length}</dd>
              <dt>{tGX(language, "Edges", "边")}</dt><dd>{graphWithPositions.edges.length}</dd>
              <dt>{tGX(language, "View", "视图")}</dt><dd>{tGX(language, "all approved nodes", "全部已批准节点")}</dd>
              <dt>{tGX(language, "Limit", "上限")}</dt><dd>{limit}</dd>
            </dl>
            <button
              className="btn ghost"
              style={{ marginTop: 10, width: "100%" }}
              disabled={!selected}
              onClick={() => setHideUnrelated(v => !v)}>
              {hideUnrelated ? tGX(language, "Show all graph nodes", "显示所有图节点") : tGX(language, "Hide unrelated nodes", "隐藏无关节点")}
            </button>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>{tGX(language, "Edge types", "边类型")}</div>
            <div className="chip-row">
              {Object.keys(edgeCounts).length === 0 && <Chip count={0}>{tGX(language, "none", "无")}</Chip>}
              {Object.entries(edgeCounts).map(([kind, count]) => <Chip key={kind} active count={count}>{kind}</Chip>)}
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <div style={{ padding: "var(--pad-3) var(--pad-4)" }}>
              <div className="eyebrow" style={{ marginBottom: 8 }}>{tGX(language, "Expand history", "展开历史")}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                <div>{tGX(language, "current", "当前")} — {tGX(language, "all approved tenant graph", "全部已批准租户图谱")} · {graphWithPositions.nodes.length} {tGX(language, "nodes", "节点")}</div>
                <div>{tGX(language, "tenant", "租户")} — {tenantId} · {tenant?.graph || tGX(language, "graph db unknown", "未知图数据库")}</div>
                <div>{tGX(language, "focus", "聚焦")} — {selected ? selected.id : tGX(language, "none; full graph contrast", "无；全图对比")}</div>
                <div>{tGX(language, "visibility", "可见性")} — {hideUnrelated && selected ? tGX(language, "selected context only", "仅选中上下文") : tGX(language, "all graph nodes", "全部图节点")}</div>
              </div>
            </div>
          </div>
          </>}

          {leftTab === "proposed" && (
            <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <ProposedGraphPanel tenantId={tenantId} proposed={proposed} loading={proposedQ.loading} source={proposedQ.source} focusElementKey={focusElementKey} compact language={language} />
            </div>
          )}

          {leftTab === "saved" && (
            <div style={{ flex: 1, padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
              {tGX(language, "No saved graph views for tenant", "该租户暂无保存视图")} {tenantId}.
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
            ) : graphWithPositions.nodes.length === 0 ? (
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                Empty graph for this scope.
              </div>
            ) : (
            <BigGraph
              data={graphWithPositions}
              selected={selected}
              onSelect={setSelected}
              hoverId={hoverId}
              setHoverId={setHoverId}
              hideUnrelated={hideUnrelated}
              onNodePositionChange={updateNodePosition}
              language={language}
            />
            )}

            <div className="graph-overlay-tl">
              <div className="row">
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "NODES", "节点")}</span><span className="v">{graphWithPositions.nodes.length}</span></div>
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "EDGES", "边")}</span><span className="v">{graphWithPositions.edges.length}</span></div>
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "FOCUS", "聚焦")}</span><span className="v">{selected ? tGX(language, "ON", "开") : tGX(language, "ALL", "全部")}</span></div>
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "VISIBLE", "可见")}</span><span className="v">{hideUnrelated && selected ? tGX(language, "LOCAL", "局部") : tGX(language, "ALL", "全部")}</span></div>
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
              <button className="icon-btn" title={tGX(language, "Zoom in", "放大")}>+</button>
              <button className="icon-btn" title={tGX(language, "Zoom out", "缩小")}>−</button>
              <button className="icon-btn" title={tGX(language, "Fit view", "适配视图")}>⌖</button>
              <button className="icon-btn" title={tGX(language, "Clear focus", "清除聚焦")} disabled={!selected} onClick={() => { setSelected(null); setHideUnrelated(false); }}>◎</button>
              <button
                className="icon-btn"
                title={hideUnrelated ? tGX(language, "Show all nodes", "显示所有节点") : tGX(language, "Hide unrelated nodes", "隐藏无关节点")}
                disabled={!selected}
                onClick={() => setHideUnrelated(v => !v)}>
                {hideUnrelated ? "◉" : "◌"}
              </button>
              <button className="icon-btn" title={tGX(language, "Expand", "展开")}>⊕</button>
              <button className="icon-btn" title={tGX(language, "Collapse", "收起")}>⊖</button>
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
              {tGX(language, "All approved tenant graph nodes are visible. Select a node to focus its local context; selected nodes can be dragged to rearrange the canvas.", "当前显示该租户全部已批准图节点。选择节点可聚焦本地上下文；选中节点可拖拽重新布局。")}
            </div>
          ) : (
          <>
          <div className="section">
            <div className="section-head">
              <span>{tGX(language, "Inspector", "检查器")}</span>
              <span className="ct">{selected.type}</span>
            </div>
            <div className="section-body">
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div className="eyebrow accent">{selected.type}</div>
                <div style={{ fontSize: 16, color: "var(--text)", fontWeight: 500 }}>{selected.id}</div>
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{labelGX(selected.label, language)}</div>
              </div>
              <div style={{ marginTop: 14 }}>
                <dl className="kv">
                  <dt>{tGX(language, "Status", "状态")}</dt><dd>{selected._raw?.status || "approved"}</dd>
                  <dt>{tGX(language, "Source row", "来源行")}</dt><dd>{selected._raw?.source_table || "source"}#{selected._raw?.source_pk || selected.id.split(":").slice(1).join(":")}</dd>
                  <dt>{tGX(language, "Edges in", "入边")}</dt><dd>{graphWithPositions.edges.filter(e => e.t === selected.id).length}</dd>
                  <dt>{tGX(language, "Edges out", "出边")}</dt><dd>{graphWithPositions.edges.filter(e => e.s === selected.id).length}</dd>
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
            <div className="section-head"><span>{tGX(language, "Connected edges", "相连边")}</span><span className="ct">{graphWithPositions.edges.filter(e => e.s === selected.id || e.t === selected.id).length}</span></div>
            <div className="section-body" style={{ padding: 0 }}>
              {graphWithPositions.edges.filter(e => e.s === selected.id || e.t === selected.id).map((e, i) => {
                const other = e.s === selected.id ? e.t : e.s;
                const dir = e.s === selected.id ? "→" : "←";
                return (
                  <div key={i} style={{ padding: "8px 14px", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
                       onClick={() => setSelected(map[other])}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{e.kind}</span>
                    <span style={{ color: "var(--dim)" }}>{dir}</span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>{labelGX(other, language)}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>{tGX(language, "Scoped reasoning", "范围推理")}</span><span className="ct">draft-only</span></div>
            <div className="section-body">
              <div className="eyebrow" style={{ marginBottom: 4 }}>{tGX(language, "Question", "问题")}</div>
              <select className="select" style={{ marginBottom: 8 }}>
                <option>{tGX(language, "Explain this node's role in the graph", "解释该节点在图谱中的作用")}</option>
                <option>{tGX(language, "Find workload / concentration risk", "发现工作负载 / 集中风险")}</option>
                <option>{tGX(language, "Explain why this edge exists", "解释这条边为什么存在")}</option>
                <option>{tGX(language, "Find unusual neighbors in this scope", "发现该范围内异常邻居")}</option>
              </select>
              <button className="btn primary" style={{ width: "100%" }}>{tGX(language, "Open scoped reasoning", "打开范围推理")}</button>
            </div>
          </div>
          </>
          )}
        </div>
      </div>
    </div>
  );
}

function ProposedGraphPanel({ tenantId, proposed, loading, source, focusElementKey, language }) {
  const [selectedElement, setSelectedElement] = useStateGX(null);
  const [kindFilter, setKindFilter] = useStateGX("all");
  const [selectedKeys, setSelectedKeys] = useStateGX([]);
  const [reviewReason, setReviewReason] = useStateGX("");
  const [reviewBusy, setReviewBusy] = useStateGX(false);
  const [reviewMessage, setReviewMessage] = useStateGX(null);
  const runs = proposed?.runs || [];
  const elements = proposed?.elements || [];
  const totalCount = proposed?.total_count ?? elements.length;
  const counts = proposed?.element_type_counts || elements.reduce((acc, item) => {
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
    if (!latest) setSelectedElement(filteredElements[0] || null);
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
      setReviewMessage({ kind: "error", text: tGX(language, "Select at least one proposed graph element.", "请至少选择一个候选图元素。") });
      return;
    }
    if ((action === "reject" || action === "needs-evidence") && !reason) {
      setReviewMessage({ kind: "error", text: tGX(language, "Review reason is required for batch reject / needs evidence.", "批量拒绝或要求补证据时必须填写审核原因。") });
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
          ? tGX(language, `${ok} recorded, ${failed} failed · ${failedItems.map(item => item.element_key || item.error).slice(0, 2).join(", ")}`, `已记录 ${ok} 条，失败 ${failed} 条 · ${failedItems.map(item => item.element_key || item.error).slice(0, 2).join(", ")}`)
          : tGX(language, `${ok} graph proposal review decisions recorded · formal graph unchanged`, `已记录 ${ok} 条图候选审核决定 · formal graph 未改变`),
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
      setReviewMessage({ kind: "error", text: tGX(language, "Review reason is required for reject / needs evidence.", "拒绝或要求补证据时必须填写审核原因。") });
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
        text: tGX(language, `${action} recorded · canonical/formal graph unchanged`, `已记录 ${action} · canonical/formal graph 未改变`),
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
        <span>{tGX(language, "Proposed graph space", "候选图空间")}</span>
        <span className="ct">{loading ? tGX(language, "loading", "加载中") : `${totalCount} ${tGX(language, "pending", "待处理")}`}</span>
      </div>
      <div className="section-body">
        <div className="chip-row" style={{ marginBottom: 10 }}>
          <Chip active={kindFilter === "all"} onClick={() => selectKind("all")} count={totalCount}>{tGX(language, "all", "全部")}</Chip>
          <Chip active={kindFilter === "node"} onClick={() => selectKind("node")} count={counts.node || 0}>{tGX(language, "nodes", "节点")}</Chip>
          <Chip active={kindFilter === "edge"} onClick={() => selectKind("edge")} count={counts.edge || 0}>{tGX(language, "edges", "边")}</Chip>
          <Chip active={kindFilter === "finding"} onClick={() => selectKind("finding")} count={counts.finding || 0}>{tGX(language, "findings", "发现")}</Chip>
        </div>
        <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: 8, marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", marginBottom: 8 }}>
            <span className="eyebrow">{tGX(language, "Batch review", "批量审核")}</span>
            <span className="ct">{selectedKeys.length} {tGX(language, "selected", "已选择")}</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <button className="btn xs" disabled={!filteredElements.length || reviewBusy} onClick={selectVisible}>{tGX(language, "Select visible", "选择当前可见")}</button>
            <button className="btn xs" disabled={!selectedKeys.length || reviewBusy} onClick={clearSelection}>{tGX(language, "Clear", "清除")}</button>
            <button className="btn xs approve" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("approve")}>{tGX(language, "Approve selected", "批准所选")}</button>
            <button className="btn xs changes" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("needs-evidence")}>{tGX(language, "Needs evidence", "需要补证据")}</button>
            <button className="btn xs reject" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("reject")}>{tGX(language, "Reject", "拒绝")}</button>
            <button className="btn xs ghost" disabled={!selectedKeys.length || reviewBusy} onClick={() => reviewSelected("comment")}>{tGX(language, "Comment", "评论")}</button>
          </div>
          <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
            {tGX(language, "Scope: selected proposed graph elements only · review decision only · formal graph write disabled.", "范围：仅所选候选图元素 · 只记录审核决定 · formal graph 写入禁用。")}
            {selectedInFilter.length > 0 ? tGX(language, ` Current filter selected: ${selectedInFilter.length}.`, ` 当前过滤结果已选择：${selectedInFilter.length}。`) : ""}
          </div>
        </div>
        {latestRun ? (
          <dl className="kv" style={{ marginBottom: 12 }}>
            <dt>{tGX(language, "Run", "运行")}</dt><dd>{latestRun.run_key}</dd>
            <dt>{tGX(language, "Status", "状态")}</dt><dd>{latestRun.status} · canonical writes disabled</dd>
            <dt>{tGX(language, "Skipped", "跳过")}</dt><dd>{(latestRun.skipped_sources || []).length}</dd>
          </dl>
        ) : (
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
            {tGX(language, "No proposed graph elements for this tenant.", "该租户暂无候选图元素。")}
          </div>
        )}
        {findings.map(item => {
          const profile = item.payload?.deep_graph_profile || {};
          return (
            <button key={item.element_key} type="button" onClick={() => { setSelectedElement(item); setReviewMessage(null); }}
                    style={{ border: selectedElement?.element_key === item.element_key ? "1px solid var(--accent)" : "1px solid var(--line)", padding: 10, marginBottom: 10, background: "var(--bg-2)", width: "100%", textAlign: "left", cursor: "pointer" }}>
              <div className="eyebrow accent">{tGX(language, "deep graph finding · draft", "深度图推理发现 · 草稿")}</div>
              <div style={{ color: "var(--text)", fontWeight: 600, marginTop: 4 }}>{labelGX(item.name, language)}</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                {labelGX(profile.path_label || compactText(item.payload?.conclusion, 140), language)}
              </div>
              <dl className="kv" style={{ marginTop: 8 }}>
                <dt>{tGX(language, "Confidence", "置信度")}</dt><dd>{Math.round((item.confidence || 0) * 100)}%</dd>
                <dt>{tGX(language, "Evidence", "证据")}</dt><dd>{(item.evidence_refs || []).join(", ") || item.source_url || "—"}</dd>
              </dl>
            </button>
          );
        })}
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>
          {tGX(language, "Showing", "显示")} {kindFilter === "all" ? tGX(language, "all proposed graph elements", "全部候选图元素") : `${kindFilter} ${tGX(language, "proposals", "候选")}`} · {tGX(language, "click an item to review.", "点击条目进行审核。")}
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
                  {labelGX(item.name, language)}
                </span>
                <span className="ct">{item.element_type}</span>
              </div>
              <div style={{ fontFamily: "var(--font-mono)", color: "var(--muted)", fontSize: 10 }}>
                {statusLabelGraphGX(item.status, language)} · {item.source_url || tGX(language, "source unknown", "来源未知")}
              </div>
            </div>
          ))}
          {filteredElements.length === 0 && (
            <div style={{ fontFamily: "var(--font-mono)", color: "var(--muted)", fontSize: 10, border: "1px solid var(--line-soft)", padding: 8 }}>
              {tGX(language, "No", "没有")} {kindFilter} {tGX(language, "proposed graph elements.", "候选图元素。")}
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
            language={language}
          />
        )}
        {source === "error" && (
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--rejected)" }}>
            {tGX(language, "Proposed graph API unavailable.", "候选图 API 不可用。")}
          </div>
        )}
      </div>
    </div>
  );
}

function ProposedGraphDetail({ item, reason, setReason, busy, message, onReview, language }) {
  const payload = item.payload || {};
  const profile = payload.deep_graph_profile || {};
  const reviewEvents = payload.review_events || [];
  const boundary = payload.review_boundary || payload.write_boundary || payload.governance || {};
  const path = profile.path || payload.path || payload.evidence_path || [];
  const pathLabel = profile.path_label || payload.path_label || "";
  const conclusion = payload.conclusion || payload.summary || payload.description || "";
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <div className="eyebrow accent">{tGX(language, "Review selected", "审核选中的")} {tGX(language, item.element_type, item.element_type === "node" ? "节点" : item.element_type === "edge" ? "边" : item.element_type === "finding" ? "发现" : item.element_type)}</div>
      <div style={{ color: "var(--text)", fontWeight: 600, marginTop: 4 }}>{labelGX(item.name, language)}</div>
      <dl className="kv" style={{ marginTop: 10 }}>
        <dt>{tGX(language, "Key", "键")}</dt><dd>{item.element_key}</dd>
        <dt>{tGX(language, "Status", "状态")}</dt><dd>{statusLabelGraphGX(item.status, language)}</dd>
        <dt>{tGX(language, "Run", "运行")}</dt><dd>{item.run_key || "—"}</dd>
        <dt>{tGX(language, "Confidence", "置信度")}</dt><dd>{Math.round((item.confidence || 0) * 100)}%</dd>
        <dt>{tGX(language, "Source", "来源")}</dt><dd>{item.source_url || "—"}</dd>
        <dt>{tGX(language, "Evidence", "证据")}</dt><dd>{(item.evidence_refs || []).join(", ") || "—"}</dd>
        <dt>{tGX(language, "Boundary", "边界")}</dt><dd>{boundary.writes_canonical === false || boundary.canonical_write === false ? "canonical disabled" : "canonical disabled"} · {boundary.writes_formal_graph === false || boundary.formal_graph_write === false ? "formal graph disabled" : "formal graph disabled"}</dd>
      </dl>
      {(pathLabel || conclusion) && (
        <div style={{ marginTop: 10, padding: 10, border: "1px solid var(--line-soft)", background: "var(--bg-2)" }}>
          {pathLabel && <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", marginBottom: 6 }}>{labelGX(pathLabel, language)}</div>}
          {conclusion && <div style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5 }}>{labelGX(conclusion, language)}</div>}
        </div>
      )}
      {Array.isArray(path) && path.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>{tGX(language, "Evidence path", "证据路径")}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {path.map((step, index) => (
              <div key={index} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", borderBottom: "1px solid var(--line-soft)", paddingBottom: 4 }}>
                {index + 1}. {labelGX(typeof step === "string" ? step : (step.label || step.name || step.key || JSON.stringify(step)), language)}
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <div className="eyebrow" style={{ marginBottom: 6 }}>{tGX(language, "Review note", "审核说明")}</div>
        <textarea className="input" value={reason} onChange={e => setReason(e.target.value)}
                  placeholder={tGX(language, "Optional for approve; required for reject / needs evidence", "批准时可选；拒绝或要求补证据时必填")}
                  style={{ minHeight: 64, resize: "vertical" }} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 8 }}>
          <button className="btn approve" disabled={busy || item.status === "approved"} onClick={() => onReview("approve")}>{tGX(language, "Approve", "批准")}</button>
          <button className="btn changes" disabled={busy} onClick={() => onReview("needs-evidence")}>{tGX(language, "Needs evidence", "需要补证据")}</button>
          <button className="btn reject" disabled={busy} onClick={() => onReview("reject")}>{tGX(language, "Reject", "拒绝")}</button>
          <button className="btn ghost" disabled={busy} onClick={() => onReview("comment")}>{tGX(language, "Comment", "评论")}</button>
        </div>
        {message && (
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: message.kind === "error" ? "var(--rejected)" : "var(--approved)" }}>
            {message.text}
          </div>
        )}
      </div>
      {reviewEvents.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>{tGX(language, "Review history", "审核历史")}</div>
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

function BigGraph({ data, selected, onSelect, hoverId, setHoverId, hideUnrelated, onNodePositionChange, language }) {
  const svgRef = useRefGX(null);
  const [dragging, setDragging] = useStateGX(null);
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
  const visibleNodeIds = new Set();
  if (focusActive && hideUnrelated) {
    visibleNodeIds.add(sel);
    neighborIds.forEach(id => visibleNodeIds.add(id));
  }

  const eventPoint = (event) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(ctm.inverse());
    return { x: point.x, y: point.y };
  };
  const startDrag = (event, node) => {
    event.preventDefault();
    event.stopPropagation();
    onSelect(node);
    setHoverId(node.id);
    setDragging({ id: node.id, pointerId: event.pointerId });
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const moveDrag = (event) => {
    if (!dragging || dragging.pointerId !== event.pointerId) return;
    const point = eventPoint(event);
    if (point) onNodePositionChange(dragging.id, point);
  };
  const endDrag = (event) => {
    if (dragging && dragging.pointerId === event.pointerId) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      setDragging(null);
    }
  };

  return (
    <svg ref={svgRef} viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid meet"
         style={{ width: "100%", height: "100%", display: "block", touchAction: "none", userSelect: "none" }}>
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
        if (focusActive && hideUnrelated && !involved) return null;
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
                {labelGX(e.kind, language)}
              </text>
            )}
          </g>
        );
      })}

      {/* nodes */}
      {data.nodes.map((n, i) => {
        const isSel = n.id === sel;
        const isNeighbor = neighborIds.has(n.id);
        if (focusActive && hideUnrelated && !visibleNodeIds.has(n.id)) return null;
        const isHover = n.id === hoverId;
        const dimmed = focusActive && !isSel && !isNeighbor;
        const showLabel = isSel;
        const stroke = n.flag ? "var(--rejected)" : (isSel ? "var(--accent)" : isNeighbor ? "var(--accent-dim)" : (n.muted ? "var(--faint)" : typeColors[n.type] || "var(--text-dim)"));
        return (
          <g key={i} onPointerDown={(event) => startDrag(event, n)}
                 onPointerMove={moveDrag}
                 onPointerUp={endDrag}
                 onPointerCancel={endDrag}
                 onMouseEnter={() => setHoverId(n.id)}
                 onMouseLeave={() => setHoverId(null)}
                 opacity={dimmed ? 0.24 : 1}
                 style={{ cursor: dragging?.id === n.id ? "grabbing" : "grab", transition: "opacity 120ms ease" }}>
            {(isSel || isHover) && (
              <circle cx={n.x} cy={n.y} r={n.r + 10} fill="var(--accent-bg)" stroke="var(--accent-line)" strokeWidth="1" />
            )}
            <circle cx={n.x} cy={n.y} r={n.r}
                    fill={isSel ? "var(--accent)" : "var(--bg-2)"}
                    stroke={stroke} strokeWidth={isSel ? 2 : 1.4} />
            {isSel && <circle cx={n.x} cy={n.y} r={n.r - 7} fill="var(--bg-1)" />}
            {showLabel && (
              <>
                <text x={n.x} y={n.y + n.r + 14}
                      textAnchor="middle"
                      fontSize="11" fontFamily="var(--font-mono)"
                      fill="var(--accent)"
                      style={{ pointerEvents: "none" }}>
                  {labelGX(n.id, language)}
                </text>
                <text x={n.x} y={n.y + n.r + 26}
                      textAnchor="middle"
                      fontSize="9.5" fontFamily="var(--font-mono)"
                      fill="var(--dim)"
                      style={{ pointerEvents: "none", letterSpacing: "0.04em" }}>
                  {labelGX(n.label, language)}
                </text>
              </>
            )}
          </g>
        );
      })}
    </svg>
  );
}

Object.assign(window, { GraphExplorer, BigGraph, ProposedGraphPanel, ProposedGraphDetail });
