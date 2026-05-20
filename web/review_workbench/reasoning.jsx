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

function Reasoning({ tenant }) {
  const [selectedKey, setSelectedKey] = useStateRX(null);
  const [activeTab, setActiveTab] = useStateRX("mine");  // mine | all | graph
  const [question, setQuestion] = useStateRX("Why is Employee #4 workload unusual?");
  const [centerNode, setCenterNode] = useStateRX("Employee:4");
  const [depth, setDepth] = useStateRX(1);
  const [limit, setLimit] = useStateRX(200);
  const [followup, setFollowup] = useStateRX("");
  const [reviewReason, setReviewReason] = useStateRX("");
  const [actionMsg, setActionMsg] = useStateRX(null);
  const [running, setRunning] = useStateRX(false);
  const [askMode, setAskMode] = useStateRX(false);
  const [submitting, setSubmitting] = useStateRX(false);
  const [evidenceFilter, setEvidenceFilter] = useStateRX("all");
  const [localTasks, setLocalTasks] = useStateRX([]);  // mock-mode submitted tasks
  // live SSE trace, keyed by canonical_key so it persists when user switches tasks
  const [traceByKey, setTraceByKey] = useStateRX({});
  const streamRef = useRefRX(null);

  const tasksQ = useApiData("reasoningTasks", [tenant ? tenant.id : "default"], { fallback: MOCK_TASKS });
  const isStale = tasksQ.source === "live-stale";
  const isMock  = tasksQ.source === "mock";
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
      default:        return allTasks.filter(isActiveTask);
    }
  }, [allTasks, activeTab]);

  const counts = {
    all:     allTasks.filter(isActiveTask).length,
    mine:    allTasks.filter(t => t.source === "manual").length,
    graph:   allTasks.filter(t => t.source === "graph").length,
  };

  const pendingKeyRef = useRefRX(null);
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
  const RUNNING_STATES = new Set(["active", "running", "in_progress", "pending", "queued", "started"]);
  const STALE_THRESHOLD_MS = 5 * 60 * 1000;  // 5 min — beyond this, "active" is suspicious

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

  // ----- stale cleanup -----
  const staleTasks = useMemoRX(() => tasks.filter(t => taskState(t).isStale), [tasks]);
  const [cleanupModal, setCleanupModal] = useStateRX(false);
  const [cleanupProgress, setCleanupProgress] = useStateRX(null);

  async function cleanupStale() {
    if (!staleTasks.length) return;
    setCleanupProgress({ done: 0, total: staleTasks.length, results: [], running: true, mode: "bulk" });
    const keys = staleTasks.map(t => t.canonical_key);
    try {
      // single bulk request — much faster than N individual calls
      await window.AL_API.bulkCloseTasks(tenant.id, { keys });
      const results = keys.map(k => ({ key: k, ok: true, endpoint: "POST /api/reasoning/tasks/bulk-close", method: "POST" }));
      setCleanupProgress({ done: keys.length, total: keys.length, results, running: false, mode: "bulk" });
    } catch (e) {
      // bulk failed — fall back to per-task close so partial success is still visible
      let done = 0;
      const results = [];
      for (const t of staleTasks) {
        try {
          await window.AL_API.closeTask(t.canonical_key, tenant.id);
          results.push({ key: t.canonical_key, ok: true, endpoint: "POST .../close", method: "POST" });
        } catch (err) {
          results.push({ key: t.canonical_key, ok: false, error: err.message || String(err) });
        }
        done += 1;
        setCleanupProgress({ done, total: staleTasks.length, results: [...results], running: done < staleTasks.length, mode: "per-task", bulkError: e.message });
      }
      setCleanupProgress({ done, total: staleTasks.length, results, running: false, mode: "per-task", bulkError: e.message });
    }
    window.dispatchEvent(new CustomEvent("aletheia:retry"));
  }
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
    if ((action === "approve" || action === "reject") && !reviewReason.trim()) {
      setActionMsg({ kind: "err", msg: "Reason required for approve / reject." }); return;
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

  async function deleteTask(taskKey) {
    if (!confirm("Delete this closed task? This cannot be undone.")) return;
    try {
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
        </div>
        <div className="spacer" />
        {staleTasks.length > 0 && (
          <button className="tool" style={{ borderColor: "oklch(0.66 0.18 25 / 0.5)", color: "var(--rejected)" }}
                  onClick={() => setCleanupModal(true)}
                  title={`${staleTasks.length} task(s) stuck in active for >5min`}>
            ⚠ Clean up {staleTasks.length} stale
          </button>
        )}
        {isMock  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {tasksQ.loading && tasksQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Refresh</button>
        {shouldRerun && (
          <button className="tool" onClick={runTask} disabled={running || !task}
                  title="Create a new task with the same question and scope, and run it.">
            {running ? "Rerunning…" : "↻ Rerun (new task)"}
          </button>
        )}
        {!shouldRerun && !finding && !runDone && (
          <button className="tool" onClick={runTask} disabled={running || !task}>{running ? "Running…" : "▶ Run reasoning"}</button>
        )}
        {task && !shouldRerun && (
          <button className="tool" onClick={stopAndClose}
                  style={{ color: "var(--rejected)" }}
                  title="Stop the current run (if any) and close this task.">
            ■ Stop &amp; close
          </button>
        )}
        <button className="tool primary" onClick={() => setAskMode(true)}>+ Ask question</button>
      </div>

      <div className="wb">
        {/* ============ LEFT — task list ============ */}
        <div className="col">
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Question → Answer → Evidence</div>
            <div style={{ marginTop: 4, fontSize: 13, color: "var(--text)" }}>Reasoning tasks</div>
            <button className="btn primary" style={{ width: "100%", marginTop: 10 }} onClick={() => setAskMode(true)}>+ Ask a new question</button>
            {tasks.filter(t => (t.status || "").toLowerCase() === "closed").length > 0 && (
              <button className="btn ghost" style={{ width: "100%", marginTop: 6, fontSize: 10, color: "var(--rejected)" }}
                      onClick={async () => {
                        const n = tasks.filter(t => (t.status || "").toLowerCase() === "closed").length;
                        if (!confirm(`Delete all ${n} closed task(s)? This cannot be undone.`)) return;
                        try {
                          const res = await window.AL_API.bulkDeleteClosed(tenant.id);
                          setLocalTasks(prev => prev.filter(t => (t.status || "").toLowerCase() !== "closed"));
                          if (task && (task.status || "").toLowerCase() === "closed") setSelectedKey(null);
                          setActionMsg({ kind: "ok", msg: `Deleted ${res.deleted_count} closed task(s).` });
                          window.dispatchEvent(new CustomEvent("aletheia:retry"));
                        } catch (e) {
                          setActionMsg({ kind: "err", msg: e.message || String(e) });
                        }
                      }}>
                ✕ Delete all closed tasks
              </button>
            )}
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            <ApiStatus q={tasksQ} what="reasoning tasks" />
            <div className="artifact-list">
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
                      <span className="key">{t.canonical_key}</span>
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
                    <div className="ar-title">{t.name || t.question || t.canonical_key}</div>
                    <div className="ar-meta">
                      <span>{t.center_node || "—"}</span>
                      {t.id != null && <span>#{t.id}</span>}
                      <span>{fmtTime(t.created_at)}</span>
                    </div>
                  </div>
                  <div className="ar-right" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {t.status}
                    {(t.status || "").toLowerCase() === "closed" && (
                      <button className="btn xs ghost" title="Delete this closed task"
                              onClick={e => { e.stopPropagation(); deleteTask(t.canonical_key); }}
                              style={{ padding: "2px 5px", fontSize: 9, color: "var(--rejected)", border: "1px solid var(--line)" }}>
                        ✕
                      </button>
                    )}
                  </div>
                </div>
                );
              })}
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
          {askMode ? (
            <AskHero
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
                    <Pill kind={statusToPill[task.status] || "proposed"}>{task.status}</Pill>
                    {task.confidence != null && <Pill kind="accent">conf {Math.round((task.confidence||0) * 100)}%</Pill>}
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
                        const ev = e._raw ? e : {
                          kind: e.kind || "fact",
                          title: e.title || e.summary || e.description || "—",
                          src: e.src || e.source_ref || e.source || "",
                          conf: e.conf != null ? e.conf : (typeof e.confidence === "number" ? e.confidence : null),
                        };
                        return (
                          <div key={i} className={"evidence-item " + ev.kind}>
                            <div className="v-bar" />
                            <div className="kind">{ev.kind}</div>
                            <div className="body-x">
                              <div className="title">{ev.title}</div>
                              <div className="src">{ev.src}</div>
                            </div>
                            <div className="conf-side">
                              {ev.conf != null ? <><span style={{ color: "var(--text)" }}>{Math.round(ev.conf * 100)}%</span><span style={{ color: "var(--dim)", fontSize: 9, marginTop: 2 }}>confidence</span></> : <span className="faint">—</span>}
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
                         placeholder="Decision rationale (required for approve / reject)…" />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn approve" onClick={() => reviewFinding("approve")} disabled={!finding}>✓ Approve finding</button>
                    <button className="btn changes" onClick={() => reviewFinding("needs-changes")} disabled={!finding}>↻ Needs changes</button>
                    <button className="btn reject"  onClick={() => reviewFinding("reject")} disabled={!finding}>✕ Reject</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("comment")} disabled={!finding}>Comment</button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* ============ RIGHT — ask + follow-up ============ */}
        <div className="col inspector">
          <div className="section">
            <div className="section-head"><span>Ask with scope</span></div>
            <div className="section-body">
              <form onSubmit={submitQuestion} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div>
                  <div className="eyebrow" style={{ marginBottom: 4 }}>Question</div>
                  <textarea className="textarea" rows={3} value={question} onChange={e => setQuestion(e.target.value)}
                            placeholder="Why is Employee #4 workload unusual?" />
                </div>
                <div>
                  <div className="eyebrow" style={{ marginBottom: 4 }}>Center node</div>
                  <input className="input" value={centerNode} onChange={e => setCenterNode(e.target.value)} />
                </div>
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
        </div>
      </div>

      <CleanupStaleModal open={cleanupModal} onClose={() => { setCleanupModal(false); setCleanupProgress(null); }}
                         staleTasks={staleTasks} progress={cleanupProgress}
                         taskState={taskState} onRun={cleanupStale} />
    </div>
  );
}

/* ---------------- CleanupStaleModal ---------------- */
function CleanupStaleModal({ open, onClose, staleTasks, progress, taskState, onRun }) {
  if (!open) return null;
  const summary = progress ? {
    ok:  progress.results.filter(r => r.ok).length,
    bad: progress.results.filter(r => !r.ok).length,
  } : null;
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
          <div className="eyebrow" style={{ color: "var(--rejected)" }}>⚠ Cleanup</div>
          <div style={{ marginLeft: 10, fontSize: 16, color: "var(--text)" }}>Stale reasoning tasks</div>
          <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", color: "var(--muted)", border: "1px solid var(--line)", padding: "3px 8px", fontFamily: "var(--font-mono)", fontSize: 10, cursor: "pointer" }}>ESC</button>
        </div>

        <div style={{ padding: 20, overflow: "auto", flex: 1 }}>
          <p style={{ color: "var(--muted)", fontSize: 13, lineHeight: 1.55, margin: "0 0 16px 0" }}>
            These tasks have <span style={{ color: "var(--text)" }}>active</span> status but haven't updated for over 5 minutes — almost certainly orphaned (worker crash or service restart). Cleaning them up will call <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>POST /api/reasoning/tasks/bulk-close</code> with the selected keys, falling back to per-task <code style={{ fontFamily: "var(--font-mono)", color: "var(--text-dim)" }}>/close</code> if the bulk call fails.
          </p>

          <div style={{ border: "1px solid var(--line)", marginBottom: 16 }}>
            {staleTasks.length === 0 ? (
              <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                No stale tasks. Nothing to clean up.
              </div>
            ) : staleTasks.map(t => {
              const ts = taskState(t);
              const r = progress && progress.results.find(x => x.key === t.canonical_key);
              return (
                <div key={t.canonical_key} style={{
                  display: "grid",
                  gridTemplateColumns: "3px 1fr auto",
                  borderBottom: "1px solid var(--line-soft)",
                }}>
                  <div style={{ background: r ? (r.ok ? "var(--approved)" : "var(--rejected)") : "var(--rejected)" }} />
                  <div style={{ padding: "10px 14px", minWidth: 0 }}>
                    <div style={{ display: "flex", gap: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                      <span style={{ color: "var(--accent)" }}>{t.canonical_key}</span>
                      <span>·</span>
                      <span>{t.center_node || "—"}</span>
                      <span>·</span>
                      <span style={{ color: "var(--rejected)" }}>stale {ts.ageLbl}</span>
                    </div>
                    <div style={{ marginTop: 3, color: "var(--text)", fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {t.name || t.question || t.canonical_key}
                    </div>
                    {r && (
                      <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: r.ok ? "var(--approved)" : "var(--rejected)" }}>
                        {r.ok ? `✓ cancelled via ${r.method} ${r.endpoint}` : `✕ ${r.error}`}
                      </div>
                    )}
                  </div>
                  <div style={{ padding: "10px 14px", display: "flex", alignItems: "center", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                    {r ? (r.ok ? "DONE" : "FAILED") : progress && progress.running ? "…" : "pending"}
                  </div>
                </div>
              );
            })}
          </div>

          {progress && (
            <div style={{
              padding: "10px 14px",
              border: "1px solid var(--line)",
              background: "var(--bg-1)",
              fontFamily: "var(--font-mono)", fontSize: 11,
              marginBottom: 16,
            }}>
              <div style={{ display: "flex", gap: 14 }}>
                <span><span style={{ color: "var(--dim)" }}>PROGRESS</span> <span style={{ color: "var(--text)" }}>{progress.done}/{progress.total}</span></span>
                <span><span style={{ color: "var(--dim)" }}>OK</span> <span style={{ color: "var(--approved)" }}>{summary.ok}</span></span>
                <span><span style={{ color: "var(--dim)" }}>FAILED</span> <span style={{ color: "var(--rejected)" }}>{summary.bad}</span></span>
                <span style={{ marginLeft: "auto", color: progress.running ? "var(--changes)" : "var(--approved)" }}>
                  {progress.running ? "● running" : "● complete"}
                </span>
              </div>
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn primary"
                    onClick={onRun}
                    disabled={!staleTasks.length || (progress && progress.running)}>
              {progress && !progress.running ? "↻ Re-run cleanup" : `⚠ Close ${staleTasks.length} stale task${staleTasks.length === 1 ? "" : "s"}`}
            </button>
            <button className="btn ghost" onClick={onClose}>Close</button>
            {progress && progress.bulkError && (
              <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)", alignSelf: "center" }}>
                ⚠ bulk-close failed, fell back to per-task: {progress.bulkError}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Reasoning, CleanupStaleModal });

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
function AskHero({ question, setQuestion, centerNode, setCenterNode, depth, setDepth, limit, setLimit, isMock, submitting, actionMsg, onDismissMsg, onCancel, onSubmit }) {
  // ESC closes the ask hero
  React.useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onCancel && onCancel(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const NODE_RE = /\b(Employee|Customer|Order|Product|Category|Region|Supplier|Shipper|Territory)[:\s#]+(\d+|\*)\b/i;
  function extractNode(text) {
    const m = text.match(NODE_RE);
    if (!m) return null;
    const type = m[1].charAt(0).toUpperCase() + m[1].slice(1).toLowerCase();
    return type + ":" + m[2];
  }
  function onQuestionChange(e) {
    const q = e.target.value;
    setQuestion(q);
    const node = extractNode(q);
    if (node) setCenterNode(node);
  }

  const suggestions = [
    { q: "Why is Employee #4 workload unusual?", node: "Employee:4" },
    { q: "What is the effective span of control for Employee #9?", node: "Employee:9" },
    { q: "Is Customer #88 a concentration risk?", node: "Customer:88" },
    { q: "Does tenure correlate with order cycle time?", node: "Employee:*" },
  ];
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
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: "0 0 8px 0", lineHeight: 1.15 }}>Ask a scoped question.</h1>
        <p style={{ color: "var(--muted)", fontSize: 14, lineHeight: 1.55, margin: "0 0 24px 0", maxWidth: "60ch" }}>
          The agent reasons only over the approved graph in this tenant. A scoped question pins a center node, depth, and limit — and produces a <span style={{ color: "var(--changes)" }}>draft</span> finding that you can review.
        </p>

        <form onSubmit={onSubmit}>
          <div style={{ border: "1px solid var(--line-strong)", background: "var(--bg-2)" }}>
            <div style={{ padding: "var(--pad-4) var(--pad-4)" }}>
              <div className="eyebrow" style={{ marginBottom: 6 }}>Question</div>
              <textarea autoFocus value={question} onChange={onQuestionChange}
                        rows={3}
                        placeholder="e.g. Why is Employee #4 workload unusual?"
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
                <input className="input" value={centerNode} onChange={e => setCenterNode(e.target.value)}
                       placeholder="Employee:4" />
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
                      onClick={() => { setQuestion(s.q); setCenterNode(s.node); }}
                      style={{
                        textAlign: "left",
                        padding: "12px 14px",
                        border: "1px solid " + (active ? "var(--accent-line)" : "var(--line)"),
                        background: active ? "var(--bg-3)" : "var(--bg-2)",
                        color: active ? "var(--text)" : "var(--text-dim)",
                        fontFamily: "var(--font-sans)",
                        fontSize: 13,
                        cursor: "pointer",
                        lineHeight: 1.45,
                        transition: "border-color 100ms, color 100ms",
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent-line)"; e.currentTarget.style.color = "var(--text)"; }}
                      onMouseLeave={e => { if (!active) { e.currentTarget.style.borderColor = "var(--line)"; e.currentTarget.style.color = "var(--text-dim)"; } }}>
                <div>{s.q}</div>
                <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: active ? "var(--accent)" : "var(--dim)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  center · {s.node}
                </div>
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

Object.assign(window, { AskHero });
