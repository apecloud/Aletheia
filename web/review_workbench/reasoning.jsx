/* Aletheia — Reasoning Process screen
   Question → Answer → Evidence flow.
   Endpoints:
     GET  /api/reasoning/tasks
     GET  /api/reasoning/tasks/{key}
     POST /api/reasoning/tasks/{key}/run
     POST /api/reasoning/questions
     POST /api/reasoning/findings/{key}/{approve|reject|needs-changes|comment}
*/

const { useState: useStateRX, useEffect: useEffectRX, useMemo: useMemoRX, useRef: useRefRX } = React;

// Mock task list shown when API isn't reachable
const MOCK_TASKS = [
  {
    canonical_key: "RT-EMP4-WL",
    name: "Why is Employee #4 workload unusual?",
    status: "completed",
    confidence: 0.82,
    center_node: "Employee:4",
    depth: 1,
    limit: 200,
    source: "manual",
    updated_at: "2026-05-18 02:11",
    finding: {
      conclusion: "Employee:4 is structurally over-allocated. 47 active Orders are all OwnedBy Employee:4 with no reassignment in 90 days. Manager (Employee:9) review cycles show no escalation despite >2σ over the team median.",
      title: "Concentration risk on Employee:4",
      status: "draft",
      action_proposal: "Propose reassignment of low-value Orders (#1014, #1101, #1147) to Employee:23 and Employee:11 to bring workload within 1σ of team median.",
      counter_evidence: "Customer relationship continuity is a stated reason for not reassigning Orders 1019 and 1012 — these are high-value strategic accounts.",
    },
  },
  {
    canonical_key: "RT-MGR-SPAN",
    name: "What is the effective span of control for Employee:9?",
    status: "draft",
    confidence: 0.61,
    center_node: "Employee:9",
    depth: 2,
    limit: 100,
    source: "graph",
    updated_at: "2026-05-18 01:42",
    finding: null,
  },
  {
    canonical_key: "RT-CUS88-RISK",
    name: "Is Customer:88 a concentration risk?",
    status: "blocked",
    confidence: 0,
    center_node: "Customer:88",
    depth: 1,
    limit: 200,
    source: "graph",
    updated_at: "2026-05-17 22:30",
    finding: null,
    blocker: "Customer ObjectType is proposed, not approved — approved-only gate active.",
  },
  {
    canonical_key: "RT-TENURE-CORR",
    name: "Does tenure correlate with order-cycle time?",
    status: "approved",
    confidence: 0.88,
    center_node: "Employee:*",
    depth: 1,
    limit: 220,
    source: "manual",
    updated_at: "2026-05-16 18:04",
    finding: {
      conclusion: "Tenure band 7y+ shows a 22% shorter median Order cycle time than <1y band, controlling for region. Effect is significant (n=84, p<0.01).",
      title: "Tenure correlates with cycle time",
      status: "approved",
    },
  },
  {
    canonical_key: "RT-REG-PARITY",
    name: "Are NE-region quotas hitting parity post-Q1 rebalance?",
    status: "rejected",
    confidence: 0.34,
    center_node: "Region:NE",
    depth: 2,
    limit: 200,
    source: "graph",
    updated_at: "2026-05-15 09:21",
    finding: {
      conclusion: "Inconclusive — Region ObjectType still rejected; cannot scope query against approved graph.",
      status: "rejected",
    },
    blocker: "Region:NE not in approved scope.",
  },
];

const MOCK_EVIDENCE = [
  { kind: "fact",       title: "Employee:4 owns 47 active Orders (>2σ over team median of 12.4)",                src: "graph://acme-prod · OwnedBy", conf: 0.97 },
  { kind: "fact",       title: "No Order reassignments from Employee:4 in 90 days",                              src: "audit://Order.assignments",   conf: 0.94 },
  { kind: "hypothesis", title: "Manager Employee:9 does not have escalation triggers for >2σ workload",          src: "policy://hr-handbook §6.1",   conf: 0.71 },
  { kind: "conflict",   title: "Orders 1019, 1012 marked strategic — reassignment carries customer-relationship cost", src: "row://Order#1019.notes", conf: 0.82 },
  { kind: "missing",    title: "Tenure-weighted workload formula not yet approved as Property",                  src: "audit://pending",              conf: null },
];

function fmtTime(raw) {
  if (!raw) return "—";
  let s = String(raw).trim();
  if (!/Z$|[+-]\d{2}:?\d{2}$/.test(s)) s += "Z";
  const d = new Date(s.replace(" ", "T"));
  if (isNaN(d)) return String(raw).slice(0, 16);
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const RUNNING_STATES = new Set(["active", "running", "in_progress", "pending", "queued", "started"]);

function asNumberRX(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function pctRX(value, digits = 2) {
  const n = asNumberRX(value);
  return n == null ? "—" : `${(n * 100).toFixed(digits)}%`;
}

function moneyRX(value) {
  const n = asNumberRX(value);
  return n == null ? "—" : `$${n.toFixed(2)}`;
}

function firstAggregatePayload(finding) {
  const evidence = (finding && finding.supporting_evidence) || [];
  const aggregate = evidence.find(e => e && e.payload && (
    e.payload.counts || e.payload.flags || e.payload.high_risk_examples
  ));
  return aggregate ? aggregate.payload : null;
}

function MetricTile({ label, value, sub, tone }) {
  return (
    <div style={{ border: "1px solid var(--line)", background: "var(--bg-1)", padding: 10, minWidth: 0 }}>
      <div className="eyebrow" style={{ marginBottom: 6 }}>{label}</div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: tone || "var(--text)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 4, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {sub}
        </div>
      )}
    </div>
  );
}

function FraudFindingSummary({ finding }) {
  const payload = firstAggregatePayload(finding);
  if (!payload) return null;
  const counts = payload.counts || {};
  const amounts = payload.amounts || {};
  const flags = payload.flags || {};
  const posMissing = (payload.pos_entry || []).find(p => p.posEntryMode == null);
  const categories = (payload.category_top || []).slice(0, 4);
  const examples = (payload.high_risk_examples || []).slice(0, 3);
  return (
    <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="eyebrow">Fraud risk summary</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
        <MetricTile label="Rows" value={counts.rows_total != null ? Number(counts.rows_total).toLocaleString() : "—"} />
        <MetricTile label="Fraud rate" value={pctRX(counts.fraud_rate)} tone="var(--rejected)" />
        <MetricTile label="Fraud tx" value={counts.fraud_count != null ? Number(counts.fraud_count).toLocaleString() : "—"} />
        <MetricTile label="Fraud avg amount" value={moneyRX(amounts.avg_fraud_amount)} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
        <MetricTile label="Card-not-present" value={pctRX(flags.fraud_cnp)} sub={`${Number(flags.cnp_count || 0).toLocaleString()} tx`} />
        <MetricTile label="cvvMatch=false" value={pctRX(flags.fraud_cvv_mismatch)} sub={`${Number(flags.cvv_mismatch_count || 0).toLocaleString()} tx`} />
        <MetricTile label="POS entry missing" value={pctRX(posMissing && posMissing.fraud_rate)} sub={`${Number(posMissing && posMissing.cnt || 0).toLocaleString()} tx`} />
        <MetricTile label="Duplicate samples" value={(payload.duplicate_pattern || []).length ? String((payload.duplicate_pattern || []).length) : "—"} sub="same account/merchant/amount/day" />
      </div>
      {categories.length > 0 && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>High-risk merchant categories</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {categories.map(cat => (
              <span key={cat.category} className="pill" style={{ borderColor: "var(--accent-line)", background: "var(--accent-bg)" }}>
                {cat.category} · {pctRX(cat.fraud_rate)}
              </span>
            ))}
          </div>
        </div>
      )}
      {examples.length > 0 && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>High-risk examples</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
            {examples.map(ex => (
              <div key={ex.transaction_id} style={{ border: "1px solid var(--line)", background: "var(--bg-1)", padding: 10, minWidth: 0 }}>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)" }}>tx {ex.transaction_id}</div>
                <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {ex.merchantName} · {ex.merchantCategoryCode}
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 6 }}>
                  {moneyRX(ex.transactionAmount)} · cardPresent={String(Boolean(Number(ex.cardPresent)))} · cvvMatch={String(Boolean(Number(ex.cvvMatch)))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
        Evidence boundary: deterministic SQL aggregates over the safe transaction view; raw CVV values are not required for this reasoning surface.
      </div>
    </div>
  );
}
const STALE_THRESHOLD_MS = 5 * 60 * 1000;

function escapeRegExpRX(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function canonicalTypeFromListRX(raw, types) {
  const compact = String(raw || "").replace(/[^a-z0-9]/gi, "").toLowerCase();
  return (types || []).find(t => String(t || "").replace(/[^a-z0-9]/gi, "").toLowerCase() === compact) || "";
}

function tenantEmptyQuestionRX(tenantId) {
  if (tenantId === "maritime-risk") return "Select a chokepoint, country, dependency, or risk result to analyze propagation risk.";
  return tenantId === "creditcardfraud"
    ? "Select a transaction, account, card, or merchant to analyze fraud risk."
    : "Select a center node to ask a scoped question.";
}

function autopilotObjectiveForTenantRX(tenantId) {
  if (tenantId === "maritime-risk") return "Find graph reasoning findings for maritime chokepoint risk";
  if (tenantId === "creditcardfraud") return "Find high-value fraud risk findings";
  return "Find high-value reasoning findings";
}

function defaultQuestionForTenantRX(tenantId, type, label, node) {
  const typeText = String(type || "").replace(/([a-z])([A-Z])/g, "$1 $2");
  const lower = typeText.toLowerCase();
  if (tenantId === "maritime-risk") {
    if (/chokepoint/i.test(type)) return `Which countries are most exposed to ${label}?`;
    if (/country/i.test(type)) return `Which chokepoint dependencies create the highest risk for ${label}?`;
    if (/dependency/i.test(type)) return `Explain the risk path for ${label}`;
    if (/risk/i.test(type) || /hazard/i.test(type)) return `What evidence supports this maritime risk signal for ${label}?`;
    return `Find maritime chokepoint risk findings for ${label || node || lower}`;
  }
  if (tenantId === "creditcardfraud") {
    if (/transaction/i.test(type)) return `Explain fraud risk signals for ${label}`;
    if (/account/i.test(type)) return `Summarize fraud exposure and suspicious activity for ${label}`;
    if (/card/i.test(type)) return `Review verification, channel, and merchant risk signals for ${label}`;
    if (/merchant/i.test(type)) return `Which fraud patterns are concentrated around ${label}?`;
    return `Find high-value fraud risk patterns for ${label || node || lower}`;
  }
  return label ? `Give a summary of ${label}` : `Which ${typeText}s have the highest activity?`;
}

function suggestedQuestionsForTenantRX({ tenantId, type, centerNode, label, question, entities }) {
  const q = (question || "").trim();
  const samples = (entities || []).slice(0, 2);
  const typeText = String(type || "").replace(/([a-z])([A-Z])/g, "$1 $2");
  const plural = typeText.endsWith("s") ? typeText : `${typeText}s`;
  const hasEntity = !!(centerNode && label);
  if (tenantId === "maritime-risk") {
    if (hasEntity) {
      if (q) {
        const base = q.toLowerCase().includes(String(label).toLowerCase()) || q.includes(centerNode) ? q : `${q} — ${label}`;
        return [
          { q: base, node: centerNode },
          { q: `Show the hazard -> chokepoint -> country -> risk metric path for ${label}`, node: centerNode },
          { q: `Which dependent countries or chokepoints should be prioritized from ${label}?`, node: centerNode },
          { q: `What action should be created from ${label}'s maritime risk evidence?`, node: centerNode },
        ];
      }
      return [
        { q: defaultQuestionForTenantRX(tenantId, type, label, centerNode), node: centerNode },
        { q: `What evidence supports the risk propagation path for ${label}?`, node: centerNode },
        { q: `Which downstream countries or trade metrics are affected by ${label}?`, node: centerNode },
      ];
    }
    const base = samples.map(ent => ({
      q: q ? `${q} — ${ent.label || ent.id}` : defaultQuestionForTenantRX(tenantId, type, ent.label || ent.id, ent.id),
      node: ent.id,
    }));
    if (base.length) {
      base.push({ q: `Which ${plural} produce the strongest multi-hop risk chain?`, node: base[0].node });
      return base;
    }
    return [{ q: tenantEmptyQuestionRX(tenantId), node: "" }];
  }
  if (tenantId === "creditcardfraud") {
    if (hasEntity) {
      if (q) {
        const base = q.toLowerCase().includes(String(label).toLowerCase()) || q.includes(centerNode) ? q : `${q} — ${label}`;
        return [
          { q: base, node: centerNode },
          { q: `What evidence supports this fraud-risk interpretation for ${label}?`, node: centerNode },
          { q: `Which merchant/channel/POS signals explain risk for ${label}?`, node: centerNode },
          { q: `What follow-up action should an analyst take for ${label}?`, node: centerNode },
        ];
      }
      return [
        { q: defaultQuestionForTenantRX(tenantId, type, label, centerNode), node: centerNode },
        { q: `What evidence supports the risk profile for ${label}?`, node: centerNode },
        { q: `Which transaction patterns around ${label} need review?`, node: centerNode },
        { q: `What action should be created from ${label}'s risk signals?`, node: centerNode },
      ];
    }
    const base = [];
    for (const ent of samples) {
      const label = ent.label || ent.id;
      base.push({ q: q ? `${q} — ${label}` : defaultQuestionForTenantRX(tenantId, type, label, ent.id), node: ent.id });
    }
    if (base.length) {
      base.push({ q: `Which ${plural} should Autopilot investigate next?`, node: base[0].node });
      return base;
    }
    return [{ q: tenantEmptyQuestionRX(tenantId), node: "" }];
  }
  if (hasEntity) {
    if (q) {
      const mentionsEntity = q.toLowerCase().includes(String(label).toLowerCase()) || q.includes(centerNode);
      const base = mentionsEntity ? q : `${q} — ${label}`;
      return [
        { q: base, node: centerNode },
        { q: `${base}, compared to other ${plural}`, node: centerNode },
        { q: `What evidence supports "${q}" for ${label}?`, node: centerNode },
        { q: `Give a complete summary of ${label}`, node: centerNode },
      ];
    }
    return [
      { q: `Give a summary of ${label}`, node: centerNode },
      { q: `What are the key relationships for ${label}?`, node: centerNode },
      { q: `How does ${label} compare to other ${plural}?`, node: centerNode },
      { q: `Are there any anomalies or risks related to ${label}?`, node: centerNode },
    ];
  }
  if (type) {
    const out = samples.map(ent => ({
      q: q ? `${q} — ${ent.label || ent.id}` : `Give a summary of ${ent.label || ent.id}`,
      node: ent.id,
    }));
    if (out.length) {
      out.push({ q: `Which ${plural} have the highest activity?`, node: out[0].node });
      out.push({ q: `Are there anomalies among ${plural}?`, node: out[0].node });
      return out;
    }
  }
  return [{ q: tenantEmptyQuestionRX(tenantId), node: "" }];
}

function Reasoning({ tenant }) {
  const [selectedKey, setSelectedKey] = useStateRX(null);
  const [activeTab, setActiveTab] = useStateRX("mine");  // mine | all | graph | autopilot
  const [question, setQuestion] = useStateRX("");
  const [centerNode, setCenterNode] = useStateRX("");
  const [depth, setDepth] = useStateRX(1);
  const [limit, setLimit] = useStateRX(200);
  const [followup, setFollowup] = useStateRX("");
  const [reviewReason, setReviewReason] = useStateRX("");
  const [autopilotReviewReason, setAutopilotReviewReason] = useStateRX("");
  const [autopilotReviewTargetKey, setAutopilotReviewTargetKey] = useStateRX("");
  const [autopilotReviewMissingKey, setAutopilotReviewMissingKey] = useStateRX("");
  const [highlightedFindingKey, setHighlightedFindingKey] = useStateRX("");
  const [autopilotObjective, setAutopilotObjective] = useStateRX("Find high-value fraud risk findings");
  const [autopilotMaxHypotheses, setAutopilotMaxHypotheses] = useStateRX(8);
  const [autopilotMaxRuns, setAutopilotMaxRuns] = useStateRX(5);
  const [autopilotMaxToolCalls, setAutopilotMaxToolCalls] = useStateRX(20);
  const [autopilotSelectedKey, setAutopilotSelectedKey] = useStateRX(null);
  const [autopilotStarting, setAutopilotStarting] = useStateRX(false);
  const [autopilotPlaybookRunning, setAutopilotPlaybookRunning] = useStateRX(false);
  const [registryFilters, setRegistryFilters] = useStateRX({
    status: "approved",
    context: "active",
    sort: "newest_reviewed",
    group: "",
    finding_type: "",
    source: "",
    action_state: "",
    freshness: "",
  });
  const [actionMsg, setActionMsg] = useStateRX(null);
  const [running, setRunning] = useStateRX(false);
  const [askMode, setAskMode] = useStateRX(false);
  const [submitting, setSubmitting] = useStateRX(false);

  const [scopeTypes, setScopeTypes] = useStateRX([]);
  const [scopeBootstrapKey, setScopeBootstrapKey] = useStateRX("");
  const typeNames = scopeTypes.map(t => typeof t === "string" ? t : (t.type || t.label)).filter(Boolean);
  const NODE_RE = typeNames.length
    ? new RegExp("\\b(" + typeNames.map(escapeRegExpRX).join("|") + ")[:\\s#]+([\\w*.-]+)\\b", "i")
    : /\b([A-Za-z][A-Za-z0-9_]*?)[:\s#]+([\w*.-]+)\b/i;
  function onQuestionChangeWithExtract(e) {
    const q = e.target.value;
    setQuestion(q);
    const m = q.match(NODE_RE);
    if (m) {
      const type = canonicalTypeFromListRX(m[1], typeNames) || m[1];
      setCenterNode(type + ":" + m[2]);
    }
  }
  const [evidenceFilter, setEvidenceFilter] = useStateRX("all");
  const [localTasks, setLocalTasks] = useStateRX([]);  // mock-mode submitted tasks
  // live SSE trace, keyed by canonical_key so it persists when user switches tasks
  const [traceByKey, setTraceByKey] = useStateRX({});
  const streamRef = useRefRX(null);

  const tasksQ = useApiData("reasoningTasks", [tenant ? tenant.id : "default"], { fallback: MOCK_TASKS });
  const autopilotSessionsQ = useApiData("autopilotSessions", [tenant ? tenant.id : "default"], { fallback: [] });
  const approvedFindingsQ = useApiData(
    "reasoningFindings",
    [tenant ? tenant.id : "default", { ...registryFilters, limit: 24 }],
    { fallback: { findings: [] } }
  );
  const isStale = tasksQ.source === "live-stale";
  const isMock  = tasksQ.source === "mock";
  const autopilotSessions = autopilotSessionsQ.data || [];
  const approvedFindingsRegistry = (approvedFindingsQ.data && approvedFindingsQ.data.findings) || [];
  const autopilotDetailQ = useApiData(
    "autopilotSession",
    [autopilotSelectedKey, tenant ? tenant.id : "default"],
    { enabled: activeTab === "autopilot" && !!autopilotSelectedKey }
  );
  const autopilotDetail = autopilotDetailQ.data || null;
  useEffectRX(() => {
    const tid = tenant ? tenant.id : "default";
    setAutopilotObjective(autopilotObjectiveForTenantRX(tid));
    setAutopilotSelectedKey(null);
  }, [tenant ? tenant.id : "default"]);
  useEffectRX(() => {
    let alive = true;
    const tid = tenant ? tenant.id : "default";
    (async () => {
      try {
        const typeData = await window.AL_API.fetchJson("/api/instances/types?tenant=" + encodeURIComponent(tid));
        if (!alive) return;
        const types = typeData.types || [];
        setScopeTypes(types);
        const currentType = centerNode && centerNode.includes(":") ? centerNode.split(":")[0] : "";
        const typeNamesLocal = types.map(t => typeof t === "string" ? t : (t.type || t.label)).filter(Boolean);
        const currentValid = currentType && typeNamesLocal.some(t => canonicalTypeFromListRX(currentType, [t]) === t);
        const bootstrapKey = tid + "|" + typeNamesLocal.join(",");
        if (currentValid && scopeBootstrapKey === bootstrapKey) return;
        const firstType = typeNamesLocal[0] || "";
        if (!firstType) {
          setCenterNode("");
          setQuestion(tenantEmptyQuestionRX(tid));
          setScopeBootstrapKey(bootstrapKey);
          return;
        }
        const qs = new URLSearchParams({ tenant: tid, type: firstType, q: "", limit: "1" });
        const searchData = await window.AL_API.fetchJson("/api/instances/search?" + qs.toString());
        if (!alive) return;
        const first = (searchData.instances || [])[0];
        const nextNode = first ? first.id : "";
        const nextLabel = first ? (first.label || first.id) : firstType;
        setCenterNode(nextNode);
        setQuestion(defaultQuestionForTenantRX(tid, firstType, nextLabel, nextNode));
        setScopeBootstrapKey(bootstrapKey);
      } catch (_) {
        if (!alive) return;
        setScopeTypes([]);
        setCenterNode("");
        setQuestion(tenantEmptyQuestionRX(tenant ? tenant.id : "default"));
      }
    })();
    return () => { alive = false; };
  }, [tenant ? tenant.id : "default"]);
  useEffectRX(() => {
    if (activeTab !== "autopilot") return;
    if (!autopilotSessions.length) { setAutopilotSelectedKey(null); return; }
    if (autopilotSessions.some(s => s.session_key === autopilotSelectedKey)) return;
    setAutopilotSelectedKey(autopilotSessions[0].session_key);
  }, [activeTab, autopilotSessions.map(s => s.session_key).join("|")]);
  // stable, deduped, sorted task list (local optimistic adds + server data)
  const allTasks = useMemoRX(() => {
    const merged = [...localTasks, ...(tasksQ.data || [])];
    const seen = new Set();
    const out = [];
    for (const t of merged) {
      const k = t.canonical_key || t.id;
      if (k && seen.has(k)) continue;
      if (k) seen.add(k);
      out.push(t);
    }
    return out;
  }, [localTasks, tasksQ.data]);

  const STATUS_ORDER = { active: 0, running: 0, in_progress: 0, pending: 0, queued: 0, started: 0 };
  function taskSortCmp(a, b) {
    const sa = STATUS_ORDER[((a.status || "").toLowerCase())] ?? 1;
    const sb = STATUS_ORDER[((b.status || "").toLowerCase())] ?? 1;
    if (sa !== sb) return sa - sb;
    const ca = a.created_at || "";
    const cb = b.created_at || "";
    return ca > cb ? -1 : ca < cb ? 1 : 0;
  }

  const isActiveTask = t => !new Set(["completed", "closed", "approved", "rejected"]).has((t.status || "").toLowerCase());

  const tasks = useMemoRX(() => {
    switch (activeTab) {
      case "mine":    return [...allTasks.filter(t => t.source === "manual")].sort(taskSortCmp);
      case "graph":   return [...allTasks.filter(t => t.source === "graph")].sort(taskSortCmp);
      case "autopilot": return [];
      default:        return allTasks.filter(isActiveTask);
    }
  }, [allTasks, activeTab]);

  const counts = {
    all:     allTasks.filter(isActiveTask).length,
    mine:    allTasks.filter(t => t.source === "manual").length,
    graph:   allTasks.filter(t => t.source === "graph").length,
    autopilot: autopilotSessions.length,
    approved: approvedFindingsRegistry.length,
  };

  const pendingKeyRef = useRefRX(null);
  useEffectRX(() => {
    try {
      const key = new URLSearchParams(location.search).get("task");
      if (!key) return;
      pendingKeyRef.current = key;
      setSelectedKey(key);
    } catch {}
  }, [tenant ? tenant.id : "default"]);
  useEffectRX(() => {
    if (!tasks.length) { setSelectedKey(null); return; }
    if (tasks.some(t => t.canonical_key === selectedKey)) {
      pendingKeyRef.current = null;
      return;
    }
    if (pendingKeyRef.current && pendingKeyRef.current === selectedKey) return;
    setSelectedKey(tasks[0].canonical_key);
  }, [activeTab, tasks.map(t => t.canonical_key).join("|")]);

  const detailQ = useApiData(
    "reasoningTask",
    [selectedKey, tenant ? tenant.id : "default"],
    { enabled: !!selectedKey }
  );
  // Use list-item immediately when user clicks; detail backfills when it arrives.
  // This prevents the "click does nothing" feeling while /api/reasoning/tasks/{key} loads.
  const fromList = tasks.find(t => t.canonical_key === selectedKey) || tasks[0];
  const detailMatchesSelection = detailQ.data
    && (detailQ.data.canonical_key === selectedKey
        || (detailQ.data.task && detailQ.data.task.canonical_key === selectedKey));
  const task = useMemoRX(() => {
    if (!selectedKey) return null;
    if (detailMatchesSelection) {
      // server response may be {task: {...}} or the task itself
      return detailQ.data.task || detailQ.data;
    }
    return fromList || null;
  }, [detailMatchesSelection, detailQ.data, fromList, selectedKey]);
  const finding = task && task.finding;
  const evidence = (task && task.evidence_paths) || [];
  const isLoadingDetail = !!selectedKey && detailQ.loading && !detailMatchesSelection;

  // Sync form fields when selected task changes OR when detail loads richer data
  const _syncKey = task && task.canonical_key;
  const _syncNode = task && task.center_node;
  const _syncQ = task && (task.question || task.name);
  useEffectRX(() => {
    if (!task) return;
    setQuestion(task.question || task.name || "");
    setCenterNode(task.center_node || "");
    setDepth(task.depth || 1);
    setLimit(task.limit || 200);
  }, [_syncKey, _syncNode, _syncQ]);

  // ----- POLLING -----
  // When the selected task is in a running-ish state, poll detail every 2.5s
  // until it lands on a terminal state. This is how an async backend's
  // POST /run becomes visible: it just flips status → we keep refreshing.

  function ageMs(t) {
    const raw = t && (t.updated_at || t.created_at || t.started_at);
    if (!raw) return null;
    // Backend writes UTC timestamps but often without a 'Z' suffix
    // (e.g. "2026-05-19 03:14:00" or "2026-05-19T03:14:00"). The browser
    // would then parse those as LOCAL time and we'd be off by tz offset
    // (8h in GMT+8 etc.) — the classic "8h stale" bug. Normalize first.
    let s = String(raw).trim();
    const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(s);
    if (!hasTz) {
      // replace space with T so Date.parse handles it as ISO, append Z
      s = s.replace(" ", "T") + "Z";
    }
    const ms = Date.parse(s);
    if (isNaN(ms)) return null;
    return Date.now() - ms;
  }
  function ageLabel(ms) {
    if (ms == null) return "—";
    const s = Math.floor(ms / 1000);
    if (s < 60)  return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    return Math.floor(s / 86400) + "d";
  }
  function taskState(t) {
    const status = (t.status || "").toLowerCase();
    const rd = t.latest_run && ["completed", "failed", "error"].includes((t.latest_run.status || "").toLowerCase());
    const hasRunInProgress = t.latest_run && !rd;
    const isRunning = RUNNING_STATES.has(status) && hasRunInProgress;
    const a = ageMs(t);
    const isStale = isRunning && a != null && a > STALE_THRESHOLD_MS;
    return { isRunning, isStale, runDone: !!rd, age: a, ageLbl: ageLabel(a) };
  }

  // If the latest_run already completed, the task is NOT genuinely running
  // even if status is still "active" (backend bug / orphan).
  const runDone = task && task.latest_run
    && ["completed", "failed", "error"].includes((task.latest_run.status || "").toLowerCase());
  const hasRunInProgress = task && task.latest_run && !runDone;
  const isTaskRunning = task && RUNNING_STATES.has((task.status || "").toLowerCase()) && hasRunInProgress;
  const selectedState = task ? taskState(task) : null;
  const isStaleActive = selectedState && selectedState.isStale;

  // ----- cleanup -----
  const [cleanupModal, setCleanupModal] = useStateRX(false);
  const [pollTick, setPollTick] = useStateRX(0);
  // when the task is stale-active, stop polling — it isn't going to change
  useEffectRX(() => {
    if (!isTaskRunning) return;
    if (isStaleActive) return;
    const interval = setInterval(() => {
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
      setPollTick(n => n + 1);
    }, 2500);
    return () => clearInterval(interval);
  }, [isTaskRunning, isStaleActive, task && task.canonical_key]);

  // live trace for the currently-selected task
  const liveTrace = (task && traceByKey[task.canonical_key]) || [];

  const showRunning = running;
  const backendRunning = isTaskRunning && !isStaleActive && !running;
  const isClosed = task && (task.status || "").toLowerCase() === "closed";
  const isTerminal = task && !isActiveTask(task);
  const shouldRerun = !!(isClosed || isTerminal);


  async function stopAndClose() {
    if (!task) return;
    if (streamRef.current && streamRef.current.close) {
      try { streamRef.current.close(); } catch {}
      streamRef.current = null;
    }
    setRunning(false);
    try {
      await window.AL_API.closeTask(task.canonical_key, tenant.id);
      setActionMsg({ kind: "ok", msg: "Task stopped and closed." });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (e) {
      setActionMsg({ kind: "err", msg: e.message || String(e) });
    }
  }

  async function runTask() {
    if (!task) return;
    setActionMsg(null);
    try {
      if (shouldRerun) {
        const baseQ = task.question || task.name || task.canonical_key;
        const payload = {
          question: baseQ,
          nonce: Date.now().toString(36),
          center_node: task.center_node,
          depth: task.depth || 1,
          limit: task.limit || 200,
        };

        setAskMode(false);
        setActiveTab("mine");
        setActionMsg({ kind: "ok", msg: "Creating new task…" });

        const res = await window.AL_API.submitQuestion(tenant.id, payload);

        const newKey =
          res?.canonical_key ||
          res?.id ||
          res?.task_key ||
          res?.key ||
          res?.task?.canonical_key ||
          res?.task?.id;

        if (!newKey) {
          setActionMsg({ kind: "err",
            msg: "Server didn't return a recognizable task key. Response: " + JSON.stringify(res).slice(0, 200)
          });
          return;
        }

        const optimisticTask = {
          canonical_key: newKey,
          name: baseQ,
          question: baseQ,
          status: "active",
          center_node: payload.center_node,
          depth: payload.depth,
          limit: payload.limit,
          source: "manual",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        setLocalTasks(prev => [optimisticTask, ...prev.filter(t => t.canonical_key !== newKey)]);
        pendingKeyRef.current = newKey;
        setSelectedKey(newKey);
        setActionMsg({ kind: "ok", msg: `New task created · ${newKey}` });
        setTimeout(() => {
          window.dispatchEvent(new CustomEvent("aletheia:retry"));
        }, 100);

        setRunning(true);
        streamRun(newKey, res);
        return;
      }
      setRunning(true);
      streamRun(task.canonical_key);
    } catch (e) {
      const hint = e.status === 400 ? " · check task state on the server" : "";
      setActionMsg({ kind: "err", msg: (e.message || String(e)) + hint });
      setRunning(false);
    }
  }

  // Streaming run — opens SSE, populates trace, falls back to sync /run on error.
  function streamRun(taskKey, submitResponse) {
    // close any prior stream
    if (streamRef.current && streamRef.current.close) {
      try { streamRef.current.close(); } catch {}
    }
    // reset trace for this task — and seed it with submission response if given
    const seed = submitResponse ? [{
      eventName: "_diag",
      stage: "submitted",
      data: { response: submitResponse },
      ts: new Date(),
    }] : [];
    setTraceByKey(prev => ({ ...prev, [taskKey]: seed }));

    let fellBackToSync = false;
    streamRef.current = window.AL_API.runReasoningStream(taskKey, tenant.id, {
      onDiag: (stage, info) => {
        // Surface transport-level diagnostics into the trace so the user can
        // see EXACTLY what's happening — "connecting", "first chunk arrived",
        // "Content-Type wrong", "CORS error", etc.
        setTraceByKey(prev => {
          const list = prev[taskKey] || [];
          return { ...prev, [taskKey]: [...list, {
            eventName: "_diag",
            stage,
            data: info,
            ts: new Date(),
          }] };
        });
      },
      onEvent: (eventName, data) => {
        setTraceByKey(prev => {
          const list = prev[taskKey] || [];
          return { ...prev, [taskKey]: [...list, { eventName, data, ts: new Date() }] };
        });
      },
      onError: async (err) => {
        if (fellBackToSync) return;
        fellBackToSync = true;
        setTraceByKey(prev => {
          const list = prev[taskKey] || [];
          return { ...prev, [taskKey]: [...list, {
            eventName: "stream_error",
            data: { message: err.message || String(err), fallback: "trying sync /run" },
            ts: new Date(),
          }] };
        });
        try {
          await window.AL_API.runReasoning(taskKey, tenant.id);
          setActionMsg({ kind: "ok", msg: "Stream failed; ran via sync /run instead." });
          window.dispatchEvent(new CustomEvent("aletheia:retry"));
        } catch (e2) {
          setActionMsg({ kind: "err", msg: "Stream + sync both failed: " + (e2.message || String(e2)) });
        } finally {
          setRunning(false);
        }
      },
      onComplete: async () => {
        setRunning(false);
        try {
          const fresh = await window.AL_API.reasoningTask(taskKey, tenant.id);
          if (fresh) {
            const t = fresh.task || fresh;
            t.latest_run = fresh.latest_run || t.latest_run;
            t.findings = fresh.findings || [];
            if (t.findings.length && !t.finding) t.finding = t.findings[0];
            setLocalTasks(prev => [t, ...prev.filter(x => x.canonical_key !== t.canonical_key)]);
          }
        } catch (_) {}
        window.dispatchEvent(new CustomEvent("aletheia:retry"));
      },
    });
  }

  // close stream on unmount
  useEffectRX(() => {
    return () => {
      if (streamRef.current && streamRef.current.close) {
        try { streamRef.current.close(); } catch {}
      }
    };
  }, []);

  async function submitQuestion(e, questionOverride) {
    if (e && e.preventDefault) e.preventDefault();
    const q = questionOverride || question;
    if (!q.trim()) { setActionMsg({ kind: "err", msg: "Question is required." }); return; }
    if (!centerNode || !centerNode.includes(":")) {
      setActionMsg({ kind: "err", msg: "Select a tenant object as the center node before submitting." });
      return;
    }
    const centerType = centerNode.split(":")[0];
    if (typeNames.length && !canonicalTypeFromListRX(centerType, typeNames)) {
      setActionMsg({ kind: "err", msg: `Center node ${centerNode} is not valid for tenant ${tenant ? tenant.id : "default"}.` });
      return;
    }
    setSubmitting(true);
    setActionMsg(null);
    try {
      const res = await window.AL_API.submitQuestion(tenant.id, {
        question: q, center_node: centerNode, depth, limit,
      });
      setActionMsg({ kind: "ok", msg: "Scoped question created · " + (res.canonical_key || res.id || "") });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
      if (res.canonical_key) {
        pendingKeyRef.current = res.canonical_key;
        setSelectedKey(res.canonical_key);
        setActiveTab("mine");
        setAskMode(false);
      }
    } catch (e) {
      setActionMsg({ kind: "err", msg: e.message || String(e) });
    } finally {
      setSubmitting(false);
    }
  }

  async function reviewFinding(action) {
    if (!finding || !task) return;
    if ((action === "approve" || action === "reject" || action === "needs-evidence" || action === "mark-stale" || action === "supersede" || action === "reaffirm" || action === "comment") && !reviewReason.trim()) {
      setActionMsg({ kind: "err", msg: "Reason required for finding review." }); return;
    }
    try {
      await window.AL_API.reviewFinding(
        finding.canonical_key || task.canonical_key,
        action,
        { reason: reviewReason.trim(), reviewer: "M. Aoki" },
        tenant.id,
      );
      setActionMsg({ kind: "ok", msg: `Finding ${action} recorded.` });
      setReviewReason("");
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (e) {
      setActionMsg({ kind: "err", msg: e.message || String(e) });
    }
  }

  async function startAutopilot(e) {
    if (e && e.preventDefault) e.preventDefault();
    if (!autopilotObjective.trim()) {
      setActionMsg({ kind: "err", msg: "Autopilot objective is required." });
      return;
    }
    setAutopilotStarting(true);
    setActionMsg(null);
    try {
      const tid = tenant ? tenant.id : "default";
      const isFraudTenant = tid === "creditcardfraud";
      const isMaritimeTenant = tid === "maritime-risk";
      const res = await window.AL_API.createAutopilotSession(tid, {
        objective: autopilotObjective.trim(),
        scope: {
          tenant: tid,
          approved_only: true,
          source_surface: "reasoning_autopilot_ui",
          ...(isFraudTenant ? { table: "credit_card_transactions_safe" } : {}),
          ...(isMaritimeTenant ? { tables: ["maritime_chokepoint_country_dependencies", "maritime_chokepoint_risk_indicators", "maritime_chokepoint_systemic_risk_results"] } : {}),
        },
        budget: {
          max_hypotheses: Number(autopilotMaxHypotheses) || 8,
          max_reasoning_tasks: Number(autopilotMaxRuns) || 5,
          max_tool_calls: Number(autopilotMaxToolCalls) || 20,
          max_runtime_seconds: 120,
        },
        safety_profile: {
          approved_only: true,
          safe_views_only: true,
          allow_sensitive_fields: false,
          blocked_fields: isFraudTenant ? ["card_verification_code_fields"] : [],
        },
        created_by: "Reasoning Autopilot UI",
      });
      const key = res?.session?.session_key;
      if (key) setAutopilotSelectedKey(key);
      setActiveTab("autopilot");
      setActionMsg({ kind: "ok", msg: "Autopilot session started · " + (key || "") });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg({ kind: "err", msg: err.message || String(err) });
    } finally {
      setAutopilotStarting(false);
    }
  }

  async function reviewAutopilotCandidate(candidate, status) {
    if (!candidate || !autopilotDetail?.session) return;
    setAutopilotReviewTargetKey(candidate.canonical_key);
    if ((status === "rejected" || status === "needs_more_evidence") && !autopilotReviewReason.trim()) {
      setAutopilotReviewMissingKey(candidate.canonical_key);
      setActionMsg({ kind: "err", msg: "Add a candidate review note before rejecting or requesting more evidence." });
      return;
    }
    try {
      setAutopilotReviewMissingKey("");
      const action = status === "needs_more_evidence" ? "needs-evidence" : status === "approved" ? "approve" : "reject";
      const res = await window.AL_API.reviewAutopilotCandidate(
        candidate.canonical_key,
        action,
        { reason: autopilotReviewReason.trim(), reviewer: "M. Aoki" },
        tenant ? tenant.id : "default",
      );
      setAutopilotReviewReason("");
      setAutopilotReviewTargetKey("");
      if (status === "approved") {
        const findingKey = res?.finding?.canonical_key || res?.finding_key || "";
        setHighlightedFindingKey(findingKey);
        setRegistryFilters(prev => ({ ...prev, status: "approved", context: "active" }));
        setActiveTab("mine");
      }
      setActionMsg({
        kind: "ok",
        msg: status === "approved" ? "Added to Finding Registry. Opened Registry panel." : `Candidate marked ${status}.`,
      });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      let message = err.message || String(err);
      if (status === "approved" && /evidence[_ ]chain/i.test(message)) {
        message = "Missing evidence chain.";
      }
      setActionMsg({ kind: "err", msg: message });
    }
  }

  async function runCreditcardfraudPlaybook() {
    setAutopilotPlaybookRunning(true);
    setActionMsg(null);
    try {
      const tid = tenant ? tenant.id : "default";
      const res = await window.AL_API.runCreditcardfraudAutopilotPlaybook(tid, {
        objective: autopilotObjective.trim() || "Discover high-value credit card fraud risk findings",
        session_key: autopilotSelectedKey || undefined,
        budget: {
          max_hypotheses: Number(autopilotMaxHypotheses) || 8,
          max_reasoning_tasks: Number(autopilotMaxRuns) || 5,
          max_tool_calls: Number(autopilotMaxToolCalls) || 20,
          max_runtime_seconds: 120,
        },
      });
      const key = res?.session?.session_key;
      if (key) setAutopilotSelectedKey(key);
      setActiveTab("autopilot");
      setActionMsg({ kind: "ok", msg: "Creditcardfraud playbook completed · " + (key || "") });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg({ kind: "err", msg: err.message || String(err) });
    } finally {
      setAutopilotPlaybookRunning(false);
    }
  }

  async function runMaritimeRiskPlaybook() {
    setAutopilotPlaybookRunning(true);
    setActionMsg(null);
    try {
      const tid = tenant ? tenant.id : "default";
      const res = await window.AL_API.runMaritimeRiskAutopilotPlaybook(tid, {
        objective: autopilotObjective.trim() || "Discover graph reasoning findings for maritime chokepoint risk",
        session_key: autopilotSelectedKey || undefined,
        budget: {
          max_hypotheses: Number(autopilotMaxHypotheses) || 8,
          max_reasoning_tasks: Number(autopilotMaxRuns) || 5,
          max_tool_calls: Number(autopilotMaxToolCalls) || 20,
          max_runtime_seconds: 120,
        },
      });
      const key = res?.session?.session_key;
      if (key) setAutopilotSelectedKey(key);
      setActiveTab("autopilot");
      setActionMsg({ kind: "ok", msg: "Maritime-risk playbook completed · " + (key || "") });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg({ kind: "err", msg: err.message || String(err) });
    } finally {
      setAutopilotPlaybookRunning(false);
    }
  }

  async function deleteTask(taskKey) {
    if (!confirm("Delete this task? This cannot be undone.")) return;
    try {
      try { await window.AL_API.closeTask(taskKey, tenant.id); } catch (_) {}
      await window.AL_API.deleteTask(taskKey, tenant.id);
      setLocalTasks(prev => prev.filter(t => t.canonical_key !== taskKey));
      if (selectedKey === taskKey) setSelectedKey(null);
      setActionMsg({ kind: "ok", msg: "Task deleted." });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (e) {
      setActionMsg({ kind: "err", msg: e.message || String(e) });
    }
  }

  const statusToPill = { completed: "approved", approved: "approved", draft: "proposed", blocked: "rejected", running: "changes", active: "changes", closed: "rejected" };

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className={"tab" + (activeTab === "mine"    ? " active" : "")} onClick={() => setActiveTab("mine")}>My Questions <span className="ct">{counts.mine}</span></div>
          <div className={"tab" + (activeTab === "all"     ? " active" : "")} onClick={() => setActiveTab("all")}>Reasoning Process <span className="ct">{counts.all}</span></div>
          <div className={"tab" + (activeTab === "graph"   ? " active" : "")} onClick={() => setActiveTab("graph")}>From Graph <span className="ct">{counts.graph}</span></div>
          <div className={"tab" + (activeTab === "autopilot" ? " active" : "")} onClick={() => setActiveTab("autopilot")}>Autopilot <span className="ct">{counts.autopilot}</span></div>
        </div>
        <div className="spacer" />
        <button className="tool" onClick={() => setCleanupModal(true)}>Clean up</button>
        {isMock  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {tasksQ.loading && tasksQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Refresh</button>
        {activeTab !== "autopilot" && shouldRerun && (
          <button className="tool" onClick={runTask} disabled={running || !task}
                  title="Create a new task with the same question and scope, and run it.">
            {running ? "Rerunning…" : "↻ Rerun (new task)"}
          </button>
        )}
        {activeTab !== "autopilot" && !shouldRerun && !finding && !runDone && (
          <button className="tool" onClick={runTask} disabled={running || !task}>{running ? "Running…" : "▶ Run reasoning"}</button>
        )}
        {activeTab !== "autopilot" && task && !shouldRerun && (
          <button className="tool" onClick={stopAndClose}
                  style={{ color: "var(--rejected)" }}
                  title="Stop the current run (if any) and close this task.">
            ■ Stop &amp; close
          </button>
        )}
        <button className="tool primary" onClick={() => activeTab === "autopilot" ? startAutopilot() : setAskMode(true)}>
          {activeTab === "autopilot" ? "▶ Start Autopilot" : "+ Ask question"}
        </button>
      </div>

      <div className="wb">
        {/* ============ LEFT — task list ============ */}
        <div className="col">
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Question → Answer → Evidence</div>
            <div style={{ marginTop: 4, fontSize: 13, color: "var(--text)" }}>
              {activeTab === "autopilot" ? "Autopilot sessions" : "Reasoning tasks"}
            </div>
            <button className="btn primary" style={{ width: "100%", marginTop: 10 }}
                    onClick={() => activeTab === "autopilot" ? startAutopilot() : setAskMode(true)}>
              {activeTab === "autopilot" ? "▶ Start Autopilot" : "+ Ask a new question"}
            </button>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {activeTab === "autopilot" ? <ApiStatus q={autopilotSessionsQ} what="autopilot sessions" /> : <ApiStatus q={tasksQ} what="reasoning tasks" />}
            <div className="artifact-list">
              {activeTab === "autopilot" ? (
                <React.Fragment>
                  {(autopilotSessionsQ.source === "live" || autopilotSessionsQ.source === "mock") && autopilotSessions.length === 0 && (
                    <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center", lineHeight: 1.6 }}>
                      No Autopilot sessions yet. Start one to queue hypotheses and collect draft candidate findings.
                    </div>
                  )}
                  {autopilotSessions.map(s => (
                    <div key={s.session_key}
                         className={"artifact-row proposed" + (s.session_key === autopilotSelectedKey ? " selected" : "")}
                         onClick={() => setAutopilotSelectedKey(s.session_key)}>
                      <div className="ar-bar" />
                      <div className="ar-main">
                        <div className="ar-top">
                          <span className="type">AUTOPILOT</span>
                          <span>·</span>
                          <span className="key" style={{ color: "var(--text)" }}>{s.objective}</span>
                        </div>
                        <div className="ar-meta">
                          <span>{s.scope?.table || s.scope?.tenant || "tenant scope"}</span>
                          <span>{fmtTime(s.updated_at || s.created_at)}</span>
                        </div>
                      </div>
                      <div className="ar-right">
                        <Pill kind="proposed">{s.status || "draft"}</Pill>
                      </div>
                    </div>
                  ))}
                </React.Fragment>
              ) : (
                <React.Fragment>
              {(tasksQ.source === "live" || tasksQ.source === "mock") && tasks.length === 0 && (
                <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center", lineHeight: 1.6 }}>
                  {activeTab === "mine"    ? "No questions of yours yet. Click \u201c+ Ask a new question\u201d above to start." :
                   activeTab === "graph"   ? "No graph-derived reasoning tasks here." :
                                             "No active reasoning tasks. Click \u201c+ Ask a new question\u201d above."}
                </div>
              )}
              {tasks.map(t => {
                const ts = taskState(t);
                return (
                <div key={t.canonical_key}
                     className={`artifact-row ${statusToPill[t.status] || "proposed"}` + (t.canonical_key === selectedKey ? " selected" : "")}
                     onClick={() => setSelectedKey(t.canonical_key)}>
                  <div className="ar-bar" />
                  <div className="ar-main">
                    <div className="ar-top">
                      <span className="type">TASK</span>
                      <span>·</span>
                      <span className="key" style={{ color: "var(--text)" }}>{t.question || t.name || t.canonical_key}</span>
                      {ts.isRunning && !ts.isStale && (
                        <span style={{ marginLeft: "auto", color: "var(--accent)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                          <span style={{ width: 6, height: 6, background: "var(--accent)", display: "inline-block", animation: "pulse 1s ease-in-out infinite" }} />
                          running
                        </span>
                      )}
                      {ts.isStale && (
                        <span style={{ marginLeft: "auto", color: "var(--rejected)" }} title={"Active but no update for " + ts.ageLbl + " — likely orphaned"}>
                          ⚠ stale {ts.ageLbl}
                        </span>
                      )}
                    </div>
                    <div className="ar-meta">
                      <span>{t.center_node || "—"}</span>
                      {t.id != null && <span>#{t.id}</span>}
                      <span>{fmtTime(t.created_at)}</span>
                    </div>
                  </div>
                  <div className="ar-right" style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 4 }}>
                    <Pill kind={statusToPill[t.status] || "proposed"}>{t.status}</Pill>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      {ts.isRunning && !ts.isStale && (
                        <button className="btn xs ghost" title="Stop task"
                                onClick={e => { e.stopPropagation(); setSelectedKey(t.canonical_key); stopAndClose(); }}
                                style={{ padding: "4px 7px", fontSize: 12, color: "var(--rejected)", border: "1px solid oklch(0.66 0.18 25 / 0.3)", lineHeight: 1, borderRadius: 4 }}>
                          ■
                        </button>
                      )}
                      {!ts.isRunning && (
                        <React.Fragment>
                          <button className="btn xs ghost" title="Rerun (new task)"
                                  onClick={e => { e.stopPropagation(); setSelectedKey(t.canonical_key); setTimeout(runTask, 50); }}
                                  style={{ padding: "4px 7px", color: "var(--accent)", border: "1px solid var(--line)", lineHeight: 1, borderRadius: 4 }}>
                            <span style={{ display: "inline-flex", width: 16, height: 16 }} dangerouslySetInnerHTML={{ __html: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8a5.5 5.5 0 0 1 9.3-4"/><path d="M13.5 8a5.5 5.5 0 0 1-9.3 4"/><path d="M11.8 1.5V4h-2.5"/><path d="M4.2 14.5V12h2.5"/></svg>' }} />
                          </button>
                          <button className="btn xs ghost" title="Delete task"
                                  onClick={e => { e.stopPropagation(); deleteTask(t.canonical_key); }}
                                  style={{ padding: "4px 7px", color: "var(--muted)", border: "1px solid var(--line)", lineHeight: 1, borderRadius: 4 }}>
                            <span style={{ display: "inline-flex", width: 16, height: 16 }} dangerouslySetInnerHTML={{ __html: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 4h10M5.5 4V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1M4.5 4l.7 9.1a1 1 0 0 0 1 .9h3.6a1 1 0 0 0 1-.9L11.5 4"/></svg>' }} />
                          </button>
                        </React.Fragment>
                      )}
                      {t.confidence != null && (
                        <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--accent)", letterSpacing: "0.04em" }}>
                          {Math.round((t.confidence || 0) * 100)}%
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                );
              })}
                </React.Fragment>
              )}
            </div>
          </div>
        </div>

        {/* ============ CENTER — answer + evidence ============ */}
        <div className="col" style={{ display: "flex", flexDirection: "column" }}>
          {actionMsg && (
            <div style={{
              padding: "8px 14px",
              fontFamily: "var(--font-mono)", fontSize: 11,
              borderBottom: "1px solid " + (actionMsg.kind === "ok" ? "oklch(0.74 0.13 165 / 0.4)" : "oklch(0.78 0.14 75 / 0.4)"),
              color: actionMsg.kind === "ok" ? "var(--approved)" : "var(--rejected)",
              background: actionMsg.kind === "ok" ? "oklch(0.74 0.13 165 / 0.06)" : "oklch(0.66 0.18 25 / 0.06)",
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <span>{actionMsg.msg}</span>
              <button className="btn xs ghost" style={{ marginLeft: "auto" }} onClick={() => setActionMsg(null)}>✕</button>
            </div>
          )}
          {activeTab === "autopilot" ? (
            <AutopilotWorkspace
              tenant={tenant}
              detailQ={autopilotDetailQ}
              detail={autopilotDetail}
              selectedKey={autopilotSelectedKey}
              reviewReason={autopilotReviewReason}
              setReviewReason={setAutopilotReviewReason}
              reviewTargetKey={autopilotReviewTargetKey}
              reviewMissingKey={autopilotReviewMissingKey}
              setReviewTargetKey={setAutopilotReviewTargetKey}
              setReviewMissingKey={setAutopilotReviewMissingKey}
              onReviewCandidate={reviewAutopilotCandidate}
              onStart={startAutopilot}
              starting={autopilotStarting}
              onRunPlaybook={(tenant && tenant.id) === "maritime-risk" ? runMaritimeRiskPlaybook : runCreditcardfraudPlaybook}
              playbookRunning={autopilotPlaybookRunning}
            />
          ) : askMode ? (
            <AskHero
              tenant={tenant}
              question={question} setQuestion={setQuestion}
              centerNode={centerNode} setCenterNode={setCenterNode}
              depth={depth} setDepth={setDepth}
              limit={limit} setLimit={setLimit}
              isMock={isMock}
              submitting={submitting}
              actionMsg={actionMsg}
              onDismissMsg={() => setActionMsg(null)}
              onCancel={() => setAskMode(false)}
              onSubmit={submitQuestion}
            />
          ) : !task ? (
            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 14, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              <div style={{ fontSize: 13, color: "var(--text-dim)" }}>
                {activeTab === "mine"    ? "You haven't asked any questions yet." :
                 activeTab === "graph"   ? "No graph-derived reasoning tasks in this scope." :
                                           "Select a reasoning task from the left, or ask a new question."}
              </div>
              <button className="btn primary" onClick={() => setAskMode(true)}>+ Ask a new question</button>
            </div>
          ) : (
            <>
              <div className="art-header">
                <div className="crumb">
                  <span className="type">Reasoning Task</span>
                  <span className="sep">/</span>
                  <span>{task.canonical_key}</span>
                  {task.center_node && <><span className="sep">·</span><span>scope {task.center_node} · d{task.depth || 1} · n{task.limit || 200}</span></>}
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                    {isLoadingDetail && (
                      <span className="pill" style={{ fontSize: 9 }}>
                        <span className="dot" style={{ background: "var(--accent)" }} />Loading detail…
                      </span>
                    )}
                    {showRunning && (
                      <span className="pill changes" style={{ fontSize: 9 }}>
                        <span className="dot" style={{ animation: "pulse 1s ease-in-out infinite" }} />
                        {isTaskRunning ? "Polling · " + pollTick : "Running…"}
                      </span>
                    )}
                  </span>
                </div>
                <h1>{task.name || task.question || "Untitled reasoning task"}</h1>
                {task.blocker && (
                  <p className="desc" style={{ color: "var(--rejected)" }}>⚠ {task.blocker}</p>
                )}
                <div className="row">
                  <div className="stat">
                    <span className="label">Center</span>
                    <span className="val mono">{task.center_node || "—"}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Depth / limit</span>
                    <span className="val mono">{task.depth || 1} · {task.limit || 200}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Source</span>
                    <span className="val mono">{task.source || "manual"}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Evidence</span>
                    <span className="val mono">{evidence.length} items</span>
                  </div>
                  <div className="stat">
                    <span className="label">Canonical write</span>
                    <span className="val" style={{ color: "var(--changes)" }}>blocked · draft only</span>
                  </div>
                  {task.id != null && (
                    <div className="stat">
                      <span className="label">Run ID</span>
                      <span className="val mono">{task.id}</span>
                    </div>
                  )}
                  <div className="stat">
                    <span className="label">Created</span>
                    <span className="val mono">{fmtTime(task.created_at)}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Completed</span>
                    <span className="val mono">{isTerminal ? fmtTime(task.updated_at) : "—"}</span>
                  </div>
                </div>
              </div>

              <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
                {/* Conclusion */}
                <Panel eyebrow="Current answer" title="Conclusion"
                       count={showRunning ? (isTaskRunning ? `polling · ${pollTick}` : "running…") : isStaleActive ? "stale" : finding ? (finding.status || "draft") : "no answer"}
                       actions={shouldRerun ? (
                         <button className="btn xs" onClick={runTask} disabled={running}
                                 title="Create a new task with same question/scope">
                           {running ? "Rerunning…" : "↻ Rerun (new task)"}
                         </button>
                       ) : null}
                       style={{ marginBottom: 16 }}>
                  {isStaleActive ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "10px 0" }}>
                      <div style={{
                        padding: "12px 14px",
                        border: "1px solid oklch(0.66 0.18 25 / 0.4)",
                        background: "oklch(0.66 0.18 25 / 0.06)",
                        color: "var(--rejected)",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        letterSpacing: "0.04em",
                        lineHeight: 1.6,
                      }}>
                        <div style={{ textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10, marginBottom: 6, color: "var(--rejected)" }}>
                          ⚠ Likely orphaned
                        </div>
                        <div style={{ color: "var(--text-dim)", textTransform: "none", letterSpacing: 0 }}>
                          Status is <span style={{ color: "var(--rejected)" }}>{task.status}</span> but the task hasn't been updated for <span style={{ color: "var(--text)" }}>{selectedState.ageLbl}</span>. The worker probably crashed or the service was restarted before it could mark this task complete. The backend status is not being actively maintained.
                        </div>
                      </div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", lineHeight: 1.7 }}>
                        <div style={{ color: "var(--accent)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>What to do</div>
                        <div>·  Click <span style={{ color: "var(--text)" }}>↻ Rerun reasoning</span> below to start a fresh run.</div>
                        <div>·  Or check the backend worker log for the original failure.</div>
                        <div>·  Status will only change if you rerun, or someone manually clears it on the server.</div>
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <button className="btn primary" onClick={runTask} disabled={running}>↻ Rerun reasoning</button>
                        <button className="btn ghost" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>Refresh once</button>
                      </div>
                    </div>
                  ) : showRunning ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "16px 0" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--accent)", fontFamily: "var(--font-mono)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                        <span style={{ width: 8, height: 8, background: "var(--accent)", display: "inline-block", animation: "pulse 1s ease-in-out infinite" }} />
                        Reasoning in progress
                        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 10 }}>
                          {liveTrace.length} event{liveTrace.length === 1 ? "" : "s"} · {liveTrace.length > 0 ? "SSE" : "starting…"}
                        </span>
                      </div>
                      <div style={{ color: "var(--muted)", fontSize: 12, lineHeight: 1.55 }}>
                        Running scoped reasoning over <span style={{ color: "var(--text)" }}>{task.center_node}</span> (depth {task.depth || 1}, limit {task.limit || 200}).
                      </div>
                      <TraceLog events={liveTrace} />
                      <div style={{ display: "flex", gap: 8 }}>
                        <button className="btn ghost" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ Refresh now</button>
                        {streamRef.current && (
                          <button className="btn ghost" onClick={() => {
                            try { streamRef.current.close(); } catch {}
                            setRunning(false);
                          }}>✕ Stop stream</button>
                        )}
                      </div>
                    </div>
                  ) : finding ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      {backendRunning && (
                        <div style={{
                          display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
                          border: "1px solid var(--accent-line)",
                          background: "var(--accent-bg)",
                          fontFamily: "var(--font-mono)", fontSize: 11,
                          marginBottom: 4,
                        }}>
                          <span style={{ width: 6, height: 6, background: "var(--accent)", display: "inline-block", animation: "pulse 1s ease-in-out infinite" }} />
                          <span style={{ color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10 }}>Running on backend</span>
                          <span style={{ color: "var(--muted)" }}>· polling · {pollTick}</span>
                          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                            <button className="btn xs" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ Refresh</button>
                          </span>
                        </div>
                      )}
                      <div style={{ fontSize: 15, color: "var(--text)", lineHeight: 1.55 }}>
                        {finding.conclusion}
                      </div>
                      <FraudFindingSummary finding={finding} />
                      {finding.action_proposal && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
                          <div className="eyebrow" style={{ marginBottom: 6 }}>Proposed action</div>
                          <div style={{ fontSize: 13, color: "var(--text-dim)" }}>{finding.action_proposal}</div>
                        </div>
                      )}
                      {finding.counter_evidence && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
                          <div className="eyebrow" style={{ marginBottom: 6, color: "var(--rejected)" }}>Counter evidence / limits</div>
                          <div style={{ fontSize: 13, color: "var(--text-dim)" }}>{finding.counter_evidence}</div>
                        </div>
                      )}
                      <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", gap: 8, alignItems: "center" }}>
                        <div className="eyebrow" style={{ color: "var(--changes)" }}>Canonical boundary</div>
                        <div style={{ fontSize: 11, color: "var(--muted)" }}>
                          Approving this finding cites it in the approved-finding layer; it does NOT modify canonical ontology or graph.
                        </div>
                      </div>
                      {shouldRerun && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", gap: 8, alignItems: "center" }}>
                          <button className="btn primary" onClick={runTask} disabled={running}
                                  title="Create a new task with the same question and scope, and run it.">
                            {running ? "Rerunning…" : "↻ Rerun (new task)"}
                          </button>
                          {isClosed && <span style={{ fontSize: 11, color: "var(--muted)" }}>Task is closed — rerun creates a fresh task.</span>}
                        </div>
                      )}
                      {liveTrace.length > 0 && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
                          <TraceLog events={liveTrace} />
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: "12px 0" }}>
                      {backendRunning && (
                        <div style={{
                          display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
                          border: "1px solid var(--accent-line)",
                          background: "var(--accent-bg)",
                          fontFamily: "var(--font-mono)", fontSize: 11,
                        }}>
                          <span style={{ width: 6, height: 6, background: "var(--accent)", display: "inline-block", animation: "pulse 1s ease-in-out infinite" }} />
                          <span style={{ color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10 }}>Running on backend</span>
                          <span style={{ color: "var(--muted)" }}>· waiting for result · polling · {pollTick}</span>
                          <button className="btn xs" style={{ marginLeft: "auto" }} onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ Refresh</button>
                        </div>
                      )}
                      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ color: "var(--dim)", fontFamily: "var(--font-mono)", fontSize: 12 }}>{backendRunning ? "No conclusion yet — task is still running." : "No conclusion yet."}</span>
                      {shouldRerun && (
                        <button className="btn primary" onClick={runTask} disabled={running}
                                title="Create a new task with the same question and scope, and run it.">
                          {running ? "Rerunning…" : "↻ Rerun (new task)"}
                        </button>
                      )}
                      {!shouldRerun && !runDone && !backendRunning && !running && (
                        <button className="btn primary" onClick={runTask}>
                          ▶ Run reasoning
                        </button>
                      )}
                      </div>
                      {liveTrace.length > 0 && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
                          <TraceLog events={liveTrace} />
                        </div>
                      )}
                    </div>
                  )}
                </Panel>

                {/* Evidence chain */}
                <Panel eyebrow="Provenance" title="Evidence chain" count={`${evidence.length} items`} nopad
                       actions={
                         <div className="chip-row">
                           {["all", "fact", "hypothesis", "conflict", "missing"].map(k => (
                             <Chip key={k} active={evidenceFilter === k} onClick={() => setEvidenceFilter(k)}
                                   count={k === "all" ? evidence.length : evidence.filter(e => (e.kind || "fact") === k).length}>
                               {k.charAt(0).toUpperCase() + k.slice(1)}
                             </Chip>
                           ))}
                         </div>
                       }>
                  {evidence.length === 0 ? (
                    <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                      No evidence yet. Run the reasoning to populate.
                    </div>
                  ) : (
                    <div className="evidence-list">
                      {evidence.filter(e => evidenceFilter === "all" || (e.kind || "fact") === evidenceFilter).map((e, i) => {
                        const raw = e._raw || e;
                        const ev = e._raw ? e : {
                          kind: e.kind || "fact",
                          title: e.title || e.summary || e.description || "—",
                          src: e.src || e.source_ref || e.source || "",
                          conf: e.conf != null ? e.conf : (typeof e.confidence === "number" ? e.confidence : null),
                        };
                        const ontologyKey = ontologyBasisKey(raw, ev);
                        return (
                          <div key={i} className={"evidence-item " + ev.kind}>
                            <div className="v-bar" />
                            <div className="kind">{ontologyKey ? "ontology basis" : ev.kind}</div>
                            <div className="body-x">
                              <div className="title">{ev.title}</div>
                              <div className="src">
                                {ontologyKey
                                  ? `${ontologyKey} · compact basis only`
                                  : ev.src}
                              </div>
                            </div>
                            <div className="conf-side">
                              {ontologyKey ? (
                                <a className="btn xs" href={`/?screen=ontology&tenant=${encodeURIComponent(tenant ? tenant.id : "default")}&artifact=${encodeURIComponent(ontologyKey)}`}
                                 title="Open full ontology governance details in Ontology.">
                                  View in Ontology
                                </a>
                              ) : ev.conf != null ? <><span style={{ color: "var(--text)" }}>{Math.round(ev.conf * 100)}%</span><span style={{ color: "var(--dim)", fontSize: 9, marginTop: 2 }}>confidence</span></> : <span className="faint">—</span>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Panel>
              </div>

              <div className="action-bar" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
                {actionMsg && (
                  <div style={{
                    padding: "8px 12px",
                    fontFamily: "var(--font-mono)", fontSize: 11,
                    border: "1px solid " + (actionMsg.kind === "ok" ? "oklch(0.74 0.13 165 / 0.4)" : "oklch(0.78 0.14 75 / 0.4)"),
                    color: actionMsg.kind === "ok" ? "var(--approved)" : "var(--changes)",
                    background: actionMsg.kind === "ok" ? "oklch(0.74 0.13 165 / 0.06)" : "oklch(0.78 0.14 75 / 0.06)",
                  }}>{actionMsg.msg}</div>
                )}
                <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                  <input className="reason-input" value={reviewReason} onChange={e => setReviewReason(e.target.value)}
                         placeholder="Decision rationale (required for approve / reject / lifecycle changes)…" />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn approve" onClick={() => reviewFinding("approve")} disabled={!finding}>✓ Approve finding</button>
                    <button className="btn changes" onClick={() => reviewFinding("needs-evidence")} disabled={!finding}>↻ Needs evidence</button>
                    <button className="btn reject"  onClick={() => reviewFinding("reject")} disabled={!finding}>✕ Reject</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("reaffirm")} disabled={!finding}>Reaffirm</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("mark-stale")} disabled={!finding}>Mark stale</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("supersede")} disabled={!finding}>Supersede</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("comment")} disabled={!finding}>Comment</button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* ============ RIGHT — ask + follow-up ============ */}
        <div className="col inspector">
          {activeTab === "autopilot" ? (
            <AutopilotStartPanel
              tenant={tenant}
              objective={autopilotObjective}
              setObjective={setAutopilotObjective}
              maxHypotheses={autopilotMaxHypotheses}
              setMaxHypotheses={setAutopilotMaxHypotheses}
              maxRuns={autopilotMaxRuns}
              setMaxRuns={setAutopilotMaxRuns}
              maxToolCalls={autopilotMaxToolCalls}
              setMaxToolCalls={setAutopilotMaxToolCalls}
              starting={autopilotStarting}
              onStart={startAutopilot}
              onRunPlaybook={(tenant && tenant.id) === "maritime-risk" ? runMaritimeRiskPlaybook : runCreditcardfraudPlaybook}
              playbookRunning={autopilotPlaybookRunning}
              detail={autopilotDetail}
            />
          ) : (
          <React.Fragment>
          <div className="section">
            <div className="section-head"><span>Ask with scope</span></div>
            <div className="section-body">
              <form onSubmit={submitQuestion} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div>
                  <div className="eyebrow" style={{ marginBottom: 4 }}>Question</div>
                  <textarea className="textarea" rows={3} value={question} onChange={onQuestionChangeWithExtract}
                            placeholder={(tenant && tenant.id) === "creditcardfraud" ? "Which transaction has elevated fraud risk?" : "Why is Employee #4 workload unusual?"} />
                </div>
                <EntityPicker tenant={tenant} centerNode={centerNode} setCenterNode={setCenterNode} question={question} setQuestion={setQuestion} compact />
                <div style={{ display: "flex", gap: 6 }}>
                  <div style={{ flex: 1 }}>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>Depth</div>
                    <input className="input" type="number" min={1} max={3} value={depth} onChange={e => setDepth(+e.target.value)} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>Limit</div>
                    <input className="input" type="number" value={limit} onChange={e => setLimit(+e.target.value)} />
                  </div>
                </div>
                <button className="btn primary" type="submit" disabled={submitting}>{submitting ? "Creating…" : "↗ Create scoped question"}</button>
              </form>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Follow-up in scope</span></div>
            <div className="section-body">
              <textarea className="textarea" rows={3} value={followup} onChange={e => setFollowup(e.target.value)}
                        placeholder="What evidence would change this conclusion?" style={{ marginBottom: 8 }} />
              <button className="btn" style={{ width: "100%" }} onClick={() => {
                if (!followup.trim()) return;
                const q = followup;
                setQuestion(q);
                setFollowup("");
                submitQuestion({ preventDefault: () => {} }, q);
              }} disabled={!followup.trim() || !task}>Create follow-up</button>
            </div>
          </div>

          <OntologyBasisPanel task={task} tenant={tenant} />

          <ApprovedFindingRegistry
            findings={approvedFindingsRegistry}
            query={approvedFindingsQ}
            tenant={tenant}
            filters={registryFilters}
            setFilters={setRegistryFilters}
            setActionMsg={setActionMsg}
            highlightedFindingKey={highlightedFindingKey}
          />

          <div className="section">
            <div className="section-head"><span>Write boundary</span></div>
            <div className="section-body">
              <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.55 }}>
                Reasoning agents can only write <span style={{ color: "var(--changes)" }}>draft</span> findings and action proposals. Structural facts (links, properties, classifications) require a separate canonical write proposal and a stronger approval gate.
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Quick actions</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>↗ Open graph context</button>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>≡ Compare with prior run</button>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>⤓ Export evidence pack</button>
            </div>
          </div>
          </React.Fragment>
          )}
        </div>
      </div>

      <CleanupModal open={cleanupModal} onClose={() => setCleanupModal(false)}
                    allTasks={allTasks} taskState={taskState} tenant={tenant}
                    onDone={() => { window.dispatchEvent(new CustomEvent("aletheia:retry")); setSelectedKey(null); }} />
    </div>
  );
}

function AutopilotWorkspace({
  tenant,
  detailQ,
  detail,
  selectedKey,
  reviewReason,
  setReviewReason,
  reviewTargetKey,
  reviewMissingKey,
  setReviewTargetKey,
  setReviewMissingKey,
  onReviewCandidate,
  onStart,
  starting,
  onRunPlaybook,
  playbookRunning,
}) {
  const session = detail && detail.session;
  const hypotheses = (detail && detail.hypotheses) || [];
  const candidates = (detail && detail.candidate_findings) || [];
  const trace = buildAutopilotTrace(session, hypotheses, candidates);
  const safety = session && session.safety_profile ? session.safety_profile : {};
  return (
    <React.Fragment>
      <div className="art-header">
        <div className="crumb">
          <span className="type">Reasoning Autopilot</span>
          <span className="sep">/</span>
          <span>{session ? session.session_key : selectedKey || "new session"}</span>
          {detailQ.loading && <span className="pill" style={{ marginLeft: "auto" }}><span className="dot" />Loading detail…</span>}
        </div>
        <h1>{session ? session.objective : "Autopilot Discovery"}</h1>
        <p className="desc">
          Autopilot queues hypotheses and ranks draft candidate findings. It cannot write canonical ontology, approve findings, or expose sensitive raw fields.
        </p>
        <div className="row">
          <div className="stat">
            <span className="label">Hypotheses</span>
            <span className="val mono">{hypotheses.length}</span>
          </div>
          <div className="stat">
            <span className="label">Candidate findings</span>
            <span className="val mono">{candidates.length}</span>
          </div>
          <div className="stat">
            <span className="label">Write scope</span>
            <span className="val" style={{ color: "var(--changes)" }}>{safety.write_scope || "draft_only"}</span>
          </div>
          <div className="stat">
            <span className="label">Canonical writes</span>
            <span className="val" style={{ color: "var(--rejected)" }}>{safety.canonical_writes || "disabled"}</span>
          </div>
          <div className="stat">
            <span className="label">Sensitive fields</span>
            <span className="val" style={{ color: safety.allow_sensitive_fields ? "var(--rejected)" : "var(--approved)" }}>
              {safety.allow_sensitive_fields ? "allowed" : "blocked"}
            </span>
          </div>
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
        {!session ? (
          <Panel eyebrow="Start" title="No Autopilot session selected" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, color: "var(--muted)", fontSize: 12 }}>
              <span>Start a session to create a visible hypothesis queue and draft Finding Inbox.</span>
              <button className="btn primary" onClick={onStart} disabled={starting}>{starting ? "Starting…" : "▶ Start Autopilot"}</button>
              {(tenant?.id === "creditcardfraud" || tenant?.id === "maritime-risk") && (
                <button className="btn" onClick={onRunPlaybook} disabled={playbookRunning}>
                  {playbookRunning ? "Running playbook…" : tenant?.id === "maritime-risk" ? "Run maritime-risk playbook" : "Run fraud playbook"}
                </button>
              )}
            </div>
          </Panel>
        ) : (
          <React.Fragment>
            <Panel eyebrow="Run trace" title="Autopilot execution trace" count={`${trace.length} events`} style={{ marginBottom: 16 }}>
              {trace.length === 0 ? (
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  No trace events yet. The playbook will append hypotheses and candidate findings through the Autopilot API.
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {trace.map((event, idx) => (
                    <div key={idx} style={{ display: "grid", gridTemplateColumns: "120px 1fr auto", gap: 10, padding: "9px 10px", border: "1px solid var(--line)", background: "var(--bg-1)", alignItems: "center" }}>
                      <span className="eyebrow" style={{ color: event.tone || "var(--accent)" }}>{event.kind}</span>
                      <span style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.45 }}>{event.title}</span>
                      <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>{event.status}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            <Panel eyebrow="Hypothesis queue" title="Queued reasoning hypotheses" count={`${hypotheses.length} items`} style={{ marginBottom: 16 }}>
              {hypotheses.length === 0 ? (
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  No hypotheses yet. Run a tenant playbook to populate this queue with draft candidate findings.
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {hypotheses.map(h => (
                    <div key={h.hypothesis_key} style={{ border: "1px solid var(--line)", background: "var(--bg-1)", padding: 12 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                        <Pill kind={h.status === "pruned" ? "rejected" : h.status === "completed" ? "approved" : "changes"}>{h.status}</Pill>
                        <strong style={{ color: "var(--text)", fontSize: 13 }}>{h.title}</strong>
                        <span className="mono" style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 10 }}>p{h.priority}</span>
                      </div>
                      <div style={{ color: "var(--text-dim)", fontSize: 12, lineHeight: 1.5 }}>{h.rationale || "No rationale recorded."}</div>
                      {h.status === "pruned" && (
                        <div style={{ marginTop: 8, color: "var(--rejected)", fontSize: 11 }}>Pruned reason: {h.pruned_reason || "missing"}</div>
                      )}
                      {h.evidence_plan?.length > 0 && (
                        <div className="mono" style={{ marginTop: 8, color: "var(--muted)", fontSize: 10 }}>
                          Evidence plan: {h.evidence_plan.map(p => p.metric || p.kind || p.source_ref).filter(Boolean).join(" · ")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            {(tenant?.id === "creditcardfraud" || tenant?.id === "maritime-risk") && (
              <Panel eyebrow="Playbook" title={tenant?.id === "maritime-risk" ? "Maritime-risk graph reasoning playbook" : "Creditcardfraud discovery playbook"} count="draft-only" style={{ marginBottom: 16 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>
                  <span>{tenant?.id === "maritime-risk"
                    ? "Run the fixed maritime playbook to populate chokepoint dependency, hazard-adjusted risk, and country-priority graph findings, plus a pruned non-graph ranking hypothesis."
                    : "Run the fixed fraud playbook to populate card-not-present, verification mismatch, POS missing, merchant category, duplicate-cluster candidates, plus a pruned hypothesis with reason."}</span>
                  <button className="btn primary" onClick={onRunPlaybook} disabled={playbookRunning} style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
                    {playbookRunning ? "Running…" : "Run playbook"}
                  </button>
                </div>
              </Panel>
            )}

            <Panel eyebrow="Finding Inbox" title="Draft candidate findings" count={`${candidates.length} candidates`} style={{ marginBottom: 16 }}>
              {candidates.length === 0 ? (
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  No candidate findings yet. Autopilot UI is wired; the discovery playbook will fill this inbox with draft candidates.
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {candidates.map(c => {
                    const isSelectedForReview = reviewTargetKey === c.canonical_key;
                    const isMissingReviewNote = reviewMissingKey === c.canonical_key && !reviewReason.trim();
                    const isReviewed = ["approved", "rejected", "needs_more_evidence"].includes(c.status);
                    return (
                    <div key={c.canonical_key} style={{ border: "1px solid var(--line)", background: "var(--bg-1)", padding: 14 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
                        <Pill kind={c.status === "rejected" ? "rejected" : c.status === "needs_more_evidence" ? "changes" : "proposed"}>{c.status}</Pill>
                        <strong style={{ color: "var(--text)", fontSize: 14 }}>{c.title}</strong>
                      </div>
                      <div style={{ color: "var(--text-dim)", fontSize: 13, lineHeight: 1.55 }}>{c.conclusion}</div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8, marginTop: 12 }}>
                        <MetricMini label="Value" value={pctRX(c.value_score, 0)} />
                        <MetricMini label="Confidence" value={pctRX(c.confidence, 0)} />
                        <MetricMini label="Novelty" value={pctRX(c.novelty_score, 0)} />
                        <MetricMini label="Impact" value={pctRX(c.impact_score, 0)} />
                      </div>
                      <div style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
                        <div className="eyebrow" style={{ marginBottom: 6 }}>Evidence chain</div>
                        {(c.evidence_chain || []).length === 0 ? (
                          <div style={{ color: "var(--rejected)", fontSize: 11 }}>Missing evidence chain; should not pass final validation.</div>
                        ) : (
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {(c.evidence_chain || []).map((e, i) => (
                              <div key={i} className="mono" style={{ color: "var(--muted)", fontSize: 10, lineHeight: 1.45 }}>
                                {e.kind || "evidence"} · {e.source_ref || e.source || "source"} · {e.metric || e.title || ""} {e.value ? `= ${e.value}` : ""}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      {(c.evidence_limits || []).length > 0 && (
                        <div style={{ marginTop: 10, color: "var(--muted)", fontSize: 11, lineHeight: 1.5 }}>
                          Limits: {(c.evidence_limits || []).join(" · ")}
                        </div>
                      )}
                      <div style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
                        {isMissingReviewNote && (
                          <div style={{
                            marginBottom: 8,
                            padding: "8px 10px",
                            border: "1px solid oklch(0.78 0.14 75 / 0.45)",
                            background: "oklch(0.78 0.14 75 / 0.08)",
                            color: "var(--changes)",
                            fontFamily: "var(--font-mono)",
                            fontSize: 11,
                            lineHeight: 1.45,
                          }}>
                            Review note required before rejecting or requesting more evidence.
                          </div>
                        )}
                        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                          {isReviewed ? (
                            <span style={{ color: c.status === "approved" ? "var(--approved)" : c.status === "rejected" ? "var(--rejected)" : "var(--changes)", fontSize: 11 }}>
                              Review recorded · {c.status === "approved" ? "approved finding created" : c.status}
                            </span>
                          ) : (
                            <>
                              <button className="btn approve" onClick={() => onReviewCandidate(c, "approved")}>Approve as finding</button>
                              <button className="btn changes" onClick={() => onReviewCandidate(c, "needs_more_evidence")}>Needs more evidence</button>
                              <button className="btn reject" onClick={() => onReviewCandidate(c, "rejected")}>Reject candidate</button>
                            </>
                          )}
                          <span style={{ marginLeft: "auto", color: "var(--changes)", fontSize: 11 }}>
                            Requires human approval · Autopilot suggests, people approve
                          </span>
                        </div>
                        {isSelectedForReview && !isReviewed && (
                          <div style={{ marginTop: 8 }}>
                            <textarea className="textarea" rows={2} value={reviewReason}
                                      onChange={e => { setReviewReason(e.target.value); setReviewMissingKey(""); }}
                                      placeholder="Optional note for approval; required for reject or needs-more-evidence." />
                          </div>
                        )}
                      </div>
                    </div>
                  );})}
                </div>
              )}
            </Panel>

            <Panel eyebrow="Review gate" title="Candidate review note" count="human approval required">
              <textarea className="textarea" rows={3} value={reviewReason}
                        onChange={e => { setReviewReason(e.target.value); setReviewMissingKey(""); }}
                        onFocus={() => setReviewTargetKey(reviewTargetKey || (candidates[0] && candidates[0].canonical_key) || "")}
                        placeholder="Reason required for approve / reject / needs more evidence." />
              <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 11, lineHeight: 1.5 }}>
                Candidate approval creates a reviewed Finding Registry entry. It can be reused as prior_finding context and can draft next actions/proposals, but does not write canonical ontology or graph.
              </div>
            </Panel>
          </React.Fragment>
        )}
      </div>
    </React.Fragment>
  );
}

function AutopilotStartPanel({ tenant, objective, setObjective, maxHypotheses, setMaxHypotheses, maxRuns, setMaxRuns, maxToolCalls, setMaxToolCalls, starting, onStart, onRunPlaybook, playbookRunning, detail }) {
  const safety = detail?.session?.safety_profile || {};
  const budget = detail?.session?.budget || {};
  return (
    <React.Fragment>
      <div className="section">
        <div className="section-head"><span>Start Autopilot</span></div>
        <div className="section-body">
          <form onSubmit={onStart} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Objective</div>
              <textarea className="textarea" rows={3} value={objective} onChange={e => setObjective(e.target.value)}
                        placeholder="Find high-value candidate findings…" />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 6 }}>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Hypotheses</div>
                <input className="input" type="number" min={1} max={25} value={maxHypotheses} onChange={e => setMaxHypotheses(+e.target.value)} />
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Runs</div>
                <input className="input" type="number" min={1} max={20} value={maxRuns} onChange={e => setMaxRuns(+e.target.value)} />
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Tool calls</div>
                <input className="input" type="number" min={1} max={80} value={maxToolCalls} onChange={e => setMaxToolCalls(+e.target.value)} />
              </div>
            </div>
            <button className="btn primary" type="submit" disabled={starting}>{starting ? "Starting…" : "▶ Start Autopilot"}</button>
            {(tenant?.id === "creditcardfraud" || tenant?.id === "maritime-risk") && (
              <button className="btn" type="button" onClick={onRunPlaybook} disabled={playbookRunning}>
                {playbookRunning ? "Running playbook…" : tenant?.id === "maritime-risk" ? "Run maritime-risk playbook" : "Run creditcardfraud playbook"}
              </button>
            )}
          </form>
        </div>
      </div>

      <div className="section">
        <div className="section-head"><span>Safety profile</span></div>
        <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <BoundaryLine label="Tenant" value={tenant ? tenant.id : "default"} />
          <BoundaryLine label="Safe views only" value={String(safety.safe_views_only !== false)} tone="var(--approved)" />
          <BoundaryLine label="Sensitive fields" value={safety.allow_sensitive_fields ? "allowed" : "blocked"} tone={safety.allow_sensitive_fields ? "var(--rejected)" : "var(--approved)"} />
          <BoundaryLine label="Canonical writes" value={safety.canonical_writes || "disabled"} tone="var(--rejected)" />
          <BoundaryLine label="Auto approve" value={String(!!safety.auto_approve_findings)} tone={safety.auto_approve_findings ? "var(--rejected)" : "var(--approved)"} />
          <BoundaryLine label="Blocked field group" value={(safety.blocked_fields || []).length ? (safety.blocked_fields || []).join(", ") : "none"} />
        </div>
      </div>

      <div className="section">
        <div className="section-head"><span>Budget</span></div>
        <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <BoundaryLine label="Max hypotheses" value={budget.max_hypotheses || maxHypotheses} />
          <BoundaryLine label="Max reasoning tasks" value={budget.max_reasoning_tasks || maxRuns} />
          <BoundaryLine label="Max tool calls" value={budget.max_tool_calls || maxToolCalls} />
          <BoundaryLine label="Sample strategy" value={budget.sample_strategy || "deterministic_full_table_aggregates"} />
        </div>
      </div>
    </React.Fragment>
  );
}

function BoundaryLine({ label, value, tone }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 11, borderBottom: "1px solid var(--line)", paddingBottom: 6 }}>
      <span style={{ color: "var(--muted)" }}>{label}</span>
      <span className="mono" style={{ color: tone || "var(--text)", textAlign: "right", overflowWrap: "anywhere" }}>{value}</span>
    </div>
  );
}

function MetricMini({ label, value }) {
  return (
    <div style={{ border: "1px solid var(--line)", padding: 8, background: "var(--bg-2)", minWidth: 0 }}>
      <div className="eyebrow" style={{ marginBottom: 4 }}>{label}</div>
      <div className="mono" style={{ color: "var(--text)", fontSize: 13 }}>{value}</div>
    </div>
  );
}

function buildAutopilotTrace(session, hypotheses, candidates) {
  const out = [];
  if (session) {
    out.push({ kind: "session", title: session.objective, status: session.status || "draft", tone: "var(--accent)" });
    out.push({ kind: "safety", title: `write_scope=${session.safety_profile?.write_scope || "draft_only"} · canonical_writes=${session.safety_profile?.canonical_writes || "disabled"}`, status: "enforced", tone: "var(--approved)" });
  }
  hypotheses.forEach(h => {
    out.push({
      kind: h.status === "pruned" ? "pruned" : "hypothesis",
      title: h.status === "pruned" ? `${h.title} · ${h.pruned_reason || "missing prune reason"}` : h.title,
      status: h.status,
      tone: h.status === "pruned" ? "var(--rejected)" : "var(--changes)",
    });
  });
  candidates.forEach(c => {
    out.push({
      kind: "candidate",
      title: c.title,
      status: c.status,
      tone: c.status === "draft" ? "var(--changes)" : c.status === "rejected" ? "var(--rejected)" : "var(--accent)",
    });
  });
  return out;
}

/* ---------------- CleanupModal ---------------- */
function CleanupModal({ open, onClose, allTasks, taskState, tenant, onDone }) {
  if (!open) return null;
  const CATEGORIES = [
    { key: "active",    label: "Active",    color: "var(--changes)",  match: t => { const s = (t.status||"").toLowerCase(); return RUNNING_STATES.has(s) && !taskState(t).isStale; } },
    { key: "stale",     label: "Stale",     color: "var(--rejected)", match: t => taskState(t).isStale },
    { key: "completed", label: "Completed", color: "var(--approved)", match: t => { const s = (t.status||"").toLowerCase(); return s === "completed" || s === "approved"; } },
    { key: "closed",    label: "Closed",    color: "var(--muted)",    match: t => (t.status||"").toLowerCase() === "closed" },
  ];
  const [checked, setChecked] = React.useState(new Set());
  const [progress, setProgress] = React.useState(null);

  const counts = {};
  const buckets = {};
  for (const cat of CATEGORIES) {
    const list = allTasks.filter(cat.match);
    counts[cat.key] = list.length;
    buckets[cat.key] = list;
  }

  const filtered = React.useMemo(() => {
    if (!checked.size) return [];
    const set = new Set();
    for (const k of checked) {
      for (const t of (buckets[k] || [])) set.add(t);
    }
    return [...set];
  }, [checked, allTasks]);

  function toggle(key) {
    setChecked(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function runCleanup() {
    if (!filtered.length) return;
    const total = filtered.length;
    setProgress({ done: 0, total, ok: 0, fail: 0, running: true });
    let done = 0, ok = 0, fail = 0;
    for (const t of filtered) {
      try {
        try { await window.AL_API.closeTask(t.canonical_key, tenant.id); } catch (_) {}
        await window.AL_API.deleteTask(t.canonical_key, tenant.id);
        ok++;
      } catch (_) { fail++; }
      done++;
      setProgress({ done, total, ok, fail, running: done < total });
    }
    setProgress({ done, total, ok, fail, running: false });
    if (onDone) onDone();
  }

  const summary = progress;

  return (
    <div style={{
      position: "fixed", inset: 0,
      background: "rgba(7, 9, 12, 0.7)",
      backdropFilter: "blur(2px)",
      zIndex: 999,
      display: "grid", placeItems: "center",
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        width: 640,
        maxHeight: "82vh",
        display: "flex", flexDirection: "column",
        background: "var(--bg-2)",
        border: "1px solid var(--line-strong)",
        boxShadow: "0 30px 80px rgba(0,0,0,0.55)",
      }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line)", background: "var(--bg-3)", display: "flex", alignItems: "center" }}>
          <div className="eyebrow" style={{ color: "var(--rejected)" }}>Cleanup</div>
          <div style={{ marginLeft: 10, fontSize: 16, color: "var(--text)" }}>Task cleanup</div>
          <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", color: "var(--muted)", border: "1px solid var(--line)", padding: "3px 8px", fontFamily: "var(--font-mono)", fontSize: 10, cursor: "pointer" }}>ESC</button>
        </div>

        <div style={{ padding: 20, overflow: "auto", flex: 1 }}>
          <p style={{ color: "var(--muted)", fontSize: 13, lineHeight: 1.55, margin: "0 0 16px 0" }}>
            Select which task statuses to delete. Tasks will be closed then permanently deleted.
          </p>

          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
            {CATEGORIES.map(cat => (
              <label key={cat.key} style={{
                display: "flex", alignItems: "center", gap: 6, cursor: "pointer",
                padding: "6px 12px",
                border: "1px solid " + (checked.has(cat.key) ? cat.color : "var(--line)"),
                background: checked.has(cat.key) ? "var(--bg-3)" : "transparent",
                fontSize: 12,
              }}>
                <input type="checkbox" checked={checked.has(cat.key)} onChange={() => toggle(cat.key)}
                       style={{ accentColor: cat.color }} />
                <span style={{ color: cat.color }}>{cat.label}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>({counts[cat.key]})</span>
              </label>
            ))}
          </div>

          <div style={{ border: "1px solid var(--line)", marginBottom: 16, maxHeight: 300, overflowY: "auto" }}>
            {filtered.length === 0 ? (
              <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                {checked.size ? "No tasks match the selected filters." : "Select a status to see matching tasks."}
              </div>
            ) : filtered.map(t => {
              const ts = taskState(t);
              const statusLabel = ts.isStale ? "stale" : (t.status || "—").toLowerCase();
              const cat = CATEGORIES.find(c => c.match(t));
              return (
                <div key={t.canonical_key} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 14px",
                  borderBottom: "1px solid var(--line-soft)",
                }}>
                  <div style={{ width: 3, alignSelf: "stretch", background: cat ? cat.color : "var(--line)" }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ color: "var(--text)", fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {t.question || t.name || t.canonical_key}
                    </div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                      {t.center_node || "—"}
                    </div>
                  </div>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: cat ? cat.color : "var(--muted)", flexShrink: 0 }}>
                    {statusLabel}
                  </span>
                </div>
              );
            })}
          </div>

          {summary && (
            <div style={{
              padding: "10px 14px",
              border: "1px solid var(--line)",
              background: "var(--bg-1)",
              fontFamily: "var(--font-mono)", fontSize: 11,
              marginBottom: 16,
            }}>
              <div style={{ display: "flex", gap: 14 }}>
                <span><span style={{ color: "var(--dim)" }}>PROGRESS</span> <span style={{ color: "var(--text)" }}>{summary.done}/{summary.total}</span></span>
                <span><span style={{ color: "var(--dim)" }}>OK</span> <span style={{ color: "var(--approved)" }}>{summary.ok}</span></span>
                <span><span style={{ color: "var(--dim)" }}>FAILED</span> <span style={{ color: "var(--rejected)" }}>{summary.fail}</span></span>
                <span style={{ marginLeft: "auto", color: summary.running ? "var(--changes)" : "var(--approved)" }}>
                  {summary.running ? "● running" : "● complete"}
                </span>
              </div>
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn primary"
                    style={{ color: filtered.length ? undefined : "var(--muted)" }}
                    onClick={runCleanup}
                    disabled={!filtered.length || (progress && progress.running)}>
              {progress && !progress.running
                ? `Done — deleted ${progress.ok} task(s)`
                : `Delete ${filtered.length} task${filtered.length === 1 ? "" : "s"}`}
            </button>
            <button className="btn ghost" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Reasoning, CleanupModal });

function ontologyBasisKey(raw, normalized) {
  const src = (normalized && normalized.src) || raw.source_ref || raw.source || "";
  const payload = raw.payload || {};
  const direct = raw.ontology_artifact || raw.ontology_link || payload.ontology_artifact || payload.ontology_link;
  if (direct) return direct;
  if ((raw.kind || raw.evidence_type) === "ontology_artifact" && src.startsWith("artifact:")) {
    return src.slice("artifact:".length);
  }
  if (src.startsWith("artifact:")) return src.slice("artifact:".length);
  return null;
}

function ontologyBasisLabel(key) {
  const labels = {
    "link:employee:1:n:order": "Employee 1:N Order",
    "object:employee": "Employee",
    "object:order": "Order",
  };
  return labels[key] || key;
}

function OntologyBasisPanel({ task, tenant }) {
  if (!task) return null;
  const scope = task.scope || {};
  const keys = new Set();
  (scope.allowed_link_keys || []).forEach(k => keys.add(k));
  (scope.allowed_node_types || []).forEach(t => keys.add("object:" + String(t).toLowerCase()));
  ((task.evidence_paths || [])).forEach(e => {
    const key = ontologyBasisKey(e, { src: e.source_ref || e.source || "" });
    if (key) keys.add(key);
  });
  const list = [...keys].filter(Boolean);
  if (!list.length) return null;
  const tenantId = tenant ? tenant.id : "default";
  return (
    <div className="section">
      <div className="section-head"><span>Ontology basis</span><span className="ct">{list.length}</span></div>
      <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {list.map(key => (
          <a key={key}
             className="btn ghost"
             href={`/?screen=ontology&tenant=${encodeURIComponent(tenantId)}&artifact=${encodeURIComponent(key)}`}
             style={{ justifyContent: "space-between", gap: 10 }}
             title="Open full ontology governance details in Ontology.">
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{ontologyBasisLabel(key)}</span>
            <span style={{ color: "var(--accent)", flexShrink: 0 }}>View in Ontology</span>
          </a>
        ))}
        <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.55 }}>
          Compact basis only. Detailed source mapping, approval audit, canonical state, and graph eligibility live in Ontology.
        </div>
      </div>
    </div>
  );
}

function ApprovedFindingRegistry({ findings, query, tenant, filters, setFilters, setActionMsg, highlightedFindingKey }) {
  const list = findings || [];
  const tenantId = tenant ? tenant.id : "default";
  const [selected, setSelected] = useStateRX({});
  const [owner, setOwner] = useStateRX("@Itachi");
  const [dueAt, setDueAt] = useStateRX("");
  const [result, setResult] = useStateRX("confirmed_risk");
  const selectedKeys = Object.keys(selected).filter(k => selected[k]);
  const updateFilter = (key, value) => setFilters({ ...(filters || {}), [key]: value });
  async function createAction(finding) {
    try {
      await window.AL_API.createFindingAction(finding.canonical_key, {
        title: "Follow up approved finding",
        action_type: "investigate",
        owner,
        due_at: dueAt || null,
        priority: "medium",
        reviewer: "M. Aoki",
      }, tenantId);
      setActionMsg && setActionMsg({ kind: "ok", msg: "Workspace action created." });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg && setActionMsg({ kind: "err", msg: err.message || String(err) });
    }
  }
  async function transitionAction(actionKey, action, extra) {
    try {
      await window.AL_API.updateFindingAction(actionKey, action, {
        ...(extra || {}),
        result: action === "close" ? result : undefined,
        reviewer: "M. Aoki",
        reason: `Registry action ${action}`,
      }, tenantId);
      setActionMsg && setActionMsg({ kind: "ok", msg: `Action ${action} recorded.` });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg && setActionMsg({ kind: "err", msg: err.message || String(err) });
    }
  }
  async function batch(action) {
    if (!selectedKeys.length) {
      setActionMsg && setActionMsg({ kind: "err", msg: "Select findings for batch revalidation." });
      return;
    }
    try {
      await window.AL_API.batchRevalidateFindings(tenantId, {
        finding_keys: selectedKeys,
        action,
        owner,
        due_at: dueAt || null,
        reviewer: "M. Aoki",
        reason: `Batch ${action} from Approved Finding Registry`,
      });
      setSelected({});
      setActionMsg && setActionMsg({ kind: "ok", msg: `Batch ${action} recorded for ${selectedKeys.length} findings.` });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg && setActionMsg({ kind: "err", msg: err.message || String(err) });
    }
  }
  return (
    <div className="section">
      <div className="section-head"><span>Approved Finding Registry</span><span className="ct">{list.length}</span></div>
      <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <ApiStatus q={query} what="approved findings" />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 6 }}>
          <select className="input" value={filters.status || ""} onChange={e => updateFilter("status", e.target.value)}>
            <option value="">Any status</option>
            <option value="approved">Approved</option>
            <option value="stale">Stale</option>
            <option value="superseded">Superseded</option>
            <option value="rejected">Rejected</option>
            <option value="needs_more_evidence">Needs evidence</option>
          </select>
          <select className="input" value={filters.context || ""} onChange={e => updateFilter("context", e.target.value)}>
            <option value="">Audit/history</option>
            <option value="active">Active context</option>
          </select>
          <select className="input" value={filters.finding_type || ""} onChange={e => updateFilter("finding_type", e.target.value)}>
            <option value="">Any type</option>
            <option value="risk_pattern">Risk pattern</option>
            <option value="operational_anomaly">Operational anomaly</option>
            <option value="quality_issue">Quality issue</option>
            <option value="ontology_conflict">Ontology conflict</option>
            <option value="investigation_prompt">Investigation prompt</option>
          </select>
          <select className="input" value={filters.action_state || ""} onChange={e => updateFilter("action_state", e.target.value)}>
            <option value="">Any action</option>
            <option value="no_action">No action</option>
            <option value="open_action">Open action</option>
            <option value="overdue_action">Overdue action</option>
            <option value="closed_action">Closed action</option>
          </select>
          <select className="input" value={filters.freshness || ""} onChange={e => updateFilter("freshness", e.target.value)}>
            <option value="">Any freshness</option>
            <option value="reaffirmed_recently">Reaffirmed</option>
            <option value="due_for_revalidation">Due for review</option>
            <option value="stale">Stale</option>
            <option value="superseded">Superseded</option>
          </select>
          <select className="input" value={filters.sort || ""} onChange={e => updateFilter("sort", e.target.value)}>
            <option value="newest_reviewed">Newest reviewed</option>
            <option value="value_desc">Value score</option>
            <option value="oldest_unrevalidated">Oldest unrevalidated</option>
            <option value="action_due_asc">Action due date</option>
            <option value="confidence_desc">Confidence</option>
          </select>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          <input className="input" value={owner} onChange={e => setOwner(e.target.value)} placeholder="@owner" />
          <input className="input" type="datetime-local" value={dueAt} onChange={e => setDueAt(e.target.value)} />
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button className="btn xs approve" onClick={() => batch("reaffirm")} disabled={!selectedKeys.length}>Reaffirm selected</button>
          <button className="btn xs changes" onClick={() => batch("mark_stale")} disabled={!selectedKeys.length}>Mark stale</button>
          <button className="btn xs" onClick={() => batch("assign_owner")} disabled={!selectedKeys.length}>Assign owner</button>
        </div>
        {list.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.55 }}>
            No active approved findings yet. Approved findings will enter future reasoning as prior_finding / reviewed_inference context.
          </div>
        ) : list.slice(0, 8).map(f => {
          const highlighted = highlightedFindingKey && f.canonical_key === highlightedFindingKey;
          return (
          <div key={f.canonical_key} style={{
            border: "1px solid " + (highlighted ? "var(--approved)" : "var(--line)"),
            background: highlighted ? "oklch(0.74 0.13 165 / 0.08)" : "var(--bg-1)",
            padding: 10,
            boxShadow: highlighted ? "0 0 0 1px oklch(0.74 0.13 165 / 0.18) inset" : "none",
          }}>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <input type="checkbox" checked={!!selected[f.canonical_key]} onChange={e => setSelected({ ...selected, [f.canonical_key]: e.target.checked })} />
              <a href={`/?screen=reasoning&tenant=${encodeURIComponent(tenantId)}&task=${encodeURIComponent(f.task_key || (f.task && f.task.canonical_key) || "")}`}
                 style={{ color: "var(--text)", textDecoration: "none", flex: 1 }}>
                <span style={{ fontSize: 10, color: f.reasoning_use ? "var(--approved)" : "var(--muted)", fontFamily: "var(--font-mono)" }}>
                  {highlighted ? "newly added · " : ""}{f.reasoning_use ? "active prior insight · reviewed_inference" : "audit only"} · {f.source_label || "Reasoning"} · {f.finding_type || "finding"}
                </span>
                <strong style={{ display: "block", fontSize: 12, marginTop: 3 }}>{f.title}</strong>
                <span style={{ display: "block", fontSize: 11, color: "var(--muted)", lineHeight: 1.4, marginTop: 3 }}>
                  {(f.conclusion || "").slice(0, 140)}
                </span>
              </a>
              <Pill kind={f.status === "approved" ? "approved" : f.status === "stale" ? "changes" : f.status === "rejected" ? "rejected" : "proposed"}>{f.status}</Pill>
            </div>
            <div className="mono" style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8, color: "var(--muted)", fontSize: 10 }}>
              <span>conf {pctRX(f.confidence, 0)}</span>
              <span>value {pctRX(f.value_score, 0)}</span>
              <span>evidence {f.evidence_count || 0}</span>
              <span>freshness {f.freshness || "-"}</span>
              <span>action {f.action_summary?.state || "no_action"}</span>
            </div>
            {f.action_summary?.primary ? (
              <div style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 8, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  {f.action_summary.primary.owner || "unowned"} · due {f.action_summary.primary.due_at || "-"} · {f.action_summary.primary.status}
                </span>
                <select className="input" value={result} onChange={e => setResult(e.target.value)} style={{ width: 150 }}>
                  <option value="confirmed_risk">confirmed risk</option>
                  <option value="false_positive">false positive</option>
                  <option value="evidence_added">evidence added</option>
                  <option value="proposal_created">proposal created</option>
                  <option value="no_action_needed">no action needed</option>
                  <option value="rerun_scheduled">rerun scheduled</option>
                </select>
                <button className="btn xs" onClick={() => transitionAction(f.action_summary.primary.action_key, "start")}>Start</button>
                <button className="btn xs changes" onClick={() => transitionAction(f.action_summary.primary.action_key, "block")}>Block</button>
                <button className="btn xs approve" onClick={() => transitionAction(f.action_summary.primary.action_key, "close")}>Close</button>
                <button className="btn xs" onClick={() => transitionAction(f.action_summary.primary.action_key, "reopen")}>Reopen</button>
              </div>
            ) : (
              <div style={{ marginTop: 8 }}>
                <button className="btn xs" onClick={() => createAction(f)}>Create action</button>
              </div>
            )}
          </div>
          );
        })}
        <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.45 }}>
          Action close/reopen records Finding usage events only; it does not change Finding status or write canonical ontology/graph.
        </div>
      </div>
    </div>
  );
}

/* ---------------- TraceLog ---------------- 
   Renders the live SSE trace stream as a styled timeline. Each event type
   gets its own color + shape so plan / step / evidence / finding / complete
   are scannable at a glance. */
function TraceLog({ events }) {
  const containerRef = React.useRef(null);
  // auto-scroll to bottom when new events arrive
  React.useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [events.length]);

  // last step gives us the progress
  const lastStep = [...events].reverse().find(e => e.eventName === "step");
  const stepNum = lastStep && lastStep.data && (lastStep.data.step || lastStep.data.index);
  const stepTotal = lastStep && lastStep.data && (lastStep.data.total || lastStep.data.steps);

  const colors = {
    plan:         "var(--proposed)",
    step:         "var(--accent)",
    evidence:     "var(--approved)",
    finding:      "var(--changes)",
    run_complete: "var(--approved)",
    stream_error: "var(--rejected)",
    error:        "var(--rejected)",
    _diag:        "var(--dim)",
  };
  const labels = {
    plan:         "PLAN",
    step:         "STEP",
    evidence:     "EVIDENCE",
    finding:      "FINDING",
    run_complete: "DONE",
    stream_error: "STREAM ERR",
    error:        "ERROR",
    message:      "MSG",
    _diag:        "TRANSPORT",
  };

  return (
    <div style={{
      border: "1px solid var(--line)",
      background: "var(--bg-1)",
    }}>
      {/* header — overall progress */}
      <div style={{
        padding: "8px 12px",
        borderBottom: "1px solid var(--line)",
        background: "var(--bg-2)",
        display: "flex",
        alignItems: "center",
        gap: 12,
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "var(--muted)",
        letterSpacing: "0.06em",
        textTransform: "uppercase",
      }}>
        <span style={{ color: "var(--accent)" }}>Live trace</span>
        {stepNum && stepTotal && (
          <>
            <span>·</span>
            <span style={{ color: "var(--text)" }}>step {stepNum}/{stepTotal}</span>
            <div style={{ flex: 1, height: 3, background: "var(--bg-3)", position: "relative", overflow: "hidden" }}>
              <div style={{
                position: "absolute", left: 0, top: 0, bottom: 0,
                width: ((stepNum / stepTotal) * 100) + "%",
                background: "var(--accent)",
                transition: "width 250ms",
              }} />
            </div>
            <span style={{ color: "var(--text-dim)" }}>{Math.round((stepNum / stepTotal) * 100)}%</span>
          </>
        )}
        {!stepTotal && <span style={{ color: "var(--dim)" }}>waiting for plan…</span>}
      </div>

      {/* event timeline */}
      <div ref={containerRef} style={{
        maxHeight: 260,
        overflow: "auto",
        padding: "8px 0",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
      }}>
        {events.length === 0 && (
          <div style={{ padding: "20px 14px", color: "var(--dim)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>
            <span style={{ display: "inline-block", width: 6, height: 6, background: "var(--accent)", marginRight: 8, animation: "pulse 1s ease-in-out infinite" }} />
            Connecting to stream…
          </div>
        )}
        {events.map((e, i) => {
          const c = colors[e.eventName] || "var(--muted)";
          const label = labels[e.eventName] || e.eventName.toUpperCase();
          const ts = e.ts.toISOString().slice(11, 19);
          return (
            <div key={i} style={{
              display: "grid",
              gridTemplateColumns: "60px 90px 1fr",
              gap: 10,
              padding: "5px 12px",
              borderBottom: i < events.length - 1 ? "1px solid var(--line-soft)" : "none",
              alignItems: "start",
            }}>
              <span style={{ color: "var(--dim)" }}>{ts}</span>
              <span style={{
                color: c, textTransform: "uppercase", letterSpacing: "0.06em",
                fontSize: 9.5,
                display: "inline-flex", alignItems: "center", gap: 5,
              }}>
                <span style={{ width: 6, height: 6, background: c, display: "inline-block" }} />
                {label}
              </span>
              <span style={{ color: "var(--text-dim)", wordBreak: "break-word", lineHeight: 1.45 }}>
                <TraceEventBody name={e.eventName} data={e.data} stage={e.stage} />
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TraceEventBody({ name, data, stage }) {
  if (name === "_diag") {
    // transport-level info — formatted clearly so user understands the state
    const s = stage;
    const elapsed = data && data.elapsed_ms != null ? ` · ${data.elapsed_ms}ms` : "";
    if (s === "submitted")        return <span><strong style={{ color: "var(--accent)", fontWeight: 500 }}>✓ Task submitted</strong> · <span style={{ color: "var(--dim)" }}>server returned: {JSON.stringify(data.response).slice(0, 120)}…</span></span>;
    if (s === "request_start")    return <span><span style={{ color: "var(--accent)" }}>→ POST</span> <span style={{ color: "var(--text)" }}>/run/stream</span> <span style={{ color: "var(--dim)" }}>opening connection…</span></span>;
    if (s === "response_headers") return <span><span style={{ color: data.status < 400 ? "var(--approved)" : "var(--rejected)" }}>← {data.status} {data.statusText}</span>{elapsed} · <span style={{ color: "var(--dim)" }}>Content-Type: {data.contentType || "—"}</span></span>;
    if (s === "first_chunk")      return <span><strong style={{ color: "var(--text)", fontWeight: 500 }}>● First byte received</strong>{elapsed} · <span style={{ color: "var(--dim)" }}>stream is alive, waiting for events…</span></span>;
    if (s === "warning")          return <span style={{ color: "var(--changes)" }}>⚠ {data.message}</span>;
    if (s === "parse_error")      return <span style={{ color: "var(--rejected)" }}>parse error on event "{data.event}": {data.error} · raw: {data.raw.slice(0, 80)}…</span>;
    if (s === "stream_closed")    return <span><strong style={{ color: "var(--text)", fontWeight: 500 }}>● Stream closed</strong>{elapsed} · {data.totalBytes} bytes</span>;
    if (s === "aborted")          return <span style={{ color: "var(--dim)" }}>aborted{elapsed}</span>;
    if (s === "error")            return <span style={{ color: "var(--rejected)" }}>✕ {data.message}{elapsed}</span>;
    return <span style={{ color: "var(--dim)" }}>{s} · {JSON.stringify(data)}</span>;
  }

  if (data == null) return <span style={{ color: "var(--dim)" }}>—</span>;
  if (typeof data === "string") return <span>{data}</span>;
  if (typeof data !== "object") return <span>{String(data)}</span>;

  switch (name) {
    case "plan": {
      const steps = data.query_plan || data.steps || data.plan;
      const taskLabel = data.task && typeof data.task === "string" ? data.task
        : data.task && data.task.question ? data.task.question
        : null;
      return (
        <span>
          {taskLabel && <span style={{ color: "var(--accent)" }}>{taskLabel} · </span>}
          {Array.isArray(steps) && (
            <span>{steps.length}-step plan: <span style={{ color: "var(--text)" }}>{steps.map(s => typeof s === "string" ? s : (s.name || s.tool)).join(" → ")}</span></span>
          )}
          {!steps && (data.description || data.summary) && <span>{data.description || data.summary}</span>}
        </span>
      );
    }
    case "step": {
      const n = data.step || data.index;
      const total = data.total || data.steps;
      const tool = data.tool || data.name;
      const summary = data.summary || data.result_summary || (data.output && (typeof data.output === "string" ? data.output : null));
      return (
        <span>
          <strong style={{ color: "var(--text)", fontWeight: 500 }}>
            {n != null && total != null ? `(${n}/${total}) ` : ""}{tool || "step"}
          </strong>
          {data.duration_ms != null && <span style={{ color: "var(--dim)" }}> · {data.duration_ms}ms</span>}
          {summary && <span style={{ color: "var(--muted)" }}> · {summary}</span>}
        </span>
      );
    }
    case "evidence": {
      const count = (data.evidence || data.paths || data.items || []).length;
      return (
        <span>
          <strong style={{ color: "var(--text)", fontWeight: 500 }}>
            {count > 0 ? `${count} evidence path${count === 1 ? "" : "s"} collected` : "evidence collected"}
          </strong>
          {data.summary && <span style={{ color: "var(--muted)" }}> · {data.summary}</span>}
        </span>
      );
    }
    case "finding": {
      const conclusion = data.conclusion || (data.finding && data.finding.conclusion);
      const status = data.status || (data.finding && data.finding.status) || "draft";
      return (
        <span>
          <strong style={{ color: "var(--text)", fontWeight: 500 }}>finding</strong>
          <span style={{ color: "var(--dim)" }}> · status {status}</span>
          {conclusion && <div style={{ color: "var(--muted)", marginTop: 2, fontFamily: "var(--font-sans)", fontSize: 12, lineHeight: 1.5 }}>"{String(conclusion).slice(0, 200)}{String(conclusion).length > 200 ? "…" : ""}"</div>}
        </span>
      );
    }
    case "run_complete":
      return <strong style={{ color: "var(--approved)", fontWeight: 500 }}>Run complete · {data.findings_count || (data.findings && data.findings.length) || 0} finding(s)</strong>;
    case "stream_error":
      return <span style={{ color: "var(--rejected)" }}>{data.message} {data.fallback ? <span style={{ color: "var(--muted)" }}>· {data.fallback}</span> : null}</span>;
    default:
      // generic — show keys
      try {
        const keys = Object.keys(data);
        return <span style={{ color: "var(--muted)" }}>{keys.slice(0, 4).map(k => `${k}=${truncJson(data[k])}`).join(" · ")}</span>;
      } catch { return <span>{JSON.stringify(data)}</span>; }
  }
}
function truncJson(v) {
  if (v == null) return "—";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 30 ? s.slice(0, 30) + "…" : s;
}

Object.assign(window, { TraceLog, TraceEventBody });

/* ---------------- AskHero ----------------
   The centered ask form shown when askMode is true, or as
   empty state. Question-first, scope-second. */
function AskHero({ tenant, question, setQuestion, centerNode, setCenterNode, depth, setDepth, limit, setLimit, isMock, submitting, actionMsg, onDismissMsg, onCancel, onSubmit }) {
  const tenantId = tenant ? tenant.id : "default";
  const isFraudTenant = tenantId === "creditcardfraud";

  React.useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onCancel && onCancel(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  function extractNode(text) {
    if (!entityTypes.length) return null;
    const typePattern = entityTypes.map(escapeRegExpRX).join("|");
    const m = text.match(new RegExp("\\b(" + typePattern + ")[:\\s#]+([\\w*.-]+)\\b", "i"));
    if (!m) return null;
    const type = canonicalTypeFromListRX(m[1], entityTypes) || m[1];
    return type + ":" + m[2];
  }
  function onQuestionChange(e) {
    const q = e.target.value;
    setQuestion(q);
    const node = extractNode(q);
    if (node) {
      setCenterNode(node);
      const [t] = node.split(":");
      if (t && t !== pickedType) setPickedType(t);
    }
  }

  // --- entity type list ---
  const [entityTypes, setEntityTypes] = React.useState([]);
  React.useEffect(() => {
    (async () => {
      try {
        const data = await window.AL_API.fetchJson("/api/instances/types?tenant=" + encodeURIComponent(tenantId));
        setEntityTypes((data.types || []).map(t => t.type || t.label));
      } catch (_) {}
    })();
  }, [tenantId]);

  // --- picked type (derived from centerNode or first available) ---
  const currentType = centerNode && centerNode.includes(":") ? centerNode.split(":")[0] : "";
  const [pickedType, setPickedType] = React.useState(currentType || "");
  React.useEffect(() => {
    if (!pickedType && entityTypes.length > 0) setPickedType(entityTypes[0]);
  }, [entityTypes]);

  // --- entity search ---
  const [entityQuery, setEntityQuery] = React.useState("");
  const [entities, setEntities] = React.useState([]);
  const [entitiesLoading, setEntitiesLoading] = React.useState(false);
  const [showDropdown, setShowDropdown] = React.useState(false);
  const debounceRef = React.useRef(null);
  const dropdownRef = React.useRef(null);

  function fetchEntities(type, q) {
    if (!type) return;
    setEntitiesLoading(true);
    const qs = new URLSearchParams({ tenant: tenantId, type, q: q || "", limit: "10" });
    window.AL_API.fetchJson("/api/instances/search?" + qs.toString())
      .then(data => { setEntities(data.instances || []); setEntitiesLoading(false); })
      .catch(() => { setEntities([]); setEntitiesLoading(false); });
  }

  React.useEffect(() => {
    if (pickedType) fetchEntities(pickedType, "");
  }, [pickedType, tenantId]);

  function onEntityQueryChange(e) {
    const q = e.target.value;
    setEntityQuery(q);
    setShowDropdown(true);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchEntities(pickedType, q), 250);
  }

  const prevLabelRef = React.useRef("");
  const questionRef = React.useRef(question || "");
  React.useEffect(() => { questionRef.current = question || ""; }, [question]);
  React.useEffect(() => {
    if (!entityTypes.length) {
      if (pickedType) setPickedType("");
      if (centerNode) {
        setCenterNode("");
        setEntityQuery("");
      }
      return;
    }
    const currentType = centerNode && centerNode.includes(":") ? centerNode.split(":")[0] : "";
    const selectedType = pickedType || currentType;
    const isValidType = selectedType && entityTypes.some(t => canonicalTypeFromListRX(selectedType, [t]) === t);
    if (!isValidType) {
      const nextType = entityTypes[0];
      setPickedType(nextType);
      if (centerNode) setCenterNode("");
      setEntityQuery("");
      setEntities([]);
    } else if (currentType && currentType !== pickedType) {
      setPickedType(currentType);
      setEntityQuery("");
      setEntities([]);
    }
  }, [entityTypes, pickedType, centerNode, setCenterNode, tenantId]);
  React.useEffect(() => {
    if (!prevLabelRef.current && centerNode && entities.length) {
      const match = entities.find(e => e.id === centerNode);
      if (match) prevLabelRef.current = match.label || match.id;
    }
  }, [centerNode, entities]);

  function selectEntity(ent) {
    const oldCenterNode = centerNode || "";
    setCenterNode(ent.id);
    const newLabel = ent.label || ent.id;
    setEntityQuery(newLabel);
    setShowDropdown(false);
    let prev = prevLabelRef.current;
    const q = questionRef.current.trim();
    if (!q || q === tenantEmptyQuestionRX(tenantId)) {
      setQuestion(defaultQuestionForTenantRX(tenantId, pickedType, newLabel, ent.id));
    } else if (prev && prev.length > 1 && q.includes(prev)) {
      setQuestion(q.split(prev).join(newLabel));
    } else {
      // try matching entity labels from the list
      let found = false;
      for (const e of entities) {
        if (e.id !== ent.id && e.label && e.label.length > 1 && q.includes(e.label)) {
          setQuestion(q.split(e.label).join(newLabel));
          found = true; break;
        }
      }
      // try matching the old center node ID pattern (e.g. "#4" or "Employee:4")
      if (!found && oldCenterNode) {
        const oldId = oldCenterNode.includes(":") ? oldCenterNode.split(":")[1] : oldCenterNode;
        const patterns = [oldCenterNode, `#${oldId}`, ` ${oldId} `];
        for (const pat of patterns) {
          if (q.includes(pat)) {
            setQuestion(q.split(pat).join(newLabel));
            found = true; break;
          }
        }
      }
    }
    prevLabelRef.current = newLabel;
  }

  function onTypeChange(e) {
    const t = e.target.value;
    setPickedType(t);
    setEntityQuery("");
    setCenterNode("");
  }

  // close dropdown on outside click
  React.useEffect(() => {
    function handler(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setShowDropdown(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const suggestions = React.useMemo(() => {
    const type = pickedType || "";
    const hasEntity = centerNode && centerNode.includes(":");
    const selectedEnt = hasEntity && entities.find(e => e.id === centerNode);
    const label = selectedEnt ? selectedEnt.label : (hasEntity ? centerNode : "");
    return suggestedQuestionsForTenantRX({ tenantId, type, centerNode, label, question, entities });
  }, [tenantId, pickedType, centerNode, entities, question]);
  return (
    <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-5) var(--pad-6)", position: "relative" }}>
      {/* close button — top right of the canvas */}
      <button onClick={onCancel} type="button"
              title="Close (Esc)"
              style={{
                position: "absolute",
                top: 20, right: 24,
                width: 32, height: 32,
                background: "var(--bg-2)",
                border: "1px solid var(--line)",
                color: "var(--muted)",
                fontFamily: "var(--font-mono)",
                fontSize: 16,
                cursor: "pointer",
                lineHeight: 1,
                display: "grid",
                placeItems: "center",
                zIndex: 10,
              }}
              onMouseEnter={e => { e.currentTarget.style.color = "var(--text)"; e.currentTarget.style.borderColor = "var(--line-strong)"; }}
              onMouseLeave={e => { e.currentTarget.style.color = "var(--muted)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
        ✕
      </button>
      <div style={{ maxWidth: 760, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginBottom: 8 }}>
          <div className="eyebrow accent">New reasoning task</div>
          <button onClick={onCancel} type="button"
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 10,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    color: "var(--muted)",
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    padding: 0,
                    textDecoration: "underline",
                    textUnderlineOffset: 3,
                  }}>
            ← back to task list
          </button>
        </div>
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: "0 0 8px 0", lineHeight: 1.15 }}>
          {isFraudTenant ? "Ask a fraud-scoped question." : "Ask a scoped question."}
        </h1>
        <p style={{ color: "var(--muted)", fontSize: 14, lineHeight: 1.55, margin: "0 0 24px 0", maxWidth: "60ch" }}>
          The agent reasons only over the approved graph and live source objects for this tenant. A scoped question pins a center node, depth, and limit — and produces a <span style={{ color: "var(--changes)" }}>draft</span> finding that you can review.
        </p>

        <form onSubmit={onSubmit}>
          <div style={{ border: "1px solid var(--line-strong)", background: "var(--bg-2)" }}>
            <div style={{ padding: "var(--pad-4) var(--pad-4)" }}>
              <div className="eyebrow" style={{ marginBottom: 6 }}>Question</div>
              <textarea autoFocus value={question} onChange={onQuestionChange}
                        rows={3}
                        placeholder={isFraudTenant ? "e.g. Which transactions have elevated fraud risk?" : "e.g. Why is Employee #4 workload unusual?"}
                        style={{
                          width: "100%",
                          background: "var(--bg-1)",
                          border: "1px solid var(--line)",
                          color: "var(--text)",
                          padding: "12px 14px",
                          fontFamily: "var(--font-sans)",
                          fontSize: 16,
                          lineHeight: 1.45,
                          resize: "vertical",
                          outline: "none",
                        }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", borderTop: "1px solid var(--line)" }}>
              <div style={{ padding: "var(--pad-3) var(--pad-4)", borderRight: "1px solid var(--line)" }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Center node</div>
                <div style={{ display: "flex", gap: 6 }} ref={dropdownRef}>
                  <select className="input" value={pickedType} onChange={onTypeChange}
                          style={{ width: 110, flexShrink: 0, cursor: "pointer" }}>
                    {entityTypes.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <div style={{ flex: 1, position: "relative" }}>
                    <input className="input" style={{ width: "100%" }}
                           value={entityQuery}
                           onChange={onEntityQueryChange}
                           onFocus={() => setShowDropdown(true)}
                           placeholder={entitiesLoading ? "Loading…" : entityTypes.length ? (entities.length ? entities[0].label || entities[0].id : "Search…") : "No tenant objects"} />
                    {showDropdown && entities.length > 0 && (
                      <div style={{
                        position: "absolute", top: "100%", left: 0, right: 0, zIndex: 20,
                        maxHeight: 240, overflowY: "auto",
                        background: "var(--bg-2)", border: "1px solid var(--line-strong)",
                        boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
                      }}>
                        {entities.map(ent => {
                          const selected = centerNode === ent.id;
                          return (
                            <div key={ent.id}
                                 onClick={() => selectEntity(ent)}
                                 style={{
                                   padding: "7px 10px", cursor: "pointer",
                                   display: "flex", alignItems: "center", gap: 8,
                                   background: selected ? "var(--bg-3)" : "transparent",
                                   borderBottom: "1px solid var(--line)",
                                 }}
                                 onMouseEnter={e => e.currentTarget.style.background = "var(--bg-3)"}
                                 onMouseLeave={e => { if (!selected) e.currentTarget.style.background = "transparent"; }}>
                              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", minWidth: 80 }}>{ent.id}</span>
                              <span style={{ fontSize: 12, color: "var(--text)" }}>{ent.label}</span>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
                {centerNode && (
                  <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", letterSpacing: "0.04em" }}>
                    {centerNode}
                  </div>
                )}
              </div>
              <div style={{ padding: "var(--pad-3) var(--pad-4)", borderRight: "1px solid var(--line)" }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Depth</div>
                <input className="input" type="number" min={1} max={3}
                       value={depth} onChange={e => setDepth(+e.target.value)} />
              </div>
              <div style={{ padding: "var(--pad-3) var(--pad-4)" }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Limit</div>
                <input className="input" type="number" value={limit} onChange={e => setLimit(+e.target.value)} />
              </div>
            </div>
            <div style={{
              padding: "var(--pad-3) var(--pad-4)",
              borderTop: "1px solid var(--line)",
              background: "var(--bg-3)",
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}>
              <span className="eyebrow" style={{ color: "var(--muted)" }}>Scope</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>
                approved-only · tenant-scoped · agent writes <span style={{ color: "var(--changes)" }}>draft</span> only
              </span>
              {isMock && (
                <span className="pill changes" style={{ marginLeft: "auto" }}>
                  <span className="dot" />Mock — will save locally
                </span>
              )}
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button type="submit" className="btn primary" style={{ padding: "10px 18px", fontSize: 12 }} disabled={!question.trim() || submitting}>
              {submitting ? "Creating…" : "↗ Create scoped question"}
            </button>
            <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
          </div>
          {actionMsg && (
            <div style={{
              marginTop: 12, padding: "10px 14px",
              border: "1px solid " + (actionMsg.kind === "ok" ? "oklch(0.74 0.13 165 / 0.4)" : "oklch(0.66 0.18 25 / 0.4)"),
              background: actionMsg.kind === "ok" ? "oklch(0.74 0.13 165 / 0.06)" : "oklch(0.66 0.18 25 / 0.06)",
              color: actionMsg.kind === "ok" ? "var(--approved)" : "var(--rejected)",
              fontFamily: "var(--font-mono)", fontSize: 11,
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <span>{actionMsg.msg}</span>
              <button type="button" className="btn xs ghost" style={{ marginLeft: "auto" }} onClick={onDismissMsg}>✕</button>
            </div>
          )}
        </form>

        <div style={{ marginTop: 32 }}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Suggested questions</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {suggestions.map((s, i) => {
              const active = centerNode === s.node;
              return (
              <button key={i}
                      type="button"
                      disabled={!s.node}
                      onClick={() => {
                        setQuestion(s.q);
                        if (s.node) {
                          setCenterNode(s.node);
                          const [t] = s.node.split(":");
                          if (t) setPickedType(t);
                          const ent = entities.find(e => e.id === s.node);
                          const lbl = ent ? ent.label : "";
                          setEntityQuery(lbl);
                          if (lbl) prevLabelRef.current = lbl;
                        }
                      }}
                      style={{
                        textAlign: "left",
                        padding: "12px 14px",
                        border: "1px solid " + (active ? "var(--accent-line)" : "var(--line)"),
                        background: active ? "var(--bg-3)" : "var(--bg-2)",
                        color: !s.node ? "var(--dim)" : active ? "var(--text)" : "var(--text-dim)",
                        fontFamily: "var(--font-sans)",
                        fontSize: 13,
                        cursor: s.node ? "pointer" : "default",
                        lineHeight: 1.45,
                        transition: "border-color 100ms, color 100ms",
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent-line)"; e.currentTarget.style.color = "var(--text)"; }}
                      onMouseLeave={e => { if (!active) { e.currentTarget.style.borderColor = "var(--line)"; e.currentTarget.style.color = "var(--text-dim)"; } }}>
                <div>{s.q}</div>
                {s.node && (
                <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: active ? "var(--accent)" : "var(--dim)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  center · {s.node}
                </div>
                )}
              </button>);
            })}
          </div>
        </div>

        <div style={{ marginTop: 32, padding: "14px 16px", border: "1px solid var(--line)", background: "var(--bg-2)" }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>How this works</div>
          <ol style={{ margin: 0, paddingLeft: 18, color: "var(--muted)", fontSize: 12, lineHeight: 1.7 }}>
            <li>Create a scoped question — pinned to a center node, depth, and limit on the approved graph.</li>
            <li>Run reasoning. The agent produces a <span style={{ color: "var(--changes)" }}>draft</span> conclusion with an evidence chain.</li>
            <li>Review the evidence and approve, request changes, or reject the finding.</li>
            <li>Approval cites the finding in the approved-finding layer — it does <strong style={{ color: "var(--text)" }}>not</strong> modify the canonical ontology or graph.</li>
          </ol>
        </div>
      </div>
    </div>
  );
}

/* ---------------- EntityPicker ----------------
   Reusable entity type + search picker. Used in both AskHero and sidebar "Ask with scope". */
function EntityPicker({ tenant, centerNode, setCenterNode, question, setQuestion, compact }) {
  const tenantId = tenant ? tenant.id : "default";

  const [entityTypes, setEntityTypes] = React.useState([]);
  React.useEffect(() => {
    (async () => {
      try {
        const data = await window.AL_API.fetchJson("/api/instances/types?tenant=" + encodeURIComponent(tenantId));
        setEntityTypes((data.types || []).map(t => t.type || t.label));
      } catch (_) {}
    })();
  }, [tenantId]);

  const currentType = centerNode && centerNode.includes(":") ? centerNode.split(":")[0] : "";
  const [pickedType, setPickedType] = React.useState(currentType || "");
  React.useEffect(() => {
    if (!entityTypes.length) {
      if (pickedType) setPickedType("");
      if (centerNode) {
        setCenterNode("");
        setEntityQuery("");
      }
      setEntities([]);
      return;
    }
    const selectedType = pickedType || currentType;
    const isValidType = selectedType && entityTypes.some(t => canonicalTypeFromListRX(selectedType, [t]) === t);
    if (!isValidType) {
      setPickedType(entityTypes[0]);
      if (centerNode) setCenterNode("");
      setEntityQuery("");
      setEntities([]);
    } else if (currentType && currentType !== pickedType) {
      setPickedType(currentType);
      setEntityQuery("");
      setEntities([]);
    }
  }, [entityTypes, tenantId, centerNode, pickedType]);

  const [entityQuery, setEntityQuery] = React.useState("");
  const [entities, setEntities] = React.useState([]);
  const [entitiesLoading, setEntitiesLoading] = React.useState(false);
  const [showDropdown, setShowDropdown] = React.useState(false);
  const debounceRef = React.useRef(null);
  const dropdownRef = React.useRef(null);
  const prevLabelRef = React.useRef("");
  const questionRef = React.useRef(question || "");
  React.useEffect(() => { questionRef.current = question || ""; }, [question]);
  function fetchEntities(type, q) {
    if (!type) return;
    setEntitiesLoading(true);
    const qs = new URLSearchParams({ tenant: tenantId, type, q: q || "", limit: "10" });
    window.AL_API.fetchJson("/api/instances/search?" + qs.toString())
      .then(data => {
        setEntities(data.instances || []);
        setEntitiesLoading(false);
        if (!prevLabelRef.current && centerNode) {
          const match = (data.instances || []).find(e => e.id === centerNode);
          if (match) prevLabelRef.current = match.label || match.id;
        }
      })
      .catch(() => { setEntities([]); setEntitiesLoading(false); });
  }

  React.useEffect(() => {
    if (pickedType) fetchEntities(pickedType, "");
  }, [pickedType, tenantId]);

  function onEntityQueryChange(e) {
    const q = e.target.value;
    setEntityQuery(q);
    setShowDropdown(true);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchEntities(pickedType, q), 250);
  }

  function selectEntity(ent) {
    const oldCenterNode = centerNode || "";
    setCenterNode(ent.id);
    const newLabel = ent.label || ent.id;
    setEntityQuery(newLabel);
    setShowDropdown(false);
    if (setQuestion) {
      const prev = prevLabelRef.current;
      const q = questionRef.current.trim();
      if (!q || q === tenantEmptyQuestionRX(tenantId)) {
        setQuestion(defaultQuestionForTenantRX(tenantId, pickedType, newLabel, ent.id));
      } else if (prev && prev.length > 1 && q.includes(prev)) {
        setQuestion(q.split(prev).join(newLabel));
      } else {
        let found = false;
        for (const e of entities) {
          if (e.id !== ent.id && e.label && e.label.length > 1 && q.includes(e.label)) {
            setQuestion(q.split(e.label).join(newLabel));
            found = true; break;
          }
        }
        if (!found && oldCenterNode) {
          const oldId = oldCenterNode.includes(":") ? oldCenterNode.split(":")[1] : oldCenterNode;
          const patterns = [oldCenterNode, `#${oldId}`, ` ${oldId} `];
          for (const pat of patterns) {
            if (q.includes(pat)) {
              setQuestion(q.split(pat).join(newLabel));
              found = true; break;
            }
          }
        }
      }
    }
    prevLabelRef.current = newLabel;
  }

  function onTypeChange(e) {
    const t = e.target.value;
    setPickedType(t);
    setEntityQuery("");
    setCenterNode("");
  }

  React.useEffect(() => {
    function handler(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setShowDropdown(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 4 }}>Center node</div>
      <div style={{ display: "flex", gap: 6 }} ref={dropdownRef}>
        <select className="input" value={pickedType} onChange={onTypeChange}
                style={{ width: compact ? 90 : 110, flexShrink: 0, cursor: "pointer" }}>
          {entityTypes.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <div style={{ flex: 1, position: "relative" }}>
          <input className="input" style={{ width: "100%" }}
                 value={entityQuery}
                 onChange={onEntityQueryChange}
                 onFocus={() => setShowDropdown(true)}
                 placeholder={entitiesLoading ? "Loading…" : entityTypes.length ? (entities.length ? entities[0].label || entities[0].id : "Search…") : "No tenant objects"} />
          {showDropdown && entities.length > 0 && (
            <div style={{
              position: "absolute", top: "100%", left: 0, right: 0, zIndex: 20,
              maxHeight: 200, overflowY: "auto",
              background: "var(--bg-2)", border: "1px solid var(--line-strong)",
              boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
            }}>
              {entities.map(ent => {
                const selected = centerNode === ent.id;
                return (
                  <div key={ent.id}
                       onClick={() => selectEntity(ent)}
                       style={{
                         padding: "7px 10px", cursor: "pointer",
                         display: "flex", alignItems: "center", gap: 8,
                         background: selected ? "var(--bg-3)" : "transparent",
                         borderBottom: "1px solid var(--line)",
                       }}
                       onMouseEnter={e => e.currentTarget.style.background = "var(--bg-3)"}
                       onMouseLeave={e => { if (!selected) e.currentTarget.style.background = "transparent"; }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", minWidth: 60 }}>{ent.id}</span>
                    <span style={{ fontSize: 12, color: "var(--text)" }}>{ent.label}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
      {centerNode && (
        <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", letterSpacing: "0.04em" }}>
          {centerNode}
        </div>
      )}
      {setQuestion && (() => {
        const type = pickedType || "";
        const hasEntity = centerNode && centerNode.includes(":");
        const selectedEnt = hasEntity && entities.find(e => e.id === centerNode);
        const label = selectedEnt ? selectedEnt.label : (hasEntity ? centerNode : "");
        const items = suggestedQuestionsForTenantRX({ tenantId, type, centerNode, label, question, entities })
          .filter(item => item.node)
          .slice(0, 4)
          .map(item => item.q);
        if (!items.length) return null;
        return (
          <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
            {items.map((s, i) => (
              <button key={i} type="button" className="btn xs ghost"
                      style={{ fontSize: 10, padding: "3px 8px", color: "var(--accent)", border: "1px solid var(--line)", borderRadius: 3, textAlign: "left" }}
                      onClick={() => setQuestion(s)}>
                {s}
              </button>
            ))}
          </div>
        );
      })()}
    </div>
  );
}

Object.assign(window, { AskHero, EntityPicker });
