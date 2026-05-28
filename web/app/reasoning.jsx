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

function isZhRX(language) {
  return String(language || "en").toLowerCase().startsWith("zh");
}

function tRX(language, en, zh) {
  return isZhRX(language) ? zh : en;
}

const RESULT_TITLE_ZH_RX = {
  "Card-not-present transactions carry elevated fraud risk": "非面对面交易具有更高欺诈风险",
  "Verification mismatch is a compact fraud-risk signal": "验证不匹配是紧凑的欺诈风险信号",
  "Missing POS entry mode should be reviewed as a weak-control pattern": "缺失 POS 录入模式应作为弱控制模式复核",
  "Merchant category concentration reveals high-yield fraud review segments": "商户类别集中度揭示高价值欺诈复核分组",
  "Same-day duplicate transaction clusters need multi-swipe review": "同日重复交易簇需要复核多次刷卡风险",
  "Single chokepoint dependency creates concentrated country exposure": "单一咽喉点依赖造成国家风险集中暴露",
  "Hazard-adjusted chokepoint risk should drive review priority": "咽喉点复核优先级应纳入风险因子调整",
  "Bab el-Mandeb risk propagation identifies countries for immediate review": "Bab el-Mandeb 风险传播识别需立即复核的国家",
  "Single-chokepoint dependency can create concentrated country exposure": "单一咽喉点依赖可能造成国家暴露集中",
  "Hazard severity should be joined to dependent trade value before ranking chokepoints": "咽喉点排序前应把风险严重度与依赖贸易额关联",
  "Red Sea / Bab el-Mandeb escalation should prioritize dependent countries by systemic risk": "红海 / Bab el-Mandeb 升级风险应按系统性风险确定国家优先级",
  "High throughput alone is not enough for a graph reasoning finding": "仅有高吞吐量不足以形成图推理发现",
  "Card-not-present transactions concentrate fraud risk": "非面对面交易集中欺诈风险",
  "Verification mismatch transactions have elevated fraud rate": "验证不匹配交易欺诈率更高",
  "Missing POS entry mode may identify a weak-control channel": "缺失 POS 录入模式可能识别弱控制渠道",
  "Merchant categories concentrate fraud exposure": "商户类别集中欺诈暴露",
  "Same account/merchant/amount/day duplicate clusters indicate multi-swipe risk": "同账户/商户/金额/日期重复簇提示多次刷卡风险",
  "Expiration-key mismatch does not clear the value threshold": "有效期录入不匹配未达到价值阈值",
  "Find graph reasoning findings for maritime chokepoint risk": "发现海运咽喉点风险的图推理发现",
  "Discover graph reasoning findings for maritime chokepoint risk": "发现海运咽喉点风险的图推理发现",
  "Find high-value fraud risk findings": "发现高价值欺诈风险发现",
  "Discover high-value credit card fraud risk findings": "发现高价值信用卡欺诈风险发现",
  "Find high-value reasoning findings": "发现高价值推理发现",
  "Compare card-present and card-not-present fraud rates against the dataset baseline.": "对比面对面与非面对面交易欺诈率相对数据集基线的差异。",
  "Use the safe derived verification-match flag instead of raw verification values.": "使用安全的派生验证匹配标记，而不是原始验证值。",
  "Missing POS entry mode showed the highest fraud-rate lift in the imported dataset profile.": "导入数据画像中，POS 录入模式缺失显示出最高的欺诈率提升。",
  "Rank merchant categories by fraud rate and volume to separate noisy rates from high-value findings.": "按欺诈率和交易量对商户类别排序，区分噪声比例和高价值发现。",
  "Repeated same-day transaction clusters are useful triage candidates for duplicate authorization or multi-swipe review.": "同日重复交易簇适合作为重复授权或多次刷卡复核的分诊候选。",
  "The imported profile did not show enough value lift to promote this into a candidate finding before stronger evidence exists.": "导入画像尚未显示足够价值提升，因此在更强证据出现前不提升为候选发现。",
  "Rank country/chokepoint pairs by value share and dependent trade value to find countries exposed to one chokepoint.": "按价值占比和依赖贸易额排序国家/咽喉点组合，识别暴露于单一咽喉点的国家。",
  "Combine hazard likelihood/severity with systemic risk results so the finding explains risk propagation, not just trade volume.": "把风险可能性/严重度与系统性风险结果结合，使发现能解释风险传播，而不只是贸易量。",
  "Use the chokepoint hazard row and downstream country risk rows to prioritize analyst review when upstream events increase.": "使用咽喉点风险行和下游国家风险行，在上游事件升级时确定分析师复核优先级。",
  "A volume-only ranking does not explain hazard, dependency, country exposure, and action linkage.": "仅按交易量排名无法解释风险因子、依赖关系、国家暴露和行动关联。",
  "No rationale recorded.": "未记录依据。",
};

const RESULT_TEXT_ZH_RX = [
  [/Card-not-present transactions show a fraud rate of ([^ ]+) versus the dataset baseline of ([^,]+), making this a high-value triage segment\./,
    "非面对面交易欺诈率为 $1，高于数据集基线 $2，因此是高价值分诊分组。"],
  [/Transactions where the derived verification-match flag is false show a fraud rate of ([^,]+), above the baseline of ([^.]+)\./,
    "派生验证匹配标记为 false 的交易欺诈率为 $1，高于基线 $2。"],
  [/Transactions with missing POS entry mode show a fraud rate of ([^,]+), materially above baseline\./,
    "POS 录入模式缺失的交易欺诈率为 $1，明显高于基线。"],
  [/The highest-risk merchant categories include (.+)\./,
    "最高风险的商户类别包括 $1。"],
  [/The dataset contains ([0-9,]+) same customer \/ same merchant \/ same amount \/ same-day duplicate clusters, a useful review entry point for duplicate authorization and multi-swipe behavior\./,
    "数据集中存在 $1 个同客户 / 同商户 / 同金额 / 同日重复交易簇，可作为重复授权和多次刷卡行为的复核入口。"],
  [/([A-Z]{2,3}) depends heavily on ([^,]+), where the hazard profile includes conflict likelihood ([^ ]+) and geopolitical likelihood ([^:]+): ([^ ]+) of modeled maritime trade value flows through that chokepoint \(\$([^)]+) of dependent value\)\./,
    "$1 高度依赖 $2；该咽喉点风险画像包含冲突可能性 $3、地缘政治可能性 $4。建模海运贸易价值中有 $5 经过该咽喉点，依赖价值为 $6。"],
  [/([^ ]+(?: [^ ]+)*) has the highest modeled trade-at-risk row in the current dataset: ([A-Z]{2,3}) shows \$(.+) expected trade value at risk and \$(.+) trade impacted\./,
    "当前数据集中 $1 对应最高的建模风险贸易行：$2 的预期风险贸易价值为 $3，受影响贸易额为 $4。"],
  [/If Red Sea \/ Bab el-Mandeb risk rises, the first review queue should include (.+)\. The graph path is hazard at Bab el-Mandeb -> chokepoint -> dependent country -> systemic risk metric -> analyst action\./,
    "如果红海 / Bab el-Mandeb 风险上升，首批复核队列应包含 $1。图路径为：Bab el-Mandeb 风险因子 -> 咽喉点 -> 依赖国家 -> 系统性风险指标 -> 分析师行动。"],
  [/Draft candidate from maritime-risk graph playbook; requires human review before formal finding approval\./,
    "来自 maritime-risk 图推理 playbook 的候选发现；正式批准前需要人工复核。"],
  [/This phase uses structural 2022 dependency data and does not include live event updates\./,
    "当前阶段使用 2022 年结构性依赖数据，尚未包含实时事件更新。"],
  [/The playbook uses structural chokepoint risk data; ACLED\/GDELT live events are a planned enrichment, not yet imported\./,
    "该 playbook 使用结构化咽喉点风险数据；ACLED/GDELT 实时事件是计划中的信息增益数据，尚未导入。"],
  [/Draft candidate from Autopilot playbook; requires human review before formal finding approval\./,
    "来自 Autopilot playbook 的候选发现；正式批准前需要人工复核。"],
  [/Uses a derived match flag only; raw verification values are not surfaced\./,
    "仅使用派生匹配标记；原始验证值不会展示。"],
  [/Category ranking should be paired with volume and amount thresholds before operational use\./,
    "商户类别排序在投入运营前应结合交易量和金额阈值。"],
  [/Duplicate clusters include benign repeats; candidate requires case-level review\./,
    "重复簇可能包含正常重复交易；该候选需要逐案复核。"],
  [/Pruned because expected fraud-rate lift is below candidate threshold and no strong operational action follows from the field alone\./,
    "已剪枝：预期欺诈率提升低于候选阈值，且仅凭该字段无法形成强运营动作。"],
  [/Pruned because it is a ranking\/reporting hypothesis without a complete hazard -> chokepoint -> country -> risk metric -> action path\./,
    "已剪枝：这只是排名/报表假设，缺少完整的风险因子 -> 咽喉点 -> 国家 -> 风险指标 -> 行动路径。"],
  [/Use this draft as a reviewer prompt; do not treat it as an approved finding until it passes the review gate\./,
    "将该草稿作为审核提示使用；在通过审核关口前，不要把它当作已批准发现。"],
  [/Conclusions are based solely on the approved graph and controlled aggregation; performance targets, utilization, profitability, or satisfaction data are not included\./,
    "结论仅基于已批准图谱和受控聚合；不包含绩效目标、利用率、利润率或满意度数据。"],
  [/Conclusions are based solely on the approved graph and controlled aggregation; external benchmarks, thresholds, and unapproved evidence are not included\./,
    "结论仅基于已批准图谱和受控聚合；不包含外部基准、阈值或未批准证据。"],
  [/Conclusions are based solely on the approved graph; external benchmarks, thresholds, and unapproved evidence are not included\./,
    "结论仅基于已批准图谱；不包含外部基准、阈值或未批准证据。"],
  [/Profile based on employees source table and approved graph controlled aggregation\./,
    "画像基于 employees 源表和已批准图谱的受控聚合。"],
  [/Rankings reflect a current snapshot — no time-series trends or external benchmarks\./,
    "排名反映当前快照；不包含时间序列趋势或外部基准。"],
  [/How do (.+)'s relationship patterns change over time\?/,
    "$1 的关系模式随时间如何变化？"],
  [/How does (.+) compare to typical Employee\(s\) in the same segment\?/,
    "$1 与同分组典型 Employee 相比如何？"],
  [/Are there anomalous patterns or potential risks\?/,
    "是否存在异常模式或潜在风险？"],
];

function resultTextRX(value, language) {
  const text = value == null ? "" : String(value);
  if (!isZhRX(language)) return text;
  if (RESULT_TITLE_ZH_RX[text]) return RESULT_TITLE_ZH_RX[text];
  for (const [pattern, replacement] of RESULT_TEXT_ZH_RX) {
    if (pattern.test(text)) return expandCountryCodesRX(text.replace(pattern, replacement), language);
  }
  return expandCountryCodesRX(text, language);
}

function expandCountryCodesRX(text, language) {
  if (!isZhRX(language) || text == null) return text;
  if (typeof displayCountryCodesUI === "function") return displayCountryCodesUI(text, language);
  const names = {
    ARE: "United Arab Emirates",
    CHN: "China",
    GMB: "Gambia",
    IND: "India",
    IRN: "Iran",
    JPN: "Japan",
    KOR: "South Korea",
    SAU: "Saudi Arabia",
    USA: "United States",
  };
  return String(text).replace(/\b(ARE|CHN|GMB|IND|IRN|JPN|KOR|SAU|USA)\b/g, code => names[code] ? `${names[code]} (${code})` : code);
}

function resultListRX(values, language) {
  return (values || []).map(v => resultTextRX(v, language));
}

function questionTextRX(value, language) {
  const text = value == null ? "" : String(value);
  if (!isZhRX(language)) return text;
  const replacements = [
    [/^Select a chokepoint, country, dependency, or risk result to analyze propagation risk\.$/, "选择咽喉点、国家、依赖关系或风险结果来分析传播风险。"],
    [/^Select a transaction, account, card, or merchant to analyze fraud risk\.$/, "选择交易、账户、卡或商户来分析欺诈风险。"],
    [/^Select a center node to ask a scoped question\.$/, "选择中心节点来提出范围问题。"],
    [/^Which countries are most exposed to (.+)\?$/, "哪些国家最暴露于 $1？"],
    [/^Which chokepoint dependencies create the highest risk for (.+)\?$/, "$1 的哪些咽喉点依赖带来最高风险？"],
    [/^Explain the risk path for (.+)$/, "解释 $1 的风险路径"],
    [/^What evidence supports this maritime risk signal for (.+)\?$/, "哪些证据支持 $1 的海运风险信号？"],
    [/^Find maritime chokepoint risk findings for (.+)$/, "发现 $1 的海运咽喉点风险发现"],
    [/^Show the hazard -> chokepoint -> country -> risk metric path for (.+)$/, "展示 $1 的 风险因子 -> 咽喉点 -> 国家 -> 风险指标 路径"],
    [/^Which dependent countries or chokepoints should be prioritized from (.+)\?$/, "基于 $1，应优先复核哪些依赖国家或咽喉点？"],
    [/^What action should be created from (.+)'s maritime risk evidence\?$/, "应基于 $1 的海运风险证据创建什么行动？"],
    [/^What evidence supports the risk propagation path for (.+)\?$/, "哪些证据支持 $1 的风险传播路径？"],
    [/^Which downstream countries or trade metrics are affected by (.+)\?$/, "$1 影响哪些下游国家或贸易指标？"],
    [/^Which (.+) produce the strongest multi-hop risk chain\?$/, "哪些 $1 产生最强多跳风险链？"],
    [/^Explain fraud risk signals for (.+)$/, "解释 $1 的欺诈风险信号"],
    [/^Summarize fraud exposure and suspicious activity for (.+)$/, "总结 $1 的欺诈暴露和可疑活动"],
    [/^Review verification, channel, and merchant risk signals for (.+)$/, "复核 $1 的验证、渠道和商户风险信号"],
    [/^Which fraud patterns are concentrated around (.+)\?$/, "$1 周围集中出现哪些欺诈模式？"],
    [/^Find high-value fraud risk patterns for (.+)$/, "发现 $1 的高价值欺诈风险模式"],
    [/^What evidence supports this fraud-risk interpretation for (.+)\?$/, "哪些证据支持 $1 的欺诈风险解释？"],
    [/^Which merchant\/channel\/POS signals explain risk for (.+)\?$/, "哪些商户/渠道/POS 信号解释 $1 的风险？"],
    [/^What follow-up action should an analyst take for (.+)\?$/, "分析师应对 $1 采取什么后续行动？"],
    [/^What evidence supports the risk profile for (.+)\?$/, "哪些证据支持 $1 的风险画像？"],
    [/^Which transaction patterns around (.+) need review\?$/, "$1 周边哪些交易模式需要复核？"],
    [/^What action should be created from (.+)'s risk signals\?$/, "应基于 $1 的风险信号创建什么行动？"],
    [/^Which (.+) should Autopilot investigate next\?$/, "Autopilot 接下来应调查哪些 $1？"],
    [/^Give a summary of (.+)$/, "总结 $1"],
    [/^Which (.+)s have the highest activity\?$/, "哪些 $1 活跃度最高？"],
    [/^(.+) — (.+)$/, "$1 — $2"],
  ];
  for (const [pattern, replacement] of replacements) {
    if (pattern.test(text)) return expandCountryCodesRX(text.replace(pattern, replacement), language);
  }
  return expandCountryCodesRX(text, language);
}

function evidenceKindLabelRX(kind, language) {
  const labels = {
    aggregate: "聚合指标",
    volume: "交易量",
    cluster: "重复簇",
    example: "样例",
    privacy_boundary: "隐私边界",
    hazard: "风险因子",
    chokepoint: "咽喉点",
    dependent_country: "依赖国家",
    dependent_countries: "依赖国家",
    trade_metric: "贸易指标",
    risk_metric: "风险指标",
    recommended_action: "建议行动",
    graph_path: "图路径",
    join: "关联证据",
    fact: "事实",
    hypothesis: "假设",
    conflict: "冲突",
    missing: "缺失",
  };
  if (!isZhRX(language)) return kind || "evidence";
  return labels[kind] || kind || "证据";
}

function statusTextRX(status, language) {
  const text = status == null ? "" : String(status);
  if (!isZhRX(language)) return text || "—";
  const map = {
    active: "进行中",
    approved: "已批准",
    blocked: "已阻塞",
    closed: "已关闭",
    completed: "已完成",
    draft: "草稿",
    failed: "失败",
    needs_more_evidence: "需更多证据",
    pending: "等待中",
    proposed: "待审核",
    pruned: "已剪枝",
    rejected: "已拒绝",
    running: "运行中",
    stale: "已过期",
    superseded: "已替代",
  };
  return map[text.toLowerCase()] || text || "—";
}

function evidenceFilterLabelRX(key, language) {
  const labels = {
    all: ["All", "全部"],
    fact: ["Fact", "事实"],
    hypothesis: ["Hypothesis", "假设"],
    conflict: ["Conflict", "冲突"],
    missing: ["Missing", "缺失"],
  };
  const pair = labels[key] || [key, key];
  return tRX(language, pair[0], pair[1]);
}

function structuredTitleRX(finding, language) {
  if (!isZhRX(language) || !finding || !finding.structured_answer) return null;
  const s = finding.structured_answer;
  const m = s.metrics || {};
  if (m.object_type !== "Employee") return resultTextRX(s.title || finding.title, language);
  const name = m.label || s.metrics?.name || "Employee";
  const ranking = (m.rankings || [])[0] || {};
  const valueAgg = (m.value_aggregations || [])[0] || {};
  const levelZh = ranking.level === "high" ? "高活跃" : ranking.level === "low" ? "低活跃" : "中等活跃";
  const orders = ranking.my_count || m.edge_count || m.neighbor_count || 0;
  const rank = ranking.rank && ranking.total_peers ? `#${ranking.rank}/${ranking.total_peers}` : "未排名";
  const value = valueAgg.my_value != null ? Number(valueAgg.my_value).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
  const share = valueAgg.value_share != null ? `${(Number(valueAgg.value_share) * 100).toFixed(1)}%` : "—";
  return `${name} 业务画像：${orders} 单（${rank}，${levelZh}），价值 ${value}（占比 ${share}）`;
}

function structuredEmployeeSummaryRX(finding, language) {
  if (!finding || !finding.structured_answer || !finding.structured_answer.metrics) {
    return finding ? finding.conclusion : "";
  }
  const s = finding.structured_answer;
  const m = s.metrics || {};
  if (m.object_type !== "Employee") {
    return s.profile_summary || finding.conclusion || "";
  }
  if (isZhRX(language) && m.object_type === "Employee") {
    const name = m.label || m.name || `Employee:${m.instance_id || m.employee_id || ""}`;
    const facts = s.key_facts || [];
    const attrs = facts.find(f => /attributes/i.test(f.label || "")) || {};
    const manager = facts.find(f => /reportsTo/i.test(f.label || "")) || {};
    const role = String(attrs.value || "").match(/title: ([^;]+)/)?.[1] || m.title || "未标注职位";
    const managerName = String(manager.value || "").replace(/\s*\(Employee:[^)]+\)/, "") || "未标注上级";
    const ranking = (m.rankings || [])[0] || {};
    const valueAgg = (m.value_aggregations || [])[0] || {};
    const linkStats = (m.link_stats || [])[0] || {};
    const orders = ranking.my_count || linkStats.row_count || m.edge_count || 0;
    const peers = ranking.total_peers || "—";
    const rank = ranking.rank || "—";
    const avg = ranking.avg != null ? Number(ranking.avg).toFixed(1) : "—";
    const multiplier = ranking.avg ? (Number(orders) / Number(ranking.avg)).toFixed(1) : "—";
    const levelZh = ranking.level === "high" ? "高活跃 Employee" : ranking.level === "low" ? "低活跃 Employee" : "中等活跃 Employee";
    const value = valueAgg.my_value != null ? Number(valueAgg.my_value).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
    const total = valueAgg.total_value != null ? Number(valueAgg.total_value).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—";
    const share = valueAgg.value_share != null ? `${(Number(valueAgg.value_share) * 100).toFixed(1)}%` : "—";
    const valueRank = valueAgg.value_rank && valueAgg.value_total_peers ? `#${valueAgg.value_rank}/${valueAgg.value_total_peers}` : "未排名";
    const categories = (valueAgg.category_breakdown || []).slice(0, 3).map(c => c.label).filter(Boolean).join("、") || "未形成品类聚合";
    const years = (linkStats.yearly || []).filter(y => y.year != null);
    const firstYear = years[0];
    const lastYear = years[years.length - 1];
    const peak = years.length ? years.reduce((best, item) => Number(item.count) > Number(best.count) ? item : best, years[0]) : null;
    const trend = firstYear && lastYear && peak
      ? `活动从 ${firstYear.count}（${firstYear.year}）到 ${lastYear.count}（${lastYear.year}），${peak.year} 年达到峰值 ${peak.count}。`
      : "当前证据不足以形成时间趋势。";
    const customers = linkStats.distinct_counterparties?.customer || m.customer_count || 0;
    const topItems = linkStats.top_counterparties?.customer?.items || [];
    const topMax = topItems.length && orders ? Math.max(...topItems.map(i => Number(i.count || 0))) : 0;
    const topShare = topMax && orders ? `${((topMax / Number(orders)) * 100).toFixed(1)}%` : "—";
    return `${name}（${role}，向 ${managerName} 汇报）是${levelZh}：处理 ${orders} 单，在 ${peers} 名同类中排名 #${rank}，约为平均值 ${avg} 的 ${multiplier} 倍。收入贡献为 ${value}（占总计 ${total} 的 ${share}），按价值排名 ${valueRank}。收入主要分布在 ${categories}。${trend} 活动覆盖 ${customers} 个客户，最大单一客户占比约 ${topShare}，客户分布较分散。`;
  }
  const name = m.name || `Employee:${m.employee_id || ""}`;
  const orderCount = Number(m.order_count || 0);
  const totalOrders = Number(m.total_orders || 0);
  const orderShare = asNumberRX(m.order_share_percent);
  const rank = m.order_rank && m.employee_count ? `${m.order_rank}/${m.employee_count}` : "n/a";
  const customerCount = Number(m.customer_count || 0);
  const location = m.location || "unknown location";
  const role = m.title || "untitled role";
  const load = rank !== "n/a" && Number(m.order_rank) >= Math.ceil(Number(m.employee_count || 0) * 0.7)
    ? "low order load"
    : "moderate/high order load";
  return `${name} is ${role} in ${location}. In the approved Northwind graph and controlled aggregates, this profile shows ${load}: ${orderCount} handled orders out of ${totalOrders} (${orderShare == null ? "n/a" : orderShare.toFixed(1) + "%"}), order rank ${rank}, and coverage across ${customerCount} customers. Current evidence does not by itself prove an abnormal workload; targets, time allocation, profitability, and customer quality would be needed for an operational judgment.`;
}

function displayFindingConclusionRX(finding, language) {
  if (!finding) return "";
  const structured = structuredEmployeeSummaryRX(finding, language);
  return resultTextRX(structured || finding.conclusion, language);
}

function displayFindingTitleRX(finding, language) {
  return structuredTitleRX(finding, language) || resultTextRX(finding && finding.title, language);
}

function displayCandidateTitleRX(candidate, language) {
  return resultTextRX(candidate && candidate.title, language);
}

function displayCandidateConclusionRX(candidate, language) {
  return resultTextRX(candidate && candidate.conclusion, language);
}

function displayActionTextRX(value, language) {
  const text = value == null ? "" : String(value);
  if (!isZhRX(language)) return text;
  const map = {
    "Break down by merchant category and transaction amount decile.": "按商户类别和交易金额分位进一步拆分。",
    "Prioritize mismatch transactions with high amount or card-not-present channel.": "优先复核高金额或非面对面渠道中的验证不匹配交易。",
    "Review ingestion completeness and POS-mode normalization rules.": "复核数据摄取完整性和 POS 模式归一化规则。",
    "Create category-specific review queues for high-rate and high-volume intersections.": "为高欺诈率与高交易量交叉分组创建类别专项复核队列。",
    "Separate reversals, merchant retries, and high-confidence multi-swipe clusters.": "区分冲正、商户重试和高置信多次刷卡簇。",
    "Open a country/chokepoint dependency review and compare alternate maritime routes.": "创建国家/咽喉点依赖复核，并比较替代海运路线。",
    "Rank chokepoint review by hazard-adjusted trade-at-risk, not volume alone.": "按风险调整后的贸易风险排序咽喉点复核优先级，而不是仅按吞吐量排序。",
    "Create a Bab el-Mandeb review case for the top exposed countries and attach live event enrichment when available.": "为最高暴露国家创建 Bab el-Mandeb 复核事项，并在可用时附加实时事件信息增益证据。",
    "Use this draft as a reviewer prompt; do not treat it as an approved finding until it passes the review gate.": "将该草稿作为审核提示使用；在通过审核关口前，不要把它当作已批准发现。",
  };
  return map[text] || resultTextRX(text, language);
}

function reasoningResponseV1RX(finding) {
  if (!finding) return null;
  const direct = finding.structured_response;
  const action = finding.recommended_action && finding.recommended_action.structured_response;
  const structured = direct || action;
  return structured && structured.schema_version === "reasoning_response_v1" ? structured : null;
}

function compactMetricRX(value) {
  const n = asNumberRX(value);
  if (n == null) return value == null ? "—" : String(value);
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function ReasoningResponseV1View({ response, finding, language }) {
  if (!response) return null;
  const answer = response.answer || {};
  const graph = response.graph_context || {};
  const degree = graph.degree || {};
  const rankedPaths = response.ranked_paths || [];
  const secondHop = response.second_hop_paths || [];
  const keyFacts = response.key_facts || [];
  const interpretations = response.business_interpretation || [];
  const limits = response.limits || [];
  const nextQuestions = response.next_questions || [];
  const relatedEdges = graph.related_edges || [];
  const sourceEdges = graph.source_backed_related_edges || [];
  const sectionStyle = { paddingTop: 12, borderTop: "1px solid var(--line)" };
  const labelStyle = { fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--accent)", letterSpacing: "0.08em", textTransform: "uppercase", marginBottom: 6 };
  const listStyle = { margin: 0, paddingLeft: 18, color: "var(--text-dim)", fontSize: 13, lineHeight: 1.6 };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 15, color: "var(--text)", lineHeight: 1.6 }}>
        {resultTextRX(answer.conclusion || finding?.conclusion || "", language)}
      </div>

      <div style={sectionStyle}>
        <div style={labelStyle}>{tRX(language, "Graph degree and scope", "图谱度数与范围")}</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 }}>
          {[
            [tRX(language, "Visible degree", "可见度数"), degree.visible_graph_center ?? degree.center],
            [tRX(language, "Source rows", "源数据行"), degree.source_key_row_degree],
            [tRX(language, "Ranked paths", "排序路径"), degree.source_key_top_path_count],
            [tRX(language, "Related edges", "关联边"), relatedEdges.length || sourceEdges.length],
          ].map(([k, v]) => (
            <div key={k} style={{ border: "1px solid var(--line)", padding: "8px 10px", background: "var(--surface)" }}>
              <div style={{ fontSize: 10, color: "var(--muted)", fontFamily: "var(--font-mono)", textTransform: "uppercase" }}>{k}</div>
              <div style={{ fontSize: 16, color: "var(--text)", fontFamily: "var(--font-mono)" }}>{compactMetricRX(v)}</div>
            </div>
          ))}
        </div>
      </div>

      {rankedPaths.length > 0 && (
        <div style={sectionStyle}>
          <div style={labelStyle}>{tRX(language, "Ranked exposure paths", "排序暴露路径")}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {rankedPaths.slice(0, 8).map((p, i) => (
              <div key={`${p.label || i}-${p.metric || ""}`} style={{ display: "grid", gridTemplateColumns: "34px minmax(0, 1fr) auto", gap: 8, alignItems: "center", fontSize: 12, color: "var(--text-dim)" }}>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>#{p.rank || i + 1}</span>
                <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text)" }}>{resultTextRX(p.label || "—", language)}</span>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--muted)" }}>{p.metric || "metric"} {compactMetricRX(p.metric_value)} · {p.row_count || 0} rows</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {secondHop.length > 0 && (
        <div style={sectionStyle}>
          <div style={labelStyle}>{tRX(language, "Depth context", "深度上下文")}</div>
          <ul style={listStyle}>
            {secondHop.slice(0, 5).map((p, i) => (
              <li key={`${p.label || i}-hop`}>
                <span style={{ color: "var(--text)" }}>{resultTextRX(p.label || "—", language)}</span>
                {" → "}
                {(p.top_peers || []).slice(0, 6).map(peer => peer.key || peer.label || peer.id).filter(Boolean).join(", ") || "—"}
              </li>
            ))}
          </ul>
        </div>
      )}

      {keyFacts.length > 0 && (
        <div style={sectionStyle}>
          <div style={labelStyle}>{tRX(language, "Key facts", "关键事实")}</div>
          <ul style={listStyle}>
            {keyFacts.slice(0, 8).map((fact, i) => (
              <li key={`${fact.label || i}-fact`}>
                <span style={{ color: "var(--text)" }}>{resultTextRX(fact.label || "Fact", language)}</span>
                {": "}
                {resultTextRX(fact.value || fact.summary || "", language)}
                {fact.source_ref && <span style={{ color: "var(--muted)" }}> · {fact.source_ref}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {interpretations.length > 0 && (
        <div style={sectionStyle}>
          <div style={labelStyle}>{tRX(language, "Interpretation", "解读")}</div>
          <ul style={listStyle}>
            {resultListRX(interpretations.slice(0, 5), language).map((text, i) => <li key={`${i}-interp`}>{text}</li>)}
          </ul>
        </div>
      )}

      {limits.length > 0 && (
        <div style={sectionStyle}>
          <div style={{ ...labelStyle, color: "var(--rejected)" }}>{tRX(language, "Limits", "边界")}</div>
          <ul style={listStyle}>
            {resultListRX(limits, language).map((text, i) => <li key={`${i}-limit`}>{text}</li>)}
          </ul>
        </div>
      )}

      {nextQuestions.length > 0 && (
        <div style={sectionStyle}>
          <div style={labelStyle}>{tRX(language, "Next questions", "后续问题")}</div>
          <ul style={listStyle}>
            {resultListRX(nextQuestions.slice(0, 5), language).map((text, i) => <li key={`${i}-next`}>{text}</li>)}
          </ul>
        </div>
      )}

      <div style={{ ...sectionStyle, display: "flex", gap: 8, alignItems: "center" }}>
        <div className="eyebrow" style={{ color: "var(--changes)" }}>{tRX(language, "Canonical boundary", "正式边界")}</div>
        <div style={{ fontSize: 11, color: "var(--muted)" }}>
          {tRX(language,
            "This response is draft-only; approving it cites the finding layer and does not modify canonical ontology or formal graph.",
            "该响应仅为草稿；批准后只引用到发现层，不会修改正式本体或正式图谱。")}
        </div>
      </div>
    </div>
  );
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

function FraudFindingSummary({ finding, language }) {
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
      <div className="eyebrow">{tRX(language, "Fraud risk summary", "欺诈风险摘要")}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
        <MetricTile label={tRX(language, "Rows", "行数")} value={counts.rows_total != null ? Number(counts.rows_total).toLocaleString() : "—"} />
        <MetricTile label={tRX(language, "Fraud rate", "欺诈率")} value={pctRX(counts.fraud_rate)} tone="var(--rejected)" />
        <MetricTile label={tRX(language, "Fraud tx", "欺诈交易")} value={counts.fraud_count != null ? Number(counts.fraud_count).toLocaleString() : "—"} />
        <MetricTile label={tRX(language, "Fraud avg amount", "欺诈平均金额")} value={moneyRX(amounts.avg_fraud_amount)} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
        <MetricTile label={tRX(language, "Card-not-present", "非面对面交易")} value={pctRX(flags.fraud_cnp)} sub={`${Number(flags.cnp_count || 0).toLocaleString()} tx`} />
        <MetricTile label="cvvMatch=false" value={pctRX(flags.fraud_cvv_mismatch)} sub={`${Number(flags.cvv_mismatch_count || 0).toLocaleString()} tx`} />
        <MetricTile label={tRX(language, "POS entry missing", "POS 录入缺失")} value={pctRX(posMissing && posMissing.fraud_rate)} sub={`${Number(posMissing && posMissing.cnt || 0).toLocaleString()} tx`} />
        <MetricTile label={tRX(language, "Duplicate samples", "重复样例")} value={(payload.duplicate_pattern || []).length ? String((payload.duplicate_pattern || []).length) : "—"} sub={tRX(language, "same account/merchant/amount/day", "同账户/商户/金额/日期")} />
      </div>
      {categories.length > 0 && (
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>{tRX(language, "High-risk merchant categories", "高风险商户类别")}</div>
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
          <div className="eyebrow" style={{ marginBottom: 8 }}>{tRX(language, "High-risk examples", "高风险样例")}</div>
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
        {tRX(language,
          "Evidence boundary: deterministic SQL aggregates over the safe transaction view; raw CVV values are not required for this reasoning surface.",
          "证据边界：基于安全交易视图的确定性 SQL 聚合；该推理界面不需要原始 CVV 值。")}
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

function autopilotObjectiveForTenantRX(tenantId, language) {
  if (tenantId === "maritime-risk") return tRX(language, "Find graph reasoning findings for maritime chokepoint risk", "发现海运咽喉点风险的图推理发现");
  if (tenantId === "creditcardfraud") return tRX(language, "Find high-value fraud risk findings", "发现高价值欺诈风险发现");
  return tRX(language, "Find high-value reasoning findings", "发现高价值推理发现");
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

function Reasoning({ tenant, language }) {
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
  const [deletedTaskKeys, setDeletedTaskKeys] = useStateRX(new Set());
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
    setAutopilotObjective(autopilotObjectiveForTenantRX(tid, language));
    setAutopilotSelectedKey(null);
  }, [tenant ? tenant.id : "default", language]);
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
          setQuestion(questionTextRX(tenantEmptyQuestionRX(tid), language));
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
        setQuestion(questionTextRX(defaultQuestionForTenantRX(tid, firstType, nextLabel, nextNode), language));
        setScopeBootstrapKey(bootstrapKey);
      } catch (_) {
        if (!alive) return;
        setScopeTypes([]);
        setCenterNode("");
        setQuestion(questionTextRX(tenantEmptyQuestionRX(tenant ? tenant.id : "default"), language));
      }
    })();
    return () => { alive = false; };
  }, [tenant ? tenant.id : "default", language]);
  useEffectRX(() => {
    if (isZhRX(language)) setQuestion(q => questionTextRX(q, language));
  }, [language]);
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
      if (k && deletedTaskKeys.has(k)) continue;
      if (k && seen.has(k)) continue;
      if (k) seen.add(k);
      out.push(t);
    }
    return out;
  }, [localTasks, tasksQ.data, deletedTaskKeys]);

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
    { enabled: !!selectedKey && allTasks.some(t => t.canonical_key === selectedKey) }
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
      setActionMsg({ kind: "ok", msg: tRX(language, "Maritime-risk playbook completed · ", "Maritime-risk playbook 已完成 · ") + (key || "") });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg({ kind: "err", msg: err.message || String(err) });
    } finally {
      setAutopilotPlaybookRunning(false);
    }
  }

  async function deleteTask(taskKey) {
    if (!confirm(tRX(language, "Delete this task? This cannot be undone.", "删除这个任务？此操作无法撤销。"))) return;
    try {
      try { await window.AL_API.closeTask(taskKey, tenant.id); } catch (_) {}
      await window.AL_API.deleteTask(taskKey, tenant.id);
      setDeletedTaskKeys(prev => {
        const next = new Set(prev);
        next.add(taskKey);
        return next;
      });
      setLocalTasks(prev => prev.filter(t => t.canonical_key !== taskKey));
      if (selectedKey === taskKey) setSelectedKey(null);
      setActionMsg({ kind: "ok", msg: tRX(language, "Task deleted.", "任务已删除。") });
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
          <div className={"tab" + (activeTab === "mine"    ? " active" : "")} onClick={() => setActiveTab("mine")}>{tRX(language, "My Questions", "我的问题")} <span className="ct">{counts.mine}</span></div>
          <div className={"tab" + (activeTab === "all"     ? " active" : "")} onClick={() => setActiveTab("all")}>{tRX(language, "Reasoning Process", "推理流程")} <span className="ct">{counts.all}</span></div>
          <div className={"tab" + (activeTab === "graph"   ? " active" : "")} onClick={() => setActiveTab("graph")}>{tRX(language, "From Graph", "来自图谱")} <span className="ct">{counts.graph}</span></div>
          <div className={"tab" + (activeTab === "autopilot" ? " active" : "")} onClick={() => setActiveTab("autopilot")}>{tRX(language, "Autopilot", "自动推理")} <span className="ct">{counts.autopilot}</span></div>
        </div>
        <div className="spacer" />
        <button className="tool" onClick={() => setCleanupModal(true)}>{tRX(language, "Clean up", "清理")}</button>
        {isMock  && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tRX(language, "Mock fallback", "模拟回退")}</span>}
        {isStale && <span className="pill changes" style={{ marginRight: 8 }}><span className="dot" />{tRX(language, "Stale · last fetch failed", "数据陈旧 · 最近拉取失败")}</span>}
        {tasksQ.loading && tasksQ.data && <span className="pill"><span className="dot" />{tRX(language, "Refreshing…", "刷新中…")}</span>}
        <button className="tool" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>⟲ {tRX(language, "Refresh", "刷新")}</button>
        {activeTab !== "autopilot" && shouldRerun && (
          <button className="tool" onClick={runTask} disabled={running || !task}
                  title={tRX(language, "Create a new task with the same question and scope, and run it.", "创建同问题和范围的新任务并运行。")}>
            {running ? tRX(language, "Rerunning…", "重新运行中…") : "↻ " + tRX(language, "Rerun (new task)", "重新运行（新任务）")}
          </button>
        )}
        {activeTab !== "autopilot" && !shouldRerun && !finding && !runDone && (
          <button className="tool" onClick={runTask} disabled={running || !task}>{running ? tRX(language, "Running…", "运行中…") : "▶ " + tRX(language, "Run reasoning", "运行推理")}</button>
        )}
        {activeTab !== "autopilot" && task && !shouldRerun && (
          <button className="tool" onClick={stopAndClose}
                  style={{ color: "var(--rejected)" }}
                  title={tRX(language, "Stop the current run (if any) and close this task.", "停止当前运行（如有）并关闭任务。")}>
            ■ {tRX(language, "Stop & close", "停止并关闭")}
          </button>
        )}
        <button className="tool primary" onClick={() => activeTab === "autopilot" ? startAutopilot() : setAskMode(true)}>
          {activeTab === "autopilot" ? "▶ " + tRX(language, "Start Autopilot", "启动 Autopilot") : "+ " + tRX(language, "Ask question", "提问")}
        </button>
      </div>

      <div className="wb">
        {/* ============ LEFT — task list ============ */}
        <div className="col">
          <div style={{ padding: "var(--pad-3) var(--pad-4)", borderBottom: "1px solid var(--line)", background: "var(--bg-2)" }}>
            <div className="eyebrow accent">{tRX(language, "Question → Answer → Evidence", "问题 → 答案 → 证据")}</div>
            <div style={{ marginTop: 4, fontSize: 13, color: "var(--text)" }}>
              {activeTab === "autopilot" ? tRX(language, "Autopilot sessions", "Autopilot 会话") : tRX(language, "Reasoning tasks", "推理任务")}
            </div>
            <button className="btn primary" style={{ width: "100%", marginTop: 10 }}
                    onClick={() => activeTab === "autopilot" ? startAutopilot() : setAskMode(true)}>
              {activeTab === "autopilot" ? "▶ " + tRX(language, "Start Autopilot", "启动 Autopilot") : "+ " + tRX(language, "Ask a new question", "新建问题")}
            </button>
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            {activeTab === "autopilot" ? <ApiStatus q={autopilotSessionsQ} what={tRX(language, "autopilot sessions", "Autopilot 会话")} /> : <ApiStatus q={tasksQ} what={tRX(language, "reasoning tasks", "推理任务")} />}
            <div className="artifact-list">
              {activeTab === "autopilot" ? (
                <React.Fragment>
                  {(autopilotSessionsQ.source === "live" || autopilotSessionsQ.source === "mock") && autopilotSessions.length === 0 && (
                    <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center", lineHeight: 1.6 }}>
                      {tRX(language, "No Autopilot sessions yet. Start one to queue hypotheses and collect draft candidate findings.", "暂无 Autopilot 会话。启动后会排队假设并收集候选发现草稿。")}
                    </div>
                  )}
                  {autopilotSessions.map(s => (
                    <div key={s.session_key}
                         className={"artifact-row proposed" + (s.session_key === autopilotSelectedKey ? " selected" : "")}
                         onClick={() => setAutopilotSelectedKey(s.session_key)}>
                      <div className="ar-bar" />
                      <div className="ar-main">
                        <div className="ar-top">
                          <span className="type">Autopilot</span>
                          <span>·</span>
                          <span className="key" style={{ color: "var(--text)" }}>{s.objective}</span>
                        </div>
                        <div className="ar-meta">
                          <span>{s.scope?.table || s.scope?.tenant || tRX(language, "tenant scope", "租户范围")}</span>
                          <span>{fmtTime(s.updated_at || s.created_at)}</span>
                        </div>
                      </div>
                      <div className="ar-right">
                        <Pill kind="proposed">{statusTextRX(s.status || "draft", language)}</Pill>
                      </div>
                    </div>
                  ))}
                </React.Fragment>
              ) : (
                <React.Fragment>
              {(tasksQ.source === "live" || tasksQ.source === "mock") && tasks.length === 0 && (
                <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", textAlign: "center", lineHeight: 1.6 }}>
                  {activeTab === "mine"    ? tRX(language, "No questions of yours yet. Click “+ Ask a new question” above to start.", "你还没有问题。点击上方“+ 新建问题”开始。") :
                   activeTab === "graph"   ? tRX(language, "No graph-derived reasoning tasks here.", "这里暂无来自图谱的推理任务。") :
                                             tRX(language, "No active reasoning tasks. Click “+ Ask a new question” above.", "暂无活跃推理任务。点击上方“+ 新建问题”。")}
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
                      <span className="type">{tRX(language, "TASK", "任务")}</span>
                      <span>·</span>
                      <span className="key" style={{ color: "var(--text)" }}>{t.question || t.name || t.canonical_key}</span>
                      {ts.isRunning && !ts.isStale && (
                        <span style={{ marginLeft: "auto", color: "var(--accent)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                          <span style={{ width: 6, height: 6, background: "var(--accent)", display: "inline-block", animation: "pulse 1s ease-in-out infinite" }} />
                          {tRX(language, "running", "运行中")}
                        </span>
                      )}
                      {ts.isStale && (
                        <span style={{ marginLeft: "auto", color: "var(--rejected)" }} title={tRX(language, "Active but no update for ", "任务仍活跃但已 ") + ts.ageLbl + tRX(language, " — likely orphaned", " 未更新，可能已孤立")}>
                          ⚠ {tRX(language, "stale", "陈旧")} {ts.ageLbl}
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
                    <Pill kind={statusToPill[t.status] || "proposed"}>{statusTextRX(t.status, language)}</Pill>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      {ts.isRunning && !ts.isStale && (
                        <button className="btn xs ghost" title={tRX(language, "Stop task", "停止任务")}
                                onClick={e => { e.stopPropagation(); setSelectedKey(t.canonical_key); stopAndClose(); }}
                                style={{ padding: "4px 7px", fontSize: 12, color: "var(--rejected)", border: "1px solid oklch(0.66 0.18 25 / 0.3)", lineHeight: 1, borderRadius: 4 }}>
                          ■
                        </button>
                      )}
                      {!ts.isRunning && (
                        <React.Fragment>
                          <button className="btn xs ghost" title={tRX(language, "Rerun (new task)", "重新运行（新任务）")}
                                  onClick={e => { e.stopPropagation(); setSelectedKey(t.canonical_key); setTimeout(runTask, 50); }}
                                  style={{ padding: "4px 7px", color: "var(--accent)", border: "1px solid var(--line)", lineHeight: 1, borderRadius: 4 }}>
                            <span style={{ display: "inline-flex", width: 16, height: 16 }} dangerouslySetInnerHTML={{ __html: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8a5.5 5.5 0 0 1 9.3-4"/><path d="M13.5 8a5.5 5.5 0 0 1-9.3 4"/><path d="M11.8 1.5V4h-2.5"/><path d="M4.2 14.5V12h2.5"/></svg>' }} />
                          </button>
                          <button className="btn xs ghost" title={tRX(language, "Delete task", "删除任务")}
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
              language={language}
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
              language={language}
            />
          ) : !task ? (
            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 14, color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              <div style={{ fontSize: 13, color: "var(--text-dim)" }}>
                {activeTab === "mine"    ? tRX(language, "You haven't asked any questions yet.", "你还没有提出问题。") :
                 activeTab === "graph"   ? tRX(language, "No graph-derived reasoning tasks in this scope.", "此范围内暂无来自图谱的推理任务。") :
                                           tRX(language, "Select a reasoning task from the left, or ask a new question.", "从左侧选择推理任务，或新建问题。")}
              </div>
              <button className="btn primary" onClick={() => setAskMode(true)}>+ {tRX(language, "Ask a new question", "新建问题")}</button>
            </div>
          ) : (
            <>
              <div className="art-header">
                <div className="crumb">
                  <span className="type">{tRX(language, "Reasoning Task", "推理任务")}</span>
                  <span className="sep">/</span>
                  <span>{task.canonical_key}</span>
                  {task.center_node && <><span className="sep">·</span><span>{tRX(language, "scope", "范围")} {task.center_node} · d{task.depth || 1} · n{task.limit || 200}</span></>}
                  <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                    {isLoadingDetail && (
                      <span className="pill" style={{ fontSize: 9 }}>
                        <span className="dot" style={{ background: "var(--accent)" }} />{tRX(language, "Loading detail…", "加载详情…")}
                      </span>
                    )}
                    {showRunning && (
                      <span className="pill changes" style={{ fontSize: 9 }}>
                        <span className="dot" style={{ animation: "pulse 1s ease-in-out infinite" }} />
                        {isTaskRunning ? tRX(language, "Polling · ", "轮询 · ") + pollTick : tRX(language, "Running…", "运行中…")}
                      </span>
                    )}
                  </span>
                </div>
                <h1>{finding ? displayFindingTitleRX(finding, language) : (task.name || task.question || "Untitled reasoning task")}</h1>
                {task.blocker && (
                  <p className="desc" style={{ color: "var(--rejected)" }}>⚠ {task.blocker}</p>
                )}
                <div className="row">
                  <div className="stat">
                    <span className="label">{tRX(language, "Center", "中心")}</span>
                    <span className="val mono">{task.center_node || "—"}</span>
                  </div>
                  <div className="stat">
                    <span className="label">{tRX(language, "Depth / limit", "深度 / 上限")}</span>
                    <span className="val mono">{task.depth || 1} · {task.limit || 200}</span>
                  </div>
                  <div className="stat">
                    <span className="label">{tRX(language, "Source", "来源")}</span>
                    <span className="val mono">{task.source || "manual"}</span>
                  </div>
                  <div className="stat">
                    <span className="label">{tRX(language, "Evidence", "证据")}</span>
                    <span className="val mono">{evidence.length} {tRX(language, "items", "项")}</span>
                  </div>
                  <div className="stat">
                    <span className="label">{tRX(language, "Canonical write", "正式写入")}</span>
                    <span className="val" style={{ color: "var(--changes)" }}>{tRX(language, "blocked · draft only", "已阻断 · 仅草稿")}</span>
                  </div>
                  {task.id != null && (
                    <div className="stat">
                      <span className="label">{tRX(language, "Run ID", "运行 ID")}</span>
                      <span className="val mono">{task.id}</span>
                    </div>
                  )}
                  <div className="stat">
                    <span className="label">{tRX(language, "Created", "创建时间")}</span>
                    <span className="val mono">{fmtTime(task.created_at)}</span>
                  </div>
                  <div className="stat">
                    <span className="label">{tRX(language, "Completed", "完成时间")}</span>
                    <span className="val mono">{isTerminal ? fmtTime(task.updated_at) : "—"}</span>
                  </div>
                </div>
              </div>

              <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
                {/* Conclusion */}
                <Panel eyebrow={tRX(language, "Current answer", "当前答案")} title={tRX(language, "Conclusion", "结论")}
                       count={showRunning ? (isTaskRunning ? `${tRX(language, "polling", "轮询")} · ${pollTick}` : tRX(language, "running…", "运行中…")) : isStaleActive ? tRX(language, "stale", "已陈旧") : finding ? statusTextRX(finding.status || "draft", language) : tRX(language, "no answer", "暂无答案")}
                       actions={shouldRerun ? (
                         <button className="btn xs" onClick={runTask} disabled={running}
                                 title={tRX(language, "Create a new task with same question/scope", "创建同问题/范围的新任务")}>
                           {running ? tRX(language, "Rerunning…", "重新运行中…") : "↻ " + tRX(language, "Rerun (new task)", "重新运行（新任务）")}
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
                          ⚠ {tRX(language, "Likely orphaned", "可能已孤立")}
                        </div>
                        <div style={{ color: "var(--text-dim)", textTransform: "none", letterSpacing: 0 }}>
                          {tRX(language, "Status is", "状态为")} <span style={{ color: "var(--rejected)" }}>{statusTextRX(task.status, language)}</span>{tRX(language, " but the task hasn't been updated for ", "，但任务已 ")}<span style={{ color: "var(--text)" }}>{selectedState.ageLbl}</span>{tRX(language, ". The worker probably crashed or the service was restarted before it could mark this task complete. The backend status is not being actively maintained.", " 未更新。可能是 worker 崩溃或服务重启，后端状态未继续维护。")}
                        </div>
                      </div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", lineHeight: 1.7 }}>
                        <div style={{ color: "var(--accent)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>{tRX(language, "What to do", "处理建议")}</div>
                        <div>·  {tRX(language, "Click", "点击")} <span style={{ color: "var(--text)" }}>↻ {tRX(language, "Rerun reasoning", "重新运行推理")}</span> {tRX(language, "below to start a fresh run.", "启动一次新的运行。")}</div>
                        <div>·  {tRX(language, "Or check the backend worker log for the original failure.", "也可以检查后端 worker 日志确认原始失败。")}</div>
                        <div>·  {tRX(language, "Status will only change if you rerun, or someone manually clears it on the server.", "只有重新运行或人工清理服务端状态后，状态才会变化。")}</div>
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <button className="btn primary" onClick={runTask} disabled={running}>↻ {tRX(language, "Rerun reasoning", "重新运行推理")}</button>
                        <button className="btn ghost" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>{tRX(language, "Refresh once", "刷新一次")}</button>
                      </div>
                    </div>
                  ) : showRunning ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "16px 0" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--accent)", fontFamily: "var(--font-mono)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                        <span style={{ width: 8, height: 8, background: "var(--accent)", display: "inline-block", animation: "pulse 1s ease-in-out infinite" }} />
                        {tRX(language, "Reasoning in progress", "推理进行中")}
                        <span style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 10 }}>
                          {liveTrace.length} {tRX(language, "events", "事件")} · {liveTrace.length > 0 ? "SSE" : tRX(language, "starting…", "启动中…")}
                        </span>
                      </div>
                      <div style={{ color: "var(--muted)", fontSize: 12, lineHeight: 1.55 }}>
                        {tRX(language, "Running scoped reasoning over", "正在针对")} <span style={{ color: "var(--text)" }}>{task.center_node}</span> {tRX(language, "(depth", "执行范围推理（深度")} {task.depth || 1}, {tRX(language, "limit", "上限")} {task.limit || 200}).
                      </div>
                      <TraceLog events={liveTrace} />
                      <div style={{ display: "flex", gap: 8 }}>
                        <button className="btn ghost" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ {tRX(language, "Refresh now", "立即刷新")}</button>
                        {streamRef.current && (
                          <button className="btn ghost" onClick={() => {
                            try { streamRef.current.close(); } catch {}
                            setRunning(false);
                          }}>✕ {tRX(language, "Stop stream", "停止流")}</button>
                        )}
                      </div>
                    </div>
                  ) : finding ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      {(() => {
                        const responseV1 = reasoningResponseV1RX(finding);
                        return responseV1 ? <ReasoningResponseV1View response={responseV1} finding={finding} language={language} /> : null;
                      })()}
                      {backendRunning && (
                        <div style={{
                          display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
                          border: "1px solid var(--accent-line)",
                          background: "var(--accent-bg)",
                          fontFamily: "var(--font-mono)", fontSize: 11,
                          marginBottom: 4,
                        }}>
                          <span style={{ width: 6, height: 6, background: "var(--accent)", display: "inline-block", animation: "pulse 1s ease-in-out infinite" }} />
                          <span style={{ color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10 }}>{tRX(language, "Running on backend", "后端运行中")}</span>
                          <span style={{ color: "var(--muted)" }}>· {tRX(language, "polling", "轮询")} · {pollTick}</span>
                          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                            <button className="btn xs" onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ {tRX(language, "Refresh", "刷新")}</button>
                          </span>
                        </div>
                      )}
                      {!reasoningResponseV1RX(finding) && (
                        <>
                          <div style={{ fontSize: 15, color: "var(--text)", lineHeight: 1.55 }}>
                            {displayFindingConclusionRX(finding, language)}
                          </div>
                          <FraudFindingSummary finding={finding} language={language} />
                        </>
                      )}
                      {finding.action_proposal && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
                          <div className="eyebrow" style={{ marginBottom: 6 }}>{tRX(language, "Proposed action", "建议动作")}</div>
                          <div style={{ fontSize: 13, color: "var(--text-dim)" }}>{displayActionTextRX(finding.action_proposal, language)}</div>
                        </div>
                      )}
                      {finding.counter_evidence && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
                          <div className="eyebrow" style={{ marginBottom: 6, color: "var(--rejected)" }}>{tRX(language, "Counter evidence / limits", "反向证据 / 边界")}</div>
                          <div style={{ fontSize: 13, color: "var(--text-dim)" }}>{Array.isArray(finding.counter_evidence) ? resultListRX(finding.counter_evidence.map(e => e.summary || e), language).join(" · ") : resultTextRX(finding.counter_evidence, language)}</div>
                        </div>
                      )}
                      <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", gap: 8, alignItems: "center" }}>
                        <div className="eyebrow" style={{ color: "var(--changes)" }}>{tRX(language, "Canonical boundary", "正式边界")}</div>
                        <div style={{ fontSize: 11, color: "var(--muted)" }}>
                          {tRX(language,
                            "Approving this finding cites it in the approved-finding layer; it does NOT modify canonical ontology or graph.",
                            "批准该发现只会把它引用到已审核发现层；不会修改正式本体或图谱。")}
                        </div>
                      </div>
                      {shouldRerun && (
                        <div style={{ paddingTop: 12, borderTop: "1px solid var(--line)", display: "flex", gap: 8, alignItems: "center" }}>
                          <button className="btn primary" onClick={runTask} disabled={running}
                                  title={tRX(language, "Create a new task with the same question and scope, and run it.", "创建同问题和范围的新任务并运行。")}>
                            {running ? tRX(language, "Rerunning…", "重新运行中…") : "↻ " + tRX(language, "Rerun (new task)", "重新运行（新任务）")}
                          </button>
                          {isClosed && <span style={{ fontSize: 11, color: "var(--muted)" }}>{tRX(language, "Task is closed — rerun creates a fresh task.", "任务已关闭，重新运行会创建新任务。")}</span>}
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
                          <span style={{ color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.08em", fontSize: 10 }}>{tRX(language, "Running on backend", "后端运行中")}</span>
                          <span style={{ color: "var(--muted)" }}>· {tRX(language, "waiting for result", "等待结果")} · {tRX(language, "polling", "轮询")} · {pollTick}</span>
                          <button className="btn xs" style={{ marginLeft: "auto" }} onClick={() => window.dispatchEvent(new CustomEvent("aletheia:retry"))}>↻ {tRX(language, "Refresh", "刷新")}</button>
                        </div>
                      )}
                      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ color: "var(--dim)", fontFamily: "var(--font-mono)", fontSize: 12 }}>{backendRunning ? tRX(language, "No conclusion yet — task is still running.", "暂无结论，任务仍在运行。") : tRX(language, "No conclusion yet.", "暂无结论。")}</span>
                      {shouldRerun && (
                        <button className="btn primary" onClick={runTask} disabled={running}
                                title={tRX(language, "Create a new task with the same question and scope, and run it.", "创建同问题和范围的新任务并运行。")}>
                          {running ? tRX(language, "Rerunning…", "重新运行中…") : "↻ " + tRX(language, "Rerun (new task)", "重新运行（新任务）")}
                        </button>
                      )}
                      {!shouldRerun && !runDone && !backendRunning && !running && (
                        <button className="btn primary" onClick={runTask}>
                          ▶ {tRX(language, "Run reasoning", "运行推理")}
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
                <Panel eyebrow={tRX(language, "Provenance", "来源")} title={tRX(language, "Evidence chain", "证据链")} count={`${evidence.length} ${tRX(language, "items", "项")}`} nopad
                       actions={
                         <div className="chip-row">
                           {["all", "fact", "hypothesis", "conflict", "missing"].map(k => (
                             <Chip key={k} active={evidenceFilter === k} onClick={() => setEvidenceFilter(k)}
                                   count={k === "all" ? evidence.length : evidence.filter(e => (e.kind || "fact") === k).length}>
                               {evidenceFilterLabelRX(k, language)}
                             </Chip>
                           ))}
                         </div>
                       }>
                  {evidence.length === 0 ? (
                    <div style={{ padding: 24, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                      {tRX(language, "No evidence yet. Run the reasoning to populate.", "暂无证据。运行推理后会生成证据链。")}
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
                            <div className="kind">{ontologyKey ? tRX(language, "ontology basis", "本体依据") : evidenceKindLabelRX(ev.kind, language)}</div>
                            <div className="body-x">
                              <div className="title">{resultTextRX(ev.title, language)}</div>
                              <div className="src">
                                {ontologyKey
                                  ? `${ontologyKey} · ${tRX(language, "compact basis only", "仅紧凑依据")}`
                                  : ev.src}
                              </div>
                            </div>
                            <div className="conf-side">
                              {ontologyKey ? (
                                <a className="btn xs" href={`/?screen=ontology&tenant=${encodeURIComponent(tenant ? tenant.id : "default")}&artifact=${encodeURIComponent(ontologyKey)}`}
                                 title={tRX(language, "Open full ontology governance details in Ontology.", "在 Ontology 中打开完整本体治理详情。")}>
                                  {tRX(language, "View in Ontology", "在 Ontology 中查看")}
                                </a>
                              ) : ev.conf != null ? <><span style={{ color: "var(--text)" }}>{Math.round(ev.conf * 100)}%</span><span style={{ color: "var(--dim)", fontSize: 9, marginTop: 2 }}>{tRX(language, "confidence", "置信度")}</span></> : <span className="faint">—</span>}
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
                         placeholder={tRX(language, "Decision rationale (required for reject / lifecycle changes; optional for approve)…", "决策说明（拒绝/生命周期变更必填；批准可选）…")} />
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn approve" onClick={() => reviewFinding("approve")} disabled={!finding}>✓ {tRX(language, "Approve finding", "批准发现")}</button>
                    <button className="btn changes" onClick={() => reviewFinding("needs-evidence")} disabled={!finding}>↻ {tRX(language, "Needs evidence", "需补证据")}</button>
                    <button className="btn reject"  onClick={() => reviewFinding("reject")} disabled={!finding}>✕ {tRX(language, "Reject", "拒绝")}</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("reaffirm")} disabled={!finding}>{tRX(language, "Reaffirm", "再次确认")}</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("mark-stale")} disabled={!finding}>{tRX(language, "Mark stale", "标记陈旧")}</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("supersede")} disabled={!finding}>{tRX(language, "Supersede", "标记替代")}</button>
                    <button className="btn ghost"   onClick={() => reviewFinding("comment")} disabled={!finding}>{tRX(language, "Comment", "评论")}</button>
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
              language={language}
            />
          ) : (
          <React.Fragment>
          <div className="section">
            <div className="section-head"><span>{tRX(language, "Ask with scope", "按范围提问")}</span></div>
            <div className="section-body">
              <form onSubmit={submitQuestion} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div>
                  <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Question", "问题")}</div>
                  <textarea className="textarea" rows={3} value={question} onChange={onQuestionChangeWithExtract}
                            placeholder={(tenant && tenant.id) === "creditcardfraud" ? tRX(language, "Which transaction has elevated fraud risk?", "哪笔交易存在更高欺诈风险？") : tRX(language, "Why is Employee #4 workload unusual?", "为什么 Employee #4 的工作量异常？")} />
                </div>
                <EntityPicker tenant={tenant} centerNode={centerNode} setCenterNode={setCenterNode} question={question} setQuestion={setQuestion} compact language={language} />
                <div style={{ display: "flex", gap: 6 }}>
                  <div style={{ flex: 1 }}>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Depth", "深度")}</div>
                    <input className="input" type="number" min={1} max={3} value={depth} onChange={e => setDepth(+e.target.value)} />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Limit", "上限")}</div>
                    <input className="input" type="number" value={limit} onChange={e => setLimit(+e.target.value)} />
                  </div>
                </div>
                <button className="btn primary" type="submit" disabled={submitting}>{submitting ? tRX(language, "Creating…", "创建中…") : "↗ " + tRX(language, "Create scoped question", "创建范围问题")}</button>
              </form>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>{tRX(language, "Follow-up in scope", "范围内追问")}</span></div>
            <div className="section-body">
              <textarea className="textarea" rows={3} value={followup} onChange={e => setFollowup(e.target.value)}
                        placeholder={tRX(language, "What evidence would change this conclusion?", "什么证据会改变这个结论？")} style={{ marginBottom: 8 }} />
              <button className="btn" style={{ width: "100%" }} onClick={() => {
                if (!followup.trim()) return;
                const q = followup;
                setQuestion(q);
                setFollowup("");
                submitQuestion({ preventDefault: () => {} }, q);
              }} disabled={!followup.trim() || !task}>{tRX(language, "Create follow-up", "创建追问")}</button>
            </div>
          </div>

          <OntologyBasisPanel task={task} tenant={tenant} language={language} />

          <ApprovedFindingRegistry
            findings={approvedFindingsRegistry}
            query={approvedFindingsQ}
            tenant={tenant}
            filters={registryFilters}
            setFilters={setRegistryFilters}
            setActionMsg={setActionMsg}
            highlightedFindingKey={highlightedFindingKey}
            language={language}
          />

          <div className="section">
            <div className="section-head"><span>{tRX(language, "Write boundary", "写入边界")}</span></div>
            <div className="section-body">
              <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.55 }}>
                {tRX(language, "Reasoning agents can only write", "推理 agent 只能写入")} <span style={{ color: "var(--changes)" }}>{tRX(language, "draft", "草稿")}</span> {tRX(language, "findings and action proposals. Structural facts (links, properties, classifications) require a separate canonical write proposal and a stronger approval gate.", "发现和行动建议。结构性事实（链接、属性、分类）需要单独的正式写入提案和更强的审批关口。")}
              </div>
            </div>
          </div>

          <div className="section">
            <div className="section-head"><span>{tRX(language, "Quick actions", "快捷操作")}</span></div>
            <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>↗ {tRX(language, "Open graph context", "打开图谱上下文")}</button>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>≡ {tRX(language, "Compare with prior run", "与上次运行对比")}</button>
              <button className="btn ghost" style={{ justifyContent: "flex-start" }}>⤓ {tRX(language, "Export evidence pack", "导出证据包")}</button>
            </div>
          </div>
          </React.Fragment>
          )}
        </div>
      </div>

      <CleanupModal open={cleanupModal} onClose={() => setCleanupModal(false)}
                    allTasks={allTasks} taskState={taskState} tenant={tenant}
                    onDone={(deletedKeys = []) => {
                      if (deletedKeys.length) {
                        setDeletedTaskKeys(prev => {
                          const next = new Set(prev);
                          for (const key of deletedKeys) next.add(key);
                          return next;
                        });
                        setLocalTasks(prev => prev.filter(t => !deletedKeys.includes(t.canonical_key)));
                      }
                      window.dispatchEvent(new CustomEvent("aletheia:retry"));
                      setSelectedKey(null);
                    }} />
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
  language,
}) {
  const session = detail && detail.session;
  const hypotheses = (detail && detail.hypotheses) || [];
  const candidates = (detail && detail.candidate_findings) || [];
  const trace = buildAutopilotTrace(session, hypotheses, candidates);
  const safety = session && session.safety_profile ? session.safety_profile : {};
  const deepGraphCandidates = candidates.filter(c => c.deep_graph_profile?.multi_hop);
  const incompleteGraphCandidates = candidates.filter(c => c.deep_graph_profile && !c.deep_graph_profile.multi_hop && (c.deep_graph_profile.observed_steps || []).length > 0);
  return (
    <React.Fragment>
      <div className="art-header">
        <div className="crumb">
          <span className="type">{tRX(language, "Reasoning Autopilot", "推理 Autopilot")}</span>
          <span className="sep">/</span>
          <span>{session ? session.session_key : selectedKey || tRX(language, "new session", "新会话")}</span>
          {detailQ.loading && <span className="pill" style={{ marginLeft: "auto" }}><span className="dot" />{tRX(language, "Loading detail…", "加载详情…")}</span>}
        </div>
        <h1>{session ? resultTextRX(session.objective, language) : tRX(language, "Autopilot Discovery", "Autopilot 自动发现")}</h1>
        <p className="desc">
          {tRX(language,
            "Autopilot queues hypotheses and ranks draft candidate findings. It cannot write canonical ontology, approve findings, or expose sensitive raw fields.",
            "Autopilot 会排队假设并排序候选发现草稿；它不能写入正式本体、不能自动批准发现，也不能暴露敏感原始字段。")}
        </p>
        <div className="row">
          <div className="stat">
            <span className="label">{tRX(language, "Hypotheses", "假设")}</span>
            <span className="val mono">{hypotheses.length}</span>
          </div>
          <div className="stat">
            <span className="label">{tRX(language, "Candidate findings", "候选发现")}</span>
            <span className="val mono">{candidates.length}</span>
          </div>
          <div className="stat">
            <span className="label">{tRX(language, "Write scope", "写入范围")}</span>
            <span className="val" style={{ color: "var(--changes)" }}>{safety.write_scope || "draft_only"}</span>
          </div>
          <div className="stat">
            <span className="label">{tRX(language, "Canonical writes", "正式写入")}</span>
            <span className="val" style={{ color: "var(--rejected)" }}>{safety.canonical_writes || "disabled"}</span>
          </div>
          <div className="stat">
            <span className="label">{tRX(language, "Sensitive fields", "敏感字段")}</span>
            <span className="val" style={{ color: safety.allow_sensitive_fields ? "var(--rejected)" : "var(--approved)" }}>
              {safety.allow_sensitive_fields ? tRX(language, "allowed", "允许") : tRX(language, "blocked", "已阻断")}
            </span>
          </div>
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "var(--pad-4) var(--pad-5)" }}>
        {!session ? (
          <Panel eyebrow={tRX(language, "Start", "开始")} title={tRX(language, "No Autopilot session selected", "未选择 Autopilot 会话")} style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, color: "var(--muted)", fontSize: 12 }}>
              <span>{tRX(language, "Start a session to create a visible hypothesis queue and draft Finding Inbox.", "启动会话后会生成可见的假设队列和候选发现草稿收件箱。")}</span>
              <button className="btn primary" onClick={onStart} disabled={starting}>{starting ? tRX(language, "Starting…", "启动中…") : "▶ " + tRX(language, "Start Autopilot", "启动 Autopilot")}</button>
              {(tenant?.id === "creditcardfraud" || tenant?.id === "maritime-risk") && (
                <button className="btn" onClick={onRunPlaybook} disabled={playbookRunning}>
                  {playbookRunning ? tRX(language, "Running playbook…", "Playbook 运行中…") : tenant?.id === "maritime-risk" ? tRX(language, "Run maritime-risk playbook", "运行 maritime-risk playbook") : tRX(language, "Run fraud playbook", "运行欺诈 playbook")}
                </button>
              )}
            </div>
          </Panel>
        ) : (
          <React.Fragment>
            <Panel eyebrow={tRX(language, "Run trace", "运行轨迹")} title={tRX(language, "Autopilot execution trace", "Autopilot 执行轨迹")} count={`${trace.length} ${tRX(language, "events", "事件")}`} style={{ marginBottom: 16 }}>
              {trace.length === 0 ? (
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  {tRX(language, "No trace events yet. The playbook will append hypotheses and candidate findings through the Autopilot API.", "暂无轨迹事件。Playbook 会通过 Autopilot API 追加假设和候选发现。")}
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {trace.map((event, idx) => (
                    <div key={idx} style={{ display: "grid", gridTemplateColumns: "120px 1fr auto", gap: 10, padding: "9px 10px", border: "1px solid var(--line)", background: "var(--bg-1)", alignItems: "center" }}>
                      <span className="eyebrow" style={{ color: event.tone || "var(--accent)" }}>{statusTextRX(event.kind, language)}</span>
                      <span style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.45 }}>{resultTextRX(event.title, language)}</span>
                      <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>{statusTextRX(event.status, language)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            <Panel eyebrow={tRX(language, "Deep graph findings", "深度图发现")} title={tRX(language, "Multi-hop reasoning focus", "多跳推理重点")} count={`${deepGraphCandidates.length} ${tRX(language, "complete", "完整")}`} style={{ marginBottom: 16 }}>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8, marginBottom: deepGraphCandidates.length ? 12 : 0 }}>
                <MetricMini label={tRX(language, "Complete chains", "完整链路")} value={deepGraphCandidates.length} />
                <MetricMini label={tRX(language, "Incomplete chains", "不完整链路")} value={incompleteGraphCandidates.length} />
                <MetricMini label={tRX(language, "Max hops", "最大跳数")} value={Math.max(0, ...candidates.map(c => c.deep_graph_profile?.hop_count || 0))} />
                <MetricMini label={tRX(language, "Required path", "要求路径")} value={tRX(language, "5 steps", "5 步")} />
              </div>
              {deepGraphCandidates.length === 0 ? (
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  {tRX(language, "No complete deep graph findings yet. A complete finding must connect hazard, chokepoint, dependent country, risk metric, and recommended action.", "尚无完整深度图发现。完整发现必须连接风险因子、咽喉点、依赖国家、风险指标和建议动作。")}
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {deepGraphCandidates.slice(0, 3).map(c => (
                    <DeepGraphPathCard key={c.canonical_key} profile={c.deep_graph_profile} title={displayCandidateTitleRX(c, language)} language={language} compact />
                  ))}
                </div>
              )}
            </Panel>

            <Panel eyebrow={tRX(language, "Hypothesis queue", "假设队列")} title={tRX(language, "Queued reasoning hypotheses", "排队中的推理假设")} count={`${hypotheses.length} ${tRX(language, "items", "项")}`} style={{ marginBottom: 16 }}>
              {hypotheses.length === 0 ? (
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  {tRX(language, "No hypotheses yet. Run a tenant playbook to populate this queue with draft candidate findings.", "暂无假设。运行租户 playbook 后会用候选发现草稿填充该队列。")}
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {hypotheses.map(h => (
                    <div key={h.hypothesis_key} style={{ border: "1px solid var(--line)", background: "var(--bg-1)", padding: 12 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                        <Pill kind={h.status === "pruned" ? "rejected" : h.status === "completed" ? "approved" : "changes"}>{statusTextRX(h.status, language)}</Pill>
                        <strong style={{ color: "var(--text)", fontSize: 13 }}>{resultTextRX(h.title, language)}</strong>
                        <span className="mono" style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 10 }}>p{h.priority}</span>
                      </div>
                      <div style={{ color: "var(--text-dim)", fontSize: 12, lineHeight: 1.5 }}>{resultTextRX(h.rationale || "No rationale recorded.", language)}</div>
                      {h.status === "pruned" && (
                        <div style={{ marginTop: 8, color: "var(--rejected)", fontSize: 11 }}>{tRX(language, "Pruned reason", "剪枝原因")}: {resultTextRX(h.pruned_reason || "missing", language)}</div>
                      )}
                      {h.evidence_plan?.length > 0 && (
                        <div className="mono" style={{ marginTop: 8, color: "var(--muted)", fontSize: 10 }}>
                          {tRX(language, "Evidence plan", "证据计划")}: {h.evidence_plan.map(p => p.metric || p.kind || p.source_ref).filter(Boolean).join(" · ")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Panel>

            {(tenant?.id === "creditcardfraud" || tenant?.id === "maritime-risk") && (
              <Panel eyebrow="Playbook" title={tenant?.id === "maritime-risk" ? tRX(language, "Maritime-risk graph reasoning playbook", "Maritime-risk 图推理 Playbook") : tRX(language, "Creditcardfraud discovery playbook", "信用卡欺诈发现 Playbook")} count={tRX(language, "draft-only", "仅草稿")} style={{ marginBottom: 16 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>
                  <span>{tenant?.id === "maritime-risk"
                    ? tRX(language, "Run the fixed maritime playbook to populate chokepoint dependency, hazard-adjusted risk, and country-priority graph findings, plus a pruned non-graph ranking hypothesis.", "运行固定 maritime playbook，生成咽喉点依赖、风险因子调整、国家优先级等图推理发现，并保留一条已剪枝的非图排名假设。")
                    : tRX(language, "Run the fixed fraud playbook to populate card-not-present, verification mismatch, POS missing, merchant category, duplicate-cluster candidates, plus a pruned hypothesis with reason.", "运行固定欺诈 playbook，生成非面对面交易、验证不匹配、POS 缺失、商户类别、重复簇等候选发现，并保留一条带原因的剪枝假设。")}</span>
                  <button className="btn primary" onClick={onRunPlaybook} disabled={playbookRunning} style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>
                    {playbookRunning ? tRX(language, "Running…", "运行中…") : tRX(language, "Run playbook", "运行 Playbook")}
                  </button>
                </div>
              </Panel>
            )}

            <Panel eyebrow={tRX(language, "Finding Inbox", "发现收件箱")} title={tRX(language, "Draft candidate findings", "候选发现草稿")} count={`${candidates.length} ${tRX(language, "candidates", "候选")}`} style={{ marginBottom: 16 }}>
              {candidates.length === 0 ? (
                <div style={{ color: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                  {tRX(language, "No candidate findings yet. Autopilot UI is wired; the discovery playbook will fill this inbox with draft candidates.", "暂无候选发现。Autopilot UI 已接通，发现 playbook 会把候选草稿写入这里。")}
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
                        <Pill kind={c.status === "rejected" ? "rejected" : c.status === "needs_more_evidence" ? "changes" : "proposed"}>{statusTextRX(c.status, language)}</Pill>
                <strong style={{ color: "var(--text)", fontSize: 14 }}>{displayCandidateTitleRX(c, language)}</strong>
                      </div>
                      <div style={{ color: "var(--text-dim)", fontSize: 13, lineHeight: 1.55 }}>{displayCandidateConclusionRX(c, language)}</div>
                      {c.deep_graph_profile && (
                        <DeepGraphPathCard profile={c.deep_graph_profile} title={tRX(language, "Finding path", "发现路径")} language={language} />
                      )}
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8, marginTop: 12 }}>
                        <MetricMini label={tRX(language, "Value", "价值")} value={pctRX(c.value_score, 0)} />
                        <MetricMini label={tRX(language, "Confidence", "置信度")} value={pctRX(c.confidence, 0)} />
                        <MetricMini label={tRX(language, "Novelty", "新颖度")} value={pctRX(c.novelty_score, 0)} />
                        <MetricMini label={tRX(language, "Impact", "影响")} value={pctRX(c.impact_score, 0)} />
                      </div>
                      <div style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
                        <div className="eyebrow" style={{ marginBottom: 6 }}>{tRX(language, "Evidence chain", "证据链")}</div>
                        {(c.evidence_chain || []).length === 0 ? (
                          <div style={{ color: "var(--rejected)", fontSize: 11 }}>{tRX(language, "Missing evidence chain; should not pass final validation.", "缺少证据链；不应通过最终验证。")}</div>
                        ) : (
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {(c.evidence_chain || []).map((e, i) => (
                              <div key={i} className="mono" style={{ color: "var(--muted)", fontSize: 10, lineHeight: 1.45 }}>
                                {evidenceKindLabelRX(e.kind, language)} · {e.source_ref || e.source || "source"} · {e.metric || e.title || ""} {e.value ? `= ${Array.isArray(e.value) ? JSON.stringify(e.value) : resultTextRX(e.value, language)}` : ""}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      {(c.evidence_limits || []).length > 0 && (
                        <div style={{ marginTop: 10, color: "var(--muted)", fontSize: 11, lineHeight: 1.5 }}>
                          {tRX(language, "Limits", "证据边界")}: {resultListRX(c.evidence_limits || [], language).join(" · ")}
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
                            {tRX(language, "Review note required before rejecting or requesting more evidence.", "拒绝或要求更多证据前必须填写审核说明。")}
                          </div>
                        )}
                        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                          {isReviewed ? (
                            <span style={{ color: c.status === "approved" ? "var(--approved)" : c.status === "rejected" ? "var(--rejected)" : "var(--changes)", fontSize: 11 }}>
                              {tRX(language, "Review recorded", "复核已记录")} · {c.status === "approved" ? tRX(language, "approved finding created", "已创建批准发现") : statusTextRX(c.status, language)}
                            </span>
                          ) : (
                            <>
                              <button className="btn approve" onClick={() => onReviewCandidate(c, "approved")}>{tRX(language, "Approve as finding", "批准为发现")}</button>
                              <button className="btn changes" onClick={() => onReviewCandidate(c, "needs_more_evidence")}>{tRX(language, "Needs more evidence", "需要更多证据")}</button>
                              <button className="btn reject" onClick={() => onReviewCandidate(c, "rejected")}>{tRX(language, "Reject candidate", "拒绝候选")}</button>
                            </>
                          )}
                          <span style={{ marginLeft: "auto", color: "var(--changes)", fontSize: 11 }}>
                            {tRX(language, "Requires human approval · Autopilot suggests, people approve", "需要人工批准 · Autopilot 提建议，人来批准")}
                          </span>
                        </div>
                        {isSelectedForReview && !isReviewed && (
                          <div style={{ marginTop: 8 }}>
                            <textarea className="textarea" rows={2} value={reviewReason}
                                      onChange={e => { setReviewReason(e.target.value); setReviewMissingKey(""); }}
                                      placeholder={tRX(language, "Optional note for approval; required for reject or needs-more-evidence.", "批准时备注可选；拒绝或要求更多证据时必填。")} />
                          </div>
                        )}
                      </div>
                    </div>
                  );})}
                </div>
              )}
            </Panel>

            <Panel eyebrow={tRX(language, "Review gate", "审核关口")} title={tRX(language, "Candidate review note", "候选审核说明")} count={tRX(language, "human approval required", "需要人工批准")}>
              <textarea className="textarea" rows={3} value={reviewReason}
                        onChange={e => { setReviewReason(e.target.value); setReviewMissingKey(""); }}
                        onFocus={() => setReviewTargetKey(reviewTargetKey || (candidates[0] && candidates[0].canonical_key) || "")}
                        placeholder={tRX(language, "Reason required for reject / needs more evidence; optional for approve.", "拒绝/要求更多证据时必须填写原因；批准时可选。")} />
              <div style={{ marginTop: 8, color: "var(--muted)", fontSize: 11, lineHeight: 1.5 }}>
                {tRX(language, "Candidate approval creates a reviewed Finding Registry entry. It can be reused as prior_finding context and can draft next actions/proposals, but does not write canonical ontology or graph.", "候选批准后会创建已审核的 Finding Registry 条目。它可以作为 prior_finding 上下文复用，也可以草拟后续行动/提案，但不会写入正式本体或图谱。")}
              </div>
            </Panel>
          </React.Fragment>
        )}
      </div>
    </React.Fragment>
  );
}

function DeepGraphPathCard({ profile, title, language, compact = false }) {
  if (!profile) return null;
  const observed = profile.observed_steps || [];
  const missing = profile.missing_steps || [];
  const path = profile.path || [];
  return (
    <div style={{
      marginTop: compact ? 0 : 12,
      padding: compact ? "9px 10px" : "10px 12px",
      border: "1px solid var(--line)",
      background: profile.multi_hop ? "oklch(0.7 0.11 155 / 0.08)" : "oklch(0.78 0.14 75 / 0.08)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span className="eyebrow" style={{ color: profile.multi_hop ? "var(--approved)" : "var(--changes)" }}>
          {profile.multi_hop ? tRX(language, "deep graph finding", "深度图发现") : tRX(language, "partial graph chain", "部分图链路")}
        </span>
        <strong style={{ fontSize: 12, color: "var(--text)" }}>{title}</strong>
        <span className="mono" style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 10 }}>
          {profile.hop_count || 0} {tRX(language, "hops", "跳")}
        </span>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {(profile.required_steps || []).map(step => {
          const ok = observed.includes(step);
          return (
            <span key={step} className="mono" style={{
              fontSize: 10,
              padding: "4px 6px",
              border: "1px solid var(--line)",
              color: ok ? "var(--approved)" : "var(--muted)",
              background: ok ? "oklch(0.7 0.11 155 / 0.08)" : "var(--bg-1)",
            }}>
              {ok ? "✓ " : "· "}{graphStepLabelRX(step, language)}
            </span>
          );
        })}
      </div>
      {path.length > 0 && !compact && (
        <div className="mono" style={{ marginTop: 8, fontSize: 10, color: "var(--muted)", lineHeight: 1.45 }}>
          {path.map(node => node.label).filter(Boolean).join(" -> ")}
        </div>
      )}
      {missing.length > 0 && (
        <div style={{ marginTop: 8, color: "var(--changes)", fontSize: 11 }}>
          {tRX(language, "Missing", "缺失")}: {missing.map(step => graphStepLabelRX(step, language)).join(" / ")}
        </div>
      )}
    </div>
  );
}

function graphStepLabelRX(step, language) {
  const labels = {
    hazard: ["hazard", "风险因子"],
    chokepoint: ["chokepoint", "咽喉点"],
    dependent_country: ["dependent country", "依赖国家"],
    risk_metric: ["risk metric", "风险指标"],
    recommended_action: ["recommended action", "建议动作"],
  };
  const pair = labels[step] || [step, step];
  return tRX(language, pair[0], pair[1]);
}

function AutopilotStartPanel({ tenant, objective, setObjective, maxHypotheses, setMaxHypotheses, maxRuns, setMaxRuns, maxToolCalls, setMaxToolCalls, starting, onStart, onRunPlaybook, playbookRunning, detail, language }) {
  const safety = detail?.session?.safety_profile || {};
  const budget = detail?.session?.budget || {};
  return (
    <React.Fragment>
      <div className="section">
        <div className="section-head"><span>{tRX(language, "Start Autopilot", "启动 Autopilot")}</span></div>
        <div className="section-body">
          <form onSubmit={onStart} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Objective", "目标")}</div>
              <textarea className="textarea" rows={3} value={objective} onChange={e => setObjective(e.target.value)}
                        placeholder={tRX(language, "Find high-value candidate findings…", "发现高价值候选发现…")} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 6 }}>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Hypotheses", "假设")}</div>
                <input className="input" type="number" min={1} max={25} value={maxHypotheses} onChange={e => setMaxHypotheses(+e.target.value)} />
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Runs", "运行")}</div>
                <input className="input" type="number" min={1} max={20} value={maxRuns} onChange={e => setMaxRuns(+e.target.value)} />
              </div>
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Tool calls", "工具调用")}</div>
                <input className="input" type="number" min={1} max={80} value={maxToolCalls} onChange={e => setMaxToolCalls(+e.target.value)} />
              </div>
            </div>
            <button className="btn primary" type="submit" disabled={starting}>{starting ? tRX(language, "Starting…", "启动中…") : "▶ " + tRX(language, "Start Autopilot", "启动 Autopilot")}</button>
            {(tenant?.id === "creditcardfraud" || tenant?.id === "maritime-risk") && (
              <button className="btn" type="button" onClick={onRunPlaybook} disabled={playbookRunning}>
                {playbookRunning ? tRX(language, "Running playbook…", "Playbook 运行中…") : tenant?.id === "maritime-risk" ? tRX(language, "Run maritime-risk playbook", "运行 maritime-risk playbook") : tRX(language, "Run creditcardfraud playbook", "运行 creditcardfraud playbook")}
              </button>
            )}
          </form>
        </div>
      </div>

      <div className="section">
        <div className="section-head"><span>{tRX(language, "Safety profile", "安全配置")}</span></div>
        <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <BoundaryLine label={tRX(language, "Tenant", "租户")} value={tenant ? tenant.id : "default"} />
          <BoundaryLine label={tRX(language, "Safe views only", "仅安全视图")} value={String(safety.safe_views_only !== false)} tone="var(--approved)" />
          <BoundaryLine label={tRX(language, "Sensitive fields", "敏感字段")} value={safety.allow_sensitive_fields ? tRX(language, "allowed", "允许") : tRX(language, "blocked", "已阻断")} tone={safety.allow_sensitive_fields ? "var(--rejected)" : "var(--approved)"} />
          <BoundaryLine label={tRX(language, "Canonical writes", "正式写入")} value={safety.canonical_writes || "disabled"} tone="var(--rejected)" />
          <BoundaryLine label={tRX(language, "Auto approve", "自动批准")} value={String(!!safety.auto_approve_findings)} tone={safety.auto_approve_findings ? "var(--rejected)" : "var(--approved)"} />
          <BoundaryLine label={tRX(language, "Blocked field group", "阻断字段组")} value={(safety.blocked_fields || []).length ? (safety.blocked_fields || []).join(", ") : tRX(language, "none", "无")} />
        </div>
      </div>

      <div className="section">
        <div className="section-head"><span>{tRX(language, "Budget", "预算")}</span></div>
        <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <BoundaryLine label={tRX(language, "Max hypotheses", "最大假设数")} value={budget.max_hypotheses || maxHypotheses} />
          <BoundaryLine label={tRX(language, "Max reasoning tasks", "最大推理任务数")} value={budget.max_reasoning_tasks || maxRuns} />
          <BoundaryLine label={tRX(language, "Max tool calls", "最大工具调用数")} value={budget.max_tool_calls || maxToolCalls} />
          <BoundaryLine label={tRX(language, "Sample strategy", "采样策略")} value={budget.sample_strategy || "deterministic_full_table_aggregates"} />
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
    const deletedKeys = [];
    for (const t of filtered) {
      try {
        try { await window.AL_API.closeTask(t.canonical_key, tenant.id); } catch (_) {}
        await window.AL_API.deleteTask(t.canonical_key, tenant.id);
        if (t.canonical_key) deletedKeys.push(t.canonical_key);
        ok++;
      } catch (_) { fail++; }
      done++;
      setProgress({ done, total, ok, fail, running: done < total });
    }
    setProgress({ done, total, ok, fail, running: false });
    if (onDone) onDone(deletedKeys);
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

function OntologyBasisPanel({ task, tenant, language }) {
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
      <div className="section-head"><span>{tRX(language, "Ontology basis", "本体依据")}</span><span className="ct">{list.length}</span></div>
      <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {list.map(key => (
          <a key={key}
             className="btn ghost"
             href={`/?screen=ontology&tenant=${encodeURIComponent(tenantId)}&artifact=${encodeURIComponent(key)}`}
             style={{ justifyContent: "space-between", gap: 10 }}
             title={tRX(language, "Open full ontology governance details in Ontology.", "在 Ontology 中打开完整本体治理详情。")}>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{ontologyBasisLabel(key)}</span>
            <span style={{ color: "var(--accent)", flexShrink: 0 }}>{tRX(language, "View in Ontology", "在 Ontology 中查看")}</span>
          </a>
        ))}
        <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.55 }}>
          {tRX(language, "Compact basis only. Detailed source mapping, approval audit, canonical state, and graph eligibility live in Ontology.", "这里只显示紧凑依据。详细源映射、审批审计、正式状态和图谱资格在 Ontology 中查看。")}
        </div>
      </div>
    </div>
  );
}

function ApprovedFindingRegistry({ findings, query, tenant, filters, setFilters, setActionMsg, highlightedFindingKey, language }) {
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
      setActionMsg && setActionMsg({ kind: "ok", msg: tRX(language, "Workspace action created.", "Workspace 行动已创建。") });
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
      setActionMsg && setActionMsg({ kind: "ok", msg: tRX(language, "Action recorded.", "行动已记录。") });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg && setActionMsg({ kind: "err", msg: err.message || String(err) });
    }
  }
  async function batch(action) {
    if (!selectedKeys.length) {
      setActionMsg && setActionMsg({ kind: "err", msg: tRX(language, "Select findings for batch revalidation.", "请选择要批量复验的发现。") });
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
      setActionMsg && setActionMsg({ kind: "ok", msg: tRX(language, "Batch review recorded for ", "已批量记录 ") + selectedKeys.length + tRX(language, " findings.", " 个发现。") });
      window.dispatchEvent(new CustomEvent("aletheia:retry"));
    } catch (err) {
      setActionMsg && setActionMsg({ kind: "err", msg: err.message || String(err) });
    }
  }
  return (
    <div className="section">
      <div className="section-head"><span>{tRX(language, "Approved Finding Registry", "已批准发现库")}</span><span className="ct">{list.length}</span></div>
      <div className="section-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <ApiStatus q={query} what={tRX(language, "approved findings", "已批准发现")} />
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 6 }}>
          <select className="input" value={filters.status || ""} onChange={e => updateFilter("status", e.target.value)}>
            <option value="">{tRX(language, "Any status", "任意状态")}</option>
            <option value="approved">{tRX(language, "Approved", "已批准")}</option>
            <option value="stale">{tRX(language, "Stale", "已陈旧")}</option>
            <option value="superseded">{tRX(language, "Superseded", "已替代")}</option>
            <option value="rejected">{tRX(language, "Rejected", "已拒绝")}</option>
            <option value="needs_more_evidence">{tRX(language, "Needs evidence", "需补证据")}</option>
          </select>
          <select className="input" value={filters.context || ""} onChange={e => updateFilter("context", e.target.value)}>
            <option value="">{tRX(language, "Audit/history", "审计/历史")}</option>
            <option value="active">{tRX(language, "Active context", "活跃上下文")}</option>
          </select>
          <select className="input" value={filters.finding_type || ""} onChange={e => updateFilter("finding_type", e.target.value)}>
            <option value="">{tRX(language, "Any type", "任意类型")}</option>
            <option value="risk_pattern">{tRX(language, "Risk pattern", "风险模式")}</option>
            <option value="operational_anomaly">{tRX(language, "Operational anomaly", "运营异常")}</option>
            <option value="quality_issue">{tRX(language, "Quality issue", "质量问题")}</option>
            <option value="ontology_conflict">{tRX(language, "Ontology conflict", "本体冲突")}</option>
            <option value="investigation_prompt">{tRX(language, "Investigation prompt", "调研提示")}</option>
          </select>
          <select className="input" value={filters.action_state || ""} onChange={e => updateFilter("action_state", e.target.value)}>
            <option value="">{tRX(language, "Any action", "任意行动")}</option>
            <option value="no_action">{tRX(language, "No action", "无行动")}</option>
            <option value="open_action">{tRX(language, "Open action", "开放行动")}</option>
            <option value="overdue_action">{tRX(language, "Overdue action", "逾期行动")}</option>
            <option value="closed_action">{tRX(language, "Closed action", "已关闭行动")}</option>
          </select>
          <select className="input" value={filters.freshness || ""} onChange={e => updateFilter("freshness", e.target.value)}>
            <option value="">{tRX(language, "Any freshness", "任意新鲜度")}</option>
            <option value="reaffirmed_recently">{tRX(language, "Reaffirmed", "已再次确认")}</option>
            <option value="due_for_revalidation">{tRX(language, "Due for review", "待复验")}</option>
            <option value="stale">{tRX(language, "Stale", "已陈旧")}</option>
            <option value="superseded">{tRX(language, "Superseded", "已替代")}</option>
          </select>
          <select className="input" value={filters.sort || ""} onChange={e => updateFilter("sort", e.target.value)}>
            <option value="newest_reviewed">{tRX(language, "Newest reviewed", "最新复核")}</option>
            <option value="value_desc">{tRX(language, "Value score", "价值分")}</option>
            <option value="oldest_unrevalidated">{tRX(language, "Oldest unrevalidated", "最久未复验")}</option>
            <option value="action_due_asc">{tRX(language, "Action due date", "行动截止时间")}</option>
            <option value="confidence_desc">{tRX(language, "Confidence", "置信度")}</option>
          </select>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          <input className="input" value={owner} onChange={e => setOwner(e.target.value)} placeholder="@owner" />
          <input className="input" type="datetime-local" value={dueAt} onChange={e => setDueAt(e.target.value)} />
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button className="btn xs approve" onClick={() => batch("reaffirm")} disabled={!selectedKeys.length}>{tRX(language, "Reaffirm selected", "再次确认选中项")}</button>
          <button className="btn xs changes" onClick={() => batch("mark_stale")} disabled={!selectedKeys.length}>{tRX(language, "Mark stale", "标记陈旧")}</button>
          <button className="btn xs" onClick={() => batch("assign_owner")} disabled={!selectedKeys.length}>{tRX(language, "Assign owner", "分配负责人")}</button>
        </div>
        {list.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.55 }}>
            {tRX(language,
              "No active approved findings yet. Approved findings will enter future reasoning as prior_finding / reviewed_inference context.",
              "暂无活跃的已批准发现。批准后的发现会作为 prior_finding / reviewed_inference 进入后续推理上下文。")}
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
                  {highlighted ? tRX(language, "newly added · ", "新加入 · ") : ""}{f.reasoning_use ? tRX(language, "active prior insight · reviewed_inference", "活跃历史洞察 · reviewed_inference") : tRX(language, "audit only", "仅审计")} · {f.source_label || "Reasoning"} · {f.finding_type || "finding"}
                </span>
                <strong style={{ display: "block", fontSize: 12, marginTop: 3 }}>{displayFindingTitleRX(f, language)}</strong>
                <span style={{ display: "block", fontSize: 11, color: "var(--muted)", lineHeight: 1.4, marginTop: 3 }}>
                  {displayFindingConclusionRX(f, language).slice(0, 140)}
                </span>
              </a>
              <Pill kind={f.status === "approved" ? "approved" : f.status === "stale" ? "changes" : f.status === "rejected" ? "rejected" : "proposed"}>{statusTextRX(f.status, language)}</Pill>
            </div>
            <div className="mono" style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 8, color: "var(--muted)", fontSize: 10 }}>
              <span>{tRX(language, "conf", "置信度")} {pctRX(f.confidence, 0)}</span>
              <span>{tRX(language, "value", "价值")} {pctRX(f.value_score, 0)}</span>
              <span>{tRX(language, "evidence", "证据")} {f.evidence_count || 0}</span>
              <span>{tRX(language, "freshness", "新鲜度")} {f.freshness || "-"}</span>
              <span>{tRX(language, "action", "行动")} {f.action_summary?.state || "no_action"}</span>
            </div>
            {f.action_summary?.primary ? (
              <div style={{ marginTop: 8, borderTop: "1px solid var(--line)", paddingTop: 8, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  {f.action_summary.primary.owner || tRX(language, "unowned", "未分配")} · {tRX(language, "due", "截止")} {f.action_summary.primary.due_at || "-"} · {statusTextRX(f.action_summary.primary.status, language)}
                </span>
                <select className="input" value={result} onChange={e => setResult(e.target.value)} style={{ width: 150 }}>
                  <option value="confirmed_risk">{tRX(language, "confirmed risk", "确认风险")}</option>
                  <option value="false_positive">{tRX(language, "false positive", "误报")}</option>
                  <option value="evidence_added">{tRX(language, "evidence added", "已补证据")}</option>
                  <option value="proposal_created">{tRX(language, "proposal created", "已创建提案")}</option>
                  <option value="no_action_needed">{tRX(language, "no action needed", "无需行动")}</option>
                  <option value="rerun_scheduled">{tRX(language, "rerun scheduled", "已安排重跑")}</option>
                </select>
                <button className="btn xs" onClick={() => transitionAction(f.action_summary.primary.action_key, "start")}>{tRX(language, "Start", "开始")}</button>
                <button className="btn xs changes" onClick={() => transitionAction(f.action_summary.primary.action_key, "block")}>{tRX(language, "Block", "阻塞")}</button>
                <button className="btn xs approve" onClick={() => transitionAction(f.action_summary.primary.action_key, "close")}>{tRX(language, "Close", "关闭")}</button>
                <button className="btn xs" onClick={() => transitionAction(f.action_summary.primary.action_key, "reopen")}>{tRX(language, "Reopen", "重开")}</button>
              </div>
            ) : (
              <div style={{ marginTop: 8 }}>
                <button className="btn xs" onClick={() => createAction(f)}>{tRX(language, "Create action", "创建行动")}</button>
              </div>
            )}
          </div>
          );
        })}
        <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.45 }}>
          {tRX(language,
            "Action close/reopen records Finding usage events only; it does not change Finding status or write canonical ontology/graph.",
            "关闭/重开行动只记录发现的使用事件；不会改变发现状态，也不会写入正式本体或图谱。")}
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
    llm_request_body: "var(--accent)",
    llm_response_body: "var(--approved)",
    no_llm_call: "var(--dim)",
    question_body: "var(--accent)",
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
    llm_request_body: "LLM REQUEST",
    llm_response_body: "LLM RESPONSE",
    no_llm_call: "NO LLM CALL",
    question_body: "QUESTION",
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
  if (data.response_body != null || data.response_title) {
    return <RequestBodyPreview data={data} title={data.response_title || "response body"} />;
  }
  if (data.request_body != null || data.request_title) {
    return <RequestBodyPreview data={data} title={data.request_title || "request body"} />;
  }
  if (data.stage && data.reason) {
    return <RequestBodyPreview data={data} title={`${data.stage} result`} />;
  }

  switch (name) {
    case "llm_request_body": {
      return <RequestBodyPreview data={data} title={data.request_title || "request body"} />;
    }
    case "llm_response_body": {
      return <RequestBodyPreview data={data} title={data.response_title || "response body"} />;
    }
    case "no_llm_call": {
      return <RequestBodyPreview data={data} title={data.stage ? `${data.stage} result` : "no llm call"} />;
    }
    case "question_body": {
      return <RequestBodyPreview data={data} title="question body" />;
    }
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

function splitRequestPreviewTokensRX(text) {
  const raw = String(text || "");
  const words = raw.trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) return { tokens: words, joiner: " " };
  return { tokens: Array.from(raw), joiner: "" };
}

function RequestBodyPreview({ data, title }) {
  const [expanded, setExpanded] = React.useState(false);
  const bodySource = data && (data.request_body || data.response_body || data.question_body || data.body || data.question || data);
  const body = typeof bodySource === "string" ? bodySource : JSON.stringify(bodySource || {}, null, 2);
  const limit = 50;
  const { tokens, joiner } = splitRequestPreviewTokensRX(body);
  const overLimit = tokens.length > limit;
  const preview = overLimit && !expanded ? `${tokens.slice(0, limit).join(joiner)}…` : body;
  const meta = [];
  if (data && data.tenant_id) meta.push(data.tenant_id);
  if (data && data.center_node) meta.push(data.center_node);
  if (data && data.depth != null) meta.push(`d${data.depth}`);
  if (data && data.node_limit != null) meta.push(`n${data.node_limit}`);
  return (
    <span>
      <strong style={{ color: "var(--text)", fontWeight: 500 }}>{title || "request body"}</strong>
      {meta.length > 0 && <span style={{ color: "var(--dim)" }}> · {meta.join(" · ")}</span>}
      <div style={{
        color: "var(--muted)",
        marginTop: 2,
        fontFamily: "var(--font-sans)",
        fontSize: 12,
        lineHeight: 1.5,
        whiteSpace: "pre-wrap",
      }}>
        {preview || "—"}
      </div>
      {overLimit && (
        <button
          type="button"
          className="btn xs ghost"
          onClick={() => setExpanded(v => !v)}
          style={{ marginTop: 4, padding: "2px 7px", minHeight: 0 }}
        >
          {expanded ? "less" : "more"}
        </button>
      )}
    </span>
  );
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
function AskHero({ tenant, question, setQuestion, centerNode, setCenterNode, depth, setDepth, limit, setLimit, isMock, submitting, actionMsg, onDismissMsg, onCancel, onSubmit, language }) {
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
      setQuestion(questionTextRX(defaultQuestionForTenantRX(tenantId, pickedType, newLabel, ent.id), language));
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
              title={tRX(language, "Close (Esc)", "关闭（Esc）")}
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
          <div className="eyebrow accent">{tRX(language, "New reasoning task", "新推理任务")}</div>
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
            ← {tRX(language, "back to task list", "返回任务列表")}
          </button>
        </div>
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: "0 0 8px 0", lineHeight: 1.15 }}>
          {isFraudTenant ? tRX(language, "Ask a fraud-scoped question.", "提出欺诈范围问题。") : tRX(language, "Ask a scoped question.", "提出范围问题。")}
        </h1>
        <p style={{ color: "var(--muted)", fontSize: 14, lineHeight: 1.55, margin: "0 0 24px 0", maxWidth: "60ch" }}>
          {tRX(language, "The agent reasons only over the approved graph and live source objects for this tenant. A scoped question pins a center node, depth, and limit — and produces a", "Agent 只基于该租户的已批准图谱和 live source 对象推理。范围问题会固定中心节点、深度和上限，并生成可审核的")} <span style={{ color: "var(--changes)" }}>{tRX(language, "draft", "草稿")}</span> {tRX(language, "finding that you can review.", "发现。")}
        </p>

        <form onSubmit={onSubmit}>
          <div style={{ border: "1px solid var(--line-strong)", background: "var(--bg-2)" }}>
            <div style={{ padding: "var(--pad-4) var(--pad-4)" }}>
              <div className="eyebrow" style={{ marginBottom: 6 }}>{tRX(language, "Question", "问题")}</div>
              <textarea autoFocus value={question} onChange={onQuestionChange}
                        rows={3}
                        placeholder={isFraudTenant ? tRX(language, "e.g. Which transactions have elevated fraud risk?", "例如：哪些交易存在更高欺诈风险？") : tRX(language, "e.g. Why is Employee #4 workload unusual?", "例如：为什么 Employee #4 的工作量异常？")}
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
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Center node", "中心节点")}</div>
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
                           placeholder={entitiesLoading ? tRX(language, "Loading…", "加载中…") : entityTypes.length ? (entities.length ? entities[0].label || entities[0].id : tRX(language, "Search…", "搜索…")) : tRX(language, "No tenant objects", "无租户对象")} />
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
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Depth", "深度")}</div>
                <input className="input" type="number" min={1} max={3}
                       value={depth} onChange={e => setDepth(+e.target.value)} />
              </div>
              <div style={{ padding: "var(--pad-3) var(--pad-4)" }}>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Limit", "上限")}</div>
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
              <span className="eyebrow" style={{ color: "var(--muted)" }}>{tRX(language, "Scope", "范围")}</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-dim)" }}>
                {tRX(language, "approved-only · tenant-scoped · agent writes", "仅已批准 · 租户范围 · agent 仅写")} <span style={{ color: "var(--changes)" }}>{tRX(language, "draft", "草稿")}</span>
              </span>
              {isMock && (
                <span className="pill changes" style={{ marginLeft: "auto" }}>
                  <span className="dot" />{tRX(language, "Mock — will save locally", "模拟模式 — 将本地保存")}
                </span>
              )}
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <button type="submit" className="btn primary" style={{ padding: "10px 18px", fontSize: 12 }} disabled={!question.trim() || submitting}>
              {submitting ? tRX(language, "Creating…", "创建中…") : "↗ " + tRX(language, "Create scoped question", "创建范围问题")}
            </button>
            <button type="button" className="btn ghost" onClick={onCancel}>{tRX(language, "Cancel", "取消")}</button>
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
          <div className="eyebrow" style={{ marginBottom: 10 }}>{tRX(language, "Suggested questions", "建议问题")}</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {suggestions.map((s, i) => {
              const active = centerNode === s.node;
              return (
              <button key={i}
                      type="button"
                      disabled={!s.node}
                      onClick={() => {
                        setQuestion(questionTextRX(s.q, language));
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
                <div>{questionTextRX(s.q, language)}</div>
                {s.node && (
                <div style={{ marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: active ? "var(--accent)" : "var(--dim)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  {tRX(language, "center", "中心")} · {s.node}
                </div>
                )}
              </button>);
            })}
          </div>
        </div>

        <div style={{ marginTop: 32, padding: "14px 16px", border: "1px solid var(--line)", background: "var(--bg-2)" }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>{tRX(language, "How this works", "工作方式")}</div>
          <ol style={{ margin: 0, paddingLeft: 18, color: "var(--muted)", fontSize: 12, lineHeight: 1.7 }}>
            <li>{tRX(language, "Create a scoped question — pinned to a center node, depth, and limit on the approved graph.", "创建范围问题：固定已批准图谱中的中心节点、深度和上限。")}</li>
            <li>{tRX(language, "Run reasoning. The agent produces a", "运行推理。Agent 会生成带证据链的")} <span style={{ color: "var(--changes)" }}>{tRX(language, "draft", "草稿")}</span> {tRX(language, "conclusion with an evidence chain.", "结论。")}</li>
            <li>{tRX(language, "Review the evidence and approve, request changes, or reject the finding.", "复核证据后批准、要求修改或拒绝发现。")}</li>
            <li>{tRX(language, "Approval cites the finding in the approved-finding layer — it does", "批准只会把发现引用到已批准发现层，")} <strong style={{ color: "var(--text)" }}>{tRX(language, "not", "不会")}</strong> {tRX(language, "modify the canonical ontology or graph.", "修改正式本体或图谱。")}</li>
          </ol>
        </div>
      </div>
    </div>
  );
}

/* ---------------- EntityPicker ----------------
   Reusable entity type + search picker. Used in both AskHero and sidebar "Ask with scope". */
function EntityPicker({ tenant, centerNode, setCenterNode, question, setQuestion, compact, language }) {
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
        setQuestion(questionTextRX(defaultQuestionForTenantRX(tenantId, pickedType, newLabel, ent.id), language));
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
      <div className="eyebrow" style={{ marginBottom: 4 }}>{tRX(language, "Center node", "中心节点")}</div>
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
                 placeholder={entitiesLoading ? tRX(language, "Loading…", "加载中…") : entityTypes.length ? (entities.length ? entities[0].label || entities[0].id : tRX(language, "Search…", "搜索…")) : tRX(language, "No tenant objects", "无租户对象")} />
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
                      onClick={() => setQuestion(questionTextRX(s, language))}>
                {questionTextRX(s, language)}
              </button>
            ))}
          </div>
        );
      })()}
    </div>
  );
}

Object.assign(window, { AskHero, EntityPicker });
