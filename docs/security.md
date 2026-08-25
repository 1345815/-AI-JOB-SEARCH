# 安全规范（Security）

## 密钥管理

- **API Key 用环境变量注入**：`LLM_API_KEY`（docker-compose 已支持），优先级 **env > settings.json**
- 禁止把 api_key 写入：settings.json（明文）、日志、审计、前端返回体
- `settings` API 永远只回 `has_key` 布尔值，不回显明文（现状已满足，保持不变）
- `api_key` 出现于日志时即视为安全事故（可观测性日志字段白名单已排除）

## 审计保留策略

- `audit_log` 默认保留 **180 天**，过期清理由 `web/cache.py` 的 cleanup 任务扩展
  （当前 cleanup 清理 sessions/login_attempts/cache_entries；audit_log 保留策略上线时追加 DELETE）
- `admin_actions` 表记录管理员治理操作（停用/提权），与 audit_log 互补

## 角色权限（RBAC）

| 角色 | 能力 |
|---|---|
| guest | 种子岗位查看、登录注册、游客使用 |
| user | 自身数据管理：岗位/申请/简历/文档/对话/团队 |
| admin | + settings 管理、audit 查看、用户治理、团队治理 |

权限矩阵定义在 `web/authz.py`（`has_permission` / `require_permission`）。

## 威胁模型与缓解

| 威胁 | 缓解 |
|---|---|
| XSS | CSP `default-src 'self'`；前端全部输出经 `esc()` 转义 |
| CSRF | SameSite=Lax cookie；无跨站写接口 |
| 越权访问 | 所有 /api/admin/* 校验 admin 角色（403）；资源操作按 user_id 隔离 |
| 密钥泄漏 | api_key 环境变量注入；日志字段白名单；settings 不回显 |
| 暴力破解 | 登录限流（DB 共享，跨 worker 生效）+ 审计留痕 |
| SSRF | job_extractor 校验目标为公网地址（`validate_public_url`） |
| 会话劫持 | HttpOnly + Secure cookie；会话滑动过期限频 |

## SSO（可选）

`web/sso.py` 提供标准 OIDC 客户端抽象（authorize/exchange/userinfo），
配置 `OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET` 后可用；未配置时保持密码登录。
