const params = new URLSearchParams(window.location.search);

const state = {
  tenant: params.get("tenant") || "default",
  overview: null,
};

const els = {
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  breadcrumb: document.querySelector("#breadcrumb"),
  askForm: document.querySelector("#ask-form"),
  askInput: document.querySelector("#ask-input"),
  statusSpace: document.querySelector("#status-space"),
  statusGraph: document.querySelector("#status-graph"),
  statusEntities: document.querySelector("#status-entities"),
  statusRelations: document.querySelector("#status-relations"),
  statusFindings: document.querySelector("#status-findings"),
  statusState: document.querySelector("#status-state"),
  statusUpdate: document.querySelector("#status-update"),
  keyFindings: document.querySelector("#key-findings"),
  attentionItems: document.querySelector("#attention-items"),
  quickTasks: document.querySelector("#quick-tasks"),
  recentChanges: document.querySelector("#recent-changes"),
  recentCount: document.querySelector("#recent-count"),
  allFindingsLink: document.querySelector("#all-findings-link"),
  qualityLink: document.querySelector("#quality-link"),
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

function confidenceLabel(value) {
  const confidence = Number(value || 0);
  if (confidence >= 0.85) return "high";
  if (confidence >= 0.7) return "medium";
  return "low";
}

function scopeLabel(scope = {}) {
  if (scope.center_node) return scope.center_node;
  if (scope.center_edge) return `${scope.center_edge.source} -> ${scope.center_edge.target}`;
  return `${scope.object_type || "Employee"}:${scope.instance_id || "4"}`;
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

async function loadOverview() {
  state.overview = await fetchJson(tenantUrl("/api/portal/overview"));
  renderOverview();
}

function renderOverview() {
  const overview = state.overview;
  const tenant = overview.tenant;
  const status = overview.knowledge_status || {};
  els.shellTenantLabel.textContent = tenant.display_name;
  els.shellTenantMeta.textContent = `namespace ${tenant.namespace} · graph ${tenant.graph_database}`;
  els.breadcrumb.textContent = `Workbench / ${tenant.namespace} / reasoning situation`;
  els.statusSpace.textContent = tenant.display_name;
  els.statusGraph.textContent = `namespace ${tenant.namespace} · graph ${tenant.graph_database}`;
  els.statusEntities.textContent = String(status.entity_count ?? 0);
  els.statusRelations.textContent = String(status.relation_count ?? 0);
  els.statusFindings.textContent = String(status.finding_count ?? 0);
  els.statusState.textContent = `${status.system_state || "unknown"} · approved-only ${status.approved_only ? "on" : "off"}`;
  els.statusUpdate.textContent = status.latest_update || "No runs yet";
  els.allFindingsLink.href = tenantUrl("/findings.html");
  els.qualityLink.href = tenantUrl("/quality.html");
  renderKeyFindings(overview.key_findings || []);
  renderAttention(overview.attention_items || []);
  renderQuickTasks(overview.quick_tasks || []);
  renderRecent(overview.recent_changes || {});
  setNavLinks();
}

function renderKeyFindings(findings) {
  if (!findings.length) {
    els.keyFindings.innerHTML = '<section class="empty-state">No reasoning findings yet. Ask a question or run scoped reasoning to create the first draft finding.</section>';
    return;
  }
  els.keyFindings.innerHTML = findings
    .map((finding) => {
      const evidenceCount = (finding.supporting_evidence || []).length;
      const href = tenantUrl("/findings.html", { finding: finding.canonical_key });
      return `
        <article class="finding-card">
          <div class="finding-card-top">
            <span class="status-pill muted-pill">${escapeHtml(finding.status)}</span>
            <span class="metric">${confidenceLabel(finding.confidence)} · ${Number(finding.confidence || 0).toFixed(2)}</span>
          </div>
          <h3>${escapeHtml(finding.title)}</h3>
          <p>${escapeHtml(finding.conclusion)}</p>
          <dl class="compact-meta">
            <div><dt>Scope</dt><dd>${escapeHtml(scopeLabel(finding.task_scope))}</dd></div>
            <div><dt>Evidence</dt><dd>${evidenceCount} item${evidenceCount === 1 ? "" : "s"}</dd></div>
            <div><dt>Question</dt><dd>${escapeHtml(finding.question || "-")}</dd></div>
          </dl>
          <a class="panel-link" href="${escapeHtml(href)}">Open explanation</a>
        </article>
      `;
    })
    .join("");
}

function renderAttention(items) {
  if (!items.length) {
    els.attentionItems.innerHTML = '<section class="empty-state">No high-priority attention items in the current tenant.</section>';
    return;
  }
  els.attentionItems.innerHTML = items
    .map(
      (item) => `
        <a class="attention-item ${escapeHtml(item.severity || "review")}" href="${escapeHtml(item.href || tenantUrl("/quality.html"))}">
          <strong>${escapeHtml(item.title)}</strong>
          <span>${escapeHtml(item.summary || "")}</span>
          <small>${escapeHtml(item.kind || "attention")}</small>
        </a>
      `,
    )
    .join("");
}

function renderQuickTasks(tasks) {
  els.quickTasks.innerHTML = (tasks || [])
    .map((task) => `<a class="quick-task" href="${escapeHtml(task.href)}">${escapeHtml(task.label)}</a>`)
    .join("");
}

function renderRecent(recent) {
  const rows = [
    ...(recent.findings || []).map((item) => ({ kind: "finding", title: item.title, time: item.updated_at, href: tenantUrl("/findings.html", { finding: item.canonical_key }) })),
    ...(recent.runs || []).map((item) => ({ kind: "run", title: item.run_key, time: item.created_at, href: tenantUrl("/questions.html", { task: item.task_key }) })),
    ...(recent.tasks || []).map((item) => ({ kind: "question", title: item.question, time: item.updated_at, href: tenantUrl("/questions.html", { task: item.canonical_key }) })),
  ]
    .filter((row) => row.title)
    .slice(0, 10);
  els.recentCount.textContent = `${rows.length} item${rows.length === 1 ? "" : "s"}`;
  if (!rows.length) {
    els.recentChanges.innerHTML = '<section class="empty-state">No recent reasoning activity.</section>';
    return;
  }
  els.recentChanges.innerHTML = rows
    .map(
      (row) => `
        <a class="timeline-item" href="${escapeHtml(row.href)}">
          <span class="metric">${escapeHtml(row.kind)}</span>
          <strong>${escapeHtml(row.title)}</strong>
          <small>${escapeHtml(row.time || "")}</small>
        </a>
      `,
    )
    .join("");
}

els.askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = els.askInput.value.trim();
  window.location.href = tenantUrl("/questions.html", question ? { question } : {});
});

loadOverview().catch((error) => showToast(error.message));
