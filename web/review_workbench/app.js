const state = {
  artifacts: [],
  selectedKey: null,
  selectedArtifact: null,
  tenant: new URLSearchParams(window.location.search).get("tenant") || "default",
  tenants: [],
};

const els = {
  list: document.querySelector("#artifact-list"),
  stats: document.querySelector("#stats"),
  search: document.querySelector("#search"),
  typeFilter: document.querySelector("#type-filter"),
  statusFilter: document.querySelector("#status-filter"),
  refresh: document.querySelector("#refresh"),
  tenantSwitcher: document.querySelector("#tenant-switcher"),
  tenantNamespace: document.querySelector("#tenant-namespace"),
  tenantGraph: document.querySelector("#tenant-graph"),
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  navWorkbench: document.querySelector("#nav-workbench"),
  navInstances: document.querySelector("#nav-instances"),
  breadcrumb: document.querySelector("#breadcrumb"),
  empty: document.querySelector("#empty-state"),
  grid: document.querySelector("#detail-grid"),
  artifactType: document.querySelector("#artifact-type"),
  artifactTitle: document.querySelector("#artifact-title"),
  artifactKey: document.querySelector("#artifact-key"),
  statusPill: document.querySelector("#status-pill"),
  versionPill: document.querySelector("#version-pill"),
  workflowStatus: document.querySelector("#workflow-status"),
  workflowNext: document.querySelector("#workflow-next"),
  workflowEligibility: document.querySelector("#workflow-eligibility"),
  latestAuditDecision: document.querySelector("#latest-audit-decision"),
  latestAuditDetail: document.querySelector("#latest-audit-detail"),
  instanceLink: document.querySelector("#instance-link"),
  instanceLinkEmpty: document.querySelector("#instance-link-empty"),
  confidence: document.querySelector("#confidence"),
  description: document.querySelector("#description"),
  sourceAgent: document.querySelector("#source-agent"),
  projectId: document.querySelector("#project-id"),
  updatedAt: document.querySelector("#updated-at"),
  payload: document.querySelector("#payload"),
  copyPayload: document.querySelector("#copy-payload"),
  reason: document.querySelector("#reason"),
  approve: document.querySelector("#approve"),
  needsChanges: document.querySelector("#needs-changes"),
  reject: document.querySelector("#reject"),
  comment: document.querySelector("#comment"),
  editName: document.querySelector("#edit-name"),
  editDescription: document.querySelector("#edit-description"),
  editPayload: document.querySelector("#edit-payload"),
  saveEdit: document.querySelector("#save-edit"),
  evidenceCount: document.querySelector("#evidence-count"),
  evidenceList: document.querySelector("#evidence-list"),
  reviewCount: document.querySelector("#review-count"),
  reviewList: document.querySelector("#review-list"),
  toast: document.querySelector("#toast"),
};

function debounce(fn, delay = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function json(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

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
  window.setTimeout(() => els.toast.classList.remove("visible"), 3000);
}

function statusClass(status) {
  return `status-${String(status || "unknown").replaceAll("_", "_")}`;
}

function statusLabel(status) {
  if (status === "approved") return "approved · canonical";
  if (status === "rejected") return "rejected · excluded";
  if (status === "needs_changes") return "needs changes · blocked";
  if (status === "draft") return "draft · not canonical";
  if (status === "proposed") return "proposed · review";
  return `${status || "unknown"} · not canonical`;
}

function nextAction(status) {
  if (status === "approved") return "Eligible now; comment, edit, or reject if evidence changes.";
  if (status === "rejected") return "Excluded from ingestion; edit or regenerate before review.";
  if (status === "needs_changes") return "Resolve requested changes, then approve or reject.";
  if (status === "draft") return "Review evidence; approve to publish or reject with reason.";
  if (status === "proposed") return "Make a review decision before publishing.";
  return "Review status before publishing.";
}

function eligibilityText(status) {
  return status === "approved"
    ? "Eligible for default ingestion"
    : "Not eligible for default ingestion";
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `${response.status} ${response.statusText}`);
  }
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
  renderTenant(current);
}

function renderTenant(tenant) {
  if (!tenant) return;
  els.tenantNamespace.textContent = tenant.namespace;
  els.tenantGraph.textContent = tenant.graph_database;
  els.shellTenantLabel.textContent = `${tenant.display_name}`;
  els.shellTenantMeta.textContent = `namespace ${tenant.namespace} · graph ${tenant.graph_database}`;
  els.navWorkbench.href = `/?tenant=${encodeURIComponent(state.tenant)}`;
  els.navInstances.href = `/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee&id=4`;
}

function filterParams() {
  const params = new URLSearchParams();
  if (els.search.value.trim()) params.set("search", els.search.value.trim());
  if (els.typeFilter.value) params.set("artifact_type", els.typeFilter.value);
  if (els.statusFilter.value) params.set("status", els.statusFilter.value);
  return params;
}

async function loadArtifacts() {
  const query = filterParams();
  query.set("tenant", state.tenant);
  const data = await fetchJson(`/api/artifacts?${query.toString()}`);
  state.artifacts = data.artifacts || [];
  renderFilterOptions(data.stats || []);
  renderStats(data.stats || []);
  renderList();
  const requestedArtifact = new URLSearchParams(window.location.search).get("artifact");
  if (!state.selectedKey && requestedArtifact) {
    await selectArtifact(requestedArtifact);
  } else if (!state.selectedKey && state.artifacts.length > 0) {
    await selectArtifact(state.artifacts[0].canonical_key);
  }
}

function renderFilterOptions(stats) {
  const selectedType = els.typeFilter.value;
  const selectedStatus = els.statusFilter.value;
  const types = [...new Set(stats.map((item) => item.artifact_type))].sort();
  const statuses = [...new Set(stats.map((item) => item.status))].sort();
  els.typeFilter.innerHTML =
    '<option value="">All types</option>' +
    types.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join("");
  els.statusFilter.innerHTML =
    '<option value="">All statuses</option>' +
    statuses.map((status) => `<option value="${escapeHtml(status)}">${escapeHtml(status)}</option>`).join("");
  els.typeFilter.value = selectedType;
  els.statusFilter.value = selectedStatus;
}

function renderStats(stats) {
  const total = stats.reduce((sum, item) => sum + Number(item.count || 0), 0);
  const approved = stats
    .filter((item) => item.status === "approved")
    .reduce((sum, item) => sum + Number(item.count || 0), 0);
  els.stats.innerHTML = [
    `<span class="stat-chip">total ${total}</span>`,
    `<span class="stat-chip status-approved">approved ${approved}</span>`,
    ...stats.map(
      (item) =>
        `<span class="stat-chip">${escapeHtml(item.artifact_type)}:${escapeHtml(item.status)} ${item.count}</span>`,
    ),
  ].join("");
}

function renderList() {
  if (state.artifacts.length === 0) {
    els.list.innerHTML = '<div class="empty-state">No artifacts match the current filters.</div>';
    return;
  }
  els.list.innerHTML = state.artifacts
    .map(
      (artifact) => `
        <button class="artifact-item ${artifact.canonical_key === state.selectedKey ? "active" : ""}"
          type="button"
          data-key="${escapeHtml(artifact.canonical_key)}">
          <span class="artifact-item-title">
            <strong>${escapeHtml(artifact.name)}</strong>
            <span class="status-pill ${statusClass(artifact.status)}">${escapeHtml(artifact.status)}</span>
          </span>
          <span class="key-text">${escapeHtml(artifact.canonical_key)}</span>
          <span class="artifact-item-meta">
            <span>${escapeHtml(artifact.artifact_type)}</span>
            <span>v${escapeHtml(artifact.version)}</span>
          </span>
        </button>
      `,
    )
    .join("");
  els.list.querySelectorAll(".artifact-item").forEach((item) => {
    item.addEventListener("click", () => selectArtifact(item.dataset.key));
  });
}

async function selectArtifact(canonicalKey) {
  state.selectedKey = canonicalKey;
  updateWorkbenchUrl(canonicalKey);
  renderList();
  const artifact = await fetchJson(urlWithTenant(`/api/artifacts/${encodeURIComponent(canonicalKey)}`));
  state.selectedArtifact = artifact;
  renderArtifact(artifact);
}

function updateWorkbenchUrl(canonicalKey) {
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  url.searchParams.set("artifact", canonicalKey);
  window.history.replaceState({}, "", url);
}

function renderArtifact(artifact) {
  els.empty.classList.add("hidden");
  els.grid.classList.remove("hidden");
  els.artifactType.textContent = artifact.artifact_type;
  els.artifactTitle.textContent = artifact.name;
  els.artifactKey.textContent = artifact.canonical_key;
  els.statusPill.textContent = statusLabel(artifact.status);
  els.statusPill.className = `status-pill ${statusClass(artifact.status)}`;
  els.versionPill.textContent = `v${artifact.version}`;
  els.workflowStatus.textContent = statusLabel(artifact.status);
  els.workflowNext.textContent = nextAction(artifact.status);
  els.workflowEligibility.textContent = eligibilityText(artifact.status);
  els.workflowEligibility.className = artifact.status === "approved" ? "eligible" : "not-eligible";
  els.confidence.textContent = `confidence ${Number(artifact.confidence ?? 0).toFixed(2)}`;
  els.description.textContent = artifact.description || "No description.";
  els.sourceAgent.textContent = artifact.source_agent || "-";
  els.projectId.textContent = artifact.tenant
    ? `${artifact.tenant.display_name} / ${artifact.tenant.namespace}`
    : artifact.project_id || "-";
  els.updatedAt.textContent = artifact.updated_at || "-";
  els.payload.textContent = json(artifact.payload);
  els.editName.value = artifact.name || "";
  els.editDescription.value = artifact.description || "";
  els.editPayload.value = json(artifact.payload);
  renderEvidence(artifact.evidence || []);
  renderReviews(artifact.reviews || []);
  renderLatestAudit((artifact.reviews || [])[0]);
  renderInstanceLink(artifact);
  els.breadcrumb.textContent = `Workbench / ${artifact.artifact_type} / ${artifact.canonical_key}`;
}

function renderEvidence(evidence) {
  els.evidenceCount.textContent = `${evidence.length} item${evidence.length === 1 ? "" : "s"}`;
  if (evidence.length === 0) {
    els.evidenceList.innerHTML = '<p class="muted">No evidence recorded.</p>';
    return;
  }
  els.evidenceList.innerHTML = evidence
    .map(
      (item) => `
        <section class="evidence-item">
          <div class="row-between">
            <strong>${escapeHtml(item.evidence_type)}</strong>
            <span class="metric">${Number(item.confidence ?? 0).toFixed(2)}</span>
          </div>
          <span class="source-ref">${escapeHtml(item.source_ref)}</span>
          <p class="muted">${escapeHtml(item.summary || "No summary.")}</p>
        </section>
      `,
    )
    .join("");
}

function renderLatestAudit(item) {
  if (!item) {
    els.latestAuditDecision.textContent = "No audit event";
    els.latestAuditDetail.textContent = "Review events will appear here after actions.";
    return;
  }
  els.latestAuditDecision.textContent = `${item.decision} by ${item.reviewer}`;
  els.latestAuditDetail.textContent =
    `${item.before_status} -> ${item.after_status} · v${item.before_version} -> v${item.after_version} · ${item.reason || "No reason"} · ${item.created_at || ""}`;
}

function renderInstanceLink(artifact) {
  const key = artifact.canonical_key;
  if (key === "object:employee") {
    els.instanceLink.href = `/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee`;
    els.instanceLink.textContent = "Browse Employee instances";
  } else if (key === "link:employee:1:n:order") {
    els.instanceLink.href = `/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee&id=4&edgeSource=${encodeURIComponent("Employee:4")}&edgeTarget=${encodeURIComponent("Order:10250")}`;
    els.instanceLink.textContent = "Open Employee #4 -> Orders";
  } else if (key === "object:order") {
    els.instanceLink.href = `/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee&id=4&node=${encodeURIComponent("Order:10250")}`;
    els.instanceLink.textContent = "View Orders through Employee #4";
  } else {
    els.instanceLink.classList.add("hidden");
    els.instanceLinkEmpty.classList.remove("hidden");
    return;
  }
  els.instanceLink.classList.remove("hidden");
  els.instanceLinkEmpty.classList.add("hidden");
}

function renderReviews(reviews) {
  els.reviewCount.textContent = `${reviews.length} event${reviews.length === 1 ? "" : "s"}`;
  if (reviews.length === 0) {
    els.reviewList.innerHTML = '<p class="muted">No review events recorded.</p>';
    return;
  }
  els.reviewList.innerHTML = reviews
    .map(
      (item) => `
        <section class="review-item">
          <div class="row-between">
            <strong>${escapeHtml(item.decision)}</strong>
            <span class="muted">${escapeHtml(item.created_at || "")}</span>
          </div>
          <span>${escapeHtml(item.reviewer)} · ${escapeHtml(item.before_status)} → ${escapeHtml(item.after_status)} · v${escapeHtml(item.before_version)} → v${escapeHtml(item.after_version)}</span>
          <p class="muted">${escapeHtml(item.reason || "No reason.")}</p>
        </section>
      `,
    )
    .join("");
}

async function runAction(action) {
  if (!state.selectedKey) return;
  const reason = els.reason.value.trim();
  if (["reject", "needs-changes", "comment"].includes(action) && !reason) {
    showToast("Reason is required for this action.");
    return;
  }
  const artifact = await fetchJson(urlWithTenant(`/api/artifacts/${encodeURIComponent(state.selectedKey)}/${action}`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviewer: "Itachi", reason }),
  });
  els.reason.value = "";
  state.selectedArtifact = artifact;
  renderArtifact(artifact);
  await loadArtifacts();
  showToast(`${action} recorded for ${artifact.canonical_key}`);
}

function validatePayloadField() {
  try {
    JSON.parse(els.editPayload.value || "{}");
    els.editPayload.classList.remove("invalid");
    return true;
  } catch (error) {
    els.editPayload.classList.add("invalid");
    return false;
  }
}

async function saveEdit() {
  if (!state.selectedKey) return;
  let payload;
  try {
    payload = JSON.parse(els.editPayload.value || "{}");
  } catch (error) {
    showToast(`Payload JSON is invalid: ${error.message}`);
    return;
  }
  const reason = els.reason.value.trim() || "Workbench edit";
  const artifact = await fetchJson(urlWithTenant(`/api/artifacts/${encodeURIComponent(state.selectedKey)}/edit`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reviewer: "Itachi",
      reason,
      name: els.editName.value,
      description: els.editDescription.value,
      payload,
    }),
  });
  state.selectedArtifact = artifact;
  renderArtifact(artifact);
  await loadArtifacts();
  showToast(`Edit recorded for ${artifact.canonical_key}`);
}

els.refresh.addEventListener("click", () => loadArtifacts().catch((error) => showToast(error.message)));
els.search.addEventListener("input", debounce(() => loadArtifacts().catch((error) => showToast(error.message))));
els.typeFilter.addEventListener("change", () => loadArtifacts().catch((error) => showToast(error.message)));
els.statusFilter.addEventListener("change", () => loadArtifacts().catch((error) => showToast(error.message)));
els.tenantSwitcher.addEventListener("change", async () => {
  state.tenant = els.tenantSwitcher.value;
  state.selectedKey = null;
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  url.searchParams.delete("artifact");
  window.history.replaceState({}, "", url);
  await loadTenants();
  await loadArtifacts();
});
els.approve.addEventListener("click", () => runAction("approve").catch((error) => showToast(error.message)));
els.needsChanges.addEventListener("click", () => runAction("needs-changes").catch((error) => showToast(error.message)));
els.reject.addEventListener("click", () => runAction("reject").catch((error) => showToast(error.message)));
els.comment.addEventListener("click", () => runAction("comment").catch((error) => showToast(error.message)));
els.saveEdit.addEventListener("click", () => saveEdit().catch((error) => showToast(error.message)));
els.editPayload.addEventListener("input", validatePayloadField);
els.copyPayload.addEventListener("click", async () => {
  await navigator.clipboard.writeText(els.payload.textContent);
  showToast("Payload copied.");
});

loadTenants()
  .then(() => loadArtifacts())
  .catch((error) => showToast(error.message));
