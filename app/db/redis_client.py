"""Redis 连接与健康检查（高频问答缓存、验证码、登录失败计数）。"""

from __future__ import annotations

import time
from functools import lru_cache

from redis import Redis

from config import Settings, get_settings


@lru_cache(maxsize=4)
def _build_client(url: str) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
        health_check_interval=30,
        retry_on_timeout=True,
    )


def get_redis(settings: Settings | None = None) -> Redis:
    s = settings or get_settings()
    return _build_client(s.redis.url)


def check_redis(settings: Settings | None = None) -> dict:
    started = time.perf_counter()
    try:
        client = get_redis(settings)
        client.ping()
        try:
            version = client.info("server").get("redis_version")
        except Exception:  # noqa: BLE001 - 版本号拿不到不影响可用性判断
            version = None
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"redis {version}" if version else "redis",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "down",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": None,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


def close_redis() -> None:
    _build_client.cache_clear()
