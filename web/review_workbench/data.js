// Aletheia mock data — exported to window for cross-script use
(function () {
  const ARTIFACTS = [
    {
      id: "OT-EMP-001",
      type: "ObjectType",
      key: "Employee",
      title: "Employee",
      desc: "A person employed by the organization. Source-of-truth attributes are drawn from the HR canonical row; downstream link types reference this object.",
      status: "approved",
      confidence: 0.94,
      agent: "ontology-synth",
      version: 7,
      updated: "2026-05-14 09:32",
      payload: {
        name: "Employee",
        keys: ["employee_id"],
        properties: ["first_name","last_name","title","manager_id","hired_at","status"],
        provenance: { table: "hr.employees", rows: 218 }
      },
      webEnrichment: [
        {
          proposal_key: "webenrichment:object_employee:demo",
          target_artifact_key: "object:employee",
          source_url: "https://example.org/employee-master-data-governance",
          source_title: "Employee master data governance reference",
          summary: "External reference suggests employee master records should preserve manager hierarchy, employment status, and source ownership as reviewable context.",
          confidence: 0.64,
          status: "draft",
          created_at: "2026-05-23 22:00",
          raw_payload: {
            source: {
              search_query: "Employee ontology definition source evidence",
              retrieved_at: "2026-05-23T14:00:00Z",
              robots_risk: "demo fixture; reviewer must verify robots policy",
              license_risk: "not detected; reviewer must verify reuse terms"
            },
            field_provenance: [
              { artifact_field: "description", source_url: "https://example.org/employee-master-data-governance", proposed_operation: "enrich_context", review_required: true }
            ],
            governance: { canonical_writes: "disabled", graph_writes: "disabled", review_gate: "ontology_review_required" }
          }
        }
      ]
    },
    {
      id: "LT-RPT-014",
      type: "LinkType",
      key: "ReportsTo",
      title: "Employee reports-to Employee",
      desc: "Directed manager relationship between two Employees. Used by workload-balance reasoning and concentration-risk queries.",
      status: "proposed",
      confidence: 0.71,
      agent: "ontology-synth",
      version: 2,
      updated: "2026-05-15 02:11",
      payload: {
        name: "ReportsTo",
        source: "Employee",
        target: "Employee",
        cardinality: "many-to-one",
        properties: ["since","scope"],
        provenance: { fk: "employees.manager_id" }
      }
    },
    {
      id: "OT-ORD-002",
      type: "ObjectType",
      key: "Order",
      title: "Order",
      desc: "A customer-placed commercial order. Carries lifecycle status, monetary value, and an owning employee.",
      status: "approved",
      confidence: 0.91,
      agent: "ontology-synth",
      version: 5,
      updated: "2026-05-12 14:08",
    },
    {
      id: "LT-OWN-007",
      type: "LinkType",
      key: "OwnedBy",
      title: "Order owned-by Employee",
      desc: "Assignment of an Order to a responsible Employee. Drives workload distribution and concentration metrics.",
      status: "approved",
      confidence: 0.88,
      agent: "ontology-synth",
      version: 3,
      updated: "2026-05-13 11:45",
    },
    {
      id: "PR-EMP-022",
      type: "Property",
      key: "tenure_band",
      title: "Employee.tenure_band",
      desc: "Derived bucket: <1y, 1-3y, 3-7y, 7y+. Computed from hired_at; backfill on schedule.",
      status: "changes",
      confidence: 0.62,
      agent: "ontology-synth",
      version: 1,
      updated: "2026-05-15 01:48",
    },
    {
      id: "OT-CUS-003",
      type: "ObjectType",
      key: "Customer",
      title: "Customer",
      desc: "A purchasing counterparty. Distinct from Lead. Linked to Orders through an OwnedBy assignment to Employee.",
      status: "proposed",
      confidence: 0.83,
      agent: "ontology-synth",
      version: 1,
      updated: "2026-05-15 03:02",
    },
    {
      id: "LT-PLA-019",
      type: "LinkType",
      key: "PlacedBy",
      title: "Order placed-by Customer",
      desc: "Origin of an Order in the customer dimension.",
      status: "proposed",
      confidence: 0.79,
      agent: "ontology-synth",
      version: 1,
      updated: "2026-05-15 02:55",
    },
    {
      id: "OT-REG-004",
      type: "ObjectType",
      key: "Region",
      title: "Region",
      desc: "Geographic territory. Used to bucket Employees and Customers for territorial analysis.",
      status: "rejected",
      confidence: 0.51,
      agent: "ontology-synth",
      version: 2,
      updated: "2026-05-14 16:21",
    },
    {
      id: "PR-ORD-031",
      type: "Property",
      key: "value_band",
      title: "Order.value_band",
      desc: "Categorical bucket over Order.value. Cut points need analyst approval before downstream use.",
      status: "changes",
      confidence: 0.58,
      agent: "ontology-synth",
      version: 1,
      updated: "2026-05-14 22:09",
    },
    {
      id: "LT-MEN-029",
      type: "LinkType",
      key: "MentorOf",
      title: "Employee mentor-of Employee",
      desc: "Soft developmental relationship — distinct from ReportsTo. Sparse, mostly inferred from calendar evidence.",
      status: "proposed",
      confidence: 0.43,
      agent: "calendar-ingest",
      version: 1,
      updated: "2026-05-15 03:38",
    },
  ];

  const REASONING_THREAD = [
    {
      who: "Analyst",
      role: "user",
      name: "M. Aoki",
      time: "02:11:08",
      text: "We saw Employee:4 working ~3× the median order count last quarter. Is the ReportsTo link useful here, or is OwnedBy enough?"
    },
    {
      who: "Aletheia",
      role: "agent",
      name: "ontology-synth",
      time: "02:11:09",
      text: "ReportsTo and OwnedBy answer different questions. OwnedBy explains workload — it carries the assignment. ReportsTo explains accountability — who reviewed and ratified the workload."
    },
    {
      who: "Aletheia",
      role: "agent",
      name: "ontology-synth",
      time: "02:11:10",
      text: "For Employee:4, concentration is structural: 47 active Orders, all OwnedBy Employee:4, none reassigned in 90 days. ReportsTo points at Employee:9 (Tier-2 Manager); no manager change recorded across that window."
    },
    {
      who: "Aletheia",
      role: "agent",
      name: "ontology-synth",
      time: "02:11:11",
      text: "Proposing ReportsTo as a separate, sparse LinkType — required by concentration-risk reasoning, not by basic workload distribution. Cardinality many-to-one. Evidence: 3 reviewer chains, 1 conflict (Employee:18 reports to two managers in source data — to flag, not block)."
    },
  ];

  const EVIDENCE = [
    { kind: "fact",       title: "FK constraint employees.manager_id → employees.employee_id confirmed",                                   src: "schema://hr.employees", conf: 0.99 },
    { kind: "fact",       title: "97.2% of 218 rows have manager_id populated; 6 NULLs are C-suite",                                       src: "table://hr.employees",  conf: 0.97 },
    { kind: "hypothesis", title: "ReportsTo cardinality is many-to-one in practice (1 manager per employee at a time)",                    src: "policy://hr-handbook §4.2", conf: 0.86 },
    { kind: "conflict",   title: "Employee:18 has 2 managers in source data — temporal overlap (2026-02-14 → 2026-03-01)",                  src: "row://hr.employees#18", conf: 0.74 },
    { kind: "fact",       title: "ReportsTo is required by 4 downstream reasoning templates (workload-bal, concentration, tenure, span)",  src: "registry://reasoning",  conf: 0.95 },
    { kind: "missing",    title: "No reviewer signature for ReportsTo evidence pack — analyst sign-off required before approval",          src: "audit://pending",       conf: null },
  ];

  const AUDIT = [
    { ts: "02:11", act: "proposed",  who: "ontology-synth",  detail: "Initial proposal — confidence 0.71" },
    { ts: "02:09", act: "draft",     who: "ontology-synth",  detail: "Evidence pack assembled (5 items)" },
    { ts: "01:45", act: "comment",   who: "M. Aoki",         detail: "Asked about distinction from OwnedBy" },
    { ts: "01:32", act: "changes",   who: "S. Park",         detail: "Need cardinality clarified — many-to-one not stated" },
    { ts: "Yest.", act: "draft",     who: "ontology-synth",  detail: "Detected via FK analysis on employees.manager_id" },
  ];

  const ATTENTION = [
    { sev: "crit", reason: "BLOCKED RUN",    title: "workload-balance reasoning halted — missing approved ReportsTo",     meta: "run #4821 · scope Employee:4 · 02:11", conf: "—",   age: "8m" },
    { sev: "crit", reason: "EVIDENCE GAP",   title: "MentorOf has 0 corroborating sources beyond calendar inference",     meta: "LT-MEN-029 · agent calendar-ingest",   conf: "0.43", age: "12m" },
    { sev: "warn", reason: "LOW CONFIDENCE", title: "Order.value_band cut points unreviewed",                              meta: "PR-ORD-031 · agent ontology-synth",   conf: "0.58", age: "1h" },
    { sev: "warn", reason: "LOW CONFIDENCE", title: "Employee.tenure_band derivation needs analyst review",                meta: "PR-EMP-022 · agent ontology-synth",   conf: "0.62", age: "2h" },
    { sev: "info", reason: "POLICY",         title: "calendar-ingest agent emitted 7 proposals — sandbox gate required",  meta: "agent calendar-ingest · 03:14",       conf: "—",   age: "3h" },
    { sev: "warn", reason: "CONFLICT",       title: "Employee:18 has two ReportsTo edges in source — temporal overlap",   meta: "LT-RPT-014 · row hr.employees#18",    conf: "0.74", age: "4h" },
    { sev: "info", reason: "SANDBOX",        title: "Region not yet approved — sandbox queries return empty",             meta: "OT-REG-004 · rejected v2",            conf: "0.51", age: "1d" },
  ];

  const RUNTIMES = [
    { id: "anthropic.claude-sonnet", name: "claude-sonnet",   status: "ok",   binary: "/usr/local/bin/anthropic-cli", template: "default_cli_policy", lastRun: "00:42",  enabled: true,  runs24h: 312 },
    { id: "openai.gpt-4o",           name: "gpt-4o",          status: "ok",   binary: "/usr/local/bin/openai-cli",    template: "default_cli_policy", lastRun: "02:08",  enabled: true,  runs24h: 188 },
    { id: "ollama.llama-3-70b",      name: "llama-3-70b",     status: "warn", binary: "/usr/local/bin/ollama",        template: "default_cli_policy", lastRun: "yest.",  enabled: true,  runs24h: 14  },
    { id: "calendar-ingest",         name: "calendar-ingest", status: "warn", binary: "/opt/aletheia/agents/cal.py",  template: "calendar_policy",    lastRun: "03:14",  enabled: true,  runs24h: 7   },
    { id: "tableau.exporter",        name: "tableau.exporter",status: "down", binary: "/opt/aletheia/agents/tab.sh",  template: "—",                  lastRun: "—",      enabled: false, runs24h: 0   },
  ];

  // graph nodes — positions are in normalized [0..1] space; we render with viewBox 0 0 1000 600
  const GRAPH = {
    center: "Employee:4",
    nodes: [
      { id: "Employee:4",  type: "Employee", x: 500, y: 300, r: 18, label: "M. Beresford",   center: true },
      { id: "Employee:9",  type: "Employee", x: 500, y: 130, r: 13, label: "T. Khalil (mgr)" },
      { id: "Employee:11", type: "Employee", x: 270, y: 220, r: 10, label: "J. Alder" },
      { id: "Employee:18", type: "Employee", x: 340, y: 430, r: 10, label: "R. Vasquez", flag: true },
      { id: "Employee:23", type: "Employee", x: 670, y: 460, r: 10, label: "S. Naidu" },
      { id: "Order:1012",  type: "Order",    x: 760, y: 240, r:  9, label: "#1012 $24k" },
      { id: "Order:1014",  type: "Order",    x: 810, y: 320, r:  9, label: "#1014 $9k" },
      { id: "Order:1019",  type: "Order",    x: 770, y: 400, r:  9, label: "#1019 $61k" },
      { id: "Order:1101",  type: "Order",    x: 870, y: 350, r:  8, label: "#1101 $7k" },
      { id: "Customer:88", type: "Customer", x: 150, y: 380, r: 11, label: "Strand Ltd." },
      { id: "Customer:91", type: "Customer", x: 170, y: 100, r: 11, label: "Iverson Corp" },
      { id: "Region:NE",   type: "Region",   x:  80, y: 250, r:  8, label: "Region:NE", muted: true },
    ],
    edges: [
      { s: "Employee:4",  t: "Employee:9",  kind: "ReportsTo" },
      { s: "Employee:11", t: "Employee:9",  kind: "ReportsTo" },
      { s: "Employee:18", t: "Employee:9",  kind: "ReportsTo", flag: true },
      { s: "Employee:23", t: "Employee:9",  kind: "ReportsTo" },
      { s: "Order:1012",  t: "Employee:4",  kind: "OwnedBy" },
      { s: "Order:1014",  t: "Employee:4",  kind: "OwnedBy" },
      { s: "Order:1019",  t: "Employee:4",  kind: "OwnedBy" },
      { s: "Order:1101",  t: "Employee:4",  kind: "OwnedBy" },
      { s: "Order:1012",  t: "Customer:91", kind: "PlacedBy" },
      { s: "Order:1019",  t: "Customer:88", kind: "PlacedBy" },
      { s: "Customer:91", t: "Region:NE",   kind: "InRegion", muted: true },
    ],
  };

  const SPARK = [4,6,5,9,7,12,10,14,11,15,13,18,16,21,19,24,22,28,26,32];

  window.AL_DATA = { ARTIFACTS, REASONING_THREAD, EVIDENCE, AUDIT, ATTENTION, RUNTIMES, GRAPH, SPARK };
})();
