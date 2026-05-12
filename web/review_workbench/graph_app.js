const params = new URLSearchParams(window.location.search);

const state = {
  tenant: params.get("tenant") || "default",
  tenants: [],
  graph: null,
  baseGraph: null,
  selectedNodeId: params.get("node") || null,
  selectedEdgeId: params.get("edge") || null,
  selectedKind: null,
  selectedDetail: null,
  transform: { x: 0, y: 0, scale: 1 },
  drag: null,
  expandedNodeIds: new Set(),
  expandHistory: [],
};

const els = {
  tenantSwitcher: document.querySelector("#tenant-switcher"),
  tenantNamespace: document.querySelector("#tenant-namespace"),
  tenantGraph: document.querySelector("#tenant-graph"),
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  navWorkbench: document.querySelector("#nav-workbench"),
  navInstances: document.querySelector("#nav-instances"),
  navGraph: document.querySelector("#nav-graph"),
  navReasoning: document.querySelector("#nav-reasoning"),
  navSettings: document.querySelector("#nav-settings"),
  breadcrumb: document.querySelector("#breadcrumb"),
  reloadGraph: document.querySelector("#reload-graph"),
  centerType: document.querySelector("#center-type"),
  centerId: document.querySelector("#center-id"),
  depth: document.querySelector("#graph-depth"),
  limit: document.querySelector("#graph-limit"),
  loadGraph: document.querySelector("#load-graph"),
  scopeCenter: document.querySelector("#scope-center"),
  scopeNodes: document.querySelector("#scope-nodes"),
  scopeEdges: document.querySelector("#scope-edges"),
  scopeLimit: document.querySelector("#scope-limit"),
  expandHistory: document.querySelector("#expand-history"),
  graphTitle: document.querySelector("#graph-title"),
  graphSubtitle: document.querySelector("#graph-subtitle"),
  graphStatus: document.querySelector("#graph-status"),
  graphWarning: document.querySelector("#graph-warning"),
  canvas: document.querySelector("#graph-canvas"),
  svg: document.querySelector("#graph-svg"),
  zoomIn: document.querySelector("#zoom-in"),
  zoomOut: document.querySelector("#zoom-out"),
  fitView: document.querySelector("#fit-view"),
  focusSelected: document.querySelector("#focus-selected"),
  expandSelected: document.querySelector("#expand-selected"),
  collapseExpanded: document.querySelector("#collapse-expanded"),
  inspectorKind: document.querySelector("#inspector-kind"),
  inspectorTitle: document.querySelector("#inspector-title"),
  inspectorBody: document.querySelector("#inspector-body"),
  reasoningQuestion: document.querySelector("#reasoning-question"),
  startReasoning: document.querySelector("#start-reasoning"),
  reasoningResult: document.querySelector("#reasoning-result"),
  reasoningStatus: document.querySelector("#reasoning-status"),
  toast: document.querySelector("#toast"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function json(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("visible");
  window.setTimeout(() => els.toast.classList.remove("visible"), 3200);
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function urlWithTenant(path, query = {}) {
  const next = new URLSearchParams(query);
  next.set("tenant", state.tenant);
  return `${path}?${next.toString()}`;
}

async function loadTenants() {
  const data = await fetchJson(urlWithTenant("/api/tenants"));
  state.tenants = data.tenants || [];
  const current = data.current || state.tenants.find((tenant) => tenant.tenant_id === state.tenant);
  state.tenant = current?.tenant_id || data.default_tenant_id || state.tenant;
  els.tenantSwitcher.innerHTML = state.tenants
    .map(
      (tenant) =>
        `<option value="${escapeHtml(tenant.tenant_id)}">${escapeHtml(tenant.display_name)} / ${escapeHtml(tenant.namespace)}</option>`,
    )
    .join("");
  els.tenantSwitcher.value = state.tenant;
  if (current) {
    els.tenantNamespace.textContent = current.namespace;
    els.tenantGraph.textContent = current.graph_database;
    els.shellTenantLabel.textContent = current.display_name;
    els.shellTenantMeta.textContent = `namespace ${current.namespace} · graph ${current.graph_database}`;
  }
  els.navWorkbench.href = `/?tenant=${encodeURIComponent(state.tenant)}`;
  els.navInstances.href = `/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee&id=4`;
  els.navGraph.href = `/graph.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee&id=4&depth=1`;
  els.navReasoning.href = `/reasoning.html?tenant=${encodeURIComponent(state.tenant)}`;
  els.navSettings.href = `/settings.html?tenant=${encodeURIComponent(state.tenant)}`;
}

async function loadGraph({ preserveSelection = false } = {}) {
  els.graphStatus.textContent = "loading";
  els.graphStatus.className = "status-pill muted-pill";
  const graph = await fetchJson(
    urlWithTenant("/api/graph/context", {
      type: els.centerType.value || "Employee",
      id: els.centerId.value || "4",
      depth: els.depth.value || "1",
      limit: els.limit.value || "200",
    }),
  );
  state.graph = graph;
  state.baseGraph = structuredClone(graph);
  state.expandedNodeIds = new Set();
  state.expandHistory = [];
  if (!preserveSelection) {
    state.selectedNodeId = graph.center?.id || null;
    state.selectedEdgeId = null;
    state.selectedKind = state.selectedNodeId ? "node" : null;
  }
  if (graph.approved === false) {
    renderGraph();
    renderScope();
    updateUrl();
    renderBlocked(graph);
    return;
  }
  renderGraph();
  renderScope();
  updateUrl();
  els.graphWarning.classList.add("hidden");
  els.graphTitle.textContent = graph.center?.label || "Approved graph";
  els.graphSubtitle.textContent = `Depth ${graph.depth}; ${graph.relations_summary?.returned_orders || 0} of ${graph.relations_summary?.handled_orders || 0} orders returned.`;
  els.graphStatus.textContent = `${graph.nodes.length} nodes / ${graph.edges.length} edges`;
  els.graphStatus.className = "status-pill status-approved";
  if (state.selectedEdgeId) {
    await selectEdge(state.selectedEdgeId);
  } else if (state.selectedNodeId) {
    await selectNode(state.selectedNodeId);
  }
}

function renderBlocked(graph) {
  els.svg.innerHTML = "";
  state.selectedNodeId = null;
  state.selectedEdgeId = null;
  state.selectedKind = null;
  state.selectedDetail = null;
  els.graphWarning.classList.remove("hidden");
  els.graphWarning.textContent = `Graph blocked by approved-only gate. Missing artifacts: ${(graph.missing_approved_artifacts || []).join(", ")}`;
  els.graphStatus.textContent = "blocked";
  els.graphStatus.className = "status-pill status-rejected";
  els.inspectorKind.textContent = "Blocked";
  els.inspectorTitle.textContent = "Approved-only gate";
  els.inspectorBody.innerHTML = `<section class="detail-section"><h3>Missing approved artifacts</h3><pre class="code-block">${escapeHtml(json(graph.missing_approved_artifacts || []))}</pre></section>`;
}

function renderScope() {
  const graph = state.graph || {};
  els.scopeCenter.textContent = graph.center?.id || "-";
  els.scopeNodes.textContent = String((graph.nodes || []).length);
  els.scopeEdges.textContent = String((graph.edges || []).length);
  const limits = graph.limits || {};
  els.scopeLimit.textContent = `${limits.applied_limit || graph.limit || "-"} / hard ${limits.hard_limit || 300}${limits.truncated ? " · truncated" : ""}`;
  els.expandHistory.innerHTML =
    state.expandHistory.length === 0
      ? '<p class="muted">No expansions yet.</p>'
      : state.expandHistory
          .map(
            (item) =>
              `<section class="review-item"><strong>${escapeHtml(item.node)}</strong><span class="muted">${escapeHtml(item.summary)}</span></section>`,
          )
          .join("");
  els.breadcrumb.textContent = `Graph / ${graph.center?.id || "Employee:4"}`;
}

function layoutGraph(graph) {
  const nodes = graph.nodes || [];
  if (!nodes.length || !graph.center) return new Map();
  const centerId = graph.center?.id || nodes[0]?.id;
  const orders = nodes.filter((node) => node.id !== centerId);
  const positioned = new Map();
  const center = nodes.find((node) => node.id === centerId) || graph.center;
  if (!center?.id) return positioned;
  positioned.set(centerId, { ...center, x: 0, y: 0 });
  const radius = Math.max(260, Math.min(720, orders.length * 5));
  orders.forEach((node, index) => {
    const ring = index < 80 ? 1 : 2;
    const ringItems = ring === 1 ? Math.min(80, orders.length) : orders.length - 80;
    const ringIndex = ring === 1 ? index : index - 80;
    const angle = (ringIndex / Math.max(1, ringItems)) * Math.PI * 2;
    const nodeRadius = ring === 1 ? radius : radius + 220;
    positioned.set(node.id, { ...node, x: Math.cos(angle) * nodeRadius, y: Math.sin(angle) * nodeRadius });
  });
  return positioned;
}

function renderGraph() {
  const graph = state.graph;
  if (!graph) return;
  const positions = layoutGraph(graph);
  const selectedEdge = state.selectedEdgeId ? graphEdge(state.selectedEdgeId) : null;
  const orderedEdges = [
    ...(graph.edges || []).filter((edge) => edge.id !== state.selectedEdgeId),
    ...(graph.edges || []).filter((edge) => edge.id === state.selectedEdgeId),
  ];
  const edgeMarkup = orderedEdges
    .map((edge) => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) return "";
      const selected = state.selectedEdgeId === edge.id ? " selected" : "";
      const adjacent = state.selectedNodeId && (edge.source === state.selectedNodeId || edge.target === state.selectedNodeId) ? " adjacent" : "";
      return `
        <g class="graph-edge-group${selected}${adjacent}" data-edge="${escapeHtml(edge.id)}">
          <line class="graph-edge-hit" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"></line>
          <line class="graph-edge${selected}${adjacent}" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"></line>
        </g>
      `;
    })
    .join("");
  const nodeMarkup = [...positions.values()]
    .map((node) => {
      const selected = state.selectedNodeId === node.id ? " selected" : "";
      const center = graph.center?.id === node.id ? " center" : "";
      const expanded = state.expandedNodeIds.has(node.id) ? " expanded" : "";
      const endpoint = selectedEdge && (selectedEdge.source === node.id || selectedEdge.target === node.id) ? " endpoint" : "";
      const label = node.type === "Order" ? node.id.replace("Order:", "#") : node.label;
      return `
        <g class="graph-node-svg${selected}${center}${expanded}${endpoint}" data-node="${escapeHtml(node.id)}" transform="translate(${node.x}, ${node.y})">
          <circle class="graph-node-hit" r="${node.type === "Employee" ? 46 : 28}"></circle>
          <circle r="${node.type === "Employee" ? 34 : 18}"></circle>
          <text y="${node.type === "Employee" ? 54 : 34}" text-anchor="middle">${escapeHtml(label)}</text>
        </g>
      `;
    })
    .join("");
  els.svg.innerHTML = `
    <g id="graph-viewport" transform="translate(${state.transform.x}, ${state.transform.y}) scale(${state.transform.scale})">
      ${edgeMarkup}
      ${nodeMarkup}
    </g>
  `;
  els.svg.querySelectorAll("[data-node]").forEach((item) => {
    item.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
    });
    item.addEventListener("click", (event) => {
      event.stopPropagation();
      selectNode(item.dataset.node).catch((error) => showToast(error.message));
    });
  });
  els.svg.querySelectorAll("[data-edge]").forEach((item) => {
    item.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
    });
    item.addEventListener("click", (event) => {
      event.stopPropagation();
      selectEdge(item.dataset.edge).catch((error) => showToast(error.message));
    });
  });
}

function updateTransform() {
  const viewport = document.querySelector("#graph-viewport");
  if (viewport) viewport.setAttribute("transform", `translate(${state.transform.x}, ${state.transform.y}) scale(${state.transform.scale})`);
}

function graphNode(nodeId) {
  return (state.graph?.nodes || []).find((node) => node.id === nodeId);
}

function graphEdge(edgeId) {
  return (state.graph?.edges || []).find((edge) => edge.id === edgeId);
}

async function selectNode(nodeId) {
  const node = graphNode(nodeId);
  if (!node) return;
  state.selectedNodeId = nodeId;
  state.selectedEdgeId = null;
  state.selectedKind = "node";
  updateUrl();
  renderGraph();
  const data = await fetchJson(urlWithTenant(`/api/graph/node/${encodeURIComponent(nodeId)}`));
  state.selectedDetail = data.node;
  renderNodeInspector(data.node);
  els.graphStatus.textContent = `selected node ${nodeId}`;
  els.graphStatus.className = "status-pill status-approved";
}

async function selectEdge(edgeId) {
  const edge = graphEdge(edgeId);
  if (!edge) return;
  state.selectedNodeId = null;
  state.selectedEdgeId = edgeId;
  state.selectedKind = "edge";
  updateUrl();
  renderGraph();
  const data = await fetchJson(urlWithTenant(`/api/graph/edge/${encodeURIComponent(edgeId)}`));
  state.selectedDetail = data.edge;
  renderEdgeInspector(data.edge);
  els.graphStatus.textContent = `selected edge ${edgeId}`;
  els.graphStatus.className = "status-pill status-approved";
}

function renderNodeInspector(node) {
  els.inspectorKind.textContent = `${node.type} node`;
  els.inspectorTitle.textContent = node.label;
  const summary = node.neighborhood_summary || node.relations_summary || {};
  els.inspectorBody.innerHTML = `
    <section class="detail-section">
      <h3>Identity</h3>
      <dl class="provenance-grid">
        <div><dt>Node key</dt><dd>${escapeHtml(node.id)}</dd></div>
        <div><dt>Tenant</dt><dd>${escapeHtml(node.tenant_id)}</dd></div>
        <div><dt>Source</dt><dd>${escapeHtml(node.source_table)} · ${escapeHtml(node.source_pk)}</dd></div>
        <div><dt>Ontology</dt><dd>${escapeHtml(node.ontology_artifact)} · approved</dd></div>
      </dl>
    </section>
    <section class="detail-section">
      <h3>Neighborhood summary</h3>
      <pre class="code-block">${escapeHtml(json(summary))}</pre>
    </section>
    <section class="detail-section">
      <h3>Properties</h3>
      <pre class="code-block">${escapeHtml(json(node.key_properties))}</pre>
    </section>
    <section class="detail-section">
      <h3>Evidence links</h3>
      <a class="panel-link" href="/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=${encodeURIComponent(node.type)}&id=${encodeURIComponent(node.id.split(":")[1])}&node=${encodeURIComponent(node.id)}">Open in Instance Explorer</a>
      <a class="panel-link" href="/?tenant=${encodeURIComponent(state.tenant)}&artifact=${encodeURIComponent(node.ontology_artifact)}">Open ontology artifact</a>
    </section>
    <section class="detail-section">
      <h3>Source row</h3>
      <pre class="code-block">${escapeHtml(json(node.source_row))}</pre>
    </section>
  `;
  els.reasoningResult.textContent = "Ready to create a scoped draft reasoning task from this node.";
}

function renderEdgeInspector(edge) {
  els.inspectorKind.textContent = "Graph edge";
  els.inspectorTitle.textContent = edge.id;
  els.inspectorBody.innerHTML = `
    <section class="detail-section">
      <h3>Relationship</h3>
      <dl class="provenance-grid">
        <div><dt>Source</dt><dd>${escapeHtml(edge.source)}</dd></div>
        <div><dt>Target</dt><dd>${escapeHtml(edge.target)}</dd></div>
        <div><dt>Label</dt><dd>${escapeHtml(edge.label)}</dd></div>
        <div><dt>Join</dt><dd>${escapeHtml(edge.join_condition)}</dd></div>
      </dl>
    </section>
    <section class="detail-section">
      <h3>Provenance</h3>
      <dl class="provenance-grid">
        <div><dt>Ontology link</dt><dd>${escapeHtml(edge.ontology_link)}</dd></div>
        <div><dt>Artifact status</dt><dd>${escapeHtml(edge.artifact_status)} · v${escapeHtml(edge.artifact_version)}</dd></div>
        <div><dt>Source field</dt><dd>${escapeHtml(edge.source_field)}</dd></div>
        <div><dt>Graph database</dt><dd>${escapeHtml(edge.graph_database)}</dd></div>
      </dl>
      <p class="muted">${escapeHtml(edge.evidence)}</p>
    </section>
    <section class="detail-section">
      <h3>Evidence links</h3>
      <a class="panel-link" href="/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee&id=${encodeURIComponent(edge.source.split(":")[1])}&edgeSource=${encodeURIComponent(edge.source)}&edgeTarget=${encodeURIComponent(edge.target)}">Open source edge in Instance Explorer</a>
      <a class="panel-link" href="/?tenant=${encodeURIComponent(state.tenant)}&artifact=${encodeURIComponent(edge.ontology_link)}">Open link artifact</a>
    </section>
    <section class="detail-section">
      <h3>Source row</h3>
      <pre class="code-block">${escapeHtml(json(edge.source_row))}</pre>
    </section>
    <section class="detail-section">
      <h3>Target row</h3>
      <pre class="code-block">${escapeHtml(json(edge.target_row))}</pre>
    </section>
  `;
  els.reasoningResult.textContent = "Ready to create a scoped draft reasoning task from this edge.";
}

async function expandSelected() {
  if (!state.selectedNodeId) {
    showToast("Select a node before expanding");
    return;
  }
  const expanded = await fetchJson(urlWithTenant("/api/graph/expand"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      node_key: state.selectedNodeId,
      depth: els.depth.value || "1",
      limit: els.limit.value || "200",
    }),
  });
  if (expanded.approved === false) {
    renderBlocked(expanded);
    return;
  }
  const beforeNodes = new Set((state.graph.nodes || []).map((node) => node.id));
  const beforeEdges = new Set((state.graph.edges || []).map((edge) => edge.id));
  state.graph.nodes = [...state.graph.nodes, ...expanded.nodes.filter((node) => !beforeNodes.has(node.id))];
  state.graph.edges = [...state.graph.edges, ...expanded.edges.filter((edge) => !beforeEdges.has(edge.id))];
  state.expandedNodeIds.add(state.selectedNodeId);
  state.expandHistory.unshift({
    node: state.selectedNodeId,
    summary: `${expanded.nodes.length} nodes / ${expanded.edges.length} edges returned within approved scope`,
  });
  renderGraph();
  renderScope();
  showToast(`Expanded ${state.selectedNodeId}`);
}

function collapseExpanded() {
  if (!state.baseGraph) return;
  const selectedNodeId = state.selectedNodeId;
  const selectedEdgeId = state.selectedEdgeId;
  state.graph = structuredClone(state.baseGraph);
  state.expandedNodeIds = new Set();
  state.expandHistory = [];
  if (selectedNodeId && graphNode(selectedNodeId)) {
    selectNode(selectedNodeId).catch((error) => showToast(error.message));
  } else if (selectedEdgeId && graphEdge(selectedEdgeId)) {
    selectEdge(selectedEdgeId).catch((error) => showToast(error.message));
  } else {
    state.selectedNodeId = state.graph.center?.id || null;
    state.selectedEdgeId = null;
  }
  renderGraph();
  renderScope();
}

function focusSelected() {
  if (!state.selectedNodeId) {
    showToast("Select an Employee node to focus");
    return;
  }
  const node = graphNode(state.selectedNodeId);
  if (!node || node.type !== "Employee") {
    showToast("First version can focus Employee nodes only");
    return;
  }
  els.centerType.value = node.type;
  els.centerId.value = node.id.split(":")[1];
  loadGraph().catch((error) => showToast(error.message));
}

function fitView() {
  state.transform = { x: els.canvas.clientWidth / 2, y: els.canvas.clientHeight / 2, scale: 0.42 };
  updateTransform();
}

function zoomBy(multiplier) {
  state.transform.scale = Math.max(0.15, Math.min(2.5, state.transform.scale * multiplier));
  updateTransform();
}

function updateUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  url.searchParams.set("type", els.centerType.value || "Employee");
  url.searchParams.set("id", els.centerId.value || "4");
  url.searchParams.set("depth", els.depth.value || "1");
  url.searchParams.set("limit", els.limit.value || "200");
  url.searchParams.delete("node");
  url.searchParams.delete("edge");
  if (state.selectedNodeId) url.searchParams.set("node", state.selectedNodeId);
  if (state.selectedEdgeId) url.searchParams.set("edge", state.selectedEdgeId);
  window.history.replaceState({}, "", url);
}

function evidencePaths() {
  const graphUrl = `${window.location.pathname}${window.location.search}`;
  if (state.selectedKind === "edge" && state.selectedDetail) {
    return [
      {
        kind: "graph_edge",
        label: state.selectedDetail.id,
        summary: state.selectedDetail.evidence,
        url: graphUrl,
        source_ref: state.selectedDetail.source_field,
        payload: { edge_id: state.selectedDetail.id, ontology_link: state.selectedDetail.ontology_link },
      },
    ];
  }
  if (state.selectedDetail) {
    return [
      {
        kind: "graph_node",
        label: state.selectedDetail.id,
        summary: state.selectedDetail.label,
        url: graphUrl,
        source_ref: state.selectedDetail.source_pk,
        payload: { node_id: state.selectedDetail.id, ontology_artifact: state.selectedDetail.ontology_artifact },
      },
    ];
  }
  return [];
}

async function createReasoningTask() {
  if (!state.selectedKind || !state.selectedDetail) {
    showToast("Select a node or edge first");
    return;
  }
  const centerEdge =
    state.selectedKind === "edge"
      ? { id: state.selectedDetail.id, source: state.selectedDetail.source, target: state.selectedDetail.target }
      : null;
  const payload = {
    question: els.reasoningQuestion.value,
    graph_url: `${window.location.pathname}${window.location.search}`,
    scope: {
      center_node: state.selectedKind === "node" ? state.selectedDetail.id : undefined,
      center_edge: centerEdge,
      depth: Number(els.depth.value || 1),
      node_limit: Number(els.limit.value || 200),
      edge_limit: Number(els.limit.value || 200),
      allowed_node_types: ["Employee", "Order"],
      allowed_link_keys: ["link:employee:1:n:order"],
      approved_only: true,
      evidence_paths: evidencePaths(),
    },
  };
  els.reasoningStatus.textContent = "creating";
  const data = await fetchJson(urlWithTenant("/api/reasoning/tasks/from-graph"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  els.reasoningStatus.textContent = "draft-only";
  els.reasoningResult.innerHTML = `<a class="panel-link" href="${escapeHtml(data.reasoning_url)}">Open scoped reasoning task</a>`;
  showToast("Scoped reasoning task created");
}

function bindCanvas() {
  els.svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.12 : 0.88);
  });
  els.svg.addEventListener(
    "pointerdown",
    (event) => {
      const nodeEl = event.target.closest?.("[data-node]");
      const edgeEl = event.target.closest?.("[data-edge]");
      if (!nodeEl && !edgeEl) return;
      event.preventDefault();
      event.stopPropagation();
      state.drag = null;
      const selection = nodeEl
        ? selectNode(nodeEl.dataset.node)
        : selectEdge(edgeEl.dataset.edge);
      selection.catch((error) => showToast(error.message));
    },
    true,
  );
  els.svg.addEventListener("pointerdown", (event) => {
    const nodeEl = event.target.closest?.("[data-node]");
    const edgeEl = event.target.closest?.("[data-edge]");
    if (nodeEl || edgeEl) {
      event.stopPropagation();
      state.drag = null;
      const selection = nodeEl
        ? selectNode(nodeEl.dataset.node)
        : selectEdge(edgeEl.dataset.edge);
      selection.catch((error) => showToast(error.message));
      return;
    }
    state.drag = { x: event.clientX, y: event.clientY, startX: state.transform.x, startY: state.transform.y };
    els.svg.setPointerCapture(event.pointerId);
  });
  els.svg.addEventListener("pointermove", (event) => {
    if (!state.drag) return;
    state.transform.x = state.drag.startX + event.clientX - state.drag.x;
    state.transform.y = state.drag.startY + event.clientY - state.drag.y;
    updateTransform();
  });
  els.svg.addEventListener("pointerup", () => {
    state.drag = null;
  });
}

els.tenantSwitcher.addEventListener("change", async () => {
  state.tenant = els.tenantSwitcher.value;
  updateUrl();
  await loadTenants();
  await loadGraph();
});
els.reloadGraph.addEventListener("click", () => loadGraph().catch((error) => showToast(error.message)));
els.loadGraph.addEventListener("click", () => loadGraph().catch((error) => showToast(error.message)));
els.zoomIn.addEventListener("click", () => zoomBy(1.2));
els.zoomOut.addEventListener("click", () => zoomBy(0.8));
els.fitView.addEventListener("click", fitView);
els.focusSelected.addEventListener("click", focusSelected);
els.expandSelected.addEventListener("click", () => expandSelected().catch((error) => showToast(error.message)));
els.collapseExpanded.addEventListener("click", collapseExpanded);
els.startReasoning.addEventListener("click", () => createReasoningTask().catch((error) => showToast(error.message)));

if (params.get("type")) els.centerType.value = params.get("type");
if (params.get("id")) els.centerId.value = params.get("id");
if (params.get("depth")) els.depth.value = params.get("depth");
if (params.get("limit")) els.limit.value = params.get("limit");

bindCanvas();
loadTenants()
  .then(() => {
    fitView();
    return loadGraph({ preserveSelection: Boolean(state.selectedNodeId || state.selectedEdgeId) });
  })
  .catch((error) => showToast(error.message));
