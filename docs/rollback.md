# 回滚 SOP（Rollback Playbook）

## 触发条件

满足任一即触发回滚：
- 迁移执行报错 / 迁移后 API 大面积 5xx
- `/healthz` 不健康
- 冒烟关键路由失败（岗位列表 / 今日待办 / 漏斗）
- SLO 告警触发（5xx 率 >1% / P95 >2s）持续 10 分钟以上

## 回滚步骤

1. **停新版本**：停止服务进程（`Ctrl+C` 或停 systemd/docker 容器）
2. **恢复数据库**：用最近一份有效备份恢复
   - `python -m web.db_backup` 的 restore 功能，或
   - 手动：`cp web/data/backups/<最新>.db web/data/careerpilot.db`
   - 恢复后 `python -m web.db_backup` 校验完整性（`verify_database`）
3. **回滚迁移**（若数据库已执行新迁移且无备份可恢复）：
   - `python -m web.migrations down 1`（每版本回滚一步）
   - ⚠️ 注意：**已提交的迁移事务无法自动撤销**；down 迁移必须手工编写，禁止空 down
4. **起旧版本**：恢复上一个发布版本的代码（git checkout 上个 tag / bundle 回拷）
5. **验证**：`curl -s localhost:8000/healthz` + 关键 API 冒烟 + 全量测试

## 原则

- 每个发布版本独立 commit / tag，回滚以版本为单位，禁止 `reset --hard`
- 迁移文件一旦发布不可修改（只允许新增更高版本），确保幂等
- 回滚后必须复盘根因，写新迁移修复而非改旧迁移
