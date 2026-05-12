const state = {
  results: [],
  graph: null,
  selected: null,
  tenant: new URLSearchParams(window.location.search).get("tenant") || "default",
  tenants: [],
};

const els = {
  type: document.querySelector("#instance-type"),
  query: document.querySelector("#instance-query"),
  searchButton: document.querySelector("#instance-search-button"),
  tenantSwitcher: document.querySelector("#tenant-switcher"),
  tenantNamespace: document.querySelector("#tenant-namespace"),
  tenantGraph: document.querySelector("#tenant-graph"),
  results: document.querySelector("#instance-results"),
  graphTitle: document.querySelector("#graph-title"),
  graphSubtitle: document.querySelector("#graph-subtitle"),
  graphCount: document.querySelector("#graph-count"),
  approvedWarning: document.querySelector("#approved-warning"),
  graphArea: document.querySelector("#graph-area"),
  detailKind: document.querySelector("#detail-kind"),
  detailTitle: document.querySelector("#detail-title"),
  detailBody: document.querySelector("#detail-body"),
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

async function fetchJson(url) {
  const response = await fetch(url);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function urlWithTenant(path, params = {}) {
  const query = new URLSearchParams(params);
  query.set("tenant", state.tenant);
  return `${path}?${query.toString()}`;
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
  }
}

async function searchInstances() {
  const type = els.type.value || "Employee";
  const query = els.query.value.trim();
  const data = await fetchJson(urlWithTenant("/api/instances/search", { type, q: query }));
  state.results = data.instances || [];
  renderResults(data);
  if (state.results.length > 0) {
    const first = state.results[0];
    const id = first.id.split(":")[1];
    await loadNeighborhood(first.type, id);
  }
}

function renderResults(data) {
  if (data.approved === false) {
    els.results.innerHTML = `<section class="empty-state">${escapeHtml(data.reason)}</section>`;
    return;
  }
  if (state.results.length === 0) {
    els.results.innerHTML = '<section class="empty-state">No instances found.</section>';
    return;
  }
  els.results.innerHTML = state.results
    .map(
      (item) => `
        <button class="artifact-item" type="button" data-type="${escapeHtml(item.type)}" data-id="${escapeHtml(item.id.split(":")[1])}">
          <span class="artifact-item-title">
            <strong>${escapeHtml(item.label)}</strong>
            <span class="status-pill status-approved">${escapeHtml(item.type)}</span>
          </span>
          <span class="key-text">${escapeHtml(item.source_table)} · ${escapeHtml(item.source_pk)}</span>
          <span class="artifact-item-meta">
            <span>${escapeHtml(item.summary || "")}</span>
          </span>
        </button>
      `,
    )
    .join("");
  els.results.querySelectorAll(".artifact-item").forEach((item) => {
    item.addEventListener("click", () => loadNeighborhood(item.dataset.type, item.dataset.id));
  });
}

async function loadNeighborhood(type, id) {
  const graph = await fetchJson(
    urlWithTenant(`/api/instances/${encodeURIComponent(type)}/${encodeURIComponent(id)}/neighborhood`, {
      depth: "1",
      limit: "200",
    }),
  );
  state.graph = graph;
  if (graph.approved === false) {
    els.approvedWarning.classList.remove("hidden");
    els.approvedWarning.textContent = `Default instance graph is blocked. Missing approved artifacts: ${graph.missing_approved_artifacts.join(", ")}`;
    els.graphArea.innerHTML = "";
    els.graphCount.textContent = "0 edges";
    return;
  }
  els.approvedWarning.classList.add("hidden");
  els.graphTitle.textContent = graph.center?.label || "Employee -> Orders";
  els.graphSubtitle.textContent = `Depth ${graph.depth}; ${graph.relations_summary.returned_orders} of ${graph.relations_summary.handled_orders} orders returned.`;
  els.graphCount.textContent = `${graph.edges.length} edges`;
  renderGraph(graph);
  showNodeDetail(graph.center);
}

function renderGraph(graph) {
  const employee = graph.center;
  const orders = graph.nodes.filter((node) => node.type === "Order");
  els.graphArea.innerHTML = `
    <section class="center-node">
      <button class="graph-node employee-node" type="button" data-node="${escapeHtml(employee.id)}">
        <strong>${escapeHtml(employee.label)}</strong>
        <span>${escapeHtml(employee.source_pk)}</span>
      </button>
    </section>
    <section class="edge-list">
      ${orders
        .map((order) => {
          const edge = graph.edges.find((item) => item.target === order.id);
          return `
            <button class="edge-row" type="button" data-edge="${escapeHtml(edge.id)}">
              <span class="edge-label">${escapeHtml(employee.label)} handled ${escapeHtml(order.label)}</span>
              <span class="source-ref">${escapeHtml(edge.source_ref)} · ${escapeHtml(edge.ontology_link)}</span>
            </button>
            <button class="graph-node order-node" type="button" data-node="${escapeHtml(order.id)}">
              <strong>${escapeHtml(order.label)}</strong>
              <span>${escapeHtml(order.summary || order.source_pk)}</span>
            </button>
          `;
        })
        .join("")}
    </section>
  `;
  els.graphArea.querySelectorAll("[data-node]").forEach((node) => {
    node.addEventListener("click", () => {
      const item = graph.nodes.find((candidate) => candidate.id === node.dataset.node);
      showNodeDetail(item);
    });
  });
  els.graphArea.querySelectorAll("[data-edge]").forEach((edgeEl) => {
    edgeEl.addEventListener("click", async () => {
      const edge = graph.edges.find((candidate) => candidate.id === edgeEl.dataset.edge);
      const detail = await fetchJson(urlWithTenant("/api/instances/edge", { source: edge.source, target: edge.target }));
      showEdgeDetail(detail);
    });
  });
}

async function showNodeDetail(node) {
  const id = node.id.split(":")[1];
  const detail = await fetchJson(urlWithTenant(`/api/instances/${encodeURIComponent(node.type)}/${encodeURIComponent(id)}`));
  els.detailKind.textContent = `${detail.type} node`;
  els.detailTitle.textContent = detail.label;
  els.detailBody.innerHTML = `
    <section class="detail-section">
      <a class="panel-link" href="/?tenant=${encodeURIComponent(state.tenant)}&artifact=${encodeURIComponent(detail.ontology_artifact)}">Open ontology artifact</a>
    </section>
    <section class="detail-section">
      <h3>Source</h3>
      <p>${escapeHtml(detail.source_table)} · ${escapeHtml(detail.source_pk)}</p>
      <p class="muted">${escapeHtml(detail.namespace)} · graph ${escapeHtml(detail.graph_database)}</p>
    </section>
    <section class="detail-section">
      <h3>Key properties</h3>
      <pre class="code-block">${escapeHtml(json(detail.key_properties))}</pre>
    </section>
    <section class="detail-section">
      <h3>Source row</h3>
      <pre class="code-block">${escapeHtml(json(detail.source_row))}</pre>
    </section>
  `;
}

function showEdgeDetail(edge) {
  els.detailKind.textContent = "Employee-Order edge";
  els.detailTitle.textContent = edge.label;
  els.detailBody.innerHTML = `
    <section class="detail-section">
      <a class="panel-link" href="/?tenant=${encodeURIComponent(state.tenant)}&artifact=${encodeURIComponent(edge.ontology_link)}">Open ontology link</a>
    </section>
    <section class="detail-section">
      <h3>Provenance</h3>
      <dl class="provenance-grid">
        <div><dt>Source field</dt><dd>${escapeHtml(edge.source_field)}</dd></div>
        <div><dt>Join condition</dt><dd>${escapeHtml(edge.join_condition)}</dd></div>
        <div><dt>Ontology link</dt><dd>${escapeHtml(edge.ontology_link)}</dd></div>
        <div><dt>Graph database</dt><dd>${escapeHtml(edge.graph_database)}</dd></div>
      </dl>
      <p class="muted">${escapeHtml(edge.evidence)}</p>
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
}

els.searchButton.addEventListener("click", () => searchInstances().catch((error) => showToast(error.message)));
els.query.addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchInstances().catch((error) => showToast(error.message));
});
els.tenantSwitcher.addEventListener("change", async () => {
  state.tenant = els.tenantSwitcher.value;
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  window.history.replaceState({}, "", url);
  await loadTenants();
  await searchInstances();
});

const params = new URLSearchParams(window.location.search);
if (params.get("type")) els.type.value = params.get("type");
if (params.get("q")) els.query.value = params.get("q");
loadTenants()
  .then(() => searchInstances())
  .then(async () => {
    if (params.get("id")) await loadNeighborhood(params.get("type") || "Employee", params.get("id"));
  })
  .catch((error) => showToast(error.message));
