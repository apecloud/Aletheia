const state = {
  tenant: new URLSearchParams(window.location.search).get("tenant") || "default",
  tenants: [],
  runtimes: [],
  policies: [],
  runs: [],
  selectedRuntimeId: null,
};

const els = {
  tenantSwitcher: document.querySelector("#tenant-switcher"),
  tenantNamespace: document.querySelector("#tenant-namespace"),
  tenantGraph: document.querySelector("#tenant-graph"),
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  navWorkbench: document.querySelector("#nav-workbench"),
  navInstances: document.querySelector("#nav-instances"),
  navReasoning: document.querySelector("#nav-reasoning"),
  navSettings: document.querySelector("#nav-settings"),
  refresh: document.querySelector("#refresh"),
  runtimeList: document.querySelector("#runtime-list"),
  runtimeTitle: document.querySelector("#runtime-title"),
  runtimeSubtitle: document.querySelector("#runtime-subtitle"),
  runtimeStatus: document.querySelector("#runtime-status"),
  runtimeType: document.querySelector("#runtime-type"),
  runtimeBinary: document.querySelector("#runtime-binary"),
  runtimeTemplate: document.querySelector("#runtime-template"),
  runtimeEnabled: document.querySelector("#runtime-enabled"),
  policyId: document.querySelector("#policy-id"),
  policyDetail: document.querySelector("#policy-detail"),
  runCount: document.querySelector("#run-count"),
  runList: document.querySelector("#run-list"),
  healthCheck: document.querySelector("#health-check"),
  smokeRun: document.querySelector("#smoke-run"),
  copyPolicy: document.querySelector("#copy-policy"),
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
    els.shellTenantLabel.textContent = current.display_name;
    els.shellTenantMeta.textContent = `namespace ${current.namespace} · graph ${current.graph_database}`;
  }
  els.navWorkbench.href = `/?tenant=${encodeURIComponent(state.tenant)}`;
  els.navInstances.href = `/instances.html?tenant=${encodeURIComponent(state.tenant)}&type=Employee&id=4`;
  els.navReasoning.href = `/reasoning.html?tenant=${encodeURIComponent(state.tenant)}`;
  els.navSettings.href = `/settings.html?tenant=${encodeURIComponent(state.tenant)}`;
}

async function loadSettings() {
  const data = await fetchJson(urlWithTenant("/api/agent-gateway/settings"));
  state.runtimes = data.runtimes || [];
  state.policies = data.policies || [];
  state.runs = data.runs || [];
  if (!state.selectedRuntimeId && state.runtimes.length > 0) {
    state.selectedRuntimeId = state.runtimes[0].runtime_id;
  }
  renderRuntimes();
  renderSelectedRuntime();
  renderPolicy();
  renderRuns();
}

function renderRuntimes() {
  els.runtimeList.innerHTML = state.runtimes
    .map(
      (runtime) => `
        <button class="artifact-item ${runtime.runtime_id === state.selectedRuntimeId ? "active" : ""}" type="button" data-runtime="${escapeHtml(runtime.runtime_id)}">
          <span class="artifact-item-title">
            <strong>${escapeHtml(runtime.runtime_type)}</strong>
            <span class="status-pill ${runtime.health_status === "available" ? "status-approved" : "muted-pill"}">${escapeHtml(runtime.health_status)}</span>
          </span>
          <span class="key-text">${escapeHtml(runtime.runtime_id)}</span>
          <span class="artifact-item-meta">
            <span>${escapeHtml(runtime.binary_ref)}</span>
            <span>${escapeHtml(runtime.command_template_id)}</span>
          </span>
        </button>
      `,
    )
    .join("");
  els.runtimeList.querySelectorAll("[data-runtime]").forEach((item) => {
    item.addEventListener("click", () => {
      state.selectedRuntimeId = item.dataset.runtime;
      renderRuntimes();
      renderSelectedRuntime();
    });
  });
}

function renderSelectedRuntime() {
  const runtime = selectedRuntime();
  if (!runtime) return;
  els.runtimeTitle.textContent = runtime.runtime_id;
  els.runtimeSubtitle.textContent = `${runtime.runtime_type} / ${runtime.command_template_id}`;
  els.runtimeStatus.textContent = runtime.health_status;
  els.runtimeStatus.className = `status-pill ${runtime.health_status === "available" ? "status-approved" : "muted-pill"}`;
  els.runtimeType.textContent = runtime.runtime_type;
  els.runtimeBinary.textContent = runtime.binary_ref;
  els.runtimeTemplate.textContent = runtime.command_template_id;
  els.runtimeEnabled.textContent = runtime.enabled ? "enabled" : "disabled";
}

function renderPolicy() {
  const policy = state.policies[0];
  if (!policy) return;
  els.policyId.textContent = policy.policy_id;
  els.policyDetail.innerHTML = `
    <section class="detail-section">
      <h3>Allowed paths</h3>
      <pre class="code-block">${escapeHtml(json(policy.allowed_paths))}</pre>
    </section>
    <section class="detail-section">
      <h3>Allowed tools</h3>
      <pre class="code-block">${escapeHtml(json(policy.allowed_tools))}</pre>
    </section>
    <section class="detail-section">
      <h3>Blocked tools</h3>
      <pre class="code-block">${escapeHtml(json(policy.blocked_tools))}</pre>
    </section>
    <section class="detail-section">
      <h3>Secrets</h3>
      <p class="muted">secret_policy=${escapeHtml(policy.secret_policy)}; env_allowlist=${escapeHtml(json(policy.env_allowlist))}</p>
    </section>
  `;
}

function renderRuns() {
  els.runCount.textContent = `${state.runs.length}`;
  if (state.runs.length === 0) {
    els.runList.innerHTML = '<p class="muted">No AgentRun records for this tenant.</p>';
    return;
  }
  els.runList.innerHTML = state.runs
    .map(
      (run) => `
        <section class="review-item">
          <div class="row-between">
            <strong>${escapeHtml(run.run_key)}</strong>
            <span class="status-pill ${run.status === "completed" ? "status-approved" : "muted-pill"}">${escapeHtml(run.status)}</span>
          </div>
          <span class="source-ref">${escapeHtml(run.runtime_id)} · ${escapeHtml(run.task_type)} · ${escapeHtml(run.started_at)}</span>
          <p class="muted">${escapeHtml(run.output_refs?.summary || "")}</p>
          <pre class="code-block">${escapeHtml(json({ files_touched: run.files_touched, policy_violations: run.policy_violations }))}</pre>
        </section>
      `,
    )
    .join("");
}

function selectedRuntime() {
  return state.runtimes.find((runtime) => runtime.runtime_id === state.selectedRuntimeId);
}

async function runHealthCheck() {
  const runtime = selectedRuntime();
  if (!runtime) return;
  await fetchJson(urlWithTenant(`/api/agent-gateway/runtimes/${encodeURIComponent(runtime.runtime_id)}/health`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  await loadSettings();
  showToast(`Health check completed for ${runtime.runtime_id}`);
}

async function runSmoke() {
  const runtime = selectedRuntime();
  if (!runtime) return;
  const result = await fetchJson(urlWithTenant("/api/agent-gateway/runs"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      runtime_id: runtime.runtime_id,
      policy_id: state.policies[0]?.policy_id || "default_cli_policy",
      task_type: "report",
      prompt: "Read the Aletheia README and produce a repository structure smoke report.",
    }),
  });
  await loadSettings();
  showToast(`AgentRun ${result.run.run_key} ${result.run.status}`);
}

els.tenantSwitcher.addEventListener("change", async () => {
  state.tenant = els.tenantSwitcher.value;
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  window.history.replaceState({}, "", url);
  await loadTenants();
  await loadSettings();
});
els.refresh.addEventListener("click", () => loadSettings().catch((error) => showToast(error.message)));
els.healthCheck.addEventListener("click", () => runHealthCheck().catch((error) => showToast(error.message)));
els.smokeRun.addEventListener("click", () => runSmoke().catch((error) => showToast(error.message)));
els.copyPolicy.addEventListener("click", async () => {
  await navigator.clipboard.writeText(json(state.policies[0] || {}));
  showToast("Policy copied");
});

loadTenants()
  .then(() => loadSettings())
  .catch((error) => showToast(error.message));
