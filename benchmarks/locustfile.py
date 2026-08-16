

"""
压测前: pip install locust
启动服务并灌库后:
  locust -f benchmarks/locustfile.py --host http://127.0.0.1:8000
浏览器打开 http://localhost:8089 配置并发。

环境变量:
  RAG_BENCHMARK_TOKEN  与 .env 中一致时可免 login（可选）
"""
import os

from locust import HttpUser, between, task

QUESTIONS = [
    "出差报销要在几个工作日内提交？",
    "单笔报销超过多少需要总监审批？",
    "报销需要附什么材料？",
]


class RagLoadUser(HttpUser):
    wait_time = between(0.3, 1.0)
    token: str | None = None

    def on_start(self):
        bench = os.getenv("RAG_BENCHMARK_TOKEN", "")
        if bench:
            self.token = bench
            return
        resp = self.client.post(
            "/api/v1/login",
            json={"username": "demo", "password": "demo123"},
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    @task(10)
    def rag_local_search(self):
        q = QUESTIONS[self.environment.runner.user_count % len(QUESTIONS)]
        self.client.post(
            "/api/v1/rag/local_search",
            json={"question": q, "top_k": 3},
            headers=self._headers(),
            name="/rag/local_search",
        )

    @task(1)
    def rag_ask(self):
        self.client.post(
            "/api/v1/rag/ask",
            json={"question": QUESTIONS[0], "top_k": 3},
            headers=self._headers(),
            name="/rag/ask",
        )