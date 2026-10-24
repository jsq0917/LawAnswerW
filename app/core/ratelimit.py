"""轻量限流：基于 Redis 固定窗口，防止 LLM 接口被刷爆费用。

策略：
* 匿名用户（无 token）按 IP 限流：20 次/分钟；
* 登录用户按 user_id 限流：60 次/分钟；
* Redis 不可用时**放行**（fail-open），不能因为缓存故障把问答链路搞挂。

key 设计：``rl:{identity}:{minute_bucket}``，INCR 后首次设 EXPIRE=120s（留余量）。
"""

from __future__ import annotations

import time

from fastapi import Request

from app.core.errors import RateLimited
from app.core.logger import get_logger
from app.db.redis_client import get_redis
from config import Settings, get_settings

logger = get_logger("lawanswer.ratelimit")


def _client_ip(request: Request) -> str:
    """取客户端真实 IP（优先 X-Forwarded-For 第一段）。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(
    request: Request,
    *,
    user_id: int | None = None,
    settings: Settings | None = None,
) -> None:
    """检查当前请求是否超限。超限抛 :class:`RateLimitError`。

    :param user_id: 登录用户 ID；None 表示匿名，按 IP 限流
    """
    s = settings or get_settings()
    # 身份：登录用 uid，匿名用 IP
    identity = f"uid:{user_id}" if user_id else f"ip:{_client_ip(request)}"
    limit = s.rate_limit_authed if user_id else s.rate_limit_anonymous

    # 分钟桶：当前分钟数（UTC 秒级时间戳 / 60）
    bucket = int(time.time()) // 60
    key = f"rl:{identity}:{bucket}"

    try:
        redis = get_redis(s)
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, 120)  # 窗口 + 1 分钟余量
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
