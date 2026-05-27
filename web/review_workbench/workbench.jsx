/* Aletheia — lightweight Workspace / Work Queue */
const { useState: useStateWB, useMemo: useMemoWB, useEffect: useEffectWB } = React;

function isZhWB(language) {
  return typeof isZhUI === "function" ? isZhUI(language) : String(language || "").startsWith("zh");
}

function tWB(language, en, zh) {
  return typeof tUI === "function" ? tUI(language, en, zh) : (isZhWB(language) ? zh : en);
}

function textWB(value, language) {
  if (typeof displayLabelUI === "function") return displayLabelUI(value, language);
  return typeof displayCountryCodesUI === "function" ? displayCountryCodesUI(value, language) : value;
}

function statusLabelWB(status, language) {
  if (!isZhWB(language)) return status || "—";
  const map = {
    active: "待处理",
    approved: "已批准",
    blocked: "已阻塞",
    candidate: "候选",
    changes: "需修改",
    completed: "已完成",
    done: "已完成",
    draft: "草稿",
    failed: "失败",
    needs_evidence: "需补证据",
    proposed: "待审核",
    rejected: "已拒绝",
    running: "运行中",
  };
  return map[String(status || "").toLowerCase()] || status || "—";
}

function itemTypeLabelWB(type, language) {
  if (!isZhWB(language)) return type || "Work item";
  const map = {
    "Ontology proposal": "本体候选",
    "Graph node": "图节点",
    "Graph edge": "图边",
    "Graph proposal": "图候选",
    "Candidate finding": "候选发现",
  };
  return map[type] || type || "工作项";
}

function nextActionLabelWB(text, language) {
  if (!isZhWB(language)) return text || "—";
  const map = {
    "Resolve review feedback in Ontology": "在本体审核中处理反馈",
    "Approve, reject, or request changes in Ontology": "在本体审核中批准、拒绝或要求修改",
    "Review evidence gap or rejection reason in Graph": "在图谱审核中处理证据缺口或拒绝原因",
    "Review proposed graph element": "审核候选图元素",
    "Add evidence or mark as rejected in Reasoning": "在推理中补充证据或标记拒绝",
    "Approve, reject, or request evidence in Reasoning": "在推理中批准、拒绝或要求补证据",
  };
  return map[text] || text || "—";
}

function reviewLinkLabelWB(label, language) {
  if (!isZhWB(language)) return label;
  const map = {
    "Open in Ontology": "打开本体审核",
    "Open in Graph": "打开图谱审核",
    "Open in Reasoning": "打开推理审核",
  };
  return map[label] || label;
}

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

function Workbench({ data, tenant, language }) {
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
      setReviewMessage({ tone: "changes", text: tWB(language, "Add a short review note before this action.", "执行该操作前请填写简短审核说明。") });
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
      setReviewMessage({ tone: "changes", text: tWB(language, "This work item does not support inline review yet.", "该工作项暂不支持工作台内审核。") });
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
      setReviewMessage({ tone: "approved", text: tWB(language, `Review action recorded: ${inlineActionLabel(action)}.`, `审核操作已记录：${inlineActionLabel(action, language)}。`) });
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
          <div className={"tab" + (workspaceTab === "workqueue" ? " active" : "")} onClick={() => selectWorkspaceTab("workqueue")}>{tWB(language, "Work Queue", "工作队列")} <span className="ct">{counts.active}</span></div>
          <div className={"tab" + (workspaceTab === "agents" ? " active" : "")} onClick={() => selectWorkspaceTab("agents")}>{tWB(language, "Agent", "Agent")} <span className="ct">{agentRuns.length}</span></div>
        </div>
        <div className="spacer" />
        {isMock && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tWB(language, "Mock fallback", "模拟回退")}</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tWB(language, "Stale · last fetch failed", "数据陈旧 · 最近拉取失败")}</span>}
        {tasksQ.loading && tasksQ.data && <span className="pill"><span className="dot" />{tWB(language, "Refreshing…", "刷新中…")}</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ {tWB(language, "Refresh", "刷新")}</button>
        <a className="tool primary" href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>+ {tWB(language, "New Case", "新问题")}</a>
      </div>

      {workspaceTab === "agents" ? (
        <AgentRunsWorkspace
          tenantId={tenantId}
          query={agentRunsQ}
          artifacts={artifactsQ.data || []}
          graphElements={graphProposedQ.data?.elements || []}
          language={language}
        />
      ) : (
      <div className="wb">
        <div className="col">
          <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">{tWB(language, "Work Queue", "工作队列")}</div>
            <div style={{ marginTop: 5, color: "var(--text-dim)", fontSize: 12, lineHeight: 1.45 }}>
              {tWB(language, "Review objects waiting for a human decision: ontology proposals, proposed graph nodes or edges, and candidate findings.", "集中处理等待人工决策的对象：本体候选、候选图节点/边，以及候选发现。")}
            </div>
            <div style={{ position: "relative", marginTop: 10 }}>
              <input className="input" value={search} onChange={e => setSearch(e.target.value)}
                     placeholder={tWB(language, "search review object, type, run…", "搜索审核对象、类型或运行…")}
                     style={{ paddingLeft: 28 }} />
              <span style={{ position: "absolute", left: 9, top: 7, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>⌕</span>
            </div>
            <div className="tabs" style={{ marginTop: 10, height: 32, border: "1px solid var(--line)", display: "flex" }}>
              <div className={"tab" + (statusView === "active" ? " active" : "")} onClick={() => setStatusView("active")}>{tWB(language, "Active", "待处理")} <span className="ct">{counts.active}</span></div>
              <div className={"tab" + (statusView === "blocked" ? " active" : "")} onClick={() => setStatusView("blocked")}>{tWB(language, "Blocked", "已阻塞")} <span className="ct">{counts.blocked}</span></div>
              <div className={"tab" + (statusView === "done" ? " active" : "")} onClick={() => setStatusView("done")}>{tWB(language, "Done", "已完成")} <span className="ct">{counts.done}</span></div>
              <div className={"tab" + (statusView === "all" ? " active" : "")} onClick={() => setStatusView("all")}>{tWB(language, "All", "全部")} <span className="ct">{counts.all}</span></div>
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <div className="stat"><span className="label">{tWB(language, "Active", "待处理")}</span><span className="val mono">{counts.active}</span></div>
              <div className="stat"><span className="label">{tWB(language, "Blocked", "已阻塞")}</span><span className="val mono">{counts.blocked}</span></div>
              <div className="stat"><span className="label">{tWB(language, "Done", "已完成")}</span><span className="val mono">{counts.done}</span></div>
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
                  {tWB(language, "No review objects match this view.", "当前视图没有匹配的审核对象。")}
                </div>
              )}
              {filtered.map(c => (
                <div key={c.id}
                     className={`artifact-row ${caseTone(c.status)}` + (c.id === selectedKey ? " selected" : "")}
                     onClick={() => setSelectedKey(c.id)}>
                  <div className="ar-bar" />
                  <div className="ar-main">
                    <div className="ar-top">
                      <span className="type">{itemTypeLabelWB(c.itemType, language)}</span>
                      <span>·</span>
                      <span className="key">{c.id}</span>
                    </div>
                    <div className="ar-title">{textWB(c.title, language)}</div>
                    <div className="ar-meta">
                      <span>{c.tenantId}</span>
                      <span>{textWB(c.sourceRun, language)}</span>
                      <span>{c.updatedLabel}</span>
                    </div>
                  </div>
                  <div className="ar-right">{statusLabelWB(c.statusLabel, language)}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="col" style={{ display: "flex", flexDirection: "column" }}>
          {!selected ? (
            <div style={{ flex: 1, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {tWB(language, "Select a work item from the queue.", "从队列中选择一个工作项。")}
            </div>
          ) : (
            <>
              <div className="art-header">
                <div className="crumb">
                  <span className="type">{itemTypeLabelWB(selected.itemType, language)}</span>
                  <span className="sep">/</span>
                  <span>{selected.id}</span>
                  <span className="sep">·</span>
                  <span>{selected.updatedLabel}</span>
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                    <Pill kind={selected.tone}>{statusLabelWB(selected.statusLabel, language)}</Pill>
                    <Pill kind="accent">{textWB(selected.sourceRun, language)}</Pill>
                  </span>
                </div>
                <h1>{textWB(selected.title, language)}</h1>
                <p className="desc">{textWB(selected.summary, language)}</p>
                <div className="row">
                  <div className="stat">
                    <span className="label">{tWB(language, "Tenant", "租户")}</span>
                    <span className="val mono">{selected.tenantId}</span>
                  </div>
                  <div className="stat">
                    <span className="label">{tWB(language, "Status", "状态")}</span>
                    <span className="val" style={{ color: selected.statusGroup === "blocked" ? "var(--changes)" : "var(--approved)" }}>{statusLabelWB(selected.statusLabel, language)}</span>
                  </div>
                  <div className="stat lg">
                    <span className="label">{tWB(language, "Next action", "下一步")}</span>
                    <span className="val">{nextActionLabelWB(selected.nextAction, language)}</span>
                  </div>
                </div>
              </div>

              <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
                <Panel eyebrow={tWB(language, "Work routing", "工作流转")} title={tWB(language, "What needs attention", "需要处理什么")} count={statusLabelWB(selected.statusLabel, language)}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <CaseField label={tWB(language, "Object type", "对象类型")} value={itemTypeLabelWB(selected.itemType, language)} />
                    <CaseField label={tWB(language, "Source / run", "来源 / 运行")} value={textWB(selected.sourceRun, language)} />
                    <CaseField label={tWB(language, "Confidence", "置信度")} value={selected.confidenceLabel} />
                    <CaseField label={tWB(language, "Next action", "下一步")} value={nextActionLabelWB(selected.nextAction, language)} />
                  </div>
                </Panel>

                <Panel eyebrow={tWB(language, "Inline review", "工作台内审核")} title={tWB(language, "Review this proposed draft", "审核这个候选草稿")} count={textWB(selected.reviewKindLabel || selected.itemType, language)} style={{ marginTop: 16 }}>
                  <InlineReviewDetails item={selected} language={language} />
                  <div style={{ marginTop: 12 }}>
                    <div className="eyebrow" style={{ marginBottom: 6 }}>{tWB(language, "Review note", "审核说明")}</div>
                    <textarea
                      className="input"
                      value={reviewNote}
                      onChange={e => setReviewNote(e.target.value)}
                      placeholder={tWB(language, "Optional for approve. Required for reject, needs evidence/changes, or comment.", "批准时可选；拒绝、要求补证据/修改或评论时必填。")}
                      style={{ minHeight: 72, resize: "vertical", lineHeight: 1.45 }}
                    />
                  </div>
                  {reviewMessage && (
                    <div className={`pill ${reviewMessage.tone || ""}`} style={{ marginTop: 10, maxWidth: "100%", whiteSpace: "normal", lineHeight: 1.45 }}>
                      <span className="dot" />{reviewMessage.text}
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                    <button className="btn primary" disabled={reviewBusy} onClick={() => submitInlineReview("approve")}>{tWB(language, "Approve", "批准")}</button>
                    <button className="btn ghost" disabled={reviewBusy} onClick={() => submitInlineReview("needs")}>{selected.reviewKind === "ontology" ? tWB(language, "Needs changes", "需要修改") : tWB(language, "Needs evidence", "需要补证据")}</button>
                    <button className="btn ghost" disabled={reviewBusy} onClick={() => submitInlineReview("reject")}>{tWB(language, "Reject", "拒绝")}</button>
                    <button className="btn ghost" disabled={reviewBusy} onClick={() => submitInlineReview("comment")}>{tWB(language, "Comment", "评论")}</button>
                  </div>
                  <div style={{ marginTop: 10, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5 }}>
                    {tWB(language, "Inline review calls the owning review gate. It does not create a second workflow and does not bypass canonical ontology, formal graph, or finding evidence boundaries.", "工作台内审核仍调用原有审核入口，不创建第二套流程，也不会绕过 canonical ontology、formal graph 或 finding 证据边界。")}
                  </div>
                </Panel>

                <Panel eyebrow={tWB(language, "Boundary", "边界")} title={tWB(language, "Where to continue", "去哪里继续")} style={{ marginTop: 16 }}>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <a className="btn primary" href={selected.reviewHref}>{reviewLinkLabelWB(selected.reviewLabel, language)}</a>
                    {selected.runHref && <a className="btn ghost" href={selected.runHref}>{tWB(language, "Open agent run", "打开 Agent 运行")}</a>}
                  </div>
                  <div style={{ marginTop: 12, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5 }}>
                    {tWB(language, "Workspace routes the decision. Approval, rejection, and evidence review still happen in the owning review surface.", "Workspace 只负责汇总入口；批准、拒绝和证据审核仍由所属审核面处理。")}
                  </div>
                </Panel>
              </div>
            </>
          )}
        </div>

        <div className="col inspector">
          <div className="section">
            <div className="section-head"><span>{tWB(language, "Queue summary", "队列汇总")}</span><span className="ct">{reviewItems.length}</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">{tWB(language, "active", "待处理")}</span><span className="track"><i style={{ width: pct(counts.active, counts.all) }} /></span><span className="num">{counts.active}</span></div>
              <div className="hbar"><span className="lbl">{tWB(language, "blocked", "已阻塞")}</span><span className="track"><i style={{ width: pct(counts.blocked, counts.all) }} /></span><span className="num">{counts.blocked}</span></div>
              <div className="hbar"><span className="lbl">{tWB(language, "done", "已完成")}</span><span className="track"><i style={{ width: pct(counts.done, counts.all) }} /></span><span className="num">{counts.done}</span></div>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>{tWB(language, "Selected work item", "选中的工作项")}</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {selected ? (
                <>
                  <CaseField label={tWB(language, "Work item key", "工作项键")} value={selected.id} />
                  <CaseField label={tWB(language, "Status", "状态")} value={statusLabelWB(selected.statusLabel, language)} />
                  <CaseField label={tWB(language, "Source", "来源")} value={textWB(selected.sourceRun, language)} />
                  <CaseField label={tWB(language, "Boundary", "边界")} value={textWB(selected.boundary, language)} />
                </>
              ) : (
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>{tWB(language, "No work item selected.", "未选择工作项。")}</div>
              )}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>{tWB(language, "Quick links", "快捷入口")}</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>{tWB(language, "Open reasoning queue", "打开推理队列")}</a>
              <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=ontology&tenant=${encodeURIComponent(tenantId)}`}>{tWB(language, "Open ontology catalog", "打开本体目录")}</a>
              <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed`}>{tWB(language, "Open proposed graph", "打开候选图谱")}</a>
            </div>
          </div>
        </div>
      </div>
      )}
    </div>
  );
}

function AgentRunsWorkspace({ tenantId, query, artifacts = [], graphElements = [], language }) {
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
      setMessage({ kind: "ok", text: tWB(language, "Auto enriching settings saved.", "自动信息增益设置已保存。") });
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
          <div className="eyebrow accent">{tWB(language, "Agent", "Agent")}</div>
          <div style={{ marginTop: 5, color: "var(--text-dim)", fontSize: 12, lineHeight: 1.45 }}>
            {tWB(language, "Manage automatic reasoning and enrichment agents. Review stays in the owning surfaces.", "管理自动推理和信息增益 Agent；审核仍由各自所属页面和 gate 处理。")}
          </div>
          <div className="tabs" style={{ marginTop: 10, height: 32, border: "1px solid var(--line)", display: "flex" }}>
            <div className={"tab" + (agentTab === "enrichment" ? " active" : "")} onClick={() => selectAgentTab("enrichment")}>{tWB(language, "Auto enriching", "自动信息增益")} <span className="ct">{(kindCounts.web_enrichment_crawl || 0) + (kindCounts.iterative_graph_enrichment || 0)}</span></div>
            <div className={"tab" + (agentTab === "autopilot" ? " active" : "")} onClick={() => selectAgentTab("autopilot")}>{tWB(language, "Autopilot reasoning", "自动推理")} <span className="ct">{kindCounts.autopilot_deep_reasoning || 0}</span></div>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <div className="stat"><span className="label">{tWB(language, "Runs", "运行")}</span><span className="val mono">{runs.length}</span></div>
            <div className="stat"><span className="label">{tWB(language, "Pending review", "待审核")}</span><span className="val mono">{pending}</span></div>
            <div className="stat"><span className="label">{tWB(language, "Failed", "失败")}</span><span className="val mono">{failed}</span></div>
          </div>
        </div>
        <div style={{ flex: 1, overflow: "auto" }}>
          <ApiStatus q={query} what={tWB(language, "agent runs", "Agent 运行")} />
          <div style={{ padding: "10px 14px 4px", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            {tWB(language, "Agent run history", "Agent 运行历史")}
          </div>
          <div className="artifact-list">
            {filteredRuns.map(run => (
              <div key={run.run_key}
                   className={`artifact-row ${runToneWB(run.status)}` + (selected?.run_key === run.run_key ? " selected" : "")}
                   onClick={() => setSelectedRunKey(run.run_key)}>
                <div className="ar-bar" />
                <div className="ar-main">
                  <div className="ar-top">
                    <span className="type">{agentKindLabelWB(run.kind, language)}</span>
                    <span>·</span>
                    <span className="key">{statusLabelWB(run.status || "unknown", language)}</span>
                  </div>
                  <div className="ar-title">{textWB(agentObjectiveWB(run), language)}</div>
                  <div className="ar-meta">
                    <span>{run.started_at ? String(run.started_at).slice(0, 16) : tWB(language, "no start time", "无开始时间")}</span>
                    <span>{runOutputCountWB(run)} {tWB(language, "outputs", "输出")}</span>
                    <span>{runSkippedCountWB(run)} {tWB(language, "skipped", "跳过")}</span>
                  </div>
                </div>
                <div className="ar-right">{runOutputCountWB(run)}</div>
              </div>
            ))}
            {filteredRuns.length === 0 && (
              <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                {tWB(language, "No", "没有")}{agentTab === "autopilot" ? tWB(language, " Autopilot reasoning", "自动推理") : tWB(language, " Auto enriching", "自动信息增益")}{tWB(language, " runs for tenant ", "运行，租户 ")}{tenantId}.
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="col" style={{ display: "flex", flexDirection: "column" }}>
        <div className="art-header">
          <div className="crumb">
            <span className="type">{tWB(language, "Agent", "Agent")}</span>
            <span className="sep">/</span>
            <span>{agentTab === "autopilot" ? tWB(language, "Autopilot reasoning", "自动推理") : tWB(language, "Auto enriching", "自动信息增益")}</span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <Pill kind={agentTab === "autopilot" ? "accent" : (session?.status === "running" ? "approved" : "proposed")}>{agentTab === "autopilot" ? tWB(language, "run on demand", "按需运行") : statusLabelWB(session?.status || "no session", language)}</Pill>
              <Pill kind="accent">{filteredRuns.length} {tWB(language, "runs", "次运行")}</Pill>
            </span>
          </div>
          <h1>{agentTab === "autopilot" ? tWB(language, "Autopilot reasoning agent", "自动推理 Agent") : tWB(language, "Auto enriching agent", "自动信息增益 Agent")}</h1>
          <p className="desc">{agentTab === "autopilot"
            ? tWB(language, "Run bounded deep reasoning over the current tenant graph and send candidate findings to review.", "在当前租户图谱上执行有边界的深度推理，并把候选 findings 送入审核。")
            : tWB(language, "Keep the graph enrichment loop visible and bounded while generated objects route to review.", "持续展示并约束图谱信息增益循环，生成对象进入审核流程。")}</p>
          <div className="row">
            <div className="stat">
              <span className="label">{tWB(language, "Scope", "范围")}</span>
              <span className="val mono">{compactTextWB(agentParams.scope, 42)}</span>
            </div>
            <div className="stat">
              <span className="label">{tWB(language, "Budget", "预算")}</span>
              <span className="val mono">{agentParams.budget}</span>
            </div>
            <div className="stat lg">
              <span className="label">{tWB(language, "Next action", "下一步")}</span>
              <span className="val">{pending ? tWB(language, "Review generated proposals", "审核生成的候选对象") : tWB(language, "Run once or inspect latest run", "运行一次或查看最近运行")}</span>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
          <Panel eyebrow={tWB(language, "Parameters", "参数")} title={tWB(language, "Agent settings", "Agent 设置")}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 120px", gap: 10 }}>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Scope", "范围")}</div>
                <input className="input" value={agentParams.scope} onChange={e => updateAgentParam("scope", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Budget", "预算")}</div>
                <input className="input" value={agentParams.budget} onChange={e => updateAgentParam("budget", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Allowlist / safety", "允许域名 / 安全")}</div>
                <input className="input" value={agentParams.allowlist} onChange={e => updateAgentParam("allowlist", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Cadence", "频率")}</div>
                <select className="select" value={agentParams.cadence} onChange={e => updateAgentParam("cadence", e.target.value)}>
                  <option value="manual">{tWB(language, "Manual", "手动")}</option>
                  <option value="hourly">{tWB(language, "Hourly", "每小时")}</option>
                  <option value="daily">{tWB(language, "Daily", "每天")}</option>
                  <option value="custom">{tWB(language, "Custom interval", "自定义间隔")}</option>
                </select>
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Custom minutes", "自定义分钟数")}</div>
                <input className="input" value={agentParams.customInterval} onChange={e => updateAgentParam("customInterval", e.target.value)} />
              </label>
              <label>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Stop condition", "停止条件")}</div>
                <input className="input" value={agentParams.stopCondition} onChange={e => updateAgentParam("stopCondition", e.target.value)} />
              </label>
            </div>
            <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
              {agentParams.safety} · {tWB(language, "next run", "下次运行")} {session?.config?.next_run_at || tWB(language, "manual", "手动")}
            </div>
          </Panel>

          <Panel eyebrow={tWB(language, "Controls", "控制")} title={tWB(language, "Run and pause from Workspace", "在 Workspace 中运行和暂停")} style={{ marginTop: 16 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn primary" disabled={busy || (agentTab !== "autopilot" && !session)} onClick={runOnce}>{tWB(language, "Run once", "运行一次")}</button>
              {agentTab !== "autopilot" && (
                <button className="btn" disabled={busy || !session} onClick={saveAgentSettings}>{tWB(language, "Save settings", "保存设置")}</button>
              )}
              <button className="btn ghost" disabled={busy || (agentTab !== "autopilot" && !session)} onClick={toggleSession}>
                {agentTab === "autopilot" ? tWB(language, "Pause / Resume", "暂停 / 恢复") : (["running", "active", "idle"].includes(String(session?.status || "").toLowerCase()) ? tWB(language, "Pause", "暂停") : tWB(language, "Resume", "恢复"))}
              </button>
              <a className="btn" href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed`}>{tWB(language, "Open results", "打开结果")}</a>
              <a className="btn ghost" href={`/?screen=workbench&tenant=${encodeURIComponent(tenantId)}&workspace_tab=agents&agent_tab=${encodeURIComponent(agentTab)}`}>{tWB(language, "Full run log", "完整运行日志")}</a>
              <a className="btn ghost" href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>{tWB(language, "Open reasoning", "打开推理")}</a>
            </div>
            {message && (
              <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: message.kind === "error" ? "var(--rejected)" : "var(--approved)" }}>
                {message.text}
              </div>
            )}
          </Panel>

          <AgentOutputsPanel groups={outputGroups} agentTab={agentTab} language={language} />

          {agentTab !== "autopilot" && session?.config?.latest_events?.length > 0 && (
            <Panel eyebrow={tWB(language, "Agent chain", "Agent 链路")} title={tWB(language, "Latest enrichment events", "最近信息增益事件")} style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {session.config.latest_events.slice(-6).reverse().map((event, index) => (
                  <div key={`${event.type}-${index}`} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)" }}>
                      {event.type} · {event.created_at ? String(event.created_at).slice(0, 19) : tWB(language, "no time", "无时间")}
                    </div>
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      {compactTextWB(event.autopilot_session_key || event.run_key || event.reason || tWB(language, "event recorded", "事件已记录"), 130)}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {agentTab !== "autopilot" && session?.frontier?.length > 0 && (
            <Panel eyebrow={tWB(language, "Frontier priority", "Frontier 优先级")} title={tWB(language, "Next enrichment seeds", "下一批信息增益种子")} count={`${session.frontier.length} ${tWB(language, "queued", "排队中")}`} style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {session.frontier.slice(0, 8).map((item, index) => (
                  <div key={`${item.key || item.target_key || index}`} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                      <strong style={{ fontSize: 12 }}>{compactTextWB(item.name || item.target_key || item.key, 80)}</strong>
                      <span className="chip">{item.source_kind || item.source || "frontier"}</span>
                    </div>
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      {tWB(language, "priority", "优先级")} {item.priority ?? "—"} · {compactTextWB(textWB(item.reason || item.path || item.key, language), 140)}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          <Panel eyebrow={tWB(language, "Agent run log", "Agent 运行日志")} title={selected ? textWB(agentObjectiveWB(selected), language) : tWB(language, "No run selected", "未选择运行")} count={statusLabelWB(selected?.status || "—", language)} style={{ marginTop: 16 }}>
            {selected ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <CaseField label={tWB(language, "Kind", "类型")} value={agentKindLabelWB(selected.kind, language)} />
                <CaseField label={tWB(language, "Status", "状态")} value={statusLabelWB(selected.status || selected.statusLabel, language)} />
                <CaseField label={tWB(language, "Started", "开始时间")} value={selected.started_at ? String(selected.started_at).slice(0, 19) : "—"} />
                <CaseField label={tWB(language, "Outputs", "输出")} value={`${runOutputCountWB(selected)} ${tWB(language, "generated", "生成")} · ${runSkippedCountWB(selected)} ${tWB(language, "skipped", "跳过")}`} />
                <CaseField label={tWB(language, "Run key", "运行键")} value={compactTextWB(selected.run_key, 72)} />
                <CaseField label={tWB(language, "Trace rows", "Trace 行")} value={(selected.trace || []).length} />
              </div>
            ) : (
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>{tWB(language, "Select a run from the list.", "从列表中选择一次运行。")}</div>
            )}
          </Panel>

          {selected && (
            <Panel eyebrow={tWB(language, "Agent run log", "Agent 运行日志")} title={tWB(language, "Timeline and trace", "时间线与 trace")} style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {(selected.trace || []).slice(0, 5).map((step, index) => (
                  <div key={index} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)" }}>
                      {compactTextWB(step.query || step.title || step.hypothesis_key || `step ${index + 1}`, 120)}
                    </div>
                    <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      {tWB(language, "results", "结果")} {step.result_count ?? "—"} · {tWB(language, "extracted", "抽取")} {(step.extracted_candidates || []).length || (step.reasoning_task_keys || []).length || 0}
                    </div>
                    {(step.query_terms || step.graph_context_used || step.path_context_used) && (
                      <div style={{ marginTop: 6, display: "grid", gap: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                        {step.query_terms && (
                          <div>{tWB(language, "query terms", "查询词")} · {compactTextWB(flatQueryTermsWB(step.query_terms), 150)}</div>
                        )}
                        {step.graph_context_used && (
                          <div>{tWB(language, "graph context", "图谱上下文")} · {compactTextWB(textWB(graphContextLabelWB(step.graph_context_used), language), 150)}</div>
                        )}
                        {step.path_context_used && (
                          <div>{tWB(language, "path context", "路径上下文")} · {compactTextWB(textWB(pathContextLabelWB(step.path_context_used), language), 150)}</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                {!(selected.trace || []).length && (
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>{tWB(language, "No trace rows recorded.", "未记录 trace 行。")}</div>
                )}
              </div>
            </Panel>
          )}
        </div>
      </div>

      <div className="col inspector">
        <div className="section">
          <div className="section-head"><span>{tWB(language, "Run summary", "运行汇总")}</span><span className="ct">{runs.length}</span></div>
          <div className="section-body">
            <div className="hbar"><span className="lbl">web</span><span className="track"><i style={{ width: pct(kindCounts.web_enrichment_crawl || 0, runs.length) }} /></span><span className="num">{kindCounts.web_enrichment_crawl || 0}</span></div>
            <div className="hbar"><span className="lbl">graph</span><span className="track"><i style={{ width: pct(kindCounts.iterative_graph_enrichment || 0, runs.length) }} /></span><span className="num">{kindCounts.iterative_graph_enrichment || 0}</span></div>
            <div className="hbar"><span className="lbl">reasoning</span><span className="track"><i style={{ width: pct(kindCounts.autopilot_deep_reasoning || 0, runs.length) }} /></span><span className="num">{kindCounts.autopilot_deep_reasoning || 0}</span></div>
          </div>
        </div>
        <div className="section">
          <div className="section-head"><span>{tWB(language, "Write boundary", "写入边界")}</span></div>
          <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <CaseField label={tWB(language, "Ontology candidates", "本体候选")} value={tWB(language, "review required", "需要审核")} />
            <CaseField label={tWB(language, "Graph facts", "图事实")} value={tWB(language, "proposed graph space", "候选图空间")} />
            <CaseField label={tWB(language, "Findings", "发现")} value={tWB(language, "candidate / reviewed only", "仅候选 / 已审核")} />
            <CaseField label={tWB(language, "Canonical writes", "正式写入")} value={tWB(language, "disabled", "禁用")} />
          </div>
        </div>
        <div className="section">
          <div className="section-head"><span>{tWB(language, "Quick links", "快捷入口")}</span></div>
          <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed`}>{tWB(language, "Review pending graph proposals", "审核待处理图候选")}</a>
            <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=ontology&tenant=${encodeURIComponent(tenantId)}`}>{tWB(language, "Review ontology candidates", "审核本体候选")}</a>
            <a className="btn ghost" style={{ justifyContent: "flex-start" }} href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}`}>{tWB(language, "Review candidate findings", "审核候选 findings")}</a>
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

function InlineReviewDetails({ item, language }) {
  const raw = item?.raw || {};
  const payload = raw.payload || {};
  const sourceUrl = raw.source_url || payload.source?.url || payload.source_url || "";
  const evidenceRefs = workspaceEvidenceRefs(item);
  const path = workspacePathLabel(item);
  const boundary = workspaceBoundaryFacts(item);
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <CaseField label={tWB(language, "Review gate", "审核入口")} value={item.reviewKindLabel || item.reviewKind || "—"} />
        <CaseField label={tWB(language, "Current status", "当前状态")} value={statusLabelWB(item.statusLabel, language)} />
        <CaseField label={tWB(language, "Confidence", "置信度")} value={item.confidenceLabel} />
        <CaseField label={tWB(language, "Source run", "来源运行")} value={item.sourceRun} />
      </div>
      {path && <CaseField label={tWB(language, "Path / relation", "路径 / 关系")} value={textWB(path, language)} />}
      {sourceUrl && <CaseField label={tWB(language, "Source URL", "来源 URL")} value={sourceUrl} />}
      {evidenceRefs.length > 0 && (
        <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>{tWB(language, "Evidence refs", "证据引用")}</div>
          <div style={{ display: "grid", gap: 4 }}>
            {evidenceRefs.slice(0, 5).map((ref, idx) => (
              <div key={`${ref}-${idx}`} style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)", overflowWrap: "anywhere" }}>{ref}</div>
            ))}
            {evidenceRefs.length > 5 && <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>+{evidenceRefs.length - 5} {tWB(language, "more", "条更多")}</div>}
          </div>
        </div>
      )}
      <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
        <div className="eyebrow" style={{ marginBottom: 6 }}>{tWB(language, "Write boundary", "写入边界")}</div>
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
  const source = pathPartWB(payload.source_label || payload.source || payload.country || "");
  const relation = pathPartWB(payload.relation || payload.edge_type || payload.path_relation || "");
  const target = pathPartWB(payload.target_label || payload.target || payload.chokepoint || "");
  if (source || relation || target) {
    return [source, relation, target].filter(Boolean).join(" -> ");
  }
  const pathLabel = payload.path_label || payload.deep_graph_profile?.path_label || payload.analysis_path || raw.path_label;
  if (pathLabel) return Array.isArray(pathLabel) ? pathLabel.join(" -> ") : String(pathLabel);
  if (payload.metrics && payload.metrics.length) return `metrics: ${payload.metrics.join(", ")}`;
  return "";
}

function pathPartWB(value) {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(pathPartWB).filter(Boolean).join(" -> ");
  return value.label || value.name || value.key || value.id || value.url || "";
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

function inlineActionLabel(action, language) {
  const labels = {
    approve: tWB(language, "approve", "批准"),
    reject: tWB(language, "reject", "拒绝"),
    needs: tWB(language, "needs evidence / changes", "需要补证据 / 修改"),
    comment: tWB(language, "comment", "评论"),
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

function AgentOutputsPanel({ groups, agentTab, language }) {
  return (
    <Panel eyebrow={tWB(language, "Agent outputs", "Agent 输出")} title={agentTab === "autopilot" ? tWB(language, "Findings ranked by confidence and detail", "按置信度和详情排序的 findings") : tWB(language, "Enrichment proposals by type", "按类型分组的信息增益候选")} style={{ marginTop: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        {(groups || []).map(group => (
          <div key={group.key} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div className="eyebrow accent" style={{ flex: 1 }}>{agentOutputGroupTitleWB(group.title, language)}</div>
              <span className="ct">{group.items.length}</span>
            </div>
            <div style={{ marginTop: 4, color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>{agentOutputGroupDescriptionWB(group.description, language)}</div>
            <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 8 }}>
              {group.items.slice(0, 6).map(item => (
                <a key={item.element_key || item.canonical_key || item.id || item.name}
                   href={group.href(item)}
                   style={{ display: "block", border: "1px solid var(--line-soft)", padding: "8px 9px", background: "var(--bg-1)", textDecoration: "none" }}>
          <div style={{ color: "var(--text)", fontSize: 12, fontWeight: 600 }}>{compactTextWB(textWB(agentOutputTitle(item), language), 74)}</div>
                  <div style={{ marginTop: 5, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <span>{tWB(language, "conf", "置信度")} {agentOutputConfidence(item).toFixed(2)}</span>
                    <span>{tWB(language, "detail", "详情")} {agentOutputDetailScore(item)}</span>
                    <span>{statusLabelWB(item.status || item.rawStatus || "draft", language)}</span>
                  </div>
                </a>
              ))}
              {group.items.length === 0 && (
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>{tWB(language, "No outputs in this category.", "该类别暂无输出。")}</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function agentOutputGroupTitleWB(title, language) {
  if (!isZhWB(language)) return title;
  const map = {
    "Proposed findings": "候选 findings",
    "Findings": "已审核 findings",
    "Proposed ontologies": "候选本体",
    "Proposed nodes": "候选节点",
    "Proposed edges": "候选边",
  };
  return map[title] || title;
}

function agentOutputGroupDescriptionWB(description, language) {
  if (!isZhWB(language)) return description;
  const map = {
    "Candidate findings generated by reasoning runs, sorted by confidence and detail.": "自动推理生成的候选 findings，按置信度和详情排序。",
    "Reviewed or graph-level findings surfaced for comparison.": "用于对比的已审核或图谱级 findings。",
    "Ontology candidates and web enrichment proposals that still require ontology review.": "仍需本体审核的本体候选和网页信息增益候选。",
    "Graph node proposals generated by enrichment runs.": "信息增益运行生成的候选图节点。",
    "Graph edge proposals generated by enrichment runs.": "信息增益运行生成的候选图边。",
  };
  return map[description] || description;
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

function agentKindLabelWB(kind, language) {
  const labels = {
    web_enrichment_crawl: tWB(language, "Crawl", "爬取"),
    iterative_graph_enrichment: tWB(language, "Graph enrich", "图谱信息增益"),
    autopilot_deep_reasoning: tWB(language, "Reasoning", "推理"),
  };
  return labels[kind] || kind || tWB(language, "Agent", "Agent");
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
