const params = new URLSearchParams(window.location.search);

const state = {
  tenant: params.get("tenant") || "default",
  tasks: [],
};

const els = {
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  breadcrumb: document.querySelector("#breadcrumb"),
  form: document.querySelector("#question-form"),
  questionInput: document.querySelector("#question-input"),
  scopeType: document.querySelector("#scope-type"),
  centerNode: document.querySelector("#center-node"),
  depth: document.querySelector("#depth"),
  limit: document.querySelector("#limit"),
  graphContextLink: document.querySelector("#graph-context-link"),
  result: document.querySelector("#question-result"),
  taskCount: document.querySelector("#task-count"),
  taskList: document.querySelector("#task-list"),
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

function statusText(status) {
  return t(status || "not run");
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

function scopeLabel(scope = {}) {
  if (scope.center_node) return `${isZh() ? "图谱节点" : "graph node"} ${scope.center_node}`;
  if (scope.center_edge) return `${isZh() ? "图谱关系" : "graph edge"} ${scope.center_edge.source} -> ${scope.center_edge.target}`;
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

async function loadData() {
  const [tenants, tasks] = await Promise.all([
    fetchJson(tenantUrl("/api/tenants")),
    fetchJson(tenantUrl("/api/reasoning/tasks")),
  ]);
  const current = tenants.current;
  els.shellTenantLabel.textContent = current.display_name;
  els.shellTenantMeta.textContent = `namespace ${current.namespace} · graph ${current.graph_database}`;
  els.breadcrumb.textContent = `Questions / ${current.namespace}`;
  state.tasks = tasks.tasks || [];
  setNavLinks();
  renderTasks();
  hydrateFromUrl();
}

function hydrateFromUrl() {
  if (params.get("question")) els.questionInput.value = params.get("question");
  if (params.get("task")) {
    const task = state.tasks.find((item) => item.canonical_key === params.get("task"));
    if (task) renderSelectedTask(task);
  }
  updateGraphLink();
}

function renderTasks() {
  els.taskCount.textContent = `${state.tasks.length} task${state.tasks.length === 1 ? "" : "s"}`;
  if (!state.tasks.length) {
    els.taskList.innerHTML = '<section class="empty-state">No reasoning questions yet.</section>';
    return;
  }
  els.taskList.innerHTML = state.tasks
    .map(
      (task) => `
        <article class="task-card" data-key="${escapeHtml(task.canonical_key)}">
          <div class="finding-card-top">
            <span class="status-pill muted-pill">${escapeHtml(statusText(task.status))}</span>
            <span class="metric">${escapeHtml(task.latest_run ? statusText(task.latest_run.status) : statusText("not run"))}</span>
          </div>
          <h3>${escapeHtml(task.question)}</h3>
          <p class="key-text">${escapeHtml(task.canonical_key)}</p>
          <dl class="compact-meta">
            <div><dt>Scope</dt><dd>${escapeHtml(scopeLabel(task.scope))}</dd></div>
            <div><dt>Source</dt><dd>${escapeHtml(task.scope?.source || "fixed_reasoning")}</dd></div>
            <div><dt>Depth / limit</dt><dd>${escapeHtml(task.scope?.depth || 1)} / ${escapeHtml(task.scope?.node_limit || 200)}</dd></div>
          </dl>
          <div class="action-row">
            <a class="panel-link" href="${escapeHtml(tenantUrl("/reasoning.html", { task: task.canonical_key }))}">Open reasoning process</a>
            <a class="panel-link" href="${escapeHtml(tenantUrl("/findings.html", { task: task.canonical_key }))}">Open findings</a>
          </div>
        </article>
      `,
    )
    .join("");
  els.taskList.querySelectorAll("[data-key]").forEach((item) => {
    item.addEventListener("click", () => {
      const task = state.tasks.find((candidate) => candidate.canonical_key === item.dataset.key);
      if (task) renderSelectedTask(task);
    });
  });
}

function renderSelectedTask(task) {
  els.result.innerHTML = `
    <strong>${escapeHtml(task.question)}</strong>
    <span class="key-text">${escapeHtml(task.canonical_key)}</span>
    <p>${escapeHtml(t("Scope"))}: ${escapeHtml(scopeLabel(task.scope))}. ${escapeHtml(t("Source"))}: ${escapeHtml(task.scope?.source || "fixed_reasoning")}.</p>
    <a class="panel-link" href="${escapeHtml(tenantUrl("/reasoning.html", { task: task.canonical_key }))}">Open reasoning process</a>
  `;
}

function updateGraphLink() {
  const node = els.centerNode.value || "Employee:4";
  const [type, id] = node.includes(":") ? node.split(":", 2) : ["Employee", "4"];
  els.graphContextLink.href = tenantUrl("/graph.html", {
    type,
    id,
    depth: els.depth.value || "1",
    limit: els.limit.value || "200",
    node,
  });
}

async function createQuestion(event) {
  event.preventDefault();
  const question = els.questionInput.value.trim();
  if (!question) {
    showToast("Question is required");
    return;
  }
  const payload = {
    question,
    scope: {
      type: els.scopeType.value,
      center_node: els.centerNode.value || "Employee:4",
      depth: Number(els.depth.value || 1),
      limit: Number(els.limit.value || 200),
      graph_url: els.graphContextLink.getAttribute("href"),
    },
  };
  const data = await fetchJson(tenantUrl("/api/reasoning/questions"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  els.result.innerHTML = `
    <strong>Scoped question created</strong>
    <p>${escapeHtml(data.task.question)}</p>
    <a class="panel-link" href="${escapeHtml(data.reasoning_url)}">Open reasoning process</a>
  `;
  await loadData();
  showToast("Scoped question created");
  window.location.href = data.reasoning_url;
}

els.form.addEventListener("submit", (event) => createQuestion(event).catch((error) => showToast(error.message)));
els.centerNode.addEventListener("input", updateGraphLink);
els.depth.addEventListener("input", updateGraphLink);
els.limit.addEventListener("input", updateGraphLink);

loadData().catch((error) => showToast(error.message));
