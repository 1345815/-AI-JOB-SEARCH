/* CareerPilot AI 填网申 - content script
 * 检测页面表单，注入浮动按钮；点击后从 CareerPilot 拉取档案并按字段语义填充。 */
(function () {
  "use strict";

  var FIELDS = {
    name: {
      label: "姓名",
      hint: ["姓名", "您的姓名", "你的姓名", "真实姓名", "full name", "name"],
      value: function (p) { return p.name; },
    },
    phone: {
      label: "手机",
      hint: ["手机", "手机号", "联系电话", "电话", "联系方式", "mobile", "phone", "tel"],
      value: function (p) { return p.phone; },
    },
    email: {
      label: "邮箱",
      hint: ["邮箱", "电子邮件", "e-mail", "email", "mail"],
      value: function (p) { return p.email; },
    },
    school: {
      label: "学校",
      hint: ["学校", "院校", "毕业院校", "大学", "school", "university", "college"],
      value: function (p) { return p.school; },
    },
    degree: {
      label: "学历",
      hint: ["学历", "最高学历", "学位", "degree", "education"],
      value: function (p) {
        var d = p.degree || "";
        if (d.indexOf("本科") >= 0) return "本科";
        if (d.indexOf("硕士") >= 0) return "硕士";
        if (d.indexOf("博士") >= 0) return "博士";
        if (d.indexOf("大专") >= 0 || d.indexOf("专科") >= 0) return "大专";
        return d;
      },
    },
    major: {
      label: "专业",
      hint: ["专业", "所学专业", "主修", "major", "specialty"],
      value: function (p) { return p.major; },
    },
    graduation: {
      label: "毕业时间",
      hint: ["毕业时间", "毕业年份", "预计毕业", "graduation", "毕业日期"],
      value: function (p) {
        var g = p.graduation || "";
        var m = g.match(/(20\d{2})[年.\/-]?(\d{1,2})?/);
        if (m) return m[2] ? m[1] + "-" + m[2] : m[1];
        return g;
      },
    },
    english: {
      label: "英语等级",
      hint: ["英语", "cet", "四六级", "六级", "四级", "english"],
      value: function (p) { return p.english_level; },
    },
    gender: {
      label: "性别",
      hint: ["性别", "gender", "sex"],
      value: function (p) { return p.gender; },
    },
    city: {
      label: "意向城市",
      hint: ["期望城市", "意向城市", "工作城市", "工作地点", "base", "city"],
      value: function (p) { return p.city; },
    },
    experience: {
      label: "经历",
      hint: ["工作经历", "实习经历", "个人经历", "项目经历", "工作经验", "实习经验", "项目经验", "工作与实习", "experience"],
      value: function (p) {
        return (p.experiences || []).map(function (e) {
          return e.company + " " + e.title;
        }).join("；");
      },
    },
  };

  function settings(cb) {
    chrome.storage.sync.get(["cpBase", "cpToken"], function (s) { cb(s || {}); });
  }

  function fieldHint(el) {
    var parts = [];
    if (el.getAttribute("placeholder")) parts.push(el.getAttribute("placeholder"));
    if (el.getAttribute("name")) parts.push(el.getAttribute("name"));
    if (el.getAttribute("id")) parts.push(el.getAttribute("id"));
    if (el.getAttribute("aria-label")) parts.push(el.getAttribute("aria-label"));
    if (el.getAttribute("title")) parts.push(el.getAttribute("title"));
    var label = null;
    if (el.id) {
      var l = document.querySelector('label[for="' + el.id + '"]');
      if (l) label = l.textContent;
    }
    if (!label && el.closest && el.closest("form")) {
      var wrap = el.closest("div, li, tr, .form-item, .field");
      if (wrap && wrap !== el) label = wrap.textContent;
    }
    if (label) parts.push(label);
    var prev = el.previousElementSibling;
    if (prev && prev.tagName === "LABEL") parts.push(prev.textContent);
    return parts.join(" ").toLowerCase();
  }

  function pickValue(fieldKey, hintText, profile) {
    var field = FIELDS[fieldKey];
    var score = 0;
    field.hint.forEach(function (kw) {
      if (hintText.indexOf(kw.toLowerCase()) >= 0) score++;
    });
    var value = field.value(profile);
    return { score: score, value: value };
  }

  function fillableEls() {
    var out = [];
    document.querySelectorAll("input, select, textarea").forEach(function (el) {
      if (el.disabled || el.readOnly) return;
      var type = (el.getAttribute("type") || "text").toLowerCase();
      if (["hidden", "submit", "button", "reset", "file", "image", "checkbox", "radio", "password"].indexOf(type) >= 0) return;
      if (el.offsetParent === null && !el.getAttribute("required")) return; // 不可见且非必填跳过
      out.push(el);
    });
    return out;
  }

  function setValue(el, value) {
    if (!value) return false;
    var tag = el.tagName.toLowerCase();
    if (tag === "select") {
      var opts = Array.prototype.slice.call(el.options || []);
      var match = opts.find(function (o) {
        var t = o.textContent.trim();
        return t.indexOf(value) >= 0 || value.indexOf(t) >= 0;
      }) || opts.find(function (o) { return o.textContent.trim() === value; });
      if (!match) return false;
      el.value = match.value;
    } else {
      el.focus();
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.blur();
    }
    el.style.outline = "2px solid #16a34a";
    el.style.outlineOffset = "1px";
    return true;
  }

  function runFill(profile) {
    var filled = 0;
    var filledNames = [];
    fillableEls().forEach(function (el) {
      var hint = fieldHint(el);
      var best = null;
      Object.keys(FIELDS).forEach(function (key) {
        var r = pickValue(key, hint, profile);
        if (r.score > 0 && (!best || r.score > best.score)) best = r;
      });
      if (best && best.value) {
        if (setValue(el, best.value)) {
          filled++;
          if (filledNames.indexOf(best.label) < 0) filledNames.push(best.label);
        }
      }
    });
    return { filled: filled, fields: filledNames };
  }

  function ensureButton() {
    var id = "cpAutofillBtn";
    if (document.getElementById(id)) return document.getElementById(id);
    var btn = document.createElement("button");
    btn.id = id;
    btn.textContent = "🤖 AI 填表";
    btn.style.cssText = "position:fixed;right:18px;bottom:90px;z-index:2147483647;padding:10px 16px;border:none;border-radius:999px;background:#2563eb;color:#fff;font-size:14px;font-weight:600;cursor:pointer;box-shadow:0 4px 14px rgba(37,99,235,.4);font-family:system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;";
    btn.addEventListener("click", function () {
      settings(function (s) {
        if (!s.cpBase || !s.cpToken) {
          alert("请先在插件弹窗中配置 CareerPilot 服务地址和插件令牌");
          return;
        }
        btn.textContent = "填表中…";
        btn.disabled = true;
        fetch(s.cpBase.replace(/\/+$/, "") + "/api/ext/profile", {
          headers: { "Authorization": "Bearer " + s.cpToken },
        }).then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        }).then(function (data) {
          if (!data.ok || !data.profile) throw new Error(data.error || "档案为空");
          var result = runFill(data.profile);
          alert("已填写 " + result.filled + " 个字段：" + (result.fields.join("、") || "无") + "。请核对后提交。");
        }).catch(function (e) {
          alert("填表失败：" + e.message + "。请检查服务地址/令牌，或确认档案已填写。");
        }).finally(function () {
          btn.textContent = "🤖 AI 填表";
          btn.disabled = false;
        });
      });
    });
    document.body.appendChild(btn);
    return btn;
  }

  // 页面有表单时显示按钮（延迟等待动态表单）
  function maybeShow() {
    if (document.querySelector("form") || document.querySelector('input[name], input[type="text"], select, textarea')) {
      ensureButton();
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { setTimeout(maybeShow, 800); });
  } else {
    setTimeout(maybeShow, 800);
  }
  setInterval(maybeShow, 4000);
})();
