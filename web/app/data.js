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

  const SPARK = [4,6,5,9,7,12,10,14,11,15,13,18,16,21,19,24,22,28,26,32];

  window.AL_DATA = { ARTIFACTS, ATTENTION, RUNTIMES, SPARK };
})();
