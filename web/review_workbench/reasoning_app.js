const state = {
  tenant: new URLSearchParams(window.location.search).get("tenant") || "default",
  tenants: [],
  taskKey: new URLSearchParams(window.location.search).get("task") || "reasoning:employee-4-workload-analysis",
  task: null,
  run: null,
  findings: [],
  selectedFinding: null,
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
  breadcrumb: document.querySelector("#breadcrumb"),
  runTask: document.querySelector("#run-task"),
  taskList: document.querySelector("#task-list"),
  taskTitle: document.querySelector("#task-title"),
  taskQuestion: document.querySelector("#task-question"),
  runStatus: document.querySelector("#run-status"),
  warning: document.querySelector("#reasoning-warning"),
  evidencePaths: document.querySelector("#evidence-paths"),
  findingStatus: document.querySelector("#finding-status"),
  findingDetail: document.querySelector("#finding-detail"),
  runTitle: document.querySelector("#run-title"),
  traceBody: document.querySelector("#trace-body"),
  reviewReason: document.querySelector("#review-reason"),
  approveFinding: document.querySelector("#approve-finding"),
  needsChangesFinding: document.querySelector("#needs-changes-finding"),
  rejectFinding: document.querySelector("#reject-finding"),
  commentFinding: document.querySelector("#comment-finding"),
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
  els.navReasoning.href = `/reasoning.html?tenant=${encodeURIComponent(state.tenant)}&task=${encodeURIComponent(state.taskKey)}`;
  els.breadcrumb.textContent = `Reasoning / ${state.taskKey}`;
}

async function loadTasks() {
  const data = await fetchJson(urlWithTenant("/api/reasoning/tasks"));
  const tasks = data.tasks || [];
  state.task = tasks.find((task) => task.canonical_key === state.taskKey) || tasks[0];
  renderTasks(tasks);
  await loadTaskDetail();
}

function renderTasks(tasks) {
  els.taskList.innerHTML = tasks
    .map(
      (task) => `
        <button class="artifact-item ${task.canonical_key === state.taskKey ? "active" : ""}" type="button" data-key="${escapeHtml(task.canonical_key)}">
          <span class="artifact-item-title">
            <strong>${escapeHtml(task.canonical_key)}</strong>
            <span class="status-pill status-approved">${escapeHtml(task.status)}</span>
          </span>
          <span class="key-text">${escapeHtml(task.question)}</span>
          <span class="artifact-item-meta">
            <span>${escapeHtml(task.scope?.object_type || "Employee")}:${escapeHtml(task.scope?.instance_id || "4")}</span>
            <span>${task.latest_run ? escapeHtml(task.latest_run.status) : "not run"}</span>
          </span>
        </button>
      `,
    )
    .join("");
  els.taskList.querySelectorAll("[data-key]").forEach((item) => {
    item.addEventListener("click", async () => {
      state.taskKey = item.dataset.key;
      updateUrl();
      await loadTaskDetail();
    });
  });
}

async function loadTaskDetail() {
  const data = await fetchJson(urlWithTenant(`/api/reasoning/tasks/${encodeURIComponent(state.taskKey)}`));
  state.task = data.task;
  state.run = data.latest_run;
  state.findings = data.findings || [];
  state.selectedFinding = state.findings[0] || null;
  renderTask();
}

function renderTask() {
  els.taskTitle.textContent = state.task?.canonical_key || "Reasoning task";
  els.taskQuestion.textContent = state.task?.question || "";
  els.runStatus.textContent = state.run?.status || "not run";
  els.runStatus.className = `status-pill ${state.run?.status === "completed" ? "status-approved" : "muted-pill"}`;
  els.breadcrumb.textContent = `Reasoning / ${state.task?.scope?.object_type || "Employee"}:${state.task?.scope?.instance_id || "4"}`;
  if (state.run?.status === "blocked") {
    const missing = state.run.output?.missing_approved_artifacts || [];
    els.warning.classList.remove("hidden");
    els.warning.textContent = `Reasoning blocked by approved-only gate. Missing artifacts: ${missing.join(", ")}`;
  } else {
    els.warning.classList.add("hidden");
  }
  renderEvidence();
  renderFinding();
  renderTrace();
}

function renderEvidence() {
  const paths = state.selectedFinding?.supporting_evidence || state.run?.evidence_paths || [];
  if (paths.length === 0) {
    els.evidencePaths.innerHTML = '<section class="empty-state">No evidence paths yet.</section>';
    return;
  }
  els.evidencePaths.innerHTML = paths
    .map(
      (path) => `
        <article class="panel evidence-path-card">
          <div class="panel-header">
            <h3>${escapeHtml(path.label)}</h3>
            <span class="metric">${escapeHtml(path.kind)}</span>
          </div>
          <p>${escapeHtml(path.summary)}</p>
          <p class="source-ref">${escapeHtml(path.source_ref)}</p>
          <a class="panel-link" href="${escapeHtml(path.url)}">Open evidence</a>
        </article>
      `,
    )
    .join("");
}

function renderFinding() {
  const finding = state.selectedFinding;
  if (!finding) {
    els.findingStatus.textContent = "draft";
    els.findingStatus.className = "status-pill muted-pill";
    els.findingDetail.innerHTML = '<p class="muted">No finding has been proposed for this tenant.</p>';
    return;
  }
  els.findingStatus.textContent = `${finding.status} · v${finding.version}`;
  els.findingStatus.className = `status-pill ${finding.status === "approved" ? "status-approved" : "muted-pill"}`;
  els.findingDetail.innerHTML = `
    <section class="detail-section">
      <h3>${escapeHtml(finding.title)}</h3>
      <p>${escapeHtml(finding.conclusion)}</p>
      <p class="metric">confidence ${Number(finding.confidence || 0).toFixed(2)}</p>
    </section>
    <section class="detail-section">
      <h3>Recommended action proposal</h3>
      <pre class="code-block">${escapeHtml(json(finding.recommended_action))}</pre>
    </section>
    <section class="detail-section">
      <h3>Counter evidence / limits</h3>
      <pre class="code-block">${escapeHtml(json(finding.counter_evidence))}</pre>
    </section>
  `;
}

function renderTrace() {
  if (!state.run) {
    els.runTitle.textContent = "No run selected";
    els.traceBody.innerHTML = '<p class="muted">Run the task to generate a trace.</p>';
    return;
  }
  els.runTitle.textContent = state.run.run_key;
  els.traceBody.innerHTML = `
    <section class="detail-section">
      <h3>Query plan</h3>
      <ol class="trace-list">
        ${(state.run.query_plan || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ol>
    </section>
    <section class="detail-section">
      <h3>Tool calls</h3>
      <pre class="code-block">${escapeHtml(json(state.run.tool_calls))}</pre>
    </section>
    <section class="detail-section">
      <h3>Eval</h3>
      <pre class="code-block">${escapeHtml(json(state.run.eval_result))}</pre>
    </section>
    <section class="detail-section">
      <h3>Output</h3>
      <pre class="code-block">${escapeHtml(json(state.run.output))}</pre>
    </section>
  `;
}

function updateUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  url.searchParams.set("task", state.taskKey);
  window.history.replaceState({}, "", url);
}

async function runTask() {
  els.runStatus.textContent = "running";
  const result = await fetchJson(urlWithTenant(`/api/reasoning/tasks/${encodeURIComponent(state.taskKey)}/run`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  state.task = result.task;
  state.run = result.run;
  state.findings = result.findings || [];
  state.selectedFinding = state.findings[0] || null;
  renderTask();
  showToast(result.approved === false ? "Reasoning blocked by approved-only gate" : "Draft finding proposed");
}

async function reviewFinding(action) {
  if (!state.selectedFinding) {
    showToast("No finding selected");
    return;
  }
  const reason = els.reviewReason.value.trim();
  const data = await fetchJson(
    urlWithTenant(`/api/reasoning/findings/${encodeURIComponent(state.selectedFinding.canonical_key)}/${action}`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer: "Itachi", reason }),
    },
  );
  state.selectedFinding = data.finding;
  state.findings = [data.finding, ...state.findings.filter((item) => item.canonical_key !== data.finding.canonical_key)];
  renderFinding();
  showToast(`${action} recorded for ${data.finding.canonical_key}`);
}

els.tenantSwitcher.addEventListener("change", async () => {
  state.tenant = els.tenantSwitcher.value;
  updateUrl();
  await loadTenants();
  await loadTasks();
});
els.runTask.addEventListener("click", () => runTask().catch((error) => showToast(error.message)));
els.approveFinding.addEventListener("click", () => reviewFinding("approve").catch((error) => showToast(error.message)));
els.needsChangesFinding.addEventListener("click", () => reviewFinding("needs-changes").catch((error) => showToast(error.message)));
els.rejectFinding.addEventListener("click", () => reviewFinding("reject").catch((error) => showToast(error.message)));
els.commentFinding.addEventListener("click", () => reviewFinding("comment").catch((error) => showToast(error.message)));

loadTenants()
  .then(() => loadTasks())
  .catch((error) => showToast(error.message));
