/* Aletheia API client
   ------------------------------------------------------------------
   Talks to the same endpoints the existing review_workbench.py
   server exposes. Base URL is configurable + persisted to localStorage.

   Normalizes the server's snake_case artifact shape into the
   camelCase shape this prototype's components consume.
*/

(function () {
  const LS_KEY = "aletheia.api.baseUrl";
  const LS_MOCK = "aletheia.api.allowMock"; // "1" = mock fallback on, anything else = off (default)

  // ---------- base URL ----------
  function getBaseUrl() {
    try {
      const v = localStorage.getItem(LS_KEY);
      if (v) return v.replace(/\/$/, "");
    } catch {}
    return "http://localhost:8765";
  }
  function setBaseUrl(v) {
    try { localStorage.setItem(LS_KEY, (v || "").replace(/\/$/, "")); } catch {}
    window.dispatchEvent(new CustomEvent("aletheia:base-url-changed", { detail: getBaseUrl() }));
  }
  function isMockAllowed() {
    try { return localStorage.getItem(LS_MOCK) === "1"; } catch { return false; }
  }
  function setMockAllowed(v) {
    try { localStorage.setItem(LS_MOCK, v ? "1" : "0"); } catch {}
    window.dispatchEvent(new CustomEvent("aletheia:mock-toggled", { detail: v }));
  }

  // ---------- fetch helper ----------
  async function fetchJson(path, opts = {}) {
    const url = getBaseUrl() + path;
    const res = await fetch(url, {
      ...opts,
      headers: {
        "Accept": "application/json",
        ...(opts.body ? { "Content-Type": "application/json" } : {}),
        ...(opts.headers || {}),
      },
    });
    if (!res.ok) {
      let detail = "";
      try { const b = await res.json(); detail = b.error || b.message || b.detail || ""; } catch {}
      const msg = detail || `HTTP ${res.status} ${res.statusText}`;
      const err = new Error(`${msg} · ${path}`);
      err.status = res.status;
      err.path = path;
      throw err;
    }
    if (res.status === 204) return null;
    return await res.json();
  }
  function withTenantQs(path, tenant, extra = {}) {
    const qs = new URLSearchParams({ tenant: tenant || "default", ...extra });
    return path + "?" + qs.toString();
  }

  // ---------- reasoning normalizers ----------
  const EVIDENCE_KIND_MAP = {
    instance_node: "fact",
    instance_edge: "fact",
    ontology_artifact: "fact",
    aggregate: "fact",
    question_scope: "fact",
    controlled_aggregate: "fact",
    graph_node: "fact",
    graph_edge: "fact",
    hypothesis: "hypothesis",
    conflict: "conflict",
    missing: "missing",
    fact: "fact",
  };
  function normalizeEvidencePath(ep) {
    return {
      kind: EVIDENCE_KIND_MAP[ep.kind] || ep.kind || "fact",
      title: ep.label || ep.title || ep.summary || ep.description || "—",
      src: ep.source_ref || ep.source || ep.src || "",
      conf: typeof ep.confidence === "number" ? ep.confidence : (ep.conf != null ? ep.conf : null),
      url: ep.url || null,
      _raw: ep,
    };
  }
  function normalizeReasoningTask(t, extra) {
    if (!t) return t;
    const scope = t.scope || {};
    const run = (extra && extra.latest_run) || t.latest_run || null;
    const findings = (extra && extra.findings) || t.findings || [];
    const out = { ...t };
    if (!out.center_node) {
      out.center_node = scope.center_node
        || (scope.object_type && scope.instance_id ? scope.object_type + ":" + scope.instance_id : null)
        || null;
    }
    if (out.depth == null) out.depth = scope.depth || 1;
    if (out.limit == null) out.limit = scope.node_limit || scope.limit || 200;
    if (!out.source) {
      const rawSource = scope.source
        || (t.canonical_key && t.canonical_key.includes("graph-scope") ? "graph_explorer" : "manual");
      const sourceMap = { question_center: "manual", graph_explorer: "graph", manual: "manual", graph: "graph" };
      out.source = sourceMap[rawSource] || rawSource;
    }
    if (!out.name) out.name = out.question;
    if (run) {
      out.latest_run = run;
      if (!out.evidence_paths || out.evidence_paths.length === 0) {
        out.evidence_paths = (run.evidence_paths || []).map(normalizeEvidencePath);
      }
    }
    if (!out.evidence_paths) out.evidence_paths = [];
    if (findings.length > 0 && !out.finding) {
      const latest = findings[0];
      let actionText = latest.action_proposal || latest.recommended_action || null;
      if (actionText && typeof actionText === "object") {
        actionText = actionText.description || actionText.summary || JSON.stringify(actionText);
      }
      let counterText = latest.counter_evidence;
      if (Array.isArray(counterText)) {
        counterText = counterText.map(c => c.summary || c.description || JSON.stringify(c)).join(" · ");
      } else if (counterText && typeof counterText === "object") {
        counterText = counterText.summary || counterText.description || JSON.stringify(counterText);
      }
      out.finding = {
        ...latest,
        action_proposal: actionText,
        counter_evidence: counterText || null,
      };
      out.findings = findings;
    }
    if (out.finding && !out.confidence && out.finding.confidence != null) {
      out.confidence = out.finding.confidence;
    }
    return out;
  }

  // ---------- artifact normalizers ----------
  // Convert real artifact (snake_case) → the prototype's shape.
  function normalizeArtifact(a) {
    if (!a) return null;
    const typeMap = {
      object: "ObjectType", link: "LinkType", property: "Property", action: "Action",
      ObjectType: "ObjectType", LinkType: "LinkType", Property: "Property",
    };
    const statusMap = {
      approved: "approved",
      proposed: "proposed",
      needs_changes: "changes",
      rejected: "rejected",
      draft: "proposed",
    };
    return {
      id: a.canonical_key || a.id,
      canonical_key: a.canonical_key || a.id,
      type: typeMap[a.artifact_type] || a.artifact_type || "ObjectType",
      key: a.name || a.canonical_key || "",
      title: a.name || a.canonical_key || "Untitled",
      desc: a.description || "",
      status: statusMap[a.status] || a.status || "proposed",
      rawStatus: a.status,
      confidence: typeof a.confidence === "number" ? a.confidence : (a.confidence ? parseFloat(a.confidence) : 0),
      agent: a.source_agent || "unknown",
      version: a.version != null ? String(a.version) : "1",
      updated: a.updated_at || a.created_at || "",
      created: a.created_at || "",
      payload: a.payload || {},
      evidence: (a.evidence || []).map(normalizeEvidence),
      audit:    (a.reviews  || []).map(normalizeAudit),
      sourceRefs: a.source_refs || [],
      _raw: a,
    };
  }
  function normalizeEvidence(e) {
    const kindMap = { fact: "fact", hypothesis: "hypothesis", conflict: "conflict", missing: "missing" };
    return {
      kind: kindMap[e.kind] || e.kind || "fact",
      title: e.title || e.summary || e.description || "(unlabeled evidence)",
      src: e.source_ref || e.source || e.path || "",
      conf: typeof e.confidence === "number" ? e.confidence : null,
      _raw: e,
    };
  }
  function normalizeAudit(r) {
    const actMap = {
      approved: "approved", approve: "approved",
      rejected: "rejected", reject: "rejected",
      needs_changes: "changes",
      comment: "comment", commented: "comment",
      proposed: "proposed", drafted: "draft", draft: "draft",
    };
    const ts = r.created_at || r.timestamp || r.at || "";
    return {
      ts: typeof ts === "string" ? ts.slice(11, 16) || ts.slice(0, 10) : "—",
      act: actMap[r.action] || actMap[r.status] || r.action || "comment",
      who: r.reviewer || r.actor || r.source_agent || "system",
      detail: r.reason || r.note || r.comment || "",
      _raw: r,
    };
  }
  function normalizeTenant(t) {
    return {
      id: t.tenant_id || t.id,
      name: (t.display_name || t.tenant_id || "tenant") + (t.namespace ? " · " + t.namespace : ""),
      namespace: t.namespace || "",
      graph: t.graph_db || t.graph_database || t.graph || "",
      _raw: t,
    };
  }

  // ---------- endpoint wrappers ----------
  const api = {
    LS_KEY,
    getBaseUrl, setBaseUrl,
    isMockAllowed, setMockAllowed,
    fetchJson,

    async ping() {
      // not all backends have a dedicated ping — /api/tenants is cheap
      const data = await fetchJson("/api/tenants");
      return { ok: true, tenants: (data.tenants || data || []).length };
    },

    async tenants() {
      const data = await fetchJson("/api/tenants");
      return (data.tenants || data || []).map(normalizeTenant);
    },

    async artifacts(tenant, filters = {}) {
      const qs = new URLSearchParams({ tenant: tenant || "default" });
      if (filters.type) qs.set("type", filters.type);
      if (filters.status) qs.set("status", filters.status);
      if (filters.search) qs.set("q", filters.search);
      const data = await fetchJson("/api/artifacts?" + qs.toString());
      return (data.artifacts || data || []).map(normalizeArtifact);
    },

    async artifact(canonicalKey, tenant) {
      const data = await fetchJson(withTenantQs(
        `/api/artifacts/${encodeURIComponent(canonicalKey)}`, tenant));
      return normalizeArtifact(data.artifact || data);
    },

    async reviewAction(canonicalKey, action, body, tenant) {
      // action: "approve" | "reject" | "needs-changes" | "comment"
      const data = await fetchJson(withTenantQs(
        `/api/artifacts/${encodeURIComponent(canonicalKey)}/${action}`, tenant), {
        method: "POST",
        body: JSON.stringify(body || {}),
      });
      return normalizeArtifact(data.artifact || data);
    },

    async graphContext(tenant, { type, id, depth = 1, limit = 200 } = {}) {
      const data = await fetchJson(withTenantQs("/api/graph/context", tenant, {
        type: type || "Employee",
        id: id || "4",
        depth: String(depth),
        limit: String(limit),
      }));
      return data;
    },

    async overview(tenant) {
      const data = await fetchJson(withTenantQs("/api/portal/overview", tenant));
      return data;
    },

    async reasoningTasks(tenant) {
      const data = await fetchJson(withTenantQs("/api/reasoning/tasks", tenant));
      const list =
        Array.isArray(data) ? data :
        Array.isArray(data?.tasks) ? data.tasks :
        Array.isArray(data?.items) ? data.items :
        Array.isArray(data?.questions) ? data.questions :
        Array.isArray(data?.results) ? data.results :
        [];
      return list.map(t => normalizeReasoningTask(t)).sort((a, b) => {
        const au = a.updated_at || a.created_at || "";
        const bu = b.updated_at || b.created_at || "";
        if (au !== bu) return au < bu ? 1 : -1;
        return (a.canonical_key || "").localeCompare(b.canonical_key || "");
      });
    },

    async reasoningTask(canonicalKey, tenant) {
      const data = await fetchJson(withTenantQs(
        `/api/reasoning/tasks/${encodeURIComponent(canonicalKey)}`, tenant));
      const task = data.task || data;
      return normalizeReasoningTask(task, {
        latest_run: data.latest_run || task.latest_run,
        findings: data.findings || [],
      });
    },

    async runReasoning(canonicalKey, tenant) {
      const data = await fetchJson(withTenantQs(
        `/api/reasoning/tasks/${encodeURIComponent(canonicalKey)}/run`, tenant),
        { method: "POST", body: JSON.stringify({}) });
      return data;
    },

    async submitQuestion(tenant, body) {
      const b = body || {};
      const scope = b.scope || {
        center_node: b.center_node,
        depth: b.depth,
        limit: b.limit,
      };
      if (b.nonce) scope.nonce = b.nonce;
      const wrapped = { question: b.question, scope };
      const data = await fetchJson(withTenantQs("/api/reasoning/questions", tenant), {
        method: "POST",
        body: JSON.stringify(wrapped),
      });
      return normalizeReasoningTask(data.task || data, { findings: data.findings || [] });
    },

    async reviewFinding(canonicalKey, action, body, tenant) {
      const data = await fetchJson(withTenantQs(
        `/api/reasoning/findings/${encodeURIComponent(canonicalKey)}/${action}`, tenant),
        { method: "POST", body: JSON.stringify(body || {}) });
      return data;
    },

    // ---- task lifecycle (new API) ----
    // status filter: undefined | "active" | "completed" | "closed"
    async reasoningTasksFiltered(tenant, status) {
      const path = "/api/reasoning/tasks";
      const qs = new URLSearchParams({ tenant: tenant || "default" });
      if (status) qs.set("status", status);
      const data = await fetchJson(path + "?" + qs.toString());
      const list =
        Array.isArray(data) ? data :
        Array.isArray(data?.tasks) ? data.tasks :
        Array.isArray(data?.items) ? data.items :
        Array.isArray(data?.questions) ? data.questions :
        Array.isArray(data?.results) ? data.results :
        [];
      return list.map(t => normalizeReasoningTask(t)).sort((a, b) => {
        const au = a.updated_at || a.created_at || "";
        const bu = b.updated_at || b.created_at || "";
        if (au !== bu) return au < bu ? 1 : -1;
        return (a.canonical_key || "").localeCompare(b.canonical_key || "");
      });
    },

    async closeTask(taskKey, tenant) {
      const data = await fetchJson(withTenantQs(
        `/api/reasoning/tasks/${encodeURIComponent(taskKey)}/close`, tenant), {
        method: "POST",
        body: JSON.stringify({}),
      });
      return data;
    },

    async reopenTask(taskKey, tenant) {
      const data = await fetchJson(withTenantQs(
        `/api/reasoning/tasks/${encodeURIComponent(taskKey)}/reopen`, tenant), {
        method: "POST",
        body: JSON.stringify({}),
      });
      return data;
    },

    async bulkDeleteClosed(tenant) {
      const data = await fetchJson(withTenantQs("/api/reasoning/tasks/bulk-delete-closed", tenant), {
        method: "POST",
        body: JSON.stringify({}),
      });
      return data;
    },

    async deleteTask(taskKey, tenant) {
      const data = await fetchJson(withTenantQs(
        `/api/reasoning/tasks/${encodeURIComponent(taskKey)}/delete`, tenant), {
        method: "POST",
        body: JSON.stringify({}),
      });
      return data;
    },

    // body: { keys: [...] }  OR  { before: "2026-05-14" }
    async bulkCloseTasks(tenant, body) {
      const data = await fetchJson(withTenantQs(
        "/api/reasoning/tasks/bulk-close", tenant), {
        method: "POST",
        body: JSON.stringify(body || {}),
      });
      return data;
    },

    // legacy alias kept for any older callers
    async cancelTask(taskKey, tenant) {
      return await this.closeTask(taskKey, tenant);
    },

    // ---- SSE streaming run ----
    // POST /api/reasoning/tasks/{key}/run/stream — text/event-stream
    // callbacks: { onEvent(eventName, data), onError(err), onComplete(), onDiag(stage, info) }
    // onDiag is for surface-able transport events: "request_start", "response_headers",
    // "first_chunk", "parse_error" — so the UI can show what's happening before
    // application-level events start flowing.
    // returns: { close() } so caller can abort the stream
    runReasoningStream(taskKey, tenant, callbacks) {
      const cb = callbacks || {};
      const controller = new AbortController();
      const url = getBaseUrl()
        + `/api/reasoning/tasks/${encodeURIComponent(taskKey)}/run/stream`
        + `?${new URLSearchParams({ tenant: tenant || "default" }).toString()}`;

      (async () => {
        const t0 = performance.now();
        try {
          if (cb.onDiag) cb.onDiag("request_start", { url });
          const res = await fetch(url, {
            method: "POST",
            headers: {
              "Accept": "text/event-stream",
              "Content-Type": "application/json",
              "Cache-Control": "no-cache",
            },
            body: JSON.stringify({}),
            signal: controller.signal,
          });
          if (cb.onDiag) cb.onDiag("response_headers", {
            status: res.status,
            statusText: res.statusText,
            contentType: res.headers.get("content-type"),
            elapsed_ms: Math.round(performance.now() - t0),
          });
          if (!res.ok || !res.body) {
            const err = new Error(`HTTP ${res.status} ${res.statusText} · /run/stream`);
            err.status = res.status;
            throw err;
          }
          // Some proxies / Cloudflare set buffering; warn if content-type isn't SSE
          const ct = res.headers.get("content-type") || "";
          if (!ct.includes("text/event-stream")) {
            // try to fall through anyway, but warn
            if (cb.onDiag) cb.onDiag("warning", { message: `Content-Type is "${ct}", expected text/event-stream — server may be buffering.` });
          }

          const reader = res.body.getReader();
          const decoder = new TextDecoder("utf-8");
          let buffer = "";
          let currentEvent = "message";
          let dataLines = [];
          let totalBytes = 0;
          let firstChunk = true;

          function flushEvent() {
            if (!dataLines.length) return;
            const raw = dataLines.join("\n");
            let parsed;
            try { parsed = JSON.parse(raw); }
            catch (e) {
              if (cb.onDiag) cb.onDiag("parse_error", { event: currentEvent, raw, error: e.message });
              parsed = raw;
            }
            if (cb.onEvent) cb.onEvent(currentEvent, parsed);
            currentEvent = "message";
            dataLines = [];
          }

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            if (firstChunk) {
              firstChunk = false;
              if (cb.onDiag) cb.onDiag("first_chunk", {
                bytes: value.length,
                elapsed_ms: Math.round(performance.now() - t0),
              });
            }
            totalBytes += value.length;
            buffer += decoder.decode(value, { stream: true });
            // SSE events separated by blank line; lines split on \n
            let lineEnd;
            while ((lineEnd = buffer.indexOf("\n")) !== -1) {
              const line = buffer.slice(0, lineEnd).replace(/\r$/, "");
              buffer = buffer.slice(lineEnd + 1);
              if (line === "") {
                flushEvent();
              } else if (line.startsWith(":")) {
                // SSE comment, ignore
              } else if (line.startsWith("event:")) {
                currentEvent = line.slice(6).trim();
              } else if (line.startsWith("data:")) {
                dataLines.push(line.slice(5).replace(/^ /, ""));
              }
            }
          }
          // any tail
          flushEvent();
          if (cb.onDiag) cb.onDiag("stream_closed", { totalBytes, elapsed_ms: Math.round(performance.now() - t0) });
          if (cb.onComplete) cb.onComplete();
        } catch (e) {
          if (e.name === "AbortError") {
            if (cb.onDiag) cb.onDiag("aborted", { elapsed_ms: Math.round(performance.now() - t0) });
            return;
          }
          if (cb.onDiag) cb.onDiag("error", {
            message: e.message || String(e),
            name: e.name,
            elapsed_ms: Math.round(performance.now() - t0),
          });
          if (cb.onError) cb.onError(e);
        }
      })();

      return { close: () => controller.abort() };
    },
  };

  window.AL_API = api;
})();
