const params = new URLSearchParams(window.location.search);

const state = {
  tenant: params.get("tenant") || "default",
  findings: [],
  selectedKey: params.get("finding") || null,
};

const els = {
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  breadcrumb: document.querySelector("#breadcrumb"),
  findingList: document.querySelector("#finding-list"),
  findingKind: document.querySelector("#finding-kind"),
  findingTitle: document.querySelector("#finding-title"),
  findingSummary: document.querySelector("#finding-summary"),
  findingStatus: document.querySelector("#finding-status"),
  findingEmpty: document.querySelector("#finding-empty"),
  findingDetail: document.querySelector("#finding-detail"),
  confidence: document.querySelector("#confidence"),
  conclusion: document.querySelector("#conclusion"),
  question: document.querySelector("#question"),
  scope: document.querySelector("#scope"),
  run: document.querySelector("#run"),
  evidenceCount: document.querySelector("#evidence-count"),
  supportingEvidence: document.querySelector("#supporting-evidence"),
  counterEvidence: document.querySelector("#counter-evidence"),
  graphPath: document.querySelector("#graph-path"),
  basis: document.querySelector("#basis"),
  followups: document.querySelector("#followups"),
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
  els.toast.textContent = t(message);
  els.toast.classList.add("visible");
  window.setTimeout(() => els.toast.classList.remove("visible"), 3200);
}

function t(key, vars = {}) {
  return window.AletheiaShell?.t ? window.AletheiaShell.t(key, vars) : key;
}

function isZh() {
  return window.AletheiaShell?.lang?.() === "zh";
}

function statusText(status, version) {
  const base = t(status || "draft");
  return version ? `${base} · v${version}` : base;
}

function confidenceText(value) {
  const score = Number(value || 0).toFixed(2);
  return isZh() ? `${t("Confidence")} ${score}` : `confidence ${score}`;
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

async function loadData() {
  const overview = await fetchJson(tenantUrl("/api/portal/overview"));
  const tenant = overview.tenant;
  els.shellTenantLabel.textContent = tenant.display_name;
  els.shellTenantMeta.textContent = `namespace ${tenant.namespace} · graph ${tenant.graph_database}`;
  state.findings = overview.key_findings || [];
  setNavLinks();
  renderList();
  const first = state.selectedKey || state.findings[0]?.canonical_key;
  if (first) await selectFinding(first);
}

function renderList() {
  if (!state.findings.length) {
    els.findingList.innerHTML = '<section class="empty-state">No reasoning findings yet.</section>';
    return;
  }
  els.findingList.innerHTML = state.findings
    .map(
      (finding) => `
        <button class="artifact-item ${finding.canonical_key === state.selectedKey ? "active" : ""}" type="button" data-key="${escapeHtml(finding.canonical_key)}">
          <span class="artifact-item-title">
            <strong>${escapeHtml(finding.title)}</strong>
            <span class="status-pill muted-pill">${escapeHtml(statusText(finding.status))}</span>
          </span>
          <span class="key-text">${escapeHtml(scopeLabel(finding.task_scope))}</span>
          <span class="artifact-item-meta">
            <span>${escapeHtml(confidenceText(finding.confidence))}</span>
            <span>${(finding.supporting_evidence || []).length} evidence</span>
          </span>
        </button>
      `,
    )
    .join("");
  els.findingList.querySelectorAll("[data-key]").forEach((item) => {
    item.addEventListener("click", () => selectFinding(item.dataset.key).catch((error) => showToast(error.message)));
  });
}

async function selectFinding(key) {
  state.selectedKey = key;
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  url.searchParams.set("finding", key);
  window.history.replaceState({}, "", url);
  renderList();
  const data = await fetchJson(tenantUrl(`/api/portal/findings/${encodeURIComponent(key)}`));
  renderFinding(data.finding);
}

function renderFinding(finding) {
  const scope = finding.task?.scope || {};
  const run = finding.run || {};
  els.findingEmpty.classList.add("hidden");
  els.findingDetail.classList.remove("hidden");
  els.findingKind.textContent = t("Explainable conclusion");
  els.findingTitle.textContent = finding.title;
  els.findingSummary.textContent = finding.conclusion;
  els.findingStatus.textContent = statusText(finding.status, finding.version);
  els.confidence.textContent = confidenceText(finding.confidence);
  els.conclusion.textContent = finding.conclusion;
  els.question.textContent = finding.task?.question || "-";
  els.scope.textContent = scopeLabel(scope);
  els.run.textContent = `${run.run_key || "-"} · ${run.status || "not run"}`;
  els.breadcrumb.textContent = `Findings / ${scopeLabel(scope)} / ${finding.canonical_key}`;
  renderEvidence(finding.supporting_evidence || []);
  renderCounterEvidence(finding.counter_evidence || []);
  renderGraphPath(scope, finding.supporting_evidence || []);
  renderBasis(scope, run);
  renderFollowups(finding, scope);
}

function renderEvidence(paths) {
  els.evidenceCount.textContent = `${paths.length} item${paths.length === 1 ? "" : "s"}`;
  if (!paths.length) {
    els.supportingEvidence.innerHTML = '<section class="empty-state">No supporting evidence recorded.</section>';
    return;
  }
  els.supportingEvidence.innerHTML = paths.map(renderEvidenceCard).join("");
}

function renderCounterEvidence(items) {
  if (!items.length) {
    els.counterEvidence.innerHTML = '<section class="empty-state">No counter evidence or conflicts recorded for this draft.</section>';
    return;
  }
  els.counterEvidence.innerHTML = items.map((item) => renderEvidenceCard({ ...item, kind: item.kind || "counter_evidence" }, "conflict")).join("");
}

function renderEvidenceCard(path, fallbackType = "fact") {
  return `
    <section class="evidence-item">
      <div class="row-between">
        <strong>${escapeHtml(path.label || path.kind || "Evidence")}</strong>
        <span class="status-pill muted-pill">${escapeHtml(evidenceType(path, fallbackType))}</span>
      </div>
      <p>${escapeHtml(path.summary || "No summary.")}</p>
      <span class="source-ref">${escapeHtml(path.source_ref || "")}</span>
      ${path.url ? `<a class="panel-link" href="${escapeHtml(path.url)}">Open source context</a>` : ""}
    </section>
  `;
}

function renderGraphPath(scope, evidence) {
  const graphUrl = evidence.find((item) => item.url)?.url || scope.graph_url;
  const center = scopeLabel(scope);
  els.graphPath.innerHTML = `
    <p>${escapeHtml(center)}</p>
    ${scope.center_edge ? `<p class="muted">Path: ${escapeHtml(scope.center_edge.source)} -> ${escapeHtml(scope.center_edge.target)}</p>` : ""}
    ${graphUrl ? `<a class="panel-link" href="${escapeHtml(graphUrl)}">Open graph context</a>` : '<p class="muted">No graph context recorded.</p>'}
  `;
}

function renderBasis(scope, run) {
  const allowed = scope.allowed_link_keys || ["link:employee:1:n:order"];
  const tools = (run.tool_calls || []).map((item) => item.tool).filter(Boolean);
  els.basis.innerHTML = `
    <dl class="compact-meta">
      <div><dt>Ontology links</dt><dd>${escapeHtml(allowed.join(", "))}</dd></div>
      <div><dt>Approved-only</dt><dd>${scope.approved_only === false ? "off" : "on"}</dd></div>
      <div><dt>Review gate</dt><dd>${escapeHtml(scope.review_gate || "draft_only")}</dd></div>
      <div><dt>Tools</dt><dd>${escapeHtml(tools.join(", ") || "-")}</dd></div>
    </dl>
  `;
}

function renderFollowups(finding, scope) {
  const center = scopeLabel(scope);
  const questions = [
    `Which evidence is most important for ${center}?`,
    `Is there counter evidence for this conclusion?`,
    `Which downstream decisions depend on ${center}?`,
    `If this evidence is removed, does the conclusion still hold?`,
  ];
  els.followups.innerHTML = questions
    .map((question) => `<a class="quick-task" href="${escapeHtml(tenantUrl("/questions.html", { question, task: finding.task?.canonical_key || "" }))}">${escapeHtml(question)}</a>`)
    .join("");
}

loadData().catch((error) => showToast(error.message));
