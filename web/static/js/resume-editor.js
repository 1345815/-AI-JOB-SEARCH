/* 简历可视化编辑器：openResumeEditor(profile) */
(function () {
  "use strict";

  var TEMPLATES = ["clean", "business", "modern", "academic"];
  var TEMPLATE_NAMES = { clean: "简洁", business: "商务", modern: "现代", academic: "学术" };
  var currentTpl = "clean";
  var currentProfile = {};

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function contactLine(p) {
    return [p.email, p.phone, p.city, p.github].filter(Boolean).join(" | ");
  }

  function section(title, html) {
    if (!html) return "";
    return '<section class="tpl-sec"><h3 class="tpl-sec-title">' + esc(title) + "</h3>" + html + "</section>";
  }

  function itemsHtml(items, titleKey, subFn, pointKey) {
    if (!items || !items.length) return "";
    return items.map(function (it) {
      var head = "<div class=\"tpl-item-head\"><span>" + esc(it[titleKey] || "") + "</span><span class=\"tpl-item-period\">" + esc(it.period || "") + "</span></div>";
      var sub = subFn ? '<div class="tpl-item-sub">' + esc(subFn(it)) + "</div>" : "";
      var pts = (it[pointKey] || []).map(function (pt) { return "<li>" + esc(pt) + "</li>"; }).join("");
      var ul = pts ? "<ul>" + pts + "</ul>" : "";
      return '<div class="tpl-item">' + head + sub + ul + "</div>";
    }).join("");
  }

  function buildResumeHtml(p, tpl) {
    p = p || {};
    var body = "";
    if (p.notes) body += section("个人概述", "<p style=\"margin:0\">" + esc(p.notes).replace(/\n/g, "<br>") + "</p>");
    body += section("教育背景", itemsHtml(p.education || [], "school", function (e) {
      return [e.degree, e.detail].filter(Boolean).join(" · ");
    }, null));
    body += section("实习经历", itemsHtml(p.experiences || [], "title", function (e) {
      return [e.company, e.role].filter(Boolean).join(" · ");
    }, "points"));
    body += section("项目经历", itemsHtml(p.projects || [], "title", function (pr) {
      return [pr.role, pr.tech].filter(Boolean).join(" · ");
    }, "points"));
    var skills = (p.skills && p.skills.strong) || [];
    if (skills.length) {
      body += section("专业技能", '<div class="tpl-tags">' + skills.map(function (s) { return '<span class="tpl-tag">' + esc(s) + "</span>"; }).join("") + "</div>");
    }
    if (p.certifications && p.certifications.length) {
      body += section("证书与获奖", "<ul>" + p.certifications.map(function (c) { return "<li>" + esc(c) + "</li>"; }).join("") + "</ul>");
    }
    var side = "";
    if (tpl === "modern") {
      side = '<aside class="tpl-side">' + section("联系方式", contactLine(p).split(" | ").map(function (c) { return "<div>" + esc(c) + "</div>"; }).join("")) +
        (skills.length ? section("专业技能", '<div class="tpl-tags">' + skills.map(function (s) { return '<span class="tpl-tag">' + esc(s) + "</span>"; }).join("") + "</div>") : "") + "</aside>";
      body = '<div class="tpl-main">' + body.replace(/<section class="tpl-sec"><h3 class="tpl-sec-title">专业技能<\/h3>[\s\S]*?<\/section>/, "") + "</div>";
    }
    var header = '<header class="tpl-header"><h1 class="tpl-name">' + esc(p.name || "候选人") + '</h1>' +
      (p.status ? '<div class="tpl-intent">' + esc(p.status) + "</div>" : "") +
      '<div class="tpl-contact">' + esc(contactLine(p)) + "</div></header>";
    return '<div class="resume-tpl ' + tpl + '">' + side + '<div>' + header + '<div class="tpl-body">' + body + "</div></div></div>";
  }

  function openResumeEditor(profile) {
    currentProfile = profile || {};
    var overlay = document.createElement("div");
    overlay.className = "resume-editor-overlay";
    overlay.innerHTML =
      '<div class="resume-editor">' +
      '<div class="resume-editor-head"><strong>🎨 简历制作</strong><span class="muted">编辑所见即所得 · 打印/另存为 PDF 导出</span>' +
      '<button class="btn btn-sm" id="reCloseEditor">关闭</button></div>' +
      '<div class="resume-editor-toolbar">' +
      '<span class="muted">模板：</span><div class="tpl-group" id="reTplGroup">' +
      TEMPLATES.map(function (t) { return '<button class="tpl-btn' + (t === currentTpl ? " active" : "") + '" data-tpl="' + t + '">' + TEMPLATE_NAMES[t] + "</button>"; }).join("") +
      '</div><span style="flex:1"></span>' +
      '<button class="btn btn-sm" id="rePrint">导出 PDF</button></div>' +
      '<div class="resume-editor-body">' +
      '<aside class="resume-editor-side"><h4>快捷调整</h4>' +
      '<div class="field"><label>姓名</label><input id="reName"></div>' +
      '<div class="field"><label>求职意向</label><input id="reIntent"></div>' +
      '<div class="field"><label>联系方式（邮箱 | 电话 | 城市）</label><input id="reContact"></div>' +
      '<p class="muted" style="font-size:12px">预览区可直接点击修改任意文字，改动即时生效。导出前建议保持一页。</p>' +
      "</aside>" +
      '<div class="resume-editor-preview"><div class="resume-page" contenteditable="true" id="rePage"></div></div>' +
      "</div></div>";
    document.body.appendChild(overlay);

    var page = overlay.querySelector("#rePage");
    var tplGroup = overlay.querySelector("#reTplGroup");
    overlay.querySelector("#reName").value = currentProfile.name || "";
    overlay.querySelector("#reIntent").value = currentProfile.status || "";
    overlay.querySelector("#reContact").value = contactLine(currentProfile);

    function render() {
      var p = Object.assign({}, currentProfile, {
        name: overlay.querySelector("#reName").value || currentProfile.name,
        status: overlay.querySelector("#reIntent").value || currentProfile.status,
        email: overlay.querySelector("#reContact").value,
        phone: "", city: "", github: "",
      });
      // 保留用户对页面内文字的修改：只重建外层结构，避免覆盖 contenteditable 内容
      page.innerHTML = buildResumeHtml(p, currentTpl);
      page.className = "resume-page resume-tpl-wrap " + currentTpl;
    }
    render();

    tplGroup.addEventListener("click", function (e) {
      var btn = e.target.closest(".tpl-btn");
      if (!btn) return;
      currentTpl = btn.getAttribute("data-tpl");
      tplGroup.querySelectorAll(".tpl-btn").forEach(function (b) { b.classList.toggle("active", b === btn); });
      render();
    });

    overlay.querySelector("#rePrint").addEventListener("click", function () { window.print(); });
    overlay.querySelector("#reCloseEditor").addEventListener("click", function () { overlay.remove(); });
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
  }

  window.openResumeEditor = openResumeEditor;
})();
