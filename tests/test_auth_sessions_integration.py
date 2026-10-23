"""阶段 6 联调：真实 MySQL + Redis 跑通「注册 → 登录 → 建会话 → 问答落库 → 改名 → 删除」。

    RUN_INTEGRATION=1 uv run python -m pytest tests/test_auth_sessions_integration.py -v -m integration

问答用标准问法走纯结构化回放（不需要 DASHSCOPE_API_KEY），
落库、归属校验、软删全部走真实 SQL。
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from app.core.sse import parse_sse_frames

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1",
        reason="需要真实 MySQL + Redis，设置 RUN_INTEGRATION=1 启用",
    ),
]


@pytest.fixture(scope="module")
def credentials():
    username = f"it_{uuid.uuid4().hex[:10]}"
    return {"username": username, "password": "secret123"}


def _read_captcha(captcha_id: str) -> str:
    """联调时直接从 Redis 取验证码答案（生产不可这样做）。"""
    from app.db.redis_client import get_redis

    value = get_redis().get(f"cap:{captcha_id}")
    return value.decode() if isinstance(value, bytes) else str(value)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


class TestAuthFlowReal:
    def test_captcha_from_redis(self, client):
        resp = client.post("/api/auth/captcha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["image"].startswith("data:image/svg+xml;base64,")
        code = _read_captcha(data["captcha_id"])
        assert len(code) == 4 and code.isdigit()

    def test_register_then_login(self, client, credentials):
        cap = client.post("/api/auth/captcha").json()
        code = _read_captcha(cap["captcha_id"])
        resp = client.post(
            "/api/auth/register",
            json={
                "username": credentials["username"],
                "password": credentials["password"],
                "captcha_id": cap["captcha_id"],
                "captcha_code": code,
            },
        )
        assert resp.status_code == 201, resp.text

        cap2 = client.post("/api/auth/captcha").json()
        code2 = _read_captcha(cap2["captcha_id"])
        login = client.post(
            "/api/auth/login",
            json={
                "username": credentials["username"],
                "password": credentials["password"],
                "captcha_id": cap2["captcha_id"],
                "captcha_code": code2,
            },
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == credentials["username"]
        credentials["token"] = token

    def test_login_wrong_password_real(self, client, credentials):
        cap = client.post("/api/auth/captcha").json()
        code = _read_captcha(cap["captcha_id"])
        resp = client.post(
            "/api/auth/login",
            json={
                "username": credentials["username"],
                "password": "wrongpass1",
                "captcha_id": cap["captcha_id"],
                "captcha_code": code,
            },
        )
        assert resp.status_code == 401
        assert resp.json()["message"] == "账号或密码错误"


class TestSessionFlowReal:
    @pytest.fixture(autouse=True)
    def _auth(self, credentials):
        if "token" not in credentials:
            pytest.skip("登录未成功，跳过会话链路")

    def _headers(self, credentials):
        return {"Authorization": f"Bearer {credentials['token']}"}

    def test_full_flow(self, client, credentials):
        headers = self._headers(credentials)

        created = client.post("/api/sessions", json={"title": "联调会话"}, headers=headers)
        assert created.status_code == 201, created.text
        sid = created.json()["id"]
        assert len(sid) == 26

        listed = client.get("/api/sessions", headers=headers).json()
        assert any(s["id"] == sid for s in listed)

        # 问答：标准问法 → 纯结构化回放（不需要 LLM key），真实落库
        resp = client.post(
            "/api/chat/stream",
            json={"query": "试用期最长可以约定多久？", "kb_ids": ["kb_labor"], "session_id": sid},
            headers=headers,
        )
        assert resp.status_code == 200
        frames = parse_sse_frames(resp.text)
        names = [n for n, _ in frames]
        assert names[-1] == "done"
        assert "六个月" in "".join(d["text"] for n, d in frames if n == "delta")

        # 消息历史：user + assistant 两行，sources JSON 可解析
        msgs = client.get(f"/api/sessions/{sid}/messages", headers=headers).json()
        assert msgs["count"] == 2
        assert [m["role"] for m in msgs["items"]] == ["user", "assistant"]
        assistant = msgs["items"][1]
        assert assistant["route"] == "pure_structured"
        assert assistant["intent"] == "labor_law"
        assert isinstance(assistant["sources"], list) and assistant["sources"]
        assert assistant["ttft_ms"] is not None
        assert assistant["error"] is None

        # 改名 + 参数
        patched = client.patch(
            f"/api/sessions/{sid}",
            json={"title": "改名后", "temperature": 0.9, "history_rounds": 3},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "改名后"
        assert patched.json()["temperature"] == 0.9

        # 归属隔离：另一个用户看不到这个会话
        other = self._make_other_user(client)
        assert client.get(f"/api/sessions/{sid}/messages", headers=other).status_code == 404
        assert client.get("/api/sessions", headers=other).json() == []

        # 软删
        assert client.delete(f"/api/sessions/{sid}", headers=headers).status_code == 200
        assert all(s["id"] != sid for s in client.get("/api/sessions", headers=headers).json())

    def _make_other_user(self, client) -> dict:
        username = f"it_{uuid.uuid4().hex[:10]}"
        cap = client.post("/api/auth/captcha").json()
        client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "secret123",
                "captcha_id": cap["captcha_id"],
                "captcha_code": _read_captcha(cap["captcha_id"]),
            },
        )
        cap2 = client.post("/api/auth/captcha").json()
        token = client.post(
            "/api/auth/login",
            json={
                "username": username,
                "password": "secret123",
                "captcha_id": cap2["captcha_id"],
                "captcha_code": _read_captcha(cap2["captcha_id"]),
            },
        ).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


def teardown_module(module):
    """清理联调账号（会话与消息由外键级联删除）。"""
    from sqlalchemy import text

    from app.db.mysql import get_engine

    try:
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM users WHERE username LIKE 'it\\_%'"))
    except Exception:  # noqa: BLE001 - 清理失败不影响结论
        pass
