# 05 · RBAC / 审计 / SSO / 密钥管理提示词（Codex）

## 🩺 根因诊断

| 坑 | 当前表现 | 排查命令 |
|---|---|---|
| RBAC 只有雏形 | `users.role` 字段存在但无权限矩阵、无资源级校验，任何登录用户可调所有 API | `web/server.py:242`、`_api()` L1807 无权限检查 |
| 无审计日志 | 登录失败、settings 变更、数据删除等敏感操作无任何可追溯记录 | 仓库无 audit 相关表/代码 |
| 密钥明文落盘 | `api_key` 明文存 `settings.json`，权限依赖 OS 文件系统 | `web/server.py:42`、`:1877-1897` |
| 无 SSO | 只有本地账号密码登录，无法对接企业 IdP（OIDC） | `web/server.py:411-417` 密码哈希（本阶段不动） |
| 无合规能力 | 无数据保留策略、无审计保留/导出，无法回答"谁在何时改了什么" | `docs/` 无 security/compliance 文档 |

**最常见组合**：① + ② + ③ —— 先做"权限矩阵 + 审计表 + 密钥去明文"，SSO 作为可选增强。

## 📋 提示词正文（整段复制给 Codex）

```text
请在当前工作区（AI-JOB-SEARCH 仓库根目录）实施"RBAC/审计/密钥管理（SSO 可选）"。你是资深后端工程师，只做本任务，不做无关重构。

# 目标
1. 完整 RBAC：角色枚举 + 权限矩阵 + require_permission 校验，复用现有 users.role 字段
2. 审计日志：audit_log 表 + 关键动作埋点 + admin 查询端点
3. 密钥管理：api_key 不再依赖 settings.json 明文（环境变量优先），settings API 永不回显明文
4. SSO（可选交付）：OIDC 客户端抽象 + 配置文档，默认关闭、零影响

# 必读上下文（先读再动手）
- web/server.py:242 users.role（现有角色字段，默认 'guest'）
- web/server.py:171 load_settings（api_key 读取 @:175 已支持 os.environ LLM_API_KEY）/ :191 save_settings
- web/server.py:1877-1897 settings API 的 api_key 处理（has_key 逻辑保留）
- web/server.py:411 hash_password / :417 verify_password（本阶段禁止改动）
- web/server.py:1777 _current_user / :498 user_public（用户对象与脱敏）
- web/server.py:1807 _api() 分发（审计埋点与 admin 端点的落点）
- web/server.py:430-452 登录限流（登录成功/失败审计埋点在此附近；login 路由定位方式：grep -n "login\|password" web/server.py）
- 02 阶段迁移框架 web/migrations.py（新表必须走迁移）

# 你必须新增/修改的内容
1. 新建迁移 web/migrations/003_audit_log.sql + .down.sql：
   - up：
     CREATE TABLE IF NOT EXISTS audit_log (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       ts REAL NOT NULL,
       user_id INTEGER,
       action TEXT NOT NULL,
       resource TEXT DEFAULT '',
       resource_id TEXT DEFAULT '',
       ip TEXT DEFAULT '',
       user_agent TEXT DEFAULT '',
       meta_json TEXT DEFAULT '{}'
     );
     CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
   - down：DROP TABLE audit_log;
2. 新建 web/authz.py：
   - ROLES = {"guest": [...], "user": [...], "admin": [...]}（权限项如 jobs.view、evaluations.manage、settings.manage、audit.view、admin.all）
   - has_permission(role, perm)、require_permission(perm) 装饰器（装饰 _api 内的 handler 方法或分发时校验）
   - 默认映射：guest = 现有匿名能力（种子岗位查看/登录注册），user = 自身数据管理，admin = settings/audit/用户管理
   - 用 403 表示已登录但无权限，401 表示未登录（保持既有错误格式：{"error": ...}）
3. 修改 web/server.py：
   - 新增 audit(action, resource="", resource_id="", user_id=None, ip="", ua="", meta=None)：写 audit_log（失败不抛出，避免影响主流程）
   - 埋点（至少）：登录成功（action=login.success）、登录失败（login.failure）、settings 变更（settings.update，meta 不含值只含变更键名）、profile 变更（profile.update）、任意 DELETE（data.delete，resource+resource_id）
   - 新增 GET /api/admin/audit?limit=100&action=xxx：仅 admin（require_permission("audit.view")），返回脱敏列表（不含 body）
   - settings 的 api_key：读取优先级 os.environ["LLM_API_KEY"] > settings.json；save_settings 永不写明文 api_key（保留 has_key）；GET 永远只回 has_key（现状已如此，保持）
4. 新建 web/sso.py（可选，但必须交付）：
   - class OIDCClient（authorize_url / exchange_code / userinfo），纯标准库 urllib 实现
   - 配置从环境变量 OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET 读取；未配置时 login 流程走原密码登录，SSO 登录入口不渲染
   - 不引入第三方 OIDC 库；实现以"可对接标准 OIDC Provider"为验收
5. 新建 docs/security.md：
   - 密钥管理规范：api_key 用环境变量注入（docker-compose 已支持 LLM_API_KEY），禁止写入 settings.json / 日志 / git
   - 审计保留策略：audit_log 默认保留 180 天，过期由 04 阶段 cleanup 扩展清理（本阶段文档声明）
   - 威胁模型简述与缓解对照（XSS/CSRF/越权/密钥泄漏）
6. 新增 tests/test_authz.py：
   - guest 访问 /api/admin/audit → 403（或 401）
   - admin 访问 → 200
   - 登录失败会写 audit_log（action=login.failure）
   - settings GET 不含明文 api_key
   - 迁移 003 应用后表存在

# 约束
- 禁止改动 hash_password / verify_password（L411/417）与密码存储格式
- 不破坏现有登录/注册流程与 guest 能力
- 审计日志禁止记录：password、api_key、request body、profile_json 内容
- SSO 默认关闭且不引入第三方依赖；未配置环境变量时行为与现状完全一致
- 新表只走迁移框架（003）

# 期望交付物
- web/migrations/003_audit_log.sql + .down.sql
- web/authz.py、web/sso.py（新）
- web/server.py（审计埋点 + admin 端点 + settings key 处理）
- docs/security.md（新）
- tests/test_authz.py（新）

# 验收（全部通过才算完成）
cd AI-JOB-SEARCH
python -m pytest tests/test_authz.py -q                # 新测试全绿
python -m pytest -q                                    # 全量 0 failed
python -m compileall web
python -m web.migrations up --db /tmp/cp_backup.db     # 003 应用成功
# 手工验证：
# 1) 未登录访问 admin 端点：
curl -s -o /dev/null -w "%{http_code}" localhost:8000/api/admin/audit        # 401 或 403
# 2) 登录后（替换 token）：
curl -s -H "Cookie: careerpilot_session=<admin_token>" localhost:8000/api/admin/audit?limit=10   # 200 JSON
# 3) 审计落库：
sqlite3 data/careerpilot.db "SELECT action, COUNT(*) FROM audit_log GROUP BY action;"
# 4) 无明文 key 泄漏：
curl -s localhost:8000/api/settings | grep -c api_key   # 只应出现 "has_key" 键，无真实值
grep -rn "api_key.*=.*sk-" web/ 2>/dev/null | grep -v has_key || echo "无明文 key 硬编码"
```

## 🛠 自检命令（脱离 Codex 手动验证）

```bash
cd AI-JOB-SEARCH
sqlite3 data/careerpilot.db "SELECT ts, user_id, action, resource FROM audit_log ORDER BY id DESC LIMIT 10;"
```

## 💡 高级选项

- SSO 对接真实 IdP：配置 OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET 后，/api/auth/oidc 提供跳转；docs/sso.md 给出 Keycloak/Auth0 对接步骤。
- 更严的密钥管理：若未来需要"加密存储"，在 web/sso.py 同级提供 KMS/Env 双模式，不落明文；当前环境变量方案已满足最小合规。
