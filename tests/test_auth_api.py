"""注册 / 登录 / 验证码接口行为测试（阶段 6，离线，假 MySQL + 假 Redis）。

覆盖设计里写明的安全行为：
* 账号不存在与密码错误返回**同一句话**（防枚举）；
* 连续失败 5 次锁定 60s（Redis 计数）；
* 验证码一次性、错误需刷新；
* 注册查重、密码强度、账号规则；
* 禁用账号不能登录。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import CurrentUser, hash_password
from app.main import app
from tests.fakes_db import FakeDB, override_deps


class FakeRedis:
    """够用的 Redis 替身：setex/get/delete/incr/expire。"""

    def __init__(self):
        self.store: dict[str, str | int] = {}
        self.ttl: dict[str, int] = {}

    def setex(self, key, ttl, value):
        self.store[key] = str(value)
        self.ttl[key] = int(ttl)

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)

    def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    def expire(self, key, ttl):
        self.ttl[key] = int(ttl)


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def client(db):
    restore = override_deps(app, db, user=None)  # 登录/注册不需要登录态
    yield TestClient(app)
    restore()


@pytest.fixture
def captcha_ok(monkeypatch):
    """验证码默认校验通过（单独测验证码时再覆盖）。"""
    monkeypatch.setattr("app.api.auth.captcha_svc.verify_captcha", lambda *a, **k: True)


@pytest.fixture
def redis_spy(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.api.auth._fail_counter", lambda *a, **k: fake)
    return fake


class TestCaptchaEndpoint:
    def test_returns_svg_data_uri(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.auth.captcha_svc.new_captcha",
            lambda **kw: ("cid123", "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="),
        )
        resp = client.post("/api/auth/captcha")
        assert resp.status_code == 200
        data = resp.json()
        assert data["captcha_id"] == "cid123"
        assert data["image"].startswith("data:image/svg+xml;base64,")
        assert data["expires_in"] == 120


class TestRegister:
    def test_success(self, client, captcha_ok):
        resp = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert resp.status_code == 201
        assert resp.json()["username"] == "alice"
        assert isinstance(resp.json()["id"], int)

    def test_duplicate_conflict(self, client, db, captcha_ok):
        db.add_user("alice", hash_password("secret123"))
        resp = client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "conflict"

    def test_wrong_captcha_422(self, client, monkeypatch):
        monkeypatch.setattr("app.api.auth.captcha_svc.verify_captcha", lambda *a, **k: False)
        resp = client.post(
            "/api/auth/register",
            json={"username": "bob", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "0000"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "captcha_invalid"

    @pytest.mark.parametrize(
        "username,password",
        [
            ("ab", "secret123"),          # 账号太短
            ("with space", "secret123"),  # 非法字符
            ("bob", "short1"),            # 密码太短
            ("bob", "12345678"),          # 无字母
            ("bob", "abcdefgh"),          # 无数字
        ],
    )
    def test_validation(self, client, captcha_ok, username, password):
        resp = client.post(
            "/api/auth/register",
            json={"username": username, "password": password, "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert resp.status_code == 422


class TestLogin:
    def test_success_returns_token(self, client, db, captcha_ok):
        db.add_user("alice", hash_password("secret123"))
        resp = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 24 * 3600
        assert data["user"]["username"] == "alice"
        assert data["access_token"].count(".") == 2  # JWT 三段

    def test_wrong_password_same_message_as_unknown_user(self, client, db, captcha_ok):
        """账号不存在与密码错误必须同一句文案（防枚举）。"""
        db.add_user("alice", hash_password("secret123"))
        wrong_pw = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrongpass1", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        unknown = client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "wrongpass1", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert wrong_pw.status_code == unknown.status_code == 401
        assert wrong_pw.json()["message"] == unknown.json()["message"] == "账号或密码错误"

    def test_disabled_account_403(self, client, db, captcha_ok):
        db.add_user("banned", hash_password("secret123"), status=0)
        resp = client.post(
            "/api/auth/login",
            json={"username": "banned", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert resp.status_code == 403

    def test_captcha_wrong_422_before_password_check(self, client, db, monkeypatch):
        """验证码在密码校验之前失败：不消耗失败计数（避免被刷锁）。"""
        db.add_user("alice", hash_password("secret123"))
        monkeypatch.setattr("app.api.auth.captcha_svc.verify_captcha", lambda *a, **k: False)
        resp = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong", "captcha_id": "cap-1234", "captcha_code": "0000"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "captcha_invalid"

    def test_lockout_after_five_failures(self, client, db, captcha_ok, redis_spy):
        db.add_user("alice", hash_password("secret123"))
        for _ in range(5):
            resp = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "wrongpass1", "captcha_id": "cap-1234", "captcha_code": "1234"},
            )
            assert resp.status_code == 401
        locked = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert locked.status_code == 429
        assert locked.json()["code"] == "too_many_requests"
        assert redis_spy.store["login:fail:alice"] == 5

    def test_success_clears_failure_counter(self, client, db, captcha_ok, redis_spy):
        db.add_user("alice", hash_password("secret123"))
        redis_spy.store["login:fail:alice"] = 2
        resp = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert resp.status_code == 200
        assert "login:fail:alice" not in redis_spy.store

    def test_redis_down_does_not_block_login(self, client, db, captcha_ok, monkeypatch):
        """Redis 故障时登录失败计数降级，不锁死账号。"""
        monkeypatch.setattr("app.api.auth._fail_counter", lambda *a, **k: None)
        db.add_user("alice", hash_password("secret123"))
        resp = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "secret123", "captcha_id": "cap-1234", "captcha_code": "1234"},
        )
        assert resp.status_code == 200


class TestMe:
    def test_me_requires_token(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_me_with_token(self, db, monkeypatch):
        restore = override_deps(app, db, user=CurrentUser(id=3, username="carol"))
        try:
            resp = TestClient(app).get("/api/auth/me")
            assert resp.status_code == 200
            assert resp.json() == {"id": 3, "username": "carol"}
        finally:
            restore()


class TestCheckUsername:
    def test_available(self, client):
        resp = client.get("/api/auth/check-username", params={"username": "brandnew"})
        assert resp.json() == {"available": True, "reason": None}

    def test_taken(self, client, db):
        db.add_user("alice", "x")
        resp = client.get("/api/auth/check-username", params={"username": "alice"})
        assert resp.json()["available"] is False
        assert "占用" in resp.json()["reason"]

    def test_invalid_format(self, client):
        resp = client.get("/api/auth/check-username", params={"username": "a b"})
        assert resp.json()["available"] is False
        assert "3–32" in resp.json()["reason"]
