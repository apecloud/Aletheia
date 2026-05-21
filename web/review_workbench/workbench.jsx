/* Aletheia — lightweight Workspace / Case Inbox */
const { useState: useStateWB, useMemo: useMemoWB, useEffect: useEffectWB } = React;

const FALLBACK_CASES = [
  {
    canonical_key: "case:employee-order-basis",
    question: "Confirm the Employee 1:N Order basis used by active reasoning cases",
    status: "active",
    source: "reasoning",
    scope: {
      center_node: "Employee:4",
      allowed_link_keys: ["link:employee:1:n:order"],
      allowed_node_types: ["Employee", "Order"],
      review_gate: "draft_only",
    },
    updated_at: "2026-05-21 15:57",
  },
  {
    canonical_key: "case:employee-profile-answer",
    question: "Validate Employee profile answer quality before broader rollout",
    status: "completed",
    source: "manual",
    scope: {
      center_node: "Employee:5",
      allowed_link_keys: ["link:employee:1:n:order"],
      allowed_node_types: ["Employee", "Order"],
    },
    updated_at: "2026-05-21 14:20",
  },
  {
    canonical_key: "case:workspace-boundary",
    question: "Keep Workspace as a lightweight Case Inbox while Ontology owns governance detail",
    status: "active",
    source: "product",
    scope: {
      allowed_link_keys: ["link:employee:1:n:order"],
      allowed_node_types: ["Employee", "Order"],
    },
    updated_at: "2026-05-21 16:03",
  },
];

function Workbench({ data, tenant }) {
  const tenantId = tenant ? tenant.id : "default";
  const tasksQ = useApiData("reasoningTasks", [tenantId], { fallback: FALLBACK_CASES });
  const isStale = tasksQ.source === "live-stale";
  const isMock = tasksQ.source === "mock";
  const [selectedKey, setSelectedKey] = useStateWB(null);
  const [statusView, setStatusView] = useStateWB("all");
  const [search, setSearch] = useStateWB("");

  const cases = useMemoWB(() => {
    const live = (tasksQ.data || []).map(taskToCase);
    const base = live.length ? live : FALLBACK_CASES.map(taskToCase);
    return base.sort((a, b) => (b.updated || "").localeCompare(a.updated || ""));
  }, [tasksQ.data]);

  const filtered = useMemoWB(() => {
    const q = search.trim().toLowerCase();
    return cases.filter(c => {
      const statusOk =
        statusView === "all" ? true :
        statusView === "done" ? c.status === "done" :
        statusView === "blocked" ? c.status === "blocked" :
        c.status !== "done";
      const textOk = !q || [c.title, c.id, c.summary, c.owner, c.basisLabel].join(" ").toLowerCase().includes(q);
      return statusOk && textOk;
    });
  }, [cases, statusView, search]);

  useEffectWB(() => {
    if (!filtered.length) { setSelectedKey(null); return; }
    if (!selectedKey || !filtered.some(c => c.id === selectedKey)) {
      setSelectedKey(filtered[0].id);
    }
  }, [filtered.map(c => c.id).join("|")]);

  const selected = filtered.find(c => c.id === selectedKey) || filtered[0] || null;
  const counts = {
    open: cases.filter(c => c.status !== "done").length,
    blocked: cases.filter(c => c.status === "blocked").length,
    done: cases.filter(c => c.status === "done").length,
    all: cases.length,
  };

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className={"tab" + (statusView === "open" ? " active" : "")} onClick={() => setStatusView("open")}>Case Inbox <span className="ct">{counts.open}</span></div>
          <div className={"tab" + (statusView === "blocked" ? " active" : "")} onClick={() => setStatusView("blocked")}>Blocked <span className="ct">{counts.blocked}</span></div>
          <div className={"tab" + (statusView === "done" ? " active" : "")} onClick={() => setStatusView("done")}>Done <span className="ct">{counts.done}</span></div>
          <div className={"tab" + (statusView === "all" ? " active" : "")} onClick={() => setStatusView("all")}>All <span className="ct">{counts.all}</span></div>
        </div>
        <div className="spacer" />
        {isMock && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {tasksQ.loading && tasksQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Refresh</button>
        <a className="tool primary" href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>+ New Case</a>
      </div>

      <div className="wb">
        <div className="col">
          <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div style={{ position: "relative" }}>
              <input className="input" value={search} onChange={e => setSearch(e.target.value)}
                     placeholder="search case, owner, basis…"
                     style={{ paddingLeft: 28 }} />
              <span style={{ position: "absolute", left: 9, top: 7, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>⌕</span>
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <div className="stat"><span className="label">Open</span><span className="val mono">{counts.open}</span></div>
              <div className="stat"><span className="label">Blocked</span><span className="val mono">{counts.blocked}</span></div>
              <div className="stat"><span className="label">Done</span><span className="val mono">{counts.done}</span></div>
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <ApiStatus q={tasksQ} what="cases" />
            <div className="artifact-list">
              {filtered.length === 0 && (
                <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                  No cases match this view.
                </div>
              )}
              {filtered.map(c => (
                <div key={c.id}
                     className={`artifact-row ${caseTone(c.status)}` + (c.id === selectedKey ? " selected" : "")}
                     onClick={() => setSelectedKey(c.id)}>
                  <div className="ar-bar" />
                  <div className="ar-main">
                    <div className="ar-top">
                      <span className="type">CASE</span>
                      <span>·</span>
                      <span className="key">{c.id}</span>
                    </div>
                    <div className="ar-title">{c.title}</div>
                    <div className="ar-meta">
                      <span>{c.owner}</span>
                      <span>{c.basisLabel}</span>
                      <span>{c.updatedLabel}</span>
                    </div>
                  </div>
                  <div className="ar-right">{c.status}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="col" style={{ display: "flex", flexDirection: "column" }}>
          {!selected ? (
            <div style={{ flex: 1, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              Select a Case from the inbox.
            </div>
          ) : (
            <>
              <div className="art-header">
                <div className="crumb">
                  <span className="type">Case</span>
                  <span className="sep">/</span>
                  <span>{selected.id}</span>
                  <span className="sep">·</span>
                  <span>{selected.updatedLabel}</span>
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                    <Pill kind={caseTone(selected.status)}>{selected.status}</Pill>
                    <Pill kind="accent">{selected.source}</Pill>
                  </span>
                </div>
                <h1>{selected.title}</h1>
                <p className="desc">{selected.summary}</p>
                <div className="row">
                  <div className="stat">
                    <span className="label">Owner</span>
                    <span className="val mono">{selected.owner}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Current blocker</span>
                    <span className="val" style={{ color: selected.blocker ? "var(--changes)" : "var(--approved)" }}>{selected.blocker || "none"}</span>
                  </div>
                  <div className="stat lg">
                    <span className="label">Next action</span>
                    <span className="val">{selected.nextAction}</span>
                  </div>
                </div>
              </div>

              <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
                <Panel eyebrow="Case routing" title="What needs attention" count={selected.status}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <CaseField label="Scope" value={selected.scopeLabel} />
                    <CaseField label="Ontology basis" value={selected.basisLabel} />
                    <CaseField label="Owner" value={selected.owner} />
                    <CaseField label="Next action" value={selected.nextAction} />
                  </div>
                </Panel>

                <Panel eyebrow="Boundary" title="Where to continue" style={{ marginTop: 16 }}>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <a className="btn" href={selected.reasoningHref}>Open reasoning</a>
                    <a className="btn ghost" href={selected.ontologyHref}>Open ontology basis</a>
                  </div>
                </Panel>
              </div>
            </>
          )}
        </div>

        <div className="col inspector">
          <div className="section">
            <div className="section-head"><span>Inbox summary</span><span className="ct">{cases.length}</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">open</span><span className="track"><i style={{ width: pct(counts.open, counts.all) }} /></span><span className="num">{counts.open}</span></div>
              <div className="hbar"><span className="lbl">blocked</span><span className="track"><i style={{ width: pct(counts.blocked, counts.all) }} /></span><span className="num">{counts.blocked}</span></div>
              <div className="hbar"><span className="lbl">done</span><span className="track"><i style={{ width: pct(counts.done, counts.all) }} /></span><span className="num">{counts.done}</span></div>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Selected Case</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {selected ? (
                <>
                  <CaseField label="Case key" value={selected.id} />
                  <CaseField label="Status" value={selected.status} />
                  <CaseField label="Basis" value={selected.basisLabel} />
                  <CaseField label="Blocker" value={selected.blocker || "none"} />
                </>
              ) : (
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>No Case selected.</div>
              )}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Quick links</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>Open reasoning queue</a>
              <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=ontology&tenant=${encodeURIComponent(tenantId)}`}>Open ontology catalog</a>
              <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=quality&tenant=${encodeURIComponent(tenantId)}`}>Open quality checks</a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function taskToCase(task) {
  const scope = task.scope || {};
  const basisKey = firstBasisKey(scope) || "link:employee:1:n:order";
  const center = scope.center_node || scope.center_edge?.source || "tenant";
  const rawStatus = (task.status || "active").toLowerCase();
  const status =
    ["completed", "closed", "approved", "done"].includes(rawStatus) ? "done" :
    ["blocked", "rejected", "failed"].includes(rawStatus) ? "blocked" :
    "active";
  const title = task.question || task.name || "Untitled Case";
  return {
    id: task.canonical_key || task.id || title,
    title,
    status,
    source: task.source || scope.source || "reasoning",
    owner: ownerFor(task, scope),
    blocker: task.blocker || (status === "blocked" ? "attention required" : ""),
    summary: summaryFor(task, center, basisKey),
    nextAction: nextActionFor(status, task),
    scopeLabel: center,
    basisKey,
    basisLabel: ontologyBasisLabelWB(basisKey),
    updated: task.updated_at || task.updated || task.created_at || "",
    updatedLabel: fmtCaseTime(task.updated_at || task.updated || task.created_at),
    reasoningHref: `/?screen=reasoning&tenant=${encodeURIComponent(task.tenant_id || "default")}&task=${encodeURIComponent(task.canonical_key || task.id || "")}`,
    ontologyHref: `/?screen=ontology&tenant=${encodeURIComponent(task.tenant_id || "default")}&artifact=${encodeURIComponent(basisKey)}`,
  };
}

function ownerFor(task, scope) {
  if (task.owner) return task.owner;
  if ((task.source || scope.source) === "graph") return "Graph handoff";
  if ((task.source || scope.source) === "question_center") return "Question Center";
  return "Workspace";
}

function summaryFor(task, center, basisKey) {
  const output = task.latest_run?.output?.summary || task.finding?.conclusion || "";
  if (output) return output;
  return `Case centered on ${center}, using ${ontologyBasisLabelWB(basisKey)} as compact ontology basis.`;
}

function nextActionFor(status, task) {
  if (status === "done") return "Archive or open reasoning for follow-up";
  if (status === "blocked") return "Resolve blocker in the owning detail page";
  if (task.latest_run) return "Review latest reasoning result";
  return "Open reasoning and run the Case";
}

function firstBasisKey(scope) {
  if (scope.allowed_link_keys && scope.allowed_link_keys.length) return scope.allowed_link_keys[0];
  if (scope.ontology_basis) return scope.ontology_basis;
  if (scope.allowed_node_types && scope.allowed_node_types.length) {
    return "object:" + String(scope.allowed_node_types[0]).toLowerCase();
  }
  return null;
}

function ontologyBasisLabelWB(key) {
  const labels = {
    "link:employee:1:n:order": "Employee 1:N Order",
    "object:employee": "Employee",
    "object:order": "Order",
  };
  return labels[key] || key;
}

function caseTone(status) {
  return status === "done" ? "approved" : status === "blocked" ? "changes" : "proposed";
}

function fmtCaseTime(raw) {
  if (!raw) return "recent";
  return String(raw).slice(0, 16);
}

function pct(value, total) {
  if (!total) return "0%";
  return Math.max(3, Math.round((value / total) * 100)) + "%";
}

function CaseField({ label, value }) {
  return (
    <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
      <div className="eyebrow" style={{ marginBottom: 4 }}>{label}</div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text)" }}>{value || "—"}</div>
    </div>
  );
}

Object.assign(window, { Workbench });
