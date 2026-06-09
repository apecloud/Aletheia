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
    custom: "自定义",
    daily: "每天",
    done: "已完成",
    draft: "草稿",
    failed: "失败",
    hourly: "每小时",
    idle: "空闲",
    manual: "手动",
    needs_evidence: "需补证据",
    paused: "已暂停",
    proposed: "待审核",
    rejected: "已拒绝",
    running: "运行中",
    stopped: "已停止",
  };
  return map[String(status || "").toLowerCase()] || status || "—";
}

const DEDUP_AUDIT_FIELDS_WB = [
  "candidate_id",
  "task_id",
  "run_id",
  "frontier_id",
  "dedup_decision",
  "matched_node_key",
  "matched_edge_key",
  "matched_element_key",
  "matched_status",
  "matched_source",
  "match_score",
  "match_evidence",
  "conflict_fields",
  "decision_reason",
  "source_fingerprint",
  "evidence_fingerprint",
  "llm_merge_decision_allowed",
];

function dedupAuditWB(item) {
  const raw = item?.raw || item || {};
  const payload = raw.payload || {};
  const audit = { ...(item?.dedupAudit || raw.dedup_audit || payload.dedup_audit || {}) };
  DEDUP_AUDIT_FIELDS_WB.forEach(field => {
    if (audit[field] !== undefined) return;
    const value = payload[field] ?? raw[field];
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return;
    audit[field] = value;
  });
  if (audit.llm_merge_decision_allowed === undefined && Object.keys(audit).length) audit.llm_merge_decision_allowed = false;
  return audit;
}

function dedupDecisionLabelWB(decision, language) {
  const key = String(decision || "");
  const labels = {
    merge_existing: tWB(language, "merge existing approved object", "命中已批准对象"),
    duplicate_existing_proposal: tWB(language, "duplicate existing proposal", "重复候选"),
    duplicate_current_run: tWB(language, "duplicate in current run", "本轮重复"),
    needs_review: tWB(language, "needs human review", "需要人工判定"),
    new_proposal: tWB(language, "new proposal", "新候选"),
  };
  return labels[key] || key || "—";
}

function dedupAuditSummaryWB(item, language) {
  const audit = dedupAuditWB(item);
  const decision = String(audit.dedup_decision || "").toLowerCase();
  const title = agentOutputTitle(item);
  const matchedKey = audit.matched_node_key || audit.matched_edge_key || audit.matched_element_key || "";
  if (decision.includes("duplicate")) {
    return tWB(
      language,
      `${title} matches an existing proposal; duplicate creation was blocked.`,
      `${textWB(title, language)} 与已有候选重复，已阻止重复创建。`
    );
  }
  if (decision === "merge_existing") {
    return tWB(
      language,
      `${title} matches an approved graph object; no new proposal is required.`,
      `${textWB(title, language)} 命中已批准图对象，不需要新增候选。`
    );
  }
  if (["rejected", "filtered", "blocked"].includes(String(item?.status || item?.rawStatus || "").toLowerCase())) {
    return tWB(
      language,
      `${title} is not in the active review queue.`,
      `${textWB(title, language)} 不在当前待审核队列中。`
    );
  }
  return matchedKey
    ? tWB(language, `${title} has audit evidence linked to ${matchedKey}.`, `${textWB(title, language)} 有可追溯审计证据。`)
    : tWB(language, `${title} has dedup/filter audit evidence.`, `${textWB(title, language)} 有去重或过滤审计证据。`);
}

function llmMergePolicyLabelWB(value, language) {
  if (value === true) return tWB(language, "LLM auto-merge enabled", "LLM 自动合并已启用");
  return tWB(language, "LLM does not auto-merge; review required", "LLM 不参与自动合并 / 合并需审核");
}

function auditValueWB(value) {
  if (value === false) return "false";
  if (value === true) return "true";
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(auditValueWB).join(" · ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
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

function Workbench({ data, tenant, language }) {
  const tenantId = tenant ? tenant.id : "default";
  const tasksQ = useApiData("reasoningTasks", [tenantId]);
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
  const initialWorkspaceItem = (() => {
    try { return new URLSearchParams(location.search).get("workspace_item") || null; }
    catch { return null; }
  })();
  const [selectedKey, setSelectedKey] = useStateWB(initialWorkspaceItem);
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
      url.searchParams.set("screen", "workbench");
      url.searchParams.set("tenant", tenantId);
      url.searchParams.set("workspace_tab", tab);
      history.replaceState(null, "", url.toString());
    } catch {}
  }

  function selectWorkQueueItem(key) {
    setSelectedKey(key);
  }

  useEffectWB(() => {
    try {
      const url = new URL(location.href);
      url.searchParams.set("screen", "workbench");
      url.searchParams.set("tenant", tenantId);
      url.searchParams.set("workspace_tab", workspaceTab);
      if (workspaceTab === "workqueue" && selectedKey) url.searchParams.set("workspace_item", selectedKey);
      else url.searchParams.delete("workspace_item");
      history.replaceState(null, "", url.toString());
    } catch {}
  }, [tenantId, workspaceTab, selectedKey]);

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
                     onClick={() => selectWorkQueueItem(c.id)}>
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
  const initialRunKey = (() => {
    try { return new URLSearchParams(location.search).get("run_key") || ""; }
    catch { return ""; }
  })();
  const [selectedRunKey, setSelectedRunKey] = useStateWB(initialRunKey);
  const [busy, setBusy] = useStateWB(false);
  const [message, setMessage] = useStateWB(null);
  const [settingsOpen, setSettingsOpen] = useStateWB(false);
  const [agentParams, setAgentParams] = useStateWB({
    scope: "",
    budget: "3",
    allowlist: "zenodo.org",
    cadence: "manual",
    customInterval: "60",
    nodeSimilarityThreshold: "0.6",
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
  const latestRun = session?.latest?.run || null;
  const headerRun = selected || latestRun || null;
  const agentDisplayName = agentTab === "autopilot"
    ? tWB(language, "Autopilot reasoning agent", "自动推理 Agent")
    : tWB(language, "Auto enriching agent", "自动信息增益 Agent");
  const agentDisplayId = agentTab === "autopilot"
    ? (headerRun?.run_key || "autopilot")
    : (session?.session_key || headerRun?.run_key || "—");
  const agentStartedAt = headerRun?.started_at || session?.created_at || "";
  const agentFinishedAt = headerRun?.finished_at || session?.updated_at || "";
  const runtimeState = session?.runtime_state || {};
  const runtimeQueue = runtimeState.frontier_queue || {};
  const queueCount = runtimeQueue.total_count ?? runtimeState.frontier_queue_count ?? session?.frontier?.length ?? 0;
  const runtimeBudget = runtimeState.budget || {};
  const remainingCycles = runtimeBudget.remaining_cycles;
  const maxCycles = runtimeBudget.max_cycles;
  const backoffState = runtimeState.backoff || {};
  const sessionScope = agentTab === "autopilot" ? agentParams.scope : (session?.objective || agentParams.scope);
  const nextRunAt = runtimeState.next_run_at || session?.config?.next_run_at || "";
  const schedulerLabel = agentTab === "autopilot"
    ? tWB(language, "run on demand", "按需运行")
    : agentSchedulerStateLabelWB(runtimeState, session?.status, language);
  const budgetRemainingLabel = agentTab === "autopilot"
    ? agentParams.budget
    : agentBudgetRemainingLabelWB(remainingCycles, maxCycles, language);
  const backoffLabel = agentTab === "autopilot"
    ? "—"
    : agentBackoffLabelWB(backoffState, runtimeState.stop_reason, language);
  const latestExtractionBlockers = Number(latestRun?.proposed_count || 0) === 0 ? latestRun?.extraction_blockers || null : null;
  const parsedNodeSimilarityThreshold = Number(agentParams.nodeSimilarityThreshold);
  const nodeSimilarityThreshold = Number.isFinite(parsedNodeSimilarityThreshold) ? parsedNodeSimilarityThreshold : 0.6;
  const latestBlockerParts = latestExtractionBlockers ? [
    ...Object.entries(latestExtractionBlockers.extraction_engine_status_counts || {}).map(([key, count]) => `${key}:${count}`),
    ...Object.entries(latestExtractionBlockers.rejected_candidate_reason_counts || {}).map(([key, count]) => `${key}:${count}`),
    ...Object.entries(latestExtractionBlockers.pruned_reason_counts || {}).map(([key, count]) => `${key}:${count}`),
  ] : [];
  const liveTraceRows = useMemoWB(() => buildAgentLiveTraceRowsWB({
    run: selected,
    session,
    latestExtractionBlockers,
  }), [
    selected?.run_key,
    JSON.stringify(selected?.trace || []),
    JSON.stringify(selected?.frontier || []),
    JSON.stringify(selected?.skipped_sources || []),
    JSON.stringify(selected?.counts || {}),
    JSON.stringify(session?.config?.latest_events || []),
    JSON.stringify(latestExtractionBlockers || {}),
  ]);

  useEffectWB(() => {
    setAgentParams(prev => {
      if (prev.scope && prev.scope !== "—" && prev.scope !== "default") return prev;
      return { ...prev, scope: "" };
    });
  }, [tenantId]);

  useEffectWB(() => {
    if (!session?.config) return;
    setAgentParams(prev => ({
      ...prev,
      scope: session.objective || session.config.scope || prev.scope,
      allowlist: (session.config.allowed_domains || []).join(", ") || prev.allowlist,
      cadence: session.config.cadence || prev.cadence,
      customInterval: String(session.config.custom_interval_minutes || prev.customInterval || "60"),
      budget: String(session.config.max_frontier || prev.budget || "3"),
      nodeSimilarityThreshold: String(session.config.node_similarity_dedup_threshold ?? prev.nodeSimilarityThreshold ?? "0.6"),
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
      url.searchParams.set("screen", "workbench");
      url.searchParams.set("tenant", tenantId);
      url.searchParams.set("workspace_tab", "agents");
      url.searchParams.set("agent_tab", tab);
      history.replaceState(null, "", url.toString());
    } catch {}
  }

  useEffectWB(() => {
    try {
      const url = new URL(location.href);
      url.searchParams.set("screen", "workbench");
      url.searchParams.set("tenant", tenantId);
      url.searchParams.set("workspace_tab", "agents");
      url.searchParams.set("agent_tab", agentTab);
      if (selectedRunKey) url.searchParams.set("run_key", selectedRunKey);
      else url.searchParams.delete("run_key");
      history.replaceState(null, "", url.toString());
    } catch {}
  }, [tenantId, agentTab, selectedRunKey]);

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
        if (tenantId === "creditcardfraud") result = await AL_API.runCreditcardfraudAutopilotPlaybook(tenantId, body);
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
        objective: agentParams.scope,
        scope: agentParams.scope,
        budget: Number(agentParams.budget) || 3,
        allowlist: agentParams.allowlist,
        cadence: agentParams.cadence,
        custom_interval_minutes: Number(agentParams.customInterval) || 60,
        node_similarity_dedup_threshold: nodeSimilarityThreshold,
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
        objective: agentParams.scope,
        cadence: agentParams.cadence,
        custom_interval_minutes: Number(agentParams.customInterval) || 60,
        allowlist: agentParams.allowlist,
        budget: Number(agentParams.budget) || 3,
        node_similarity_dedup_threshold: nodeSimilarityThreshold,
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
              <span className="label">{tWB(language, "Name", "名称")}</span>
              <span className="val">{agentDisplayName}</span>
            </div>
            <div className="stat lg">
              <span className="label">{tWB(language, "ID", "ID")}</span>
              <span className="val mono">{compactTextWB(agentDisplayId, 56)}</span>
            </div>
            <div className="stat">
              <span className="label">{tWB(language, "Started", "启动时间")}</span>
              <span className="val mono">{agentStartedAt ? String(agentStartedAt).slice(0, 19) : "—"}</span>
            </div>
            <div className="stat">
              <span className="label">{tWB(language, "Finished", "结束时间")}</span>
              <span className="val mono">{agentFinishedAt ? String(agentFinishedAt).slice(0, 19) : "—"}</span>
            </div>
          </div>
          <div className="row">
            <div className="stat">
              <span className="label">{tWB(language, "Scope", "范围")}</span>
              <span className="val mono">{compactTextWB(sessionScope, 42)}</span>
            </div>
            <div className="stat">
              <span className="label">{tWB(language, "Budget", "预算")}</span>
              <span className="val mono">{budgetRemainingLabel}</span>
            </div>
            <div className="stat lg">
              <span className="label">{tWB(language, "Next action", "下一步")}</span>
              <span className="val">{pending ? tWB(language, "Review generated proposals", "审核生成的候选对象") : tWB(language, "Run once or inspect latest run", "运行一次或查看最近运行")}</span>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
          {agentTab !== "autopilot" && (
            <Panel
              eyebrow={tWB(language, "Persistent session", "持久会话")}
              title={tWB(language, "Self enrichment loop", "Self enrichment 循环")}
              count={statusLabelWB(session?.status || "no session", language)}
            >
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
                <CaseField label={tWB(language, "Mode", "模式")} value={runtimeState.persistent ? tWB(language, "persistent", "持久运行") : "—"} />
                <CaseField label={tWB(language, "Scheduler", "调度")} value={schedulerLabel} />
                <CaseField label={tWB(language, "Next run", "下次运行")} value={nextRunAt ? String(nextRunAt).slice(0, 19) : tWB(language, "manual", "手动")} />
                <CaseField label={tWB(language, "Frontier queue", "Frontier 队列")} value={`${queueCount} ${tWB(language, "queued", "排队中")}`} />
                <CaseField label={tWB(language, "Budget left", "剩余预算")} value={budgetRemainingLabel} />
                <CaseField label={tWB(language, "Backoff / stop", "退避 / 停止")} value={backoffLabel} />
              </div>
            </Panel>
          )}

          <Panel
            eyebrow={tWB(language, "Parameters", "参数")}
            title={tWB(language, "Agent settings", "Agent 设置")}
            actions={<button className="btn ghost" onClick={() => setSettingsOpen(open => !open)}>{settingsOpen ? tWB(language, "Collapse", "折叠") : tWB(language, "Expand", "展开")}</button>}
            style={{ marginTop: agentTab !== "autopilot" ? 16 : 0 }}
          >
            {settingsOpen ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 120px", gap: 10 }}>
                <label>
                  <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Objective (optional)", "Objective（可选）")}</div>
                  <input className="input" value={agentParams.scope} onChange={e => updateAgentParam("scope", e.target.value)} placeholder={tWB(language, "Leave empty to search from frontier only", "留空则只根据 frontier 检索")} />
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
                  <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Node dedup threshold", "节点去重阈值")}</div>
                  <input className="input" type="number" min="0" max="1" step="0.01" value={agentParams.nodeSimilarityThreshold} onChange={e => updateAgentParam("nodeSimilarityThreshold", e.target.value)} />
                </label>
                <label>
                  <div className="eyebrow" style={{ marginBottom: 4 }}>{tWB(language, "Stop condition", "停止条件")}</div>
                  <input className="input" value={agentParams.stopCondition} onChange={e => updateAgentParam("stopCondition", e.target.value)} />
                </label>
              </div>
            ) : (
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", overflowWrap: "anywhere" }}>
                {tWB(language, "Settings collapsed", "设置已折叠")} · {tWB(language, "scope", "范围")} {compactTextWB(agentParams.scope, 70)} · {tWB(language, "budget", "预算")} {agentParams.budget} · {tWB(language, "node dedup", "节点去重")} {agentParams.nodeSimilarityThreshold} · {tWB(language, "cadence", "频率")} {agentParams.cadence}
              </div>
            )}
            <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
              {agentParams.safety} · {tWB(language, "next run", "下次运行")} {nextRunAt || tWB(language, "manual", "手动")}
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

          {agentTab !== "autopilot" && latestBlockerParts.length > 0 && (
            <Panel eyebrow={tWB(language, "Extraction blockers", "抽取阻塞原因")} title={tWB(language, "Why the latest cycle produced no proposals", "最近一次运行为何没有候选")} style={{ marginTop: 16 }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)", overflowWrap: "anywhere" }}>
                {latestBlockerParts.join(" · ")}
              </div>
              {latestExtractionBlockers?.source_urls?.length > 0 && (
                <div style={{ marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", overflowWrap: "anywhere" }}>
                  {compactTextWB(latestExtractionBlockers.source_urls.join(" · "), 180)}
                </div>
              )}
            </Panel>
          )}

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
                    {event.extraction_blockers && (
                      <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)", overflowWrap: "anywhere" }}>
                        {[
                          ...Object.entries(event.extraction_blockers.extraction_engine_status_counts || {}).map(([key, count]) => `${key}:${count}`),
                          ...Object.entries(event.extraction_blockers.rejected_candidate_reason_counts || {}).map(([key, count]) => `${key}:${count}`),
                          ...Object.entries(event.extraction_blockers.pruned_reason_counts || {}).map(([key, count]) => `${key}:${count}`),
                        ].join(" · ")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Panel>
          )}

          {agentTab !== "autopilot" && queueCount > 0 && (
            <Panel eyebrow={tWB(language, "Frontier queue", "Frontier 队列")} title={tWB(language, "Next enrichment seeds", "下一批信息增益种子")} count={`${queueCount} ${tWB(language, "queued", "排队中")}`} style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {(runtimeQueue.preview?.length ? runtimeQueue.preview : session.frontier).slice(0, 8).map((item, index) => (
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
            <Panel eyebrow={tWB(language, "Live trace", "Live trace")} title={tWB(language, "Step-by-step enrichment detail", "逐步信息增益详情")} count={`${liveTraceRows.length} ${tWB(language, "steps", "步")}`} style={{ marginTop: 16 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {liveTraceRows.map((step, index) => (
                  <div key={index} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "8px 10px" }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", justifyContent: "space-between" }}>
                      <div style={{ minWidth: 0 }}>
                        <div className="eyebrow" style={{ marginBottom: 3 }}>{step.phase}</div>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", overflowWrap: "anywhere" }}>
                          {compactTextWB(step.title || `step ${index + 1}`, 150)}
                        </div>
                      </div>
                      <Pill kind={agentTraceToneWB(step.status)}>{compactTextWB(step.status || "recorded", 32)}</Pill>
                    </div>
                    {step.details?.length > 0 && (
                      <div style={{ marginTop: 7, display: "grid", gap: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                        {step.details.map((line, detailIndex) => (
                          <div key={detailIndex} style={{ overflowWrap: "anywhere" }}>{line}</div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {!liveTraceRows.length && (
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
        dedupAudit: dedupAuditWB(e),
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
  const dedupAudit = dedupAuditWB(item);
  const matchedKey = dedupAudit.matched_node_key || dedupAudit.matched_edge_key || dedupAudit.matched_element_key || "";
  const matchEvidence = Array.isArray(dedupAudit.match_evidence) ? dedupAudit.match_evidence : [];
  const conflictFields = Array.isArray(dedupAudit.conflict_fields) ? dedupAudit.conflict_fields : [];
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
      {Object.keys(dedupAudit).length > 0 && (
        <div style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
          <div className="eyebrow accent" style={{ marginBottom: 8 }}>{tWB(language, "Dedup audit", "去重审计")}</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <CaseField label={tWB(language, "Decision", "判定")} value={dedupDecisionLabelWB(dedupAudit.dedup_decision, language)} />
            <CaseField label={tWB(language, "Matched", "命中")} value={matchedKey || "—"} />
            <CaseField label={tWB(language, "Candidate", "候选")} value={auditValueWB(dedupAudit.candidate_id)} />
            <CaseField label={tWB(language, "Score", "分数")} value={dedupAudit.match_score === undefined ? "—" : String(dedupAudit.match_score)} />
            <CaseField label={tWB(language, "Task / run / frontier", "任务 / 运行 / frontier")} value={[dedupAudit.task_id, dedupAudit.run_id, dedupAudit.frontier_id].filter(Boolean).join(" · ") || "—"} />
            <CaseField label={tWB(language, "Fingerprints", "指纹")} value={[dedupAudit.source_fingerprint, dedupAudit.evidence_fingerprint].filter(Boolean).join(" · ") || "—"} />
            <CaseField label={tWB(language, "LLM auto-merge", "LLM 自动合并")} value={llmMergePolicyLabelWB(dedupAudit.llm_merge_decision_allowed, language)} />
          </div>
          {dedupAudit.decision_reason && (
            <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)", overflowWrap: "anywhere" }}>
              {tWB(language, "Reason", "原因")}: {textWB(dedupAudit.decision_reason, language)}
            </div>
          )}
          {conflictFields.length > 0 && (
            <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--changes)", overflowWrap: "anywhere" }}>
              {tWB(language, "Conflicts require review gate", "冲突字段需要审核入口判定")}: {conflictFields.join(", ")}
            </div>
          )}
          {matchEvidence.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div className="eyebrow" style={{ marginBottom: 5 }}>{tWB(language, "Match evidence", "匹配证据")}</div>
              {matchEvidence.slice(0, 4).map((evidence, index) => (
                <div key={index} style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", borderTop: "1px solid var(--line-soft)", paddingTop: 4, marginTop: 4, overflowWrap: "anywhere" }}>
                  {auditValueWB(evidence)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
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
  const target = pathPartWB(payload.target_label || payload.target || "");
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

function buildAgentLiveTraceRowsWB({ run, session, latestExtractionBlockers }) {
  const rows = [];
  const events = session?.config?.latest_events || [];
  events.slice(-8).forEach(event => {
    const blockers = blockerSummaryPartsWB(event.extraction_blockers);
    const eventLayer = queryLayerLabelWB(event);
    const details = [
      event.created_at ? `time: ${String(event.created_at).slice(0, 19)}` : "",
      event.run_key ? `run: ${event.run_key}` : "",
      event.autopilot_session_key ? `autopilot: ${event.autopilot_session_key}` : "",
      eventLayer ? `query layer: ${eventLayer}` : "",
      event.query ? `query: ${event.query}` : "",
      event.request_url ? `request url: ${event.request_url}` : "",
      event.frontier_key ? `frontier: ${event.frontier_key}` : "",
      event.frontier_used_count != null ? `frontier used: ${event.frontier_used_count}` : "",
      event.trusted_source_count != null ? `trusted sources: ${event.trusted_source_count}` : "",
      event.skipped_source_count != null ? `skipped sources: ${event.skipped_source_count}` : "",
      event.proposed_count != null ? `proposed: ${event.proposed_count}` : "",
      event.error ? `error: ${event.error}` : "",
      blockers.length ? `blockers: ${blockers.join(" · ")}` : "",
    ].filter(Boolean);
    rows.push({
      phase: "session event",
      title: event.type || "event",
      status: event.status || event.reason || event.type || "recorded",
      details,
    });
  });

  if (!run) return rows;

  const counts = run.counts || {};
  rows.push({
    phase: "run",
    title: run.run_key || run.objective || "selected run",
    status: run.status || "recorded",
    details: [
      run.started_at ? `started: ${String(run.started_at).slice(0, 19)}` : "",
      run.finished_at ? `finished: ${String(run.finished_at).slice(0, 19)}` : "",
      `outputs: proposed ${counts.proposed ?? 0} · pruned ${counts.pruned ?? 0} · findings ${counts.findings ?? 0}`,
      run.error ? `error: ${run.error}` : "",
    ].filter(Boolean),
  });

  if ((run.frontier || []).length) {
    rows.push({
      phase: "frontier selection",
      title: `${run.frontier.length} frontier seeds selected`,
      status: "selected",
      details: (run.frontier || []).slice(0, 8).map(item =>
        compactTextWB(`${item.source_kind || item.kind || "frontier"} · ${item.name || item.target_key || item.key || ""}`, 180)
      ),
    });
  }

  (run.skipped_sources || []).slice(0, 12).forEach(source => {
    rows.push({
      phase: "source trust",
      title: source.url || source.source_url || source.domain || "skipped source",
      status: source.reason || "skipped",
      details: [
        source.domain ? `domain: ${source.domain}` : "",
        source.reason ? `reason: ${source.reason}` : "",
        source.request_url ? `request url: ${source.request_url}` : "",
        source.frontier_key ? `frontier: ${source.frontier_key}` : "",
      ].filter(Boolean),
    });
  });

  (run.trace || []).forEach((step, index) => {
    const profile = step.last_extraction_profile || {};
    const quality = profile.quality || {};
    const rejected = profile.rejected_or_ambiguous_candidates || [];
    const pruned = step.pruned || [];
    const prunedParts = countObjectPartsWB(countByFieldWB(pruned, "reason"));
    const rejectedParts = countObjectPartsWB(countByFieldWB(rejected, "reason"));
    const sourceUrls = uniqueCompactWB([
      ...(pruned || []).map(item => item.url || item.source_url),
      ...(rejected || []).map(item => item.source_ref || item.source_url || item.url),
    ]);
    const frontier = step.frontier || {};
    const selectedLayer = queryLayerLabelWB(step.selected_query_plan);
    const queryLadder = queryLadderSummaryWB(step.query_plans || []);
    const schemaEdgeCount = profile.schema_context?.edge_types?.length;
    const extractedCount = (step.extracted_candidates || []).length;
    rows.push({
      phase: `trace step ${index + 1}`,
      title: step.query || frontier.name || frontier.key || `trace step ${index + 1}`,
      status: profile.extraction_engine_status || profile.extraction_engine || "trace",
      details: [
        frontier.key ? `frontier: ${frontier.key}` : "",
        frontier.name ? `frontier name: ${frontier.name}` : "",
        selectedLayer ? `selected query layer: ${selectedLayer}` : "",
        queryLadder ? `query ladder: ${queryLadder}` : "",
        step.result_count != null ? `source results: ${step.result_count}` : "",
        `extracted: candidates ${extractedCount} · nodes ${quality.node_count ?? profile.nodes?.length ?? 0} · edges ${quality.edge_count ?? profile.edges?.length ?? 0} · findings ${quality.finding_count ?? profile.findings?.length ?? 0}`,
        profile.extraction_engine ? `engine: ${profile.extraction_engine}` : "",
        profile.extraction_source ? `source: ${profile.extraction_source}` : "",
        profile.prompt_version || step.extraction_prompt_version ? `prompt: ${profile.prompt_version || step.extraction_prompt_version}` : "",
        schemaEdgeCount != null ? `schema edge types: ${schemaEdgeCount}` : "",
        step.graph_context_used ? `graph context: ${compactTextWB(textWB(graphContextLabelWB(step.graph_context_used), "en"), 150)}` : "",
        step.path_context_used ? `path context: ${compactTextWB(textWB(pathContextLabelWB(step.path_context_used), "en"), 150)}` : "",
        step.query_terms ? `query terms: ${compactTextWB(flatQueryTermsWB(step.query_terms), 180)}` : "",
        prunedParts.length ? `pruned: ${prunedParts.join(" · ")}` : "",
        rejectedParts.length ? `review/blocked: ${rejectedParts.join(" · ")}` : "",
        sourceUrls.length ? `source urls: ${compactTextWB(sourceUrls.join(" · "), 220)}` : "",
      ].filter(Boolean),
    });
  });

  const latestBlockers = blockerSummaryPartsWB(latestExtractionBlockers);
  if (latestBlockers.length) {
    rows.push({
      phase: "cycle summary",
      title: "latest cycle produced no proposals",
      status: "blocked",
      details: [
        `blockers: ${latestBlockers.join(" · ")}`,
        latestExtractionBlockers?.frontier_keys?.length ? `frontier keys: ${compactTextWB(latestExtractionBlockers.frontier_keys.join(" · "), 220)}` : "",
        latestExtractionBlockers?.source_urls?.length ? `source urls: ${compactTextWB(latestExtractionBlockers.source_urls.join(" · "), 220)}` : "",
      ].filter(Boolean),
    });
  }

  return rows;
}

function queryLayerLabelWB(plan) {
  if (!plan) return "";
  const granularity = plan.granularity || plan.next_granularity;
  const coarseLevel = plan.coarse_level ?? plan.plan_index ?? plan.next_plan_index;
  const intent = plan.selected_intent || plan.intent;
  const parts = [];
  if (granularity) parts.push(granularity);
  if (coarseLevel !== undefined && coarseLevel !== null) parts.push(`layer ${coarseLevel}`);
  if (intent) parts.push(intent);
  return parts.join(" · ");
}

function queryLadderSummaryWB(plans) {
  if (!Array.isArray(plans) || plans.length === 0) return "";
  return plans
    .slice(0, 5)
    .map(plan => {
      const granularity = plan.granularity || `L${plan.coarse_level ?? "?"}`;
      const intent = plan.intent || plan.selected_intent || "query";
      const query = plan.query ? `: ${compactTextWB(plan.query, 64)}` : "";
      return `${granularity} ${intent}${query}`;
    })
    .join(" | ");
}

function countByFieldWB(items, field) {
  return (items || []).reduce((acc, item) => {
    const key = item?.[field] || "unknown";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
}

function countObjectPartsWB(counts) {
  return Object.entries(counts || {}).map(([key, count]) => `${key}:${count}`);
}

function blockerSummaryPartsWB(blockers) {
  if (!blockers) return [];
  return [
    ...countObjectPartsWB(blockers.extraction_engine_status_counts),
    ...countObjectPartsWB(blockers.rejected_candidate_reason_counts),
    ...countObjectPartsWB(blockers.pruned_reason_counts),
  ];
}

function uniqueCompactWB(values) {
  return Array.from(new Set((values || []).map(value => value == null ? "" : String(value)).filter(Boolean)));
}

function agentTraceToneWB(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("missing") || value.includes("blocked") || value.includes("error") || value.includes("fail") || value.includes("conflict")) return "changes";
  if (value.includes("skip") || value.includes("pruned") || value.includes("no_")) return "proposed";
  if (value.includes("complete") || value.includes("selected") || value.includes("recorded")) return "approved";
  return "accent";
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
  const activeGraphItems = dedupeAgentOutputs((graphElements || []).filter(agentOutputIsActiveGraphProposalWB));
  const duplicateGraphItems = dedupeAgentOutputs((graphElements || []).filter(agentOutputIsDuplicateCandidateWB));
  const historyGraphItems = dedupeAgentOutputs((graphElements || []).filter(agentOutputIsHistoryGraphOutputWB));
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
      description: "Current graph node proposals visible in Graph Proposed review.",
      items: activeGraphItems.filter(item => String(item.element_type || item.type || "").toLowerCase().includes("node")),
      href: item => `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`,
    },
    {
      key: "edges",
      title: "Proposed edges",
      description: "Current graph edge proposals visible in Graph Proposed review.",
      items: activeGraphItems.filter(item => String(item.element_type || item.type || "").toLowerCase().includes("edge")),
      href: item => `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`,
    },
    {
      key: "graph_findings",
      title: "Proposed graph findings",
      description: "Current graph findings visible in Graph Proposed review.",
      items: activeGraphItems.filter(item => String(item.element_type || item.type || "").toLowerCase().includes("finding")),
      href: item => `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`,
    },
    {
      key: "duplicate_outputs",
      title: "Duplicate candidates",
      description: "Candidates that match existing graph proposals and still need review handling.",
      items: duplicateGraphItems,
      duplicateOnly: true,
      href: item => `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`,
    },
    {
      key: "history_outputs",
      title: "Rejected / filtered history",
      description: "Rejected, blocked, or filtered outputs kept for audit history.",
      items: historyGraphItems,
      historyOnly: true,
      href: item => `/?screen=graph&tenant=${encodeURIComponent(tenantId)}&graph_tab=proposed&proposed_key=${encodeURIComponent(item.element_key || "")}`,
    },
  ];
}

function agentOutputIsActionableStatusWB(item) {
  const status = String(item?.status || item?.rawStatus || "draft").toLowerCase();
  const actionable = ["", "draft", "proposed", "candidate", "new", "new_proposal", "needs_review", "needs_more_evidence", "needs_evidence"];
  return actionable.includes(status);
}

function agentOutputIsActiveGraphProposalWB(item) {
  const decision = String(dedupAuditWB(item).dedup_decision || "").toLowerCase();
  if (["merge_existing", "duplicate_existing_proposal", "duplicate_current_run"].includes(decision)) return false;
  if (agentOutputIsHistoryGraphOutputWB(item)) return false;
  return agentOutputIsActionableStatusWB(item) || decision === "new_proposal" || decision === "needs_review";
}

function agentOutputIsDuplicateCandidateWB(item) {
  if (agentOutputIsHistoryGraphOutputWB(item)) return false;
  const decision = String(dedupAuditWB(item).dedup_decision || "").toLowerCase();
  return agentOutputIsActionableStatusWB(item)
    && ["merge_existing", "duplicate_existing_proposal", "duplicate_current_run"].includes(decision);
}

function agentOutputIsHistoryGraphOutputWB(item) {
  const status = String(item?.status || item?.rawStatus || "draft").toLowerCase();
  return ["rejected", "filtered", "blocked", "closed", "archived"].includes(status);
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
  const visibleGroups = (groups || []).filter(group => !group.duplicateOnly && !group.historyOnly);
  const duplicateGroups = (groups || []).filter(group => group.duplicateOnly && group.items.length > 0);
  const historyGroups = (groups || []).filter(group => group.historyOnly && group.items.length > 0);
  const duplicateCount = duplicateGroups.reduce((sum, group) => sum + group.items.length, 0);
  const historyCount = historyGroups.reduce((sum, group) => sum + group.items.length, 0);
  return (
    <Panel eyebrow={tWB(language, "Agent outputs", "Agent 输出")} title={agentTab === "autopilot" ? tWB(language, "Findings ranked by confidence and detail", "按置信度和详情排序的 findings") : tWB(language, "Enrichment proposals by type", "按类型分组的信息增益候选")} style={{ marginTop: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        {visibleGroups.map(group => (
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
      {duplicateCount > 0 && (
        <details style={{ marginTop: 12, border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
          <summary style={{ cursor: "pointer", color: "var(--text)", fontSize: 12, fontWeight: 650 }}>
            {tWB(language, "Duplicate candidates", "去重命中候选")} · {duplicateCount}
            <span style={{ marginLeft: 8, color: "var(--muted)", fontWeight: 400 }}>
              {tWB(language, "matched existing proposals; expand to review handling evidence", "命中已有候选，展开查看审核处理证据")}
            </span>
          </summary>
          <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
            {duplicateGroups.flatMap(group => group.items.map(item => ({ group, item }))).slice(0, 12).map(({ group, item }) => {
              const audit = dedupAuditWB(item);
              return (
                <a key={item.element_key || item.canonical_key || item.id || item.name}
                   href={group.href(item)}
                   style={{ display: "block", border: "1px solid var(--line-soft)", padding: "8px 9px", background: "var(--bg-1)", textDecoration: "none" }}>
                  <div style={{ color: "var(--text)", fontSize: 12, fontWeight: 600 }}>{compactTextWB(textWB(agentOutputTitle(item), language), 74)}</div>
                  <div style={{ marginTop: 4, color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>{compactTextWB(dedupAuditSummaryWB(item, language), 150)}</div>
                  <div style={{ marginTop: 5, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <span>{dedupDecisionLabelWB(audit.dedup_decision, language)}</span>
                    <span>{statusLabelWB(item.status || item.rawStatus || "draft", language)}</span>
                    {audit.match_score !== undefined && <span>{tWB(language, "score", "分数")} {audit.match_score}</span>}
                    <span>{llmMergePolicyLabelWB(audit.llm_merge_decision_allowed, language)}</span>
                  </div>
                </a>
              );
            })}
          </div>
        </details>
      )}
      {historyCount > 0 && (
        <details style={{ marginTop: 12, border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
          <summary style={{ cursor: "pointer", color: "var(--text)", fontSize: 12, fontWeight: 650 }}>
            {tWB(language, "Rejected / filtered history", "已拒绝 / 已过滤历史")} · {historyCount}
            <span style={{ marginLeft: 8, color: "var(--muted)", fontWeight: 400 }}>
              {tWB(language, "hidden from active review counts", "不计入当前待处理主数")}
            </span>
          </summary>
          <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
            {historyGroups.flatMap(group => group.items.map(item => ({ group, item }))).slice(0, 12).map(({ group, item }) => (
              <a key={item.element_key || item.canonical_key || item.id || item.name}
                 href={group.href(item)}
                 style={{ display: "block", border: "1px solid var(--line-soft)", padding: "8px 9px", background: "var(--bg-1)", textDecoration: "none" }}>
                <div style={{ color: "var(--text)", fontSize: 12, fontWeight: 600 }}>{compactTextWB(textWB(agentOutputTitle(item), language), 74)}</div>
                <div style={{ marginTop: 4, color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>{compactTextWB(dedupAuditSummaryWB(item, language), 150)}</div>
                <div style={{ marginTop: 5, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                  {statusLabelWB(item.status || item.rawStatus || "draft", language)}
                </div>
              </a>
            ))}
          </div>
        </details>
      )}
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
    "Proposed graph findings": "候选图发现",
    "Duplicate candidates": "去重命中候选",
    "Rejected / filtered history": "已拒绝 / 已过滤历史",
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
    "Current graph node proposals visible in Graph Proposed review.": "当前 Graph 候选审核页可见的候选图节点。",
    "Current graph edge proposals visible in Graph Proposed review.": "当前 Graph 候选审核页可见的候选图边。",
    "Current graph findings visible in Graph Proposed review.": "当前 Graph 候选审核页可见的候选图发现。",
    "Candidates that match existing graph proposals and still need review handling.": "命中已有图候选、仍需审核处理的候选。",
    "Rejected, blocked, or filtered outputs kept for audit history.": "已拒绝、已阻塞或已过滤输出，仅保留为审计历史。",
  };
  return map[description] || description;
}

function agentOutputTitle(item) {
  const payload = item?.payload || {};
  return item?.name || item?.title || item?.key || item?.canonical_key || item?.element_key || payload.conclusion || "Untitled output";
}

function taskToCase(task) {
  const scope = task.scope || {};
  const basisKey = firstBasisKey(scope);
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
    ontologyHref: basisKey ? `/?screen=ontology&tenant=${encodeURIComponent(task.tenant_id || "default")}&artifact=${encodeURIComponent(basisKey)}` : "",
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
  if (!basisKey) return `Case centered on ${center}; ontology basis is not available until data import, schema-to-graph modeling, and review are complete.`;
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
  const value = String(key || "");
  if (value.startsWith("object:")) return value.slice("object:".length).replace(/[_:-]+/g, " ");
  if (value.startsWith("link:")) return value.slice("link:".length).replace(/[_:-]+/g, " ");
  return value;
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

function agentSchedulerStateLabelWB(runtimeState, status, language) {
  const normalizedStatus = String(status || "").toLowerCase();
  if (normalizedStatus === "paused") return tWB(language, "paused", "已暂停");
  if (normalizedStatus === "stopped") return tWB(language, "stopped", "已停止");
  if (runtimeState?.backoff?.active) return tWB(language, "backoff active", "退避中");
  if (runtimeState?.auto_due) return tWB(language, "due now", "已到期");
  const cadence = runtimeState?.cadence || "manual";
  if (cadence === "manual") return tWB(language, "manual", "手动");
  const cadenceLabel = cadence === "custom"
    ? `${runtimeState?.custom_interval_minutes || 60}m`
    : statusLabelWB(cadence, language);
  return `${cadenceLabel} · ${runtimeState?.auto_due_reason || "scheduled"}`;
}

function agentBudgetRemainingLabelWB(remainingCycles, maxCycles, language) {
  if (maxCycles === null || maxCycles === undefined || maxCycles === "") return tWB(language, "open", "未限制");
  const remaining = remainingCycles === null || remainingCycles === undefined ? "—" : remainingCycles;
  return `${remaining}/${maxCycles}`;
}

function agentBackoffLabelWB(backoff, stopReason, language) {
  if (backoff?.active) {
    const parts = [
      tWB(language, "active", "生效中"),
      backoff.backoff_until ? String(backoff.backoff_until).slice(0, 19) : "",
      backoff.last_error ? compactTextWB(backoff.last_error, 48) : "",
    ].filter(Boolean);
    return parts.join(" · ");
  }
  if (stopReason) return compactTextWB(stopReason, 72);
  return tWB(language, "clear", "无");
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

function compactTextWB(value, max = 80) {
  const text = String(value || "");
  if (text.length <= max) return text;
  return text.slice(0, Math.max(0, max - 1)).trimEnd() + "…";
}

Object.assign(window, { Workbench });
