"""Milvus 客户端（懒加载单例）与健康检查。"""

from __future__ import annotations

import time
from functools import lru_cache

from pymilvus import MilvusClient

from config import Settings, get_settings


@lru_cache(maxsize=2)
def _build_client(uri: str, timeout: int) -> MilvusClient:
    return MilvusClient(uri=uri, timeout=timeout)


def get_milvus_client(settings: Settings | None = None) -> MilvusClient:
    """首次调用才真正建连（构造即连接，故必须懒加载，避免 import 阶段阻塞）。"""
    s = settings or get_settings()
    return _build_client(s.milvus.uri, s.milvus.timeout)


def get_milvus_db_client(settings: Settings | None = None) -> MilvusClient:
    """切到本项目 database 的客户端。"""
    s = settings or get_settings()
    client = get_milvus_client(s)
    if s.milvus.database and s.milvus.database != "default":
        client.using_database(s.milvus.database)
    return client


def check_milvus(settings: Settings | None = None) -> dict:
    started = time.perf_counter()
    try:
        s = settings or get_settings()
        client = get_milvus_client(s)
        databases = client.list_databases()
        collections: list[str] = []
        if s.milvus.database in databases:
            client.using_database(s.milvus.database)
            collections = client.list_collections()
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"db={s.milvus.database} collections={len(collections)}",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "down",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": None,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


def close_milvus() -> None:
    try:
        get_milvus_client().close()
    except Exception:  # noqa: BLE001
        pass
    _build_client.cache_clear()
