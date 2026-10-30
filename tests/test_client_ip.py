"""真实客户端 IP 解析的单元测试（全离线）。

背景：生产是 nginx → uvicorn(127.0.0.1)，`request.client.host` 恒为 127.0.0.1。
若不按 `trust_proxy` 读代理头，匿名限流会把全站当一个人；
若**无条件**信任代理头，客户端又能伪造 IP 绕过限流。两侧都要锁住。
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core.netutil import client_ip, is_trusted_proxy


def make_request(peer: str | None, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": raw,
        "client": (peer, 45678) if peer else None,
    }
    return Request(scope)


class _S:
    """最小配置桩：netutil 只读这两个字段。"""

    def __init__(self, trust: bool = True, trusted: str = "127.0.0.1,::1") -> None:
        self.trust_proxy = trust
        self.trusted_proxies = trusted


# ---------------------------------------------------------------------------
# 关闭信任时：永远是 TCP 对端
# ---------------------------------------------------------------------------
def test_trust_off_ignores_headers():
    req = make_request("127.0.0.1", {"X-Real-IP": "203.0.113.9"})
    assert client_ip(req, _S(trust=False)) == "127.0.0.1"


# ---------------------------------------------------------------------------
# 开启信任 + 对端可信：读代理头
# ---------------------------------------------------------------------------
def test_trusted_peer_reads_x_real_ip():
    req = make_request("127.0.0.1", {"X-Real-IP": "203.0.113.9"})
    assert client_ip(req, _S()) == "203.0.113.9"


def test_ipv6_x_real_ip_is_normalized():
    req = make_request("::1", {"X-Real-IP": "2001:db8::1"})
    assert client_ip(req, _S()) == "2001:db8::1"


def test_x_real_ip_wins_over_xff():
    req = make_request(
        "127.0.0.1",
        {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "198.51.100.7, 127.0.0.1"},
    )
    assert client_ip(req, _S()) == "203.0.113.9"


def test_xff_takes_first_untrusted_from_right():
    req = make_request(
        "127.0.0.1",
        {"X-Forwarded-For": "1.2.3.4, 10.0.0.5, 127.0.0.1"},
    )
    assert client_ip(req, _S(trusted="127.0.0.1,::1,10.0.0.0/8")) == "1.2.3.4"


def test_xff_all_trusted_falls_back_to_peer():
    req = make_request("127.0.0.1", {"X-Forwarded-For": "10.0.0.5, 127.0.0.1"})
    assert client_ip(req, _S(trusted="127.0.0.1,::1,10.0.0.0/8")) == "127.0.0.1"


# ---------------------------------------------------------------------------
# 防伪造：对端不可信时，头一律不看
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("header", ["X-Real-IP", "X-Forwarded-For"])
def test_untrusted_peer_cannot_spoof(header):
    req = make_request("8.8.8.8", {header: "203.0.113.9"})
    assert client_ip(req, _S()) == "8.8.8.8"


def test_garbage_x_real_ip_ignored():
    req = make_request("127.0.0.1", {"X-Real-IP": "not-an-ip"})
    assert client_ip(req, _S()) == "127.0.0.1"


def test_empty_x_real_ip_falls_through_to_xff():
    req = make_request("127.0.0.1", {"X-Real-IP": "", "X-Forwarded-For": "198.51.100.7"})
    assert client_ip(req, _S()) == "198.51.100.7"


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_no_client_returns_unknown():
    assert client_ip(make_request(None), _S()) == "unknown"


def test_cidr_trusted_proxy():
    assert is_trusted_proxy("10.1.2.3", "10.0.0.0/8") is True
    assert is_trusted_proxy("11.1.2.3", "10.0.0.0/8") is False
    assert is_trusted_proxy("bad", "10.0.0.0/8") is False


def test_broken_trusted_config_trusts_nothing():
    """配错网段时宁可不信任，也不能因为解析失败就放行所有头。"""
    req = make_request("127.0.0.1", {"X-Real-IP": "203.0.113.9"})
    assert client_ip(req, _S(trusted="not-a-cidr")) == "127.0.0.1"


# ---------------------------------------------------------------------------
# 限流是否真的按解析出的 IP 分桶
# ---------------------------------------------------------------------------
def test_rate_limit_separates_clients_behind_proxy(monkeypatch):
    from app.core import ratelimit as rl

    seen: list[str] = []

    class CountingRedis:
        def eval(self, script: str, numkeys: int, key: str, expire: int) -> int:
            seen.append(key)
            return 1

    class S(_S):
        rate_limit_enabled = True
        rate_limit_anonymous = 20
        rate_limit_authed = 60

    monkeypatch.setattr(rl, "get_redis", lambda s=None: CountingRedis())
    s = S()
    rl.check_rate_limit(make_request("127.0.0.1", {"X-Real-IP": "203.0.113.9"}), settings=s)
    rl.check_rate_limit(make_request("127.0.0.1", {"X-Real-IP": "198.51.100.7"}), settings=s)

    assert len(seen) == 2
    assert "ip:203.0.113.9" in seen[0]
    assert "ip:198.51.100.7" in seen[1]
    assert seen[0] != seen[1]
