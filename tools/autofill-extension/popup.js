(function () {
  "use strict";
  var status = document.getElementById("status");
  function say(msg, cls) { status.textContent = msg; status.className = cls || ""; }

  chrome.storage.sync.get(["cpBase", "cpToken"], function (s) {
    if (s.cpBase) document.getElementById("cpBase").value = s.cpBase;
    if (s.cpToken) document.getElementById("cpToken").value = s.cpToken;
  });

  document.getElementById("saveBtn").addEventListener("click", function () {
    var base = document.getElementById("cpBase").value.trim();
    var token = document.getElementById("cpToken").value.trim();
    if (!base || !token) { say("请填写服务地址和令牌", "err"); return; }
    chrome.storage.sync.set({ cpBase: base, cpToken: token }, function () {
      say("✓ 已保存", "ok");
    });
  });

  document.getElementById("testBtn").addEventListener("click", function () {
    var base = document.getElementById("cpBase").value.trim();
    var token = document.getElementById("cpToken").value.trim();
    if (!base || !token) { say("请先填写并保存", "err"); return; }
    say("测试中…");
    fetch(base.replace(/\/+$/, "") + "/api/ext/profile", {
      headers: { "Authorization": "Bearer " + token },
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      if (!data.ok) throw new Error(data.error || "未知错误");
      var p = data.profile || {};
      say("✓ 连接成功：档案 " + (p.name || "未填写") + "（" + (p.school || "学校未填") + "）", "ok");
    }).catch(function (e) {
      say("✗ 失败：" + e.message + "。检查地址/令牌/CORS", "err");
    });
  });
})();
