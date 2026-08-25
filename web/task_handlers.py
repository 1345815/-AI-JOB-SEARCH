"""任务执行器：task_type → 具体业务函数映射。

每个 handler 接收 (input_dict) 返回可 JSON 序列化的结果；
worker 捕获异常后由队列层标记 failed 并写审计。
业务函数延迟 import web.server，避免模块循环依赖。
"""

import time


class TaskError(Exception):
    """业务级错误（如岗位不存在），worker 会记录 error 并置 failed。"""


def _server():
    try:
        import server
        return server
    except ImportError:
        from web import server
        return server


def handle_resume_generate(data):
    """生成定制简历（resume.generate）：input {job_id, profile}"""
    return _handle_doc(data, "resume")


def handle_cover_letter_generate(data):
    """生成定制求职信（cover_letter.generate）：input {job_id, profile}"""
    return _handle_doc(data, "cover_letter")


def _handle_doc(data, kind):
    server = _server()
    job_id = (data or {}).get("job_id")
    profile = (data or {}).get("profile") or {}
    job = server.get_job(job_id) if job_id else None
    if not job:
        raise TaskError("岗位不存在")
    profile = server.normalize_profile(profile)
    if kind == "resume":
        content = server.generate_resume(job, profile)
    else:
        content = server.generate_cover_letter(job, profile)
    user_id = (data or {}).get("user_id")
    conn = server.db()
    conn.execute(
        "INSERT INTO documents (user_id, job_id, kind, content, created_at) VALUES (?,?,?,?,?)",
        (user_id, job_id, kind, content, time.strftime("%Y-%m-%d %H:%M")),
    )
    conn.commit()
    doc_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.close()
    return {"id": doc_id, "job_id": job_id, "kind": kind, "content": content}


def handle_interview_generate(data):
    """生成面试准备包（interview.generate）：input {job_id, profile}"""
    server = _server()
    job_id = (data or {}).get("job_id")
    profile = (data or {}).get("profile") or {}
    job = server.get_job(job_id) if job_id else None
    if not job:
        raise TaskError("岗位不存在")
    profile = server.normalize_profile(profile)
    content = server.generate_interview_prep(job, profile)
    user_id = (data or {}).get("user_id")
    conn = server.db()
    conn.execute(
        """INSERT OR REPLACE INTO interview_preps (user_id, job_id, content, created_at)
           VALUES (?,?,?,?)""",
        (user_id, job["id"], content, time.strftime("%Y-%m-%d %H:%M")),
    )
    conn.commit()
    conn.close()
    return {"job_id": job["id"], "content": content}


def handle_search(data):
    """岗位搜索（search）：input {query, settings} — 外部数据源聚合，耗时操作"""
    server = _server()
    try:
        from job_extractor import search_jobs
    except ImportError:
        from web.job_extractor import search_jobs
    query = (data or {}).get("query") or {}
    settings = (data or {}).get("settings") or server.load_settings()
    results = search_jobs(query, settings)
    return {"data": results, "sources": server.search_source_health(results),
            "cached": bool((data or {}).get("cached"))}


HANDLERS = {
    "resume.generate": handle_resume_generate,
    "cover_letter.generate": handle_cover_letter_generate,
    "interview.generate": handle_interview_generate,
    "search": handle_search,
}
