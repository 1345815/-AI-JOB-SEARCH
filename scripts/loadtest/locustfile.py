# -*- coding: utf-8 -*-
"""Locust 压测文件（可选）。

用法：需先安装 locust（不进 requirements.txt）：
    pip install locust
    locust -f scripts/loadtest/locustfile.py --host http://127.0.0.1:8000

与 scripts/loadtest/basic_load.py（纯标准库版）二选一。
"""

from locust import HttpUser, between, task  # noqa: E402


class CareerPilotUser(HttpUser):
    wait_time = between(0.5, 2)

    @task(3)
    def healthz(self):
        self.client.get("/healthz")

    @task(2)
    def jobs(self):
        self.client.get("/api/jobs?limit=20")

    @task(1)
    def today_tasks(self):
        self.client.get("/api/today-tasks")
