"""把采集到的岗位 JSON 导入 CareerPilot 岗位库（去重 + AI 评分由服务端完成）。

用法：
  python import_jobs.py --file jobs/xxx.json --base http://111.230.228.15:8000 --user 用户名 --password 密码
  （推荐 --user/--password 登录；脚本会自动携带会话 cookie）

说明：认证走 CareerPilot 的 cookie 会话机制，密码仅本次进程内使用。
"""
import argparse
import http.cookiejar
import json
import sys
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:8000"


def make_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)), cj


def login(opener, base, user, password):
    req = urllib.request.Request(
        base.rstrip("/") + "/api/auth/login",
        data=json.dumps({"username": user, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=15) as resp:
        return json.loads(resp.read())


def import_jobs(opener, base, jobs):
    url = base.rstrip("/") + "/api/jobs/import"
    req = urllib.request.Request(
        url, data=json.dumps({"jobs": jobs}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with opener.open(req, timeout=180) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser(description="导入采集岗位到 CareerPilot")
    ap.add_argument("--file", required=True, help="采集器输出的 JSON 文件")
    ap.add_argument("--base", default=DEFAULT_BASE, help="CareerPilot 服务地址")
    ap.add_argument("--user", required=True, help="CareerPilot 用户名")
    ap.add_argument("--password", required=True, help="CareerPilot 密码")
    args = ap.parse_args()

    jobs = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(jobs, list):
        print("文件格式错误：应为岗位数组")
        return 1
    print(f"待导入 {len(jobs)} 条岗位 → {args.base}")

    opener, _ = make_opener()
    try:
        login(opener, args.base, args.user, args.password)
    except urllib.error.HTTPError as e:
        print(f"登录失败 HTTP {e.code}：{e.read().decode('utf-8', 'ignore')[:150]}")
        return 1
    except Exception as e:
        print(f"登录失败：{e}")
        return 1

    try:
        res = import_jobs(opener, args.base, jobs)
        print(f"✅ 导入完成：新增 {res.get('added', 0)} 条，重复跳过 {res.get('skipped', 0)} 条，失败 {res.get('failed', 0)} 条")
        if res.get("added"):
            print("去 CareerPilot「岗位搜索」即可看到新岗位（已自动评分）")
        return 0
    except urllib.error.HTTPError as e:
        print(f"导入失败 HTTP {e.code}：{e.read().decode('utf-8', 'ignore')[:200]}")
        return 1
    except Exception as e:
        print(f"导入失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
