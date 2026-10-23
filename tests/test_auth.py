"""认证与安全原语测试（阶段 6，全离线）。

只测纯逻辑：bcrypt / JWT / ULID / 密码与账号规则 / 验证码 SVG 渲染。
需要 MySQL/Redis 的端点行为在 test_auth_api.py（monkeypatch）与
test_auth_sessions_integration.py（真依赖）里覆盖。
"""

from __future__ import annotations

import time

import pytest

from app.core.captcha import CAPTCHA_LEN, render_svg, verify_captcha
from app.core.ids import new_ulid, ulid_timestamp_ms
from app.core.security import (
    BCRYPT_ROUNDS,
    create_access_token,
    decode_access_token,
    dummy_verify,
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)
from config import ConfigError


class TestPassword:
    def test_hash_and_verify(self):
        h = hash_password("secret123")
        assert h != "secret123"
        assert h.startswith("$2b$") or h.startswith("$2a$")
        assert verify_password("secret123", h)
        assert not verify_password("wrong-pass1", h)

    def test_salt_is_random(self):
        assert hash_password("secret123") != hash_password("secret123")

    def test_cost_factor(self):
        """bcrypt cost 必须是 12（§2.1），从哈希串解析验证。"""
        h = hash_password("secret123")
        assert int(h.split("$")[2]) == BCRYPT_ROUNDS == 12

    def test_corrupt_hash_returns_false(self):
        assert not verify_password("secret123", "not-a-bcrypt-hash")

    def test_dummy_verify_does_not_raise(self):
        dummy_verify()

    @pytest.mark.parametrize("password", ["short1", "12345678", "abcdefgh", "a1"])
    def test_weak_passwords_rejected(self, password):
        with pytest.raises(Exception) as exc:
            validate_password(password)
        assert "密码" in str(exc.value)

    def test_strong_password_ok(self):
        validate_password("secret123")
        validate_password("A1" + "x" * 30)


class TestUsername:
    @pytest.mark.parametrize("name", ["abc", "a_b_1", "A" * 32, "user123"])
    def test_valid(self, name):
        assert validate_username(name) == name

    @pytest.mark.parametrize("name", ["ab", "a" * 33, "中文账号", "with space", "emoji😀", ""])
    def test_invalid(self, name):
        with pytest.raises(Exception):
            validate_username(name)

    def test_trimmed(self):
        assert validate_username("  abc  ") == "abc"


class TestJwt:
    def test_roundtrip(self, settings_factory):
        s = settings_factory()
        token = create_access_token(7, "alice", settings=s)
        payload = decode_access_token(token, settings=s)
        assert payload["sub"] == "7"
        assert payload["username"] == "alice"
        assert payload["exp"] > payload["iat"]

    def test_expired(self, settings_factory):
        s = settings_factory()
        token = create_access_token(1, "bob", settings=s, expires_hours=-1)
        with pytest.raises(Exception) as exc:
            decode_access_token(token, settings=s)
        assert "过期" in str(exc.value)

    def test_tampered_token(self, settings_factory):
        s = settings_factory()
        token = create_access_token(1, "bob", settings=s)
        broken = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
        with pytest.raises(Exception):
            decode_access_token(broken, settings=s)

    def test_wrong_secret(self, settings_factory):
        s1 = settings_factory()
        s2 = settings_factory(jwt_secret="another-secret-key-for-testing-only-1234")
        token = create_access_token(1, "bob", settings=s1)
        with pytest.raises(Exception):
            decode_access_token(token, settings=s2)

    def test_missing_sub_rejected(self, settings_factory):
        import jwt

        s = settings_factory()
        token = jwt.encode({"username": "x", "exp": int(time.time()) + 60}, s.jwt_secret, algorithm=s.jwt_algorithm)
        with pytest.raises(Exception) as exc:
            decode_access_token(token, settings=s)
        assert "用户标识" in str(exc.value)

    def test_expire_hours_from_settings(self, settings_factory):
        s = settings_factory(jwt_expire_hours=2)
        payload = decode_access_token(create_access_token(1, "x", settings=s), settings=s)
        assert payload["exp"] - payload["iat"] == 2 * 3600


class TestUlid:
    def test_length_and_charset(self):
        value = new_ulid()
        assert len(value) == 26
        assert all(ch in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for ch in value)

    def test_time_ordered(self):
        """同一毫秒内随机，但时间戳前缀必须单调（列表排序依赖它）。"""
        assert ulid_timestamp_ms(new_ulid(1_700_000_000_000)) == 1_700_000_000_000
        a = new_ulid(1_700_000_000_000)
        b = new_ulid(1_700_000_001_000)
        assert a < b  # 字典序 = 时间序

    def test_unique(self):
        values = {new_ulid() for _ in range(200)}
        assert len(values) == 200

    def test_invalid_rejected(self):
        with pytest.raises(ValueError):
            ulid_timestamp_ms("SHORT")


class TestCaptchaSvg:
    def test_svg_contains_digits(self):
        svg = render_svg("1234", seed=42)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        for ch in "1234":
            assert f">{ch}</text>" in svg

    def test_svg_has_noise(self):
        """干扰线（path）与噪点（circle）必须存在，否则验证码形同虚设。"""
        svg = render_svg("0000", seed=1)
        assert "<path" in svg
        assert "<circle" in svg

    def test_render_is_deterministic_with_seed(self):
        assert render_svg("5678", seed=7) == render_svg("5678", seed=7)

    def test_code_length_contract(self):
        assert CAPTCHA_LEN == 4

    def test_verify_degrades_when_redis_down(self, monkeypatch):
        """Redis 不可用时校验降级放行（避免缓存故障锁死登录）。"""
        import app.core.captcha as mod

        monkeypatch.setattr(mod, "_redis", lambda *a, **k: None)
        assert verify_captcha("cid", "1234") is True

    def test_verify_one_time_consume(self, monkeypatch):
        """校验即删除：同一张图不能重复使用（防暴力试错）。"""

        class FakeRedis:
            def __init__(self):
                self.store = {"cap:abc": "1234"}

            def get(self, key):
                return self.store.get(key)

            def delete(self, key):
                self.store.pop(key, None)

        fake = FakeRedis()
        import app.core.captcha as mod

        monkeypatch.setattr(mod, "_redis", lambda *a, **k: fake)
        assert verify_captcha("abc", "1234") is True
        assert verify_captcha("abc", "1234") is False  # 已消费

    def test_verify_wrong_code(self, monkeypatch):
        class FakeRedis:
            def get(self, key):
                return b"9876"

            def delete(self, key):
                pass

        import app.core.captcha as mod

        monkeypatch.setattr(mod, "_redis", lambda *a, **k: FakeRedis())
        assert verify_captcha("abc", "1234") is False


class TestConfigGuards:
    def test_settings_has_auth_fields(self, settings_factory):
        s = settings_factory()
        assert s.captcha_ttl_s == 120
        assert s.login_fail_limit == 5
        assert s.login_fail_window_s == 60
