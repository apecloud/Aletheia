/* Aletheia — Graph Explorer */

const { useState: useStateGX, useRef: useRefGX, useEffect: useEffectGX, useMemo: useMemoGX } = React;

// Deterministic per-type color (hash → hue) so the palette scales past a handful of
// node types instead of cycling through a fixed 4-color list. hashSeedGX is defined
// further down but hoisted, since both are plain top-level function declarations.
function graphTypeColorGX(type) {
  const hue = hashSeedGX(String(type || "unknown")) % 360;
  return `hsl(${hue} 62% 58%)`;
}

const GRAPH_ROLE_COLORS_GX = {
  selected: "var(--graph-blue)",
  selectedBg: "var(--graph-blue-bg)",
  selectedLine: "var(--graph-blue-line)",
  approved: "var(--graph-green)",
  approvedBg: "var(--graph-green-bg)",
  approvedLine: "var(--graph-green-line)",
  candidate: "var(--graph-yellow)",
  candidateBg: "var(--graph-yellow-bg)",
  candidateLine: "var(--graph-yellow-line)",
  conflict: "var(--graph-red)",
  conflictBg: "var(--graph-red-bg)",
  conflictLine: "var(--graph-red-line)",
  edgeDefault: "var(--graph-edge-default)",
};

let _cssVarCacheGX = null;
function resolveCssVarGX(token) {
  if (typeof token !== "string") return token;
  const match = token.match(/^var\((--[a-zA-Z0-9-]+)\)$/);
  if (!match) return token;
  if (!_cssVarCacheGX) _cssVarCacheGX = {};
  const name = match[1];
  if (_cssVarCacheGX[name] !== undefined) return _cssVarCacheGX[name];
  let value = token;
  try {
    value = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || token;
  } catch {}
  _cssVarCacheGX[name] = value;
  return value;
}

function resolveCssColorsGX(palette) {
  const out = {};
  Object.entries(palette).forEach(([key, value]) => { out[key] = resolveCssVarGX(value); });
  return out;
}

let _roleColorsCYCache = null;
function getRoleColorsCY() {
  if (!_roleColorsCYCache) _roleColorsCYCache = resolveCssColorsGX(GRAPH_ROLE_COLORS_GX);
  return _roleColorsCYCache;
}

let _themeTokensCYCache = null;
function getThemeTokensCY() {
  if (!_themeTokensCYCache) {
    _themeTokensCYCache = {
      bg1: resolveCssVarGX("var(--bg-1)"),
      lineStrong: resolveCssVarGX("var(--line-strong)"),
      muted: resolveCssVarGX("var(--muted)"),
      faint: resolveCssVarGX("var(--faint)"),
      fontMono: resolveCssVarGX("var(--font-mono)") || "monospace",
    };
  }
  return _themeTokensCYCache;
}

function isZhGX(language) {
  return typeof isZhUI === "function" ? isZhUI(language) : String(language || "").startsWith("zh");
}

function tGX(language, en, zh) {
  return typeof tUI === "function" ? tUI(language, en, zh) : (isZhGX(language) ? zh : en);
}

function labelGX(value, language) {
  return typeof displayLabelUI === "function" ? displayLabelUI(value, language) : value;
}

function edgeKindLabelGX(value, language) {
  return labelGX(value, language);
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

const DEDUP_AUDIT_FIELDS_GX = [
  "candidate_id",
  "task_id",
  "run_id",
  "frontier_id",
  "dedup_decision",
  "matched_node_key",
  "matched_edge_key",
  "matched_element_key",
  "matched_status",
  "matched_source",
  "matched_collection",
  "nearest_proposal_match",
  "match_score",
  "match_evidence",
  "conflict_fields",
  "decision_reason",
  "source_fingerprint",
  "evidence_fingerprint",
  "llm_merge_decision_allowed",
];

function dedupAuditGX(item) {
  const payload = item?.payload || {};
  const audit = { ...(item?.dedup_audit || payload.dedup_audit || {}) };
  DEDUP_AUDIT_FIELDS_GX.forEach(field => {
    if (audit[field] !== undefined) return;
    const value = payload[field];
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return;
    audit[field] = value;
  });
  if (audit.llm_merge_decision_allowed === undefined && Object.keys(audit).length) audit.llm_merge_decision_allowed = false;
  return audit;
}

function dedupDecisionLabelGX(decision, language) {
  const key = String(decision || "");
  const labels = {
    merge_existing: tGX(language, "merge existing approved object", "命中已批准对象"),
    duplicate_existing_proposal: tGX(language, "duplicate existing proposal", "重复候选"),
    duplicate_current_run: tGX(language, "duplicate in current run", "本轮重复"),
    needs_review: tGX(language, "needs human review", "需要人工判定"),
    new_proposal: tGX(language, "new proposal", "新候选"),
  };
  return labels[key] || key || "—";
}

function knowledgeKindGX(item) {
  const kind = String(item?.knowledge_kind || "").trim().toLowerCase();
  if (kind) return kind;
  const graphKind = String(item?.graph_element_kind || "").trim().toLowerCase();
  if (graphKind === "node") return "object";
  if (graphKind === "edge") return "relation";
  const elementType = String(item?.element_type || item?.type || "").trim().toLowerCase();
  if (elementType.includes("finding")) return "finding";
  if (elementType.includes("edge")) return "relation";
  if (elementType.includes("node")) return "object";
  return elementType || "object";
}

function knowledgeKindLabelGX(kind, language) {
  const key = String(kind || "").trim().toLowerCase();
  const labels = {
    object: tGX(language, "objects", "对象"),
    relation: tGX(language, "relations", "关系"),
    finding: tGX(language, "findings", "发现"),
    claim: tGX(language, "claims", "断言"),
    observation: tGX(language, "observations", "观测"),
    actionable_object: tGX(language, "actions", "动作"),
    model_concept: tGX(language, "model concepts", "模型概念"),
  };
  return labels[key] || labelGX(key, language);
}

function endpointNodeWriteLabelGX(data, language) {
  if (data?.proposed_node_created) return tGX(language, "created in this run", "本轮已创建");
  if (data?.matched_node_key) return tGX(language, "skipped; reused matched node", "未新建，复用命中节点");
  return tGX(language, "not created", "未创建");
}

function auditValueGX(value) {
  if (value === false) return "false";
  if (value === true) return "true";
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(auditValueGX).join(" · ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function nodePropertyEntriesGX(node, detail) {
  const raw = detail || node?._raw || node || {};
  const payloads = [
    raw.key_properties,
    raw.properties,
    raw.source_row,
    node?._raw?.key_properties,
    node?._raw?.properties,
  ];
  const entries = [];
  const seen = new Set();
  payloads.forEach(payload => {
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return;
    Object.entries(payload).forEach(([key, value]) => {
      if (seen.has(key)) return;
      seen.add(key);
      entries.push([key, value]);
    });
  });
  return entries;
}

function NodePropertiesTableGX({ node, detail, loading, language }) {
  const entries = nodePropertyEntriesGX(node, detail);
  return (
    <div style={{ marginTop: 12 }}>
      <div className="eyebrow accent" style={{ marginBottom: 6 }}>{tGX(language, "Node properties", "节点属性")}</div>
      {loading && (
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginBottom: 6 }}>
          {tGX(language, "Loading source properties…", "正在加载来源属性…")}
        </div>
      )}
      {entries.length === 0 ? (
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.4 }}>
          {tGX(language, "No node properties returned for this graph node.", "该图节点没有返回属性。")}
        </div>
      ) : (
        <dl className="kv" style={{ alignItems: "start" }}>
          {entries.slice(0, 80).map(([key, value]) => (
            <React.Fragment key={key}>
              <dt>{key}</dt>
              <dd style={{ overflowWrap: "anywhere", whiteSpace: "normal" }}>{auditValueGX(value)}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}
      {entries.length > 80 && (
        <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
          {entries.length - 80} {tGX(language, "additional properties hidden", "个额外属性已隐藏")}
        </div>
      )}
    </div>
  );
}

function isOntologyModelNodeGX(node) {
  const raw = node?._raw || node || {};
  return String(raw.projection_source || "") === "OntologyModelGraph"
    || String(raw.id || node?.id || "").startsWith("ontology:")
    || ["OntologyObject", "OntologyProperty", "OntologyAction", "OntologyLink", "SemanticItem"].includes(String(raw.type || node?.type || ""));
}

function ontologyCatalogHrefGX(tenantId, node) {
  const raw = node?._raw || node || {};
  const canonicalKey = raw.canonical_key || raw.properties?.canonical_key || "";
  if (!canonicalKey) return "";
  return `/?screen=ontology&tenant=${encodeURIComponent(tenantId)}&artifact=${encodeURIComponent(canonicalKey)}&ontology_tab=catalog`;
}

function endpointDedupEvidenceGX(item) {
  const payload = item?.payload || {};
  const evidence = payload.endpoint_dedup_evidence || payload.endpoint_dedup || {};
  if (!evidence || typeof evidence !== "object") return [];
  return ["source", "target"].map(role => ({ role, data: evidence[role] })).filter(entry => entry.data && typeof entry.data === "object");
}

function ProposalMatchSummaryGX({ match, language }) {
  if (!match || typeof match !== "object") return null;
  const relation = match.relation_label || match.relation;
  const endpointLine = match.element_type === "edge"
    ? [match.source_label, relation, match.target_label].filter(Boolean).join(" ")
    : [match.label, match.ontology_type].filter(Boolean).join(" · ");
  return (
    <div style={{ marginTop: 6, border: "1px solid var(--line-soft)", background: "var(--bg)", padding: "8px 9px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
        <div style={{ fontWeight: 600, color: "var(--text)", overflowWrap: "anywhere" }}>
          {labelGX(match.name || endpointLine || match.element_key, language)}
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", whiteSpace: "nowrap" }}>
          {match.element_type || "—"} · {statusLabelGraphGX(match.status, language)}
        </div>
      </div>
      {endpointLine && (
        <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-dim)", overflowWrap: "anywhere" }}>
          {labelGX(endpointLine, language)}
        </div>
      )}
      <dl className="kv" style={{ marginTop: 6 }}>
        <dt>{tGX(language, "Key", "键")}</dt><dd>{match.element_key || "—"}</dd>
        <dt>{tGX(language, "Run", "运行")}</dt><dd>{match.run_key || "—"}</dd>
        <dt>{tGX(language, "Confidence", "置信度")}</dt><dd>{match.confidence === undefined || match.confidence === null ? "—" : `${Math.round(match.confidence * 100)}%`}</dd>
        <dt>{tGX(language, "Source", "来源")}</dt><dd>{match.source_url || "—"}</dd>
      </dl>
    </div>
  );
}

function rawEdgeKindGX(edge) {
  return edge?.link_key || edge?.kind || edge?.ontology_link || edge?.label || "";
}

function graphEdgeKeyGX(edge) {
  const raw = edge?._raw || edge || {};
  return String(
    raw.id ||
    raw.edge_key ||
    raw.key ||
    edge?._key ||
    edge?.id ||
    `${edge?.s || raw.source || raw.s}->${edge?.t || raw.target || raw.t}:${edge?.kind || rawEdgeKindGX(raw)}:${edge?.factNode || raw.fact_node || raw.source_pk || ""}`
  );
}

function graphEdgeSearchTextGX(edge, nodeMap = {}) {
  const s = edge?.s || edge?._raw?.source || "";
  const t = edge?.t || edge?._raw?.target || "";
  return [
    s,
    t,
    nodeMap[s]?.label,
    nodeMap[t]?.label,
    edge?.kind,
    edge?.factNode,
    edge?._raw?.label,
    edge?._raw?.source_table,
    edge?._raw?.source_pk,
  ].filter(Boolean).join(" ").toLowerCase();
}

function graphEdgeRankGX(edge, selectedId = "") {
  let score = 0;
  if (edge?.s === selectedId) score += 4;
  if (edge?.t === selectedId) score += 2;
  if (edge?.muted) score -= 10;
  return score;
}

// Unweighted BFS shortest path over the already-loaded subgraph — no backend round
// trip needed since the client already holds the full node/edge set for the active
// scope. Returns { nodeIds, edgeKeys } (both include source..target) or null.
function findShortestPathGX(edges, sourceId, targetId) {
  if (!sourceId || !targetId || sourceId === targetId) return null;
  const adjacency = new Map();
  edges.forEach(e => {
    const edgeKey = graphEdgeKeyGX(e);
    if (!adjacency.has(e.s)) adjacency.set(e.s, []);
    if (!adjacency.has(e.t)) adjacency.set(e.t, []);
    adjacency.get(e.s).push({ next: e.t, edgeKey });
    adjacency.get(e.t).push({ next: e.s, edgeKey });
  });
  const visited = new Set([sourceId]);
  const queue = [{ id: sourceId, nodeIds: [sourceId], edgeKeys: [] }];
  let head = 0;
  while (head < queue.length) {
    const current = queue[head++];
    if (current.id === targetId) return { nodeIds: current.nodeIds, edgeKeys: current.edgeKeys };
    for (const { next, edgeKey } of adjacency.get(current.id) || []) {
      if (visited.has(next)) continue;
      visited.add(next);
      queue.push({ id: next, nodeIds: [...current.nodeIds, next], edgeKeys: [...current.edgeKeys, edgeKey] });
    }
  }
  return null;
}

// BFS hop-distance from sourceId over the already-loaded edge set, capped at
// maxHops. Powers ego mode's "structural distance" view -- no embeddings
// needed, just graph topology.
function bfsHopDistancesGX(edges, sourceId, maxHops) {
  const distances = new Map();
  if (!sourceId) return distances;
  const adjacency = new Map();
  edges.forEach(e => {
    if (!adjacency.has(e.s)) adjacency.set(e.s, []);
    if (!adjacency.has(e.t)) adjacency.set(e.t, []);
    adjacency.get(e.s).push(e.t);
    adjacency.get(e.t).push(e.s);
  });
  distances.set(sourceId, 0);
  const queue = [sourceId];
  let head = 0;
  while (head < queue.length) {
    const current = queue[head++];
    const hop = distances.get(current);
    if (hop >= maxHops) continue;
    for (const next of adjacency.get(current) || []) {
      if (distances.has(next)) continue;
      distances.set(next, hop + 1);
      queue.push(next);
    }
  }
  return distances;
}

function findGraphNodeMatchGX(query, nodes, language) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return null;
  const exactId = nodes.find(n => String(n.id).toLowerCase() === q);
  if (exactId) return exactId;
  const exactLabel = nodes.find(n => String(labelGX(n.label, language) || "").toLowerCase() === q);
  if (exactLabel) return exactLabel;
  return nodes.find(n => String(n.id).toLowerCase().includes(q) || String(labelGX(n.label, language) || "").toLowerCase().includes(q)) || null;
}

function rankGraphNodeMatchesGX(query, nodes, language, limit = 8) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return [];
  const scored = [];
  nodes.forEach(n => {
    const id = String(n.id).toLowerCase();
    const label = String(labelGX(n.label, language) || "").toLowerCase();
    let score = -1;
    if (id === q || label === q) score = 3;
    else if (id.startsWith(q) || label.startsWith(q)) score = 2;
    else if (id.includes(q) || label.includes(q)) score = 1;
    if (score >= 0) scored.push({ node: n, score });
  });
  scored.sort((a, b) => b.score - a.score || String(a.node.id).localeCompare(String(b.node.id)));
  return scored.slice(0, limit).map(s => s.node);
}

function graphEdgeToneGX(edge) {
  if (edge?.flag || edge?._raw?.conflict) return GRAPH_ROLE_COLORS_GX.conflict;
  if (edge?._raw?.status === "proposed" || edge?._raw?.status === "draft") return GRAPH_ROLE_COLORS_GX.candidate;
  return GRAPH_ROLE_COLORS_GX.edgeDefault;
}

function graphElementTypeColorGX(type) {
  const key = String(type || "").toLowerCase();
  if (key === "node") return GRAPH_ROLE_COLORS_GX.selected;
  if (key === "edge") return GRAPH_ROLE_COLORS_GX.approved;
  if (key === "finding") return GRAPH_ROLE_COLORS_GX.candidate;
  return GRAPH_ROLE_COLORS_GX.conflict;
}

function graphReviewStatusColorGX(status) {
  const key = String(status || "").toLowerCase();
  if (key === "approved" || key === "done") return GRAPH_ROLE_COLORS_GX.approved;
  if (key === "rejected" || key === "failed" || key === "blocked") return GRAPH_ROLE_COLORS_GX.conflict;
  if (key === "draft" || key === "proposed" || key === "needs_evidence" || key === "changes") return GRAPH_ROLE_COLORS_GX.candidate;
  return GRAPH_ROLE_COLORS_GX.selected;
}

function hashSeedGX(value) {
  let h = 2166136261 >>> 0;
  const s = String(value || "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

const GRAPH_LAYOUT_GOLDEN_ANGLE_GX = 137.50776405003785 * Math.PI / 180;
const GRAPH_LAYOUT_CENTER_X_GX = 500;
const GRAPH_LAYOUT_CENTER_Y_GX = 300;

// Deterministic hash-seeded layout for nodes the backend doesn't position and the
// user hasn't dragged: nodes are seeded by type into a golden-angle spiral (a
// reasonable, non-overlapping starting scatter), then relaxed with a bounded
// force simulation -- pairwise repulsion, spring attraction along real edges,
// and a mild pull toward each node's same-type centroid -- so connected and
// same-type nodes visually cluster instead of sitting on a flat ring. No
// Math.random anywhere, so the same node set always converges to the same
// layout (stable across reloads).
function layoutGraphNodesGX(nodes, edges, centerId) {
  const pending = nodes.filter(n => n.x == null || n.y == null || Number.isNaN(n.x) || Number.isNaN(n.y));
  if (!pending.length) return nodes;
  const pendingIds = new Set(pending.map(n => n.id));
  const pos = new Map();
  nodes.forEach(n => {
    if (!pendingIds.has(n.id)) pos.set(n.id, { x: n.x, y: n.y });
  });

  // Seed: per-type cluster spread around a circle, golden-angle spiral within
  // each cluster -- only needs to be a reasonable starting point, the force
  // pass below does the real layout work.
  const groups = new Map();
  pending.forEach(n => {
    const key = n.type || "unknown";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  });
  const groupKeys = Array.from(groups.keys()).sort();
  const clusterRadius = groupKeys.length > 1 ? 200 : 0;
  groupKeys.forEach((key, gi) => {
    const members = groups.get(key).slice().sort((a, b) => String(a.id).localeCompare(String(b.id)));
    const clusterAngle = groupKeys.length > 1 ? (gi / groupKeys.length) * 2 * Math.PI : 0;
    const clusterCx = GRAPH_LAYOUT_CENTER_X_GX + clusterRadius * Math.cos(clusterAngle);
    const clusterCy = GRAPH_LAYOUT_CENTER_Y_GX + clusterRadius * Math.sin(clusterAngle);
    members.forEach((n, i) => {
      if (centerId && n.id === centerId) {
        pos.set(n.id, { x: GRAPH_LAYOUT_CENTER_X_GX, y: GRAPH_LAYOUT_CENTER_Y_GX });
        return;
      }
      const seed = hashSeedGX(n.id);
      const jitter = ((seed % 1000) / 1000 - 0.5) * 6;
      const angle = i * GRAPH_LAYOUT_GOLDEN_ANGLE_GX + (seed % 360) * (Math.PI / 180) * 0.02;
      const radius = 22 * Math.sqrt(i + 1) + jitter;
      pos.set(n.id, { x: clusterCx + radius * Math.cos(angle), y: clusterCy + radius * Math.sin(angle) });
    });
  });

  // Force relaxation: classic Fruchterman-Reingold -- repulsion k^2/dist between
  // every pair, attraction dist^2/k along real edges and (more weakly) toward
  // each node's same-type centroid, both forces scaled off the same ideal
  // distance k so they stay balanced regardless of node count (unlike ad hoc
  // constants, which either explode or collapse the layout at different
  // graph sizes). Per-iteration displacement is capped by a "temperature"
  // that cools linearly to 0, the standard technique for convergence without
  // oscillation. Bounded iteration count keeps this well under a frame budget
  // even at a few hundred nodes; skipped entirely past a safety threshold to
  // avoid an O(n^2) blowup on very large scopes.
  const allIds = nodes.map(n => n.id);
  const nodeType = new Map(nodes.map(n => [n.id, n.type || "unknown"]));
  const layoutEdges = (edges || [])
    .map(e => ({ s: e.s, t: e.t }))
    .filter(e => pos.has(e.s) && pos.has(e.t) && e.s !== e.t);
  const movableIds = allIds.filter(id => pendingIds.has(id) && id !== centerId);
  const iterations = allIds.length > 500 ? 0 : (allIds.length > 300 ? 60 : 150);
  const area = Math.max(1000 * 600, allIds.length * 4500);
  const k = Math.sqrt(area / Math.max(1, allIds.length));
  const TYPE_PULL_FACTOR = 0.2;
  const CENTER_PULL = 0.09;
  let temperature = Math.sqrt(area) / 12;
  const coolingStep = iterations ? temperature / iterations : 0;

  for (let iter = 0; iter < iterations; iter++) {
    const disp = new Map(allIds.map(id => [id, { x: 0, y: 0 }]));

    for (let i = 0; i < allIds.length; i++) {
      const a = allIds[i], pa = pos.get(a);
      for (let j = i + 1; j < allIds.length; j++) {
        const b = allIds[j], pb = pos.get(b);
        let dx = pa.x - pb.x, dy = pa.y - pb.y;
        let dist = Math.hypot(dx, dy);
        if (dist < 0.01) { dx = 0.1; dy = 0.1; dist = 0.14; }
        const force = (k * k) / dist;
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        const da = disp.get(a), db = disp.get(b);
        da.x += fx; da.y += fy;
        db.x -= fx; db.y -= fy;
      }
    }

    layoutEdges.forEach(({ s, t }) => {
      const ps = pos.get(s), pt = pos.get(t);
      const dx = pt.x - ps.x, dy = pt.y - ps.y;
      const dist = Math.max(0.1, Math.hypot(dx, dy));
      const force = (dist * dist) / k;
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      disp.get(s).x += fx; disp.get(s).y += fy;
      disp.get(t).x -= fx; disp.get(t).y -= fy;
    });

    const centroids = new Map();
    allIds.forEach(id => {
      const key = nodeType.get(id);
      if (!centroids.has(key)) centroids.set(key, { x: 0, y: 0, n: 0 });
      const c = centroids.get(key), p = pos.get(id);
      c.x += p.x; c.y += p.y; c.n += 1;
    });
    centroids.forEach(c => { c.x /= c.n; c.y /= c.n; });
    allIds.forEach(id => {
      const c = centroids.get(nodeType.get(id));
      const p = pos.get(id), d = disp.get(id);
      const cdx = c.x - p.x, cdy = c.y - p.y;
      const cdist = Math.max(0.1, Math.hypot(cdx, cdy));
      const typeForce = ((cdist * cdist) / k) * TYPE_PULL_FACTOR;
      d.x += (cdx / cdist) * typeForce + (GRAPH_LAYOUT_CENTER_X_GX - p.x) * CENTER_PULL;
      d.y += (cdy / cdist) * typeForce + (GRAPH_LAYOUT_CENTER_Y_GX - p.y) * CENTER_PULL;
    });

    movableIds.forEach(id => {
      const p = pos.get(id), d = disp.get(id);
      const len = Math.max(0.01, Math.hypot(d.x, d.y));
      const capped = Math.min(len, temperature);
      p.x += (d.x / len) * capped;
      p.y += (d.y / len) * capped;
    });
    temperature = Math.max(0, temperature - coolingStep);
  }

  return nodes.map(n => {
    const p = pos.get(n.id);
    return p ? { ...n, x: p.x, y: p.y } : n;
  });
}

// normalize /api/graph/context response into the app's graph shape.
// The backend never sends node x/y; layoutGraphNodesGX fills in a deterministic
// position for any node the user hasn't manually dragged (see BigGraph).
function normalizeGraph(raw, fallback, language) {
  if (!raw || !raw.nodes) return fallback;
  const rawNodes = raw.nodes || [];
  const rawEdges = raw.edges || [];
  const edges = rawEdges.map((e, edgeIndex) => ({
    _key: e.id || e.edge_key || e.key || `${e.source || e.s}->${e.target || e.t}:${e.link_key || e.label || e.kind || e.ontology_link || ""}:${e.source_pk || edgeIndex}`,
    s: e.source || e.s,
    t: e.target || e.t,
    kind: e.label || e.kind || e.ontology_link || e.link_key || "",
    flag: e.flag || e.conflict || false,
    muted: e.muted,
    _raw: e,
  }));
  const degreeById = new Map();
  edges.forEach(e => {
    degreeById.set(e.s, (degreeById.get(e.s) || 0) + 1);
    degreeById.set(e.t, (degreeById.get(e.t) || 0) + 1);
  });
  const nodes = rawNodes.map(n => {
    const isCenter = !!(raw.center && raw.center.id === n.id);
    const degree = degreeById.get(n.id) || 0;
    return {
      id: n.id, type: n.type,
      label: n.type === "Country"
        ? countryLabelGX(n.label || n.id, language)
        : labelGX(n.label || (n.key_properties && (n.key_properties.name || n.key_properties.title)) || n.id, language),
      x: n.x, y: n.y, r: n.r || (isCenter ? 18 : 11 + Math.min(7, Math.round(Math.sqrt(degree) * 2.2))),
      degree,
      center: isCenter,
      flag: !!n.flag,
      _raw: n,
    };
  });
  return {
    center: raw.center && raw.center.id,
    nodes: layoutGraphNodesGX(nodes, edges, raw.center && raw.center.id),
    edges,
  };
}

function compactText(value, limit = 96) {
  const text = String(value || "");
  return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
}

function normalizeCenterInputGX(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function resolveCenterInputGX(input, centerType, candidates) {
  const raw = String(input || "").trim();
  if (!raw) return "";
  const normalized = normalizeCenterInputGX(raw);
  const matches = (candidates || []).map(c => {
    const id = String(c.id || "");
    const shortId = id.split(":").slice(1).join(":");
    const label = String(c.label || c.name || shortId || id);
    const localizedNames = c.localized_names && typeof c.localized_names === "object" ? Object.values(c.localized_names) : [];
    const aliases = Array.isArray(c.aliases) ? c.aliases : [];
    const keyProps = c.key_properties && typeof c.key_properties === "object" ? Object.values(c.key_properties) : [];
    const searchTerms = [id, shortId, label, c.name, c.title, ...aliases, ...localizedNames, ...keyProps]
      .filter(v => v != null && v !== "")
      .map(v => normalizeCenterInputGX(v));
    return { ...c, id, shortId, label, searchTerms };
  });
  const displayedShortCode = raw.match(/\(([A-Za-z0-9_-]{2,16})\)\s*$/);
  const directShortId = displayedShortCode ? displayedShortCode[1] : (/^[a-z0-9_-]{2,16}$/i.test(raw) ? raw : "");
  if (directShortId) {
    const code = normalizeCenterInputGX(directShortId);
    const directMatch = matches.find(c =>
      normalizeCenterInputGX(c.shortId) === code ||
      normalizeCenterInputGX(c.id) === normalizeCenterInputGX(`${centerType}:${directShortId}`) ||
      (c.searchTerms || []).some(term => term === normalized || term === code || term.includes(`(${code})`))
    );
    return directMatch ? directMatch.shortId : "";
  }
  const exact = matches.find(c =>
    (c.searchTerms || []).some(term => term === normalized)
  );
  if (exact) return exact.shortId;
  const fuzzy = matches.find(c =>
    (c.searchTerms || []).some(term => term.includes(normalized))
  );
  return fuzzy ? fuzzy.shortId : "";
}

function GraphExplorer({ data, tenant, language }) {
  const initialParams = useMemoGX(() => {
    try { return new URLSearchParams(location.search); } catch { return new URLSearchParams(); }
  }, []);
  const tenantId = tenant ? tenant.id : "default";
  const debugGraphCandidates = ["1", "true", "yes", "debug"].includes(String(initialParams.get("debug_graph_candidates") || "").toLowerCase());
  const requestedGraphTab = initialParams.get("graph_tab") || "approved";
  const normalizeGraphTab = (tab) => {
    if (["approved", "ontology", "saved"].includes(tab)) return tab;
    if (debugGraphCandidates && (tab === "proposed" || tab === "runs")) return "proposed";
    return "approved";
  };
  const graphView = "all";
  const requestedTenantId = initialParams.get("tenant") || "";
  const requestedCenterType = initialParams.get("type") || "";
  const requestedCenterNodeId = initialParams.get("id") || "";
  const requestedSelectedNodeId = initialParams.get("selected_node") || "";
  const requestedSelectedEdgeKey = initialParams.get("selected_edge") || "";
  const [centerType, setCenterType] = useStateGX(initialParams.get("type") || "");
  const [centerNodeId, setCenterNodeId] = useStateGX(initialParams.get("id") || "");
  const [depth, setDepth] = useStateGX(Math.max(1, Math.min(Number(initialParams.get("depth") || 1), 3)));
  const [limit, setLimit] = useStateGX(Math.max(1, Number(initialParams.get("limit") || 200)));
  const [hoverId, setHoverId] = useStateGX(null);
  const [leftTab, setLeftTab] = useStateGX(normalizeGraphTab(requestedGraphTab));
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
  const candidateMatchesCenterType = (candidate) => {
    const id = String(candidate?.id || "");
    return !!centerType && (id === centerType || id.startsWith(`${centerType}:`));
  };
  const candidates = Array.isArray(candidatesQ.data)
    ? candidatesQ.data.filter(candidateMatchesCenterType)
    : [];
  const allCandidatesQ = useApiData(
    "instanceSearch",
    [tenantId, centerType, "", 300, { includeDraft: true }],
    { enabled: typesLoaded && !!activeType, fallback: [] }
  );
  const allCandidates = Array.isArray(allCandidatesQ.data)
    ? allCandidatesQ.data.filter(candidateMatchesCenterType)
    : [];
  const centerCandidatesLoading = candidatesQ.loading || allCandidatesQ.loading || candidatesQ.source === "loading" || allCandidatesQ.source === "loading";
  const centerCandidateUniverse = allCandidates.length ? allCandidates : candidates;
  const visibleCandidates = candidates.length || centerSearch.trim() ? candidates : allCandidates;
  const proposedQ = useApiData("graphProposedElements", [tenantId, {}], {
    enabled: debugGraphCandidates,
    fallback: { runs: [], elements: [] },
  });
  const proposed = proposedQ.data || { runs: [], elements: [] };
  const proposedGraphDebugCount = debugGraphCandidates ? (proposed.elements || []).filter(item => !isOntologyReviewElementGX(item)).length : 0;
  const selectGraphTab = (tab) => {
    setLeftTab(normalizeGraphTab(tab));
  };
  const applyCenterInput = () => {
    if (!centerType || !centerSearch.trim()) return;
    const resolved = resolveCenterInputGX(centerSearch, centerType, centerCandidateUniverse);
    if (resolved) {
      setCenterNodeId(resolved);
      setFocusMessage(tGX(language, "Center resolved inside current tenant. Load full graph to focus it.", "已在当前租户内解析中心节点；点击加载全图后聚焦。"));
    } else {
      setCenterNodeId("");
      setFocusMessage(tGX(language, "No matching center node for this type in the current tenant.", "当前租户的当前类型下没有匹配的中心节点。"));
    }
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
    if (!centerType || centerCandidatesLoading) return;
    if (centerCandidateUniverse.length === 0) {
      if (centerNodeId) setCenterNodeId("");
      return;
    }
    const expectedId = `${centerType}:${centerNodeId}`;
    const match = centerNodeId && centerCandidateUniverse.some(c => c.id === expectedId || String(c.id || "").endsWith(`:${centerNodeId}`));
    if (
      !centerNodeId &&
      requestedCenterType === centerType &&
      requestedCenterNodeId &&
      (!requestedTenantId || requestedTenantId === tenantId)
    ) {
      const resolvedRequested = resolveCenterInputGX(requestedCenterNodeId, centerType, centerCandidateUniverse);
      if (resolvedRequested) setCenterNodeId(resolvedRequested);
      else setCenterSearch("");
      return;
    }
    if (!centerNodeId && !centerSearch.trim()) {
      const first = centerCandidateUniverse[0];
      setCenterNodeId(String(first.id || "").split(":").slice(1).join(":"));
    } else if (!match && centerSearch.trim()) {
      const resolved = resolveCenterInputGX(centerSearch, centerType, centerCandidateUniverse);
      if (resolved) {
        setCenterNodeId(resolved);
        setFocusMessage(tGX(language, "Center resolved inside current tenant. Load full graph to focus it.", "已在当前租户内解析中心节点；点击加载全图后聚焦。"));
        return;
      }
      setCenterNodeId("");
      setFocusMessage(tGX(language, "No matching center node for this type in the current tenant.", "当前租户的当前类型下没有匹配的中心节点。"));
    } else if (!match) {
      setCenterNodeId("");
      setFocusMessage(`${expectedId} is outside the visible center list.`);
    }
  }, [tenantId, centerType, centerSearch, centerCandidatesLoading, JSON.stringify(centerCandidateUniverse.map(c => c.id))]);

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
      if (debugGraphCandidates) url.searchParams.set("debug_graph_candidates", "1");
      else url.searchParams.delete("debug_graph_candidates");
      url.searchParams.delete("artifact");
      if (debugGraphCandidates && focusElementKey) url.searchParams.set("proposed_key", focusElementKey); else url.searchParams.delete("proposed_key");
      history.replaceState(null, "", url.toString());
    } catch {}
  }, [tenantId, centerType, centerNodeId, depth, limit, leftTab, focusElementKey, graphView, debugGraphCandidates]);

  const graphQ = useApiData(
    "graphContext",
    [tenantId, { type: centerType, id: centerNodeId, depth, limit, view: graphView }],
    { enabled: typesLoaded, fallback: null }
  );
  const ontologyGraphQ = useApiData(
    "ontologyModelGraph",
    [tenantId, { limit }],
    { enabled: true, fallback: null }
  );
  const activeGraphQ = leftTab === "ontology" ? ontologyGraphQ : graphQ;
  const isStaleG = activeGraphQ.source === "live-stale";
  const isMockG  = activeGraphQ.source === "mock";
  const approvedGraph = useMemoGX(() => normalizeGraph(graphQ.data, { nodes: [], edges: [] }, language), [graphQ.data, language]);
  const ontologyGraph = useMemoGX(() => normalizeGraph(ontologyGraphQ.data, { nodes: [], edges: [] }, language), [ontologyGraphQ.data, language]);
  const graph = useMemoGX(() => normalizeGraph(activeGraphQ.data, { nodes: [], edges: [] }, language), [activeGraphQ.data, language]);
  const ontologyScope = (ontologyGraphQ.data && ontologyGraphQ.data.scope) || {};

  const [selected, setSelected] = useStateGX(null);
  const [trailNodeIds, setTrailNodeIds] = useStateGX([]);
  const [trailEdgeKeys, setTrailEdgeKeys] = useStateGX([]);
  const [selectedEdgeKey, setSelectedEdgeKey] = useStateGX(requestedSelectedEdgeKey);
  const [edgeSearch, setEdgeSearch] = useStateGX("");
  const [edgeSort, setEdgeSort] = useStateGX("rank");
  const [showNearbyCandidates, setShowNearbyCandidates] = useStateGX(true);
  const [pathTargetQuery, setPathTargetQuery] = useStateGX("");
  const [pathTraceResult, setPathTraceResult] = useStateGX(null);
  const [nodeSearchQuery, setNodeSearchQuery] = useStateGX("");
  const [egoModeEnabled, setEgoModeEnabled] = useStateGX(false);
  const [egoMaxHops, setEgoMaxHops] = useStateGX(2);
  const [alwaysShowEdgeLabels, setAlwaysShowEdgeLabels] = useStateGX(false);
  const [nodePositions, setNodePositions] = useStateGX({});
  const [hideUnrelated, setHideUnrelated] = useStateGX(false);
  const cyApiRef = useRefGX(null);
  const [collapseOffTrailEdges, setCollapseOffTrailEdges] = useStateGX(true);
  const [pendingCenterFocus, setPendingCenterFocus] = useStateGX("");
  const [focusMessage, setFocusMessage] = useStateGX("");
  const [initialGraphSelectionApplied, setInitialGraphSelectionApplied] = useStateGX(false);
  const selectedIsOntologyModel = isOntologyModelNodeGX(selected);
  const selectedNodeDetailQ = useApiData(
    "graphNodeDetail",
    [tenantId, selected?.id || ""],
    { enabled: !!selected?.id && !selectedIsOntologyModel, fallback: null }
  );
  const selectedNodeDetail = selectedNodeDetailQ.data || selected?._raw || null;
  useEffectGX(() => {
    setSelected(null);
    setTrailNodeIds([]);
    setTrailEdgeKeys([]);
    setSelectedEdgeKey("");
    setEdgeSearch("");
    setShowNearbyCandidates(true);
    setHoverId(null);
    setPendingCenterFocus("");
    setFocusMessage("");
    setNodePositions({});
    setHideUnrelated(false);
    setPathTargetQuery("");
    setPathTraceResult(null);
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
      if (trailNodeIds.length) setTrailNodeIds([]);
      return;
    }
    setSelected(prev => graphWithPositions.nodes.find(n => prev && n.id === prev.id) || null);
    const nodeIds = new Set(graphWithPositions.nodes.map(n => n.id));
    setTrailNodeIds(prev => {
      const next = prev.filter(id => nodeIds.has(id));
      return next.length === prev.length ? prev : next;
    });
    const edgeKeys = new Set(graphWithPositions.edges.map(graphEdgeKeyGX));
    setTrailEdgeKeys(prev => {
      const next = prev.filter(key => edgeKeys.has(key));
      return next.length === prev.length ? prev : next;
    });
    setSelectedEdgeKey(prev => !prev || edgeKeys.has(prev) ? prev : "");
  }, [graphWithPositions]);
  useEffectGX(() => {
    if (!selected && hideUnrelated) setHideUnrelated(false);
  }, [selected, hideUnrelated]);

  useEffectGX(() => {
    if (initialGraphSelectionApplied || !graphWithPositions.nodes.length) return;
    if (!requestedSelectedNodeId && !requestedSelectedEdgeKey) {
      setInitialGraphSelectionApplied(true);
      return;
    }
    const initialNode = requestedSelectedNodeId
      ? graphWithPositions.nodes.find(node => node.id === requestedSelectedNodeId)
      : null;
    if (initialNode) {
      setSelected(initialNode);
      setTrailNodeIds([initialNode.id]);
    }
    if (requestedSelectedEdgeKey) {
      const edge = graphWithPositions.edges.find(item => graphEdgeKeyGX(item) === requestedSelectedEdgeKey);
      if (edge) {
        setSelectedEdgeKey(requestedSelectedEdgeKey);
        setTrailEdgeKeys([requestedSelectedEdgeKey]);
        const nodeId = initialNode?.id || edge.s || edge.t;
        const node = graphWithPositions.nodes.find(item => item.id === nodeId);
        if (node) {
          setSelected(node);
          setTrailNodeIds(prev => prev.includes(node.id) ? prev : [...prev, node.id]);
        }
      }
    }
    setInitialGraphSelectionApplied(true);
  }, [
    initialGraphSelectionApplied,
    requestedSelectedNodeId,
    requestedSelectedEdgeKey,
    graphWithPositions.nodes.map(node => node.id).join("|"),
    graphWithPositions.edges.map(graphEdgeKeyGX).join("|"),
  ]);

  useEffectGX(() => {
    try {
      const url = new URL(location.href);
      url.searchParams.set("screen", "graph");
      url.searchParams.set("tenant", tenantId);
      if (selected?.id) url.searchParams.set("selected_node", selected.id);
      else url.searchParams.delete("selected_node");
      if (selectedEdgeKey) url.searchParams.set("selected_edge", selectedEdgeKey);
      else url.searchParams.delete("selected_edge");
      if (hideUnrelated) url.searchParams.set("trail_focus", "1");
      else url.searchParams.delete("trail_focus");
      history.replaceState(null, "", url.toString());
    } catch {}
  }, [tenantId, selected?.id, selectedEdgeKey, hideUnrelated]);

  const map = Object.fromEntries(graphWithPositions.nodes.map(n => [n.id, n]));
  const connectedEdgesAll = selected
    ? graphWithPositions.edges.filter(e => e.s === selected.id || e.t === selected.id)
    : [];
  const connectedEdgesInCount = selected ? connectedEdgesAll.filter(e => e.t === selected.id).length : 0;
  const connectedEdgesOutCount = selected ? connectedEdgesAll.filter(e => e.s === selected.id).length : 0;
  const connectedEdgeKindCounts = connectedEdgesAll.reduce((acc, e) => {
    const key = e.kind || "edge";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
  const topConnectedEdgeKind = Object.entries(connectedEdgeKindCounts).sort((a, b) => b[1] - a[1])[0] || null;
  const connectedEdgeLimit = 20;
  const connectedEdgesRanked = connectedEdgesAll.slice().sort((a, b) => {
    if (edgeSort === "kind") {
      return String(a.kind || "").localeCompare(String(b.kind || "")) || String(a.s || "").localeCompare(String(b.s || ""));
    }
    if (edgeSort === "target") {
      const ao = a.s === selected?.id ? a.t : a.s;
      const bo = b.s === selected?.id ? b.t : b.s;
      return labelGX(map[ao]?.label || ao, language).localeCompare(labelGX(map[bo]?.label || bo, language));
    }
    return graphEdgeRankGX(b, selected?.id) - graphEdgeRankGX(a, selected?.id)
      || String(a.kind || "").localeCompare(String(b.kind || ""))
      || String(a.s || "").localeCompare(String(b.s || ""));
  });
  const edgeSearchText = edgeSearch.trim().toLowerCase();
  const connectedEdgesFiltered = edgeSearchText
    ? connectedEdgesRanked.filter(edge => graphEdgeSearchTextGX(edge, map).includes(edgeSearchText))
    : connectedEdgesRanked;
  const connectedEdgesVisible = connectedEdgesFiltered.slice(0, 1000);
  const canvasCandidateEdges = showNearbyCandidates
    ? connectedEdgesRanked.slice(0, connectedEdgeLimit)
    : [];
  const canvasCandidateEdgeKeys = canvasCandidateEdges.map(graphEdgeKeyGX);
  const hiddenCanvasEdgeCount = Math.max(0, connectedEdgesAll.length - canvasCandidateEdges.length);
  const rememberTrailEdge = (edge) => {
    if (!edge) return;
    const key = graphEdgeKeyGX(edge);
    setTrailEdgeKeys(prev => prev.includes(key) ? prev : [...prev, key]);
    setSelectedEdgeKey(key);
  };
  const appendTrailNodes = (nodeIds) => {
    setTrailNodeIds(prev => {
      const next = [...prev];
      nodeIds.forEach(id => {
        if (id && !next.includes(id)) next.push(id);
      });
      return next;
    });
  };
  const selectGraphNode = (node, options = {}) => {
    if (!node) return;
    const previousId = selected?.id || "";
    setSelected(node);
    setPathTraceResult(null);
    if (options.reset) {
      setTrailNodeIds(node.id ? [node.id] : []);
      setTrailEdgeKeys([]);
      setSelectedEdgeKey("");
      setShowNearbyCandidates(true);
      return;
    }
    appendTrailNodes([node.id]);
    if (previousId && node.id && previousId !== node.id) {
      const edge = graphWithPositions.edges.find(e => (
        (e.s === previousId && e.t === node.id) || (e.t === previousId && e.s === node.id)
      ));
      if (edge) rememberTrailEdge(edge);
    }
  };
  const nodeSearchMatches = rankGraphNodeMatchesGX(nodeSearchQuery, graphWithPositions.nodes, language, 8);
  const mostConnectedNodes = graphWithPositions.nodes
    .filter(n => (n.degree || 0) > 0)
    .slice()
    .sort((a, b) => (b.degree || 0) - (a.degree || 0))
    .slice(0, 5);
  const selectNodeFromSearch = (node) => {
    selectGraphNode(node, { reset: true });
    setNodeSearchQuery("");
  };
  const selectConnectedEdge = (edge) => {
    if (!edge || !selected) return;
    const other = edge.s === selected.id ? edge.t : edge.s;
    appendTrailNodes([selected.id, other]);
    rememberTrailEdge(edge);
    setHideUnrelated(true);
    setCollapseOffTrailEdges(true);
    setShowNearbyCandidates(true);
    if (map[other]) setSelected(map[other]);
  };
  const clearGraphTrail = () => {
    setTrailNodeIds([]);
    setTrailEdgeKeys([]);
    setSelectedEdgeKey("");
    setSelected(null);
    setHideUnrelated(false);
    setPathTargetQuery("");
    setPathTraceResult(null);
  };
  const stepBackGraphTrail = () => {
    if (!trailNodeIds.length) return;
    const next = trailNodeIds.slice(0, -1);
    const nextEdges = trailEdgeKeys.slice(0, Math.max(0, next.length - 1));
    setTrailNodeIds(next);
    setTrailEdgeKeys(nextEdges);
    setSelectedEdgeKey(nextEdges[nextEdges.length - 1] || "");
    const nextSelected = next.length ? map[next[next.length - 1]] : null;
    setSelected(nextSelected || null);
    if (!next.length) setHideUnrelated(false);
  };
  const toggleTrailFocus = () => {
    if (!selected) return;
    setTrailNodeIds(prev => selected.id && !prev.includes(selected.id) ? [...prev, selected.id] : prev);
    setHideUnrelated(v => !v);
    setEgoModeEnabled(false);
  };
  const toggleEgoMode = () => {
    if (!selected) return;
    setEgoModeEnabled(v => !v);
    setHideUnrelated(false);
  };
  const egoHopDistances = useMemoGX(() => {
    if (!egoModeEnabled || !selected) return null;
    return bfsHopDistancesGX(graphWithPositions.edges, selected.id, egoMaxHops);
  }, [egoModeEnabled, selected?.id, egoMaxHops, graphWithPositions.edges]);
  const tracePathToTarget = () => {
    if (!selected) return;
    const query = pathTargetQuery.trim();
    if (!query) return;
    const target = findGraphNodeMatchGX(query, graphWithPositions.nodes, language);
    if (!target || target.id === selected.id) {
      setPathTraceResult({ found: false, query });
      return;
    }
    const path = findShortestPathGX(graphWithPositions.edges, selected.id, target.id);
    if (!path) {
      setPathTraceResult({ found: false, query, target });
      return;
    }
    setTrailNodeIds(path.nodeIds);
    setTrailEdgeKeys(path.edgeKeys);
    setSelectedEdgeKey(path.edgeKeys[path.edgeKeys.length - 1] || "");
    setHideUnrelated(true);
    setCollapseOffTrailEdges(true);
    setShowNearbyCandidates(true);
    setPathTraceResult({ found: true, query, target, nodeIds: path.nodeIds, edgeKeys: path.edgeKeys });
  };
  const clearPathTrace = () => {
    setPathTargetQuery("");
    setPathTraceResult(null);
  };
  const trailNodes = trailNodeIds.map(id => map[id]).filter(Boolean);
  const graphTypes = Array.from(new Set(graphWithPositions.nodes.map(n => n.type).filter(Boolean)));
  const typeColors = Object.fromEntries(graphTypes.map(t => [t, graphTypeColorGX(t)]));
  const typeNodeCounts = graphWithPositions.nodes.reduce((acc, n) => {
    if (!n.type) return acc;
    acc[n.type] = (acc[n.type] || 0) + 1;
    return acc;
  }, {});
  const legendTypeLimit = 8;
  const legendTypeEntries = Object.entries(typeColors).sort((a, b) => (typeNodeCounts[b[0]] || 0) - (typeNodeCounts[a[0]] || 0));
  const legendTypeVisible = legendTypeEntries.slice(0, legendTypeLimit);
  const legendTypeHiddenCount = Math.max(0, legendTypeEntries.length - legendTypeVisible.length);
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
      selectGraphNode(match, { reset: true });
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
    setTrailNodeIds([]);
    setTrailEdgeKeys([]);
    setSelectedEdgeKey("");
    setHideUnrelated(false);
    setFocusMessage(`Loading full graph for ${centerKey}…`);
    window.dispatchEvent(new CustomEvent("aletheia:retry"));
  };
  useEffectGX(() => {
    if (!pendingCenterFocus || graphQ.loading) return;
    const match = graphWithPositions.nodes.find(node => node.id === pendingCenterFocus);
    if (match) {
      selectGraphNode(match, { reset: true });
      setFocusMessage(`Focused ${pendingCenterFocus}`);
      setPendingCenterFocus("");
    } else if (graphQ.source !== "loading") {
      setFocusMessage(`${pendingCenterFocus} is not in the loaded full graph.`);
      setPendingCenterFocus("");
    }
  }, [pendingCenterFocus, graphQ.loading, graphQ.source, graphWithPositions.nodes.map(n => n.id).join("|")]);

  const updateNodePosition = (nodeId, point) => {
    const { x, y } = point;
    setNodePositions(prev => ({ ...prev, [nodeId]: { x, y } }));
    setSelected(prev => {
      if (!prev || prev.id !== nodeId) return prev;
      return { ...prev, x, y };
    });
  };
  const resetGraphLayoutGX = () => setNodePositions({});
  const exportSelectedNodeJsonGX = () => {
    if (!selected) return;
    const payload = {
      node: selectedNodeDetail || selected._raw || selected,
      connectedEdges: connectedEdgesAll.map(e => ({ source: e.s, target: e.t, kind: e.kind })),
      pathTrace: pathTraceResult && pathTraceResult.found ? pathTraceResult : null,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${String(selected.id).replace(/[^a-zA-Z0-9_.-]/g, "_")}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="eyebrow accent">{tGX(language, "Graph Explorer", "图谱探索")}</div>
        <div className="spacer" />
        {isMockG  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tGX(language, "Mock fallback", "模拟回退")}</span>}
        {isStaleG && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tGX(language, "Stale · last fetch failed", "数据陈旧 · 最近拉取失败")}</span>}
        {activeGraphQ.loading && activeGraphQ.data && <span className="pill"><span className="dot" />{tGX(language, "Refreshing…", "刷新中…")}</span>}
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
                {tGX(language, "Approved graph", "已批准图谱")} <span className="ct">{approvedGraph.nodes.length}</span>
              </button>
              <button className={"side-tab" + (leftTab === "ontology" ? " active" : "")} onClick={() => selectGraphTab("ontology")}>
                {tGX(language, "Ontology model", "本体模型")} <span className="ct">{ontologyScope.node_count || ontologyGraph.nodes.length || 0}</span>
              </button>
              {debugGraphCandidates && (
                <button className={"side-tab" + (leftTab === "proposed" ? " active" : "")} onClick={() => selectGraphTab("proposed")}>
                  {tGX(language, "Raw candidate projection", "原始候选投影")} <span className="ct">{proposedGraphDebugCount}</span>
                </button>
              )}
              <button className={"side-tab" + (leftTab === "saved" ? " active" : "")} onClick={() => selectGraphTab("saved")}>
                {tGX(language, "Saved views", "保存视图")} <span className="ct">0</span>
              </button>
            </div>
          </div>

          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 4 }}>{tGX(language, "Find node", "查找节点")}</div>
            <input
              className="input"
              value={nodeSearchQuery}
              onChange={e => setNodeSearchQuery(e.target.value)}
              placeholder={tGX(language, "Search loaded nodes by id or label…", "按 ID 或标签搜索已加载节点…")}
            />
            {nodeSearchQuery.trim() && (
              <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4, maxHeight: 180, overflowY: "auto" }}>
                {nodeSearchMatches.length === 0 && (
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                    {tGX(language, "No matches in the loaded graph.", "已加载的图中没有匹配项。")}
                  </div>
                )}
                {nodeSearchMatches.map(node => (
                  <button
                    key={node.id}
                    type="button"
                    className="btn ghost"
                    style={{ textAlign: "left", justifyContent: "flex-start" }}
                    onClick={() => selectNodeFromSearch(node)}>
                    <span style={{ color: "var(--accent)", marginRight: 6 }}>{node.type}</span>
                    {labelGX(node.label || node.id, language)}
                  </button>
                ))}
              </div>
            )}
          </div>

          {mostConnectedNodes.length > 0 && (
            <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
              <div className="eyebrow" style={{ marginBottom: 4 }}>{tGX(language, "Most connected", "连接最多")}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {mostConnectedNodes.map(node => (
                  <button
                    key={node.id}
                    type="button"
                    className="btn ghost"
                    style={{ textAlign: "left", justifyContent: "space-between", display: "flex" }}
                    onClick={() => selectGraphNode(node, { reset: true })}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      <span style={{ color: "var(--accent)", marginRight: 6 }}>{node.type}</span>
                      {labelGX(node.label || node.id, language)}
                    </span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>{node.degree}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {leftTab === "approved" && <>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>{tGX(language, "Center", "中心")}</div>
              <div style={{ display: "flex", gap: 6 }}>
                <select className="select" style={{ width: 110 }} value={centerType} onChange={e => { setCenterType(e.target.value); setCenterNodeId(""); setCenterSearch(""); clearGraphTrail(); setFocusMessage(""); }}>
                  {centerTypes.length === 0 && <option value="">{tGX(language, "No tenant types", "无租户类型")}</option>}
                  {centerTypes.map(t => <option key={t.type} value={t.type}>{t.label || t.type}{t.approved ? "" : " · draft"}</option>)}
                </select>
                <select className="select" value={centerNodeId} onChange={e => { setCenterNodeId(e.target.value); setCenterSearch(e.target.value); }} disabled={!centerType || centerCandidatesLoading || visibleCandidates.length === 0}>
                  {centerCandidatesLoading && <option value="">{tGX(language, "Loading center nodes", "加载中心节点")}</option>}
                  {!centerCandidatesLoading && visibleCandidates.length === 0 && <option value="">{tGX(language, "No center nodes", "无中心节点")}</option>}
                  {visibleCandidates.map(c => {
                    const id = String(c.id || "").split(":").slice(1).join(":");
                    return <option key={c.id} value={id}>{centerType === "Country" ? countryLabelGX(c.label || c.id, language) : labelGX(c.label || c.id, language)}</option>;
                  })}
                </select>
              </div>
              <input
                className="input"
                value={centerSearch}
                onChange={e => { setCenterSearch(e.target.value); setFocusMessage(""); }}
                onKeyDown={e => { if (e.key === "Enter") applyCenterInput(); }}
                placeholder={centerType === "Country" ? tGX(language, "Search or type country", "搜索或输入国家") : tGX(language, "Search or type center id", "搜索或输入中心 ID")}
                disabled={!centerType}
                style={{ marginTop: 6 }}
              />
              <button className="btn ghost" style={{ marginTop: 6, width: "100%" }} disabled={!centerType || !centerSearch.trim()} onClick={applyCenterInput}>
                {tGX(language, "Use typed center", "使用输入的中心节点")}
              </button>
              <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                {activeType ? `${activeType.table} · ${activeType.ontology_artifact} · ${activeType.artifact_status || "unknown"} · ${centerCandidatesLoading ? "…" : centerCandidateUniverse.length} ${tGX(language, "candidates", "候选")}` : tGX(language, "No tenant graph center types for this tenant.", "该租户没有可用的图谱中心类型。")}
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
              <dt>{tGX(language, "Trail", "路径")}</dt><dd>{trailNodes.length}</dd>
              <dt>{tGX(language, "Limit", "上限")}</dt><dd>{limit}</dd>
            </dl>
            {trailNodes.length > 0 && (
              <div style={{ marginTop: 8, padding: 8, border: "1px solid var(--line-soft)", background: "var(--bg-2)", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
                <div className="eyebrow accent" style={{ marginBottom: 4 }}>{tGX(language, "Trail focus", "路径聚焦")}</div>
                {trailNodes.slice(-6).map((node, index) => (
                  <div key={node.id} style={{ color: node.id === selected?.id ? "var(--accent)" : "var(--text-dim)" }}>
                    {index > 0 ? "→ " : ""}{labelGX(node.id, language)}
                  </div>
                ))}
                {trailNodes.length > 6 && <div>… {trailNodes.length - 6} {tGX(language, "earlier nodes", "个更早节点")}</div>}
              </div>
            )}
            <button
              className="btn ghost"
              style={{ marginTop: 10, width: "100%" }}
              disabled={!selected}
              onClick={toggleTrailFocus}>
              {hideUnrelated ? tGX(language, "Show all graph nodes", "显示所有图节点") : tGX(language, "Hide unrelated to trail", "隐藏路径外节点")}
            </button>
            {hideUnrelated && selected && (
              <button
                className="btn ghost"
                style={{ marginTop: 8, width: "100%" }}
                onClick={() => setShowNearbyCandidates(value => !value)}>
                {showNearbyCandidates
                  ? tGX(language, "Hide nearby candidates", "隐藏附近候选")
                  : tGX(language, "Show nearby candidates", "显示附近候选")}
              </button>
            )}
            {hideUnrelated && selected && (
              <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.35 }}>
                {tGX(language, "Canvas limit: trail plus top 20 candidate edges. Full edge set stays in the right list.", "画布限制：路径加 top 20 候选边；完整边集保留在右侧列表。")}
              </div>
            )}
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button className="btn ghost" style={{ flex: 1 }} disabled={trailNodes.length < 2} onClick={stepBackGraphTrail}>{tGX(language, "Back", "回退一步")}</button>
              <button className="btn ghost" style={{ flex: 1 }} disabled={!trailNodes.length} onClick={clearGraphTrail}>{tGX(language, "Clear trail", "清空路径")}</button>
            </div>
            <button
              className="btn ghost"
              style={{ marginTop: 8, width: "100%" }}
              disabled={!selected}
              onClick={toggleEgoMode}>
              {egoModeEnabled ? tGX(language, "Exit ego mode", "退出自我中心模式") : tGX(language, "Ego mode (hop distance)", "自我中心模式（跳数距离）")}
            </button>
            {egoModeEnabled && selected && (
              <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>{tGX(language, "Hops", "跳数")}</span>
                <input type="range" min={1} max={6} value={egoMaxHops} onChange={e => setEgoMaxHops(+e.target.value)} style={{ flex: 1 }} />
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)" }}>{egoMaxHops}</span>
              </div>
            )}
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
                <div>{tGX(language, "trail", "路径")} — {trailNodes.length ? trailNodes.map(n => n.id).join(" → ") : tGX(language, "none", "无")}</div>
                <div>{tGX(language, "visibility", "可见性")} — {hideUnrelated && selected ? tGX(language, "trail plus top candidates", "路径和少量候选") : tGX(language, "all graph nodes", "全部图节点")}</div>
              </div>
            </div>
          </div>
          </>}

          {leftTab === "ontology" && (
            <div style={{ flex: 1, overflow: "auto" }}>
              <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
                <div className="eyebrow accent">{tGX(language, "Ontology Model Graph", "本体模型图")}</div>
                <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
                  {tGX(language,
                    "Approved ontology artifacts are model nodes. Semantic items are evidence nodes linked by support, mention, target, and governance relations.",
                    "已批准本体是模型节点。semantic items 是证据节点，通过 support、mention、target、governance 等关系连接。")}
                </div>
              </div>
              <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>{tGX(language, "Projection scope", "投影范围")}</div>
                <dl className="kv">
                  <dt>{tGX(language, "Nodes", "节点")}</dt><dd>{ontologyScope.node_count ?? graphWithPositions.nodes.length}</dd>
                  <dt>{tGX(language, "Edges", "边")}</dt><dd>{ontologyScope.edge_count ?? graphWithPositions.edges.length}</dd>
                  <dt>{tGX(language, "Ontology artifacts", "本体 artifact")}</dt><dd>{ontologyScope.ontology_artifact_count ?? "—"}</dd>
                  <dt>{tGX(language, "Semantic items", "语义项")}</dt><dd>{ontologyScope.semantic_item_count ?? "—"}</dd>
                  <dt>{tGX(language, "Projection", "投影")}</dt><dd>{ontologyScope.projection_source || "OntologyModelGraph"}</dd>
                </dl>
              </div>
              <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)" }}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>{tGX(language, "Relation types", "关系类型")}</div>
                <div className="chip-row">
                  {Object.keys(edgeCounts).length === 0 && <Chip count={0}>{tGX(language, "none", "无")}</Chip>}
                  {Object.entries(edgeCounts).map(([kind, count]) => <Chip key={kind} active count={count}>{kind}</Chip>)}
                </div>
              </div>
              <div style={{ padding: "var(--pad-3) var(--pad-4)", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
                {tGX(language,
                  "This graph is read-only. Ontology approval updates the model catalog; formal instance graph writes remain separate.",
                  "该图是只读投影。批准本体会更新模型目录；正式实例图写入仍然独立。")}
              </div>
            </div>
          )}

          {leftTab === "proposed" && (
            <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <ProposedGraphPanel tenantId={tenantId} proposed={proposed} loading={proposedQ.loading} source={proposedQ.source} focusElementKey={focusElementKey} onReviewed={proposedQ.refetch} compact readOnly debugOnly language={language} />
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
            {activeGraphQ.source === "error" || activeGraphQ.source === "loading" ? (
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
                <ApiStatus q={activeGraphQ} what={leftTab === "ontology" ? "ontology model graph" : "graph context"} />
              </div>
            ) : graphWithPositions.nodes.length === 0 ? (
              <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                Empty graph for this scope.
              </div>
            ) : (
            <BigGraph
              data={graphWithPositions}
              selected={selected}
              onSelect={selectGraphNode}
              hoverId={hoverId}
              setHoverId={setHoverId}
              hideUnrelated={hideUnrelated}
              collapseOffTrailEdges={collapseOffTrailEdges}
              trailNodeIds={trailNodeIds}
              trailEdgeKeys={trailEdgeKeys}
              selectedEdgeKey={selectedEdgeKey}
              candidateEdgeKeys={canvasCandidateEdgeKeys}
              candidateEdgeLimit={connectedEdgeLimit}
              showNearbyCandidates={showNearbyCandidates}
              onNodePositionChange={updateNodePosition}
              onSelectEdge={rememberTrailEdge}
              apiRef={cyApiRef}
              egoModeEnabled={egoModeEnabled}
              egoHopDistances={egoHopDistances}
              egoMaxHops={egoMaxHops}
              alwaysShowEdgeLabels={alwaysShowEdgeLabels}
              language={language}
            />
            )}

            <div className="graph-overlay-tl">
              <div className="row">
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "NODES", "节点")}</span><span className="v">{graphWithPositions.nodes.length}</span></div>
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "EDGES", "边")}</span><span className="v">{graphWithPositions.edges.length}</span></div>
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "FOCUS", "聚焦")}</span><span className="v">{selected ? tGX(language, "ON", "开") : tGX(language, "ALL", "全部")}</span></div>
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "TRAIL", "路径")}</span><span className="v">{trailNodes.length}</span></div>
                <div><span style={{ color: "var(--dim)" }}>{tGX(language, "VISIBLE", "可见")}</span><span className="v">{hideUnrelated && selected ? tGX(language, "TRAIL", "路径") : tGX(language, "ALL", "全部")}</span></div>
                {hideUnrelated && selected && <div><span style={{ color: "var(--dim)" }}>{tGX(language, "EDGES", "边")}</span><span className="v">{tGX(language, "LIMITED", "已限制")}</span></div>}
                {hideUnrelated && selected && <div><span style={{ color: "var(--dim)" }}>{tGX(language, "CANVAS", "画布")}</span><span className="v">{canvasCandidateEdges.length}/{connectedEdgesAll.length}</span></div>}
                <div><span style={{ color: "var(--dim)" }}>SOURCE</span><span className="v" style={{ color: activeGraphQ.source === "live" ? GRAPH_ROLE_COLORS_GX.approved : activeGraphQ.source === "live-stale" ? GRAPH_ROLE_COLORS_GX.candidate : GRAPH_ROLE_COLORS_GX.conflict }}>{activeGraphQ.source === "live" ? "LIVE" : activeGraphQ.source === "live-stale" ? "STALE" : activeGraphQ.source === "loading" ? "…" : "NONE"}</span></div>
              </div>
            </div>

            <div className="graph-overlay-tr">
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", justifyContent: "flex-end" }}>
                {legendTypeVisible.map(([k, c]) => (
                  <div key={k} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ width: 8, height: 8, background: c, borderRadius: "50%", display: "inline-block" }} />
                    <span>{k}</span>
                    <span style={{ color: "var(--muted)" }}>{typeNodeCounts[k] || 0}</span>
                  </div>
                ))}
                {legendTypeHiddenCount > 0 && (
                  <div style={{ color: "var(--muted)" }}>+{legendTypeHiddenCount} {tGX(language, "more types", "更多类型")}</div>
                )}
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "flex-end", marginTop: 7, paddingTop: 7, borderTop: "1px solid var(--line-soft)" }}>
                {[
                  [GRAPH_ROLE_COLORS_GX.selected, tGX(language, "selected", "选中")],
                  [GRAPH_ROLE_COLORS_GX.approved, tGX(language, "approved/path", "已审/路径")],
                  [GRAPH_ROLE_COLORS_GX.candidate, tGX(language, "candidate", "候选")],
                  [GRAPH_ROLE_COLORS_GX.conflict, tGX(language, "risk/conflict", "风险/冲突")],
                ].map(([color, label]) => (
                  <div key={label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                    <span style={{ width: 14, height: 3, background: color, display: "inline-block", borderRadius: 2 }} />
                    <span>{label}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="graph-overlay-bl">
              <button className="icon-btn" title={tGX(language, "Zoom in", "放大")} onClick={() => cyApiRef.current?.zoomIn()}>+</button>
              <button className="icon-btn" title={tGX(language, "Zoom out", "缩小")} onClick={() => cyApiRef.current?.zoomOut()}>−</button>
              <button className="icon-btn" title={tGX(language, "Fit view", "适配视图")} onClick={() => cyApiRef.current?.fit()}>⌖</button>
              <button className="icon-btn" title={tGX(language, "Clear trail", "清空路径")} disabled={!selected && !trailNodes.length} onClick={clearGraphTrail}>◎</button>
              <button className="icon-btn" title={tGX(language, "Back one trail step", "路径回退一步")} disabled={trailNodes.length < 2} onClick={stepBackGraphTrail}>↶</button>
              <button
                className="icon-btn"
                title={hideUnrelated ? tGX(language, "Show all nodes", "显示所有节点") : tGX(language, "Hide unrelated to trail", "隐藏路径外节点")}
                disabled={!selected}
                onClick={toggleTrailFocus}>
                {hideUnrelated ? "◉" : "◌"}
              </button>
              <button
                className="icon-btn"
                title={showNearbyCandidates ? tGX(language, "Hide nearby candidates", "隐藏附近候选") : tGX(language, "Show nearby candidates", "显示附近候选")}
                disabled={!selected || !hideUnrelated}
                onClick={() => setShowNearbyCandidates(value => !value)}>
                {showNearbyCandidates ? "·" : "⋯"}
              </button>
              <button
                className="icon-btn"
                title={alwaysShowEdgeLabels ? tGX(language, "Hide edge labels by default", "默认隐藏边标签") : tGX(language, "Always show edge labels", "始终显示边标签")}
                onClick={() => setAlwaysShowEdgeLabels(v => !v)}>
                L
              </button>
              <button
                className="icon-btn"
                title={tGX(language, "Reset layout (clear dragged positions)", "重置布局（清除手动拖拽的位置）")}
                onClick={resetGraphLayoutGX}>
                ↺
              </button>
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
              {leftTab === "ontology"
                ? tGX(language, "Approved ontology model nodes and semantic evidence nodes are visible. Select a node to inspect its catalog key, source evidence, and linked model context.", "当前显示已批准本体模型节点和语义证据节点。选择节点可查看目录键、来源证据以及相连模型上下文。")
                : tGX(language, "All approved tenant graph nodes are visible. Select a node to focus its local context; selected nodes can be dragged to rearrange the canvas.", "当前显示该租户全部已批准图节点。选择节点可聚焦本地上下文；选中节点可拖拽重新布局。")}
            </div>
          ) : (
          <>
          <div className="section">
            <div className="section-head">
              <span>{tGX(language, "Inspector", "检查器")}</span>
              <span className="ct">{selected.type}</span>
              <button type="button" className="btn xs ghost" style={{ marginLeft: 8 }} onClick={exportSelectedNodeJsonGX}>
                {tGX(language, "Export JSON", "导出 JSON")}
              </button>
            </div>
            <div className="section-body">
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div className="eyebrow accent">{selected.type}</div>
                <div style={{ fontSize: 16, color: "var(--text)", fontWeight: 500 }}>{selected.id}</div>
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{labelGX(selected.label, language)}</div>
              </div>
              <div style={{ marginTop: 14 }}>
                {isOntologyModelNodeGX(selected) ? (
                  <>
                    <dl className="kv">
                      <dt>{tGX(language, "Status", "状态")}</dt><dd>{selected._raw?.status || "approved"}</dd>
                      <dt>{tGX(language, "Artifact type", "Artifact 类型")}</dt><dd>{selected._raw?.artifact_type || selected.type || "—"}</dd>
                      <dt>{tGX(language, "Canonical key", "正式键")}</dt><dd>{selected._raw?.canonical_key || selected._raw?.properties?.canonical_key || "—"}</dd>
                      <dt>{tGX(language, "Graph-space key", "图空间键")}</dt><dd>{selected._raw?.graph_space_element_key || selected._raw?.properties?.graph_space_element_key || "—"}</dd>
                      <dt>{tGX(language, "Source", "来源")}</dt><dd>{selected._raw?.source_url || "—"}</dd>
                      <dt>{tGX(language, "Evidence", "证据")}</dt><dd>{selected._raw?.evidence_quote || "—"}</dd>
                      <dt>{tGX(language, "Edges in", "入边")}</dt><dd>{connectedEdgesAll.filter(e => e.t === selected.id).length}</dd>
                      <dt>{tGX(language, "Edges out", "出边")}</dt><dd>{connectedEdgesAll.filter(e => e.s === selected.id).length}</dd>
                    </dl>
                    {ontologyCatalogHrefGX(tenantId, selected) && (
                      <div style={{ marginTop: 10 }}>
                        <a className="btn ghost" href={ontologyCatalogHrefGX(tenantId, selected)}>{tGX(language, "Open in ontology catalog", "在本体目录中打开")}</a>
                      </div>
                    )}
                  </>
                ) : (
                  <dl className="kv">
                    <dt>{tGX(language, "Status", "状态")}</dt><dd>{selectedNodeDetail?.status || selected._raw?.status || "approved"}</dd>
                    <dt>{tGX(language, "Source row", "来源行")}</dt><dd>{selectedNodeDetail?.source_table || selected._raw?.source_table || "source"}#{selectedNodeDetail?.source_pk || selected._raw?.source_pk || selected.id.split(":").slice(1).join(":")}</dd>
                    <dt>{tGX(language, "Edges in", "入边")}</dt><dd>{connectedEdgesAll.filter(e => e.t === selected.id).length}</dd>
                    <dt>{tGX(language, "Edges out", "出边")}</dt><dd>{connectedEdgesAll.filter(e => e.s === selected.id).length}</dd>
                  </dl>
                )}
              </div>
              <NodePropertiesTableGX
                node={selected}
                detail={selectedNodeDetail}
                loading={selectedNodeDetailQ.loading}
                language={language}
              />
              {selected.flag && (
                <div style={{ marginTop: 12, padding: 10, border: "1px solid oklch(0.66 0.18 25 / 0.4)", background: "oklch(0.66 0.18 25 / 0.08)", color: "var(--rejected)", fontFamily: "var(--font-mono)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  {tGX(language, "Flagged · needs review", "已标记 · 需要复核")}
                </div>
              )}
            </div>
          </div>

          <div className="section">
            <div className="section-head">
              <span>{tGX(language, "Trace path", "路径追踪")}</span>
              {pathTraceResult?.found && <span className="ct">{pathTraceResult.nodeIds.length - 1} {tGX(language, "hops", "跳")}</span>}
            </div>
            <div className="section-body">
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  className="input"
                  value={pathTargetQuery}
                  onChange={e => setPathTargetQuery(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") tracePathToTarget(); }}
                  placeholder={tGX(language, "Trace path to node id or label…", "追踪到节点 ID 或标签…")}
                  style={{ flex: 1, minWidth: 0 }}
                />
                <button className="btn ghost" disabled={!pathTargetQuery.trim()} onClick={tracePathToTarget}>{tGX(language, "Trace", "追踪")}</button>
              </div>
              {pathTraceResult && !pathTraceResult.found && (
                <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)" }}>
                  {!pathTraceResult.target
                    ? tGX(language, `No node matches "${pathTraceResult.query}".`, `没有节点匹配 “${pathTraceResult.query}”。`)
                    : tGX(language, `No path to ${labelGX(pathTraceResult.target.id, language)} within the loaded graph. Try increasing depth/limit and reloading.`, `在已加载的图内没有到 ${labelGX(pathTraceResult.target.id, language)} 的路径，可尝试增大深度/上限后重新加载。`)}
                </div>
              )}
              {pathTraceResult?.found && (
                <>
                  <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
                    {pathTraceResult.nodeIds.map((nodeId, index) => {
                      const node = map[nodeId];
                      const edge = index > 0 ? graphWithPositions.edges.find(e => graphEdgeKeyGX(e) === pathTraceResult.edgeKeys[index - 1]) : null;
                      return (
                        <React.Fragment key={`${nodeId}-${index}`}>
                          {edge && (
                            <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                              → {edgeKindLabelGX(edge.kind, language)} →
                            </span>
                          )}
                          <span className="pill" style={{ cursor: node ? "pointer" : "default" }} onClick={() => node && selectGraphNode(node)}>
                            {labelGX(nodeId, language)}
                          </span>
                        </React.Fragment>
                      );
                    })}
                  </div>
                  <button className="btn ghost" style={{ marginTop: 8, width: "100%" }} onClick={clearPathTrace}>{tGX(language, "Clear path trace", "清除路径追踪")}</button>
                </>
              )}
            </div>
          </div>

          <div className="section">
            <div className="section-head">
              <span>{tGX(language, "Connected edges", "相连边")}</span>
              <span className="ct">
                {connectedEdgesVisible.length < connectedEdgesFiltered.length
                  ? `${connectedEdgesVisible.length}/${connectedEdgesFiltered.length}/${connectedEdgesAll.length}`
                  : connectedEdgesAll.length}
              </span>
            </div>
            {connectedEdgesAll.length > 0 && (
              <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--line-soft)", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                {connectedEdgesInCount} {tGX(language, "in", "入")} · {connectedEdgesOutCount} {tGX(language, "out", "出")}
                {topConnectedEdgeKind && ` · ${tGX(language, "top", "主要类型")}: ${edgeKindLabelGX(topConnectedEdgeKind[0], language)} (${topConnectedEdgeKind[1]})`}
              </div>
            )}
            <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "1fr 120px", gap: 8 }}>
              <input
                className="input"
                value={edgeSearch}
                onChange={event => setEdgeSearch(event.target.value)}
                placeholder={tGX(language, "Search edges, endpoints, source rows", "搜索边、端点、来源行")}
                style={{ minWidth: 0 }}
              />
              <select className="select" value={edgeSort} onChange={event => setEdgeSort(event.target.value)}>
                <option value="rank">{tGX(language, "Rank", "排序")}</option>
                <option value="kind">{tGX(language, "Type", "类型")}</option>
                <option value="target">{tGX(language, "Endpoint", "端点")}</option>
              </select>
              {hideUnrelated && selected && (
                <div style={{ gridColumn: "1 / -1", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.4 }}>
                  {tGX(language, "Canvas shows only trail + top candidate edges; use this list to choose another edge.", "画布只展示路径和 top 候选边；从这里选择其它边继续分析。")}
                  {hiddenCanvasEdgeCount > 0 ? ` ${hiddenCanvasEdgeCount} ${tGX(language, "edges are list-only.", "条边仅在列表中。")}` : ""}
                </div>
              )}
            </div>
            {connectedEdgesAll.length > 1000 && (
              <div style={{ padding: "6px 14px", borderBottom: "1px solid var(--line-soft)", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                {tGX(language, "Showing first 1000 connected edges.", "当前展示前 1000 条相连边。")}
              </div>
            )}
            <div className="section-body" style={{ padding: 0, maxHeight: "min(620px, 58vh)", overflowY: "auto", overscrollBehavior: "contain" }}>
              {connectedEdgesVisible.length === 0 && (
                <div style={{ padding: "12px 14px", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                  {edgeSearch ? tGX(language, "No connected edges match the filter.", "没有相连边匹配当前过滤。") : tGX(language, "No connected edges.", "没有相连边。")}
                </div>
              )}
              {connectedEdgesVisible.map((e, i) => {
                const other = e.s === selected.id ? e.t : e.s;
                const dir = e.s === selected.id ? "→" : "←";
                const edgeKey = graphEdgeKeyGX(e);
                const selectedEdge = edgeKey === selectedEdgeKey;
                const listOnly = hideUnrelated && !canvasCandidateEdgeKeys.includes(edgeKey) && !trailEdgeKeys.includes(edgeKey);
                return (
                  <div key={edgeKey || i} style={{ padding: "8px 14px", borderBottom: "1px solid var(--line-soft)", display: "flex", flexDirection: "column", gap: 6, background: selectedEdge ? "var(--accent-bg)" : "transparent" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
                         onClick={() => selectConnectedEdge(e)}>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{edgeKindLabelGX(e.kind, language)}</span>
                      <span style={{ color: "var(--dim)" }}>{dir}</span>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>{labelGX(other, language)}</span>
                      {listOnly && <span className="pill" style={{ marginLeft: "auto", fontSize: 9 }}>{tGX(language, "list-only", "仅列表")}</span>}
                    </div>
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
                <option>{tGX(language, "Find structurally similar nodes", "发现结构相似的节点")}</option>
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

function ProposedGraphPanel({ tenantId, proposed, loading, source, focusElementKey, onReviewed, readOnly = false, debugOnly = false, language }) {
  const [selectedElement, setSelectedElement] = useStateGX(null);
  const [kindFilter, setKindFilter] = useStateGX("all");
  const [selectedKeys, setSelectedKeys] = useStateGX([]);
  const [reviewReason, setReviewReason] = useStateGX("");
  const [reviewBusy, setReviewBusy] = useStateGX(false);
  const [reviewMessage, setReviewMessage] = useStateGX(null);
  const runs = proposed?.runs || [];
  const allElements = proposed?.elements || [];
  const elements = allElements.filter(item => !isOntologyReviewElementGX(item));
  const movedOntologyCount = allElements.length - elements.length;
  const totalCount = elements.length;
  const rawTotalCount = proposed?.raw_total_count ?? totalCount;
  const rawStatusCounts = proposed?.raw_status_counts || {};
  const rawStatusSummary = Object.entries(rawStatusCounts)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([status, count]) => `${statusLabelGraphGX(status, language)} ${count}`)
    .join(" · ");
  const counts = elements.reduce((acc, item) => {
    const kind = knowledgeKindGX(item);
    acc[kind] = (acc[kind] || 0) + 1;
    return acc;
  }, {});
  const primaryKinds = ["object", "relation", "finding"];
  const extraKinds = Object.keys(counts)
    .filter(kind => kind && !primaryKinds.includes(kind))
    .sort((left, right) => left.localeCompare(right));
  const latestRun = runs[0] || null;
  const filteredElements = kindFilter === "all"
    ? elements
    : elements.filter(item => knowledgeKindGX(item) === kindFilter);
  const findings = filteredElements.filter(item => item.element_type === "finding");
  const selectedSet = new Set(selectedKeys);
  const selectedInFilter = filteredElements.filter(item => selectedSet.has(item.element_key));
  const selectedIsReviewed = ["approved", "rejected"].includes(String(selectedElement?.status || "").toLowerCase());
  const reviewUrl = `/?screen=workbench&tenant=${encodeURIComponent(tenantId)}&workspace_tab=workqueue&agent_tab=enrichment`;
  useEffectGX(() => {
    if (!selectedElement) return;
    const latest = elements.find(item => item.element_key === selectedElement.element_key);
    if (latest && latest !== selectedElement) setSelectedElement(latest);
    if (!latest && !selectedIsReviewed) setSelectedElement(filteredElements[0] || null);
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
    setKindFilter(knowledgeKindGX(match) || "all");
    setReviewMessage(null);
  }, [focusElementKey, JSON.stringify(elements.map(item => item.element_key))]);
  function selectKind(nextKind) {
    setKindFilter(nextKind);
    setReviewMessage(null);
    const nextItems = nextKind === "all"
      ? elements
      : elements.filter(item => knowledgeKindGX(item) === nextKind);
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
      setReviewMessage({ kind: "error", text: tGX(language, "Select at least one knowledge candidate.", "请至少选择一个知识候选。") });
      return;
    }
    if ((action === "reject" || action === "needs-evidence") && !reason) {
      setReviewMessage({ kind: "error", text: tGX(language, "Review reason is required for batch reject / needs evidence.", "批量拒绝或要求补证据时必须填写审核原因。") });
      return;
    }
    setReviewBusy(true);
    setReviewMessage(null);
    try {
      const result = await window.AL_API.reviewKnowledgeCandidatesBatch(tenantId, selectedKeys, action, {
        reason,
        reviewer: "Itachi",
      });
      const failed = result?.failed_count || 0;
      const ok = result?.ok_count || 0;
      const failedItems = (result?.results || []).filter(item => !item.ok);
      const selectedResult = (result?.results || []).find(item => item.ok && item.element?.element_key === selectedElement?.element_key);
      if (selectedResult?.element && !["approved", "rejected"].includes(String(selectedResult.element.status || "").toLowerCase())) setSelectedElement(selectedResult.element);
      if (!failed) {
        setSelectedKeys([]);
        setReviewReason("");
        if (selectedResult?.element) {
          const reviewedKey = selectedResult.element.element_key;
          setSelectedElement(filteredElements.find(item => item.element_key !== reviewedKey && !selectedKeys.includes(item.element_key)) || null);
        }
      }
      setReviewMessage({
        kind: failed ? "error" : "ok",
        text: failed
          ? tGX(language, `${ok} recorded, ${failed} failed · ${failedItems.map(item => item.element_key || item.error).slice(0, 2).join(", ")}`, `已记录 ${ok} 条，失败 ${failed} 条 · ${failedItems.map(item => item.element_key || item.error).slice(0, 2).join(", ")}`)
          : tGX(language, `${ok} graph proposal review decisions recorded · formal graph unchanged`, `已记录 ${ok} 条图候选审核决定 · formal graph 未改变`),
      });
      if (onReviewed) await onReviewed();
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
      const result = await window.AL_API.reviewKnowledgeCandidate(tenantId, selectedElement.element_key, action, {
        reason,
        reviewer: "Saskue",
      });
      const reviewedKey = selectedElement.element_key;
      const reviewedStatus = String(result?.element?.status || action || "").toLowerCase();
      if (["approved", "rejected"].includes(reviewedStatus)) {
        setSelectedElement(filteredElements.find(item => item.element_key !== reviewedKey) || null);
      } else if (result?.element) {
        setSelectedElement(result.element);
      }
      setReviewReason("");
      const status = result?.element?.status || action;
      const reviewedClosed = ["approved", "rejected"].includes(reviewedStatus);
      setReviewMessage({
        kind: "ok",
        text: reviewedClosed
          ? tGX(language, `${status} recorded · removed from current pending review · canonical/formal graph unchanged`, `已记录 ${statusLabelGraphGX(status, language)} · 已从当前待审列表移出 · canonical/formal graph 未改变`)
          : tGX(language, `${status} recorded · selected proposal updated · canonical/formal graph unchanged`, `已记录 ${statusLabelGraphGX(status, language)} · 当前候选状态已更新 · canonical/formal graph 未改变`),
      });
      if (onReviewed) await onReviewed();
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
        <span>{debugOnly ? tGX(language, "Raw candidate projection", "原始候选投影") : tGX(language, "Knowledge candidate browser", "知识候选浏览")}</span>
        <span className="ct">
          {loading
            ? tGX(language, "loading", "加载中")
            : `${totalCount} ${tGX(language, "pending", "待处理")} · ${rawTotalCount} ${tGX(language, "total", "总记录")}`}
        </span>
      </div>
      <div className="section-body">
        <div className="chip-row" style={{ marginBottom: 10 }}>
          <Chip active={kindFilter === "all"} onClick={() => selectKind("all")} count={totalCount}>{tGX(language, "all", "全部")}</Chip>
          <Chip active={kindFilter === "object"} onClick={() => selectKind("object")} count={counts.object || 0}>{knowledgeKindLabelGX("object", language)}</Chip>
          <Chip active={kindFilter === "relation"} onClick={() => selectKind("relation")} count={counts.relation || 0}>{knowledgeKindLabelGX("relation", language)}</Chip>
          <Chip active={kindFilter === "finding"} onClick={() => selectKind("finding")} count={counts.finding || 0}>{tGX(language, "findings", "发现")}</Chip>
          {extraKinds.map(kind => (
            <Chip key={kind} active={kindFilter === kind} onClick={() => selectKind(kind)} count={counts[kind] || 0}>{knowledgeKindLabelGX(kind, language)}</Chip>
          ))}
        </div>
        {movedOntologyCount > 0 && (
          <div style={{ marginBottom: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", border: "1px solid var(--line-soft)", padding: 8 }}>
            {tGX(language, "Ontology candidates moved to Ontology review.", "本体候选已移至本体审核。")}{" "}
            <a href={`/?screen=ontology&tenant=${encodeURIComponent(tenantId)}&ontology_tab=discovered`} style={{ color: "var(--accent)" }}>
              {movedOntologyCount} {tGX(language, "items", "项")}
            </a>
          </div>
        )}
        {readOnly ? (
          <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: 8, marginBottom: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
            {debugOnly
              ? tGX(language, "Debug view only: these rows are storage-layer graph projections. Product review belongs to Ontology and Workbench queues.", "仅调试视图：这些记录是存储层图投影。产品审核属于本体和工作台队列。")
              : tGX(language, "Graph space is a read-only browsing and retrieval projection. Review candidate knowledge in the Workbench queue.", "图空间仅作为只读浏览和检索投影。请在工作台队列中审核知识候选。")}{" "}
            <a href={reviewUrl} style={{ color: "var(--accent)" }}>{tGX(language, "Open review queue", "打开审核队列")}</a>
          </div>
        ) : (
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
          {selectedKeys.length > 0 && (
            <textarea
              className="input"
              value={reviewReason}
              onChange={e => setReviewReason(e.target.value)}
              placeholder={tGX(language, "Batch review note; required for reject / needs evidence", "批量审核说明；拒绝或要求补证据时必填")}
              style={{ minHeight: 44, resize: "vertical", marginTop: 8 }}
            />
          )}
          <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
            {tGX(language, "Scope: selected knowledge candidates only · review decision only · graph space remains a browse/search projection.", "范围：仅所选知识候选 · 只记录审核决定 · 图空间保持浏览/检索投影。")}
            {selectedInFilter.length > 0 ? tGX(language, ` Current filter selected: ${selectedInFilter.length}.`, ` 当前过滤结果已选择：${selectedInFilter.length}。`) : ""}
          </div>
          {reviewMessage && (
            <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: reviewMessage.kind === "error" ? GRAPH_ROLE_COLORS_GX.conflict : GRAPH_ROLE_COLORS_GX.approved, overflowWrap: "anywhere" }}>
              {reviewMessage.text}
            </div>
          )}
        </div>
        )}
        {latestRun ? (
          <dl className="kv" style={{ marginBottom: 12 }}>
            <dt>{tGX(language, "Run", "运行")}</dt><dd>{latestRun.run_key}</dd>
            <dt>{tGX(language, "Status", "状态")}</dt><dd>{latestRun.status} · canonical writes disabled</dd>
            <dt>{tGX(language, "Skipped", "跳过")}</dt><dd>{(latestRun.skipped_sources || []).length}</dd>
          </dl>
        ) : (
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
            {rawTotalCount > 0
              ? tGX(language, "No current pending candidates. Historical candidate records exist outside the pending review queue.", "当前没有待处理候选；历史候选记录仍存在，但不在待审队列中。")
              : tGX(language, "No knowledge candidates for this tenant.", "该租户暂无知识候选。")}
            {rawStatusSummary ? ` ${tGX(language, "Status", "状态")}: ${rawStatusSummary}` : ""}
          </div>
        )}
        {findings.map(item => {
          const profile = item.payload?.deep_graph_profile || {};
          return (
            <button key={item.element_key} type="button" onClick={() => { setSelectedElement(item); setReviewMessage(null); }}
                    style={{ border: selectedElement?.element_key === item.element_key ? `1px solid ${GRAPH_ROLE_COLORS_GX.candidate}` : "1px solid var(--line)", borderLeft: `3px solid ${GRAPH_ROLE_COLORS_GX.candidate}`, padding: 10, marginBottom: 10, background: selectedElement?.element_key === item.element_key ? GRAPH_ROLE_COLORS_GX.candidateBg : "var(--bg-2)", width: "100%", textAlign: "left", cursor: "pointer" }}>
              <div className="eyebrow" style={{ color: GRAPH_ROLE_COLORS_GX.candidate }}>{tGX(language, "deep graph finding · draft", "深度图推理发现 · 草稿")}</div>
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
          {tGX(language, "Showing", "显示")} {kindFilter === "all" ? (debugOnly ? tGX(language, "all raw projection rows", "全部原始投影记录") : tGX(language, "all knowledge candidates", "全部知识候选")) : `${knowledgeKindLabelGX(kindFilter, language)} ${debugOnly ? tGX(language, "projection rows", "投影记录") : tGX(language, "candidates", "候选")}`} · {readOnly ? tGX(language, "click an item to inspect.", "点击条目查看详情。") : tGX(language, "click an item to review.", "点击条目进行审核。")}
        </div>
        <div style={{ maxHeight: 220, overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {filteredElements.map(item => {
            const itemColor = graphElementTypeColorGX(item.element_type);
            const statusColor = graphReviewStatusColorGX(item.status);
            const selectedItem = selectedElement?.element_key === item.element_key;
            return (
            <div key={item.element_key} role="button" tabIndex={0}
                 onClick={() => { setSelectedElement(item); setReviewMessage(null); }}
                 onKeyDown={e => { if (e.key === "Enter" || e.key === " ") setSelectedElement(item); }}
                 style={{ border: selectedItem ? `1px solid ${itemColor}` : "1px solid var(--line-soft)", borderLeft: `3px solid ${itemColor}`, padding: 8, background: selectedItem ? GRAPH_ROLE_COLORS_GX.selectedBg : "transparent", textAlign: "left", cursor: "pointer" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: "var(--text)", fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}>
                  {!readOnly && (
                    <input
                      type="checkbox"
                      checked={selectedSet.has(item.element_key)}
                      onClick={e => e.stopPropagation()}
                      onChange={e => toggleSelection(item.element_key, e.target.checked)}
                    />
                  )}
                  {labelGX(item.name, language)}
                </span>
                <span className="ct" style={{ color: itemColor }}>{knowledgeKindLabelGX(knowledgeKindGX(item), language)}</span>
              </div>
              <div style={{ fontFamily: "var(--font-mono)", color: "var(--muted)", fontSize: 10 }}>
                <span style={{ color: statusColor }}>{statusLabelGraphGX(item.status, language)}</span> · {item.source_url || tGX(language, "source unknown", "来源未知")}
              </div>
              {dedupAuditGX(item).dedup_decision && (
                <div style={{ fontFamily: "var(--font-mono)", color: GRAPH_ROLE_COLORS_GX.selected, fontSize: 10, marginTop: 3 }}>
                  {tGX(language, "dedup", "去重")} · {dedupDecisionLabelGX(dedupAuditGX(item).dedup_decision, language)}
                </div>
              )}
            </div>
            );
          })}
          {filteredElements.length === 0 && (
            <div style={{ fontFamily: "var(--font-mono)", color: "var(--muted)", fontSize: 10, border: "1px solid var(--line-soft)", padding: 8 }}>
              {rawTotalCount > 0
                ? (debugOnly ? tGX(language, "No raw projection rows match this filter.", "没有匹配该过滤条件的原始投影记录。") : tGX(language, "No current pending proposals match this filter.", "当前待审队列中没有匹配该过滤条件的候选。"))
                : `${tGX(language, "No", "没有")} ${knowledgeKindLabelGX(kindFilter, language)} ${debugOnly ? tGX(language, "projection rows.", "投影记录。") : tGX(language, "knowledge candidates.", "知识候选。")}`}
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
            onReview={readOnly ? null : reviewElement}
            readOnly={readOnly}
            debugOnly={debugOnly}
            reviewUrl={reviewUrl}
            language={language}
          />
        )}
        {source === "error" && (
          <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--rejected)" }}>
            {tGX(language, "Knowledge candidate API unavailable.", "知识候选 API 不可用。")}
          </div>
        )}
      </div>
    </div>
  );
}

function isOntologyReviewElementGX(item) {
  const type = String(item?.element_type || item?.type || "").toLowerCase();
  if (type === "ontology_concept") return true;
  const artifactType = String((item?.payload || {}).artifact_type || "").toLowerCase();
  return type.includes("ontology") && ["class", "object", "property", "link", "event", "action", "function", "policy"].includes(artifactType);
}

function ProposedGraphDetail({ item, reason, setReason, busy, message, onReview, readOnly = false, debugOnly = false, reviewUrl = "", language }) {
  const payload = item.payload || {};
  const profile = payload.deep_graph_profile || {};
  const reviewEvents = payload.review_events || [];
  const boundary = payload.review_boundary || payload.write_boundary || payload.governance || {};
  const path = profile.path || payload.path || payload.evidence_path || [];
  const pathLabel = profile.path_label || payload.path_label || "";
  const conclusion = payload.conclusion || payload.summary || payload.description || "";
  const dedupAudit = dedupAuditGX(item);
  const matchedKey = dedupAudit.matched_node_key || dedupAudit.matched_edge_key || dedupAudit.matched_element_key || "";
  const nearestProposalMatch = dedupAudit.nearest_proposal_match || payload.nearest_proposal_match;
  const matchEvidence = Array.isArray(dedupAudit.match_evidence) ? dedupAudit.match_evidence : [];
  const conflictFields = Array.isArray(dedupAudit.conflict_fields) ? dedupAudit.conflict_fields : [];
  const endpointEvidence = endpointDedupEvidenceGX(item);
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <div className="eyebrow accent">{debugOnly ? tGX(language, "Inspect projection row", "查看投影记录") : (readOnly ? tGX(language, "Inspect candidate", "查看候选") : tGX(language, "Review selected", "审核选中的"))} {knowledgeKindLabelGX(knowledgeKindGX(item), language)}</div>
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
      {Object.keys(dedupAudit).length > 0 && (
        <div style={{ marginTop: 10, padding: 10, border: "1px solid var(--line-soft)", background: "var(--bg-2)" }}>
          <div className="eyebrow accent" style={{ marginBottom: 8 }}>{tGX(language, "Dedup audit", "去重审计")}</div>
          <dl className="kv" style={{ marginTop: 0 }}>
            <dt>{tGX(language, "Decision", "判定")}</dt><dd>{dedupDecisionLabelGX(dedupAudit.dedup_decision, language)}</dd>
            <dt>{tGX(language, "Candidate", "候选")}</dt><dd>{auditValueGX(dedupAudit.candidate_id)}</dd>
            <dt>{tGX(language, "Task / run / frontier", "任务 / 运行 / frontier")}</dt><dd>{[dedupAudit.task_id, dedupAudit.run_id, dedupAudit.frontier_id].filter(Boolean).join(" · ") || "—"}</dd>
            <dt>{tGX(language, "Matched", "命中")}</dt><dd>{matchedKey || "—"}</dd>
            <dt>{tGX(language, "Score", "分数")}</dt><dd>{dedupAudit.match_score === undefined ? "—" : dedupAudit.match_score}</dd>
            <dt>{tGX(language, "Fingerprints", "指纹")}</dt><dd>{[dedupAudit.source_fingerprint, dedupAudit.evidence_fingerprint].filter(Boolean).join(" · ") || "—"}</dd>
            <dt>{tGX(language, "LLM merge", "LLM 合并")}</dt><dd>{auditValueGX(dedupAudit.llm_merge_decision_allowed)} · {tGX(language, "deterministic review boundary", "确定性审核边界")}</dd>
          </dl>
          {dedupAudit.decision_reason && (
            <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-dim)", overflowWrap: "anywhere" }}>
              {tGX(language, "Reason", "原因")}: {labelGX(dedupAudit.decision_reason, language)}
            </div>
          )}
          {nearestProposalMatch && (
            <div style={{ marginTop: 8 }}>
              <div className="eyebrow" style={{ marginBottom: 5 }}>{tGX(language, "Nearest proposal", "距离最近的候选")}</div>
              <ProposalMatchSummaryGX match={nearestProposalMatch} language={language} />
            </div>
          )}
          {conflictFields.length > 0 && (
            <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)", overflowWrap: "anywhere" }}>
              {tGX(language, "Conflicts require review gate", "冲突字段需要审核入口判定")}: {conflictFields.join(", ")}
            </div>
          )}
          {matchEvidence.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div className="eyebrow" style={{ marginBottom: 5 }}>{tGX(language, "Match evidence", "匹配证据")}</div>
              {matchEvidence.slice(0, 4).map((evidence, index) => (
                <div key={index} style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", borderTop: "1px solid var(--line-soft)", paddingTop: 4, marginTop: 4, overflowWrap: "anywhere" }}>
                  {auditValueGX(evidence)}
                </div>
              ))}
            </div>
          )}
          {endpointEvidence.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="eyebrow" style={{ marginBottom: 5 }}>{tGX(language, "Endpoint dedup evidence", "端点去重证据")}</div>
              {endpointEvidence.map(({ role, data }) => (
                <div key={role} style={{ borderTop: "1px solid var(--line-soft)", paddingTop: 6, marginTop: 6 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: data.review_required ? "var(--changes)" : "var(--accent)", marginBottom: 4 }}>
                    {tGX(language, role === "source" ? "Source endpoint" : "Target endpoint", role === "source" ? "源端点" : "目标端点")} · {dedupDecisionLabelGX(data.dedup_decision, language)}
                    {data.review_required ? ` · ${tGX(language, "needs review", "需要审核")}` : ""}
                  </div>
                  <dl className="kv" style={{ marginTop: 0 }}>
                    <dt>{tGX(language, "Node", "节点")}</dt><dd>{[data.label, data.type].filter(Boolean).join(" · ") || "—"}</dd>
                    <dt>{tGX(language, "Matched", "命中")}</dt><dd>{data.matched_node_key || data.candidate_key || "—"}</dd>
                    <dt>{tGX(language, "Space", "空间")}</dt><dd>{data.matched_space || data.matched_source || "—"}</dd>
                    <dt>{tGX(language, "Score", "分数")}</dt><dd>{data.match_score === undefined ? "—" : data.match_score}</dd>
                    <dt>{tGX(language, "Node write", "本轮节点写入")}</dt><dd>{endpointNodeWriteLabelGX(data, language)}</dd>
                  </dl>
                  {data.nearest_proposal_match && (
                    <ProposalMatchSummaryGX match={data.nearest_proposal_match} language={language} />
                  )}
                  {Array.isArray(data.match_evidence) && data.match_evidence.length > 0 && (
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", overflowWrap: "anywhere" }}>
                      {data.match_evidence.slice(0, 3).map(auditValueGX).join(" · ")}
                    </div>
                  )}
                  {Array.isArray(data.conflict_fields) && data.conflict_fields.length > 0 && (
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)", overflowWrap: "anywhere" }}>
                      {tGX(language, "Conflicts", "冲突")}: {data.conflict_fields.join(", ")}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
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
      {readOnly ? (
        <div style={{ marginTop: 12, border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
          {debugOnly
            ? tGX(language, "This is a raw storage projection inspection surface. Do not approve or reject from Graph; use Ontology for model proposals and Workbench for semantic knowledge.", "这是原始存储投影检查界面。不要在 Graph 中批准或拒绝；模型候选在本体页审核，语义知识在工作台审核。")
            : tGX(language, "This graph view is read-only. Review and promotion decisions belong to the Workbench or Ontology review surface.", "该图视图是只读的。审核和提升决策属于工作台或本体审核界面。")}{" "}
          {reviewUrl && <a href={reviewUrl} style={{ color: "var(--accent)" }}>{tGX(language, "Open review queue", "打开审核队列")}</a>}
        </div>
      ) : (
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
      )}
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

function BigGraph({
  data,
  selected,
  onSelect,
  hoverId,
  setHoverId,
  hideUnrelated,
  collapseOffTrailEdges = true,
  trailNodeIds = [],
  trailEdgeKeys = [],
  selectedEdgeKey = "",
  candidateEdgeKeys = [],
  candidateEdgeLimit = 20,
  showNearbyCandidates = true,
  onNodePositionChange,
  edgeOffsets = {},
  onEdgeOffsetChange,
  onSelectEdge,
  apiRef,
  egoModeEnabled = false,
  egoHopDistances = null,
  egoMaxHops = 2,
  alwaysShowEdgeLabels = false,
  language,
}) {
  const svgRef = useRefGX(null);
  const [dragging, setDragging] = useStateGX(null);
  const [draggingEdge, setDraggingEdge] = useStateGX(null);
  const [panState, setPanState] = useStateGX(null);
  const [viewState, setViewState] = useStateGX({ tx: 0, ty: 0, s: 1 });
  const [expandedEdgeGroupNodeIds, setExpandedEdgeGroupNodeIds] = useStateGX([]);
  const map = Object.fromEntries(data.nodes.map(n => [n.id, n]));
  const graphTypes = Array.from(new Set(data.nodes.map(n => n.type).filter(Boolean)));
  const typeColors = Object.fromEntries(graphTypes.map(t => [t, graphTypeColorGX(t)]));
  const sel = selected ? selected.id : null;
  const trailIds = new Set(trailNodeIds || []);
  if (sel) trailIds.add(sel);
  const focusActive = !!sel || trailIds.size > 0;
  const egoActive = !!(egoModeEnabled && egoHopDistances);
  const activeNeighborIds = new Set();
  const trailNeighborIds = new Set();
  const trailEdgeKeySet = new Set(trailEdgeKeys || []);
  const candidateEdgeKeySet = new Set(candidateEdgeKeys || []);
  data.edges.forEach(e => {
    const edgeKey = graphEdgeKeyGX(e);
    if (sel) {
      if (e.s === sel) activeNeighborIds.add(e.t);
      if (e.t === sel) activeNeighborIds.add(e.s);
    }
    if (trailEdgeKeySet.has(edgeKey)) {
      trailNeighborIds.add(e.t);
      trailNeighborIds.add(e.s);
    } else if (!trailEdgeKeySet.size && trailIds.has(e.s) && trailIds.has(e.t)) {
      trailEdgeKeySet.add(edgeKey);
      trailNeighborIds.add(e.t);
      trailNeighborIds.add(e.s);
    }
    if (candidateEdgeKeySet.has(edgeKey)) {
      trailNeighborIds.add(e.s);
      trailNeighborIds.add(e.t);
    }
  });
  const visibleNodeIds = new Set();
  if (focusActive && hideUnrelated) {
    trailIds.forEach(id => visibleNodeIds.add(id));
    data.edges.forEach(e => {
      const edgeKey = graphEdgeKeyGX(e);
      if (trailEdgeKeySet.has(edgeKey) || (showNearbyCandidates && candidateEdgeKeySet.has(edgeKey))) {
        visibleNodeIds.add(e.s);
        visibleNodeIds.add(e.t);
      }
    });
  }
  const topRelatedEdgeLimit = candidateEdgeLimit || 20;
  const expandedEdgeGroupIds = new Set(expandedEdgeGroupNodeIds.filter(id => trailIds.has(id)));
  const offTrailEdgesByTrailNode = new Map();
  const visibleOffTrailEdgeKeys = new Set();
  if (focusActive && hideUnrelated && collapseOffTrailEdges) {
    data.edges.forEach(e => {
      const edgeKey = graphEdgeKeyGX(e);
      if (trailEdgeKeySet.has(edgeKey)) return;
      [e.s, e.t].forEach(nodeId => {
        if (!trailIds.has(nodeId)) return;
        if (!offTrailEdgesByTrailNode.has(nodeId)) offTrailEdgesByTrailNode.set(nodeId, []);
        offTrailEdgesByTrailNode.get(nodeId).push(e);
      });
    });
    Array.from(trailIds).forEach(nodeId => {
      const shouldShowTopRelated = nodeId === sel || expandedEdgeGroupIds.has(nodeId);
      if (!shouldShowTopRelated) return;
      (offTrailEdgesByTrailNode.get(nodeId) || []).slice(0, topRelatedEdgeLimit).forEach(e => {
        if (candidateEdgeKeySet.size && !candidateEdgeKeySet.has(graphEdgeKeyGX(e))) return;
        visibleOffTrailEdgeKeys.add(graphEdgeKeyGX(e));
      });
    });
  }
  const collapsedEdgeGroups = Array.from(trailIds).filter(nodeId => nodeId === sel).map(nodeId => {
    const node = map[nodeId];
    const edges = offTrailEdgesByTrailNode.get(nodeId) || [];
    if (!node || !edges.length) return null;
    const expanded = nodeId === sel || expandedEdgeGroupIds.has(nodeId);
    const shown = expanded ? Math.min(edges.length, topRelatedEdgeLimit) : 0;
    return {
      nodeId,
      node,
      total: edges.length,
      shown,
      hidden: Math.max(0, edges.length - shown),
      expanded,
    };
  }).filter(Boolean);
  const trailKey = Array.from(trailIds).join("|");
  useEffectGX(() => {
    const activeTrailIds = new Set(trailKey ? trailKey.split("|") : []);
    setExpandedEdgeGroupNodeIds(prev => prev.filter(id => activeTrailIds.has(id)));
  }, [trailKey]);
  const toggleEdgeGroup = (event, nodeId) => {
    event.preventDefault();
    event.stopPropagation();
    setExpandedEdgeGroupNodeIds(prev => (
      prev.includes(nodeId) ? prev.filter(id => id !== nodeId) : [...prev, nodeId]
    ));
  };

  // Returns the event position in the pre-transform node coordinate space
  // (i.e. inverts the pan/zoom <g transform> below, not just the SVG viewBox scaling).
  const eventPoint = (event) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(ctm.inverse());
    return { x: (point.x - viewState.tx) / viewState.s, y: (point.y - viewState.ty) / viewState.s };
  };
  const zoomBy = (factor, pivot) => {
    setViewState(vs => {
      const nextS = Math.max(0.2, Math.min(6, vs.s * factor));
      const ratio = nextS / vs.s;
      const px = pivot ? pivot.x : GRAPH_LAYOUT_CENTER_X_GX;
      const py = pivot ? pivot.y : GRAPH_LAYOUT_CENTER_Y_GX;
      return { s: nextS, tx: px - ratio * (px - vs.tx), ty: py - ratio * (py - vs.ty) };
    });
  };
  // Bounds the fit-to-view camera by a trimmed percentile range rather than raw
  // min/max: a force layout can fling a handful of weakly-connected outliers far
  // from the main mass, and framing on their exact extent would zoom the whole
  // view out to near-invisibility for everyone else. Outliers stay reachable by
  // panning; they just don't dictate the default zoom level.
  const percentileBoundGX = (values) => {
    if (values.length < 20) return { min: Math.min(...values), max: Math.max(...values) };
    const sorted = values.slice().sort((a, b) => a - b);
    return { min: sorted[Math.floor(sorted.length * 0.03)], max: sorted[Math.ceil(sorted.length * 0.97) - 1] };
  };
  const fitToNodes = (targets) => {
    const nodesToFit = (targets && targets.length ? targets : data.nodes).filter(n => Number.isFinite(n.x) && Number.isFinite(n.y));
    if (!nodesToFit.length) { setViewState({ tx: 0, ty: 0, s: 1 }); return; }
    const xBound = percentileBoundGX(nodesToFit.map(n => n.x));
    const yBound = percentileBoundGX(nodesToFit.map(n => n.y));
    const minX = xBound.min - 40, maxX = xBound.max + 40;
    const minY = yBound.min - 40, maxY = yBound.max + 40;
    const w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
    const nextS = Math.max(0.2, Math.min(6, Math.min(1000 / w, 600 / h)));
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    setViewState({ s: nextS, tx: GRAPH_LAYOUT_CENTER_X_GX - nextS * cx, ty: GRAPH_LAYOUT_CENTER_Y_GX - nextS * cy });
  };
  const nodeIdKeyGX = data.nodes.map(n => n.id).join("|");
  useEffectGX(() => {
    fitToNodes(data.nodes);
  }, [nodeIdKeyGX]);
  useEffectGX(() => {
    if (!apiRef) return undefined;
    apiRef.current = {
      zoomIn: () => zoomBy(1.25),
      zoomOut: () => zoomBy(0.8),
      fit: () => fitToNodes(focusActive && hideUnrelated ? data.nodes.filter(n => visibleNodeIds.has(n.id)) : data.nodes),
      resetView: () => setViewState({ tx: 0, ty: 0, s: 1 }),
    };
    return () => { apiRef.current = null; };
  });
  useEffectGX(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const handleWheel = (event) => {
      event.preventDefault();
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(ctm.inverse());
      zoomBy(Math.exp(-event.deltaY * 0.0015), point);
    };
    svg.addEventListener("wheel", handleWheel, { passive: false });
    return () => svg.removeEventListener("wheel", handleWheel);
  }, []);
  const startPan = (event) => {
    if (dragging || draggingEdge) return;
    const svg = svgRef.current;
    const ctm = svg && svg.getScreenCTM();
    if (!ctm) return;
    event.preventDefault();
    setPanState({ pointerId: event.pointerId, lastClientX: event.clientX, lastClientY: event.clientY, scaleX: ctm.a || 1, scaleY: ctm.d || 1 });
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const movePan = (event) => {
    setPanState(prev => {
      if (!prev || prev.pointerId !== event.pointerId) return prev;
      const dx = (event.clientX - prev.lastClientX) / prev.scaleX;
      const dy = (event.clientY - prev.lastClientY) / prev.scaleY;
      setViewState(vs => ({ ...vs, tx: vs.tx + dx, ty: vs.ty + dy }));
      return { ...prev, lastClientX: event.clientX, lastClientY: event.clientY };
    });
  };
  const endPan = (event) => {
    if (panState && panState.pointerId === event.pointerId) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      setPanState(null);
    }
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
  const edgeGeometry = (edge) => {
    const s = map[edge.s], t = map[edge.t];
    if (!s || !t) return null;
    const edgeKey = graphEdgeKeyGX(edge);
    const offset = edgeOffsets[edgeKey] || {};
    const midX = (s.x + t.x) / 2;
    const midY = (s.y + t.y) / 2;
    const cx = Math.max(18, Math.min(982, midX + (offset.dx || 0)));
    const cy = Math.max(18, Math.min(582, midY + (offset.dy || 0)));
    const dragged = !!(offset.dx || offset.dy);
    return {
      edgeKey,
      s,
      t,
      cx,
      cy,
      dragged,
      path: dragged ? `M ${s.x} ${s.y} Q ${cx} ${cy} ${t.x} ${t.y}` : `M ${s.x} ${s.y} L ${t.x} ${t.y}`,
    };
  };
  const startEdgeDrag = (event, edge) => {
    if (!onEdgeOffsetChange) return;
    event.preventDefault();
    event.stopPropagation();
    const edgeKey = graphEdgeKeyGX(edge);
    onSelectEdge?.(edge);
    setDraggingEdge({ key: edgeKey, edge, pointerId: event.pointerId });
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const moveEdgeDrag = (event) => {
    if (!draggingEdge || draggingEdge.pointerId !== event.pointerId) return;
    const point = eventPoint(event);
    if (point) onEdgeOffsetChange?.(draggingEdge.edge, point);
  };
  const endEdgeDrag = (event) => {
    if (draggingEdge && draggingEdge.pointerId === event.pointerId) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
      setDraggingEdge(null);
    }
  };

  return (
    <svg ref={svgRef} viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid meet"
         style={{ width: "100%", height: "100%", display: "block", touchAction: "none", userSelect: "none" }}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill={GRAPH_ROLE_COLORS_GX.edgeDefault} />
        </marker>
        <marker id="arrow-accent" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill={GRAPH_ROLE_COLORS_GX.selected} />
        </marker>
      </defs>

      {/* background: catches pointer events for panning, sits behind the transformed content */}
      <rect x="0" y="0" width="1000" height="600" fill="transparent"
            style={{ cursor: panState ? "grabbing" : "grab" }}
            onPointerDown={startPan}
            onPointerMove={movePan}
            onPointerUp={endPan}
            onPointerCancel={endPan} />

      <g transform={`translate(${viewState.tx} ${viewState.ty}) scale(${viewState.s})`}>

      {/* edges */}
      {data.edges.map((e, i) => {
        const geometry = edgeGeometry(e);
        if (!geometry) return null;
        const { edgeKey, s, t, cx, cy, path, dragged } = geometry;
        const involved = e.s === sel || e.t === sel;
        const inTrail = trailEdgeKeySet.has(edgeKey);
        const inCandidate = showNearbyCandidates && candidateEdgeKeySet.has(edgeKey);
        const inVisibleOffTrail = visibleOffTrailEdgeKeys.has(edgeKey) || inCandidate;
        const isSelectedEdge = selectedEdgeKey && edgeKey === selectedEdgeKey;
        if (focusActive && hideUnrelated && !inTrail && !inCandidate) return null;
        if (focusActive && hideUnrelated && (!visibleNodeIds.has(e.s) || !visibleNodeIds.has(e.t))) return null;
        if (focusActive && hideUnrelated && collapseOffTrailEdges && !inTrail && !inVisibleOffTrail) return null;
        if (egoActive && (!egoHopDistances.has(e.s) || !egoHopDistances.has(e.t))) return null;
        const dimmed = egoActive ? false : (focusActive && !involved && !inTrail && !inCandidate && !(activeNeighborIds.has(e.s) || activeNeighborIds.has(e.t)));
        const emphasized = involved || inTrail || isSelectedEdge;
        const showEdgeLabel = alwaysShowEdgeLabels || emphasized || inVisibleOffTrail || egoActive;
        const semanticEdgeColor = graphEdgeToneGX(e);
        const edgeColor = e.flag ? GRAPH_ROLE_COLORS_GX.conflict : (emphasized ? GRAPH_ROLE_COLORS_GX.selected : inCandidate ? GRAPH_ROLE_COLORS_GX.candidate : e.muted ? "var(--faint)" : semanticEdgeColor);
        const labelColor = e.flag ? GRAPH_ROLE_COLORS_GX.conflict : (emphasized ? GRAPH_ROLE_COLORS_GX.selected : inCandidate ? GRAPH_ROLE_COLORS_GX.candidate : semanticEdgeColor);
        const edgeCursor = draggingEdge?.key === edgeKey ? "grabbing" : "grab";
        return (
          <g key={edgeKey || i} opacity={dimmed ? 0.16 : (hideUnrelated && inCandidate && !emphasized ? 0.78 : 1)}>
            <path d={path}
                  fill="none"
                  stroke={edgeColor}
                  strokeWidth={emphasized ? 2.2 : inCandidate ? 1.35 : 1}
                  strokeDasharray={inTrail && !involved ? "2 2" : e.muted ? "4 3" : ""}
                  markerEnd={emphasized ? "url(#arrow-accent)" : "url(#arrow)"} />
            <path d={path}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={Math.max(12, emphasized ? 16 : 14)}
                  style={{ cursor: edgeCursor }}
                  onPointerDown={(event) => startEdgeDrag(event, e)}
                  onPointerMove={moveEdgeDrag}
                  onPointerUp={endEdgeDrag}
                  onPointerCancel={endEdgeDrag} />
            {showEdgeLabel && (
              <g
                onPointerDown={(event) => startEdgeDrag(event, e)}
                onPointerMove={moveEdgeDrag}
                onPointerUp={endEdgeDrag}
                onPointerCancel={endEdgeDrag}
                style={{ cursor: edgeCursor }}>
              {(dragged || isSelectedEdge) && (
                <circle cx={cx} cy={cy} r="5" fill="var(--bg-1)" stroke={labelColor} strokeWidth="1.4" opacity="0.96" />
              )}
              <text x={cx} y={cy - 6}
                    textAnchor="middle" fontSize="10" fontFamily="var(--font-mono)"
                    fill={labelColor}
                    style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
                {edgeKindLabelGX(e.kind, language)}
              </text>
              </g>
            )}
          </g>
        );
      })}

      {collapsedEdgeGroups.map((group, index) => {
        const x = Math.min(840, Math.max(18, group.node.x + 18));
        const y = Math.min(560, Math.max(18, group.node.y - 34 - (index % 3) * 5));
        const label = group.expanded
          ? (language === "zh" ? `显示 ${group.shown} / 折叠 ${group.hidden}` : `${group.shown} shown / ${group.hidden} collapsed`)
          : (language === "zh" ? `+ ${group.total} 条相关边` : `+ ${group.total} related edges`);
        return (
          <g key={`edge-group-${group.nodeId}`} onClick={(event) => toggleEdgeGroup(event, group.nodeId)}
             onPointerDown={(event) => event.stopPropagation()}
             style={{ cursor: "pointer" }}>
            <rect
              x={x}
              y={y}
              width={language === "zh" ? 130 : 156}
              height="24"
              rx="7"
              fill={group.expanded ? GRAPH_ROLE_COLORS_GX.selectedBg : "var(--bg-1)"}
              stroke={group.expanded ? GRAPH_ROLE_COLORS_GX.selectedLine : GRAPH_ROLE_COLORS_GX.edgeDefault}
              opacity="0.96"
            />
            <text
              x={x + 10}
              y={y + 16}
              fontSize="10"
              fontFamily="var(--font-mono)"
              fill={group.expanded ? GRAPH_ROLE_COLORS_GX.selected : "var(--muted)"}>
              {label}
            </text>
          </g>
        );
      })}

      {/* nodes */}
      {data.nodes.map((n, i) => {
        const isSel = n.id === sel;
        const isTrail = trailIds.has(n.id);
        const isActiveNeighbor = activeNeighborIds.has(n.id);
        const isTrailNeighbor = trailNeighborIds.has(n.id);
        if (focusActive && hideUnrelated && !visibleNodeIds.has(n.id)) return null;
        const isHover = n.id === hoverId;
        const egoHop = egoActive ? egoHopDistances.get(n.id) : undefined;
        const dimmed = egoActive ? egoHop === undefined : (focusActive && !isSel && !isTrail && !isActiveNeighbor && !isTrailNeighbor);
        const egoOpacity = egoActive && egoHop !== undefined ? Math.max(0.35, 1 - (egoHop / (egoMaxHops + 1)) * 0.65) : null;
        const showLabel = isSel || isTrail || isHover || (hideUnrelated && isTrailNeighbor) || (egoActive && egoHop !== undefined);
        const stroke = n.flag ? GRAPH_ROLE_COLORS_GX.conflict : (isSel ? GRAPH_ROLE_COLORS_GX.selected : isTrail ? GRAPH_ROLE_COLORS_GX.approved : isActiveNeighbor ? GRAPH_ROLE_COLORS_GX.candidate : isTrailNeighbor ? GRAPH_ROLE_COLORS_GX.approved : (n.muted ? "var(--faint)" : typeColors[n.type] || "var(--text-dim)"));
        const hasProvenance = !!(n._raw?.source_url || n._raw?.evidence_quote);
        return (
          <g key={i} onPointerDown={(event) => startDrag(event, n)}
                 onPointerMove={moveDrag}
                 onPointerUp={endDrag}
                 onPointerCancel={endDrag}
                 onMouseEnter={() => setHoverId(n.id)}
                 onMouseLeave={() => setHoverId(null)}
                 opacity={egoOpacity !== null ? egoOpacity : (dimmed ? 0.24 : 1)}
                 style={{ cursor: dragging?.id === n.id ? "grabbing" : "grab", transition: "opacity 120ms ease" }}>
            {(isSel || isHover || isTrail) && (
              <circle cx={n.x} cy={n.y} r={n.r + 10} fill={isTrail && !isSel ? GRAPH_ROLE_COLORS_GX.approvedBg : GRAPH_ROLE_COLORS_GX.selectedBg} stroke={isTrail && !isSel ? GRAPH_ROLE_COLORS_GX.approvedLine : GRAPH_ROLE_COLORS_GX.selectedLine} strokeWidth="1" />
            )}
            <circle cx={n.x} cy={n.y} r={n.r}
                    fill={isSel ? GRAPH_ROLE_COLORS_GX.selected : "var(--bg-2)"}
                    stroke={stroke} strokeWidth={isSel ? 2 : isTrail ? 1.8 : 1.4} />
            {isSel && <circle cx={n.x} cy={n.y} r={n.r - 7} fill="var(--bg-1)" />}
            {hasProvenance && (
              <circle cx={n.x + n.r * 0.72} cy={n.y - n.r * 0.72} r={2.6}
                      fill="var(--accent)" stroke="var(--bg-1)" strokeWidth="0.8" />
            )}
            {showLabel && (
              <>
                <text x={n.x} y={n.y + n.r + 14}
                      textAnchor="middle"
                      fontSize="11" fontFamily="var(--font-mono)"
                      fill={isSel ? GRAPH_ROLE_COLORS_GX.selected : stroke}
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
      </g>
    </svg>
  );
}

Object.assign(window, { GraphExplorer, BigGraph, ProposedGraphPanel, ProposedGraphDetail });
