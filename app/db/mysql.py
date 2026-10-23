"""MySQL 连接与健康检查（SQLAlchemy 2.x + PyMySQL）。"""

from __future__ import annotations

import time
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import Settings, get_settings


#: 记录已创建的引擎，便于退出时显式关闭连接池
_ENGINES: list[Engine] = []


@lru_cache(maxsize=4)
def _build_engine(dsn: str, pool_size: int, connect_timeout: int) -> Engine:
    engine = create_engine(
        dsn,
        pool_pre_ping=True,          # 断线自动重连，容器重启后不用重启应用
        pool_size=pool_size,
        max_overflow=pool_size,
        pool_recycle=1800,
        future=True,
        connect_args={
            "connect_timeout": connect_timeout,
            "read_timeout": 30,
            "write_timeout": 30,
            "charset": "utf8mb4",
        },
    )
    _ENGINES.append(engine)
    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    s = settings or get_settings()
    return _build_engine(s.mysql.dsn, s.mysql.pool_size, s.mysql.connect_timeout)


@lru_cache(maxsize=4)
def _build_session_factory(dsn: str, pool_size: int, connect_timeout: int) -> sessionmaker[Session]:
    return sessionmaker(
        bind=_build_engine(dsn, pool_size, connect_timeout),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    s = settings or get_settings()
    return _build_session_factory(s.mysql.dsn, s.mysql.pool_size, s.mysql.connect_timeout)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级 Session，异常自动回滚。"""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/后台任务用的会话上下文。"""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_mysql(settings: Settings | None = None) -> dict:
    """健康检查：返回统一结构，异常不外抛。"""
    started = time.perf_counter()
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT VERSION()")).scalar_one()
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": str(version),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 健康检查必须吞异常
        return {
            "status": "down",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": None,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


def dispose_engines() -> None:
    """关闭连接池（应用退出时调用）。"""
    while _ENGINES:
        engine = _ENGINES.pop()
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001 - 退出清理不应抛错
            pass
    _build_engine.cache_clear()
    _build_session_factory.cache_clear()
