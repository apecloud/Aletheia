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
  const artifactsQ = useApiData("artifacts", [tenantId, {}], { fallback: [] });
  const graphProposedQ = useApiData("graphProposedElements", [tenantId, { limit: 100 }], { fallback: { runs: [], elements: [] } });
  const isStale = tasksQ.source === "live-stale";
  const isMock = tasksQ.source === "mock";
  const initialWorkspaceTab = (() => {
    try { return new URLSearchParams(location.search).get("workspace_tab") === "agents" ? "agents" : "workqueue"; }
    catch { return "workqueue"; }
  })();
  const [workspaceTab, setWorkspaceTab] = useStateWB(initialWorkspaceTab);
  const [selectedKey, setSelectedKey] = useStateWB(null);
  const [statusView, setStatusView] = useStateWB("active");
  const [search, setSearch] = useStateWB("");
  const [reviewNote, setReviewNote] = useStateWB("");
  const [reviewBusy, setReviewBusy] = useStateWB(false);
  const [reviewMessage, setReviewMessage] = useStateWB(null);

  const reviewItems = useMemoWB(() => buildWorkspaceReviewItems({
    tenantId,
    artifacts: artifactsQ.data || [],
    graphElements: graphProposedQ.data?.elements || [],
    agentRuns: agentRunsQ.data?.runs || [],
  }), [tenantId, artifactsQ.data, graphProposedQ.data, agentRunsQ.data]);

  const filtered = useMemoWB(() => {
    const q = search.trim().toLowerCase();
    return reviewItems.filter(c => {
      const statusOk =
        statusView === "all" ? true :
        statusView === "done" ? c.statusGroup === "done" :
        statusView === "blocked" ? c.statusGroup === "blocked" :
        c.statusGroup === "active";
      const textOk = !q || [c.title, c.id, c.summary, c.itemType, c.sourceRun, c.nextAction].join(" ").toLowerCase().includes(q);
      return statusOk && textOk;
    });
  }, [reviewItems, statusView, search]);

  useEffectWB(() => {
    if (!filtered.length) { setSelectedKey(null); return; }
    if (!selectedKey || !filtered.some(c => c.id === selectedKey)) {
      setSelectedKey(filtered[0].id);
    }
  }, [filtered.map(c => c.id).join("|")]);

  const selected = filtered.find(c => c.id === selectedKey) || filtered[0] || null;
  const counts = {
    active: reviewItems.filter(c => c.statusGroup === "active").length,
    blocked: reviewItems.filter(c => c.statusGroup === "blocked").length,
    done: reviewItems.filter(c => c.statusGroup === "done").length,
    all: reviewItems.length,
  };
  const agentRuns = agentRunsQ.data?.runs || [];

  function selectWorkspaceTab(tab) {
    setWorkspaceTab(tab);
    try {
      const url = new URL(location.href);
      url.searchParams.set("workspace_tab", tab);
      history.replaceState(null, "", url.toString());
    } catch {}
  }

  useEffectWB(() => {
    setReviewNote("");
    setReviewMessage(null);
  }, [selected?.id]);

  async function submitInlineReview(action) {
    if (!selected || !selected.reviewKind || reviewBusy) return;
    const requiresReason = action !== "approve";
    const reason = reviewNote.trim();
    if (requiresReason && !reason) {
      setReviewMessage({ tone: "changes", text: "Add a short review note before this action." });
      return;
    }
    const body = {
      reviewer: "Workspace",
      reason: reason || "Workspace inline review",
    };
    const actionMap = {
      ontology: {
        approve: "approve",
        reject: "reject",
        needs: "needs-changes",
        comment: "comment",
      },
      graph: {
        approve: "approve",
        reject: "reject",
        needs: "needs-evidence",
        comment: "comment",
      },
      autopilot_candidate: {
        approve: "approve",
        reject: "reject",
        needs: "needs-more-evidence",
        comment: "comment",
      },
      reasoning_finding: {
        approve: "approve",
        reject: "reject",
        needs: "needs-more-evidence",
        comment: "comment",
      },
    };
    const apiAction = actionMap[selected.reviewKind]?.[action];
    if (!apiAction) {
      setReviewMessage({ tone: "changes", text: "This work item does not support inline review yet." });
      return;
    }
    setReviewBusy(true);
    setReviewMessage(null);
    try {
      if (selected.reviewKind === "ontology") {
        await AL_API.reviewAction(selected.id, apiAction, body, tenantId);
      } else if (selected.reviewKind === "graph") {
        await AL_API.reviewGraphProposedElement(tenantId, selected.id, apiAction, body);
      } else if (selected.reviewKind === "autopilot_candidate") {
        await AL_API.reviewAutopilotCandidate(selected.id, apiAction, body, tenantId);
      } else if (selected.reviewKind === "reasoning_finding") {
        await AL_API.reviewFinding(selected.id, apiAction, body, tenantId);
      }
      setReviewNote("");
      setReviewMessage({ tone: "approved", text: `Review action recorded: ${inlineActionLabel(action)}.` });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setReviewMessage({ tone: "changes", text: err?.message || String(err) });
    } finally {
      setReviewBusy(false);
    }
  }

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className={"tab" + (workspaceTab === "workqueue" ? " active" : "")} onClick={() => selectWorkspaceTab("workqueue")}>Work Queue <span className="ct">{counts.active}</span></div>
          <div className={"tab" + (workspaceTab === "agents" ? " active" : "")} onClick={() => selectWorkspaceTab("agents")}>Agent <span className="ct">{agentRuns.length}</span></div>
        </div>
        <div className="spacer" />
        {isMock && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {tasksQ.loading && tasksQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Refresh</button>
        <a className="tool primary" href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>+ New Case</a>
      </div>

      {workspaceTab === "agents" ? (
        <AgentRunsWorkspace
          tenantId={tenantId}
          query={agentRunsQ}
          artifacts={artifactsQ.data || []}
          graphElements={graphProposedQ.data?.elements || []}
        />
      ) : (
      <div className="wb">
        <div className="col">
          <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Work Queue</div>
            <div style={{ marginTop: 5, color: "var(--text-dim)", fontSize: 12, lineHeight: 1.45 }}>
              Review objects waiting for a human decision: ontology proposals, proposed graph nodes or edges, and candidate findings.
            </div>
            <div style={{ position: "relative", marginTop: 10 }}>
              <input className="input" value={search} onChange={e => setSearch(e.target.value)}
                     placeholder="search review object, type, run…"
                     style={{ paddingLeft: 28 }} />
              <span style={{ position: "absolute", left: 9, top: 7, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>⌕</span>
            </div>
            <div className="tabs" style={{ marginTop: 10, height: 32, border: "1px solid var(--line)", display: "flex" }}>
              <div className={"tab" + (statusView === "active" ? " active" : "")} onClick={() => setStatusView("active")}>Active <span className="ct">{counts.active}</span></div>
              <div className={"tab" + (statusView === "blocked" ? " active" : "")} onClick={() => setStatusView("blocked")}>Blocked <span className="ct">{counts.blocked}</span></div>
              <div className={"tab" + (statusView === "done" ? " active" : "")} onClick={() => setStatusView("done")}>Done <span className="ct">{counts.done}</span></div>
              <div className={"tab" + (statusView === "all" ? " active" : "")} onClick={() => setStatusView("all")}>All <span className="ct">{counts.all}</span></div>
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <div className="stat"><span className="label">Active</span><span className="val mono">{counts.active}</span></div>
              <div className="stat"><span className="label">Blocked</span><span className="val mono">{counts.blocked}</span></div>
              <div className="stat"><span className="label">Done</span><span className="val mono">{counts.done}</span></div>
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <ApiStatus q={tasksQ} what="cases" />
            <ApiStatus q={artifactsQ} what="ontology proposals" />
            <ApiStatus q={graphProposedQ} what="proposed graph" />
            <ApiStatus q={agentRunsQ} what="agent findings" />
            <div className="artifact-list">
              {filtered.length === 0 && (
                <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                  No review objects match this view.
                </div>
              )}
              {filtered.map(c => (
                <div key={c.id}
                     className={`artifact-row ${caseTone(c.status)}` + (c.id === selectedKey ? " selected" : "")}
                     onClick={() => setSelectedKey(c.id)}>
                  <div className="ar-bar" />
                  <div className="ar-main">
                    <div className="ar-top">
                      <span className="type">{c.itemType}</span>
                      <span>·</span>
                      <span className="key">{c.id}</span>
                    </div>
                    <div className="ar-title">{c.title}</div>
                    <div className="ar-meta">
                      <span>{c.tenantId}</span>
                      <span>{c.sourceRun}</span>
                      <span>{c.updatedLabel}</span>
                    </div>
                  </div>
                  <div className="ar-right">{c.statusLabel}</div>
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
                  <span className="type">{selected.itemType}</span>
                  <span className="sep">/</span>
                  <span>{selected.id}</span>
                  <span className="sep">·</span>
                  <span>{selected.updatedLabel}</span>
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                    <Pill kind={selected.tone}>{selected.statusLabel}</Pill>
                    <Pill kind="accent">{selected.sourceRun}</Pill>
                  </span>
                </div>
                <h1>{selected.title}</h1>
                <p className="desc">{selected.summary}</p>
                <div className="row">
                  <div className="stat">
                    <span className="label">Tenant</span>
                    <span className="val mono">{selected.tenantId}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Status</span>
                    <span className="val" style={{ color: selected.statusGroup === "blocked" ? "var(--changes)" : "var(--approved)" }}>{selected.statusLabel}</span>
                  </div>
                  <div className="stat lg">
                    <span className="label">Next action</span>
                    <span className="val">{selected.nextAction}</span>
                  </div>
                </div>
              </div>

              <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
                <Panel eyebrow="Work routing" title="What needs attention" count={selected.statusLabel}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <CaseField label="Object type" value={selected.itemType} />
                    <CaseField label="Source / run" value={selected.sourceRun} />
                    <CaseField label="Confidence" value={selected.confidenceLabel} />
                    <CaseField label="Next action" value={selected.nextAction} />
                  </div>
                </Panel>

                <Panel eyebrow="Inline review" title="Review this proposed draft" count={selected.reviewKindLabel || selected.itemType} style={{ marginTop: 16 }}>
                  <InlineReviewDetails item={selected} />
                  <div style={{ marginTop: 12 }}>
                    <div className="eyebrow" style={{ marginBottom: 6 }}>Review note</div>
                    <textarea
                      className="input"
                      value={reviewNote}
                      onChange={e => setReviewNote(e.target.value)}
                      placeholder="Optional for approve. Required for reject, needs evidence/changes, or comment."
                      style={{ minHeight: 72, resize: "vertical", lineHeight: 1.45 }}
                    />
                  </div>
                  {reviewMessage && (
                    <div className={`pill ${reviewMessage.tone || ""}`} style={{ marginTop: 10, maxWidth: "100%", whiteSpace: "normal", lineHeight: 1.45 }}>
                      <span className="dot" />{reviewMessage.text}
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                    <button className="btn primary" disabled={reviewBusy} onClick={() => submitInlineReview("approve")}>Approve</button>
                    <button className="btn ghost" disabled={reviewBusy} onClick={() => submitInlineReview("needs")}>{selected.reviewKind === "ontology" ? "Needs changes" : "Needs evidence"}</button>
                    <button className="btn ghost" disabled={reviewBusy} onClick={() => submitInlineReview("reject")}>Reject</button>
                    <button className="btn ghost" disabled={reviewBusy} onClick={() => submitInlineReview("comment")}>Comment</button>
                  </div>
                  <div style={{ marginTop: 10, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5 }}>
                    Inline review calls the owning review gate. It does not create a second workflow and does not bypass canonical ontology, formal graph, or finding evidence boundaries.
                  </div>
                </Panel>

                <Panel eyebrow="Boundary" title="Where to continue" style={{ marginTop: 16 }}>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <a className="btn primary" href={selected.reviewHref}>{selected.reviewLabel}</a>
                    {selected.runHref && <a className="btn ghost" href={selected.runHref}>Open agent run</a>}
                  </div>
                  <div style={{ marginTop: 12, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5 }}>
                    Workspace routes the decision. Approval, rejection, and evidence review still happen in the owning review surface.
                  </div>
                </Panel>
              </div>
            </>
          )}
        </div>

        <div className="col inspector">
          <div className="section">
            <div className="section-head"><span>Queue summary</span><span className="ct">{reviewItems.length}</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">active</span><span className="track"><i style={{ width: pct(counts.active, counts.all) }} /></span><span className="num">{counts.active}</span></div>
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
                  <CaseField label="Status" value={selected.statusLabel} />
                  <CaseField label="Source" value={selected.sourceRun} />
                  <CaseField label="Boundary" value={selected.boundary} />
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
              <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed`}>Open proposed graph</a>
            </div>
          </div>
        </div>
      </div>
      )}
    </div>
  );
}

function AgentRunsWorkspace({ tenantId, query, artifacts = [], graphElements = [] }) {
  const data = query.data || { sessions: [], runs: [] };
  const runs = data.runs || [];
  const sessions = data.sessions || [];
  const initialAgentTab = (() => {
    try { return new URLSearchParams(location.search).get("agent_tab") === "autopilot" ? "autopilot" : "enrichment"; }
    catch { return "enrichment"; }
  })();
  const [agentTab, setAgentTab] = useStateWB(initialAgentTab);
  const [selectedRunKey, setSelectedRunKey] = useStateWB("");
  const [busy, setBusy] = useStateWB(false);
  const [message, setMessage] = useStateWB(null);
  const [agentParams, setAgentParams] = useStateWB({
    scope: defaultAgentScopeWB(tenantId),
    budget: "3",
    allowlist: "zenodo.org",
    cadence: "manual",
    customInterval: "60",
    stopCondition: "pause, stop, budget exhausted, or no new frontier",
    safety: "allowlist + proposed-only writes",
  });
  const filteredRuns = runs.filter(run => agentTab === "autopilot"
    ? run.kind === "autopilot_deep_reasoning"
    : run.kind === "web_enrichment_crawl" || run.kind === "iterative_graph_enrichment");
  const outputGroups = useMemoWB(() => buildAgentOutputGroups({
    agentTab,
    tenantId,
    runs: filteredRuns,
    artifacts,
    graphElements,
  }), [agentTab, tenantId, filteredRuns.map(run => run.run_key).join("|"), JSON.stringify(artifacts.map(a => [a.canonical_key, a.status, a.confidence])), JSON.stringify(graphElements.map(e => [e.element_key, e.element_type, e.status, e.confidence]))]);
  const selected = filteredRuns.find(run => run.run_key === selectedRunKey) || filteredRuns[0] || null;
  const session = sessions[0] || null;

  useEffectWB(() => {
    setAgentParams(prev => {
      if (prev.scope && prev.scope !== "—" && prev.scope !== "default") return prev;
      return { ...prev, scope: defaultAgentScopeWB(tenantId) };
    });
  }, [tenantId]);

  useEffectWB(() => {
    if (!session?.config) return;
    setAgentParams(prev => ({
      ...prev,
      allowlist: (session.config.allowed_domains || []).join(", ") || prev.allowlist,
      cadence: session.config.cadence || prev.cadence,
      customInterval: String(session.config.custom_interval_minutes || prev.customInterval || "60"),
      budget: String(session.config.max_frontier || prev.budget || "3"),
      stopCondition: session.config.stop_condition || prev.stopCondition,
    }));
  }, [session?.session_key, JSON.stringify(session?.config || {})]);

  useEffectWB(() => {
    if (!filteredRuns.length) {
      if (selectedRunKey) setSelectedRunKey("");
      return;
    }
    if (!filteredRuns.some(run => run.run_key === selectedRunKey)) setSelectedRunKey(filteredRuns[0].run_key);
  }, [agentTab, filteredRuns.map(run => run.run_key).join("|")]);

  const kindCounts = runs.reduce((acc, run) => {
    acc[run.kind] = (acc[run.kind] || 0) + 1;
    return acc;
  }, {});
  const pending = runs.reduce((acc, run) => acc + (run.elements || []).filter(item => ["draft", "candidate"].includes(item.status)).length, 0);
  const failed = runs.filter(run => run.status === "failed" || run.error).length;

  function updateAgentParam(key, value) {
    setAgentParams(prev => ({ ...prev, [key]: value }));
  }

  function selectAgentTab(tab) {
    setAgentTab(tab);
    try {
      const url = new URL(location.href);
      url.searchParams.set("agent_tab", tab);
      history.replaceState(null, "", url.toString());
    } catch {}
  }

  async function runOnce() {
    if (agentTab === "autopilot") {
      setBusy(true);
      setMessage(null);
      try {
        const body = {
          objective: agentParams.scope,
          created_by: "workspace",
          budget: Number(agentParams.budget) || 3,
          safety_profile: agentParams.safety,
        };
        let result = null;
        if (tenantId === "maritime-risk") result = await AL_API.runMaritimeRiskAutopilotPlaybook(tenantId, body);
        else if (tenantId === "creditcardfraud") result = await AL_API.runCreditcardfraudAutopilotPlaybook(tenantId, body);
        else result = await AL_API.createAutopilotSession(tenantId, body);
        setMessage({ kind: "ok", text: `Autopilot run queued: ${result?.session_key || result?.session?.session_key || result?.run_key || "created"}` });
        window.dispatchEvent(new CustomEvent("aletheia:retry"));
      } catch (err) {
        setMessage({ kind: "error", text: err.message || String(err) });
      } finally {
        setBusy(false);
      }
      return;
    }
    if (!session?.session_key) {
      setMessage({ kind: "error", text: "No continuous agent session available for this tenant." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const result = await AL_API.runContinuousEnrichmentCycle(tenantId, session.session_key, {
        created_by: "workspace",
        scope: agentParams.scope,
        budget: Number(agentParams.budget) || 3,
        allowlist: agentParams.allowlist,
        cadence: agentParams.cadence,
        custom_interval_minutes: Number(agentParams.customInterval) || 60,
        stop_condition: agentParams.stopCondition,
        trigger_autopilot: true,
      });
      const eventText = (result?.cycle?.events || []).map(event => event.type).join(", ");
      setMessage({ kind: "ok", text: `Run cycle completed: ${result?.cycle?.run_key || "completed"}${eventText ? ` · ${eventText}` : ""}` });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setMessage({ kind: "error", text: err.message || String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function toggleSession() {
    if (agentTab === "autopilot") {
      setMessage({ kind: "ok", text: "Autopilot is run-on-demand from Workspace; use Run once or open Reasoning for candidate review." });
      return;
    }
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

  async function saveAgentSettings() {
    if (!session?.session_key) return;
    setBusy(true);
    setMessage(null);
    try {
      await AL_API.configureContinuousEnrichmentSession(tenantId, session.session_key, {
        cadence: agentParams.cadence,
        custom_interval_minutes: Number(agentParams.customInterval) || 60,
        allowlist: agentParams.allowlist,
        budget: Number(agentParams.budget) || 3,
        stop_condition: agentParams.stopCondition,
      });
      setMessage({ kind: "ok", text: "Auto enriching settings saved." });
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
          <div className="eyebrow accent">Agent</div>
          <div style={{ marginTop: 5, color: "var(--text-dim)", fontSize: 12, lineHeight: 1.45 }}>
            Manage automatic reasoning and enrichment agents. Review stays in the owning surfaces.
          </div>
          <div className="tabs" style={{ marginTop: 10, height: 32, border: "1px solid var(--line)", display: "flex" }}>
            <div className={"tab" + (agentTab === "enrichment" ? " active" : "")} onClick={() => selectAgentTab("enrichment")}>Auto enriching <span className="ct">{(kindCounts.web_enrichment_crawl || 0) + (kindCounts.iterative_graph_enrichment || 0)}</span></div>
            <div className={"tab" + (agentTab === "autopilot" ? " active" : "")} onClick={() => selectAgentTab("autopilot")}>Autopilot reasoning <span className="ct">{kindCounts.autopilot_deep_reasoning || 0}</span></div>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <div className="stat"><span className="label">Runs</span><span className="val mono">{runs.length}</span></div>
            <div className="stat"><span className="label">Pending review</span><span className="val mono">{pending}</span></div>
            <div className="stat"><span className="label">Failed</span><span className="val mono">{failed}</span></div>
          </div>
        </div>
        <div style={{ flex: 1, overflow: "auto" }}>
          <ApiStatus q={query} what="agent runs" />
          <div style={{ padding: "10px 14px 4px", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Agent run history
          </div>
          <div className="artifact-list">
            {filteredRuns.map(run => (
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
            {filteredRuns.length === 0 && (
              <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                No {agentTab === "autopilot" ? "Autopilot reasoning" : "Auto enriching"} runs for tenant {tenantId}.
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="col" style={{ display: "flex", flexDirection: "column" }}>
        <div className="art-header">
          <div className="crumb">
            <span className="type">Agent</span>
            <span className="sep">/</span>
            <span>{agentTab === "autopilot" ? "Autopilot reasoning" : "Auto enriching"}</span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <Pill kind={agentTab === "autopilot" ? "accent" : (session?.status === "running" ? "approved" : "proposed")}>{agentTab === "autopilot" ? "run on demand" : (session?.status || "no session")}</Pill>
              <Pill kind="accent">{filteredRuns.length} runs</Pill>
            </span>
          </div>
          <h1>{agentTab === "autopilot" ? "Autopilot reasoning agent" : "Auto enriching agent"}</h1>
          <p className="desc">{agentTab === "autopilot"
            ? "Run bounded deep reasoning over the current tenant graph and send candidate findings to review."
            : "Keep the graph enrichment loop visible and bounded while generated objects route to review."}</p>
          <div className="row">
            <div className="stat">
              <span className="label">Scope</span>
              <span className="val mono">{compactTextWB(agentParams.scope, 42)}</span>
            </div>
            <div className="stat">
              <span className="label">Budget</span>
              <span className="val mono">{agentParams.budget}</span>
            </div>
            <div className="stat lg">
              <span className="label">Next action</span>
              <span className="val">{pending ? "Review generated proposals" : "Run once or inspect latest run"}</span>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
          <Panel eyebrow="Parameters" title="Agent settings">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 120px", gap: 10 }}>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Scope</div>
                <input className="input" value={agentParams.scope} onChange={e => updateAgentParam("scope", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Budget</div>
                <input className="input" value={agentParams.budget} onChange={e => updateAgentParam("budget", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Allowlist / safety</div>
                <input className="input" value={agentParams.allowlist} onChange={e => updateAgentParam("allowlist", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Cadence</div>
                <select className="select" value={agentParams.cadence} onChange={e => updateAgentParam("cadence", e.target.value)}>
                  <option value="manual">Manual</option>
                  <option value="hourly">Hourly</option>
                  <option value="daily">Daily</option>
                  <option value="custom">Custom interval</option>
                </select>
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Custom minutes</div>
                <input className="input" value={agentParams.customInterval} onChange={e => updateAgentParam("customInterval", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Stop condition</div>
                <input className="input" value={agentParams.stopCondition} onChange={e => updateAgentParam("stopCondition", e.target.value)} />
              </label>
            </div>
            <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
              {agentParams.safety} · next run {session?.config?.next_run_at || "manual"}
            </div>
          </Panel>

          <Panel eyebrow="Controls" title="Run and pause from Workspace" style={{ marginTop: 16 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn primary" disabled={busy || (agentTab !== "autopilot" && !session)} onClick={runOnce}>Run once</button>
              {agentTab !== "autopilot" && (
                <button className="btn" disabled={busy || !session} onClick={saveAgentSettings}>Save settings</button>
              )}
              <button className="btn ghost" disabled={busy || (agentTab !== "autopilot" && !session)} onClick={toggleSession}>
                {agentTab === "autopilot" ? "Pause / Resume" : (["running", "active", "idle"].includes(String(session?.status || "").toLowerCase()) ? "Pause" : "Resume")}
              </button>
              <a className="btn" href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed`}>Open results</a>
              <a className="btn ghost" href={`/?screen=workbench&tenant=${encodeURIComponent(tenantId)}&workspace_tab=agents&agent_tab=${encodeURIComponent(agentTab)}`}>Full run log</a>
              <a className="btn ghost" href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>Open reasoning</a>
            </div>
            {message && (
              <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: message.kind === "error" ? "var(--rejected)" : "var(--approved)" }}>
                {message.text}
              </div>
            )}
          </Panel>

          <AgentOutputsPanel groups={outputGroups} agentTab={agentTab} />

          {agentTab !== "autopilot" && session?.config?.latest_events?.length > 0 && (
            <Panel eyebrow="Agent chain" title="Latest enrichment events" style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {session.config.latest_events.slice(-6).reverse().map((event, index) => (
                  <div key={`${event.type}-${index}`} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)" }}>
                      {event.type} · {event.created_at ? String(event.created_at).slice(0, 19) : "no time"}
                    </div>
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      {compactTextWB(event.autopilot_session_key || event.run_key || event.reason || "event recorded", 130)}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {agentTab !== "autopilot" && session?.frontier?.length > 0 && (
            <Panel eyebrow="Frontier priority" title="Next enrichment seeds" count={`${session.frontier.length} queued`} style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {session.frontier.slice(0, 8).map((item, index) => (
                  <div key={`${item.key || item.target_key || index}`} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                      <strong style={{ fontSize: 12 }}>{compactTextWB(item.name || item.target_key || item.key, 80)}</strong>
                      <span className="chip">{item.source_kind || item.source || "frontier"}</span>
                    </div>
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      priority {item.priority ?? "—"} · {compactTextWB(item.reason || item.path || item.key, 140)}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          <Panel eyebrow="Agent run log" title={selected ? agentObjectiveWB(selected) : "No run selected"} count={selected?.status || "—"} style={{ marginTop: 16 }}>
            {selected ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <CaseField label="Kind" value={agentKindLabelWB(selected.kind)} />
                <CaseField label="Status" value={selected.status || selected.statusLabel} />
                <CaseField label="Started" value={selected.started_at ? String(selected.started_at).slice(0, 19) : "—"} />
                <CaseField label="Outputs" value={`${runOutputCountWB(selected)} generated · ${runSkippedCountWB(selected)} skipped`} />
                <CaseField label="Run key" value={compactTextWB(selected.run_key, 72)} />
                <CaseField label="Trace rows" value={(selected.trace || []).length} />
              </div>
            ) : (
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>Select a run from the list.</div>
            )}
          </Panel>

          {selected && (
            <Panel eyebrow="Agent run log" title="Timeline and trace" style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {(selected.trace || []).slice(0, 5).map((step, index) => (
                  <div key={index} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)" }}>
                      {compactTextWB(step.query || step.title || step.hypothesis_key || `step ${index + 1}`, 120)}
                    </div>
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      results {step.result_count ?? "—"} · extracted {(step.extracted_candidates || []).length || (step.reasoning_task_keys || []).length || 0}
                    </div>
                    {(step.query_terms || step.graph_context_used || step.path_context_used) && (
                      <div style={{ marginTop: 6, display: "grid", gap: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                        {step.query_terms && (
                          <div>query terms · {compactTextWB(flatQueryTermsWB(step.query_terms), 150)}</div>
                        )}
                        {step.graph_context_used && (
                          <div>graph context · {compactTextWB(graphContextLabelWB(step.graph_context_used), 150)}</div>
                        )}
                        {step.path_context_used && (
                          <div>path context · {compactTextWB(pathContextLabelWB(step.path_context_used), 150)}</div>
                        )}
                      </div>
                    )}
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

function buildWorkspaceReviewItems({ tenantId, artifacts, graphElements, agentRuns }) {
  const items = [];
  (artifacts || [])
    .filter(a => ["proposed", "changes"].includes(a.status) || ["draft", "needs_changes"].includes(a.rawStatus))
    .forEach(a => {
      const statusGroup = a.status === "changes" || a.rawStatus === "needs_changes" ? "blocked" : "active";
      items.push({
        id: a.canonical_key || a.id,
        title: a.title || a.key || a.canonical_key,
        summary: a.desc || "Ontology candidate needs review before it can become canonical.",
        itemType: "Ontology proposal",
        tenantId,
        statusGroup,
        statusLabel: a.rawStatus || a.status || "draft",
        tone: statusGroup === "blocked" ? "changes" : "proposed",
        sourceRun: a.agent || "ontology pipeline",
        updated: a.updated || a.created || "",
        updatedLabel: fmtCaseTime(a.updated || a.created),
        confidenceLabel: a.confidence != null ? String(a.confidence) : "—",
        nextAction: statusGroup === "blocked" ? "Resolve review feedback in Ontology" : "Approve, reject, or request changes in Ontology",
        reviewHref: `/?screen=ontology&tenant=${encodeURIComponent(tenantId)}&artifact=${encodeURIComponent(a.canonical_key || a.id || "")}`,
        reviewLabel: "Open in Ontology",
        reviewKind: "ontology",
        reviewKindLabel: "Ontology review gate",
        runHref: "",
        boundary: "review required before canonical ontology",
        raw: a,
      });
    });

  (graphElements || [])
    .filter(e => !["approved", "done"].includes(String(e.status || "").toLowerCase()))
    .forEach(e => {
      const type = String(e.element_type || e.type || "element").toLowerCase();
      const status = String(e.status || "draft").toLowerCase();
      const blocked = ["needs_evidence", "rejected", "failed", "blocked"].includes(status);
      const itemType =
        type.includes("edge") ? "Graph edge" :
        type.includes("finding") ? "Candidate finding" :
        type.includes("node") ? "Graph node" :
        "Graph proposal";
      const title = e.title || e.name || e.label || e.element_key || "Proposed graph element";
      items.push({
        id: e.element_key || e.canonical_key || title,
        title,
        summary: e.summary || e.description || "Proposed graph fact or finding waiting for graph review.",
        itemType,
        tenantId,
        statusGroup: blocked ? "blocked" : "active",
        statusLabel: status,
        tone: blocked ? "changes" : "proposed",
        sourceRun: compactTextWB(e.run_key || e.source || "proposed graph", 64),
        updated: e.updated_at || e.created_at || "",
        updatedLabel: fmtCaseTime(e.updated_at || e.created_at),
        confidenceLabel: e.confidence != null ? String(e.confidence) : "—",
        nextAction: blocked ? "Review evidence gap or rejection reason in Graph" : "Review proposed graph element",
        reviewHref: `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(e.element_key || "")}`,
        reviewLabel: "Open in Graph",
        reviewKind: "graph",
        reviewKindLabel: "Proposed graph review gate",
        runHref: e.run_key ? `/?screen=workbench&tenant=${encodeURIComponent(tenantId)}&workspace_tab=agents&agent_tab=enrichment&run_key=${encodeURIComponent(e.run_key)}` : "",
        boundary: "proposed graph space; formal graph writes disabled",
        raw: e,
      });
    });

  (agentRuns || []).forEach(run => {
    (run.elements || [])
      .filter(e => {
        const type = String(e.element_type || e.type || "").toLowerCase();
        const status = String(e.status || "").toLowerCase();
        return (type.includes("candidate") || type.includes("finding")) && ["draft", "candidate", "needs_evidence"].includes(status);
      })
      .forEach(e => {
        const status = String(e.status || "candidate").toLowerCase();
        const blocked = status === "needs_evidence";
        items.push({
          id: e.element_key || e.canonical_key || `${run.run_key}:finding`,
          title: e.title || e.name || e.label || e.summary || "Candidate finding",
          summary: e.summary || e.description || e.payload?.conclusion || "Candidate finding generated by an agent; approval remains human-gated.",
          itemType: "Candidate finding",
          tenantId,
          statusGroup: blocked ? "blocked" : "active",
          statusLabel: status,
          tone: blocked ? "changes" : "proposed",
          sourceRun: compactTextWB(run.run_key || run.kind || "autopilot", 64),
          updated: run.completed_at || run.started_at || "",
          updatedLabel: fmtCaseTime(run.completed_at || run.started_at),
          confidenceLabel: e.confidence != null ? String(e.confidence) : "—",
          nextAction: blocked ? "Add evidence or mark as rejected in Reasoning" : "Approve, reject, or request evidence in Reasoning",
          reviewHref: `/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}&active_tab=autopilot`,
          reviewLabel: "Open in Reasoning",
          reviewKind: "autopilot_candidate",
          reviewKindLabel: "Finding review gate",
          runHref: `/?screen=workbench&tenant=${encodeURIComponent(tenantId)}&workspace_tab=agents&agent_tab=autopilot&run_key=${encodeURIComponent(run.run_key || "")}`,
          boundary: "candidate finding; no automatic approval",
          raw: { ...e, source_run_key: run.run_key, source_kind: run.kind },
        });
      });
  });

  const seen = new Set();
  return items
    .filter(item => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    })
    .sort((a, b) => {
      const au = a.updated || "";
      const bu = b.updated || "";
      if (au !== bu) return au < bu ? 1 : -1;
      return a.title.localeCompare(b.title);
    });
}

function InlineReviewDetails({ item }) {
  const raw = item?.raw || {};
  const payload = raw.payload || {};
  const sourceUrl = raw.source_url || payload.source?.url || payload.source_url || "";
  const evidenceRefs = workspaceEvidenceRefs(item);
  const path = workspacePathLabel(item);
  const boundary = workspaceBoundaryFacts(item);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <CaseField label="Review gate" value={item.reviewKindLabel || item.reviewKind || "—"} />
        <CaseField label="Current status" value={item.statusLabel} />
        <CaseField label="Confidence" value={item.confidenceLabel} />
        <CaseField label="Source run" value={item.sourceRun} />
      </div>
      {path && <CaseField label="Path / relation" value={path} />}
      {sourceUrl && <CaseField label="Source URL" value={sourceUrl} />}
      {evidenceRefs.length > 0 && (
        <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Evidence refs</div>
          <div style={{ display: "grid", gap: 4 }}>
            {evidenceRefs.slice(0, 5).map((ref, idx) => (
              <div key={`${ref}-${idx}`} style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)", overflowWrap: "anywhere" }}>{ref}</div>
            ))}
            {evidenceRefs.length > 5 && <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>+{evidenceRefs.length - 5} more</div>}
          </div>
        </div>
      )}
      <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
        <div className="eyebrow" style={{ marginBottom: 6 }}>Write boundary</div>
        <div style={{ display: "grid", gap: 4, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)" }}>
          {boundary.map(line => <div key={line}>{line}</div>)}
        </div>
      </div>
    </div>
  );
}

function workspaceEvidenceRefs(item) {
  const raw = item?.raw || {};
  const payload = raw.payload || {};
  const chain = payload.evidence_chain || raw.evidence_chain || [];
  const refs = [
    ...(raw.evidence_refs || []),
    ...(raw.sourceRefs || []),
    ...(raw.source_refs || []),
    ...(payload.evidence_refs || []),
    ...(Array.isArray(chain) ? chain.map(e => e.source_url || e.source_ref || e.url || e.summary || e.metric).filter(Boolean) : []),
  ];
  return Array.from(new Set(refs.map(ref => String(ref)).filter(Boolean)));
}

function workspacePathLabel(item) {
  const raw = item?.raw || {};
  const payload = raw.payload || {};
  const source = payload.source_label || payload.source || payload.country || "";
  const relation = payload.relation || payload.edge_type || payload.path_relation || "";
  const target = payload.target_label || payload.target || payload.chokepoint || "";
  if (source || relation || target) {
    return [source, relation, target].filter(Boolean).join(" -> ");
  }
  const pathLabel = payload.path_label || payload.deep_graph_profile?.path_label || payload.analysis_path || raw.path_label;
  if (pathLabel) return Array.isArray(pathLabel) ? pathLabel.join(" -> ") : String(pathLabel);
  if (payload.metrics && payload.metrics.length) return `metrics: ${payload.metrics.join(", ")}`;
  return "";
}

function workspaceBoundaryFacts(item) {
  if (item?.reviewKind === "ontology") {
    return [
      "canonical ontology write: review gate only",
      "graph write: disabled",
      "target: ontology artifact review",
    ];
  }
  if (item?.reviewKind === "graph") {
    return [
      "canonical ontology write: false",
      "formal graph write: false",
      "target: proposed_graph_space",
    ];
  }
  return [
    "canonical write: false",
    "formal graph write: false",
    "target: candidate finding review gate",
  ];
}

function inlineActionLabel(action) {
  const labels = {
    approve: "approve",
    reject: "reject",
    needs: "needs evidence / changes",
    comment: "comment",
  };
  return labels[action] || action;
}

function buildAgentOutputGroups({ agentTab, tenantId, runs, artifacts, graphElements }) {
  const runElements = (runs || []).flatMap(run =>
    (run.elements || []).map(element => ({ ...element, source_run_key: run.run_key, source_kind: run.kind }))
  );
  if (agentTab === "autopilot") {
    const findings = dedupeAgentOutputs([
      ...runElements.filter(item => String(item.element_type || item.type || "").toLowerCase().includes("finding")),
      ...(graphElements || []).filter(item => String(item.element_type || item.type || "").toLowerCase().includes("finding")),
    ]);
    return [
      {
        key: "proposed_findings",
        title: "Proposed findings",
        description: "Candidate findings generated by reasoning runs, sorted by confidence and detail.",
        items: findings.filter(item => !["approved", "done"].includes(String(item.status || "").toLowerCase())),
        href: () => `/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}&active_tab=autopilot`,
      },
      {
        key: "findings",
        title: "Findings",
        description: "Reviewed or graph-level findings surfaced for comparison.",
        items: findings.filter(item => ["approved", "done"].includes(String(item.status || "").toLowerCase())),
        href: item => String(item.element_key || "").startsWith("proposed-graph:")
          ? `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`
          : `/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}&active_tab=autopilot`,
      },
    ];
  }
  const proposedGraphItems = [...runElements, ...(graphElements || [])];
  return [
    {
      key: "proposed_ontologies",
      title: "Proposed ontologies",
      description: "Ontology candidates and web enrichment proposals that still require ontology review.",
      items: dedupeAgentOutputs((artifacts || []).filter(artifact => {
        const status = String(artifact.rawStatus || artifact.status || "").toLowerCase();
        return !["approved", "done", "rejected"].includes(status);
      })),
      href: item => `/?screen=ontology&tenant=${encodeURIComponent(tenantId)}&artifact=${encodeURIComponent(item.canonical_key || item.id || "")}`,
    },
    {
      key: "nodes",
      title: "Proposed nodes",
      description: "Graph node proposals generated by enrichment runs.",
      items: dedupeAgentOutputs(proposedGraphItems.filter(item => String(item.element_type || item.type || "").toLowerCase().includes("node"))),
      href: item => `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`,
    },
    {
      key: "edges",
      title: "Proposed edges",
      description: "Graph edge proposals generated by enrichment runs.",
      items: dedupeAgentOutputs(proposedGraphItems.filter(item => String(item.element_type || item.type || "").toLowerCase().includes("edge"))),
      href: item => `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`,
    },
  ];
}

function dedupeAgentOutputs(items) {
  const map = new Map();
  (items || []).forEach(item => {
    const key = item.element_key || item.canonical_key || item.id || item.name || item.title;
    if (!key) return;
    const current = map.get(key);
    if (!current || agentOutputScore(item) > agentOutputScore(current)) map.set(key, item);
  });
  return Array.from(map.values()).sort((a, b) => {
    const confidence = agentOutputConfidence(b) - agentOutputConfidence(a);
    if (Math.abs(confidence) > 0.0001) return confidence;
    const detail = agentOutputDetailScore(b) - agentOutputDetailScore(a);
    if (detail !== 0) return detail;
    return String(b.created_at || b.updated || "").localeCompare(String(a.created_at || a.updated || ""));
  });
}

function agentOutputScore(item) {
  return agentOutputConfidence(item) * 1000 + agentOutputDetailScore(item);
}

function agentOutputConfidence(item) {
  const raw = item?.confidence;
  const parsed = typeof raw === "number" ? raw : parseFloat(raw || "0");
  return Number.isFinite(parsed) ? parsed : 0;
}

function agentOutputDetailScore(item) {
  const payload = item?.payload || {};
  const evidenceRefs = item?.evidence_refs || item?.sourceRefs || item?.source_refs || [];
  const evidenceChain = item?.evidence_chain || payload.evidence_chain || [];
  const path = payload.path || payload.path_nodes || payload.graph_path || payload.path_steps || [];
  const text = [item?.name, item?.title, item?.summary, item?.description, payload.conclusion, payload.summary, payload.path_label]
    .filter(Boolean).join(" ");
  return evidenceRefs.length * 8 + evidenceChain.length * 8 + path.length * 6 + Math.min(30, Math.floor(text.length / 40));
}

function AgentOutputsPanel({ groups, agentTab }) {
  return (
    <Panel eyebrow="Agent outputs" title={agentTab === "autopilot" ? "Findings ranked by confidence and detail" : "Enrichment proposals by type"} style={{ marginTop: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        {(groups || []).map(group => (
          <div key={group.key} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="eyebrow accent" style={{ flex: 1 }}>{group.title}</div>
              <span className="ct">{group.items.length}</span>
            </div>
            <div style={{ marginTop: 4, color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>{group.description}</div>
            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
              {group.items.slice(0, 6).map(item => (
                <a key={item.element_key || item.canonical_key || item.id || item.name}
                   href={group.href(item)}
                   style={{ display: "block", border: "1px solid var(--line-soft)", padding: "8px 9px", background: "var(--bg-1)", textDecoration: "none" }}>
                  <div style={{ color: "var(--text)", fontSize: 12, fontWeight: 600 }}>{compactTextWB(agentOutputTitle(item), 74)}</div>
                  <div style={{ marginTop: 5, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <span>conf {agentOutputConfidence(item).toFixed(2)}</span>
                    <span>detail {agentOutputDetailScore(item)}</span>
                    <span>{item.status || item.rawStatus || "draft"}</span>
                  </div>
                </a>
              ))}
              {group.items.length === 0 && (
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>No outputs in this category.</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function agentOutputTitle(item) {
  const payload = item?.payload || {};
  return item?.name || item?.title || item?.key || item?.canonical_key || item?.element_key || payload.conclusion || "Untitled output";
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

function flatQueryTermsWB(queryTerms) {
  if (!queryTerms || typeof queryTerms !== "object") return "";
  return Object.entries(queryTerms)
    .flatMap(([group, values]) => (Array.isArray(values) ? values.map(value => `${group}:${value}`) : []))
    .join(" · ");
}

function graphContextLabelWB(context) {
  if (!context || typeof context !== "object") return "";
  const neighbors = Array.isArray(context.neighbor_nodes) ? context.neighbor_nodes.filter(Boolean).join(" -> ") : "";
  const metrics = Array.isArray(context.metrics) ? context.metrics.filter(Boolean).join(", ") : "";
  return [context.frontier_name || context.frontier_key, context.relation, neighbors, metrics].filter(Boolean).join(" · ");
}

function pathContextLabelWB(context) {
  if (!context || typeof context !== "object") return "";
  const metrics = Array.isArray(context.metrics) ? context.metrics.filter(Boolean).join(", ") : "";
  return [context.path_label, context.source_label, context.relation, context.target_label, metrics].filter(Boolean).join(" · ");
}

function defaultAgentScopeWB(tenantId) {
  if (tenantId === "maritime-risk") return "maritime-risk chokepoint impact";
  if (tenantId === "creditcardfraud") return "creditcardfraud fraud risk patterns";
  if (tenantId === "us-iran-war") return "US-Iran conflict impact network";
  return tenantId || "tenant graph";
}

function compactTextWB(value, max = 80) {
  const text = String(value || "");
  if (text.length <= max) return text;
  return text.slice(0, Math.max(0, max - 1)).trimEnd() + "…";
}

Object.assign(window, { Workbench });
