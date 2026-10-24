"""限流与 Milvus 过滤表达式安全的单元测试（全离线）。

ratelimit 用实现 INCR/EXPIRE 语义的 FakeRedis（模拟 Lua 脚本效果）；
kb_id 过滤器重点测表达式注入面。
"""

from __future__ import annotations

import time

import pytest

from app.core import ratelimit as rl
from app.core.errors import RateLimited
from app.retrieval.hybrid import build_kb_filter


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------
class LuaFakeRedis:
    """用 Python 复刻 INCR+首记 EXPIRE 的 Lua 语义。"""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    def eval(self, script: str, numkeys: int, key: str, expire: int) -> int:
        assert "INCR" in script and "EXPIRE" in script
        self.store[key] = self.store.get(key, 0) + 1
        if self.store[key] == 1:
            self.ttl[key] = expire
        return self.store[key]


class DeadRedis:
    """模拟 Redis 不可用。"""

    def eval(self, *a, **kw):
        raise ConnectionError("down")


class _Settings:
    rate_limit_enabled = True
    rate_limit_anonymous = 3
    rate_limit_authed = 10


@pytest.fixture()
def wired(monkeypatch):
    fake = LuaFakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda s=None: fake)
    return fake


class TestRateLimit:
    def test_blocks_after_limit(self, wired):
        for i in range(3):
            rl._enforce("1.2.3.4", None, _Settings())  # 前 3 次放行
        with pytest.raises(RateLimited) as ei:
            rl._enforce("1.2.3.4", None, _Settings())
        assert ei.value.status_code == 429

    def test_different_ip_isolated(self, wired):
        for _ in range(3):
            rl._enforce("1.1.1.1", None, _Settings())
        rl._enforce("2.2.2.2", None, _Settings())  # 别的 IP 不受影响

    def test_uid_bypasses_ip_bucket(self, wired):
        for _ in range(3):
            rl._enforce("1.1.1.1", None, _Settings())
        rl._enforce("1.1.1.1", 42, _Settings())  # 同 IP 但登录了 → uid 桶

    def test_fail_open_on_redis_error(self, monkeypatch):
        monkeypatch.setattr(rl, "get_redis", lambda s=None: DeadRedis())
        for _ in range(10):  # 一直放行，不抛 429
            rl._enforce("1.1.1.1", None, _Settings())

    def test_disabled_flag(self, wired, monkeypatch):
        s = _Settings()
        s.rate_limit_enabled = False
        for _ in range(10):
            rl._enforce("1.1.1.1", None, s)  # 关闭时限流不生效
        assert wired.store == {}

    def test_expire_set_on_first_hit(self, wired):
        rl._enforce("1.1.1.1", None, _Settings())
        assert wired.ttl == {f"rl:ip:1.1.1.1:{int(time.time()) // 60}": 120}


# ---------------------------------------------------------------------------
# kb_id 过滤表达式
# ---------------------------------------------------------------------------
class TestKbFilter:
    def test_valid_ids(self):
        assert build_kb_filter(["kb_labor", "kb_civil"]) == 'kb_id in ["kb_labor", "kb_civil"]'

    def test_empty(self):
        assert build_kb_filter(None) == ""
        assert build_kb_filter([]) == ""

    def test_injection_dropped(self):
        """带引号的 id 必须被整条剔除，不能拼进表达式。"""
        out = build_kb_filter(['kb") or (1==1'])
        assert out == ""  # 全部非法 → 不过滤（返回空 = 全库检索）

    def test_mixed_valid_and_injection(self):
        out = build_kb_filter(["kb_labor", 'x" or "1'])
        assert out == 'kb_id in ["kb_labor"]'

    def test_quotes_and_spaces_rejected(self):
        assert build_kb_filter(['kb labor']) == ""
        assert build_kb_filter(['kb-labor']) == ""
