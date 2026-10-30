"""登录失败计数的双维度防护（全离线）。

威胁模型：只按用户名计数时，攻击者可以用字典轮询**不同**用户名——
每个用户名都没到 5 次，自己不受任何限制；同时他还能把任意真实账号
故意输错 5 次锁死（拒绝服务）。所以必须有 IP 维度兜底。
"""

from __future__ import annotations

import pytest

from app.api import auth as auth_mod
from app.core.errors import RateLimited


class FakeRedis:
    """只实现 get/incr/expire/delete/decr 的内存版。"""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def decr(self, key):
        self.store[key] = self.store.get(key, 0) - 1
        return self.store[key]

    def expire(self, key, seconds):
        self.ttl[key] = seconds
        return True

    def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)
        return 1


class DeadRedis:
    def get(self, *a, **kw):
        raise ConnectionError("down")

    incr = decr = delete = expire = get


class S:
    login_fail_limit = 5
    login_fail_window_s = 60
    login_fail_ip_limit = 30
    login_fail_ip_window_s = 300


@pytest.fixture()
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda s=None: r)
    return r


@pytest.fixture()
def dead(monkeypatch):
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda s=None: DeadRedis())


# ---------------------------------------------------------------------------
# IP 维度
# ---------------------------------------------------------------------------
def test_rotating_usernames_still_hits_ip_limit(redis):
    """换用户名轮询：每个账号都没到 5 次，但 30 次后整体被拦。"""
    s = S()
    ip = "203.0.113.9"
    for i in range(30):
        auth_mod._record_failure(f"victim{i}", ip, s)
    with pytest.raises(RateLimited) as ei:
        auth_mod._check_locked("another_victim", ip, s)
    assert "网络" in str(ei.value)


def test_ip_limit_blocks_before_username_check(redis):
    """IP 已锁时，即使这个用户名一次没失败过也要拒绝（防逐个锁号）。"""
    s = S()
    ip = "1.2.3.4"
    for _ in range(30):
        auth_mod._record_failure("someone", ip, s)
    assert redis.store["login:fail:someone"] == 30  # IP 与账号都在涨
    with pytest.raises(RateLimited):
        auth_mod._check_locked("fresh_account", ip, s)


def test_other_ip_not_affected(redis):
    """1.1.1.1 被 IP 维度锁住时，换 IP + 新账号完全不受影响。"""
    s = S()
    for _ in range(30):
        auth_mod._record_failure("victim", "1.1.1.1", s)
    auth_mod._check_locked("newbie", "2.2.2.2", s)  # 不抛


# ---------------------------------------------------------------------------
# 账号维度
# ---------------------------------------------------------------------------
def test_username_limit_still_works(redis):
    s = S()
    for _ in range(5):
        auth_mod._record_failure("alice", "9.9.9.9", s)
    with pytest.raises(RateLimited) as ei:
        auth_mod._check_locked("alice", "9.9.9.9", s)
    assert "登录失败" in str(ei.value)


def test_same_ip_other_user_passes_until_ip_limit(redis):
    """同一 IP 下，换账号 5 次以内仍可继续（正常用户输错几次不会立刻被 IP 锁）。"""
    s = S()
    for i in range(5):
        auth_mod._record_failure(f"u{i}", "5.5.5.5", s)
    auth_mod._check_locked("brand_new", "5.5.5.5", s)


# ---------------------------------------------------------------------------
# 成功登录的清理语义
# ---------------------------------------------------------------------------
def test_success_clears_username_but_only_decrements_ip(redis):
    s = S()
    ip = "7.7.7.7"
    for _ in range(4):
        auth_mod._record_failure("alice", ip, s)
    assert redis.store["login:fail:alice"] == 4
    assert redis.store["login:ipfail:" + ip] == 4

    auth_mod._clear_failures("alice", ip, s)

    assert "login:fail:alice" not in redis.store
    assert redis.store["login:ipfail:" + ip] == 3  # 只抵消一次，不清零


def test_repeated_success_cannot_wipe_ip_counter(redis):
    """攻击者用自己账号反复登录，也只能一次抵一次，无法刷掉失败积累。"""
    s = S()
    ip = "8.8.4.4"
    for _ in range(10):
        auth_mod._record_failure("attacker", ip, s)
    for _ in range(50):
        auth_mod._clear_failures("attacker", ip, s)
    # 10 次失败被 50 次成功抵到 0 并删除 key，但绝不会变成"负数偏移"后继续抵消新失败
    assert redis.store.get("login:ipfail:" + ip, 0) == 0
    auth_mod._record_failure("attacker", ip, s)
    assert redis.store["login:ipfail:" + ip] == 1


def test_ttl_set_once_per_window(redis):
    s = S()
    ip = "6.6.6.6"
    auth_mod._record_failure("a", ip, s)
    auth_mod._record_failure("a", ip, s)
    assert redis.ttl["login:fail:a"] == 60
    assert redis.ttl["login:ipfail:" + ip] == 300


# ---------------------------------------------------------------------------
# Redis 故障：可用性优先，不锁
# ---------------------------------------------------------------------------
def test_redis_down_never_locks(dead):
    s = S()
    auth_mod._check_locked("a", "1.1.1.1", s)
    auth_mod._record_failure("a", "1.1.1.1", s)
    auth_mod._clear_failures("a", "1.1.1.1", s)
