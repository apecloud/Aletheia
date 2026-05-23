/* Aletheia — Ontology browse, Quality dashboard, Runtime */

const { useState: useStateXS, useMemo: useMemoXS, useEffect: useEffectXS } = React;

/* ---------------- ONTOLOGY ---------------- */
function Ontology({ data, tenant }) {
  const [active, setActive] = useStateXS("ObjectType");
  const [selectedId, setSelectedId] = useStateXS(null);
  const [search, setSearch] = useStateXS("");
  const [detailMode, setDetailMode] = useStateXS("source");
  const [statusView, setStatusView] = useStateXS("approved");
  const [reviewReason, setReviewReason] = useStateXS("");
  const [reviewMsg, setReviewMsg] = useStateXS(null);

  const listQ = useApiData("artifacts", [tenant ? tenant.id : "default", {}], { fallback: data.ARTIFACTS });
  const artifacts = listQ.data || [];
  const isMock = listQ.source === "mock";
  const isStale = listQ.source === "live-stale";

  const grouped = useMemoXS(() => ({
    ObjectType: artifacts.filter(a => a.type === "ObjectType"),
    LinkType:   artifacts.filter(a => a.type === "LinkType"),
    Property:   artifacts.filter(a => a.type === "Property"),
    Action:     artifacts.filter(a => a.type === "Action"),
  }), [artifacts]);
  const activeArtifacts = grouped[active] || [];

  const filtered = useMemoXS(() => {
    const q = search.trim().toLowerCase();
    return (grouped[active] || [])
      .filter(a => statusView === "all" || a.status === statusView)
      .filter(a => !q
        || (a.title || "").toLowerCase().includes(q)
        || (a.key || "").toLowerCase().includes(q)
        || (a.canonical_key || a.id || "").toLowerCase().includes(q)
        || (a.desc || "").toLowerCase().includes(q));
  }, [grouped, active, statusView, search]);

  useEffectXS(() => {
    if (statusView === "approved" && !search.trim() && (grouped[active] || []).length > 0 && filtered.length === 0) {
      setStatusView("all");
      return;
    }
    if (filtered.length === 0) { setSelectedId(null); return; }
    if (!selectedId || !filtered.some(a => (a.id === selectedId || a.canonical_key === selectedId))) {
      setSelectedId(filtered[0].id || filtered[0].canonical_key);
    }
  }, [active, statusView, filtered.map(a => a.id || a.canonical_key).join("|")]);

  const detailQ = useApiData("artifact", [selectedId, tenant ? tenant.id : "default"], { enabled: !!selectedId });
  const fromList = artifacts.find(a => (a.id === selectedId || a.canonical_key === selectedId)) || filtered[0] || null;
  const selected = detailQ.data || fromList;
  const sourceRefs = (selected && selected.sourceRefs) || [];
  const evidence = (selected && selected.evidence) || [];
  const audit = (selected && selected.audit) || [];
  const canonicalKey = selected && (selected.canonical_key || selected.id);
  const tenantId = tenant ? tenant.id : "default";

  const stats = {
    total: activeArtifacts.length,
    approved: activeArtifacts.filter(a => a.status === "approved").length,
    proposed: activeArtifacts.filter(a => a.status === "proposed").length,
    changes: activeArtifacts.filter(a => a.status === "changes").length,
    rejected: activeArtifacts.filter(a => a.status === "rejected").length,
  };
  const catalogStats = {
    total: artifacts.length,
    approved: artifacts.filter(a => a.status === "approved").length,
    proposed: artifacts.filter(a => a.status === "proposed").length,
    changes: artifacts.filter(a => a.status === "changes").length,
    rejected: artifacts.filter(a => a.status === "rejected").length,
  };

  useEffectXS(() => {
    try {
      const key = new URLSearchParams(location.search).get("artifact");
      if (!key || !artifacts.length) return;
      const match = artifacts.find(a => (a.canonical_key || a.id) === key);
      if (!match) return;
      setActive(match.type || "ObjectType");
      setStatusView("all");
      setSelectedId(match.canonical_key || match.id);
    } catch {}
  }, [artifacts.map(a => a.canonical_key || a.id).join("|")]);

  function ontologyHref(key) {
    return `/?screen=ontology&tenant=${encodeURIComponent(tenantId)}&artifact=${encodeURIComponent(key || "")}`;
  }

  function reasoningHref(key) {
    const qs = new URLSearchParams({ screen: "reasoning", tenant: tenantId });
    if (key) qs.set("ontology_basis", key);
    return "/?" + qs.toString();
  }

  async function reviewArtifact(action) {
    if (!canonicalKey) return;
    const reason = reviewReason.trim();
    if ((action === "approve" || action === "reject") && !reason) {
      setReviewMsg({ kind: "err", msg: "Decision rationale is required for approve / reject." });
      return;
    }
    try {
      await window.AL_API.reviewAction(
        canonicalKey,
        action,
        { reason, reviewer: "M. Aoki" },
        tenantId,
      );
      setReviewReason("");
      setReviewMsg({ kind: "ok", msg: `Ontology artifact ${action} recorded.` });
      setDetailMode("review");
      if (action === "approve") setStatusView("all");
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (e) {
      setReviewMsg({ kind: "err", msg: e.message || String(e) });
    }
  }

  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className={"tab" + (active === "ObjectType" ? " active" : "")} onClick={() => setActive("ObjectType")}>Object Types <span className="ct">{grouped.ObjectType.length}</span></div>
          <div className={"tab" + (active === "LinkType" ? " active" : "")} onClick={() => setActive("LinkType")}>Link Types <span className="ct">{grouped.LinkType.length}</span></div>
          <div className={"tab" + (active === "Property" ? " active" : "")} onClick={() => setActive("Property")}>Properties <span className="ct">{grouped.Property.length}</span></div>
          {grouped.Action.length > 0 && <div className={"tab" + (active === "Action" ? " active" : "")} onClick={() => setActive("Action")}>Actions <span className="ct">{grouped.Action.length}</span></div>}
        </div>
        <div className="spacer" />
        {isMock  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Mock fallback</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />Stale · last fetch failed</span>}
        {listQ.loading && listQ.data && <span className="pill"><span className="dot" />Refreshing…</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ Refresh</button>
        <button className="tool">⤓ Export schema</button>
      </div>

      <div className="ontology-cols" style={{ flex: 1, minHeight: 0 }}>
        {/* catalog */}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Ontology Catalog · {active}s</div>
            <input className="input" value={search} onChange={e => setSearch(e.target.value)}
                   placeholder="filter by name, key, source…" style={{ marginTop: 8 }} />
            <div className="chip-row" style={{ marginTop: 10 }}>
              <Chip active={statusView === "approved"} onClick={() => setStatusView("approved")} count={stats.approved}>Canonical</Chip>
              <Chip active={statusView === "proposed"} onClick={() => setStatusView("proposed")} count={stats.proposed}>Proposed</Chip>
              <Chip active={statusView === "changes"} onClick={() => setStatusView("changes")} count={stats.changes}>Changes</Chip>
              <Chip active={statusView === "rejected"} onClick={() => setStatusView("rejected")} count={stats.rejected}>Rejected</Chip>
              <Chip active={statusView === "all"} onClick={() => setStatusView("all")} count={stats.total}>All</Chip>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 10 }}>
              <MiniMetric label="Total" value={stats.total} />
              <MiniMetric label="Canonical" value={stats.approved} tone="approved" />
              <MiniMetric label="Review" value={stats.proposed + stats.changes} tone="changes" />
              <MiniMetric label="Rejected" value={stats.rejected} tone="rejected" />
            </div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            <ApiStatus q={listQ} what="ontology artifacts" />
            <div className="artifact-list">
              {filtered.length === 0 && (
                <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center" }}>
                  No ontology artifacts match this filter.
                </div>
              )}
              {filtered.map(a => {
                const aid = a.id || a.canonical_key;
                return (
                <div key={aid}
                     className={"artifact-row " + a.status + (aid === selectedId ? " selected" : "")}
                     onClick={() => { setSelectedId(aid); setDetailMode("source"); }}>
                  <div className="ar-bar" />
                  <div className="ar-main">
                    <div className="ar-top">
                      <span className="type">{typeShort(a.type)}</span>
                      <span>·</span>
                      <span className="key">{aid}</span>
                    </div>
                    <div className="ar-title">{a.title}</div>
                    <div className="ar-meta">
                      <span>v{a.version}</span>
                      <span>{a.agent}</span>
                      <span>conf {Math.round((a.confidence || 0) * 100)}%</span>
                    </div>
                  </div>
                  <div className="ar-right">{a.status}</div>
                </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* schema + governance detail */}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          {!selected ? (
            <div style={{ flex: 1, display: "grid", placeItems: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              Select an ontology object to inspect schema, source, review, and usage.
            </div>
          ) : (
            <>
              <div className="art-header">
                <div className="crumb">
                  <span className="type">{selected.type}</span>
                  <span className="sep">/</span>
                  <span>{canonicalKey}</span>
                  <span className="sep">/</span>
                  <span>v{selected.version}</span>
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                    <Pill kind={selected.status}>{selected.status}</Pill>
                    <Pill kind="accent">conf {Math.round((selected.confidence || 0) * 100)}%</Pill>
                  </span>
                </div>
                <h1>{selected.title}</h1>
                <p className="desc">{selected.desc || "No description recorded."}</p>
                <div className="row">
                  <div className="stat">
                    <span className="label">Source agent</span>
                    <span className="val mono">{selected.agent || "unknown"}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Source evidence</span>
                    <span className="val mono">{sourceRefs.length + evidence.length}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Review events</span>
                    <span className="val mono">{audit.length}</span>
                  </div>
                  <div className="stat">
                    <span className="label">Canonical graph use</span>
                    <span className="val" style={{ color: selected.status === "approved" ? "var(--approved)" : "var(--changes)" }}>
                      {selected.status === "approved" ? "eligible" : "blocked"}
                    </span>
                  </div>
                </div>
                <OntologyReviewControls
                  selected={selected}
                  reason={reviewReason}
                  setReason={setReviewReason}
                  msg={reviewMsg}
                  onAction={reviewArtifact}
                />
              </div>

              <div className="subbar" style={{ background: "var(--bg-1)" }}>
                <div className="tabs">
                  <div className={"tab" + (detailMode === "source" ? " active" : "")} onClick={() => setDetailMode("source")}>Source &amp; Schema <span className="ct">{sourceRefs.length + evidence.length}</span></div>
                  <div className={"tab" + (detailMode === "review" ? " active" : "")} onClick={() => setDetailMode("review")}>Review history <span className="ct">{audit.length}</span></div>
                  <div className={"tab" + (detailMode === "governance" ? " active" : "")} onClick={() => setDetailMode("governance")}>Governance &amp; Impact</div>
                </div>
                <div className="spacer" />
                <a className="tool" href={ontologyHref(canonicalKey)}>Permalink</a>
              </div>

              <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
                {detailMode === "source" && (
                  <>
                    <Panel eyebrow="Canonical schema" title="Definition payload" count={`v${selected.version}`} style={{ marginBottom: 16 }}>
                      <JsonView data={selected.payload || {}} />
                    </Panel>
                    <Panel eyebrow="Source schema" title="Field properties and mapping" count={selected.sourceSchema?.schema_source || "schema"} style={{ marginBottom: 16 }}>
                      <FieldPropertiesTable schema={selected.sourceSchema || {}} />
                      <JsonView data={selected.sourceSchema || {}} />
                    </Panel>
                    <Panel eyebrow="Raw source" title="Source refs and evidence" count={`${sourceRefs.length + evidence.length} refs`} nopad style={{ marginBottom: 16 }}>
                      <SourceList sourceRefs={sourceRefs} evidence={evidence} />
                    </Panel>
                    {(selected.webEnrichment || []).length > 0 && (
                      <Panel eyebrow="Web enrichment" title="External evidence proposals" count={`${selected.webEnrichment.length} drafts`} nopad style={{ marginBottom: 16 }}>
                        <WebEnrichmentList proposals={selected.webEnrichment || []} />
                      </Panel>
                    )}
                    <Panel eyebrow="Schema map" title="Tenant ontology structure">
                      <SchemaDiagram artifacts={artifacts} selectedKey={canonicalKey} onSelect={setSelectedId} />
                    </Panel>
                  </>
                )}

                {detailMode === "review" && (
                  <Panel eyebrow="Review history" title="Decisions and rationale" count={`${audit.length} events`} nopad>
                    <ReviewTimeline audit={audit} selected={selected} />
                  </Panel>
                )}

                {detailMode === "governance" && (
                  <Panel eyebrow="Governance & Impact" title="Canonical readiness and downstream usage" count={(selected.usedBy || []).length || (canonicalKey ? "basis" : null)}>
                    <div style={{ display: "grid", gap: 12 }}>
                      <GovernanceSummary selected={selected} tenant={tenant} tenantId={tenantId} />
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <a className="btn" href={reasoningHref(canonicalKey)}>Open reasoning cases using this basis</a>
                        {canonicalKey && canonicalKey.startsWith("object:") && (
                          <a className="btn ghost" href={`/?screen=graph&tenant=${encodeURIComponent(tenantId)}&artifact=${encodeURIComponent(canonicalKey)}`}>Open graph context</a>
                        )}
                      </div>
                      {(selected.usedBy || []).length > 0 && (
                        <div className="evidence-list">
                          {(selected.usedBy || []).map((u, i) => (
                            <div key={i} className="evidence-item fact">
                              <div className="v-bar" />
                              <div className="kind">{u.kind}</div>
                              <div className="body-x">
                                <div className="title">{u.label}</div>
                                <div className="src">{u.summary}</div>
                              </div>
                              <div className="conf-side">
                                <a className="btn xs" href={u.href || reasoningHref(canonicalKey)}>Open</a>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      <dl className="kv">
                        <dt>Canonical key</dt><dd>{canonicalKey}</dd>
                        <dt>Last decision</dt><dd>{audit[0]?.act || audit[0]?.decision || "none"}</dd>
                        <dt>Blocking issue</dt><dd>{selected.status === "approved" ? "none" : "approval required before canonical graph use"}</dd>
                        <dt>Canonical write boundary</dt><dd>Only approved ontology artifacts can change canonical graph schema.</dd>
                      </dl>
                    </div>
                  </Panel>
                )}
              </div>
            </>
          )}
        </div>

        {/* governance summary */}
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Governance & Impact</div>
            <div style={{ marginTop: 4, fontSize: 13, color: "var(--text)" }}>Canonical state and downstream usage</div>
          </div>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", overflow: "auto" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Status distribution</div>
            <div className="hbar"><span className="lbl">approved</span><span className="track"><i style={{ width: pct(catalogStats.approved, catalogStats.total) + "%" }} /></span><span className="num">{catalogStats.approved}</span></div>
            <div className="hbar"><span className="lbl">proposed</span><span className="track"><i style={{ width: pct(catalogStats.proposed, catalogStats.total) + "%" }} /></span><span className="num">{catalogStats.proposed}</span></div>
            <div className="hbar"><span className="lbl">needs changes</span><span className="track"><i style={{ width: pct(catalogStats.changes, catalogStats.total) + "%" }} /></span><span className="num">{catalogStats.changes}</span></div>
            <div className="hbar"><span className="lbl">rejected</span><span className="track"><i style={{ width: pct(catalogStats.rejected, catalogStats.total) + "%" }} /></span><span className="num">{catalogStats.rejected}</span></div>

            <div className="eyebrow" style={{ marginBottom: 8, marginTop: 18 }}>Selected readiness</div>
            {selected ? (
              <GovernanceSummary selected={selected} tenant={tenant} tenantId={tenantId} compact />
            ) : (
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>No artifact selected.</div>
            )}

            <div className="eyebrow" style={{ marginBottom: 8, marginTop: 18 }}>Boundary checks</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: "var(--font-mono)", fontSize: 11 }}>
              <div style={{ display: "flex", gap: 8, color: "var(--approved)" }}><span>●</span><span>Ontology owns source/schema/review/canonical state</span></div>
              <div style={{ display: "flex", gap: 8, color: "var(--changes)" }}><span>●</span><span>Reasoning may cite this page as basis only</span></div>
              <div style={{ display: "flex", gap: 8, color: "var(--muted)" }}><span>●</span><span>Workspace remains a lightweight Case inbox</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function typeShort(type) {
  return type === "ObjectType" ? "OBJ" : type === "LinkType" ? "LINK" : type === "Property" ? "PROP" : "ACT";
}

function pct(n, total) {
  if (!total) return 0;
  return Math.max(4, Math.round((n / total) * 100));
}

function MiniMetric({ label, value, tone }) {
  return (
    <div style={{ border: "1px solid var(--line)", padding: "6px 8px", background: "var(--bg-1)" }}>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--dim)", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</div>
      <div style={{ marginTop: 2, fontFamily: "var(--font-mono)", fontSize: 15, color: tone ? `var(--${tone})` : "var(--text)" }}>{value}</div>
    </div>
  );
}

function OntologyReviewControls({ selected, reason, setReason, msg, onAction }) {
  if (!selected) return null;
  const status = (selected.status || "").toLowerCase();
  const isCanonical = status === "approved";
  const canDecide = ["proposed", "changes", "draft"].includes(status);
  return (
    <div style={{
      marginTop: 12,
      padding: 12,
      border: "1px solid var(--line)",
      background: isCanonical ? "var(--bg-1)" : "var(--accent-bg)",
      display: "grid",
      gap: 10,
    }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Ontology review gate</div>
          <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.45 }}>
            {canDecide
              ? "Review proposed ontology artifacts here before they become eligible for canonical graph use."
              : isCanonical
              ? "This artifact is canonical. Record a comment here if the review rationale needs more context."
              : "This artifact is not in an active review state. Record a comment here; reopen decisions should happen through a new proposal."}
          </div>
        </div>
        <span style={{ marginLeft: "auto" }}><Pill kind={selected.status}>{selected.status}</Pill></span>
      </div>
      <input
        className="reason-input"
        value={reason}
        onChange={e => setReason(e.target.value)}
        placeholder="Decision rationale (required for approve / reject)..."
      />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn approve" onClick={() => onAction("approve")} disabled={!canDecide}>Approve artifact</button>
        <button className="btn changes" onClick={() => onAction("needs-changes")} disabled={!canDecide}>Needs changes</button>
        <button className="btn reject" onClick={() => onAction("reject")} disabled={!canDecide}>Reject</button>
        <button className="btn ghost" onClick={() => onAction("comment")}>Comment</button>
        {msg && (
          <span style={{
            marginLeft: "auto",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: msg.kind === "ok" ? "var(--approved)" : "var(--rejected)",
          }}>
            {msg.msg}
          </span>
        )}
      </div>
    </div>
  );
}

function SourceList({ sourceRefs, evidence }) {
  const rows = [
    ...sourceRefs.map(src => ({ kind: "source_ref", title: src, src, conf: null, rawPayload: null })),
    ...evidence.map(e => ({ kind: e.kind, title: e.title, src: e.src, conf: e.conf, rawPayload: e.rawPayload, contentHash: e.contentHash })),
  ];
  if (rows.length === 0) {
    return <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>No source references or evidence recorded.</div>;
  }
  return (
    <div className="evidence-list">
      {rows.map((e, i) => (
        <div key={i} className={"evidence-item " + (e.kind || "fact")}>
          <div className="v-bar" />
          <div className="kind">{e.kind || "source"}</div>
          <div className="body-x">
            <div className="title">{e.title}</div>
            <div className="src">{e.src}{e.contentHash ? " · " + e.contentHash : ""}</div>
            {e.rawPayload && Object.keys(e.rawPayload || {}).length > 0 && <JsonView data={e.rawPayload} />}
          </div>
          <div className="conf-side">
            {e.conf != null ? <><span style={{ color: "var(--text)" }}>{Math.round(e.conf * 100)}%</span><span style={{ color: "var(--dim)", fontSize: 9, marginTop: 2 }}>confidence</span></> : <span className="faint">—</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function WebEnrichmentList({ proposals }) {
  if (!proposals.length) {
    return <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>No web enrichment proposals recorded.</div>;
  }
  return (
    <div className="evidence-list">
      {proposals.map((p, i) => {
        const raw = p.raw_payload || {};
        const source = raw.source || {};
        const fields = raw.field_provenance || [];
        return (
          <div key={p.proposal_key || i} className="evidence-item hypothesis">
            <div className="v-bar" />
            <div className="kind">web proposal</div>
            <div className="body-x">
              <div className="title">{p.source_title || p.proposal_key}</div>
              <div className="src">{p.source_url}</div>
              <div style={{ marginTop: 6, color: "var(--text-dim)" }}>{p.summary}</div>
              <dl className="kv" style={{ marginTop: 10 }}>
                <dt>Proposal</dt><dd>{p.proposal_key}</dd>
                <dt>Target</dt><dd>{p.target_artifact_key}</dd>
                <dt>Query</dt><dd>{source.search_query || "unknown"}</dd>
                <dt>Retrieved</dt><dd>{source.retrieved_at || p.created_at || "unknown"}</dd>
                <dt>Robots risk</dt><dd>{source.robots_risk || "not recorded"}</dd>
                <dt>License risk</dt><dd>{source.license_risk || "not recorded"}</dd>
                <dt>Write boundary</dt><dd>{raw.governance?.canonical_writes || "disabled"} canonical · {raw.governance?.graph_writes || "disabled"} graph</dd>
              </dl>
              {fields.length > 0 && (
                <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                  field provenance: {fields.map(f => f.artifact_field).join(", ")}
                </div>
              )}
            </div>
            <div className="conf-side">
              <span style={{ color: "var(--text)" }}>{Math.round((p.confidence || 0) * 100)}%</span>
              <span style={{ color: "var(--dim)", fontSize: 9, marginTop: 2 }}>confidence</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FieldPropertiesTable({ schema }) {
  const fields =
    schema.field_properties ||
    schema.fields ||
    [];
  if (!fields.length) {
    return (
      <div style={{ padding: "0 0 10px", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
        No field properties available for this source schema.
      </div>
    );
  }
  return (
    <div style={{ overflow: "auto", marginBottom: 12, border: "1px solid var(--line-soft)" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-mono)", fontSize: 11 }}>
        <thead>
          <tr style={{ background: "var(--bg-2)", color: "var(--muted)", textAlign: "left" }}>
            {["field", "type", "nullable", "key role", "default", "comment", "source"].map(h => (
              <th key={h} style={{ padding: "7px 8px", borderBottom: "1px solid var(--line-soft)", fontWeight: 500 }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fields.map((f, i) => (
            <tr key={(f.qualified_name || f.name || i)} style={{ borderTop: i ? "1px solid var(--line-soft)" : 0 }}>
              <td style={{ padding: "7px 8px", color: "var(--text)" }}>{f.qualified_name || f.name}</td>
              <td style={{ padding: "7px 8px", color: "var(--text-dim)" }}>{f.column_type || f.data_type || "unknown"}</td>
              <td style={{ padding: "7px 8px", color: f.nullable === false ? "var(--approved)" : "var(--muted)" }}>{String(f.nullable)}</td>
              <td style={{ padding: "7px 8px", color: f.key_role === "primary_key" ? "var(--approved)" : f.key_role === "relationship_reference" ? "var(--changes)" : "var(--text-dim)" }}>{f.key_role || "unknown"}</td>
              <td style={{ padding: "7px 8px", color: "var(--text-dim)" }}>{f.default == null ? "null" : String(f.default)}</td>
              <td style={{ padding: "7px 8px", color: "var(--text-dim)" }}>{f.comment || "unknown"}</td>
              <td style={{ padding: "7px 8px", color: "var(--muted)" }}>{schema.schema_source || "unknown"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GovernanceSummary({ selected, tenant, tenantId, compact }) {
  const status = selected.canonical?.status || selected.status;
  const version = selected.canonical?.version || selected.version;
  const eligible = selected.canonical?.graph_ingestion_eligible ?? selected.status === "approved";
  const usedBy = (selected.usedBy || []).length;
  const items = [
    { label: "Canonical state", value: `${status || "unknown"} v${version || "?"}`, tone: status === "approved" ? "approved" : "changes" },
    { label: "Graph use", value: eligible ? "eligible" : "blocked", tone: eligible ? "approved" : "changes" },
    { label: "Used by", value: `${usedBy} flows`, tone: usedBy ? "accent" : null },
  ];
  return (
    <div style={{ display: "grid", gap: compact ? 8 : 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: compact ? "1fr" : "repeat(3, minmax(0, 1fr))", gap: 8 }}>
        {items.map(item => (
          <div key={item.label} style={{ border: "1px solid var(--line-soft)", background: "var(--bg-2)", padding: "10px 12px" }}>
            <div className="eyebrow" style={{ marginBottom: 4 }}>{item.label}</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: item.tone ? `var(--${item.tone})` : "var(--text)" }}>{item.value}</div>
          </div>
        ))}
      </div>
      {!compact && (
        <dl className="kv">
          <dt>Tenant</dt><dd>{selected.canonical?.tenant_id || tenantId}</dd>
          <dt>Graph database</dt><dd>{selected.canonical?.graph_database || tenant?.graph || "—"}</dd>
          <dt>Blocking reason</dt><dd>{eligible ? "none" : "not approved for canonical graph ingestion"}</dd>
        </dl>
      )}
    </div>
  );
}

function ReviewTimeline({ audit, selected }) {
  if (!audit.length) {
    return (
      <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
        No review events recorded. Current status: {selected.status} · v{selected.version}.
      </div>
    );
  }
  return (
    <div className="audit-list">
      {audit.map((a, i) => (
        <div key={i} className="audit-item">
          <span className="ts">{a.created || a.ts}</span>
          <span className={"act " + a.act}>{a.act}</span>
          <span className="det">
            <span className="who">{a.who}</span>
            {a.beforeStatus || a.afterStatus ? ` · ${a.beforeStatus || "—"} -> ${a.afterStatus || "—"}` : ""}
            {a.beforeVersion || a.afterVersion ? ` · v${a.beforeVersion || "—"} -> v${a.afterVersion || "—"}` : ""}
            {a.detail ? ` · ${a.detail}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

function SchemaDiagram({ artifacts = [], selectedKey, onSelect }) {
  const objects = artifacts.filter(a => a.type === "ObjectType").slice(0, 8);
  const links = artifacts.filter(a => a.type === "LinkType").slice(0, 12);
  const boxes = objects.length ? objects.map((a, i) => ({
    id: a.title || a.key || a.canonical_key,
    canonical_key: a.canonical_key || a.id,
    x: 80 + (i % 3) * 280,
    y: 50 + Math.floor(i / 3) * 150,
    w: 210,
    h: 104,
    status: a.status,
    props: schemaProps(a),
  })) : [
    { id: "Employee", canonical_key: "object:employee", x: 200, y: 60,  w: 200, h: 104, status: "approved", props: ["employeeID","firstName","lastName"] },
    { id: "Order", canonical_key: "object:order", x: 540, y: 60,  w: 200, h: 104, status: "approved", props: ["orderID","employeeID","customerID"] },
  ];
  const boxByName = Object.fromEntries(boxes.map(b => [b.id.toLowerCase(), b]));
  const diagramLinks = links.map(a => {
    const p = a.payload || {};
    return {
      canonical_key: a.canonical_key || a.id,
      from: String(p.source_object_name || p.source || "").toLowerCase(),
      to: String(p.target_object_name || p.target || "").toLowerCase(),
      label: a.title || a.key || a.canonical_key,
      status: a.status,
    };
  }).filter(l => boxByName[l.from] && boxByName[l.to]);

  const statusColor = {
    approved: "var(--approved)",
    proposed: "var(--proposed)",
    rejected: "var(--rejected)",
    changes:  "var(--changes)",
  };
  const m = Object.fromEntries(boxes.map(b => [b.id.toLowerCase(), b]));

  function anchor(a, b) {
    // centers
    const ax = a.x + a.w/2, ay = a.y + a.h/2;
    const bx = b.x + b.w/2, by = b.y + b.h/2;
    // pick nearest edge points
    const dx = bx - ax, dy = by - ay;
    let sx, sy, tx, ty;
    if (Math.abs(dx) > Math.abs(dy)) {
      sx = dx > 0 ? a.x + a.w : a.x;
      sy = ay;
      tx = dx > 0 ? b.x : b.x + b.w;
      ty = by;
    } else {
      sx = ax;
      sy = dy > 0 ? a.y + a.h : a.y;
      tx = bx;
      ty = dy > 0 ? b.y : b.y + b.h;
    }
    return { sx, sy, tx, ty };
  }

  return (
    <svg viewBox="0 0 940 420" style={{ width: "100%", height: "100%", maxHeight: 420 }}>
      <defs>
        <pattern id="grid-bg" width="20" height="20" patternUnits="userSpaceOnUse">
          <path d="M 20 0 L 0 0 0 20" fill="none" stroke="var(--line-soft)" strokeWidth="0.5" />
        </pattern>
        <marker id="er-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" />
        </marker>
      </defs>
      <rect width="940" height="420" fill="url(#grid-bg)" />

      {/* links */}
      {diagramLinks.map((l, i) => {
        if (l.curve === "self") {
          const b = m[l.from];
          const cx = b.x + b.w + 30;
          const cy = b.y + b.h / 2;
          return (
            <g key={i}>
              <path d={`M ${b.x + b.w} ${b.y + 30} C ${cx + 30} ${b.y + 30}, ${cx + 30} ${b.y + b.h - 30}, ${b.x + b.w} ${b.y + b.h - 30}`}
                    fill="none" stroke={statusColor[l.status]} strokeDasharray={l.status === "proposed" ? "4 3" : ""} strokeWidth="1.4" markerEnd="url(#er-arrow)" />
              <text x={cx + 36} y={cy + 4} fontSize="10" fontFamily="var(--font-mono)"
                    fill={statusColor[l.status]}
                    style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
                {l.label}
              </text>
            </g>
          );
        }
        const a = m[l.from], b = m[l.to];
        const { sx, sy, tx, ty } = anchor(a, b);
        const mx = (sx + tx) / 2, my = (sy + ty) / 2;
        return (
          <g key={i}>
            <line x1={sx} y1={sy} x2={tx} y2={ty}
                  stroke={statusColor[l.status]}
                  strokeDasharray={l.status === "proposed" ? "4 3" : l.status === "rejected" ? "2 4" : ""}
                  strokeWidth="1.4" markerEnd="url(#er-arrow)" />
            <rect x={mx - 38} y={my - 9} width="76" height="18" fill="var(--bg-1)" stroke={statusColor[l.status]} />
              <text x={mx} y={my + 4} textAnchor="middle" fontSize="10" fontFamily="var(--font-mono)"
                  fill={statusColor[l.status]}
                  style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
              {String(l.label || "").slice(0, 18)}
            </text>
          </g>
        );
      })}

      {/* boxes */}
      {boxes.map(b => (
        <g key={b.id} onClick={() => onSelect && onSelect(b.canonical_key)} style={{ cursor: onSelect ? "pointer" : "default" }}>
          <rect x={b.x} y={b.y} width={b.w} height={b.h}
                fill="var(--bg-2)" stroke={selectedKey === b.canonical_key ? "var(--accent)" : statusColor[b.status]} strokeWidth={selectedKey === b.canonical_key ? "2.5" : "1.5"} />
          <rect x={b.x} y={b.y} width={b.w} height={26} fill="var(--bg-3)" stroke={statusColor[b.status]} strokeWidth="1.5" />
          <text x={b.x + 10} y={b.y + 17} fontSize="12" fontFamily="var(--font-sans)" fill="var(--text)" fontWeight="600">{b.id}</text>
          <text x={b.x + b.w - 10} y={b.y + 17} textAnchor="end" fontSize="9" fontFamily="var(--font-mono)"
                fill={statusColor[b.status]}
                style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
            {b.status}
          </text>
          {b.props.map((p, j) => (
            <text key={j} x={b.x + 10} y={b.y + 44 + j * 14}
                  fontSize="11" fontFamily="var(--font-mono)" fill="var(--text-dim)">
              {p}
            </text>
          ))}
        </g>
      ))}
    </svg>
  );
}

function schemaProps(a) {
  const p = a.payload || {};
  const raw = p.properties || p.keys || p.columns || [];
  if (Array.isArray(raw) && raw.length) return raw.slice(0, 5).map(x => typeof x === "string" ? x : (x.name || x.key || JSON.stringify(x)));
  return Object.keys(p).filter(k => !["description", "source_object_name", "target_object_name", "link_type"].includes(k)).slice(0, 5);
}

/* ---------------- QUALITY ---------------- */
function Quality({ data }) {
  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className="tab active">Attention Queue <span className="ct">{data.ATTENTION.length}</span></div>
          <div className="tab">Sandbox Gaps <span className="ct">4</span></div>
          <div className="tab">Agents <span className="ct">5</span></div>
          <div className="tab">Trends</div>
        </div>
        <div className="spacer" />
        <button className="tool">⤓ Triage report</button>
      </div>

      <div className="metric-grid">
        <div className="metric-card">
          <div className="label">Draft findings</div>
          <div className="val">14</div>
          <div className="sub">awaiting review</div>
          <Sparkline data={data.SPARK} />
        </div>
        <div className="metric-card">
          <div className="label">Low confidence</div>
          <div className="val warn">6</div>
          <div className="sub">below 0.65</div>
          <Sparkline data={[2,3,2,4,3,5,4,6,5,7,6,6]} color="oklch(0.78 0.14 75)" />
        </div>
        <div className="metric-card">
          <div className="label">Blocked runs</div>
          <div className="val crit">2</div>
          <div className="sub">approved-only / gaps</div>
          <Sparkline data={[1,1,2,1,2,3,2,1,2,3,2,2]} color="oklch(0.66 0.18 25)" />
        </div>
        <div className="metric-card">
          <div className="label">Agent policy</div>
          <div className="val warn">1</div>
          <div className="sub">violation flagged</div>
          <Sparkline data={[0,0,0,1,1,2,1,1,1,1,1,1]} color="oklch(0.78 0.14 75)" />
        </div>
        <div className="metric-card">
          <div className="label">Sandbox gate</div>
          <div className="val ok">OPEN</div>
          <div className="sub">negative control</div>
          <svg className="spark" viewBox="0 0 60 22"><line x1="0" y1="11" x2="60" y2="11" stroke="oklch(0.74 0.13 165)" strokeDasharray="3 2" /></svg>
        </div>
      </div>

      <div className="quality-grid">
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)", display: "flex", alignItems: "center" }}>
            <div className="eyebrow accent">Attention queue</div>
            <span className="spacer" />
            <div className="chip-row">
              <Chip active count={3}>Crit</Chip>
              <Chip active count={3}>Warn</Chip>
              <Chip count={1}>Info</Chip>
            </div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {data.ATTENTION.map((a, i) => (
              <div key={i} className={"attention-row " + a.sev}>
                <div className="a-bar" />
                <div className="reason">{a.reason}</div>
                <div className="body-x">
                  <div className="title">{a.title}</div>
                  <div className="meta">{a.meta}</div>
                </div>
                <div className="conf">{a.conf}</div>
                <div className="age">{a.age}</div>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">Approved-only gate · missing artifacts</div>
            <div style={{ marginTop: 4, fontSize: 12, color: "var(--muted)" }}>Templates that cannot run because dependent types are not yet approved</div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {[
              { tpl: "concentration-risk",  needs: "LinkType.ReportsTo",         status: "proposed" },
              { tpl: "customer-segmentation",needs: "ObjectType.Customer",       status: "proposed" },
              { tpl: "territorial-analysis", needs: "ObjectType.Region",         status: "rejected" },
              { tpl: "value-banded-orders",  needs: "Property.Order.value_band", status: "changes" },
            ].map((row, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "3px 1fr auto", borderBottom: "1px solid var(--line-soft)", alignItems: "stretch" }}>
                <div style={{ background: row.status === "rejected" ? "var(--rejected)" : row.status === "changes" ? "var(--changes)" : "var(--proposed)" }} />
                <div style={{ padding: "10px 14px" }}>
                  <div style={{ fontSize: 13, color: "var(--text)" }}>{row.tpl}</div>
                  <div style={{ marginTop: 3, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)" }}>
                    needs <span style={{ color: "var(--accent)" }}>{row.needs}</span>
                  </div>
                </div>
                <div style={{ padding: "10px 14px", display: "flex", alignItems: "center" }}>
                  <Pill kind={row.status}>{row.status}</Pill>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- RUNTIME ---------------- */
function Runtime({ data }) {
  const [sel, setSel] = useStateXS(data.RUNTIMES[0]);
  return (
    <div className="canvas">
      <div className="subbar">
        <div className="tabs">
          <div className="tab active">CLI Agents <span className="ct">{data.RUNTIMES.length}</span></div>
          <div className="tab">Policies <span className="ct">3</span></div>
          <div className="tab">Audit Log</div>
          <div className="tab">Tenants</div>
        </div>
        <div className="spacer" />
        <button className="tool">⟲ Refresh</button>
        <button className="tool primary">+ Register agent</button>
      </div>

      <div className="wb">
        {/* list */}
        <div className="col" style={{ borderRight: "1px solid var(--line)" }}>
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">AI Runtime</div>
            <div style={{ fontSize: 13, marginTop: 4, color: "var(--text)" }}>Allowlisted CLI agents</div>
            <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--changes)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              ⚠ default-deny · secrets not forwarded
            </div>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {data.RUNTIMES.map(r => (
              <div key={r.id} className={"runtime-row " + r.status + (sel.id === r.id ? " selected" : "")} onClick={() => setSel(r)}>
                <div className="r-bar" />
                <div className="r-body">
                  <div className="r-name">
                    <strong>{r.name}</strong>
                    {!r.enabled && <span className="pill" style={{ fontSize: 9 }}>disabled</span>}
                  </div>
                  <div className="r-meta">
                    <span className={r.status}>● {r.status === "ok" ? "healthy" : r.status === "warn" ? "degraded" : "down"}</span>
                    <span> · last {r.lastRun}</span>
                    <span> · {r.runs24h} runs / 24h</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* detail */}
        <div className="col" style={{ display: "flex", flexDirection: "column" }}>
          <div className="art-header">
            <div className="crumb">
              <span className="type">CLI Agent</span>
              <span className="sep">/</span>
              <span>{sel.id}</span>
              <span className="sep">·</span>
              <span>template {sel.template}</span>
              <span style={{ marginLeft: "auto" }}>
                <Pill kind={sel.status === "ok" ? "approved" : sel.status === "warn" ? "changes" : "rejected"}>
                  {sel.status === "ok" ? "healthy" : sel.status === "warn" ? "degraded" : "down"}
                </Pill>
              </span>
            </div>
            <h1>{sel.name}</h1>
            <p className="desc">
              {sel.name === "calendar-ingest" ? "Calendar-derived agent that proposes soft links (MentorOf, CoWorkerOf). Runs in sandbox until evidence quality clears the 0.65 threshold." :
               sel.name === "tableau.exporter" ? "Outbound exporter for approved findings. Currently disabled — credentials rotation in progress." :
               "Generative reasoning agent. Allowed templates execute against approved-only scope by default."}
            </p>
            <div className="row">
              <div className="stat"><span className="label">Binary</span><span className="val mono" style={{ fontSize: 12 }}>{sel.binary}</span></div>
              <div className="stat"><span className="label">Template</span><span className="val mono">{sel.template}</span></div>
              <div className="stat"><span className="label">Runs / 24h</span><span className="val mono">{sel.runs24h}</span></div>
              <div className="stat"><span className="label">Last invocation</span><span className="val mono">{sel.lastRun}</span></div>
              <div className="stat"><span className="label">Enabled</span><span className="val mono" style={{ color: sel.enabled ? "var(--approved)" : "var(--rejected)" }}>{sel.enabled ? "true" : "false"}</span></div>
            </div>
          </div>

          <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)", display: "flex", flexDirection: "column", gap: 16 }}>
            <Panel eyebrow="Health" title="Status checks" count="3 checks">
              <div style={{ display: "flex", flexDirection: "column" }}>
                {[
                  { ck: "binary present",        ok: true,  detail: "/usr/local/bin/anthropic-cli · 0o755" },
                  { ck: "policy resolves",       ok: true,  detail: "default_cli_policy v4 · 11 directives" },
                  { ck: "smoke-run (safe demo)", ok: sel.status === "ok", detail: sel.status === "ok" ? "round-trip 42ms · ✓ allowlisted template" : "timeout · last 03:14" },
                ].map((c, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "20px 200px 1fr", padding: "8px 0", borderBottom: i < 2 ? "1px solid var(--line-soft)" : "none", alignItems: "center", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    <span style={{ color: c.ok ? "var(--approved)" : "var(--rejected)" }}>{c.ok ? "✓" : "✕"}</span>
                    <span style={{ color: "var(--text-dim)" }}>{c.ck}</span>
                    <span style={{ color: "var(--dim)" }}>{c.detail}</span>
                  </div>
                ))}
                <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
                  <button className="btn">Run health check</button>
                  <button className="btn">Run readiness check</button>
                  <button className="btn">Safe demo</button>
                </div>
              </div>
            </Panel>

            <Panel eyebrow="Policy" title={sel.template} count="11 directives">
              <pre className="code">{`{
  "id":           "default_cli_policy",
  "version":      4,
  "default":      "deny",
  "secrets":      "never_forwarded",
  "templates":    ["safe_demo", "evidence_pack", "scoped_question"],
  "tenants":      ["acme-prod", "acme-staging"],
  "max_runs_5m":  20,
  "max_runtime_s":120,
  "evidence_required": true,
  "approved_only": true,
  "audit_log":    "/var/log/aletheia/agent.log"
}`}</pre>
            </Panel>

            <Panel eyebrow="Runs" title="Recent invocations" count={`${sel.runs24h} in 24h`} nopad>
              <div className="audit-list">
                {["02:11 invoked scoped_question — 198 tok in · 412 tok out · 38ms",
                  "02:09 invoked evidence_pack — 412 tok in · 1.1k tok out · 412ms",
                  "01:58 invoked scoped_question — 220 tok in · 388 tok out · 41ms",
                  "01:44 ⚠ template denied — caller=portal · template=raw_sql · 0ms",
                  "01:30 invoked safe_demo — 88 tok in · 142 tok out · 22ms"].map((line, i) => (
                  <div key={i} className="audit-item">
                    <span className="ts">{line.split(" ")[0]}</span>
                    <span className={"act " + (line.includes("denied") ? "rejected" : "approved")}>{line.includes("denied") ? "denied" : "ok"}</span>
                    <span className="det" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{line.slice(6)}</span>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </div>

        {/* inspector */}
        <div className="col inspector">
          <div className="section">
            <div className="section-head"><span>Tenant scope</span></div>
            <div className="section-body">
              <dl className="kv">
                <dt>Tenants</dt><dd>acme-prod, acme-staging</dd>
                <dt>Graphs</dt><dd>neo4j://acme-prod</dd>
                <dt>Read</dt><dd style={{ color: "var(--approved)" }}>approved-only</dd>
                <dt>Write</dt><dd style={{ color: "var(--changes)" }}>proposals → review</dd>
              </dl>
            </div>
          </div>
          <div className="section">
            <div className="section-head"><span>Token usage · 24h</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">input</span><span className="track"><i style={{ width: "62%" }} /></span><span className="num">62k</span></div>
              <div className="hbar"><span className="lbl">output</span><span className="track"><i style={{ width: "38%" }} /></span><span className="num">38k</span></div>
              <div className="hbar"><span className="lbl">cache</span><span className="track"><i style={{ width: "84%" }} /></span><span className="num">84%</span></div>
              <Sparkline data={[3,4,3,5,4,6,5,7,6,8,7,9,8,10,9,11,10,12,11,14]} width={260} height={50} />
            </div>
          </div>
          <div className="section">
            <div className="section-head"><span>Rate budget</span></div>
            <div className="section-body">
              <div className="hbar"><span className="lbl">5m window</span><span className="track"><i style={{ width: "34%" }} /></span><span className="num">7/20</span></div>
              <div className="hbar"><span className="lbl">1h window</span><span className="track"><i style={{ width: "58%" }} /></span><span className="num">58/100</span></div>
              <div className="hbar"><span className="lbl">24h cap</span><span className="track"><i style={{ width: "31%" }} /></span><span className="num">312/1k</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Ontology, Quality, Runtime });
