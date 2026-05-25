/* Aletheia — lightweight Workspace / Work Queue */
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
    question: "Keep Workspace as a lightweight Work Queue while Ontology owns governance detail",
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
  const agentRunsQ = useApiData("agentRunsConsole", [tenantId, { limit: 20 }], { fallback: { sessions: [], runs: [] } });
  const isStale = tasksQ.source === "live-stale";
  const isMock = tasksQ.source === "mock";
  const initialWorkspaceTab = (() => {
    try { return new URLSearchParams(location.search).get("workspace_tab") === "agents" ? "agents" : "cases"; }
    catch { return "cases"; }
  })();
  const [workspaceTab, setWorkspaceTab] = useStateWB(initialWorkspaceTab);
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
  const agentRuns = agentRunsQ.data?.runs || [];

  function selectWorkspaceTab(tab, nextStatus = statusView) {
    setWorkspaceTab(tab);
    setStatusView(nextStatus);
    try {
      const url = new URL(location.href);
      url.searchParams.set("workspace_tab", tab);
      history.replaceState(null, "", url.toString());
    } catch {}
  }

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className={"tab" + (workspaceTab === "cases" && statusView === "open" ? " active" : "")} onClick={() => selectWorkspaceTab("cases", "open")}>Work Queue <span className="ct">{counts.open}</span></div>
          <div className={"tab" + (workspaceTab === "agents" ? " active" : "")} onClick={() => selectWorkspaceTab("agents")}>Agent Runs <span className="ct">{agentRuns.length}</span></div>
          <div className={"tab" + (workspaceTab === "cases" && statusView === "blocked" ? " active" : "")} onClick={() => selectWorkspaceTab("cases", "blocked")}>Blocked <span className="ct">{counts.blocked}</span></div>
          <div className={"tab" + (workspaceTab === "cases" && statusView === "done" ? " active" : "")} onClick={() => selectWorkspaceTab("cases", "done")}>Done <span className="ct">{counts.done}</span></div>
          <div className={"tab" + (workspaceTab === "cases" && statusView === "all" ? " active" : "")} onClick={() => selectWorkspaceTab("cases", "all")}>All <span className="ct">{counts.all}</span></div>
        </div>
        <div className="spacer" />
        {isMock && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {tasksQ.loading && tasksQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Refresh</button>
        <a className="tool primary" href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>+ New Case</a>
      </div>

      {workspaceTab === "agents" ? (
        <AgentRunsWorkspace tenantId={tenantId} query={agentRunsQ} />
      ) : (
      <div className="wb">
        <div className="col">
          <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Work Queue</div>
            <div style={{ marginTop: 5, color: "var(--text-dim)", fontSize: 12, lineHeight: 1.45 }}>
              Cases are business questions, findings, or review follow-ups that need human attention.
            </div>
            <div style={{ position: "relative", marginTop: 10 }}>
              <input className="input" value={search} onChange={e => setSearch(e.target.value)}
                     placeholder="search work item, owner, basis…"
                     style={{ paddingLeft: 28 }} />
              <span style={{ position: "absolute", left: 9, top: 7, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>⌕</span>
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <div className="stat"><span className="label">Active</span><span className="val mono">{counts.open}</span></div>
              <div className="stat"><span className="label">Blocked</span><span className="val mono">{counts.blocked}</span></div>
              <div className="stat"><span className="label">Done</span><span className="val mono">{counts.done}</span></div>
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <ApiStatus q={tasksQ} what="cases" />
            <div className="artifact-list">
              {filtered.length === 0 && (
                <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                  No work items match this view.
                </div>
              )}
              {filtered.map(c => (
                <div key={c.id}
                     className={`artifact-row ${caseTone(c.status)}` + (c.id === selectedKey ? " selected" : "")}
                     onClick={() => setSelectedKey(c.id)}>
                  <div className="ar-bar" />
                  <div className="ar-main">
                    <div className="ar-top">
                      <span className="type">WORK ITEM</span>
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
              Select a work item from the queue.
            </div>
          ) : (
            <>
              <div className="art-header">
                <div className="crumb">
                  <span className="type">Work item</span>
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
                <Panel eyebrow="Work routing" title="What needs attention" count={selected.status}>
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
            <div className="section-head"><span>Queue summary</span><span className="ct">{cases.length}</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">active</span><span className="track"><i style={{ width: pct(counts.open, counts.all) }} /></span><span className="num">{counts.open}</span></div>
              <div className="hbar"><span className="lbl">blocked</span><span className="track"><i style={{ width: pct(counts.blocked, counts.all) }} /></span><span className="num">{counts.blocked}</span></div>
              <div className="hbar"><span className="lbl">done</span><span className="track"><i style={{ width: pct(counts.done, counts.all) }} /></span><span className="num">{counts.done}</span></div>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Selected work item</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {selected ? (
                <>
                  <CaseField label="Work item key" value={selected.id} />
                  <CaseField label="Status" value={selected.status} />
                  <CaseField label="Basis" value={selected.basisLabel} />
                  <CaseField label="Blocker" value={selected.blocker || "none"} />
                </>
              ) : (
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>No work item selected.</div>
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
      )}
    </div>
  );
}

function AgentRunsWorkspace({ tenantId, query }) {
  const data = query.data || { sessions: [], runs: [] };
  const runs = data.runs || [];
  const sessions = data.sessions || [];
  const [selectedRunKey, setSelectedRunKey] = useStateWB("");
  const [busy, setBusy] = useStateWB(false);
  const [message, setMessage] = useStateWB(null);
  const selected = runs.find(run => run.run_key === selectedRunKey) || runs[0] || null;
  const session = sessions[0] || null;

  useEffectWB(() => {
    if (!runs.length) {
      if (selectedRunKey) setSelectedRunKey("");
      return;
    }
    if (!runs.some(run => run.run_key === selectedRunKey)) setSelectedRunKey(runs[0].run_key);
  }, [runs.map(run => run.run_key).join("|")]);

  const kindCounts = runs.reduce((acc, run) => {
    acc[run.kind] = (acc[run.kind] || 0) + 1;
    return acc;
  }, {});
  const pending = runs.reduce((acc, run) => acc + (run.elements || []).filter(item => ["draft", "candidate"].includes(item.status)).length, 0);
  const failed = runs.filter(run => run.status === "failed" || run.error).length;

  async function runOnce() {
    if (!session?.session_key) {
      setMessage({ kind: "error", text: "No continuous agent session available for this tenant." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await AL_API.runContinuousEnrichmentCycle(tenantId, session.session_key, { created_by: "workspace" });
      setMessage({ kind: "ok", text: `Run cycle queued: ${result?.run?.run_key || "completed"}` });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setMessage({ kind: "error", text: err.message || String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function toggleSession() {
    if (!session?.session_key) return;
    const action = ["running", "active", "idle"].includes(String(session.status || "").toLowerCase()) ? "pause" : "resume";
    setBusy(true);
    setMessage(null);
    try {
      await AL_API.updateContinuousEnrichmentSession(tenantId, session.session_key, action);
      setMessage({ kind: "ok", text: `Agent session ${action}d.` });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setMessage({ kind: "error", text: err.message || String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wb agent-workspace">
      <div className="col">
        <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
          <div className="eyebrow accent">Automatic agents</div>
          <div style={{ marginTop: 5, color: "var(--text-dim)", fontSize: 12, lineHeight: 1.45 }}>
            Manage crawl, graph enrichment, and reasoning runs from Workspace.
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <div className="stat"><span className="label">Runs</span><span className="val mono">{runs.length}</span></div>
            <div className="stat"><span className="label">Pending review</span><span className="val mono">{pending}</span></div>
            <div className="stat"><span className="label">Failed</span><span className="val mono">{failed}</span></div>
          </div>
        </div>
        <div style={{ flex: 1, overflow: "auto" }}>
          <ApiStatus q={query} what="agent runs" />
          <div className="artifact-list">
            {runs.map(run => (
              <div key={run.run_key}
                   className={`artifact-row ${runToneWB(run.status)}` + (selected?.run_key === run.run_key ? " selected" : "")}
                   onClick={() => setSelectedRunKey(run.run_key)}>
                <div className="ar-bar" />
                <div className="ar-main">
                  <div className="ar-top">
                    <span className="type">{agentKindLabelWB(run.kind)}</span>
                    <span>·</span>
                    <span className="key">{run.status || "unknown"}</span>
                  </div>
                  <div className="ar-title">{agentObjectiveWB(run)}</div>
                  <div className="ar-meta">
                    <span>{run.started_at ? String(run.started_at).slice(0, 16) : "no start time"}</span>
                    <span>{runOutputCountWB(run)} outputs</span>
                    <span>{runSkippedCountWB(run)} skipped</span>
                  </div>
                </div>
                <div className="ar-right">{runOutputCountWB(run)}</div>
              </div>
            ))}
            {runs.length === 0 && (
              <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                No automatic agent runs for tenant {tenantId}.
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="col" style={{ display: "flex", flexDirection: "column" }}>
        <div className="art-header">
          <div className="crumb">
            <span className="type">Agent Runs</span>
            <span className="sep">/</span>
            <span>{tenantId}</span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <Pill kind={session?.status === "running" ? "approved" : "proposed"}>{session?.status || "no session"}</Pill>
              <Pill kind="accent">{runs.length} runs</Pill>
            </span>
          </div>
          <h1>Automatic agent control</h1>
          <p className="desc">One lightweight place to monitor crawl, graph enrichment, and deep reasoning agents. Detailed evidence stays in Graph, Ontology, and Reasoning.</p>
          <div className="row">
            <div className="stat">
              <span className="label">Session</span>
              <span className="val mono">{compactTextWB(session?.session_key || "none", 42)}</span>
            </div>
            <div className="stat">
              <span className="label">Cycles</span>
              <span className="val mono">{session?.cycle_count || 0}</span>
            </div>
            <div className="stat lg">
              <span className="label">Next action</span>
              <span className="val">{pending ? "Review generated proposals" : "Run once or inspect latest run"}</span>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
          <Panel eyebrow="Controls" title="Keep the loop visible and bounded">
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn primary" disabled={busy || !session} onClick={runOnce}>Run once</button>
              <button className="btn ghost" disabled={busy || !session} onClick={toggleSession}>
                {["running", "active", "idle"].includes(String(session?.status || "").toLowerCase()) ? "Pause" : "Resume"}
              </button>
              <a className="btn" href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed`}>Open results</a>
              <a className="btn ghost" href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=runs`}>Full run log</a>
              <a className="btn ghost" href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>Open reasoning</a>
            </div>
            {message && (
              <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: message.kind === "error" ? "var(--rejected)" : "var(--approved)" }}>
                {message.text}
              </div>
            )}
          </Panel>

          <Panel eyebrow="Selected run" title={selected ? agentObjectiveWB(selected) : "No run selected"} count={selected?.status || "—"} style={{ marginTop: 16 }}>
            {selected ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <CaseField label="Kind" value={agentKindLabelWB(selected.kind)} />
                <CaseField label="Status" value={selected.status} />
                <CaseField label="Started" value={selected.started_at ? String(selected.started_at).slice(0, 19) : "—"} />
                <CaseField label="Outputs" value={`${runOutputCountWB(selected)} generated · ${runSkippedCountWB(selected)} skipped`} />
              </div>
            ) : (
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>Select a run from the list.</div>
            )}
          </Panel>

          {selected && (
            <Panel eyebrow="Compact timeline" title="What happened" style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {(selected.trace || []).slice(0, 5).map((step, index) => (
                  <div key={index} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)" }}>
                      {compactTextWB(step.query || step.title || step.hypothesis_key || `step ${index + 1}`, 120)}
                    </div>
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      results {step.result_count ?? "—"} · extracted {(step.extracted_candidates || []).length || (step.reasoning_task_keys || []).length || 0}
                    </div>
                  </div>
                ))}
                {!(selected.trace || []).length && (
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>No trace rows recorded.</div>
                )}
              </div>
            </Panel>
          )}
        </div>
      </div>

      <div className="col inspector">
        <div className="section">
          <div className="section-head"><span>Run summary</span><span className="ct">{runs.length}</span></div>
          <div className="section-body">
            <div className="hbar"><span className="lbl">web</span><span className="track"><i style={{ width: pct(kindCounts.web_enrichment_crawl || 0, runs.length) }} /></span><span className="num">{kindCounts.web_enrichment_crawl || 0}</span></div>
            <div className="hbar"><span className="lbl">graph</span><span className="track"><i style={{ width: pct(kindCounts.iterative_graph_enrichment || 0, runs.length) }} /></span><span className="num">{kindCounts.iterative_graph_enrichment || 0}</span></div>
            <div className="hbar"><span className="lbl">reasoning</span><span className="track"><i style={{ width: pct(kindCounts.autopilot_deep_reasoning || 0, runs.length) }} /></span><span className="num">{kindCounts.autopilot_deep_reasoning || 0}</span></div>
          </div>
        </div>
        <div className="section">
          <div className="section-head"><span>Write boundary</span></div>
          <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <CaseField label="Ontology candidates" value="review required" />
            <CaseField label="Graph facts" value="proposed graph space" />
            <CaseField label="Findings" value="candidate / reviewed only" />
            <CaseField label="Canonical writes" value="disabled" />
          </div>
        </div>
        <div className="section">
          <div className="section-head"><span>Quick links</span></div>
          <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed`}>Review pending graph proposals</a>
            <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=ontology&tenant=${encodeURIComponent(tenantId)}`}>Review ontology candidates</a>
            <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>Review candidate findings</a>
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

function agentKindLabelWB(kind) {
  const labels = {
    web_enrichment_crawl: "Crawl",
    iterative_graph_enrichment: "Graph enrich",
    autopilot_deep_reasoning: "Reasoning",
  };
  return labels[kind] || kind || "Agent";
}

function agentObjectiveWB(run) {
  if (!run) return "No run selected";
  if (run.objective) return compactTextWB(run.objective, 92);
  return compactTextWB(run.run_key || run.kind || "Agent run", 92);
}

function runOutputCountWB(run) {
  const counts = run?.counts || {};
  if (counts.returned != null) return counts.returned;
  if (counts.proposed != null) return counts.proposed;
  if (counts.candidate_findings != null) return counts.candidate_findings;
  if (counts.proposals != null) return counts.proposals;
  return (run?.elements || []).length;
}

function runSkippedCountWB(run) {
  const skipped = run?.skipped_sources || [];
  const counts = run?.counts || {};
  return skipped.length || counts.pruned || 0;
}

function runToneWB(status) {
  const normalized = String(status || "").toLowerCase();
  if (["completed", "done", "approved"].includes(normalized)) return "approved";
  if (["failed", "error", "rejected"].includes(normalized)) return "rejected";
  if (["running", "active", "draft"].includes(normalized)) return "proposed";
  return "changes";
}

function compactTextWB(value, max = 80) {
  const text = String(value || "");
  if (text.length <= max) return text;
  return text.slice(0, Math.max(0, max - 1)).trimEnd() + "…";
}

Object.assign(window, { Workbench });
