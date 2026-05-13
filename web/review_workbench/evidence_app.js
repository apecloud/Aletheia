const params = new URLSearchParams(window.location.search);

const state = {
  tenant: params.get("tenant") || "default",
  findings: [],
  evidence: [],
  selectedId: params.get("evidence") || null,
};

const els = {
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  breadcrumb: document.querySelector("#breadcrumb"),
  total: document.querySelector("#evidence-total"),
  kindFilter: document.querySelector("#kind-filter"),
  evidenceList: document.querySelector("#evidence-list"),
  evidenceKind: document.querySelector("#evidence-kind"),
  evidenceTitle: document.querySelector("#evidence-title"),
  evidenceSummary: document.querySelector("#evidence-summary"),
  evidenceStatus: document.querySelector("#evidence-status"),
  evidenceEmpty: document.querySelector("#evidence-empty"),
  evidenceDetail: document.querySelector("#evidence-detail"),
  evidenceRole: document.querySelector("#evidence-role"),
  selectedSummary: document.querySelector("#selected-summary"),
  selectedSource: document.querySelector("#selected-source"),
  selectedFinding: document.querySelector("#selected-finding"),
  selectedTask: document.querySelector("#selected-task"),
  sourcePath: document.querySelector("#source-path"),
  basis: document.querySelector("#basis"),
  linkedFinding: document.querySelector("#linked-finding"),
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

function tenantUrl(path, extra = {}) {
  const query = new URLSearchParams(extra);
  query.set("tenant", state.tenant);
  return `${path}?${query.toString()}`;
}

function setNavLinks() {
  const links = {
    "#nav-workbench": "/",
    "#nav-questions": "/questions.html",
    "#nav-findings": "/findings.html",
    "#nav-evidence": "/evidence.html",
    "#nav-explore": "/graph.html",
    "#nav-quality": "/quality.html",
    "#nav-ontology": "/ontology.html",
    "#nav-runtime": "/settings.html",
    "#nav-audit": "/ontology.html",
  };
  Object.entries(links).forEach(([selector, path]) => {
    const el = document.querySelector(selector);
    if (!el) return;
    const query = path === "/graph.html" ? { type: "Employee", id: "4", depth: "1", limit: "200" } : {};
    el.href = tenantUrl(path, query);
  });
}

function scopeLabel(scope = {}) {
  if (scope.center_node) return scope.center_node;
  if (scope.center_edge) return `${scope.center_edge.source} -> ${scope.center_edge.target}`;
  return `${scope.object_type || "Employee"}:${scope.instance_id || "4"}`;
}

function evidenceType(path = {}, fallback = "fact") {
  if (path.kind === "graph_node" || path.kind === "graph_edge") return "fact";
  if (path.kind === "scope_limit") return "missing";
  if (path.kind === "counter_evidence") return "conflict";
  if (path.kind === "question_scope") return "hypothesis";
  return fallback;
}

function evidenceId(findingKey, role, index) {
  return `${findingKey}:${role}:${index}`;
}

async function loadData() {
  const overview = await fetchJson(tenantUrl("/api/portal/overview"));
  const tenant = overview.tenant;
  els.shellTenantLabel.textContent = tenant.display_name;
  els.shellTenantMeta.textContent = `namespace ${tenant.namespace} · graph ${tenant.graph_database}`;
  els.breadcrumb.textContent = `Evidence / ${tenant.namespace} / source chain`;
  state.findings = await Promise.all(
    (overview.key_findings || []).map((finding) =>
      fetchJson(tenantUrl(`/api/portal/findings/${encodeURIComponent(finding.canonical_key)}`)).then((data) => data.finding),
    ),
  );
  state.evidence = state.findings.flatMap(evidenceFromFinding);
  setNavLinks();
  renderList();
  selectEvidence(state.selectedId || state.evidence[0]?.id);
}

function evidenceFromFinding(finding) {
  const supporting = (finding.supporting_evidence || []).map((path, index) => normalizeEvidence(finding, path, "supporting", index));
  const counter = (finding.counter_evidence || []).map((path, index) => normalizeEvidence(finding, { ...path, kind: path.kind || "counter_evidence" }, "counter", index));
  return [...supporting, ...counter];
}

function normalizeEvidence(finding, path, role, index) {
  const scope = finding.task?.scope || {};
  return {
    ...path,
    id: evidenceId(finding.canonical_key, role, index),
    role,
    type: evidenceType(path, role === "counter" ? "conflict" : "fact"),
    finding_key: finding.canonical_key,
    finding_title: finding.title,
    task_key: finding.task?.canonical_key || "",
    task_question: finding.task?.question || "",
    scope,
    run_key: finding.run?.run_key || "",
  };
}

function visibleEvidence() {
  const kind = els.kindFilter.value;
  return kind ? state.evidence.filter((item) => item.type === kind) : state.evidence;
}

function renderList() {
  const items = visibleEvidence();
  els.total.textContent = `${state.evidence.length} item${state.evidence.length === 1 ? "" : "s"}`;
  if (!items.length) {
    els.evidenceList.innerHTML = '<section class="empty-state">No supporting evidence recorded.</section>';
    return;
  }
  els.evidenceList.innerHTML = items
    .map(
      (item) => `
        <button class="evidence-index-item ${item.id === state.selectedId ? "active" : ""}" type="button" data-id="${escapeHtml(item.id)}">
          <span class="artifact-item-title">
            <strong>${escapeHtml(item.label || item.kind || "Evidence")}</strong>
            <span class="status-pill ${item.type === "conflict" ? "status-rejected" : "status-approved"}">${escapeHtml(item.type)}</span>
          </span>
          <span class="key-text">${escapeHtml(item.source_ref || scopeLabel(item.scope))}</span>
          <span class="artifact-item-meta">
            <span>${escapeHtml(item.role)}</span>
            <span>${escapeHtml(item.finding_title)}</span>
          </span>
        </button>
      `,
    )
    .join("");
  els.evidenceList.querySelectorAll("[data-id]").forEach((item) => {
    item.addEventListener("click", () => selectEvidence(item.dataset.id));
  });
}

function selectEvidence(id) {
  const item = state.evidence.find((candidate) => candidate.id === id);
  if (!item) return;
  state.selectedId = id;
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  url.searchParams.set("evidence", id);
  window.history.replaceState({}, "", url);
  renderList();
  renderEvidence(item);
}

function renderEvidence(item) {
  els.evidenceEmpty.classList.add("hidden");
  els.evidenceDetail.classList.remove("hidden");
  els.evidenceKind.textContent = `${item.type} evidence`;
  els.evidenceTitle.textContent = item.label || item.kind || "Evidence";
  els.evidenceSummary.textContent = item.summary || "No summary recorded.";
  els.evidenceStatus.textContent = item.role;
  els.evidenceStatus.className = `status-pill ${item.type === "conflict" ? "status-rejected" : "status-approved"}`;
  els.evidenceRole.textContent = `${item.type} · ${item.role}`;
  els.selectedSummary.textContent = item.summary || "No summary recorded.";
  els.selectedSource.textContent = item.source_ref || "-";
  els.selectedFinding.textContent = item.finding_title;
  els.selectedTask.textContent = item.task_key || "-";
  els.breadcrumb.textContent = `Evidence / ${item.type} / ${item.finding_key}`;
  renderSourcePath(item);
  renderBasis(item);
  renderLinkedFinding(item);
  window.AletheiaShell?.translate?.();
}

function renderSourcePath(item) {
  const graphUrl = item.url || item.scope.graph_url;
  els.sourcePath.innerHTML = `
    <dl class="compact-meta">
      <div><dt>Kind</dt><dd>${escapeHtml(item.kind || item.type)}</dd></div>
      <div><dt>Scope</dt><dd>${escapeHtml(scopeLabel(item.scope))}</dd></div>
      <div><dt>Run</dt><dd>${escapeHtml(item.run_key || "-")}</dd></div>
    </dl>
    ${graphUrl ? `<a class="panel-link" href="${escapeHtml(graphUrl)}">Open source context</a>` : '<p class="muted">No graph context recorded.</p>'}
  `;
}

function renderBasis(item) {
  const allowed = item.scope.allowed_link_keys || ["link:employee:1:n:order"];
  els.basis.innerHTML = `
    <dl class="compact-meta">
      <div><dt>Ontology links</dt><dd>${escapeHtml(allowed.join(", "))}</dd></div>
      <div><dt>Approved-only</dt><dd>${item.scope.approved_only === false ? "off" : "on"}</dd></div>
      <div><dt>Review gate</dt><dd>${escapeHtml(item.scope.review_gate || "draft_only")}</dd></div>
    </dl>
  `;
}

function renderLinkedFinding(item) {
  els.linkedFinding.innerHTML = `
    <a class="quick-task" href="${escapeHtml(tenantUrl("/reasoning.html", { task: item.task_key }))}">Open reasoning loop</a>
    <a class="quick-task" href="${escapeHtml(tenantUrl("/findings.html", { finding: item.finding_key }))}">Open explanation</a>
    <a class="quick-task" href="${escapeHtml(tenantUrl("/questions.html", { task: item.task_key }))}">Open question history</a>
    ${item.url ? `<a class="quick-task" href="${escapeHtml(item.url)}">Open graph context</a>` : ""}
  `;
}

els.kindFilter.addEventListener("change", () => {
  renderList();
  const items = visibleEvidence();
  if (!items.find((item) => item.id === state.selectedId)) selectEvidence(items[0]?.id);
});

loadData().catch((error) => showToast(error.message));
