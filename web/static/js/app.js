(function () {
  "use strict";

  var state = {
    user: null,
    view: "dashboard",
    profile: {},
    jobs: [],
    jobsTotal: 0,
    jobFacets: { cities: [], types: [] },
    applications: [],
    selectedJobId: null,
    jobFilter: "",
    jobCityFilter: "",
    jobTypeFilter: "",
    jobSourceFilter: "",
    jobDeadlineFilter: "",
    jobSort: "score",
    searchResults: [],
    searchSkipped: [],
    searchHistory: [],
    searchMode: null,
    advancedExpanded: false,
    jobSearchMode: "local",
    onlineSearchAvailable: false,
    interviewJobId: null,
    interviewContent: "",
    chatOpen: false,
    settings: null
  };

  try {
    state.searchHistory = JSON.parse(localStorage.getItem("careerpilot_search_history") || "[]").filter(Boolean).slice(0, 8);
  } catch (e) { state.searchHistory = []; }

  var el = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  async function api(path, options) {
    options = options || {};
    var opts = {
      method: options.method || "GET",
      headers: { "Content-Type": "application/json" },
      credentials: "include"
    };
    if (options.body !== undefined) opts.body = JSON.stringify(options.body);
    var resp = await fetch("/api/" + path, opts);
    var data;
    try { data = await resp.json(); } catch (e) { data = {}; }
    if (resp.status === 401) {
      state.user = null;
      state.profile = {};
      showAuth();
      throw new Error("登录已过期，请重新登录");
    }
    if (!resp.ok || data.ok === false) {
      var error = new Error(data.error || data.message || ("请求失败 " + resp.status));
      error.status = resp.status;
      throw error;
    }
    return data;
  }

  function toast(msg, type) {
    var wrap = el("toasts");
    if (!wrap) return;
    var t = document.createElement("div");
    t.className = "toast" + (type ? " " + type : "");
    t.setAttribute("role", type === "error" ? "alert" : "status");
    t.setAttribute("aria-live", type === "error" ? "assertive" : "polite");
    t.textContent = msg;
    wrap.appendChild(t);
    setTimeout(function () { t.remove(); }, 3200);
  }

  function scoreClass(score) {
    if (score >= 75) return "good";
    if (score >= 45) return "mid";
    if (score > 0) return "low";
    return "zero";
  }

  function profileEmpty() {
    var p = state.profile || {};
    var skills = p.skills || {};
    var hasSkills = [skills.strong, skills.moderate, skills.weak].some(function (v) {
      return Array.isArray(v) ? v.some(Boolean) : Boolean(v);
    });
    var hasGoals = Array.isArray(p.career_goals) ? p.career_goals.some(Boolean) : Boolean(p.career_goals);
    return !p.name && !p.school && !p.major && !hasSkills && !hasGoals;
  }

  function verdictTag(verdict) {
    var cls = "tag-accent";
    if (verdict.indexOf("不建议") >= 0) cls = "tag-danger";
    else if (verdict.indexOf("可考虑") >= 0 || verdict.indexOf("谨慎") >= 0) cls = "tag-warn";
    return '<span class="tag ' + cls + '">' + esc(verdict) + "</span>";
  }

  function stageInfo(stage) {
    var map = {
      "已收藏": { cls: "saved", next: "已投递" },
      "已投递": { cls: "applied", next: "面试中" },
      "面试中": { cls: "interview", next: "Offer" },
      "Offer": { cls: "offer", next: "" },
      "已归档": { cls: "archived", next: "" }
    };
    return map[stage] || { cls: "saved", next: "已投递" };
  }

  function renderMarkdown(text) {
    if (!text) return "";
    var lines = String(text).split("\n");
    var out = [];
    var inList = false;
    lines.forEach(function (line) {
      var t = line;
      if (/^#{1,3}\s/.test(t)) {
        if (inList) { out.push("</div>"); inList = false; }
        var level = t.match(/^#+/)[0].length;
        out.push("<h" + level + ">" + esc(t.replace(/^#+\s*/, "")) + "</h" + level + ">");
        return;
      }
      if (/^[-*]\s/.test(t)) {
        if (!inList) { out.push('<div class="bullet-list plain">'); inList = true; }
        out.push("<li>" + esc(t.replace(/^[-*]\s*/, "")) + "</li>");
        return;
      }
      if (inList) { out.push("</div>"); inList = false; }
      if (!t.trim()) { out.push(""); return; }
      var bold = esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      out.push("<div>" + bold + "</div>");
    });
    if (inList) out.push("</div>");
    return out.join("");
  }

  function barChart(ev) {
    var dims = [
      { label: "技能", key: "skill" },
      { label: "经历", key: "experience" },
      { label: "文化", key: "culture" },
      { label: "职业", key: "career" }
    ];
    return (
      '<div class="dim-bars">' +
      dims.map(function (d) {
        var score = (ev.dimensions[d.key] && ev.dimensions[d.key].score) || 0;
        return (
          '<div class="dim-bar-row">' +
          '<span class="dim-bar-label">' + d.label + "</span>" +
          '<div class="dim-bar-track"><div class="dim-bar-fill" style="width:' + score + '%"></div></div>' +
          '<span class="dim-bar-num">' + score + "</span>" +
          "</div>"
        );
      }).join("") +
      "</div>"
    );
  }

  function jobCard(job, searchResult, searchIndex) {
    var ev = job.evaluation || {};
    var score = ev.overall || 0;
    var needs = !!ev.needs_profile;
    var cls = needs ? "zero" : scoreClass(score);
    var scoreText = needs ? "—" : score;
    var trust = jobTrust(job);
    var source = '<span class="tag ' + trust.cls + '">' + esc(trust.label) + '</span>';
    var prefilter = ev.gates && ev.gates.prefilter;
    var prefilterTag = prefilter ? '<span class="tag ' + (prefilter.status === "recommend" ? "tag-accent" : prefilter.status === "reject" ? "tag-danger" : "tag-warn") + '">' + esc(prefilter.label) + '</span>' : "";
    var deadline = job.deadline
      ? '<span class="tag ' + (job.deadline < new Date().toISOString().slice(0, 10) ? "tag-danger" : "tag-warn") + '">截止 ' + esc(job.deadline) + "</span>"
      : "";
    var searchAction = "";
    if (searchResult) {
      searchAction = job.addedThisSearch
        ? '<button class="btn btn-sm" data-undo-search="' + searchIndex + '">撤销加入</button>'
        : job.saved_job_id
          ? '<span class="tag tag-accent">已在岗位库</span>'
          : '<button class="btn btn-sm btn-primary" data-add-search="' + searchIndex + '">加入岗位库</button>';
    }
    return (
      '<div class="list-row job-item' + (state.selectedJobId === job.id && !searchResult ? " selected" : "") + '" data-job="' + esc(job.id) + '"' + (searchResult ? ' data-search-result="true"' : "") + ">" +
      '<span class="score-badge ' + cls + '">' + scoreText + "</span>" +
      '<div class="row-main">' +
      '<div class="row-title-wrap"><span class="row-title">' + esc(job.title) + "</span>" + source + prefilterTag + "</div>" +
      '<div class="row-sub">' + esc(job.company) + " · " + esc(job.city) + " · " + esc(job.salary || "薪资未标注") + "</div>" +
      "</div>" +
      '<div class="row-meta">' + deadline + searchAction + "</div>" +
      "</div>"
    );
  }

  function statCard(label, value, cls, foot) {
    return (
      '<div class="panel stat-card"><div class="stat-label"><span>' + esc(label) + "</span></div>" +
      '<div class="stat-value ' + cls + '">' + value + "</div>" +
      '<div class="stat-foot">' + esc(foot) + "</div></div>"
    );
  }

  function jobTrust(job) {
    if (job.is_demo) return { label: "示例，仅供熟悉", cls: "tag-warn", rank: 0 };
    if (job.source === "llm_suggested") return { label: "AI 建议，需核实", cls: "tag-warn", rank: 1 };
    if (job.url && /^https?:\/\//i.test(job.url)) return { label: "含真实链接", cls: "tag-accent", rank: 3 };
    return { label: "来源待核实", cls: "tag-warn", rank: 2 };
  }

  function actionJobs() {
    var today = new Date().toISOString().slice(0, 10);
    return state.jobs.filter(function (job) {
      var ev = job.evaluation || {}, pf = ev.gates && ev.gates.prefilter;
      var applied = state.applications.some(function (a) { return a.job_id === job.id && a.stage !== "已归档"; });
      return !job.is_demo && job.url && !applied && (!pf || pf.status !== "reject") && (!job.deadline || job.deadline >= today);
    }).sort(function (a, b) {
      var ta = jobTrust(a).rank, tb = jobTrust(b).rank;
      if (ta !== tb) return tb - ta;
      if (a.deadline && b.deadline) return a.deadline < b.deadline ? -1 : a.deadline > b.deadline ? 1 : 0;
      if (a.deadline && !b.deadline) return -1;
      if (b.deadline && !a.deadline) return 1;
      return ((b.evaluation || {}).overall || 0) - ((a.evaluation || {}).overall || 0);
    }).slice(0, 3);
  }

  function emptyBlock(title, sub) {
    return (
      '<div class="empty"><strong>' + esc(title) + "</strong><span>" + esc(sub) + "</span></div>"
    );
  }

  /* ---------------- Auth ---------------- */

  function showAuth() {
    el("authScreen").classList.remove("hide");
    el("app").classList.add("hide");
  }

  function showApp() {
    el("authScreen").classList.add("hide");
    el("app").classList.remove("hide");
  }

  function setUser(user) {
    state.user = user;
    state.profile = (user && user.profile) || {};
    if (!user) {
      showAuth();
      return;
    }
    showApp();
    el("userName").textContent = user.username || "用户";
    el("userAvatar").textContent = (user.username || "U").charAt(0).toUpperCase();
    var roleLabel = user.role === "guest" ? "游客" : user.role === "admin" ? "管理员" : "用户";
    el("userRole").textContent = roleLabel;
    el("menuUpgrade").classList.toggle("hide", user.role !== "guest");
    updateProfileBanner();
  }

  function updateProfileBanner() {
    var empty = !state.profile || !state.profile.name || !state.profile.skills || !state.profile.career_goals;
    el("profileBanner").classList.toggle("hide", !empty);
  }

  async function login(username, password, remember) {
    var data = await api("auth/login", { method: "POST", body: { username: username, password: password, remember: remember } });
    setUser(data.user);
    toast("登录成功", "success");
    await bootApp();
  }

  async function register(username, email, password) {
    var data = await api("auth/register", { method: "POST", body: { username: username, email: email, password: password } });
    setUser(data.user);
    toast("注册成功，去简历库上传简历吧", "success");
    await bootApp();
  }

  async function guest() {
    var data = await api("auth/guest", { method: "POST", body: {} });
    setUser(data.user);
    toast("游客模式已开启", "success");
    await bootApp();
  }

  async function upgrade(username, email, password) {
    var data = await api("auth/upgrade", { method: "POST", body: { username: username, email: email, password: password } });
    setUser(data.user);
    el("upgradeModal").classList.remove("open");
    toast("转正成功，数据已保留", "success");
  }

  async function logout() {
    try {
      await api("auth/logout", { method: "POST", body: {} });
    } catch (e) {}
    state.user = null;
    state.profile = {};
    state.jobs = [];
    state.applications = [];
    showAuth();
  }

  async function loadMe() {
    var data = await api("auth/me");
    if (data.ok && data.user) setUser(data.user);
    else showAuth();
  }

  /* ---------------- Views ---------------- */

  function renderDashboard() {
    var jobs = state.jobs;
    var apps = state.applications;
    var evs = jobs.map(function (j) { return j.evaluation || {}; }).filter(function (e) { return e.overall; });
    var avg = evs.length ? Math.round(evs.reduce(function (a, e) { return a + e.overall; }, 0) / evs.length) : 0;
    var applied = apps.filter(function (a) { return ["已投递", "面试中", "Offer"].indexOf(a.stage) >= 0; }).length;
    var interviewing = apps.filter(function (a) { return a.stage === "面试中"; }).length;
    var offers = apps.filter(function (a) { return a.stage === "Offer"; }).length;
    var upcoming = jobs.filter(function (j) { return j.deadline && j.deadline >= new Date().toISOString().slice(0, 10); }).length;

    var strong = jobs.filter(function (j) { return (j.evaluation || {}).overall >= 75; });
    var good = jobs.filter(function (j) { var s = (j.evaluation || {}).overall; return s >= 60 && s < 75; });
    var mid = jobs.filter(function (j) { var s = (j.evaluation || {}).overall; return s >= 45 && s < 60; });
    var low = jobs.filter(function (j) { var s = (j.evaluation || {}).overall; return s < 45; });
    var maxBand = Math.max(1, strong.length, good.length, mid.length, low.length);
    var bands = [
      { label: "强烈匹配", n: strong.length, cls: "high" },
      { label: "建议申请", n: good.length, cls: "mid" },
      { label: "可考虑", n: mid.length, cls: "mid" },
      { label: "谨慎/跳过", n: low.length, cls: "low" }
    ];
    var chart = '<div class="chart-wrap">' + bands.map(function (b) {
      var h = Math.max(6, Math.round((b.n / maxBand) * 165));
      return (
        '<div class="bar-col"><span class="bar-num">' + b.n + '</span>' +
        '<div class="bar-track"><div class="bar ' + (b.cls === "low" ? "low" : b.cls === "mid" ? "mid" : "") + '" style="height:' + h + 'px"></div></div>' +
        '<span class="bar-label">' + b.label + "</span></div>"
      );
    }).join("") + "</div>";

    var empty = profileEmpty();
    var topJobs = empty
      ? jobs.slice().sort(function (a, b) { return (b.created_at || "").localeCompare(a.created_at || ""); }).slice(0, 6)
      : jobs.slice().sort(function (a, b) { return (b.evaluation || {}).overall - (a.evaluation || {}).overall; }).slice(0, 6);
    var recentApps = apps.slice().slice(0, 6);
    var todayJobs = actionJobs();
    var todayActions = todayJobs.length
      ? '<div class="panel mb-14"><div class="panel-head"><strong>今天先处理这 ' + todayJobs.length + ' 个岗位</strong><span class="sub">按链接可信度、截止日期和匹配度排序</span></div><div class="list">' + todayJobs.map(function (job) {
        var ev = job.evaluation || {}, trust = jobTrust(job), pf = ev.gates && ev.gates.prefilter;
        return '<div class="list-row"><span class="score-badge ' + scoreClass(ev.overall || 0) + '">' + (ev.overall || "—") + '</span><div class="row-main"><div class="row-title">' + esc(job.title) + '</div><div class="row-sub">' + esc(job.company) + ' · ' + esc(job.city || "地点待确认") + ' · ' + '<span class="tag ' + trust.cls + '">' + esc(trust.label) + '</span>' + (job.deadline ? ' · 截止 ' + esc(job.deadline) : '') + '</div><div class="muted text-sm">' + esc((pf && pf.reasons && pf.reasons[0]) || (ev.summary || "打开岗位详情，确认要求后开始申请")) + '</div></div><button class="btn btn-sm btn-primary" data-open-job="' + esc(job.id) + '">查看并处理</button></div>';
      }).join('') + '</div></div>'
      : '<div class="panel mb-14"><div class="panel-head"><strong>今天的求职行动</strong></div><div class="panel-body"><div class="empty"><strong>还没有可直接处理的真实岗位</strong><span>去「找真实岗位」搜索并核实链接，加入岗位库后这里会自动生成今日清单。</span><button class="btn btn-primary mt-8" data-open-jobs>去找岗位</button></div></div></div>';

    el("content").innerHTML =
      '<div class="content-inner">' +
      '<div class="page-head"><div><h1>求职总览</h1><p>你的 AI 求职工作台：跟踪岗位匹配、申请进度与面试准备。</p></div></div>' +
      (empty ? '<div class="panel mb-14"><div class="panel-head"><strong>从这里开始</strong><span class="sub">完成后即可获得更准确的岗位推荐</span></div><div class="panel-body"><div class="onboarding"><button class="onboarding-step" data-onboard="profile"><b>1</b><span><strong>完善校园档案</strong><small>学校、专业、毕业时间和求职城市</small></span></button><button class="onboarding-step" data-onboard="profile"><b>2</b><span><strong>上传简历</strong><small>识别后逐项确认写入</small></span></button><button class="onboarding-step" data-onboard="jobs"><b>3</b><span><strong>搜索岗位</strong><small>筛选岗位并查看匹配度</small></span></button><button class="onboarding-step" data-onboard="pipeline"><b>4</b><span><strong>跟踪投递</strong><small>收藏、投递、面试和 Offer</small></span></button></div></div></div>' : '') +
      todayActions +
      '<div class="stat-grid">' +
      statCard("岗位池", jobs.length, "stat-accent", "内置 + 手动录入") +
      statCard("平均匹配度", empty ? "—" : avg, "stat-info", empty ? "完善档案后启用个性化匹配" : "按五维框架评分") +
      statCard("已投递", applied, "stat-accent", interviewing ? "其中 " + interviewing + " 个面试中" : "等待推进") +
      statCard("Offer", offers, offers ? "stat-warn" : "", upcoming ? upcoming + " 个岗位即将截止" : "加油推进") +
      "</div>" +
      '<div class="grid-2">' +
      '<div class="panel"><div class="panel-head"><strong>岗位匹配分布</strong><span class="sub">按综合评分分档</span></div><div class="panel-body">' + (jobs.length ? chart : emptyBlock("岗位库为空", "在「岗位搜索」中录入或查看示例岗位")) + "</div></div>" +
      '<div class="panel"><div class="panel-head"><strong>' + (empty ? "热门岗位" : "高匹配岗位") + '</strong><span class="sub">' + (empty ? "最新收录" : "评分前 6 名") + "</span></div><div class=\"panel-body\" style=\"padding:0\">" +
      (topJobs.length ? topJobs.map(jobCard).join("") : emptyBlock("暂无岗位", "")) +
      "</div></div>" +
      "</div>" +
      '<div class="mt-14 panel"><div class="panel-head"><strong>申请动态</strong><span class="sub">最近更新</span></div><div class="panel-body" style="padding:0">' +
      (recentApps.length ? recentApps.map(function (a) {
        var si = stageInfo(a.stage);
        return (
          '<div class="list-row">' +
          '<div class="row-main"><div class="row-title">' + esc(a.title) + "</div>" +
          '<div class="row-sub">' + esc(a.company) + " · " + esc(a.city) + "</div></div>" +
          '<span class="stage ' + si.cls + '">' + esc(a.stage) + "</span>" +
          "</div>"
        );
      }).join("") : emptyBlock("还没有申请记录", "在岗位详情页点击「开始申请」即可创建记录")) +
      "</div></div>" +
      "</div>";
    bindJobItems();
    document.querySelectorAll("[data-onboard]").forEach(function (button) {
      button.addEventListener("click", function () { location.hash = "#/" + button.getAttribute("data-onboard"); });
    });
    document.querySelectorAll("[data-open-job]").forEach(function (button) {
      button.addEventListener("click", function () {
        state.selectedJobId = button.getAttribute("data-open-job");
        state.view = "jobs";
        location.hash = "#/jobs";
      });
    });
    document.querySelectorAll("[data-open-jobs]").forEach(function (button) {
      button.addEventListener("click", function () { state.view = "jobs"; location.hash = "#/jobs"; });
    });
  }

  function renderJobs() {
    var jobs = state.jobs.slice();
    var hasFilters = Boolean(state.jobFilter || state.jobCityFilter || state.jobTypeFilter || state.jobSourceFilter || state.jobDeadlineFilter || (state.searchResults && state.searchResults.length));
    var realJobs = jobs.filter(function (job) { return !job.is_demo; });
    var showingDemoOnly = !hasFilters && !realJobs.length && jobs.some(function (job) { return job.is_demo; });
    if (showingDemoOnly) jobs = [];

    var selected = showingDemoOnly ? null : (state.jobs.find(function (j) { return j.id === state.selectedJobId; }) || jobs[0]);
    if (selected) state.selectedJobId = selected.id;

    var listHtml =
      '<div class="panel jobs-list">' +
      '<div class="panel-head"><strong>岗位库</strong><span class="sub">共 ' + state.jobsTotal + " 个</span></div>" +
      '<div class="panel-body" style="padding:10px 12px">' +
      '<input id="jobSearch" type="text" style="flex:1;min-height:34px;padding:0 10px;border:1px solid var(--border-strong);border-radius:6px;outline:none" placeholder="搜索标题、公司、标签…" value="' + esc(state.jobFilter) + '">' +
      '<div class="job-filters mt-8">' +
      '<select id="jobCityFilter"><option value="">全部城市</option>' + selectOptions(state.jobFacets.cities || [], state.jobCityFilter) + '</select>' +
      '<select id="jobTypeFilter"><option value="">全部类型</option>' + selectOptions(state.jobFacets.types || [], state.jobTypeFilter) + '</select>' +
      '<select id="jobSourceFilter"><option value="">全部来源</option><option value="demo"' + selectedAttr(state.jobSourceFilter, "demo") + '>示例岗位</option><option value="local"' + selectedAttr(state.jobSourceFilter, "local") + '>本地筛选结果</option><option value="llm"' + selectedAttr(state.jobSourceFilter, "llm") + '>LLM 建议岗位</option><option value="web"' + selectedAttr(state.jobSourceFilter, "web") + '>真实网页解析岗位</option></select>' +
      '<select id="jobDeadlineFilter"><option value="">全部截止日期</option><option value="3"' + selectedAttr(state.jobDeadlineFilter, "3") + '>3 天内截止</option><option value="7"' + selectedAttr(state.jobDeadlineFilter, "7") + '>7 天内截止</option><option value="30"' + selectedAttr(state.jobDeadlineFilter, "30") + '>30 天内截止</option></select></div>' +
      '<div class="flex mt-8" style="gap:6px">' +
      '<button class="btn btn-sm' + (state.jobSort === "score" ? " btn-primary" : "") + '" data-sort="score">按匹配度</button>' +
      '<button class="btn btn-sm' + (state.jobSort === "deadline" ? " btn-primary" : "") + '" data-sort="deadline">按截止</button>' +
      '<button class="btn btn-sm' + (state.jobSort === "new" ? " btn-primary" : "") + '" data-sort="new">最新</button>' +
      "</div></div>" +
      '<div class="list" style="border-top:1px solid var(--border)">' +
      (showingDemoOnly ? '<div class="real-job-guide"><strong>岗位库暂时没有真实岗位</strong><span>示例岗位只用于熟悉界面，不建议直接投递。请使用上方的联网搜索、按公司搜索，或粘贴真实岗位链接。</span></div>' : (jobs.length ? jobs.map(jobCard).join("") : emptyBlock("没有匹配的岗位", "换个关键词试试"))) +
      "</div>" +
      (!showingDemoOnly && state.jobs.length < state.jobsTotal ? '<div class="panel-body" style="padding:10px 12px;border-top:1px solid var(--border)"><button class="btn btn-sm" id="loadMoreJobs">加载更多（已显示 ' + state.jobs.length + " / 共 " + state.jobsTotal + "）</button></div>" : "") +
      "</div>";

    var detailHtml = selected ? jobDetail(selected) : '<div class="panel"><div class="panel-body">' + emptyBlock("选择岗位查看评估", "") + "</div></div>";

    var searchHtml = "";
    var pendingSearchCount = (state.searchResults || []).filter(function (job) { return job.source === "local" && !job.saved_job_id; }).length;
    if (state.searchResults && state.searchResults.length) {
      searchHtml =
        '<div class="panel mb-14" id="searchResultsPanel"><div class="panel-head">' +
        '<strong>搜索结果</strong><span class="sub">' + state.searchResults.length + " 个 · 关键词：" + esc(state.searchKeyword || "") + "</span></div>" +
        '<div class="panel-body" style="padding:10px 12px">' +
        '<div class="flex" style="gap:8px;margin-bottom:10px">' +
        '<button class="btn btn-sm btn-primary" id="addSearchAll"' + (pendingSearchCount ? "" : " disabled") + '>全部加入岗位库' + (pendingSearchCount ? "（" + pendingSearchCount + "）" : "") + "</button>" +
        '<button class="btn btn-sm" id="clearSearchResults">清除搜索结果</button>' +
        "</div>" +
        '<div class="list" style="border-top:1px solid var(--border)">' +
        state.searchResults.map(function (job, index) { return jobCard(job, true, index); }).join("") +
        "</div>" +
        ((state.searchSkipped || []).length ? '<details class="merge-skipped mt-8"><summary>部分结果处理失败（' + state.searchSkipped.length + "）</summary>" + state.searchSkipped.map(function (item) { return '<div class="text-sm muted">· ' + esc(item.url || "未知链接") + "：" + esc(item.reason || "未知原因") + "</div>"; }).join("") + "</details>" : "") +
        "</div></div>";
    } else if (state.searchMode) {
      searchHtml = '<div class="panel mb-14"><div class="panel-body">' + emptyBlock("没有找到匹配岗位", "试试换关键词，或开启 AI 模式后搜索") +
        '<div class="flex mt-8" style="justify-content:flex-end"><button class="btn btn-sm" id="clearSearchResults">清除搜索结果</button></div></div></div>';
    }

    el("content").innerHTML =
      '<div class="content-inner">' +
      '<div class="page-head"><div><h1>找真实岗位</h1><p>优先使用真实招聘信息，再用你的档案进行匹配评分。</p></div></div>' +
      '<div class="panel job-parse-bar mb-14"><div class="panel-body">' +
      '<div class="job-entry-title">三种方式找到真实岗位</div>' +
      '<div class="search-mode-switch" role="group" aria-label="搜索模式"><button class="btn btn-sm' + (state.jobSearchMode === "local" ? " btn-primary" : "") + '" id="modeLocal">本地模式</button><button class="btn btn-sm' + (state.jobSearchMode === "online" ? " btn-primary" : "") + '" id="modeOnline">线上模式</button><span class="tag" id="aiModeBadge">检测中…</span></div>' +
      '<div class="flex" style="gap:8px;flex-wrap:wrap;margin-bottom:10px"><input id="onlineSearchKeyword" type="text" style="flex:1;min-width:220px;min-height:36px;padding:0 12px;border:1px solid var(--border-strong);border-radius:6px;outline:none" placeholder="例如：AI产品运营、游戏策划、模型评测"><button class="btn btn-primary" id="btnOnlineSearch">搜索候选岗位</button></div>' +
      '<div class="job-entry-hint">① 本地模式：筛选已有岗位　② AI 模式：生成候选并标注“需核实”　③ 公司搜索 / 岗位网址：优先获取真实链接</div>' +
      (state.searchHistory.length ? '<div class="flex" style="gap:6px;flex-wrap:wrap;margin-bottom:10px"><span class="muted text-sm">最近搜索</span>' + state.searchHistory.map(function (keyword) { return '<button class="btn btn-sm" data-search-history="' + esc(keyword) + '">' + esc(keyword) + "</button>"; }).join("") + "</div>" : "") +
      '<div class="flex" style="gap:8px;flex-wrap:wrap;margin-bottom:10px">' +
      '<input id="companySearchName" type="text" style="flex:1;min-width:200px;min-height:36px;padding:0 12px;border:1px solid var(--border-strong);border-radius:6px;outline:none" placeholder="心仪公司名，如：网易" value="' + esc(state.companySearch || "") + '">' +
      '<input id="companySearchCity" type="text" style="width:130px;min-height:36px;padding:0 12px;border:1px solid var(--border-strong);border-radius:6px;outline:none" placeholder="城市（可选）" value="' + esc(state.companySearchCity || "") + '">' +
      '<button class="btn btn-primary" id="btnCompanySearch">按公司搜索</button>' +
      "</div>" +
      '<div id="companySearchStatus" class="mt-8" role="status" aria-live="polite" aria-atomic="true"></div>' +
      '<div class="flex" style="gap:8px;flex-wrap:wrap">' +
      '<input id="jobUrlInput" type="url" style="flex:1;min-width:280px;min-height:36px;padding:0 12px;border:1px solid var(--border-strong);border-radius:6px;outline:none" placeholder="粘贴岗位网址，自动提取岗位与要求">' +
      '<button class="btn btn-primary" id="jobUrlParse">快速解析</button>' +
      "</div>" +
      '<div id="jobParseStatus" class="mt-8" role="status" aria-live="polite" aria-atomic="true"></div>' +
      "</div></div>" +
      searchHtml +
      '<div class="jobs-layout">' + listHtml + '<div class="jobs-detail">' + detailHtml + "</div></div>" +
      "</div>";

    bindSearchResultsActions();
    var loadMore = el("loadMoreJobs");
    if (loadMore) loadMore.addEventListener("click", loadMoreJobs);

    var search = el("jobSearch");
    if (search) {
      search.addEventListener("input", function () {
        state.jobFilter = search.value;
        clearTimeout(state.jobFilterTimer);
        state.jobFilterTimer = setTimeout(function () { loadJobs().then(renderJobs).catch(function (e) { toast(e.message, "error"); }); }, 250);
      });
    }
    [["jobCityFilter", "jobCityFilter"], ["jobTypeFilter", "jobTypeFilter"], ["jobSourceFilter", "jobSourceFilter"], ["jobDeadlineFilter", "jobDeadlineFilter"]].forEach(function (item) {
      var filter = el(item[0]);
      if (filter) filter.addEventListener("change", function () { state[item[1]] = filter.value; loadJobs().then(renderJobs).catch(function (e) { toast(e.message, "error"); }); });
    });
    document.querySelectorAll("[data-sort]").forEach(function (btn) {
      btn.addEventListener("click", function () { state.jobSort = btn.getAttribute("data-sort"); loadJobs().then(renderJobs).catch(function (e) { toast(e.message, "error"); }); });
    });
    bindJobParse();
    bindOnlineSearch();
    bindCompanySearch();
    bindJobItems();
  }

  function selectedAttr(value, expected) { return value === expected ? " selected" : ""; }
  function selectOptions(values, selected) {
    return Array.from(new Set(values.filter(Boolean))).sort().map(function (value) {
      return '<option value="' + esc(value) + '"' + selectedAttr(selected, value) + '>' + esc(value) + '</option>';
    }).join("");
  }

  function bindOnlineSearch() {
    var btn = el("btnOnlineSearch"), badge = el("aiModeBadge"), localBtn = el("modeLocal"), onlineBtn = el("modeOnline");
    if (!btn) return;
    if (localBtn) localBtn.addEventListener("click", function () { state.jobSearchMode = "local"; renderJobs(); });
    if (onlineBtn) onlineBtn.addEventListener("click", function () { state.jobSearchMode = "online"; renderJobs(); });
    api("jobs/search").then(function (res) {
      var data = res.data; btn.disabled = false;
      state.onlineSearchAvailable = !!data.enabled;
      badge.textContent = data.enabled ? "线上服务可用 · " + data.provider : "线上服务未配置";
      btn.textContent = state.jobSearchMode === "online" ? "联网搜索" : "搜索本地岗位";
      btn.title = data.enabled ? "" : "线上模式需要先在设置中配置 AI 服务";
      if (state.jobSearchMode === "online" && !data.enabled) badge.textContent = "线上服务未配置，请先到设置开启";
    });
    var runSearch = function (keywords) {
      keywords = (keywords || "").trim();
      if (!keywords) return;
      if (state.jobSearchMode === "local") {
        state.jobFilter = keywords;
        state.searchResults = [];
        state.searchMode = "local";
        return loadJobs().then(renderJobs);
      }
      if (!state.onlineSearchAvailable) {
        toast("线上模式尚未配置，请先到设置中开启 AI 服务", "error");
        return;
      }
      btn.disabled = true;
      var originalText = btn.textContent;
      btn.textContent = "搜索中…";
      api("jobs/search", { method:"POST", body:{keywords:keywords, limit:20} }).then(function (data) {
        var results = (data.local_results || []).concat(data.data || []);
        var seen = {};
        results = results.filter(function (job) {
          var key = job.id || job.url || (job.title + job.company);
          if (seen[key]) return false;
          seen[key] = true;
          return true;
        });
        state.searchResults = results;
        state.searchSkipped = data.skipped || [];
        state.searchMode = data.mode || (data.data && data.data.length ? "llm" : "local");
        state.searchKeyword = keywords;
        rememberSearch(keywords);
        return loadJobs().then(function () {
          renderJobs();
          if (!results.length) {
            toast("没有找到匹配岗位，试试换关键词", "info");
          } else {
            toast("搜索到 " + results.length + " 个岗位，可一键加入岗位库", "success");
          }
          if (data.skipped && data.skipped.length) {
            toast("有 " + data.skipped.length + " 个链接抓取失败，详情见结果区", "warn");
          }
        });
      }).catch(function (e) {
        var status = el("jobParseStatus");
        if (status) status.innerHTML = '<div class="resume-error">' + esc(e.message) + "</div>";
        toast(e.message, "error");
      }).finally(function () { btn.disabled = false; btn.textContent = originalText; });
    };
    var keywordInput = el("onlineSearchKeyword");
    var submitSearch = function () { runSearch(keywordInput ? keywordInput.value : ""); };
    btn.addEventListener("click", submitSearch);
    if (keywordInput) keywordInput.addEventListener("keydown", function (e) { if (e.key === "Enter") submitSearch(); });
    document.querySelectorAll("[data-search-history]").forEach(function (historyBtn) {
      historyBtn.addEventListener("click", function () { runSearch(historyBtn.getAttribute("data-search-history")); });
    });
  }

  function rememberSearch(keyword) {
    state.searchHistory = [keyword].concat(state.searchHistory.filter(function (item) { return item !== keyword; })).slice(0, 8);
    try { localStorage.setItem("careerpilot_search_history", JSON.stringify(state.searchHistory)); } catch (e) {}
  }

  function bindCompanySearch() {
    var btn = el("btnCompanySearch");
    if (!btn) return;
    var run = function () {
      var nameInput = el("companySearchName"), cityInput = el("companySearchCity"), status = el("companySearchStatus");
      var company = nameInput.value.trim();
      if (!company) { status.innerHTML = '<div class="resume-error">请先输入公司名</div>'; return; }
      state.companySearch = company; state.companySearchCity = cityInput.value.trim();
      status.innerHTML = '<div class="resume-loading">正在联网抓取 ' + esc(company) + ' 的招聘岗位（约 10-30 秒）…</div>';
      btn.disabled = true;
      api("jobs/search/company", { method: "POST", body: { company: company, city: state.companySearchCity, limit: 8 } }).then(function (data) {
        var html = "";
        if (data.hint) html += '<div class="merge-source">' + esc(data.hint) + "</div>";
        var skipped = data.skipped || [];
        if (skipped.length) {
          html += '<details class="merge-skipped mt-8"><summary>部分链接抓取失败（' + skipped.length + "）</summary>" +
            skipped.map(function (s) { return '<div class="text-sm muted">· ' + esc(s.url || "(无链接)") + "：" + esc(s.reason || "未知原因") + "</div>"; }).join("") + "</details>";
        }
        status.innerHTML = html;
        return loadJobs().then(function () {
          renderJobs();
          toast("已抓取 " + (data.data || []).length + " 个真实岗位", "success");
        });
      }).catch(function (e) {
        status.innerHTML = '<div class="resume-error">' + esc(e.message) + "</div>";
      }).finally(function () { btn.disabled = false; });
    };
    btn.addEventListener("click", run);
    var nameInput = el("companySearchName");
    if (nameInput) nameInput.addEventListener("keydown", function (e) { if (e.key === "Enter") run(); });
  }

  function bindSearchResultsActions() {
    var addAll = el("addSearchAll");
    if (addAll) addAll.addEventListener("click", addSearchResultsToLibrary);
    var clearBtn = el("clearSearchResults");
    if (clearBtn) clearBtn.addEventListener("click", clearSearchResults);
    document.querySelectorAll("[data-add-search]").forEach(function (button) {
      button.addEventListener("click", function () { addSearchResult(Number(button.getAttribute("data-add-search"))); });
    });
    document.querySelectorAll("[data-undo-search]").forEach(function (button) {
      button.addEventListener("click", function () { undoSearchResult(Number(button.getAttribute("data-undo-search"))); });
    });
  }

  function clearSearchResults() {
    state.searchResults = [];
    state.searchSkipped = [];
    state.searchMode = null;
    state.searchKeyword = "";
    renderJobs();
  }

  async function addSearchResultsToLibrary() {
    var pending = (state.searchResults || []).filter(function (job) { return job && job.source === "local" && !job.saved_job_id; });
    if (!pending.length) { toast("结果都已加入岗位库", "info"); return; }
    var btn = el("addSearchAll");
    if (btn) { btn.disabled = true; btn.textContent = "正在加入…"; }
    var added = 0, failed = 0;
    for (var i = 0; i < pending.length; i++) {
      var job = pending[i];
      try {
        var saved = await saveSearchJob(job);
        job.saved_job_id = saved.id;
        job.addedThisSearch = true;
        added++;
      } catch (e) {
        failed++;
        console.warn("加入岗位失败", job.title, e);
      }
    }
    if (btn) { btn.disabled = false; btn.textContent = "全部加入岗位库"; }
    await loadJobs();
    renderJobs();
    toast(failed ? "已加入 " + added + " 个，" + failed + " 个失败" : "已全部加入岗位库（" + added + " 个）", failed ? "warn" : "success");
  }

  function saveSearchJob(job) {
    return api("jobs", { method: "POST", body: {
      title: job.title || "未命名岗位", company: job.company || "未知公司", city: job.city || "",
      posting_type: job.posting_type || "未知", work_type: job.work_type || "全职", salary: job.salary || "",
      deadline: job.deadline || "", tags: Array.isArray(job.tags) ? job.tags : [], url: job.url || "",
      description: job.description || "", requirements: Array.isArray(job.requirements) ? job.requirements : [], source: "local"
    }});
  }

  async function addSearchResult(index) {
    var job = state.searchResults[index];
    if (!job || job.saved_job_id) return;
    try {
      var saved = await saveSearchJob(job);
      job.saved_job_id = saved.id;
      job.addedThisSearch = true;
      await loadJobs();
      renderJobs();
      toast("已加入岗位库", "success");
    } catch (e) { toast("加入失败：" + e.message, "error"); }
  }

  async function undoSearchResult(index) {
    var job = state.searchResults[index];
    if (!job || !job.addedThisSearch || !job.saved_job_id) return;
    try {
      await api("jobs/" + encodeURIComponent(job.saved_job_id), { method: "DELETE" });
      job.saved_job_id = null;
      job.addedThisSearch = false;
      await loadJobs();
      renderJobs();
      toast("已撤销加入", "success");
    } catch (e) { toast("撤销失败：" + e.message, "error"); }
  }

  function bindJobParse() {
    var input = el("jobUrlInput");
    var btn = el("jobUrlParse");
    if (!input || !btn) return;
    var run = function () {
      var url = input.value.trim();
      var status = el("jobParseStatus");
      if (!/^https?:\/\//i.test(url)) {
        status.innerHTML = '<div class="resume-error">请输入以 http:// 或 https:// 开头的岗位链接</div>';
        return;
      }
      status.innerHTML = '<div class="resume-loading">正在抓取页面并提取岗位要求…</div>';
      btn.disabled = true;
      api("jobs/parse", { method: "POST", body: { url: url } }).then(function (data) {
        status.innerHTML = "";
        state.selectedJobId = data.data.id;
        return loadJobs().then(function () {
          renderJobs();
          toast("岗位解析成功，已加入岗位库", "success");
        });
      }).catch(function (e) {
        status.innerHTML = '<div class="resume-error">' + esc(e.message) + "</div>";
      }).finally(function () { btn.disabled = false; });
    };
    btn.addEventListener("click", run);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") run(); });
  }

  function jobDetail(job) {
    var ev = job.evaluation || {};
    var score = ev.overall || 0;
    var needs = !!ev.needs_profile;
    var gates = ev.gates || { items: [] };
    var prefilter = gates.prefilter;
    var app = state.applications.find(function (a) { return a.job_id === job.id; });
    var gateTags = gates.items.map(function (g) {
      var cls = g.status === "pass" ? "tag-accent" : g.status === "warn" ? "tag-warn" : "tag-danger";
      return '<span class="tag ' + cls + '" title="' + esc(g.note) + '">' + esc(g.name) + " · " + (g.status === "pass" ? "通过" : g.status === "warn" ? "待确认" : "拦截") + "</span>";
    }).join("");
    var appBtn = app
      ? '<button class="btn" data-action="openPipeline">查看申请记录</button>'
      : '<button class="btn btn-primary" data-action="addApplication">开始申请</button>';

    return (
      '<div class="panel"><div class="detail-hero">' +
      '<div class="detail-title-row"><h2>' + esc(job.title) + "</h2>" + verdictTag(ev.verdict || "未评估") + "</div>" +
      '<div class="detail-company">' + esc(job.company) + " · " + esc(job.city) + "</div>" +
      '<div class="detail-meta">' +
      '<span class="tag tag-info">' + esc(job.posting_type) + "</span>" +
      '<span class="tag">' + esc(job.work_type) + "</span>" +
      '<span class="tag">' + esc(job.salary || "薪资未标注") + "</span>" +
      (job.deadline ? '<span class="tag ' + deadlineClass(job.deadline) + '">截止 ' + esc(job.deadline) + "</span>" : '<span class="tag">未标注截止日期</span>') +
      '<span class="tag ' + jobTrust(job).cls + '">' + esc(jobTrust(job).label) + "</span>" +
      "</div>" +
      '<div class="detail-score-row"><div class="detail-score">' + (needs ? "—" : score) + "</div>" +
      '<div><div class="detail-verdict">' + esc(needs ? "完善档案后查看匹配度" : (ev.verdict || "待评估")) + "</div>" +
      '<div class="detail-summary">' + esc(needs ? "先到简历库上传简历，系统会结合你的技能、经历和职业目标进行五维匹配评分。" : (ev.summary || "")) + "</div></div></div>" +
      (gateTags ? '<div class="detail-meta">' + gateTags + "</div>" : "") +
      "</div>" +
      '<div class="detail-sections">' +
      '<div class="detail-section"><h3>下一步行动</h3><div class="text-mid">' + esc(nextAction(job, ev, app)) + '</div></div>' +
      (prefilter ? '<div class="detail-section"><h3>快速预筛（人工确认前）</h3><div class="text-mid"><span class="tag ' + (prefilter.status === "recommend" ? "tag-accent" : prefilter.status === "reject" ? "tag-danger" : "tag-warn") + '">' + esc(prefilter.label) + '</span> ' + esc((prefilter.reasons || []).join("；")) + '</div></div>' : '') +
      '<div class="detail-section"><h3>五维评估</h3>' + barChart(ev) + "</div>" +
      (needs
        ? '<div class="detail-section"><h3>个性化匹配</h3><div class="muted text-mid">完善档案后，这里会显示你的技能、经历、文化与职业方向匹配分析。</div></div>'
        : '<div class="detail-section"><h3>为什么匹配 / 为什么保留</h3>' +
          '<ul class="bullet-list good">' + (ev.strengths || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ul>" +
          '<h3 class="mt-14">需要补足</h3>' +
          '<ul class="bullet-list bad">' + (ev.gaps || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ul>" +
          "</div>") +
      '<div class="detail-section full"><h3>岗位描述</h3><div class="text-mid">' + esc(job.description || "无描述") + "</div></div>" +
      '<div class="detail-section full"><h3>任职要求</h3><ul class="bullet-list plain">' +
      (job.requirements || []).map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") +
      "</ul></div>" +
      "</div>" +
      '<div class="detail-actions">' +
      appBtn +
      '<button class="btn" data-action="resume">生成简历</button>' +
      '<button class="btn" data-action="cover">生成求职信</button>' +
      '<button class="btn" data-action="interview">面试准备</button>' +
      (job.url ? '<a class="btn" target="_blank" rel="noopener" href="' + esc(job.url) + '">查看原帖</a>' : "") +
      '<button class="btn btn-danger" data-action="deleteJob">删除</button>' +
      "</div></div>"
    );
  }

  function deadlineClass(deadline) {
    var days = Math.ceil((new Date(deadline + "T00:00:00") - new Date()) / 86400000);
    return days < 0 ? "tag-danger" : days <= 3 ? "tag-danger" : "tag-warn";
  }
  function nextAction(job, ev, app) {
    if (job.is_demo) return "这是示例岗位。熟悉流程后，请粘贴真实岗位链接或手动录入。";
    if (job.source === "llm_suggested") return "先打开原帖核实岗位、截止日期和要求，再决定是否收藏。";
    if (!app) return "核对硬性门槛和岗位链接；确认适合后加入看板并记录投递计划。";
    if (app.stage === "已收藏") return "准备定制简历并完成投递，随后更新为“已投递”。";
    return app.follow_up_at ? "在 " + app.follow_up_at + " 前跟进，并补充最新沟通记录。" : "补充下一次跟进时间，避免遗漏进展。";
  }

  function bindJobItems() {
    document.querySelectorAll(".job-item[data-job]:not([data-search-result])").forEach(function (item) {
      item.addEventListener("click", function () {
        state.selectedJobId = item.getAttribute("data-job");
        if (state.view === "jobs") renderJobs();
        else renderDashboard();
      });
    });
    document.querySelectorAll("[data-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var action = btn.getAttribute("data-action");
        var job = state.jobs.find(function (j) { return j.id === state.selectedJobId; });
        if (!job) return;
        if (action === "addApplication") addApplication(job);
        else if (action === "resume") generateDoc(job, "resume");
        else if (action === "cover") generateDoc(job, "cover_letter");
        else if (action === "interview") prepareInterview(job);
        else if (action === "openPipeline") { state.view = "pipeline"; location.hash = "#/pipeline"; }
        else if (action === "deleteJob") deleteJob(job);
      });
    });
  }

  async function addApplication(job) {
    try {
      await api("applications", { method: "POST", body: { job_id: job.id, stage: "已收藏" } });
      await loadApplications();
      toast("已加入申请看板", "success");
      renderJobs();
    } catch (e) { toast(e.message, "error"); }
  }

  async function generateDoc(job, kind) {
    var label = kind === "resume" ? "简历" : "求职信";
    toast("正在生成" + label + "…");
    try {
      var doc = await api("documents", { method: "POST", body: { job_id: job.id, kind: kind } });
      showDocModal(doc.content, (kind === "resume" ? "定制简历" : "定制求职信") + " · " + job.company, doc.id);
    } catch (e) { toast("生成失败：" + e.message, "error"); }
  }

  function showDocModal(content, title, docId) {
    var overlay = document.createElement("div");
    overlay.className = "modal-overlay open";
    overlay.innerHTML =
      '<div class="modal modal-wide">' +
      '<div class="modal-head"><strong>' + esc(title) + "</strong>" +
      '<div class="flex"><a class="btn btn-sm" href="/api/documents/download/' + docId + '">下载 Markdown</a>' +
      '<a class="btn btn-sm btn-primary" href="/api/documents/pdf/' + docId + '">下载 PDF</a>' +
      '<button class="btn btn-sm" id="printDocument">打印 / 另存为 PDF</button>' +
      '<button class="icon-btn modal-close" aria-label="关闭"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12"></path><path d="M18 6L6 18"></path></svg></button></div></div>' +
      '<div class="modal-body" style="max-height:72vh;overflow-y:auto"><div class="doc-preview">' + renderMarkdown(content) + "</div></div>" +
      "</div>";
    overlay.querySelector(".modal-close").addEventListener("click", function () { overlay.remove(); });
    overlay.querySelector("#printDocument").addEventListener("click", function () { printDocument(content, title); });
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }

  function markdownToPrintHtml(content) {
    var lines = String(content || "").split(/\r?\n/), out = [], inList = false;
    function closeList() { if (inList) { out.push("</ul>"); inList = false; } }
    lines.forEach(function (line) {
      var heading = line.match(/^(#{1,3})\s+(.+)$/);
      var bullet = line.match(/^\s*(?:[-*·])\s*(.+)$/);
      if (heading) {
        closeList();
        out.push("<h" + heading[1].length + ">" + esc(heading[2]).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</h" + heading[1].length + ">");
      } else if (bullet) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push("<li>" + esc(bullet[1]).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</li>");
      } else {
        closeList();
        if (line.trim()) out.push("<p>" + esc(line).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</p>");
      }
    });
    closeList();
    return out.join("");
  }

  function printDocument(content, title) {
    if (!content) { toast("暂无可打印内容", "error"); return; }
    var frame = document.createElement("iframe");
    frame.id = "printFrame";
    frame.setAttribute("title", "打印文档");
    frame.style.cssText = "position:fixed;width:1px;height:1px;right:0;bottom:0;border:0;opacity:0";
    document.body.appendChild(frame);
    var doc = frame.contentDocument;
    doc.open();
    doc.write('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>' + esc(title) + '</title><style>@page{size:A4;margin:0}body{box-sizing:border-box;width:210mm;min-height:297mm;margin:0 auto;padding:18mm;font-family:"PingFang SC","Microsoft YaHei",sans-serif;color:#202124;font-size:11pt;line-height:1.7}h1{font-size:22pt;text-align:center;margin:0 0 10mm}h2{font-size:15pt;border-bottom:1px solid #bbb;padding-bottom:2mm;margin:7mm 0 3mm}h3{font-size:12pt;margin:5mm 0 2mm}p{margin:1.5mm 0}ul{margin:1.5mm 0;padding-left:6mm}li{margin:1mm 0}strong{font-weight:700}@media print{body{margin:0}}</style></head><body>' + markdownToPrintHtml(content) + "</body></html>");
    doc.close();
    var cleanup = function () { if (frame.parentNode) frame.remove(); };
    frame.contentWindow.onafterprint = cleanup;
    setTimeout(function () { frame.contentWindow.focus(); frame.contentWindow.print(); }, 100);
    setTimeout(cleanup, 60000);
  }

  async function deleteJob(job) {
    if (!confirm("确定删除「" + job.title + "」？")) return;
    try {
      await api("jobs/" + encodeURIComponent(job.id), { method: "DELETE" });
      await loadJobs();
      state.selectedJobId = null;
      toast("岗位已删除", "success");
      renderJobs();
    } catch (e) { toast(e.message, "error"); }
  }

  function renderPipeline() {
    var apps = state.applications;
    var columns = ["已收藏", "已投递", "面试中", "Offer", "已归档"];
    var reminders = applicationReminders(apps);
    var board =
      '<div class="board">' +
      columns.map(function (col) {
        var items = apps.filter(function (a) { return a.stage === col; });
        var si = stageInfo(col);
        return (
          '<div class="board-col"><div class="board-col-head"><span class="stage ' + si.cls + '">' + col + "</span>" +
          '<span class="count">' + items.length + "</span></div>" +
          '<div class="board-cards">' +
          (items.length ? items.map(function (a) {
            return (
              '<div class="board-card"><h4>' + esc(a.title) + "</h4><p>" + esc(a.company) + " · " + esc(a.city) + "</p>" +
              (a.follow_up_at ? '<p class="card-reminder">跟进：' + esc(a.follow_up_at) + '</p>' : '') +
              '<div class="card-actions">' +
              (si.next ? '<button class="btn btn-sm btn-primary" data-move="' + a.id + '" data-to="' + esc(si.next) + '">推进到' + esc(si.next) + "</button>" : "") +
              (col !== "已归档" ? '<button class="btn btn-sm" data-move="' + a.id + '" data-to="已归档">归档</button>' : '<button class="btn btn-sm" data-move="' + a.id + '" data-to="已收藏">恢复</button>') +
              '<button class="btn btn-sm" data-edit-app="' + a.id + '">编辑跟进</button>' +
              "</div></div>"
            );
          }).join("") : '<div class="muted text-sm" style="padding:8px">暂无</div>') +
          "</div></div>"
        );
      }).join("") +
      "</div>";

    var submitted = apps.filter(function (a) { return ["已投递", "面试中", "Offer"].indexOf(a.stage) >= 0; }).length;
    var interviewing = apps.filter(function (a) { return a.stage === "面试中"; }).length;
    var offers = apps.filter(function (a) { return a.stage === "Offer"; }).length;

    el("content").innerHTML =
      '<div class="content-inner">' +
      '<div class="page-head"><div><h1>申请进度</h1><p>跟踪每一个岗位从收藏到 Offer 的完整旅程。</p></div></div>' +
      '<div class="stat-grid">' +
      statCard("全部记录", apps.length, "", "看板内岗位总数") +
      statCard("已投递", submitted, "stat-accent", "已进入投递阶段") +
      statCard("面试中", interviewing, "stat-info", "距离 Offer 一步之遥") +
      statCard("Offer", offers, offers ? "stat-warn" : "", "最终结果") +
      "</div>" +
      (reminders.length ? '<div class="panel mt-14"><div class="panel-head"><strong>需要处理</strong><span class="sub">截止、跟进和久未更新提醒</span></div><div class="list">' + reminders.map(function (r) { return '<div class="list-row"><div class="row-main"><div class="row-title">' + esc(r.title) + '</div><div class="row-sub">' + esc(r.company) + '</div></div><span class="tag ' + r.cls + '">' + esc(r.text) + '</span></div>'; }).join("") + '</div></div>' : '') +
      '<div class="mt-14">' + board + "</div>" +
      "</div>";

    document.querySelectorAll("[data-move]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        moveApplication(btn.getAttribute("data-move"), btn.getAttribute("data-to"));
      });
    });
    document.querySelectorAll("[data-edit-app]").forEach(function (btn) { btn.addEventListener("click", function () { editApplication(btn.getAttribute("data-edit-app")); }); });
  }

  function applicationReminders(apps) {
    var today = new Date(); today.setHours(0, 0, 0, 0);
    return apps.filter(function (a) { return a.stage !== "已归档"; }).map(function (a) {
      var follow = a.follow_up_at && new Date(a.follow_up_at + "T00:00:00");
      var deadline = a.deadline && new Date(a.deadline + "T00:00:00");
      var updated = a.updated_at && new Date(a.updated_at.replace(" ", "T"));
      if (deadline && deadline >= today && deadline - today <= 3 * 86400000) return { title:a.title, company:a.company, text:"即将截止：" + a.deadline, cls:"tag-danger" };
      if (follow && follow <= today) return { title:a.title, company:a.company, text:"需要跟进：" + a.follow_up_at, cls:"tag-warn" };
      if (updated && today - updated > 7 * 86400000) return { title:a.title, company:a.company, text:"超过 7 天未更新", cls:"tag-warn" };
      return null;
    }).filter(Boolean);
  }

  async function editApplication(id) {
    var app = state.applications.find(function (item) { return String(item.id) === String(id); });
    if (!app) return;
    var overlay = document.createElement("div");
    overlay.className = "modal-overlay open";
    overlay.innerHTML = '<div class="modal modal-wide"><div class="modal-head"><strong>编辑跟进 · ' + esc(app.title) + '</strong><button class="icon-btn modal-close" aria-label="关闭">×</button></div>' +
      '<div class="modal-body"><label class="field"><span>备注 / 沟通记录</span><textarea id="editAppNotes" rows="5" placeholder="记录沟通内容、面试反馈或下一步计划">' + esc(app.notes || "") + '</textarea></label>' +
      '<div class="form-grid"><label class="field"><span>联系人</span><input id="editAppContact" type="text" placeholder="姓名、邮箱或电话" value="' + esc(app.contact || "") + '"></label>' +
      '<label class="field"><span>下次跟进日期</span><input id="editAppFollow" type="date" value="' + esc(app.follow_up_at || "") + '"></label></div>' +
      '<label class="field"><span>附件名称</span><input id="editAppAttachment" type="text" placeholder="例如：定制简历-产品岗.pdf" value="' + esc(app.attachment_name || "") + '"></label>' +
      '<div class="modal-actions"><button class="btn" id="cancelEditApp">取消</button><button class="btn btn-primary" id="saveEditApp">保存跟进</button></div></div></div>';
    document.body.appendChild(overlay);
    var close = function () { overlay.remove(); };
    overlay.querySelector(".modal-close").addEventListener("click", close);
    overlay.querySelector("#cancelEditApp").addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    overlay.querySelector("#saveEditApp").addEventListener("click", async function () {
      var save = overlay.querySelector("#saveEditApp");
      save.disabled = true; save.textContent = "保存中…";
      try {
        await api("applications/" + id, { method:"PATCH", body:{
          notes: overlay.querySelector("#editAppNotes").value.trim(),
          contact: overlay.querySelector("#editAppContact").value.trim(),
          follow_up_at: overlay.querySelector("#editAppFollow").value,
          attachment_name: overlay.querySelector("#editAppAttachment").value.trim()
        } });
        close(); await loadApplications(); toast("跟进记录已保存", "success"); renderPipeline();
      } catch (e) { save.disabled = false; save.textContent = "保存跟进"; toast("保存失败：" + e.message, "error"); }
    });
  }

  async function moveApplication(id, stage) {
    try {
      await api("applications/" + id, { method: "PATCH", body: { stage: stage } });
      await loadApplications();
      toast("已更新为「" + stage + "」", "success");
      renderPipeline();
    } catch (e) { toast(e.message, "error"); }
  }

  function renderInterview() {
    var jobs = state.jobs.slice().sort(function (a, b) { return (b.evaluation || {}).overall - (a.evaluation || {}).overall; });
    var options = jobs.map(function (j) {
      return '<option value="' + esc(j.id) + '"' + (state.interviewJobId === j.id ? " selected" : "") + ">" + esc(j.title + " · " + j.company) + "</option>";
    }).join("");
    var preview = state.interviewContent
      ? '<div class="doc-preview">' + renderMarkdown(state.interviewContent) + "</div>"
      : '<div class="panel"><div class="panel-body">' + emptyBlock("选择岗位生成面试准备包", "包含高频问题、岗位追问、STAR 素材与反问清单") + "</div></div>";

    el("content").innerHTML =
      '<div class="content-inner">' +
      '<div class="page-head"><div><h1>面试准备</h1><p>针对目标岗位生成可背诵、可演练的完整准备包。</p></div></div>' +
      '<div class="panel mb-14"><div class="panel-body"><div class="flex" style="flex-wrap:wrap;gap:10px">' +
      '<label class="field" style="flex:1;min-width:260px"><span>目标岗位</span><select id="interviewJob" style="min-height:36px;border:1px solid var(--border-strong);border-radius:6px;padding:0 10px">' + options + "</select></label>" +
      '<button class="btn btn-primary" id="interviewGenerate" style="align-self:flex-end">生成准备包</button>' +
      "</div></div></div>" +
      preview +
      "</div>";

    var sel = el("interviewJob");
    if (sel) sel.addEventListener("change", function () { state.interviewJobId = sel.value; });
    el("interviewGenerate").addEventListener("click", function () {
      var job = state.jobs.find(function (j) { return j.id === sel.value; });
      if (job) prepareInterview(job);
    });
  }

  async function prepareInterview(job) {
    toast("正在生成面试准备包…");
    try {
      var data = await api("interview", { method: "POST", body: { job_id: job.id } });
      state.interviewJobId = job.id;
      state.interviewContent = data.content;
      if (state.view === "interview") renderInterview();
      else { state.view = "interview"; location.hash = "#/interview"; }
    } catch (e) { toast("生成失败：" + e.message, "error"); }
  }

  function profileQuality(p) {
    var issues = [];
    if (!p.phone) issues.push('缺少手机号，无法直接用于多数校招申请。');
    if (!p.email) issues.push('缺少邮箱，建议使用正式求职邮箱。');
    if (!p.school && !(p.education && p.education.length)) issues.push('学校未填写，校招筛选会直接受影响。');
    if (!p.major && !(p.education && p.education[0] && p.education[0].major)) issues.push('专业未填写，无法判断岗位专业匹配度。');
    if (!p.graduation_date && !(p.education && p.education[0] && p.education[0].graduation_date)) issues.push('毕业时间未填写，无法确认 2027 届资格。');
    if (!p.github && !p.portfolio) issues.push('缺少作品集链接，AI 产品/游戏方向建议至少放一个可查看作品。');
    if (!p.notes) issues.push('个人简介为空，招聘方看不到你的职业定位和动机。');
    if (!p.career_goals || !p.career_goals.length) issues.push('职业目标为空，系统无法稳定生成定制化简历。');
    return issues;
  }


  function renderProfile() {
    var p = state.profile || {};
    var skills = p.skills || {};
    var education = (p.education && p.education[0]) || {};
    var school = p.school || education.school || education.school_name || "";
    var major = p.major || education.major || education.field || "";
    var degree = p.highest_degree || education.degree || "本科";
    var graduation = p.graduation_date || education.graduation_date || education.end_date || "";
    var projects = (p.projects || []).filter(function (item) { return item && [item.name, item.project_name, item.title, item.description].some(function (value) { return typeof value === 'string' && value.trim(); }); });
    var experiences = (p.experiences || []).filter(function (item) { return item && item.company && item.company !== "独立开发"; });
    var inferredProjects = (p.experiences || []).filter(function (item) { return item && (item.company === "独立开发" || item.type === "project"); }).map(function (item) { return { name: item.role || item.title || item.project_name, role: item.role || "独立开发者", description: item.description || item.responsibilities || "" }; });
    projects = projects.concat(inferredProjects);
    var hasSkills = [skills.strong, skills.moderate].some(function (items) { return Array.isArray(items) ? items.some(Boolean) : Boolean(items); });
    var hasGoals = Array.isArray(p.career_goals) ? p.career_goals.some(Boolean) : Boolean(p.career_goals);
    var completion = [p.name, p.city, school, major, graduation, p.phone || p.email, hasSkills, hasGoals].filter(Boolean).length;
    var completionPct = Math.round(completion / 8 * 100);
    el("content").innerHTML =
      '<div class="content-inner">' +
      '<div class="page-head"><div><h1>简历库</h1><p>把你之前的所有简历都放进来，岗位评分、简历生成和求职信会自动使用这些资料。资料只保存在你的账号下。</p></div>' +
      '<div class="page-actions"><div class="resume-profile-badge"><span>档案完成度</span><b>' + completionPct + '%</b></div></div></div>' +
      '<div class="panel mb-14"><div class="panel-head"><strong>上传简历</strong><span class="sub">支持 PDF / DOCX / TXT / MD，每份 ≤10MB，可一次多选</span></div>' +
      '<div class="panel-body">' +
      '<div class="upload-zone" id="uploadZone"><input type="file" id="resumeFile" accept=".pdf,.docx,.txt,.md" multiple hidden>' +
      '<strong>点击选择或拖拽简历文件</strong><span class="muted">可一次上传多份历史简历；识别学校、专业、毕业时间、实习、项目、技能，逐项确认后写入</span></div>' +
      '<div id="resumeImportStatus" class="mt-8" role="status" aria-live="polite" aria-atomic="true"></div>' +
      "</div></div>" +
      '<div class="panel mb-14"><div class="panel-head"><strong>我的简历</strong><span class="sub" id="resumeListCount"></span></div>' +
      '<div class="panel-body"><div id="resumeListPanel"><div class="loading"><div class="spinner"></div></div></div></div></div>' +
      '<div id="resumeSummaryPanel"></div>' +
      '<div id="resumeMergePanel"></div>' +
      '<div class="panel mb-14 profile-section" id="profile-section-intent"><div class="panel-head"><strong>补充求职意向</strong><span class="sub">可选，填好后岗位预筛和评分更准</span></div>' +
      '<div class="panel-body profile-editor"><div class="form-grid">' +
      field("目标岗位", "profileTargetRole", p.target_role || "AI 产品运营 / AI 游戏策划") +
      field("目标方向", "profileTargetSector", (p.target_sectors || []).join("、")) +
      field("期望城市", "profileTargetCity", p.target_city || p.city || "") +
      field("可入职时间", "profileAvailableDate", p.available_date || "") +
      field("预筛关键词", "profileFilterKeywords", (p.filter_keywords || []).join("、"), "命中后标记推荐") +
      field("一票否决词", "profileFilterExclude", (p.filter_exclude_keywords || []).join("、"), "如：外包、纯销售、3年以上") +
      field("最低匹配分", "profileMinScore", p.min_match_score || "", "仅作为提醒，不会自动删除岗位") +
      '<label class="field"><span>接受实习</span><select id="profileAcceptInternship"><option value="1"' + (p.accept_internship !== false ? " selected" : "") + '>接受</option><option value="0"' + (p.accept_internship === false ? " selected" : "") + '>暂不接受</option></select></label>' +
      '</div><div class="form-actions"><button class="btn btn-primary" id="saveIntent">保存求职意向</button></div></div></div>' +
      '<div class="panel profile-section" id="advancedProfilePanel"><div class="panel-head"><strong>完整档案（高级）</strong><span class="sub">简历识别后会自动填入，需要时再展开手工修改</span>' +
      '<button class="btn btn-sm" id="toggleAdvanced">' + (state.advancedExpanded ? "收起编辑" : "展开编辑") + '</button></div>' +
      '<div class="panel-body profile-editor" id="advancedProfileBody" style="display:' + (state.advancedExpanded ? "" : "none") + '">' +
      '<h4 class="advanced-title">个人信息<span class="sub">用于生成简历和招聘表单建议</span></h4><div class="form-grid">' +
      field("姓名", "profileName", p.name) +
      field("城市", "profileCity", p.city) +
      field("手机", "profilePhone", p.phone || "") +
      field("邮箱", "profileEmail", p.email || "") +
      field("求职状态", "profileStatus", p.status) +
      field("GitHub/作品集", "profileGithub", p.github || "") +
      field("学校", "profileSchool", school) +
      field("学历", "profileDegree", degree) +
      field("专业", "profileMajor", major) +
      field("毕业时间", "profileGraduation", graduation) +
      field("英语等级", "profileEnglish", p.english_level || "") +
      "</div>" +
      '<label class="field"><span>工作地点偏好</span><textarea id="profileLocation" placeholder="例如：北京、上海、杭州；接受全国异地">' + esc(p.location_preference || "") + "</textarea></label>" +
      '<div class="resume-privacy">身份证号、紧急联系人电话等敏感资料不会被自动填入招聘表单，需你每次手动确认。</div>' +
      '<h4 class="advanced-title">教育背景<span class="sub">校招岗位重点关注信息</span></h4><div class="experience-card"><div><strong>' + esc(school || '尚未填写学校') + '</strong><span>' + esc(major || '尚未填写专业') + ' · ' + esc(degree) + '</span></div><em>' + esc(graduation || '毕业时间待填写') + '</em></div>' +
      '<h4 class="advanced-title">工作经历<button class="btn btn-sm" id="addExperience">＋ 添加</button></h4><div id="experienceList">' + renderExperienceCards(experiences) + '</div>' +
      '<h4 class="advanced-title">项目经验<button class="btn btn-sm" id="addProject">＋ 添加</button></h4><div id="projectList">' + renderProjectCards(projects) + '</div>' +
      '<h4 class="advanced-title">技能与语言<span class="sub">支持换行、逗号、顿号或分号分隔，一行可填写多个项目</span></h4>' +
      '<label class="field"><span>核心优势</span><textarea id="profileStrengths" placeholder="例如：Prompt 工程，Python 数据分析；用户洞察与结构化表达">' + esc((skills.strong || []).join("\n")) + "</textarea></label>" +
      '<label class="field"><span>辅助技能</span><textarea id="profileModerate" placeholder="例如：SQL、Figma、英语沟通">' + esc((skills.moderate || []).join("\n")) + "</textarea></label>" +
      '<label class="field"><span>职业目标（每行一条）</span><textarea id="profileGoals">' + esc((p.career_goals || []).join("\n")) + '</textarea></label>' +
      '<h4 class="advanced-title">自我描述<span class="sub">用事实说明你的优势、动机和发展方向</span></h4>' +
      '<label class="field"><span>个人简介</span><textarea id="profileMore" style="min-height:160px" placeholder="简要介绍你的经历、优势和职业目标">' + esc(p.notes || "") + '</textarea></label>' +
      '<div class="form-actions"><button class="btn btn-primary" id="saveProfile">保存完整档案</button></div>' +
      '</div></div>' +
      "</div>";
    el("toggleAdvanced").addEventListener("click", function () {
      state.advancedExpanded = !state.advancedExpanded;
      var body = el("advancedProfileBody");
      body.style.display = state.advancedExpanded ? "" : "none";
      this.textContent = state.advancedExpanded ? "收起编辑" : "展开编辑";
    });
    el("saveIntent").addEventListener("click", saveIntent);
    el("saveProfile").addEventListener("click", saveProfile);
    bindUploadZone();
    loadResumeList();
    loadResumeDraft();
    bindExperienceActions();
    bindProfileNavigation();
    var formPanel = document.createElement("div");
    formPanel.className = "panel profile-section";
    formPanel.innerHTML = '<div class="panel-head"><strong>招聘网站助手</strong><span class="sub">粘贴招聘表单 HTML，识别字段并生成填写建议</span></div><div class="panel-body"><label class="field"><span>表单 HTML</span><textarea id="formHtml" placeholder="粘贴招聘官网表单 HTML"></textarea></label><button class="btn btn-primary mt-8" id="extractForm">识别字段</button><div id="formResult" class="mt-8"></div></div>';
    el("content").querySelector(".content-inner").appendChild(formPanel);
    el("extractForm").addEventListener("click", function () {
      var result = el("formResult");
      var html = el("formHtml").value.trim();
      if (!html) { result.innerHTML = '<div class="resume-error">请先粘贴招聘官网表单的 HTML 内容</div>'; return; }
      result.innerHTML = '<div class="resume-loading">正在识别表单字段并生成填写计划…</div>';
      api("forms/extract", {method:"POST", body:{html:html}}).then(function (form) {
        return api("forms/fill-plan", {method:"POST", body:{form_id:form.data.form_id, fields:form.data.fields}});
      }).then(function (plan) {
        var mappings = plan.data.mappings || [];
        result.innerHTML = mappings.length ? '<div class="merge-title">填写计划（请在官网页面逐项核对后填写）</div>' + mappings.map(function (m) {
          var hint = m.manual_confirmation ? '敏感字段：不会自动填写，请本人手动确认。' : '填写方式：' + (m.strategy === "date_normalize" ? "日期格式已规范化" : m.strategy === "select" ? "选择建议值" : "直接填写建议值");
          return '<div class="merge-row"><div class="merge-body"><strong>' + esc(m.label || "未命名字段") + '</strong><div class="merge-value">建议值：' + esc(m.value == null ? "需手动确认" : m.value || "档案中暂无") + '</div><div class="merge-source">字段标识：' + esc(m.key || "未识别") + ' · ' + esc(hint) + '</div></div></div>';
        }).join("") : '<div class="resume-error">没有识别到可填写字段。请确认粘贴的是完整表单 HTML。</div>';
      }).catch(function (e) { result.innerHTML = '<div class="resume-error">识别失败：' + esc(e.message) + '。请检查 HTML 后重试。</div>'; });
    });
  }

  function renderExperienceCards(items) {
    if (!items.length) return '<div class="empty-experience">还没有工作经历，点击右上角添加一条。</div>';
    return items.map(function (item, i) {
      return '<article class="experience-card editable-card"><div class="experience-fields"><input data-exp="company" data-index="' + i + '" placeholder="公司名称" value="' + esc(item.company || '') + '"><input data-exp="role" data-index="' + i + '" placeholder="职位名称" value="' + esc(item.role || item.title || '') + '"><textarea data-exp="description" data-index="' + i + '" placeholder="工作职责与成果">' + esc(item.description || item.responsibilities || '') + '</textarea></div><em>' + esc(item.start_date || '') + (item.end_date ? ' - ' + esc(item.end_date) : item.current ? ' - 至今' : '') + '</em><button class="icon-btn remove-experience" data-index="' + i + '" title="删除经历">×</button></article>';
    }).join('');
  }

  function renderProjectCards(items) {
    if (!items.length) return '<div class="empty-experience">还没有项目经验，点击右上角添加一条。</div>';
    return items.map(function (item, i) {
      return '<article class="experience-card editable-card"><div class="experience-fields"><input data-project="name" data-index="' + i + '" placeholder="项目名称" value="' + esc(item.name || '') + '"><input data-project="role" data-index="' + i + '" placeholder="项目中职责" value="' + esc(item.role || '') + '"><textarea data-project="description" data-index="' + i + '" placeholder="项目描述与成果">' + esc(item.description || '') + '</textarea></div><em>' + esc(item.start_date || '') + (item.end_date ? ' - ' + esc(item.end_date) : item.current ? ' - 至今' : '') + '</em><button class="icon-btn remove-project" data-index="' + i + '" title="删除项目">×</button></article>';
    }).join('');
  }

  function bindProfileNavigation() {
    document.querySelectorAll('.profile-nav-link').forEach(function (link) {
      link.addEventListener('click', function () {
        document.querySelectorAll('.profile-nav-link').forEach(function (item) { item.classList.remove('active'); });
        link.classList.add('active');
      });
    });
  }

  function bindExperienceActions() {
    var addExperience = el('addExperience');
    var addProject = el('addProject');
    if (addExperience) addExperience.addEventListener('click', function () {
      var items = state.profile.experiences || [];
      items.push({ company: '', role: '', description: '', start_date: '', end_date: '', current: false });
      state.profile.experiences = items;
      state.advancedExpanded = true;
      renderProfile();
      var panel = el('advancedProfilePanel');
      if (panel) panel.scrollIntoView({ behavior: 'smooth' });
    });
    if (addProject) addProject.addEventListener('click', function () {
      var items = state.profile.projects || [];
      items.push({ name: '', role: '', description: '', start_date: '', end_date: '', current: false });
      state.profile.projects = items;
      state.advancedExpanded = true;
      renderProfile();
      var panel = el('advancedProfilePanel');
      if (panel) panel.scrollIntoView({ behavior: 'smooth' });
    });
    document.querySelectorAll('.remove-experience').forEach(function (button) { button.addEventListener('click', function () { state.profile.experiences.splice(Number(button.dataset.index), 1); renderProfile(); }); });
    document.querySelectorAll('.remove-project').forEach(function (button) { button.addEventListener('click', function () { state.profile.projects.splice(Number(button.dataset.index), 1); renderProfile(); }); });
  }

  function bindUploadZone() {
    var zone = el("uploadZone");
    var input = el("resumeFile");
    if (!zone || !input) return;
    zone.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () {
      if (input.files && input.files.length) uploadResume(input.files);
    });
    ["dragover", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (name === "drop" && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          uploadResume(e.dataTransfer.files);
        }
      });
    });
  }

  async function uploadResume(files) {
    files = Array.prototype.slice.call(files || []);
    if (!files.length) return;
    var status = el("resumeImportStatus");
    var allowed = [".pdf", ".docx", ".txt", ".md"];
    var invalid = files.filter(function (file) {
      var name = (file.name || "").toLowerCase();
      return !allowed.some(function (ext) { return name.endsWith(ext); }) || file.size > 10 * 1024 * 1024;
    });
    if (invalid.length) {
      status.innerHTML = '<div class="resume-error">仅支持 PDF / DOCX / TXT / MD 文件，每份不超过 10MB</div>';
      return;
    }
    var form = new FormData();
    files.forEach(function (file) { form.append("files", file); });
    var zone = el("uploadZone"), input = el("resumeFile");
    if (zone) { zone.style.pointerEvents = "none"; zone.setAttribute("aria-busy", "true"); }
    if (input) input.disabled = true;
    status.innerHTML = '<div class="resume-loading">正在上传并识别 ' + files.length + ' 份简历，请勿关闭页面…</div>';
    try {
      var resp = await fetch("/api/resumes/upload", {
        method: "POST",
        credentials: "include",
        body: form
      });
      var data = await resp.json();
      if (!resp.ok) throw new Error(data.error || data.message || "导入失败");
      var d = data.data || {};
      status.innerHTML = "";
      if (d.plan) {
        renderResumeSummary(d.plan);
        renderMergePlan(d.plan);
      }
      if (d.errors && d.errors.length) {
        status.innerHTML = '<div class="resume-error">' + d.errors.map(function (e) {
          return esc(e.filename) + "：" + esc(e.error);
        }).join("<br>") + '</div>';
      }
      loadResumeList();
      var done = (d.items || []).length;
      toast(done + " 份简历已存入简历库，识别结果可逐项确认", "success");
    } catch (e) {
      status.innerHTML = '<div class="resume-error">' + esc(e.message) + "</div>";
    } finally {
      if (zone) { zone.style.pointerEvents = ""; zone.removeAttribute("aria-busy"); }
      if (input) { input.disabled = false; input.value = ""; }
    }
  }

  function formatFileSize(bytes) {
    if (bytes === null || bytes === undefined) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  async function loadResumeList() {
    var panel = el("resumeListPanel");
    var count = el("resumeListCount");
    if (!panel) return;
    try {
      var data = await api("resumes");
      var list = data.data || [];
      if (count) count.textContent = "共 " + list.length + " 份";
      if (!list.length) {
        panel.innerHTML = '<div class="empty-resume-list"><strong>简历库还是空的</strong><span>把你之前的所有简历都传上来，评分、简历生成和求职信就能直接用了</span></div>';
        return;
      }
      panel.innerHTML = '<div class="resume-list">' + list.map(function (item) {
        return '<div class="resume-list-item">' +
          '<div class="resume-list-icon">📄</div>' +
          '<div class="resume-list-info"><strong>' + esc(item.filename) + '</strong><span class="muted">' + esc(formatFileSize(item.size)) + (item.created_at ? ' · ' + esc(item.created_at) : "") + '</span></div>' +
          '<div class="resume-list-actions">' +
          '<a class="btn btn-sm" href="/api/resumes/' + item.id + '/download">下载</a>' +
          '<button class="btn btn-sm btn-danger" data-resume-delete="' + item.id + '">删除</button>' +
          '</div></div>';
      }).join('') + '</div>';
      panel.querySelectorAll("[data-resume-delete]").forEach(function (btn) {
        btn.addEventListener("click", function () { deleteResume(Number(btn.getAttribute("data-resume-delete"))); });
      });
    } catch (e) {
      panel.innerHTML = '<div class="resume-error">简历列表加载失败：' + esc(e.message) + '</div>';
    }
  }

  async function deleteResume(id) {
    if (!window.confirm("确定删除这份简历吗？删除后无法恢复。")) return;
    try {
      await api("resumes/" + id, { method: "DELETE" });
      toast("简历已删除", "success");
      loadResumeList();
    } catch (e) { toast("删除失败：" + e.message, "error"); }
  }

  async function saveIntent() {
    var btn = el("saveIntent");
    if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
    var body = {
      target_role: el("profileTargetRole").value.trim(),
      target_sectors: el("profileTargetSector").value.split(/[、,，\n]/).map(function (s) { return s.trim(); }).filter(Boolean),
      target_city: el("profileTargetCity").value.trim(),
      available_date: el("profileAvailableDate").value.trim()
    };
    try {
      state.profile = await api("profile", { method: "PUT", body: body });
      state.user.profile = state.profile;
      await loadJobs();
      updateProfileBanner();
      toast("求职意向已保存", "success");
    } catch (e) { toast("保存失败：" + e.message, "error"); }
    finally {
      if (btn) { btn.disabled = false; btn.textContent = "保存求职意向"; }
    }
  }


  function renderResumeSummary(plan) {
    var panel = el("resumeSummaryPanel");
    if (!panel) return;
    var summary = plan && plan.summary || {};
    var confidence = plan && plan.confidence || {};
    var fields = [
      ["手机", "phone"], ["邮箱", "email"], ["学校", "school"],
      ["学历", "highest_degree"], ["专业", "major"],
      ["毕业时间", "graduation_date"], ["英语", "english_level"]
    ];
    var hasCore = fields.some(function (item) { return summary[item[1]]; }) || summary.name;
    var detected = fields.filter(function (item) { return summary[item[1]]; }).length + (summary.name ? 1 : 0);
    var lowConfidence = Object.keys(confidence).filter(function (key) { return confidence[key] === "low"; }).length;
    var name = summary.name || "未识别姓名";
    var initial = summary.name ? String(summary.name).trim().slice(0, 1) : "?";
    var chips = fields.map(function (item) {
      var value = summary[item[1]];
      return '<div class="resume-summary-chip' + (value ? '' : ' is-empty') + '"><span>' + esc(item[0]) + '</span><strong>' + esc(value || "未识别") + '</strong></div>';
    }).join("");
    panel.innerHTML = '<section class="resume-summary-card mb-14"><div class="resume-summary-main">' +
      '<div class="resume-summary-avatar">' + esc(initial) + '</div><div class="resume-summary-content">' +
      '<div class="resume-summary-heading"><h2>' + esc(name) + '</h2><span class="tag">' + esc(summary.status || "待完善") + '</span></div>' +
      '<div class="resume-detection-meta">已识别 ' + detected + ' 个核心字段' + (lowConfidence ? '，其中 ' + lowConfidence + ' 项建议人工核对' : '，暂未发现低置信度字段') + '</div>' +
      (hasCore ? '<div class="resume-summary-chips">' + chips + '</div>' : '<div class="resume-summary-empty"><strong>未识别到核心字段</strong><span>试试 PDF 文字版简历，或检查文件是否加密</span></div>') +
      '</div></div><div class="resume-summary-actions"><button class="btn btn-primary" id="fillResumeSummary">一键填入全部</button>' +
      '<button class="btn" id="toggleResumeReview">展开逐项核对</button></div></section>';
    el("fillResumeSummary").disabled = !hasCore;
    el("fillResumeSummary").addEventListener("click", function () { fillResumeSummary(plan); });
    el("toggleResumeReview").addEventListener("click", function () {
      var merge = el("resumeMergePanel");
      merge.classList.toggle("is-expanded");
      this.textContent = merge.classList.contains("is-expanded") ? "收起逐项核对" : "展开逐项核对";
    });
  }

  async function fillResumeSummary(plan) {
    var summary = plan.summary || {};
    var inputs = {
      name: "profileName", phone: "profilePhone", email: "profileEmail",
      school: "profileSchool", highest_degree: "profileDegree", major: "profileMajor",
      graduation_date: "profileGraduation", english_level: "profileEnglish", status: "profileStatus"
    };
    Object.keys(inputs).forEach(function (key) {
      var input = el(inputs[key]);
      if (!input || summary[key] === null || summary[key] === undefined || summary[key] === "") return;
      input.value = summary[key];
      input.classList.add("resume-autofilled");
      setTimeout(function () { input.classList.remove("resume-autofilled"); }, 2000);
    });
    var highPaths = (plan.fills || []).concat(plan.updates || []).filter(function (item) {
      return item.confidence === "high";
    }).map(function (item) { return item.field_path; });
    try {
      var data = await api("profile/resume-import/apply", { method: "POST", body: { accepted_field_paths: highPaths } });
      state.profile = data.data.profile;
      state.user.profile = state.profile;
      document.querySelectorAll(".merge-check").forEach(function (check) {
        check.checked = highPaths.indexOf(check.getAttribute("data-path")) !== -1;
        check.disabled = true;
      });
      updateMergeCount();
      toast("核心字段已填入档案，可展开完整档案查看", "success");
    } catch (e) { toast("自动应用失败：" + e.message, "error"); }
  }

  function renderMergePlan(plan) {
    if (!plan) return;
    var fills = plan.fills || [];
    var updates = plan.updates || [];
    var skipped = plan.skipped || [];
    var unknown = plan.unrecognized || [];
    var html =
      '<div class="panel mb-14"><div class="panel-head"><strong>确认识别结果</strong><span class="sub">只应用你勾选的字段；低置信度默认不选，已有信息不会自动覆盖</span></div><div class="panel-body">';
    html += '<h4 class="merge-title">建议填入（' + fills.length + "）</h4>";
    if (fills.length) {
      html += fills.map(function (item) {
        return mergeCheckRow(item, (item.confidence || "high") === "high");
      }).join("");
    } else {
      html += '<div class="muted text-sm">没有可直接填入的新字段</div>';
    }
    html += '<h4 class="merge-title mt-14">存在冲突（' + updates.length + "）</h4>";
    if (updates.length) {
      html += updates.map(function (item) {
        return mergeCheckRow(item, false);
      }).join("");
    } else {
      html += '<div class="muted text-sm">没有冲突字段</div>';
    }
    html += '<details class="merge-skipped mt-14"><summary>已跳过（' + skipped.length + "）</summary>";
    html += skipped.map(function (item) {
      return '<div class="text-sm muted">' + esc(item.field_path) + "：" + (item.new_value === null || item.new_value === "" ? "未识别到内容" : "置信度低") + "</div>";
    }).join("");
    html += "</details>";
    if (unknown.length) {
      html += '<details class="merge-skipped mt-8"><summary>以下内容未能自动归类（' + unknown.length + "）</summary>";
      html += unknown.map(function (s) { return '<div class="text-sm muted">· ' + esc(s) + "</div>"; }).join("");
      html += "</details>";
    }
    html +=
      '<div class="flex mt-14" style="justify-content:flex-end;gap:8px">' +
      '<button class="btn" id="discardMerge">放弃</button>' +
      '<button class="btn btn-primary" id="applyMerge">确认填入 0 项</button>' +
      "</div></div></div>";
    el("resumeMergePanel").innerHTML = html;
    el("resumeMergePanel").classList.remove("is-expanded");
    el("applyMerge").addEventListener("click", applyMerge);
    el("discardMerge").addEventListener("click", function () {
      if (confirm("确定放弃本次导入？档案不会被修改。")) {
        api("profile/resume-import/apply", { method: "POST", body: { accepted_field_paths: [] } }).then(function () {
          el("resumeMergePanel").innerHTML = "";
          toast("已放弃导入", "success");
        }).catch(function () {
          el("resumeMergePanel").innerHTML = "";
        });
      }
    });
    document.querySelectorAll(".merge-check").forEach(function (cb) {
      cb.addEventListener("change", updateMergeCount);
    });
    updateMergeCount();
  }

  function mergeCheckRow(item, checked) {
    var label = item.field_path;
    var val = item.new_value;
    var valText = typeof val === "object" ? JSON.stringify(val, null, 1) : String(val == null ? "" : val);
    var current = item.current_value;
    var currentText = typeof current === "object" ? JSON.stringify(current, null, 1) : String(current == null ? "" : current);
    var extra = "";
    if (item.current_value !== undefined && item.current_value !== null && String(item.current_value) !== "") {
      extra = '<div class="merge-current">当前值：' + esc(currentText) + "</div>";
    }
    return (
      '<label class="merge-row">' +
      '<input type="checkbox" class="merge-check" data-path="' + esc(item.field_path) + '"' + (checked ? " checked" : "") + ">" +
      '<div class="merge-body"><div class="merge-path">' + esc(label) + " <span class=\"tag\">" + esc(item.confidence || "high") + "</span></div>" +
      '<div class="merge-value">识别结果：' + esc(valText) + "</div>" + extra +
      (item.source_text ? '<div class="merge-source">原文：' + esc(item.source_text) + "</div>" : "") +
      "</div></label>"
    );
  }

  function updateMergeCount() {
    var count = document.querySelectorAll(".merge-check:checked").length;
    var btn = el("applyMerge");
    if (btn) btn.textContent = "确认填入 " + count + " 项";
  }

  async function applyMerge() {
    var paths = Array.from(document.querySelectorAll(".merge-check:checked")).map(function (c) {
      return c.getAttribute("data-path");
    });
    try {
      var data = await api("profile/resume-import/apply", { method: "POST", body: { accepted_field_paths: paths } });
      state.profile = data.data.profile;
      state.user.profile = state.profile;
      await loadJobs();
      updateProfileBanner();
      toast("已填入 " + data.data.applied + " 项，档案已更新", "success");
      renderProfile();
    } catch (e) { toast("应用失败：" + e.message, "error"); }
  }

  async function loadResumeDraft() {
    try {
      var data = await api("profile/resume-import/draft");
      if (data && data.data) {
        renderResumeSummary(data.data);
        renderMergePlan(data.data);
      }
    } catch (e) {}
  }

  function field(label, id, value, note) {
    return '<label class="field"><span>' + esc(label) + (note ? ' <em class="muted" style="font-style:normal">(' + esc(note) + ")</em>" : "") + '</span><input id="' + id + '" type="text" value="' + esc(value || "") + '"></label>';
  }

  function splitProfileItems(value) {
    return String(value || "").split(/[\n,，、;；|]+/).map(function (s) {
      return s.trim();
    }).filter(Boolean);
  }

  async function saveProfile() {
    var saveButton = el("saveProfile");
    if (saveButton && saveButton.disabled) return;
    if (saveButton) { saveButton.disabled = true; saveButton.dataset.originalText = saveButton.textContent; saveButton.textContent = "保存中…"; }
    var skills = state.profile.skills || {};
    var experiences = state.profile.experiences || [];
    document.querySelectorAll('[data-exp]').forEach(function (input) { var i = Number(input.dataset.index); experiences[i] = experiences[i] || {}; experiences[i][input.dataset.exp] = input.value.trim(); });
    var projects = state.profile.projects || [];
    document.querySelectorAll('[data-project]').forEach(function (input) { var i = Number(input.dataset.index); projects[i] = projects[i] || {}; projects[i][input.dataset.project] = input.value.trim(); });
    var body = {
      name: el("profileName").value.trim(),
      city: el("profileCity").value.trim(),
      phone: el("profilePhone").value.trim(),
      email: el("profileEmail").value.trim(),
      status: el("profileStatus").value.trim(),
      github: el("profileGithub").value.trim(),
      location_preference: el("profileLocation").value.trim(),
      skills: {
        strong: splitProfileItems(el("profileStrengths").value),
        moderate: splitProfileItems(el("profileModerate").value),
        weak: (skills.weak || [])
      },
      career_goals: el("profileGoals").value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean),
      notes: el("profileMore").value.trim(),
      school: el("profileSchool").value.trim(),
      highest_degree: el("profileDegree").value.trim(),
      major: el("profileMajor").value.trim(),
      graduation_date: el("profileGraduation").value.trim(),
      english_level: el("profileEnglish").value.trim(),
      target_role: el("profileTargetRole") ? el("profileTargetRole").value.trim() : "",
      target_sectors: el("profileTargetSector") ? el("profileTargetSector").value.split(/[、,，\n]/).map(function (s) { return s.trim(); }).filter(Boolean) : [],
      target_city: el("profileTargetCity") ? el("profileTargetCity").value.trim() : "",
      available_date: el("profileAvailableDate") ? el("profileAvailableDate").value.trim() : "",
      filter_keywords: el("profileFilterKeywords") ? splitProfileItems(el("profileFilterKeywords").value) : [],
      filter_exclude_keywords: el("profileFilterExclude") ? splitProfileItems(el("profileFilterExclude").value) : [],
      min_match_score: el("profileMinScore") ? Math.max(0, Math.min(100, Number(el("profileMinScore").value) || 0)) : 0,
      accept_internship: el("profileAcceptInternship") ? el("profileAcceptInternship").value !== "0" : true,
      experiences: experiences,
      projects: projects
    };
    try {
      state.profile = await api("profile", { method: "PUT", body: body });
      state.user.profile = state.profile;
      await loadJobs();
      updateProfileBanner();
      toast("档案已保存并重新评估岗位", "success");
    } catch (e) { toast("保存失败：" + e.message, "error"); }
    finally {
      if (saveButton) { saveButton.disabled = false; saveButton.textContent = saveButton.dataset.originalText || "保存档案"; }
    }
  }

  function renderChat() {
    var body = el("chatBody");
    body.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    api("chat").then(function (messages) {
      body.innerHTML = messages.length
        ? messages.map(function (m) {
            return '<div class="chat-msg ' + (m.role === "user" ? "user" : "assistant") + '">' + esc(m.content) + "</div>";
          }).join("")
        : '<div class="muted text-sm">你好，我是你的 AI 求职助手。可以问我岗位推荐、简历写法或面试准备。</div>';
      body.scrollTop = body.scrollHeight;
    }).catch(function () {
      body.innerHTML = '<div class="muted text-sm">聊天记录加载失败</div>';
    });
  }

  async function sendChat(text) {
    text = (text || "").trim();
    if (!text) return;
    var body = el("chatBody");
    body.insertAdjacentHTML("beforeend", '<div class="chat-msg user">' + esc(text) + "</div>");
    el("chatInput").value = "";
    body.insertAdjacentHTML("beforeend", '<div class="chat-msg assistant"><div class="spinner" style="width:14px;height:14px"></div></div>');
    body.scrollTop = body.scrollHeight;
    try {
      var reply = await api("chat", { method: "POST", body: { message: text } });
      var msgs = body.querySelectorAll(".chat-msg");
      msgs[msgs.length - 1].outerHTML = '<div class="chat-msg assistant">' + esc(reply.content) + "</div>";
      el("chatModeLabel").textContent = reply.mode === "llm" ? "AI 增强模式" : "本地智能模式";
    } catch (e) {
      var last = body.querySelectorAll(".chat-msg");
      last[last.length - 1].outerHTML = '<div class="chat-msg assistant">请求失败：' + esc(e.message) + "</div>";
    }
    body.scrollTop = body.scrollHeight;
  }

  function openSettings() {
    el("settingsModal").classList.add("open");
    if (!state.settings) {
      api("settings").then(function (s) {
        state.settings = s;
        el("llmEnabled").checked = !!s.enabled;
        el("llmBase").value = s.base_url || "";
        el("llmKey").value = s.has_key ? "******" : "";
        el("llmModel").value = s.model || "";
      });
    }
  }

  async function saveSettings() {
    var body = {
      enabled: el("llmEnabled").checked,
      base_url: el("llmBase").value.trim(),
      api_key: el("llmKey").value.trim(),
      model: el("llmModel").value.trim()
    };
    try {
      state.settings = await api("settings", { method: "PUT", body: body });
      updateModePill();
      el("settingsModal").classList.remove("open");
      toast("设置已保存", "success");
    } catch (e) { toast("保存失败：" + e.message, "error"); }
  }

  function updateModePill() {
    var llmOn = !!(state.settings && state.settings.enabled && state.settings.has_key);
    el("modePill").classList.toggle("llm", llmOn);
    el("modeLabel").textContent = llmOn ? "AI 增强模式" : "本地模式";
    el("chatModeLabel").textContent = llmOn ? "AI 增强模式" : "本地智能模式";
  }

  function openAddJob() { el("addJobModal").classList.add("open"); }

  async function saveJob() {
    var title = el("jobTitle").value.trim();
    var company = el("jobCompany").value.trim();
    if (!title || !company) { toast("请填写岗位名称和公司", "error"); return; }
    var body = {
      title: title,
      company: company,
      city: el("jobCity").value.trim(),
      posting_type: el("jobType").value,
      salary: el("jobSalary").value.trim(),
      deadline: el("jobDeadline").value || "",
      tags: el("jobTags").value.split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean),
      url: el("jobUrl").value.trim(),
      description: el("jobDesc").value.trim(),
      requirements: el("jobReqs").value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean)
    };
    try {
      var job = await api("jobs", { method: "POST", body: body });
      el("addJobModal").classList.remove("open");
      await loadJobs();
      state.selectedJobId = job.id;
      state.view = "jobs";
      location.hash = "#/jobs";
      toast("岗位已保存并完成评估", "success");
      resetAddJobForm();
    } catch (e) { toast("保存失败：" + e.message, "error"); }
  }

  function resetAddJobForm() {
    ["jobTitle", "jobCompany", "jobCity", "jobSalary", "jobDeadline", "jobTags", "jobUrl", "jobDesc", "jobReqs"].forEach(function (id) {
      var node = el(id);
      if (node) node.value = "";
    });
  }

  async function loadJobs() {
    var data = await api("jobs?" + jobQuery(0));
    state.jobs = data.jobs || [];
    state.jobsTotal = Number(data.total || state.jobs.length);
    state.jobFacets = data.facets || state.jobFacets;
    var badge = el("jobsBadge");
    var count = state.jobs.filter(function (j) { return (j.evaluation || {}).overall >= 75; }).length;
    badge.textContent = count || "0";
  }

  function jobQuery(offset) {
    var params = new URLSearchParams({ limit: "20", offset: String(offset || 0), sort: state.jobSort || "score" });
    if (state.jobFilter) params.set("q", state.jobFilter);
    if (state.jobCityFilter) params.set("city", state.jobCityFilter);
    if (state.jobTypeFilter) params.set("type", state.jobTypeFilter);
    if (state.jobSourceFilter) params.set("source", state.jobSourceFilter);
    if (state.jobDeadlineFilter) params.set("deadline", state.jobDeadlineFilter);
    return params.toString();
  }

  async function loadMoreJobs() {
    var btn = el("loadMoreJobs");
    if (btn) { btn.disabled = true; btn.textContent = "加载中…"; }
    try {
      var data = await api("jobs?" + jobQuery(state.jobs.length));
      var known = new Set(state.jobs.map(function (job) { return job.id; }));
      (data.jobs || []).forEach(function (job) { if (!known.has(job.id)) state.jobs.push(job); });
      state.jobsTotal = Number(data.total || state.jobs.length);
      renderJobs();
    } catch (e) {
      toast("加载岗位失败：" + e.message, "error");
      if (btn) { btn.disabled = false; btn.textContent = "重新加载"; }
    }
  }

  async function loadApplications() {
    state.applications = await api("applications");
    el("pipelineBadge").textContent = state.applications.filter(function (a) { return ["已投递", "面试中"].indexOf(a.stage) >= 0; }).length || "0";
  }

  var titles = {
    dashboard: "总览",
    jobs: "岗位搜索",
    pipeline: "申请进度",
    interview: "面试准备",
    profile: "简历库"
  };

  function route() {
    if (!state.user) return;
    var hash = location.hash.replace(/^#\/?/, "");
    state.view = titles[hash] ? hash : "dashboard";
    el("pageTitle").textContent = titles[state.view];
    document.querySelectorAll(".nav-item[data-view]").forEach(function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-view") === state.view);
    });
    closeSidebar();
    if (state.view === "dashboard") renderDashboard();
    else if (state.view === "jobs") renderJobs();
    else if (state.view === "pipeline") renderPipeline();
    else if (state.view === "interview") renderInterview();
    else if (state.view === "profile") renderProfile();
  }

  function openSidebar() { el("sidebar").classList.add("open"); el("sidebarScrim").classList.remove("hide"); }
  function closeSidebar() { el("sidebar").classList.remove("open"); el("sidebarScrim").classList.add("hide"); }

  function bindGlobal() {
    el("tabLogin").addEventListener("click", function () {
      el("tabLogin").classList.add("active"); el("tabRegister").classList.remove("active");
      el("loginForm").classList.remove("hide"); el("registerForm").classList.add("hide");
    });
    el("tabRegister").addEventListener("click", function () {
      el("tabRegister").classList.add("active"); el("tabLogin").classList.remove("active");
      el("registerForm").classList.remove("hide"); el("loginForm").classList.add("hide");
    });
    el("loginForm").addEventListener("submit", function (e) {
      e.preventDefault();
      login(el("loginUsername").value, el("loginPassword").value, el("rememberMe").checked).catch(function (err) { toast(err.message, "error"); });
    });
    el("registerForm").addEventListener("submit", function (e) {
      e.preventDefault();
      register(el("regUsername").value, el("regEmail").value, el("regPassword").value).catch(function (err) { toast(err.message, "error"); });
    });
    el("guestBtn").addEventListener("click", function () { guest().catch(function (err) { toast(err.message, "error"); }); });

    document.querySelectorAll(".nav-item[data-view]").forEach(function (btn) {
      btn.addEventListener("click", function () { location.hash = "#/" + btn.getAttribute("data-view"); });
    });
    el("menuBtn").addEventListener("click", openSidebar);
    el("sidebarScrim").addEventListener("click", closeSidebar);
    el("chatToggle").addEventListener("click", function () {
      state.chatOpen = !state.chatOpen;
      el("chatPanel").classList.toggle("open", state.chatOpen);
      if (state.chatOpen) renderChat();
    });
    el("chatClose").addEventListener("click", function () { state.chatOpen = false; el("chatPanel").classList.remove("open"); });
    el("chatSend").addEventListener("click", function () { sendChat(el("chatInput").value); });
    el("chatInput").addEventListener("keydown", function (e) { if (e.key === "Enter") sendChat(el("chatInput").value); });
    el("chatSuggestions").addEventListener("click", function (e) { if (e.target.tagName === "BUTTON") sendChat(e.target.textContent); });
    el("settingsToggle").addEventListener("click", openSettings);
    el("saveSettings").addEventListener("click", saveSettings);
    el("addJobBtn").addEventListener("click", openAddJob);
    el("saveJob").addEventListener("click", saveJob);
    el("profileBannerBtn").addEventListener("click", function () { location.hash = "#/profile"; });
    el("userMenuBtn").addEventListener("click", function (e) { e.stopPropagation(); el("userDropdown").classList.toggle("hide"); });
    el("menuProfile").addEventListener("click", function () { el("userDropdown").classList.add("hide"); location.hash = "#/profile"; });
    el("menuUpgrade").addEventListener("click", function () { el("userDropdown").classList.add("hide"); el("upgradeModal").classList.add("open"); });
    el("menuLogout").addEventListener("click", logout);
    el("upgradeBtn").addEventListener("click", function () {
      upgrade(el("upgUsername").value, el("upgEmail").value, el("upgPassword").value).catch(function (err) { toast(err.message, "error"); });
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest("#userMenu")) el("userDropdown").classList.add("hide");
    });
    document.querySelectorAll("[data-close]").forEach(function (btn) {
      btn.addEventListener("click", function () { el(btn.getAttribute("data-close")).classList.remove("open"); });
    });
    document.querySelectorAll(".modal-overlay").forEach(function (overlay) {
      overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.classList.remove("open"); });
    });
    window.addEventListener("hashchange", route);
  }

  async function bootApp() {
    try {
      await Promise.all([loadJobs(), loadApplications()]);
      if (!state.settings) state.settings = await api("settings");
      updateModePill();
      route();
    } catch (e) {
      el("content").innerHTML = '<div class="content-inner"><div class="panel"><div class="panel-body">' + emptyBlock("加载失败", e.message) + "</div></div></div>";
    }
  }

  async function boot() {
    bindGlobal();
    showAuth();
    try {
      await loadMe();
      if (state.user) await bootApp();
    } catch (e) {
      showAuth();
    }
  }

  boot();
})();
