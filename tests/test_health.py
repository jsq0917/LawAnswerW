"""接口层测试：健康检查、请求 ID、异常信封。

全部用 monkeypatch 替换依赖探测函数，**不连真实 MySQL/Redis/Milvus**。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app.api.health as health_mod
from app.core.errors import NotFoundError
from app.main import create_app

OK_RESULT = {"status": "ok", "latency_ms": 1.5, "detail": "stub", "error": None}
DOWN_RESULT = {"status": "down", "latency_ms": 5.0, "detail": None, "error": "ConnectionRefused"}


@pytest.fixture
def stub_services(monkeypatch):
    """默认三个依赖都健康；返回一个可改状态的字典。"""

    state = {"mysql": OK_RESULT, "redis": OK_RESULT, "milvus": OK_RESULT}
    for name in state:
        monkeypatch.setattr(health_mod, f"check_{name}", lambda n=name: state[n])
    return state


@pytest.fixture
def api() -> TestClient:
    return TestClient(create_app())


class TestHealth:
    def test_all_ok(self, api, stub_services):
        resp = api.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["app"] == "LawAnswer"
        assert set(body["services"]) == {"mysql", "redis", "milvus"}
        assert body["services"]["mysql"]["status"] == "ok"
        assert isinstance(body["llm_key_configured"], bool)

    def test_degraded_but_http_200(self, api, stub_services):
        """单点故障不应让健康检查变成 500，也不应让整站不可用。"""
        stub_services["milvus"] = DOWN_RESULT
        resp = api.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["services"]["milvus"]["status"] == "down"
        assert body["services"]["mysql"]["status"] == "ok"

    def test_health_never_leaks_secret(self, api, stub_services, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-very-secret-value")
        body = api.get("/api/health").json()
        assert body["llm_key_configured"] is True
        assert "sk-very-secret-value" not in str(body)

    def test_liveness_does_not_touch_dependencies(self, api, monkeypatch):
        def boom():  # pragma: no cover - 被调用即失败
            raise AssertionError("liveness 不应探测依赖")

        monkeypatch.setattr(health_mod, "check_mysql", boom)
        resp = api.get("/api/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "alive"}

    def test_root_endpoint(self, api):
        body = api.get("/").json()
        assert body["health"] == "/api/health"
        assert body["docs"] == "/docs"


class TestRequestId:
    def test_echoes_incoming_request_id(self, api, stub_services):
        resp = api.get("/api/health", headers={"X-Request-ID": "trace-abc-001"})
        assert resp.headers["X-Request-ID"] == "trace-abc-001"
        assert resp.json()["request_id"] == "trace-abc-001"

    def test_generates_id_when_absent(self, api, stub_services):
        resp = api.get("/api/health")
        rid = resp.headers["X-Request-ID"]
        assert len(rid) == 16

    def test_cors_preflight_allowed(self, api):
        resp = api.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5180",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") in (
            "http://localhost:5180",
            "*",
        )


class TestErrorEnvelope:
    def test_404_uses_unified_envelope(self, api):
        resp = api.get("/api/not-exists")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "request_id" in body

    def test_business_error_envelope(self):
        app: FastAPI = create_app()

        @app.get("/__test/boom")
        async def boom():
            raise NotFoundError("会话不存在")

        client = TestClient(app)
        resp = client.get("/__test/boom")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert body["message"] == "会话不存在"

    def test_validation_error_envelope(self):
        app: FastAPI = create_app()

        class Payload(BaseModel):
            age: int

        @app.post("/__test/validate")
        async def validate(payload: Payload):
            return {"ok": True}

        client = TestClient(app)
        resp = client.post("/__test/validate", json={"age": "不是数字"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_failed"
        # 未显式声明 Body 时 FastAPI 会把它当 query 参数，loc 里包含字段名即可
        assert "payload" in body["detail"][0]["field"]

    def test_unhandled_exception_returns_500_envelope(self):
        app: FastAPI = create_app()

        @app.get("/__test/crash")
        async def crash():
            raise RuntimeError("模拟数据库炸了")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/__test/crash")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "internal_error"
        # 不把内部堆栈/异常原文回传给前端
        assert "模拟数据库炸了" not in body["message"]
