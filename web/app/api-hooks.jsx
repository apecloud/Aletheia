/* Aletheia — live data hook + Connection chip/dialog */

const { useState: useStateLive, useEffect: useEffectLive, useCallback: useCallbackLive, useRef: useRefLive } = React;

/* ---------- useApiData(fnName, args, opts) -----------------
   Returns: { data, loading, error, source, refetch }
     source: "live" | "live-stale" | "loading" | "error" | "mock"
   Behavior:
     - On error: returns source "error" with null data, UNLESS:
        a) we previously got live data → "live-stale" (keep last data)
        b) mock fallback is enabled in the connection settings AND a `fallback`
           was provided → "mock" with the fallback data
     - Mock fallback is opt-in (off by default for debugging predictability).
*/
function useApiData(fnName, args, opts) {
  const fallback = opts && opts.fallback;
  const enabled = opts ? opts.enabled !== false : true;
  const [mockAllowed, setMockAllowed] = useStateLive(window.AL_API.isMockAllowed());
  useEffectLive(() => {
    const h = e => setMockAllowed(window.AL_API.isMockAllowed());
    window.addEventListener("aletheia:mock-toggled", h);
    return () => window.removeEventListener("aletheia:mock-toggled", h);
  }, []);
  const [state, setState] = useStateLive({
    data: null,
    loading: enabled,
    error: null,
    source: enabled ? "loading" : "error",
  });
  const argsKey = JSON.stringify(args || []);
  const seqRef = useRefLive(0);
  const fetchOnce = useCallbackLive(async () => {
    if (!enabled) { setState({ data: null, loading: false, error: null, source: "error" }); return; }
    const seq = ++seqRef.current;
    setState(s => ({ ...s, loading: true, error: null, source: s.data ? s.source : "loading" }));
    try {
      const fn = window.AL_API[fnName];
      const result = await fn(...(args || []));
      if (seq !== seqRef.current) return;
      setState({ data: result, loading: false, error: null, source: "live" });
      window.dispatchEvent(new CustomEvent("aletheia:api-ok"));
    } catch (err) {
      if (seq !== seqRef.current) return;
      window.dispatchEvent(new CustomEvent("aletheia:api-fail", { detail: { fn: fnName, error: err } }));
      setState(s => {
        // sticky-live: if we ever succeeded, keep last live data
        if (s.source === "live" || s.source === "live-stale") {
          return { data: s.data, loading: false, error: err, source: "live-stale" };
        }
        // opt-in mock fallback (only if a fallback was provided and user enabled it)
        if (window.AL_API.isMockAllowed() && fallback != null) {
          return { data: fallback, loading: false, error: err, source: "mock" };
        }
        return { data: null, loading: false, error: err, source: "error" };
      });
    }
  }, [fnName, argsKey, enabled, mockAllowed]);

  useEffectLive(() => { fetchOnce(); }, [fetchOnce]);

  // refetch when base URL changes
  useEffectLive(() => {
    const h = () => fetchOnce();
    window.addEventListener("aletheia:base-url-changed", h);
    window.addEventListener("aletheia:retry", h);
    return () => {
      window.removeEventListener("aletheia:base-url-changed", h);
      window.removeEventListener("aletheia:retry", h);
    };
  }, [fetchOnce]);

  return { ...state, refetch: fetchOnce };
}

/* ---------- Connection state — broadcasted via window events ---------- */
function useConnectionState() {
  const [state, setState] = useStateLive({ status: "unknown", lastChecked: null });
  useEffectLive(() => {
    function ok()  { setState({ status: "live", lastChecked: new Date() }); }
    function fail(e) { setState({ status: "down", lastChecked: new Date(), error: e?.detail?.error }); }
    window.addEventListener("aletheia:api-ok", ok);
    window.addEventListener("aletheia:api-fail", fail);
    return () => {
      window.removeEventListener("aletheia:api-ok", ok);
      window.removeEventListener("aletheia:api-fail", fail);
    };
  }, []);
  return state;
}

/* ---------- Connection chip (in topbar) ---------- */
function ConnectionChip({ onClick }) {
  const conn = useConnectionState();
  const isLive = conn.status === "live";
  const isDown = conn.status === "down";
  const dotColor =
    isLive ? "var(--approved)" :
    isDown ? "var(--rejected)" :
             "var(--changes)";
  const label = isLive ? "LIVE" : isDown ? "MOCK" : "···";
  return (
    <div className="chip" onClick={onClick}
         title="Configure API connection"
         style={{
           cursor: "pointer",
           background: isDown ? "oklch(0.78 0.14 75 / 0.10)" : isLive ? "oklch(0.74 0.13 165 / 0.10)" : "var(--bg-2)",
           borderLeft: "1px solid " + (isDown ? "oklch(0.78 0.14 75 / 0.5)" : isLive ? "oklch(0.74 0.13 165 / 0.5)" : "var(--line)"),
         }}>
      <span style={{ width: 8, height: 8, background: dotColor, display: "inline-block" }} />
      <span className="label">API</span>
      <span className="val" style={{ color: isLive ? "var(--approved)" : isDown ? "var(--changes)" : "var(--text)" }}>{label}</span>
      <span className="caret">▾</span>
    </div>
  );
}

/* ---------- Connection banner ---------- */
function MockBanner({ onClick }) {
  const conn = useConnectionState();
  if (conn.status !== "down") return null;
  return (
    <div style={{
      padding: "10px 16px",
      background: "oklch(0.66 0.18 25 / 0.10)",
      borderBottom: "1px solid oklch(0.66 0.18 25 / 0.45)",
      color: "var(--rejected)",
      fontFamily: "var(--font-mono)",
      fontSize: 11,
      letterSpacing: "0.04em",
      display: "flex",
      alignItems: "center",
      gap: 12,
    }}>
      <span style={{ width: 8, height: 8, background: "var(--rejected)", display: "inline-block" }} />
      <strong style={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10 }}>API unreachable</strong>
      <span style={{ color: "var(--muted)", textTransform: "none" }}>
        cannot reach <span style={{ color: "var(--text-dim)" }}>{window.AL_API.getBaseUrl()}</span> · screens will show empty / error states until connection is restored
      </span>
      <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
        <button className="btn xs" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ Retry now</button>
        <button className="btn xs" onClick={onClick}>Configure API</button>
      </span>
    </div>
  );
}

/* ---------- Connection Dialog ---------- */
function ConnectionDialog({ open, onClose }) {
  const [url, setUrl] = useStateLive(window.AL_API.getBaseUrl());
  const [mockOn, setMockOn] = useStateLive(window.AL_API.isMockAllowed());
  const [testing, setTesting] = useStateLive(false);
  const [result, setResult] = useStateLive(null);
  const conn = useConnectionState();

  useEffectLive(() => {
    if (open) {
      setUrl(window.AL_API.getBaseUrl());
      setMockOn(window.AL_API.isMockAllowed());
      setResult(null);
    }
  }, [open]);

  if (!open) return null;

  async function test() {
    setTesting(true); setResult(null);
    // temporarily set the URL, run ping, then notify all hooks to refetch
    const prev = window.AL_API.getBaseUrl();
    window.AL_API.setBaseUrl(url);
    try {
      const r = await window.AL_API.ping();
      setResult({ ok: true, msg: `Connected · ${r.tenants} tenant(s)` });
    } catch (e) {
      const corsHint = (e.message || "").includes("Failed to fetch") || e.name === "TypeError";
      setResult({
        ok: false,
        msg: e.message || String(e),
        corsHint,
      });
      window.AL_API.setBaseUrl(prev); // revert
    } finally {
      setTesting(false);
    }
  }

  function save() {
    window.AL_API.setBaseUrl(url);
    window.AL_API.setMockAllowed(mockOn);
    onClose();
  }

  function toggleMockNow(v) {
    setMockOn(v);
    window.AL_API.setMockAllowed(v);
    // trigger re-fetch so screens update immediately
    window.dispatchEvent(new CustomEvent("aletheia:retry"));
  }

  return (
    <div style={{
      position: "fixed", inset: 0,
      background: "rgba(7, 9, 12, 0.7)",
      backdropFilter: "blur(2px)",
      zIndex: 999,
      display: "grid", placeItems: "center"
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        width: 560,
        background: "var(--bg-2)",
        border: "1px solid var(--line-strong)",
        boxShadow: "0 30px 80px rgba(0,0,0,0.55)",
        fontFamily: "var(--font-sans)",
      }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--line)", background: "var(--bg-3)", display: "flex", alignItems: "center" }}>
          <div className="eyebrow accent">Connection</div>
          <div style={{ marginLeft: 10, fontSize: 16, color: "var(--text)" }}>API endpoint</div>
          <button onClick={onClose} style={{ marginLeft: "auto", background: "transparent", color: "var(--muted)", border: "1px solid var(--line)", padding: "3px 8px", fontFamily: "var(--font-mono)", fontSize: 10, cursor: "pointer" }}>ESC</button>
        </div>

        <div style={{ padding: 20 }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Base URL</div>
          <input className="input" value={url} onChange={e => setUrl(e.target.value)}
                 onKeyDown={e => { if (e.key === "Enter") test(); }}
                 placeholder="http://localhost:8765"
                 style={{ fontSize: 13, padding: "10px 12px" }} />

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="btn" onClick={test} disabled={testing}>{testing ? "Testing…" : "Test connection"}</button>
            <button className="btn primary" onClick={save}>Save &amp; reload</button>
            <span style={{ flex: 1 }} />
            <button className="btn ghost" onClick={() => { setUrl("http://localhost:8765"); }}>Default</button>
          </div>

          {result && (
            <div style={{
              marginTop: 14,
              padding: "10px 12px",
              border: "1px solid " + (result.ok ? "oklch(0.74 0.13 165 / 0.4)" : "oklch(0.66 0.18 25 / 0.4)"),
              background: (result.ok ? "oklch(0.74 0.13 165 / 0.08)" : "oklch(0.66 0.18 25 / 0.08)"),
              color: result.ok ? "var(--approved)" : "var(--rejected)",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
            }}>
              <div style={{ textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10, marginBottom: 4 }}>
                {result.ok ? "● OK" : "● ERROR"}
              </div>
              <div>{result.msg}</div>
              {result.corsHint && (
                <div style={{ marginTop: 8, color: "var(--changes)", lineHeight: 1.5 }}>
                  Likely CORS or network — the backend exists but the browser blocked the response. Fix on server:
                  <pre style={{ marginTop: 6, padding: 8, background: "var(--bg-1)", border: "1px solid var(--line)", color: "var(--text-dim)", whiteSpace: "pre-wrap" }}>
{`# enable CORS in the workbench server (FastAPI example)
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware,
  allow_origins=["*"],     # tighten in prod
  allow_methods=["*"],
  allow_headers=["*"])`}
                  </pre>
                </div>
              )}
            </div>
          )}

          <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Fallback behavior</div>
            <label style={{ display: "flex", alignItems: "center", gap: 12, cursor: "pointer", padding: "10px 12px", border: "1px solid var(--line)", background: "var(--bg-3)" }}>
              <input type="checkbox" checked={mockOn} onChange={e => toggleMockNow(e.target.checked)}
                     style={{ accentColor: "var(--accent)", width: 14, height: 14, margin: 0 }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: "var(--font-sans)", fontSize: 13, color: "var(--text)" }}>
                  Use mock data when API is unreachable
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 4, lineHeight: 1.5, letterSpacing: 0, textTransform: "none" }}>
                  Off (default, recommended for debugging): screens show loading / error / empty states honestly. On: failed requests are silently filled with mock data — useful for offline UI preview only.
                </div>
              </div>
              <span style={{
                fontFamily: "var(--font-mono)", fontSize: 9, textTransform: "uppercase", letterSpacing: "0.08em",
                color: mockOn ? "var(--changes)" : "var(--approved)",
              }}>
                {mockOn ? "MOCK ON" : "MOCK OFF"}
              </span>
            </label>
          </div>

          <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Status</div>
            <dl className="kv">
              <dt>Endpoint</dt><dd>{window.AL_API.getBaseUrl()}</dd>
              <dt>State</dt>
              <dd style={{ color: conn.status === "live" ? "var(--approved)" : conn.status === "down" ? "var(--rejected)" : "var(--changes)" }}>
                {conn.status === "live" ? "● live · using real data" :
                 conn.status === "down" ? "● down · using mock fallback" :
                                          "● unknown · awaiting first call"}
              </dd>
              <dt>Last check</dt><dd>{conn.lastChecked ? conn.lastChecked.toISOString().slice(11, 19) + " UTC" : "—"}</dd>
            </dl>
            <button className="btn ghost" style={{ marginTop: 12 }}
                    onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>
              ↻ Retry all queries
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------- ApiStatus —— inline loading / error / empty / stale display ---------- */
function ApiStatus({ q, what = "data", showLoading = true, showStale = true, retryLabel = "Retry", style }) {
  if (q.loading && showLoading && !q.data) {
    return (
      <div style={{ padding: 32, textAlign: "center", color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.04em", ...(style || {}) }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 8, height: 8, background: "var(--accent)", display: "inline-block", animation: "pulse 1.4s ease-in-out infinite" }} />
          Loading {what} from {window.AL_API.getBaseUrl()}…
        </div>
      </div>
    );
  }
  if (q.source === "error") {
    return (
      <div style={{
        padding: 24,
        margin: 16,
        border: "1px solid oklch(0.66 0.18 25 / 0.4)",
        background: "oklch(0.66 0.18 25 / 0.06)",
        ...(style || {}),
      }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--rejected)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>
          ● API error — failed to load {what}
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)", marginBottom: 12, wordBreak: "break-word" }}>
          {q.error?.message || String(q.error || "unknown error")}
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginBottom: 12 }}>
          Endpoint: {window.AL_API.getBaseUrl()}
        </div>
        <button className="btn" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ {retryLabel}</button>
      </div>
    );
  }
  if (q.source === "live-stale" && showStale) {
    return (
      <div style={{
        padding: "8px 14px",
        borderBottom: "1px solid oklch(0.78 0.14 75 / 0.4)",
        background: "oklch(0.78 0.14 75 / 0.06)",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "var(--changes)",
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}>
        <span style={{ width: 8, height: 8, background: "var(--changes)", display: "inline-block" }} />
        Showing last successful fetch · refresh failed
        <button className="btn xs" style={{ marginLeft: "auto" }} onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ Retry</button>
      </div>
    );
  }
  return null;
}

Object.assign(window, { useApiData, useConnectionState, ConnectionChip, ConnectionDialog, MockBanner, ApiStatus });
