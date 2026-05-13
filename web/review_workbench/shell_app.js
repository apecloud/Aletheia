const SHELL_STORAGE = {
  theme: "aletheia.portal.theme",
  lang: "aletheia.portal.lang",
  collapsed: "aletheia.portal.sidebar.collapsed",
};

const NAV_ITEMS = [
  ["#nav-workbench", "workbench", "⌂"],
  ["#nav-questions", "questions", "?"],
  ["#nav-findings", "findings", "◆"],
  ["#nav-evidence", "evidence", "≡"],
  ["#nav-explore", "explore", "◎"],
  ["#nav-quality", "quality", "!"],
  ["#nav-ontology", "ontology", "◇"],
  ["#nav-runtime", "runtime", "▣"],
  ["#nav-audit", "audit", "☷"],
];

const NAV_PATHS = {
  "#nav-workbench": "/",
  "#nav-questions": "/questions.html",
  "#nav-findings": "/findings.html",
  "#nav-evidence": "/evidence.html",
  "#nav-explore": "/graph.html",
  "#nav-quality": "/quality.html",
  "#nav-ontology": "/ontology.html",
  "#nav-runtime": "/settings.html",
  "#nav-audit": "/ontology.html",
};

const I18N = {
  en: {
    app_name: "Aletheia Portal",
    shell_title: "Reasoning Portal",
    collapse: "Collapse sidebar",
    expand: "Expand sidebar",
    open_nav: "Open navigation",
    close_nav: "Close navigation",
    theme: "Theme",
    light: "Light",
    dark: "Dark",
    language: "Language",
    english: "EN",
    chinese: "中文",
    workbench: "Workbench",
    questions: "Questions",
    findings: "Findings",
    evidence: "Evidence",
    explore: "Explore",
    quality: "Quality",
    ontology: "Ontology",
    runtime: "Runtime",
    audit: "Audit",
    "Reasoning Workbench": "Reasoning Workbench",
    "Reasoning Loop": "Reasoning Loop",
    "Findings & Evidence": "Findings & Evidence",
    "Evidence Chain": "Evidence Chain",
    "Graph Explorer": "Graph Explorer",
    "Instance Explorer": "Instance Explorer",
    "Ontology Review": "Ontology Review",
    "Quality & Attention": "Quality & Attention",
    "Question Center": "Question Center",
    "AI Runtime Settings": "AI Runtime Settings",
    "Knowledge reasoning workspace": "Knowledge reasoning workspace",
    "What matters now, why it matters, and what needs review.": "What matters now, why it matters, and what needs review.",
    "Ask: why is Employee #4 workload unusual?": "Ask: why is Employee #4 workload unusual?",
    "Why is Employee #4 workload unusual?": "Why is Employee #4 workload unusual?",
    "What evidence would change this conclusion?": "What evidence would change this conclusion?",
    "Search name, key, description": "Search name, key, description",
    "Search Employee by id or name": "Search Employee by id or name",
    "Reason, comment, or reviewer note": "Reason, comment, or reviewer note",
    Ask: "Ask",
    "Knowledge space": "Knowledge space",
    "Covered entities": "Covered entities",
    Relations: "Relations",
    "Reasoning findings": "Reasoning findings",
    "Last update": "Last update",
    "approved graph scope": "approved graph scope",
    "returned graph edges": "returned graph edges",
    "data / reasoning": "data / reasoning",
    "Key Findings": "Key Findings",
    "All findings": "All findings",
    "Recent Changes": "Recent Changes",
    "Needs Attention": "Needs Attention",
    "Quality panel": "Quality panel",
    "Quick Tasks": "Quick Tasks",
    "goal first": "goal first",
    Conclusions: "Conclusions",
    "Reasoning Findings": "Reasoning Findings",
    "Conclusion Summary": "Conclusion Summary",
    "Why Summary": "Why Summary",
    "Supporting Evidence": "Supporting Evidence",
    "Counter Evidence / Conflicts": "Counter Evidence / Conflicts",
    "Evidence Browser": "Evidence Browser",
    "Evidence item": "Evidence item",
    "Evidence Summary": "Evidence Summary",
    "Source / Path": "Source / Path",
    "Graph Path": "Graph Path",
    "Rule / Ontology Basis": "Rule / Ontology Basis",
    "Linked Explanation": "Linked Explanation",
    "Follow-up Questions": "Follow-up Questions",
    "Ask with scope": "Ask with scope",
    "Question -> Answer -> Evidence": "Question -> Answer -> Evidence",
    "Unified reasoning loop": "Unified reasoning loop",
    "Ask, inspect the current answer, expand evidence, and review without leaving this page.": "Ask, inspect the current answer, expand evidence, and review without leaving this page.",
    "Ask with approved graph scope": "Ask with approved graph scope",
    "Question & context": "Question & context",
    "Create follow-up": "Create follow-up",
    "Continue follow-up": "Continue follow-up",
    "Follow-up question": "Follow-up question",
    "Question History": "Question History",
    "Evidence Chain": "Evidence Chain",
    "Graph path": "Graph path",
    "Raw evidence payload": "Raw evidence payload",
    "Current task": "Current task",
    "Latest run": "Latest run",
    "Review gate": "Review gate",
    "Run reasoning": "Run reasoning",
    "Open question history": "Open question history",
    "Open reasoning loop": "Open reasoning loop",
    "Turn a question into a draft-only reasoning task.": "Turn a question into a draft-only reasoning task.",
    Question: "Question",
    Scope: "Scope",
    "Center node": "Center node",
    Depth: "Depth",
    Limit: "Limit",
    "Create scoped question": "Create scoped question",
    "Open graph context": "Open graph context",
    "Question History": "Question History",
    "Created tasks will appear here with a link to run or inspect them.": "Created tasks will appear here with a link to run or inspect them.",
    "Draft findings": "Draft findings",
    "Low confidence": "Low confidence",
    "Blocked reasoning": "Blocked reasoning",
    "Agent policy": "Agent policy",
    "Sandbox gate": "Sandbox gate",
    "awaiting review": "awaiting review",
    "needs scrutiny": "needs scrutiny",
    "approved-only / evidence gaps": "approved-only / evidence gaps",
    "blocked or violations": "blocked or violations",
    "negative control": "negative control",
    "Attention Items": "Attention Items",
    "Sandbox Approved-only Gaps": "Sandbox Approved-only Gaps",
    "Approved graph": "Approved graph",
    "Read-only scope": "Read-only scope",
    "Current Answer": "Current Answer",
    Conclusion: "Conclusion",
    "No conclusion has been generated yet. Run this reasoning task to produce a draft finding.": "No conclusion has been generated yet. Run this reasoning task to produce a draft finding.",
    "Recommended action proposal": "Recommended action proposal",
    "Counter evidence / limits": "Counter evidence / limits",
    "Open evidence chain": "Open evidence chain",
    "Submit review": "Submit review",
    "Request more evidence": "Request more evidence",
    "Rerun reasoning": "Rerun reasoning",
    "Graph Canvas": "Graph Canvas",
    "Scoped reasoning": "Scoped reasoning",
    "Select graph item": "Select graph item",
    "Open scoped reasoning": "Open scoped reasoning",
    Tenant: "Tenant",
    Namespace: "Namespace",
    "Graph database": "Graph database",
    Search: "Search",
    Kind: "Kind",
    "All evidence": "All evidence",
    Fact: "Fact",
    Hypothesis: "Hypothesis",
    Conflict: "Conflict",
    Missing: "Missing",
    "Current scope": "Current scope",
    "Expand history": "Expand history",
    "Load graph": "Load graph",
    "Fit view": "Fit view",
    "Focus selected": "Focus selected",
    Expand: "Expand",
    "Collapse expanded": "Collapse expanded",
    "No reasoning findings yet. Ask a question or run scoped reasoning to create the first draft finding.": "No reasoning findings yet. Ask a question or run scoped reasoning to create the first draft finding.",
    "No high-priority attention items in the current tenant.": "No high-priority attention items in the current tenant.",
    "No recent reasoning activity.": "No recent reasoning activity.",
    "No reasoning questions yet.": "No reasoning questions yet.",
    "No active quality issues for this tenant.": "No active quality issues for this tenant.",
    "Sandbox tenant has no missing artifact gaps reported.": "Sandbox tenant has no missing artifact gaps reported.",
    "No supporting evidence recorded.": "No supporting evidence recorded.",
    "No counter evidence or conflicts recorded for this draft.": "No counter evidence or conflicts recorded for this draft.",
    "No graph context recorded.": "No graph context recorded.",
    "Select evidence from the left panel.": "Select evidence from the left panel.",
    "Open a source, graph path, or conflict item to inspect how it supports or challenges a finding.": "Open a source, graph path, or conflict item to inspect how it supports or challenges a finding.",
    "No expansions yet.": "No expansions yet.",
    "No instances found.": "No instances found.",
    "No AgentRun records for this tenant.": "No AgentRun records for this tenant.",
    "Open explanation": "Open explanation",
    "Open source context": "Open source context",
    "Open run detail": "Open run detail",
    "Open findings": "Open findings",
    "Open reasoning run": "Open reasoning run",
    "Open reasoning task": "Open reasoning task",
    "Scoped question created": "Scoped question created",
    "Question is required": "Question is required",
    ready: "ready",
    blocked: "blocked",
    draft: "draft",
    approved: "approved",
    completed: "completed",
    "not run": "not run",
  },
  zh: {
    app_name: "Aletheia 门户",
    shell_title: "推理门户",
    collapse: "收起侧栏",
    expand: "展开侧栏",
    open_nav: "打开导航",
    close_nav: "关闭导航",
    theme: "主题",
    light: "浅色",
    dark: "深色",
    language: "语言",
    english: "EN",
    chinese: "中文",
    workbench: "工作台",
    questions: "问题中心",
    findings: "推理结果",
    evidence: "证据链",
    explore: "探索",
    quality: "质量与异常",
    ontology: "本体",
    runtime: "运行环境",
    audit: "审计",
    "Reasoning Workbench": "推理工作台",
    "Reasoning Loop": "推理闭环",
    "Findings & Evidence": "推理结果与证据",
    "Evidence Chain": "证据链",
    "Graph Explorer": "图谱探索",
    "Instance Explorer": "实例探索",
    "Ontology Review": "本体审核",
    "Quality & Attention": "质量与异常",
    "Question Center": "问题中心",
    "AI Runtime Settings": "AI 运行环境",
    "Knowledge reasoning workspace": "知识推理工作台",
    "What matters now, why it matters, and what needs review.": "当前最重要的结论、原因和待确认事项。",
    "Ask: why is Employee #4 workload unusual?": "提问：为什么 Employee #4 的工作量异常？",
    "Why is Employee #4 workload unusual?": "为什么 Employee #4 的工作量异常？",
    "What evidence would change this conclusion?": "什么证据会改变这个结论？",
    "Search name, key, description": "搜索名称、键或描述",
    "Search Employee by id or name": "按 ID 或姓名搜索 Employee",
    "Reason, comment, or reviewer note": "填写原因、评论或审核备注",
    Ask: "提问",
    "Knowledge space": "知识空间",
    "Covered entities": "覆盖实体",
    Relations: "关系",
    "Reasoning findings": "推理结论",
    "Last update": "最近更新",
    "approved graph scope": "已批准图谱范围",
    "returned graph edges": "返回的图关系",
    "data / reasoning": "数据 / 推理",
    "Key Findings": "关键发现",
    "All findings": "全部结论",
    "Recent Changes": "最近变化",
    "Needs Attention": "需要关注",
    "Quality panel": "质量面板",
    "Quick Tasks": "快速任务",
    "goal first": "目标优先",
    Conclusions: "结论",
    "Reasoning Findings": "推理结果",
    "Conclusion Summary": "结论摘要",
    "Why Summary": "原因摘要",
    "Supporting Evidence": "支持证据",
    "Counter Evidence / Conflicts": "反证 / 冲突",
    "Evidence Browser": "证据浏览",
    "Evidence item": "证据项",
    "Evidence Summary": "证据摘要",
    "Source / Path": "来源 / 路径",
    "Graph Path": "图谱路径",
    "Rule / Ontology Basis": "规则 / 本体依据",
    "Linked Explanation": "关联解释",
    "Follow-up Questions": "继续追问",
    "Ask with scope": "带范围提问",
    "Question -> Answer -> Evidence": "问题 -> 结论 -> 证据",
    "Unified reasoning loop": "统一推理闭环",
    "Ask, inspect the current answer, expand evidence, and review without leaving this page.": "在同一页面完成提问、查看当前结论、展开证据和审核。",
    "Ask with approved graph scope": "基于已批准图谱范围提问",
    "Question & context": "问题与上下文",
    "Create follow-up": "创建追问",
    "Continue follow-up": "继续追问",
    "Follow-up question": "追问问题",
    "Evidence Chain": "证据链",
    "Graph path": "图谱路径",
    "Raw evidence payload": "原始证据载荷",
    "Current task": "当前任务",
    "Latest run": "最近运行",
    "Review gate": "审核门禁",
    "Run reasoning": "运行推理",
    "Open question history": "打开问题历史",
    "Open reasoning loop": "打开推理闭环",
    "Turn a question into a draft-only reasoning task.": "将问题转为仅草稿的推理任务。",
    Question: "问题",
    Scope: "范围",
    "Center node": "中心节点",
    Depth: "深度",
    Limit: "限制",
    "Create scoped question": "创建范围问题",
    "Open graph context": "打开图谱上下文",
    "Question History": "问题历史",
    "Created tasks will appear here with a link to run or inspect them.": "创建后的任务会显示在这里，并提供运行或查看入口。",
    "Draft findings": "草稿结论",
    "Low confidence": "低置信度",
    "Blocked reasoning": "阻塞推理",
    "Agent policy": "Agent 策略",
    "Sandbox gate": "沙箱门禁",
    "awaiting review": "等待审核",
    "needs scrutiny": "需要检查",
    "approved-only / evidence gaps": "仅批准 / 证据缺口",
    "blocked or violations": "阻塞或违规",
    "negative control": "负向控制",
    "Attention Items": "关注事项",
    "Sandbox Approved-only Gaps": "沙箱仅批准缺口",
    "Approved graph": "已批准图谱",
    "Read-only scope": "只读范围",
    "Current Answer": "当前结论",
    Conclusion: "结论",
    "No conclusion has been generated yet. Run this reasoning task to produce a draft finding.": "尚未生成结论。运行此推理任务以生成草稿结论。",
    "Recommended action proposal": "建议动作草案",
    "Counter evidence / limits": "反证 / 限制",
    "Open evidence chain": "打开证据链",
    "Submit review": "提交审核",
    "Request more evidence": "要求补充证据",
    "Rerun reasoning": "重新运行推理",
    "Graph Canvas": "图谱画布",
    "Scoped reasoning": "范围推理",
    "Select graph item": "选择图谱对象",
    "Open scoped reasoning": "打开范围推理",
    Tenant: "租户",
    Namespace: "命名空间",
    "Graph database": "图数据库",
    Search: "搜索",
    Kind: "类型",
    "All evidence": "全部证据",
    Fact: "事实",
    Hypothesis: "假设",
    Conflict: "冲突",
    Missing: "缺失",
    "Current scope": "当前范围",
    "Expand history": "展开历史",
    "Load graph": "加载图谱",
    "Fit view": "适配视图",
    "Focus selected": "聚焦选中",
    Expand: "展开",
    "Collapse expanded": "折叠已展开",
    "No reasoning findings yet. Ask a question or run scoped reasoning to create the first draft finding.": "暂无推理结论。可以先提问或运行范围推理来创建草稿结论。",
    "No high-priority attention items in the current tenant.": "当前租户暂无高优先级关注事项。",
    "No recent reasoning activity.": "暂无最近推理活动。",
    "No reasoning questions yet.": "暂无推理问题。",
    "No active quality issues for this tenant.": "当前租户暂无活跃质量问题。",
    "Sandbox tenant has no missing artifact gaps reported.": "沙箱租户暂无缺失 artifact 缺口。",
    "No supporting evidence recorded.": "暂无支持证据。",
    "No counter evidence or conflicts recorded for this draft.": "该草稿暂无反证或冲突。",
    "No graph context recorded.": "暂无图谱上下文。",
    "Select evidence from the left panel.": "从左侧选择证据。",
    "Open a source, graph path, or conflict item to inspect how it supports or challenges a finding.": "打开来源、图谱路径或冲突项，查看它如何支持或挑战结论。",
    "No expansions yet.": "暂无展开记录。",
    "No instances found.": "未找到实例。",
    "No AgentRun records for this tenant.": "当前租户暂无 AgentRun 记录。",
    "Open explanation": "打开解释",
    "Open source context": "打开来源上下文",
    "Open run detail": "打开运行详情",
    "Open findings": "打开结论",
    "Open reasoning run": "打开推理运行",
    "Open reasoning task": "打开推理任务",
    "Scoped question created": "范围问题已创建",
    "Question is required": "问题不能为空",
    ready: "就绪",
    blocked: "已阻塞",
    draft: "草稿",
    approved: "已批准",
    completed: "已完成",
    "not run": "未运行",
  },
};

const EXACT_TRANSLATIONS = new Map(Object.entries(I18N.zh).filter(([key]) => key.length > 1));
const EXACT_REVERSE = new Map([...EXACT_TRANSLATIONS.entries()].map(([en, zh]) => [zh, en]));

const ATTR_TRANSLATIONS = [
  ["#ask-input", "placeholder", "Ask: why is Employee #4 workload unusual?"],
  ["#question-input", "placeholder", "Why is Employee #4 workload unusual?"],
  ["#followup-input", "placeholder", "What evidence would change this conclusion?"],
  ["#search", "placeholder", "Search name, key, description"],
  ["#instance-query", "placeholder", "Search Employee by id or name"],
  ["#reason", "placeholder", "Reason, comment, or reviewer note"],
];

function currentLang() {
  return localStorage.getItem(SHELL_STORAGE.lang) || "en";
}

function t(key, vars = {}) {
  const lang = currentLang();
  let text = I18N[lang]?.[key] || I18N.en[key] || key;
  Object.entries(vars).forEach(([name, value]) => {
    text = text.replaceAll(`{${name}}`, value);
  });
  return text;
}

function setTheme(theme) {
  const next = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = next;
  localStorage.setItem(SHELL_STORAGE.theme, next);
  document.querySelector("#shell-theme-toggle")?.setAttribute("aria-pressed", String(next === "dark"));
  const label = document.querySelector("#shell-theme-label");
  if (label) label.textContent = next === "dark" ? t("dark") : t("light");
}

function setLang(lang) {
  const next = lang === "zh" ? "zh" : "en";
  localStorage.setItem(SHELL_STORAGE.lang, next);
  document.documentElement.lang = next === "zh" ? "zh-CN" : "en";
  document.querySelector("#shell-lang-toggle")?.setAttribute("aria-pressed", String(next === "zh"));
  translateShell();
}

function setCollapsed(collapsed) {
  document.body.classList.toggle("shell-collapsed", collapsed);
  localStorage.setItem(SHELL_STORAGE.collapsed, collapsed ? "1" : "0");
  const button = document.querySelector("#shell-collapse");
  if (button) {
    button.textContent = collapsed ? "›" : "‹";
    button.title = collapsed ? t("expand") : t("collapse");
    button.setAttribute("aria-label", collapsed ? t("expand") : t("collapse"));
  }
}

function setMobileOpen(open) {
  document.body.classList.toggle("shell-open", open);
  const button = document.querySelector("#shell-mobile-toggle");
  if (button) button.setAttribute("aria-label", open ? t("close_nav") : t("open_nav"));
}

function buildShell() {
  const shell = document.querySelector(".portal-shell");
  if (!shell || shell.dataset.enhanced === "1") return;
  shell.dataset.enhanced = "1";
  document.body.classList.add("shell-enhanced");

  const nav = shell.querySelector(".portal-nav");
  NAV_ITEMS.forEach(([selector, key, icon]) => {
    const item = shell.querySelector(selector);
    if (!item) return;
    item.dataset.i18nKey = key;
    item.dataset.icon = icon;
    item.innerHTML = `<span class="nav-icon" aria-hidden="true">${icon}</span><span class="nav-label">${t(key)}</span>`;
    item.addEventListener("click", () => setMobileOpen(false));
  });

  const controls = document.createElement("section");
  controls.className = "shell-controls";
  controls.innerHTML = `
    <button id="shell-theme-toggle" class="shell-control" type="button" aria-pressed="false">
      <span aria-hidden="true">◐</span><span id="shell-theme-label">${t("light")}</span>
    </button>
    <button id="shell-lang-toggle" class="shell-control" type="button" aria-pressed="false">
      <span aria-hidden="true">文</span><span id="shell-lang-label">${t("english")}</span>
    </button>
    <button id="shell-collapse" class="shell-collapse" type="button" aria-label="${t("collapse")}" title="${t("collapse")}">‹</button>
  `;
  shell.appendChild(controls);

  const mobileButton = document.createElement("button");
  mobileButton.id = "shell-mobile-toggle";
  mobileButton.className = "shell-mobile-toggle";
  mobileButton.type = "button";
  mobileButton.textContent = "☰";
  mobileButton.setAttribute("aria-label", t("open_nav"));
  document.body.appendChild(mobileButton);

  document.querySelector("#shell-theme-toggle")?.addEventListener("click", () => {
    setTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
    translateShell();
  });
  document.querySelector("#shell-lang-toggle")?.addEventListener("click", () => {
    setLang(currentLang() === "zh" ? "en" : "zh");
  });
  document.querySelector("#shell-collapse")?.addEventListener("click", () => {
    setCollapsed(!document.body.classList.contains("shell-collapsed"));
  });
  mobileButton.addEventListener("click", () => setMobileOpen(!document.body.classList.contains("shell-open")));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setMobileOpen(false);
  });
  nav?.addEventListener("click", (event) => {
    if (event.target.closest("a")) setMobileOpen(false);
  });
}

function applyTenantNavLinks() {
  const params = new URLSearchParams(window.location.search);
  const tenant = params.get("tenant") || "default";
  Object.entries(NAV_PATHS).forEach(([selector, path]) => {
    const el = document.querySelector(selector);
    if (!el) return;
    const query = new URLSearchParams();
    query.set("tenant", tenant);
    if (path === "/graph.html") {
      query.set("type", "Employee");
      query.set("id", "4");
      query.set("depth", "1");
      query.set("limit", "200");
    }
    el.href = `${path}?${query.toString()}`;
  });
}

function translateExactText(root = document.body) {
  const lang = currentLang();
  const source = lang === "zh" ? EXACT_TRANSLATIONS : EXACT_REVERSE;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const skipSelector = "pre, code, .code-block, .source-ref, .graph-svg, #payload, #edit-payload";
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    const parent = node.parentElement;
    if (!parent || parent.closest(skipSelector)) return;
    const raw = node.nodeValue;
    const trimmed = raw.trim();
    if (!trimmed || !source.has(trimmed)) return;
    node.nodeValue = raw.replace(trimmed, source.get(trimmed));
  });
}

function translateShell() {
  const lang = currentLang();
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  NAV_ITEMS.forEach(([selector, key]) => {
    const item = document.querySelector(selector);
    const label = item?.querySelector(".nav-label");
    if (label) label.textContent = t(key);
  });
  const brandEyebrow = document.querySelector(".portal-brand .eyebrow");
  if (brandEyebrow) brandEyebrow.textContent = t("app_name");
  const themeLabel = document.querySelector("#shell-theme-label");
  if (themeLabel) themeLabel.textContent = document.body.dataset.theme === "dark" ? t("dark") : t("light");
  const langLabel = document.querySelector("#shell-lang-label");
  if (langLabel) langLabel.textContent = lang === "zh" ? t("chinese") : t("english");
  const collapse = document.querySelector("#shell-collapse");
  if (collapse) {
    const collapsed = document.body.classList.contains("shell-collapsed");
    collapse.title = collapsed ? t("expand") : t("collapse");
    collapse.setAttribute("aria-label", collapsed ? t("expand") : t("collapse"));
  }
  const mobile = document.querySelector("#shell-mobile-toggle");
  if (mobile) mobile.setAttribute("aria-label", document.body.classList.contains("shell-open") ? t("close_nav") : t("open_nav"));
  ATTR_TRANSLATIONS.forEach(([selector, attr, key]) => {
    const el = document.querySelector(selector);
    if (el) el.setAttribute(attr, t(key));
  });
  if (lang === "zh") translateExactText();
}

window.AletheiaShell = {
  t,
  lang: currentLang,
  translate: translateShell,
};

buildShell();
applyTenantNavLinks();
setTheme(localStorage.getItem(SHELL_STORAGE.theme) || "light");
setCollapsed(localStorage.getItem(SHELL_STORAGE.collapsed) === "1");
setLang(currentLang());

let translateTimer = null;
const observer = new MutationObserver(() => {
  window.clearTimeout(translateTimer);
  translateTimer = window.setTimeout(translateShell, 40);
});
observer.observe(document.body, { childList: true, subtree: true });
