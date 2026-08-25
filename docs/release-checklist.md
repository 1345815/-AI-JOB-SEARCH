# 发布检查清单（Release Checklist）

> 每次发布前逐项勾选。发布流程：备份 → 迁移预演 → 部署 → 迁移 → 冒烟 → 观察。

## 发布前

- [ ] `python -m pytest -q` 全量测试 0 failed（环境性失败需确认与改动无关）
- [ ] `python -m compileall web scripts` 语法编译通过
- [ ] `python -m web.migrations status` 确认待应用迁移列表符合预期
- [ ] 数据库备份：`python -m web.db_backup` 或确认 `start_backup_scheduler` 已产出最新备份
- [ ] 迁移预演：`python -m web.migrations dry-run` 检查将执行的 SQL 无意外
- [ ] 变更涉及前端时，`node --check web/static/js/app.js` 语法校验

## 发布中

1. 部署代码（bundle 离线拷贝 / git pull）
2. 执行迁移：`python -m web.migrations up`
3. 冒烟：
   - `curl -s localhost:8000/healthz` → `{"status":"ok"}`
   - `curl -s localhost:8000/metrics | head` → 有 `careerpilot_` 指标
   - 登录后访问关键 API（岗位列表 / 今日待办 / 漏斗）
4. 观察告警窗口（对应 `docs/slo.md`）：5xx 率、P95 延迟、可用性

## 发布后

- [ ] 异常时立即按 `docs/rollback.md` 回滚
- [ ] 记录发布版本与时间
