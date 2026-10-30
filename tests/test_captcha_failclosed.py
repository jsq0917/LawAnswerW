"""验证码在 Redis 故障时的行为（fail-open / fail-closed）与生产配置守卫。

2026-10-27 的事故：Redis 因卷属主错配变成只读，``captcha.setex`` 全部失败，
出题接口 502、登录注册全挂。当时还发现另一侧的不对称：**校验**侧遇到 Redis
异常是 ``return True``（直接放行），也就是说缓存一抖，验证码这道防线就消失了。
本文件锁住修复后的语义：

* ``CAPTCHA_FAIL_OPEN=true``（dev 默认）→ 放行，本地没起 Redis 也能登录；
* ``CAPTCHA_FAIL_OPEN=false``（prod 强制）→ 抛 ``UpstreamError``，绝不放行；
* ``APP_ENV=prod`` 且 ``CAPTCHA_FAIL_OPEN=true`` → 启动即失败。
"""

from __future__ import annotations

import pytest

from app.core import captcha as mod
from app.core.captcha import new_captcha, verify_captcha
from app.core.errors import UpstreamError
from config import ConfigError


class BrokenRedis:
    """连接成功但读写失败（模拟 MISCONF：只读副本）。"""

    def get(self, key):
        raise RuntimeError("MISCONF Redis is configured to save RDB snapshots")

    def delete(self, key):
        raise RuntimeError("MISCONF")

    def setex(self, key, ttl, value):
        raise RuntimeError("MISCONF")


class OkRedis:
    def __init__(self, value: str = "1234") -> None:
        self.store = {"cap:cid": value}

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)

    def setex(self, key, ttl, value):
        self.store[key] = value


# ---------------------------------------------------------------------------
# 校验侧：Redis 不可用
# ---------------------------------------------------------------------------
def test_fail_open_true_passes_when_redis_missing(monkeypatch, settings_factory):
    monkeypatch.setattr(mod, "_redis", lambda *a, **k: None)
    s = settings_factory(captcha_fail_open=True)
    assert verify_captcha("cid", "1234", settings=s) is True


def test_fail_closed_raises_when_redis_missing(monkeypatch, settings_factory):
    monkeypatch.setattr(mod, "_redis", lambda *a, **k: None)
    s = settings_factory(captcha_fail_open=False)
    with pytest.raises(UpstreamError) as ei:
        verify_captcha("cid", "1234", settings=s)
    assert "验证码" in str(ei.value)


def test_fail_closed_raises_on_redis_write_error(monkeypatch, settings_factory):
    """MISCONF 这类"连得上但读写失败"也要按不可用处理，不能悄悄放行。"""
    monkeypatch.setattr(mod, "_redis", lambda *a, **k: BrokenRedis())
    s = settings_factory(captcha_fail_open=False)
    with pytest.raises(UpstreamError):
        verify_captcha("cid", "1234", settings=s)


def test_fail_open_still_passes_on_redis_error(monkeypatch, settings_factory):
    monkeypatch.setattr(mod, "_redis", lambda *a, **k: BrokenRedis())
    s = settings_factory(captcha_fail_open=True)
    assert verify_captcha("cid", "1234", settings=s) is True


# ---------------------------------------------------------------------------
# 校验侧：Redis 正常时两种配置行为一致
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fail_open", [True, False])
def test_normal_path_unaffected_by_flag(monkeypatch, settings_factory, fail_open):
    fake = OkRedis("1234")
    monkeypatch.setattr(mod, "_redis", lambda *a, **k: fake)
    s = settings_factory(captcha_fail_open=fail_open)
    assert verify_captcha("cid", "1234", settings=s) is True
    assert verify_captcha("cid", "1234", settings=s) is False  # 一次性


# ---------------------------------------------------------------------------
# 出题侧：无论开关如何都必须报错（没有存储就无法校验）
# ---------------------------------------------------------------------------
def test_new_captcha_always_raises_without_redis(monkeypatch, settings_factory):
    monkeypatch.setattr(mod, "_redis", lambda *a, **k: None)
    for flag in (True, False):
        with pytest.raises(UpstreamError):
            new_captcha(settings=settings_factory(captcha_fail_open=flag))


def test_new_captcha_raises_on_write_error(monkeypatch, settings_factory):
    monkeypatch.setattr(mod, "_redis", lambda *a, **k: BrokenRedis())
    with pytest.raises(UpstreamError):
        new_captcha(settings=settings_factory(captcha_fail_open=False))


# ---------------------------------------------------------------------------
# 启动守卫
# ---------------------------------------------------------------------------
def test_prod_rejects_fail_open(settings_factory):
    with pytest.raises(ConfigError) as ei:
        settings_factory(app_env="prod", jwt_secret="k" * 48, captcha_fail_open=True)
    assert "CAPTCHA_FAIL_OPEN" in str(ei.value)


def test_prod_accepts_fail_closed(settings_factory):
    s = settings_factory(app_env="prod", jwt_secret="k" * 48, captcha_fail_open=False)
    assert s.captcha_fail_open is False


def test_dev_defaults_to_fail_open(settings_factory):
    assert settings_factory().captcha_fail_open is True
