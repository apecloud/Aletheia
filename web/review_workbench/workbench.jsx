/* Aletheia — Workbench screen (wired to real API with mock fallback) */
const { useState: useStateWB, useMemo: useMemoWB, useEffect: useEffectWB } = React;

function Workbench({ data, tenant }) {
  const [selectedId, setSelectedId] = useStateWB(null);
  const [statusFilter, setStatusFilter] = useStateWB(new Set(["proposed","changes","approved","rejected"]));
  const [typeFilter, setTypeFilter] = useStateWB(new Set(["ObjectType","LinkType","Property"]));
  const [search, setSearch] = useStateWB("");
  const [mode, setMode] = useStateWB("summary"); // summary | detail | trace
  const [reason, setReason] = useStateWB("");
  const [actionState, setActionState] = useStateWB(null); // {kind:"ok"|"err", msg}

  // ----- list of artifacts (live; mock fallback only if user enabled it) -----
  const listQ = useApiData("artifacts", [tenant.id, {}], { fallback: data.ARTIFACTS });
  const artifacts = listQ.data || [];
  const isStale = listQ.source === "live-stale";
  const isMock  = listQ.source === "mock";

  // ensure selectedId is valid for the current list
  useEffectWB(() => {
    if (artifacts.length === 0) { setSelectedId(null); return; }
    if (!selectedId || !artifacts.some(a => (a.id === selectedId || a.canonical_key === selectedId))) {
      setSelectedId(artifacts[0].id || artifacts[0].canonical_key);
    }
  }, [artifacts.map(a => a.id).join("|")]);

  // filtered view
  const filtered = useMemoWB(() => {
    return artifacts.filter(a =>
      statusFilter.has(a.status) &&
      typeFilter.has(a.type) &&
      (search === "" ||
        (a.title || "").toLowerCase().includes(search.toLowerCase()) ||
        (a.key   || "").toLowerCase().includes(search.toLowerCase()))
    );
  }, [artifacts, statusFilter, typeFilter, search]);

  // ----- detail for selected artifact (live) -----
  const detailQ = useApiData(
    "artifact",
    [selectedId, tenant.id],
    { enabled: !!selectedId }
  );
  const fromList = artifacts.find(a => (a.id === selectedId || a.canonical_key === selectedId)) || artifacts[0];
  const selected = useMemoWB(() => {
    if (detailQ.data) return detailQ.data;
    return fromList || null;
  }, [detailQ.data, fromList]);

  function toggle(setter, set, val) {
    const s = new Set(set);
    if (s.has(val)) s.delete(val); else s.add(val);
    setter(s);
  }

  const counts = {
    proposed: artifacts.filter(a => a.status === "proposed").length,
    changes:  artifacts.filter(a => a.status === "changes").length,
    approved: artifacts.filter(a => a.status === "approved").length,
    rejected: artifacts.filter(a => a.status === "rejected").length,
  };

  // ---- review actions ----
  async function doAction(action) {
    if (!selected) return;
    if ((action === "approve" || action === "reject") && !reason.trim()) {
      setActionState({ kind: "err", msg: "Reason required for approve / reject." });
      return;
    }
    try {
      await window.AL_API.reviewAction(selected.canonical_key, action, {
        reason: reason.trim(),
        reviewer: "M. Aoki",
      }, tenant.id);
      setActionState({ kind: "ok", msg: `${action} recorded.` });
      setReason("");
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (e) {
      setActionState({ kind: "err", msg: e.message || String(e) });
    }
  }

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className="tab active">Review Queue <span className="ct">{filtered.length}</span></div>
          <div className="tab">My Drafts <span className="ct">3</span></div>
          <div className="tab">Watching <span className="ct">12</span></div>
          <div className="tab">History</div>
        </div>
        <div className="spacer" />
        {isMock  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {listQ.loading && listQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Refresh</button>
        <button className="tool">⤓ Export</button>
        <button className="tool primary">+ New review</button>
      </div>

      <div className="wb">
        {/* ============ LEFT — artifact list ============ */}
        <div className="col">
          <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div style={{ position: "relative" }}>
              <input className="input" value={search} onChange={e => setSearch(e.target.value)}
                     placeholder="search name, key, id…"
                     style={{ paddingLeft: 28 }} />
              <span style={{ position: "absolute", left: 9, top: 7, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>⌕</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Status</div>
                <div className="chip-row">
                  <Chip active={statusFilter.has("proposed")} count={counts.proposed} onClick={() => toggle(setStatusFilter, statusFilter, "proposed")}>Proposed</Chip>
                  <Chip active={statusFilter.has("changes")} count={counts.changes} onClick={() => toggle(setStatusFilter, statusFilter, "changes")}>Changes</Chip>
                  <Chip active={statusFilter.has("approved")} count={counts.approved} onClick={() => toggle(setStatusFilter, statusFilter, "approved")}>Approved</Chip>
                  <Chip active={statusFilter.has("rejected")} count={counts.rejected} onClick={() => toggle(setStatusFilter, statusFilter, "rejected")}>Rejected</Chip>
                </div>
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>Type</div>
                <div className="chip-row">
                  <Chip active={typeFilter.has("ObjectType")} onClick={() => toggle(setTypeFilter, typeFilter, "ObjectType")}>Object</Chip>
                  <Chip active={typeFilter.has("LinkType")} onClick={() => toggle(setTypeFilter, typeFilter, "LinkType")}>Link</Chip>
                  <Chip active={typeFilter.has("Property")} onClick={() => toggle(setTypeFilter, typeFilter, "Property")}>Property</Chip>
                </div>
              </div>
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto" }}>
            <ApiStatus q={listQ} what="artifacts" />
            <div className="artifact-list">
              {listQ.source === "live" && filtered.length === 0 && (
                <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                  No artifacts match filters.
                </div>
              )}
              {filtered.map(a => {
                const aid = a.id || a.canonical_key;
                return (
                  <div key={aid}
                       className={`artifact-row ${a.status}` + (aid === selectedId ? " selected" : "")}
                       onClick={() => setSelectedId(aid)}>
                    <div className="ar-bar" />
                    <div className="ar-main">
                      <div className="ar-top">
                        <span className="type">{a.type === "ObjectType" ? "OBJ" : a.type === "LinkType" ? "LINK" : "PROP"}</span>
                        <span>·</span>
                        <span className="key">{aid}</span>
                      </div>
                      <div className="ar-title">{a.title}</div>
                      <div className="ar-meta">
                        <span className="conf"><span className="bar-mini"><i style={{ width: ((a.confidence||0) * 100) + "%" }} /></span><span>{Math.round((a.confidence||0) * 100)}%</span></span>
                        <span>v{a.version}</span>
                        <span>{(a.updated || "").slice(-5)}</span>
                      </div>
                    </div>
                    <div className="ar-right">{a.status}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* ============ CENTER — artifact workspace ============ */}
        <div className="col" style={{ display: "flex", flexDirection: "column" }}>
          {!selected ? (
            <div style={{ flex: 1, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              Select an artifact from the left to begin review.
            </div>
          ) : (
            <>
              <div className="art-header">
                <div className="crumb">
                  <span className="type">{selected.type === "LinkType" ? "Link Type" : selected.type === "ObjectType" ? "Object Type" : "Property"}</span>
                  <span className="sep">/</span>
                  <span>{selected.id}</span>
                  <span className="sep">/</span>
                  <span>v{selected.version}</span>
                  {selected.updated && <><span className="sep">·</span><span>updated {selected.updated}</span></>}
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                    <Pill kind={selected.status}>{selected.status}</Pill>
                    <Pill kind="accent">conf {Math.round((selected.confidence||0) * 100)}%</Pill>
                  </span>
                </div>
                <h1>{selected.title}</h1>
                <p className="desc">{selected.desc || "No description recorded."}</p>
                <div className="row">
                  <div className="stat lg">
                    <span className="label">Source agent</span>
                    <span className="val mono">{selected.agent}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Evidence</span>
                    <span className="val mono">{(selected.evidence || []).length} items</span>
                  </div>
                  <div className="stat">
                    <span className="label">Reviews</span>
                    <span className="val mono">{(selected.audit || []).length} events</span>
                  </div>
                  <div className="stat">
                    <span className="label">Ingestion eligible</span>
                    <span className="val" style={{ color: selected.status === "approved" ? "var(--approved)" : "var(--changes)" }}>
                      {selected.status === "approved" ? "yes · canonical" : "blocked · awaiting approval"}
                    </span>
                  </div>
                </div>
              </div>

              {/* mode tabs */}
              <div className="subbar" style={{ background: "var(--bg-1)" }}>
                <div className="tabs">
                  <div className={"tab" + (mode === "summary" ? " active" : "")} onClick={() => setMode("summary")}>Evidence &amp; Reasoning</div>
                  <div className={"tab" + (mode === "detail"  ? " active" : "")} onClick={() => setMode("detail")}>Payload</div>
                  <div className={"tab" + (mode === "trace"   ? " active" : "")} onClick={() => setMode("trace")}>Audit Trail <span className="ct">{(selected.audit || []).length}</span></div>
                </div>
                <div className="spacer" />
                <button className="tool">⤢ Open in graph</button>
                <button className="tool">≡ Diff</button>
              </div>

              <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
                {mode === "summary" && (
                  <>
                    {isMock && (
                      <Panel eyebrow="Conversation" title="Reasoning thread (mock)" count={`${data.REASONING_THREAD.length} turns`} style={{ marginBottom: 16 }}>
                        <div className="thread">
                          {data.REASONING_THREAD.map((m, i) => (
                            <div key={i} className={"msg " + (m.role === "agent" ? "agent" : "user")}>
                              <div className="who">{m.role === "agent" ? "AGT" : "YOU"}</div>
                              <div className="body-x">
                                <div className="meta">
                                  <span className="name">{m.name}</span>
                                  <span>·</span><span>{m.time}</span>
                                </div>
                                <div>{m.text}</div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </Panel>
                    )}

                    <Panel eyebrow="Provenance" title="Evidence chain"
                           count={`${(selected.evidence || []).length} items`} nopad>
                      {(selected.evidence || []).length === 0 ? (
                        <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                          No evidence attached.
                        </div>
                      ) : (
                        <div className="evidence-list">
                          {(selected.evidence || []).map((e, i) => (
                            <div key={i} className={"evidence-item " + e.kind}>
                              <div className="v-bar" />
                              <div className="kind">{e.kind}</div>
                              <div className="body-x">
                                <div className="title">{e.title}</div>
                                <div className="src">{e.src}</div>
                              </div>
                              <div className="conf-side">
                                {e.conf != null ? <><span style={{ color: "var(--text)" }}>{Math.round(e.conf * 100)}%</span><span style={{ color: "var(--dim)", fontSize: 9, marginTop: 2 }}>confidence</span></> : <span className="faint">—</span>}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </Panel>
                  </>
                )}

                {mode === "detail" && (
                  <Panel eyebrow="Artifact" title="Payload (JSON)" count={`v${selected.version}`}
                         actions={<><button className="btn xs">Copy</button><button className="btn xs">Edit</button></>}>
                    <JsonView data={selected.payload || {}} />
                  </Panel>
                )}

                {mode === "trace" && (
                  <Panel eyebrow="Audit" title="Decision history" count={`${(selected.audit || []).length} events`} nopad>
                    {(selected.audit || []).length === 0 ? (
                      <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                        No audit events recorded.
                      </div>
                    ) : (
                      <div className="audit-list">
                        {(selected.audit || []).map((a, i) => (
                          <div key={i} className="audit-item">
                            <span className="ts">{a.ts}</span>
                            <span className={"act " + a.act}>{a.act}</span>
                            <span className="det"><span className="who">{a.who}</span> · {a.detail}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </Panel>
                )}
              </div>

              {/* action bar */}
              <div className="action-bar" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
                {actionState && (
                  <div style={{
                    padding: "8px 12px",
                    fontFamily: "var(--font-mono)", fontSize: 11,
                    border: "1px solid " + (actionState.kind === "ok" ? "oklch(0.74 0.13 165 / 0.4)" : "oklch(0.78 0.14 75 / 0.4)"),
                    color: actionState.kind === "ok" ? "var(--approved)" : "var(--changes)",
                    background: actionState.kind === "ok" ? "oklch(0.74 0.13 165 / 0.06)" : "oklch(0.78 0.14 75 / 0.06)",
                  }}>{actionState.msg}</div>
                )}
                <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                  <input className="reason-input" value={reason} onChange={e => setReason(e.target.value)}
                         placeholder="Decision rationale (required for approve / reject)…" />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn approve"  onClick={() => doAction("approve")}>✓ Approve</button>
                    <button className="btn changes"  onClick={() => doAction("needs-changes")}>↻ Needs changes</button>
                    <button className="btn reject"   onClick={() => doAction("reject")}>✕ Reject</button>
                    <button className="btn ghost"    onClick={() => doAction("comment")}>Comment</button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* ============ RIGHT — inspector ============ */}
        <div className="col inspector">
          <div className="section">
            <div className="section-head"><span>Neighborhood</span><span className="ct">depth 1</span></div>
            <div className="section-body" style={{ padding: 0 }}>
              <MiniGraph data={data.GRAPH} />
              <div style={{ padding: "var(--pad-3)", borderTop: "1px solid var(--line)" }}>
                <button className="btn" style={{ width: "100%" }}>↗ Open scoped reasoning</button>
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Impact</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">workload-bal</span><span className="track"><i style={{ width: "84%" }} /></span><span className="num">84</span></div>
              <div className="hbar"><span className="lbl">concentration</span><span className="track"><i style={{ width: "72%" }} /></span><span className="num">72</span></div>
              <div className="hbar"><span className="lbl">tenure-bands</span><span className="track"><i style={{ width: "41%" }} /></span><span className="num">41</span></div>
              <div className="hbar"><span className="lbl">span-of-ctrl</span><span className="track"><i style={{ width: "26%" }} /></span><span className="num">26</span></div>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Source refs</span><span className="ct">{(selected?.sourceRefs || []).length}</span></div>
            <div className="section-body" style={{ padding: 0 }}>
              {((selected?.sourceRefs && selected.sourceRefs.length) ? selected.sourceRefs : ["schema://hr.employees", "policy://hr-handbook §4.2", "audit://pending"]).map((r, i) => (
                <div key={i} style={{ padding: "8px 14px", borderBottom: "1px solid var(--line-soft)", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>
                  {typeof r === "string" ? r : (r.path || JSON.stringify(r))}
                </div>
              ))}
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>Quick actions</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>↗ Open in graph explorer</button>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>≡ Diff vs previous version</button>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>⤓ Export evidence pack</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* tiny graph rendered as svg */
function MiniGraph({ data }) {
  const nodes = data.nodes;
  const edges = data.edges;
  const map = Object.fromEntries(nodes.map(n => [n.id, n]));
  const colorByType = { Employee: "var(--accent)", Order: "var(--changes)", Customer: "var(--proposed)", Region: "var(--dim)" };
  return (
    <div className="mini-graph" style={{ height: 220, borderTop: 0, borderLeft: 0, borderRight: 0 }}>
      <svg viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%" }}>
        {edges.map((e, i) => {
          const s = map[e.s], t = map[e.t];
          if (!s || !t) return null;
          return (
            <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                  stroke={e.muted ? "var(--faint)" : e.flag ? "oklch(0.66 0.18 25 / 0.6)" : "var(--line-strong)"}
                  strokeWidth={e.flag ? 1.5 : 1} strokeDasharray={e.muted ? "3 3" : ""} />
          );
        })}
        {nodes.map((n, i) => (
          <g key={i}>
            <circle cx={n.x} cy={n.y} r={n.r + 4} fill="var(--bg-1)" />
            <circle cx={n.x} cy={n.y} r={n.r}
                    fill={n.center ? "var(--accent-bg)" : "var(--bg-2)"}
                    stroke={n.flag ? "var(--rejected)" : (n.muted ? "var(--faint)" : colorByType[n.type] || "var(--text-dim)")}
                    strokeWidth={n.center ? 2 : 1.2} />
            {n.center && <circle cx={n.x} cy={n.y} r={n.r - 5} fill="var(--accent)" />}
          </g>
        ))}
      </svg>
    </div>
  );
}

Object.assign(window, { Workbench, MiniGraph });
