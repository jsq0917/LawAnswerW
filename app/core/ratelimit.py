"""轻量限流：基于 Redis 固定窗口，防止 LLM 接口被刷爆费用。

策略：
* 匿名用户（无 token）按 IP 限流：20 次/分钟；
* 登录用户按 user_id 限流：60 次/分钟；
* Redis 不可用时**放行**（fail-open），不能因为缓存故障把问答链路搞挂。

key 设计：``rl:{identity}:{minute_bucket}``，INCR 与 EXPIRE 用 Lua 原子执行
（非原子的两步写入在并发首请求下可能产生无过期时间的死 key）。
"""

from __future__ import annotations

import time

from fastapi import Depends, Request

from app.core.errors import RateLimited
from app.core.logger import get_logger
from app.core.security import get_current_user
from app.db.redis_client import get_redis
from config import Settings, get_settings

logger = get_logger("lawanswer.ratelimit")

#: INCR + 首次 EXPIRE 的原子脚本：返回 [当前计数, 是否新桶]
_INCR_EXPIRE_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


def _client_ip(request: Request) -> str:
    """取客户端 IP。

    直接取 TCP 对端地址，**不信任 X-Forwarded-For**——本项目直连部署
    （无反向代理改写 XFF），信任 XFF 等于让客户端伪造任意 IP 绕过限流。
    若未来加了反向代理，应改为"可信代理链上取最后一跳"。
    """
    return request.client.host if request.client else "unknown"


def check_rate_limit(
    request: Request,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
) -> None:
    """检查当前请求是否超限。超限抛 :class:`RateLimited`。

    :param user_id: 登录用户 ID；None 表示匿名，按 IP 限流
    """
    _enforce(_client_ip(request), user_id, settings)


def rate_limit_dep(
    request: Request,
    user = Depends(get_current_user),
) -> None:
    """FastAPI 依赖版限流（挂路由级 dependencies 用，登录端点按 uid 计数）。"""
    _enforce(_client_ip(request), user.id, None)


def _enforce(client_ip: str, user_id: int | None, settings: Settings | None) -> None:
    s = settings or get_settings()
    if not s.rate_limit_enabled:
        return
    # 身份：登录用 uid，匿名用 IP
    identity = f"uid:{user_id}" if user_id else f"ip:{client_ip}"
    limit = s.rate_limit_authed if user_id else s.rate_limit_anonymous

    # 分钟桶：当前分钟数（UTC 秒级时间戳 / 60）
    bucket = int(time.time()) // 60
    key = f"rl:{identity}:{bucket}"

    try:
        redis = get_redis(s)
        count = int(redis.eval(_INCR_EXPIRE_LUA, 1, key, 120))
    except Exception as exc:  # noqa: BLE001 - Redis 故障时 fail-open
        logger.warning("限流检查 Redis 不可用，放行: %s", exc)
        return

    if count > limit:
        # 本桶剩余秒数作为 retry_after
        retry_after = 60 - (int(time.time()) % 60)
        logger.info("限流触发 identity=%s count=%d limit=%d", identity, count, limit)
        raise RateLimited(
            f"请求过于频繁，请 {retry_after} 秒后再试",
            detail={"retry_after_s": retry_after},
        )
