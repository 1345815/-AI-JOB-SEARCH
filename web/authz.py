"""RBAC 权限矩阵（复用 users.role 字段）。

角色 → 权限映射：
- guest：匿名能力（种子岗位查看、登录注册、游客使用）
- user：自身数据管理（岗位/申请/简历/文档/对话）
- admin：settings/audit/用户管理/团队治理
"""

ROLES = {
    "guest": [
        "jobs.view",
        "auth.login",
        "auth.register",
    ],
    "user": [
        "jobs.view",
        "jobs.manage",          # 录入/删除自己的岗位
        "evaluations.view",
        "applications.manage",
        "resumes.manage",
        "documents.manage",
        "chat.use",
        "teams.use",
        "data.export",
        "data.delete",
    ],
    "admin": [
        "jobs.view",
        "jobs.manage",
        "evaluations.view",
        "applications.manage",
        "resumes.manage",
        "documents.manage",
        "chat.use",
        "teams.use",
        "data.export",
        "data.delete",
        "settings.manage",
        "audit.view",
        "users.manage",
        "admin.all",
    ],
}

ALL_PERMISSIONS = sorted({p for perms in ROLES.values() for p in perms})


def has_permission(role, perm):
    if not role:
        role = "guest"
    role = role if role in ROLES else "user"
    return perm in ROLES.get(role, [])


def require_permission(perm):
    """装饰器：handler 方法需要某权限（user 对象通过 self._current_user 获取）。"""
    def decorator(func):
        def wrapper(handler, *args, **kwargs):
            user = handler._current_user()
            role = (user or {}).get("role", "guest") if user else "guest"
            if not has_permission(role, perm):
                handler._send(403, {"ok": False, "error": "需要权限：" + perm})
                return None
            return func(handler, user, *args, **kwargs)
        wrapper.perm = perm
        return wrapper
    return decorator
