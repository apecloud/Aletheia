const params = new URLSearchParams(window.location.search);

const state = {
  tenant: params.get("tenant") || "default",
  tenants: [],
  taskKey: params.get("task") || "reasoning:employee-4-workload-analysis",
  task: null,
  run: null,
  findings: [],
  selectedFinding: null,
  graphHandoff: null,
  graphContext: null,
};

const els = {
  tenantSwitcher: document.querySelector("#tenant-switcher"),
  tenantNamespace: document.querySelector("#tenant-namespace"),
  tenantGraph: document.querySelector("#tenant-graph"),
  shellTenantLabel: document.querySelector("#shell-tenant-label"),
  shellTenantMeta: document.querySelector("#shell-tenant-meta"),
  navWorkbench: document.querySelector("#nav-workbench"),
  navInstances: document.querySelector("#nav-instances") || document.querySelector("#nav-explore"),
  navGraph: document.querySelector("#nav-graph") || document.querySelector("#nav-explore"),
  navReasoning: document.querySelector("#nav-reasoning"),
  navSettings: document.querySelector("#nav-settings") || document.querySelector("#nav-runtime"),
  breadcrumb: document.querySelector("#breadcrumb"),
  runTask: document.querySelector("#run-task"),
  runTaskInline: document.querySelector("#run-task-inline"),
  questionForm: document.querySelector("#loop-question-form"),
  questionInput: document.querySelector("#question-input"),
  centerNode: document.querySelector("#center-node"),
  depth: document.querySelector("#depth"),
  limit: document.querySelector("#limit"),
  graphContextLink: document.querySelector("#graph-context-link"),
  questionHistoryLink: document.querySelector("#question-history-link"),
  taskSource: document.querySelector("#task-source"),
  taskCount: document.querySelector("#task-count"),
  taskSummary: document.querySelector("#task-summary"),
  taskList: document.querySelector("#task-list"),
  taskTitle: document.querySelector("#task-title"),
  taskQuestion: document.querySelector("#task-question"),
  runStatus: document.querySelector("#run-status"),
  warning: document.querySelector("#reasoning-warning"),
  evidencePaths: document.querySelector("#evidence-paths"),
  evidenceCount: document.querySelector("#evidence-count"),
  findingStatus: document.querySelector("#finding-status"),
  findingDetail: document.querySelector("#finding-detail"),
  runTitle: document.querySelector("#run-title"),
  traceBody: document.querySelector("#trace-body"),
  followupForm: document.querySelector("#followup-form"),
  followupInput: document.querySelector("#followup-input"),
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

function confidenceText(value) {
  const score = Number(value || 0).toFixed(2);
  return isZh() ? `${t("Confidence")} ${score}` : `confidence ${score}`;
}

function statusText(status, version) {
  const base = t(status || "draft");
  return version ? `${base} · v${version}` : base;
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

function scopeLabel(task = state.task) {
  const scope = task?.scope || {};
  if (scope.center_node) return `${isZh() ? "图谱节点" : "Graph node"} ${scope.center_node}`;
  if (scope.center_edge) return `${isZh() ? "图谱关系" : "Graph edge"} ${scope.center_edge.source} -> ${scope.center_edge.target}`;
  return `${scope.object_type || "Employee"}:${scope.instance_id || "4"}`;
}

function taskDisplayTitle(task = state.task) {
  const scope = task?.scope || {};
  if (scope.center_node) return `Scoped reasoning: ${scope.center_node}`;
  if (scope.center_edge) return `Scoped reasoning: ${scope.center_edge.source} -> ${scope.center_edge.target}`;
  return task?.canonical_key || "Reasoning task";
}

function graphHandoffPayload() {
  if (params.get("source") !== "graph") return null;
  const centerNode = params.get("center_node");
  const edgeSource = params.get("center_edge_source");
  const edgeTarget = params.get("center_edge_target");
  if (!centerNode && (!edgeSource || !edgeTarget)) return null;
  const centerEdge =
    edgeSource && edgeTarget
      ? { id: params.get("center_edge_id") || `${edgeSource}->${edgeTarget}`, source: edgeSource, target: edgeTarget }
      : null;
  const evidencePath = {
    kind: params.get("evidence_kind") || (centerEdge ? "graph_edge" : "graph_node"),
    label: params.get("evidence_label") || centerNode || centerEdge?.id,
    summary: params.get("evidence_summary") || params.get("evidence_label") || centerNode || centerEdge?.id,
    url: params.get("graph_url") || `/graph.html?tenant=${encodeURIComponent(state.tenant)}`,
    source_ref: params.get("evidence_source_ref") || "",
    payload: centerEdge
      ? {
          edge_id: centerEdge.id,
          ontology_link: params.get("ontology_link") || undefined,
        }
      : {
          node_id: centerNode,
          ontology_artifact: params.get("ontology_artifact") || undefined,
        },
  };
  return {
    question:
      params.get("question") ||
      `Explain the approved graph evidence around ${centerNode || centerEdge.id} and identify any workload, concentration, or provenance risk.`,
    graph_url: evidencePath.url,
    autorun: params.get("autorun") === "1",
    scope: {
      center_node: centerNode || undefined,
      center_edge: centerEdge || undefined,
      depth: Number(params.get("depth") || 1),
      node_limit: Number(params.get("limit") || 200),
      edge_limit: Number(params.get("limit") || 200),
      allowed_node_types: ["Employee", "Order"],
      allowed_link_keys: ["link:employee:1:n:order"],
      approved_only: true,
      evidence_paths: [evidencePath],
    },
  };
}

function evidenceSignature(path = {}) {
  return [
    path.kind || "",
    path.label || "",
    path.source_ref || "",
    path.payload?.node_id || "",
    path.payload?.edge_id || "",
  ].join("|");
}

function currentRunMatchesHandoff() {
  if (!state.graphHandoff) return true;
  const expected = state.graphHandoff.scope?.evidence_paths?.[0];
  const actual = state.run?.evidence_paths?.[0];
  if (!expected || !actual) return false;
  return evidenceSignature(expected) === evidenceSignature(actual);
}

function currentFindingMatchesHandoff() {
  if (!state.graphHandoff) return true;
  const expected = state.graphHandoff.scope?.evidence_paths?.[0];
  const actual = state.selectedFinding?.supporting_evidence?.[0];
  if (!expected || !actual) return false;
  return evidenceSignature(expected) === evidenceSignature(actual);
}

function centerNodeParts(value) {
  const node = value || "Employee:4";
  return node.includes(":") ? node.split(":", 2) : ["Employee", "4"];
}

function updateGraphContextLink() {
  if (!els.graphContextLink) return;
  const [type, id] = centerNodeParts(els.centerNode?.value || state.task?.scope?.center_node || "Employee:4");
  els.graphContextLink.href = urlWithTenant("/graph.html", {
    type,
    id,
    depth: els.depth?.value || state.task?.scope?.depth || "1",
    limit: els.limit?.value || state.task?.scope?.node_limit || "200",
  });
  if (els.questionHistoryLink) els.questionHistoryLink.href = urlWithTenant("/questions.html", { task: state.taskKey });
}

function hydrateQuestionFormFromTask() {
  if (!state.task) return;
  const scope = state.task.scope || {};
  if (els.questionInput && !els.questionInput.matches(":focus")) els.questionInput.value = state.task.question || "";
  if (els.centerNode && !els.centerNode.matches(":focus")) els.centerNode.value = scope.center_node || "Employee:4";
  if (els.depth && !els.depth.matches(":focus")) els.depth.value = scope.depth || 1;
  if (els.limit && !els.limit.matches(":focus")) els.limit.value = scope.node_limit || scope.edge_limit || 200;
  updateGraphContextLink();
}

async function acceptGraphHandoffIfPresent() {
  const payload = graphHandoffPayload();
  if (!payload) return false;
  state.graphHandoff = payload;
  els.runStatus.textContent = "creating scoped task";
  const data = await fetchJson(urlWithTenant("/api/reasoning/tasks/from-graph"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: payload.question, graph_url: payload.graph_url, scope: payload.scope }),
  });
  state.taskKey = data.task.canonical_key;
  updateUrl({ keepGraphScope: true });
  showToast("Scoped reasoning task opened from graph selection");
  return true;
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
  if (els.navReasoning) els.navReasoning.href = `/reasoning.html?tenant=${encodeURIComponent(state.tenant)}&task=${encodeURIComponent(state.taskKey)}`;
  els.navSettings.href = `/settings.html?tenant=${encodeURIComponent(state.tenant)}`;
  els.breadcrumb.textContent = `Reasoning / ${state.taskKey}`;
  updateGraphContextLink();
}

async function loadTasks() {
  const data = await fetchJson(urlWithTenant("/api/reasoning/tasks"));
  const tasks = data.tasks || [];
  state.task = tasks.find((task) => task.canonical_key === state.taskKey) || tasks[0];
  renderTasks(tasks);
  await loadTaskDetail();
}

function renderTasks(tasks) {
  if (els.taskCount) els.taskCount.textContent = `${tasks.length} task${tasks.length === 1 ? "" : "s"}`;
  els.taskList.innerHTML = tasks
    .map(
      (task) => `
        <button class="artifact-item ${task.canonical_key === state.taskKey ? "active" : ""}" type="button" data-key="${escapeHtml(task.canonical_key)}">
          <span class="artifact-item-title">
            <strong>${escapeHtml(task.canonical_key)}</strong>
            <span class="status-pill status-approved">${escapeHtml(task.status)}</span>
          </span>
          <span class="key-text">${escapeHtml(taskQuestionLabel(task))}</span>
          <span class="artifact-item-meta">
            <span>${escapeHtml(scopeLabel(task))}</span>
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

function taskQuestionLabel(task) {
  const question = String(task?.question || "");
  const centerNode = task?.scope?.center_node || task?.scope?.evidence_paths?.[0]?.payload?.center_node || "";
  if (centerNode.startsWith("Employee:") && /work snapshot|approved order relationships|loaded in the current evidence scope/i.test(question)) {
    return `${centerNode} 员工画像分析`;
  }
  return question;
}

async function loadTaskDetail() {
  const data = await fetchJson(urlWithTenant(`/api/reasoning/tasks/${encodeURIComponent(state.taskKey)}`));
  state.task = data.task;
  state.run = data.latest_run;
  state.findings = data.findings || [];
  state.selectedFinding = state.findings[0] || null;
  state.graphContext = await loadGraphContextForTask();
  if (state.graphHandoff && !currentFindingMatchesHandoff()) {
    state.selectedFinding = null;
  }
  renderTask();
  if (state.graphHandoff?.autorun && (!state.run || !currentRunMatchesHandoff())) {
    state.graphHandoff.autorun = false;
    await runTask();
  }
}

async function loadGraphContextForTask() {
  const scope = state.task?.scope || {};
  const centerNode = scope.center_node || scope.evidence_paths?.[0]?.payload?.center_node;
  if (!centerNode || !centerNode.includes(":")) return null;
  const [type, id] = centerNode.split(":", 2);
  try {
    return await fetchJson(
      urlWithTenant("/api/graph/context", {
        type,
        id,
        depth: scope.depth || 1,
        limit: scope.node_limit || scope.edge_limit || 200,
      }),
    );
  } catch (_) {
    return null;
  }
}

function renderTask() {
  els.taskTitle.textContent = taskDisplayTitle();
  els.taskQuestion.textContent = state.task?.question || "";
  els.runStatus.textContent = state.run?.status || "not run";
  els.runStatus.className = `status-pill ${state.run?.status === "completed" ? "status-approved" : "muted-pill"}`;
  if (els.taskSource) els.taskSource.textContent = state.task?.scope?.source || "fixed_reasoning";
  els.breadcrumb.textContent = `Reasoning / ${scopeLabel()}`;
  hydrateQuestionFormFromTask();
  renderTaskSummary();
  if (state.run?.status === "blocked") {
    const missing = state.run.output?.missing_approved_artifacts || [];
    els.warning.classList.remove("hidden");
    els.warning.textContent = `${t("Reasoning blocked by approved-only gate. Missing artifacts:")} ${missing.join(", ")}`;
  } else {
    els.warning.classList.add("hidden");
  }
  renderEvidence();
  renderFinding();
  renderTrace();
}

function renderTaskSummary() {
  if (!els.taskSummary || !state.task) return;
  const scope = state.task.scope || {};
  const latest = state.run?.status || "not run";
  els.taskSummary.innerHTML = `
    <div><dt>Current task</dt><dd>${escapeHtml(state.task.canonical_key)}</dd></div>
    <div><dt>Scope</dt><dd>${escapeHtml(scopeLabel())}</dd></div>
    <div><dt>Source</dt><dd>${escapeHtml(scope.source || "fixed_reasoning")}</dd></div>
    <div><dt>Latest run</dt><dd>${escapeHtml(latest)}</dd></div>
    <div><dt>Review gate</dt><dd>${escapeHtml(scope.review_gate || "draft_only")}</dd></div>
    <div><dt>Approved-only</dt><dd>${scope.approved_only === false ? "off" : "on"}</dd></div>
  `;
}

function renderEvidence() {
  const supporting = state.selectedFinding?.supporting_evidence || state.run?.evidence_paths || [];
  const counter = state.selectedFinding?.counter_evidence || [];
  const paths = [...supporting, ...counter];
  if (els.evidenceCount) els.evidenceCount.textContent = `${paths.length} item${paths.length === 1 ? "" : "s"}`;
  if (paths.length === 0) {
    els.evidencePaths.innerHTML = '<section class="empty-state">No evidence paths yet.</section>';
    return;
  }
  const renderPath = (path, role, index) => {
    const graphUrl = path.url || state.task?.scope?.graph_url || "";
    const payload = path.payload ? `<pre class="code-block">${escapeHtml(json(path.payload))}</pre>` : '<p class="muted">No raw payload recorded.</p>';
    return `
      <details class="evidence-path-card loop-collapsible" ${index < 2 ? "open" : ""}>
        <summary>
          <span>${escapeHtml(path.label || path.kind || "Evidence")}</span>
          <span class="metric">${escapeHtml(role)} · ${escapeHtml(path.kind || "evidence")}</span>
        </summary>
        <div class="evidence-card-body">
          <p>${escapeHtml(path.summary || "No summary recorded.")}</p>
          <dl class="compact-meta">
            <div><dt>Role</dt><dd>${escapeHtml(role)}</dd></div>
            <div><dt>Kind</dt><dd>${escapeHtml(path.kind || "-")}</dd></div>
            <div><dt>Source ref</dt><dd>${escapeHtml(path.source_ref || "-")}</dd></div>
            <div><dt>Source path</dt><dd>${graphUrl ? `<a class="panel-link" href="${escapeHtml(graphUrl)}">Open source context</a>` : "No graph context recorded."}</dd></div>
          </dl>
          <details class="nested-detail">
            <summary>Raw evidence payload</summary>
            ${payload}
          </details>
        </div>
      </details>
    `;
  };
  const graphUrl = supporting.find((item) => item.url)?.url || state.task?.scope?.graph_url || "";
  els.evidencePaths.innerHTML = `
    <section class="evidence-loop-grid">
      <article class="detail-section">
        <h3>Supporting evidence</h3>
        ${(supporting.length ? supporting.map((path, index) => renderPath(path, "supporting", index)).join("") : '<p class="muted">No supporting evidence recorded.</p>')}
      </article>
      <article class="detail-section">
        <h3>Counter evidence / conflicts</h3>
        ${(counter.length ? counter.map((path, index) => renderPath(path, "counter", index)).join("") : '<p class="muted">No counter evidence or conflicts recorded for this draft.</p>')}
      </article>
      <details class="loop-collapsible" open>
        <summary><span>Graph path</span><span class="metric">path</span></summary>
        <div class="evidence-card-body">
          <p>${escapeHtml(scopeLabel())}</p>
          ${state.task?.scope?.center_edge ? `<p class="muted">${escapeHtml(state.task.scope.center_edge.source)} -> ${escapeHtml(state.task.scope.center_edge.target)}</p>` : ""}
          ${graphUrl ? `<a class="panel-link" href="${escapeHtml(graphUrl)}">Open graph context</a>` : '<p class="muted">No graph context recorded.</p>'}
        </div>
      </details>
      <details class="loop-collapsible">
        <summary><span>Rule / Ontology basis</span><span class="metric">basis</span></summary>
        <dl class="compact-meta evidence-card-body">
          <div><dt>Ontology links</dt><dd>${escapeHtml((state.task?.scope?.allowed_link_keys || ["link:employee:1:n:order"]).join(", "))}</dd></div>
          <div><dt>Approved-only</dt><dd>${state.task?.scope?.approved_only === false ? "off" : "on"}</dd></div>
          <div><dt>Review gate</dt><dd>${escapeHtml(state.task?.scope?.review_gate || "draft_only")}</dd></div>
        </dl>
      </details>
    </section>
  `;
}

function statusClass(status) {
  if (status === "approved") return "status-approved";
  if (status === "rejected" || status === "blocked") return "status-rejected";
  if (status === "needs_changes" || status === "needs_review") return "status-needs_review";
  if (status === "draft" || status === "proposed") return "status-draft";
  return "muted-pill";
}

function findingEvidenceSummary(finding) {
  const evidence = finding?.supporting_evidence || [];
  const graph = state.graphContext;
  if (graph?.approved !== false && graph?.center) {
    const handled = graph.relations_summary?.handled_orders;
    const returned = graph.relations_summary?.returned_orders ?? graph.edges?.length;
    return `${graph.center.label || graph.center.id} is connected to ${returned ?? 0}${handled && handled !== returned ? ` of ${handled}` : ""} approved Order relationship${returned === 1 ? "" : "s"} through the Employee-Order ontology link.`;
  }
  if (!evidence.length) return "No supporting evidence is attached to this finding yet.";
  const first = evidence[0];
  return first.summary || first.label || first.source_ref || `${evidence.length} evidence item${evidence.length === 1 ? "" : "s"} attached.`;
}

function isGenericScopedFinding(finding) {
  const title = String(finding?.title || "");
  const conclusion = String(finding?.conclusion || "");
  return title.includes("draft-only") || conclusion.includes("created from Graph Explorer evidence");
}

function answerTitle(finding) {
  if (finding.structured_answer?.title) return finding.structured_answer.title;
  return finding.title;
}

function answerConclusion(finding) {
  if (finding.structured_answer?.profile_summary) return finding.structured_answer.profile_summary;
  if (isGenericScopedFinding(finding)) {
    return "该历史推理结果缺少结构化画像，请重新执行推理以生成画像判断、关键事实、业务含义、证据边界和下一步验证。系统不会再把订单关系数量作为主结论展示。";
  }
  return finding.conclusion;
}

function structuredAnswer(finding) {
  return finding?.structured_answer || finding?.recommended_action?.structured_answer || null;
}

function renderKeyFacts(facts = []) {
  if (!facts.length) return '<p class="muted">No key facts recorded.</p>';
  return `
    <dl class="compact-meta profile-facts">
      ${facts
        .map(
          (fact) => `
            <div>
              <dt>${escapeHtml(fact.label || "Fact")}</dt>
              <dd>
                ${escapeHtml(fact.value || "-")}
                ${fact.source_ref ? `<span class="source-ref">${escapeHtml(fact.source_ref)}</span>` : ""}
              </dd>
            </div>
          `,
        )
        .join("")}
    </dl>
  `;
}

function renderBullets(items = [], emptyText = "No items recorded.") {
  if (!items.length) return `<p class="muted">${escapeHtml(emptyText)}</p>`;
  return `<ul class="profile-bullets">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderStructuredAnswer(profile) {
  if (!profile) return "";
  return `
    <section class="answer-profile-grid">
      <article class="detail-section">
        <h3>画像判断</h3>
        <p>${escapeHtml(profile.profile_summary || "")}</p>
      </article>
      <article class="detail-section">
        <h3>关键事实</h3>
        ${renderKeyFacts(profile.key_facts || [])}
      </article>
      <article class="detail-section">
        <h3>业务含义</h3>
        ${renderBullets(profile.business_interpretation || [])}
      </article>
      <article class="detail-section">
        <h3>证据边界</h3>
        ${renderBullets(profile.evidence_limits || [])}
      </article>
      <article class="detail-section">
        <h3>下一步验证</h3>
        ${renderBullets(profile.next_questions || [])}
      </article>
    </section>
  `;
}

function renderFinding() {
  const finding = state.selectedFinding;
  if (!finding) {
    const hasRun = Boolean(state.run);
    els.findingStatus.textContent = hasRun ? "no finding" : "not run";
    els.findingStatus.className = "status-pill muted-pill";
    els.findingDetail.innerHTML = `
      <section class="answer-empty">
        <h3>${hasRun ? "No finding generated" : "Not run yet"}</h3>
        <p class="muted">
          ${
            hasRun
              ? "This run did not produce a finding for the current scope. Review the trace, then rerun if the scope still needs an answer."
              : "Run this reasoning task to generate a draft answer with evidence and review status."
          }
        </p>
        <button class="secondary-action" type="button" data-run-empty="1">${hasRun ? "Rerun reasoning" : "Run reasoning"}</button>
      </section>
    `;
    els.findingDetail.querySelector("[data-run-empty]")?.addEventListener("click", () => runTask().catch((error) => showToast(error.message)));
    return;
  }
  els.findingStatus.textContent = `${finding.status} · v${finding.version}`;
  els.findingStatus.className = `status-pill ${statusClass(finding.status)}`;
  const evidence = finding.supporting_evidence || [];
  const firstEvidence = evidence[0] || {};
  const profile = structuredAnswer(finding);
  const displayTitle = answerTitle(finding);
  const displayConclusion = answerConclusion(finding);
  const findingUrl = `/findings.html?tenant=${encodeURIComponent(state.tenant)}&finding=${encodeURIComponent(finding.canonical_key)}`;
  const evidenceUrl = `#evidence-chain-panel`;
  const graphUrl = firstEvidence.url || state.task?.scope?.graph_url || "";
  const governanceStatus =
    finding.status === "approved"
      ? "Approved finding: this conclusion can be cited in the approved finding layer with task/run/evidence provenance. It still does not modify the canonical graph by itself."
      : "Draft finding pending human review: this reasoning artifact is not approved knowledge yet, is not written to the canonical graph, and cannot drive business action.";
  els.findingDetail.innerHTML = `
    <section class="answer-hero">
      <div>
        <p class="eyebrow">Current Answer</p>
        <h3>${escapeHtml(displayTitle)}</h3>
      </div>
      <span class="metric">${escapeHtml(confidenceText(finding.confidence))}</span>
    </section>
    <section class="answer-conclusion">
      <p>${escapeHtml(displayConclusion)}</p>
    </section>
    ${renderStructuredAnswer(profile)}
    <section class="answer-support">
      <div>
        <span class="hint">Key basis</span>
        <p>${escapeHtml(profile ? "结构化画像来自已批准图谱范围与受控 Northwind 聚合；关键事实列出 source_ref 以便追溯。" : findingEvidenceSummary(finding))}</p>
        ${firstEvidence.source_ref ? `<p class="source-ref">${escapeHtml(firstEvidence.source_ref)}</p>` : ""}
      </div>
      <div>
        <span class="hint">Next step</span>
        <p>${escapeHtml(profile?.next_questions?.[0] || "Review evidence, submit review, request more evidence, reject the draft, or rerun this scoped reasoning task.")}</p>
      </div>
    </section>
    <section class="governance-note">
      <div>
        <span class="hint">Governance status</span>
        <p>${escapeHtml(governanceStatus)}</p>
      </div>
      <div>
        <span class="hint">After review</span>
        <p>Review decisions are stored in audit trail / review history with reviewer, time, reason, and status transition. Approved findings enter the approved knowledge/finding layer and remain linked to this task, run, evidence, and ontology basis.</p>
      </div>
      <div>
        <span class="hint">Canonical boundary</span>
        <p>Approving this finding does not automatically change canonical ontology or graph. Structural facts, links, properties, classifications, or rules require a separate canonical write proposal and a stronger approval gate.</p>
      </div>
    </section>
    <section class="answer-actions">
      <a class="secondary-action answer-link" href="${escapeHtml(findingUrl)}">Open explanation</a>
      <a class="secondary-action answer-link" href="${escapeHtml(evidenceUrl)}">Open evidence chain</a>
      ${graphUrl ? `<a class="secondary-action answer-link" href="${escapeHtml(graphUrl)}">Open graph context</a>` : ""}
      <button class="secondary-action" type="button" data-review-answer="1">Submit review</button>
      <button class="secondary-action" type="button" data-followup-answer="1">Continue follow-up</button>
      <button class="secondary-action" type="button" data-needs-evidence="1">Request more evidence</button>
      <button class="secondary-action" type="button" data-rerun-answer="1">Rerun reasoning</button>
    </section>
    <section class="answer-secondary-grid">
      <div class="detail-section">
        <h3>Recommended action proposal</h3>
        <pre class="code-block">${escapeHtml(json(finding.recommended_action))}</pre>
      </div>
      <div class="detail-section">
        <h3>Counter evidence / limits</h3>
        <pre class="code-block">${escapeHtml(json(finding.counter_evidence))}</pre>
      </div>
    </section>
  `;
  els.findingStatus.textContent = statusText(finding.status, finding.version);
  els.findingDetail.querySelector("[data-rerun-answer]")?.addEventListener("click", () => runTask().catch((error) => showToast(error.message)));
  els.findingDetail.querySelector("[data-review-answer]")?.addEventListener("click", () => els.reviewReason.focus());
  els.findingDetail.querySelector("[data-followup-answer]")?.addEventListener("click", () => els.followupInput.focus());
  els.findingDetail.querySelector("[data-needs-evidence]")?.addEventListener("click", () => {
    els.reviewReason.value = "Needs more evidence before this finding can be approved.";
    els.reviewReason.focus();
  });
}

function renderTrace() {
  if (!state.run) {
    els.runTitle.textContent = "No run selected";
    els.traceBody.innerHTML = '<p class="muted">Run the task to generate a trace.</p>';
    return;
  }
  els.runTitle.textContent = state.run.run_key;
  const traceOutput = state.selectedFinding?.structured_answer
    ? {
        draft_only: state.run.output?.draft_only,
        finding_keys: state.run.output?.finding_keys || [],
        structured_answer: state.selectedFinding.structured_answer,
      }
    : state.run.output;
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
      <pre class="code-block">${escapeHtml(json(traceOutput))}</pre>
    </section>
  `;
}

function updateUrl({ keepGraphScope = false } = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set("tenant", state.tenant);
  url.searchParams.set("task", state.taskKey);
  if (!keepGraphScope) {
    [
      "source",
      "center_node",
      "center_edge_id",
      "center_edge_source",
      "center_edge_target",
      "question",
      "depth",
      "limit",
      "graph_url",
      "evidence_kind",
      "evidence_label",
      "evidence_summary",
      "evidence_source_ref",
      "ontology_link",
      "ontology_artifact",
      "autorun",
    ].forEach((key) => url.searchParams.delete(key));
  }
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

async function createScopedQuestion(question, overrides = {}) {
  const centerNode = overrides.center_node || els.centerNode?.value || state.task?.scope?.center_node || "Employee:4";
  const depth = Number(overrides.depth || els.depth?.value || state.task?.scope?.depth || 1);
  const limit = Number(overrides.limit || els.limit?.value || state.task?.scope?.node_limit || 200);
  updateGraphContextLink();
  const payload = {
    question,
    scope: {
      type: overrides.type || state.task?.scope?.type || "graph",
      center_node: centerNode,
      depth,
      limit,
      graph_url: els.graphContextLink?.getAttribute("href") || `/graph.html?tenant=${encodeURIComponent(state.tenant)}`,
    },
  };
  const data = await fetchJson(urlWithTenant("/api/reasoning/questions"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.taskKey = data.task.canonical_key;
  updateUrl();
  await loadTasks();
  showToast("Scoped question created in reasoning process");
  return data.task;
}

async function submitQuestion(event) {
  event.preventDefault();
  const question = els.questionInput.value.trim();
  if (!question) {
    showToast("Question is required");
    return;
  }
  await createScopedQuestion(question);
}

async function submitFollowup(event) {
  event.preventDefault();
  const question = els.followupInput.value.trim();
  if (!question) {
    showToast("Question is required");
    return;
  }
  await createScopedQuestion(question, {
    center_node: state.task?.scope?.center_node,
    depth: state.task?.scope?.depth,
    limit: state.task?.scope?.node_limit || state.task?.scope?.edge_limit,
    type: "follow_up",
  });
  els.followupInput.value = "";
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
els.runTaskInline?.addEventListener("click", () => runTask().catch((error) => showToast(error.message)));
els.questionForm?.addEventListener("submit", (event) => submitQuestion(event).catch((error) => showToast(error.message)));
els.followupForm?.addEventListener("submit", (event) => submitFollowup(event).catch((error) => showToast(error.message)));
els.centerNode?.addEventListener("input", updateGraphContextLink);
els.depth?.addEventListener("input", updateGraphContextLink);
els.limit?.addEventListener("input", updateGraphContextLink);
els.approveFinding.addEventListener("click", () => reviewFinding("approve").catch((error) => showToast(error.message)));
els.needsChangesFinding.addEventListener("click", () => reviewFinding("needs-changes").catch((error) => showToast(error.message)));
els.rejectFinding.addEventListener("click", () => reviewFinding("reject").catch((error) => showToast(error.message)));
els.commentFinding.addEventListener("click", () => reviewFinding("comment").catch((error) => showToast(error.message)));

loadTenants()
  .then(() => acceptGraphHandoffIfPresent())
  .then(() => loadTasks())
  .catch((error) => showToast(error.message));
