/* Aletheia — main app orchestrator (wired to real API) */

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (this.state.error) {
      return React.createElement("pre", {
        style: { padding: 40, color: "#f44", fontFamily: "var(--font-mono)", fontSize: 13, whiteSpace: "pre-wrap" }
      }, "UI Error:\n" + this.state.error.message + "\n\n" + (this.state.error.stack || ""));
    }
    return this.props.children;
  }
}

const { useState: useStateApp, useEffect: useEffectApp } = React;

const EMPTY_TENANT = { id: "—", name: "no tenant", namespace: "—", graph: "—" };

function App() {
  const [screen, setScreen] = useStateApp("workbench");
  const [tenantId, setTenantId] = useStateApp(localStorage.getItem("aletheia.tenant") || "default");
  const [language, setLanguage] = useStateApp(localStorage.getItem("aletheia.language") || "en");
  const [role, setRole] = useStateApp("Analyst");
  const [connOpen, setConnOpen] = useStateApp(false);

  // tweaks
  const [tweaks, setTweak] = useTweaks(/*EDITMODE-BEGIN*/{
    "accent": "#5fc3d6",
    "density": "default"
  }/*EDITMODE-END*/);

  // when ?theme=… is present, let CSS own the palette (so the Variations
  // canvas shows three genuinely-different themes). Otherwise apply the
  // user-tweaked accent via JS.
  const themeFromUrl = React.useMemo(() => {
    try { return new URLSearchParams(location.search).get("theme"); }
    catch { return null; }
  }, []);

  useEffectApp(() => {
    const root = document.documentElement.style;
    if (themeFromUrl) {
      ["--accent", "--accent-dim", "--accent-bg", "--accent-line"].forEach(p => root.removeProperty(p));
    } else {
      const hex = tweaks.accent;
      root.setProperty("--accent", hex);
      root.setProperty("--accent-dim", hex);
      root.setProperty("--accent-bg", hex + "1A");
      root.setProperty("--accent-line", hex + "59");
    }
    document.body.dataset.density = tweaks.density;
  }, [tweaks.accent, tweaks.density, themeFromUrl]);

  // ?screen= for variations canvas embedding
  useEffectApp(() => {
    try {
      const params = new URLSearchParams(location.search);
      const s = params.get("screen");
      const t = params.get("tenant");
      const lang = params.get("lang");
      if (s) setScreen(s);
      if (t) setTenantId(t);
      if (lang === "zh" || lang === "en") setLanguage(lang);
    } catch {}
  }, []);

  // Fetch tenants from real API — no mock fallback
  const tenantsQ = useApiData("tenants", []);
  const tenants = (tenantsQ.data && tenantsQ.data.length) ? tenantsQ.data : [EMPTY_TENANT];
  const tenant = tenants.find(t => t.id === tenantId) || tenants[0];

  useEffectApp(() => { try { localStorage.setItem("aletheia.tenant", tenant.id); } catch {} }, [tenant.id]);
  useEffectApp(() => {
    try {
      localStorage.setItem("aletheia.language", language);
      document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
      const url = new URL(location.href);
      url.searchParams.set("lang", language);
      history.replaceState(null, "", url.toString());
    } catch {}
  }, [language]);

  const data = window.AL_DATA;

  function cycleTenant() {
    const idx = tenants.findIndex(t => t.id === tenant.id);
    selectTenant(tenants[(idx + 1) % tenants.length].id);
  }
  function selectTenant(nextTenantId) {
    const next = nextTenantId || "default";
    setTenantId(next);
    try {
      const url = new URL(location.href);
      url.searchParams.set("tenant", next);
      url.searchParams.set("screen", screen);
      url.searchParams.delete("task");
      url.searchParams.delete("ontology_basis");
      history.replaceState(null, "", url.toString());
    } catch {}
  }
  function cycleRole() {
    const roles = ["Developer", "Analyst", "CXO"];
    const idx = roles.indexOf(role);
    setRole(roles[(idx + 1) % roles.length]);
  }
  function selectLanguage(nextLanguage) {
    setLanguage(nextLanguage === "zh" ? "zh" : "en");
  }

  return (
    <div className="app">
      <TopBar screen={screen} tenant={tenant} role={role}
              tenants={tenants} onTenantSelect={selectTenant}
              onTenant={cycleTenant} onRole={cycleRole}
              language={language} onLanguageSelect={selectLanguage}
              onConn={() => setConnOpen(true)} />
      <MockBanner onClick={() => setConnOpen(true)} />
      <div className="body">
        <Rail screen={screen} onNav={setScreen} language={language} />
        {screen === "workbench" && <ErrorBoundary><Workbench data={data} tenant={tenant} language={language} /></ErrorBoundary>}
        {screen === "reasoning" && <ErrorBoundary><Reasoning tenant={tenant} language={language} /></ErrorBoundary>}
        {screen === "ontology"  && <ErrorBoundary><Ontology data={data} tenant={tenant} language={language} /></ErrorBoundary>}
        {screen === "graph"     && <ErrorBoundary><GraphExplorer data={data} tenant={tenant} language={language} /></ErrorBoundary>}
        {screen === "quality"   && <ErrorBoundary><Quality data={data} tenant={tenant} language={language} /></ErrorBoundary>}
        {screen === "runtime"   && <ErrorBoundary><Runtime data={data} tenant={tenant} language={language} /></ErrorBoundary>}
      </div>
      <StatusBar tenant={tenant} language={language} />

      <ConnectionDialog open={connOpen} onClose={() => setConnOpen(false)} />

      <TweaksPanel title="Aletheia tweaks">
        <TweakSection label="Brand">
          <TweakColor label="Accent" value={tweaks.accent}
                      options={["#5fc3d6", "#e0a046", "#a78bfa", "#4fbf8f", "#ef5d4d", "#ffffff"]}
                      onChange={v => setTweak("accent", v)} />
        </TweakSection>
        <TweakSection label="Density">
          <TweakRadio label="Rows" value={tweaks.density}
                      options={["compact", "default", "comfortable"]}
                      onChange={v => setTweak("density", v)} />
        </TweakSection>
        <TweakSection label="Jump to screen">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            {["workbench","reasoning","ontology","graph","quality","runtime"].map(s => (
              <button key={s}
                      onClick={() => setScreen(s)}
                      style={{
                        padding: "6px 8px",
                        border: "1px solid " + (screen === s ? tweaks.accent : "#2a323e"),
                        background: screen === s ? tweaks.accent + "1A" : "transparent",
                        color: screen === s ? tweaks.accent : "#b3b9c4",
                        fontFamily: "var(--font-mono)",
                        fontSize: 10,
                        textTransform: "uppercase",
                        letterSpacing: "0.08em",
                        cursor: "pointer"
                      }}>
                {s}
              </button>
            ))}
          </div>
        </TweakSection>
        <TweakSection label="Role">
          <TweakRadio label="View as" value={role}
                      options={["Developer","Analyst","CXO"]}
                      onChange={v => setRole(v)} />
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<ErrorBoundary><App /></ErrorBoundary>);
