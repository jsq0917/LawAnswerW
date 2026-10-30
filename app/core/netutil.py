"""客户端 IP 解析（全项目唯一入口）。

为什么需要这一层
----------------
生产部署形态是 ``nginx(443) → uvicorn(127.0.0.1:8101)``。此时
``request.client.host`` **恒为 127.0.0.1**：

* 匿名限流（``rl:ip:{ip}``）会把全站访客当成同一个人 → 一个刷子就能让所有人 429；
* 登录失败计数（``login:fail:ip:{ip}``）同理，等于没有 IP 维度。

信任规则（防伪造）
------------------
只有当 TCP 对端本身是**可信代理**时，才读取代理下发的真实 IP 头；否则
一律用对端地址。这样公网请求自己带一个 ``X-Real-IP: 1.2.3.4`` 是无效的。

头优先级：

1. ``X-Real-IP``——nginx 用 ``proxy_set_header X-Real-IP $remote_addr`` 覆盖写入，
   客户端无法伪造（它自己的同名头会被 nginx 覆盖）。
2. ``X-Forwarded-For``——从右往左跳过可信代理，取第一个不可信地址
   （最左端可被客户端随意填写，不能直接取 ``[0]``）。
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

from config import Settings, get_settings

__all__ = ["client_ip", "is_trusted_proxy"]

_Net = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=16)
def _networks(raw: str) -> tuple[_Net, ...]:
    """把 ``trusted_proxies`` 解析成网段元组（按原始字符串缓存）。"""
    nets: list[_Net] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:  # 配错了就当没配，绝不因此放行伪造头
            continue
    return tuple(nets)


def _as_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def is_trusted_proxy(host: str, trusted_raw: str) -> bool:
    """``host`` 是否落在可信代理网段内。"""
    addr = _as_ip(host)
    if addr is None:
        return False
    ip = ipaddress.ip_address(addr)
    return any(ip in net for net in _networks(trusted_raw))


def client_ip(request: Request, settings: Settings | None = None) -> str:
    """解析真实客户端 IP；无法确定时返回 TCP 对端地址（可为 ``"unknown"``）。"""
    s = settings or get_settings()
    peer = request.client.host if request.client else ""

    trust = getattr(s, "trust_proxy", False)
    trusted_raw = getattr(s, "trusted_proxies", "127.0.0.1,::1")
    if not trust or not is_trusted_proxy(peer, trusted_raw):
        return peer or "unknown"

    real = request.headers.get("x-real-ip")
    if real:
        got = _as_ip(real)
        if got:
            return got

    xff = request.headers.get("x-forwarded-for")
    if xff:
        for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
            got = _as_ip(hop)
            if got and not is_trusted_proxy(got, trusted_raw):
                return got

    return peer or "unknown"
