const params = new URLSearchParams(window.location.search);

const state = {
  tenant: params.get("tenant") || "default",
};

const els = {
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  breadcrumb: document.querySelector("#breadcrumb"),
  draftCount: document.querySelector("#draft-count"),
  lowCount: document.querySelector("#low-count"),
  blockedRunCount: document.querySelector("#blocked-run-count"),
  agentCount: document.querySelector("#agent-count"),
  sandboxState: document.querySelector("#sandbox-state"),
  attentionCount: document.querySelector("#attention-count"),
  attentionList: document.querySelector("#attention-list"),
  sandboxGaps: document.querySelector("#sandbox-gaps"),
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

async function loadQuality() {
  const overview = await fetchJson(tenantUrl("/api/portal/overview"));
  const tenant = overview.tenant;
  const quality = overview.quality || {};
  els.shellTenantLabel.textContent = tenant.display_name;
  els.shellTenantMeta.textContent = `namespace ${tenant.namespace} · graph ${tenant.graph_database}`;
  els.breadcrumb.textContent = `Quality / ${tenant.namespace}`;
  els.draftCount.textContent = String(quality.draft_findings || 0);
  els.lowCount.textContent = String(quality.low_confidence_findings || 0);
  els.blockedRunCount.textContent = String(quality.blocked_reasoning_runs || 0);
  els.agentCount.textContent = String(quality.blocked_agent_runs || 0);
  els.sandboxState.textContent = quality.sandbox_approved === false ? "blocked" : "ready";
  renderAttention(overview.attention_items || []);
  renderSandbox(quality.sandbox_missing_artifacts || []);
  setNavLinks();
}

function renderAttention(items) {
  els.attentionCount.textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;
  if (!items.length) {
    els.attentionList.innerHTML = '<section class="empty-state">No active quality issues for this tenant.</section>';
    return;
  }
  els.attentionList.innerHTML = items
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

function renderSandbox(missing) {
  if (!missing.length) {
    els.sandboxGaps.innerHTML = '<section class="empty-state">Sandbox tenant has no missing artifact gaps reported.</section>';
    return;
  }
  els.sandboxGaps.innerHTML = missing
    .map(
      (key) => `
        <section class="evidence-item">
          <div class="row-between">
            <strong>${escapeHtml(key)}</strong>
            <span class="status-pill status-rejected">missing</span>
          </div>
          <p>Sandbox blocks this approved-only graph path instead of falling back to default tenant data.</p>
        </section>
      `,
    )
    .join("");
}

loadQuality().catch((error) => showToast(error.message));
