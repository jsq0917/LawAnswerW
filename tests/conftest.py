"""pytest 公共夹具。

原则：单元测试**全离线** —— 不加载真实模型、不调 DashScope、不连 MySQL/Milvus。
需要真实依赖的用例标记 ``@pytest.mark.integration``，默认跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.logger import shutdown_logging  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_logging():
    """每个用例结束后移除本模块安装的 handler，避免用例之间互相污染。"""
    yield
    shutdown_logging()


@pytest.fixture
def client():
    """不带 lifespan 的 TestClient：不起日志、不建目录、不连依赖。"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def settings_factory():
    """构造不读真实 .env 的 Settings（避免测试受本机配置影响）。"""
    from config import Settings

    def _make(**kwargs):
        kwargs.setdefault("_env_file", None)
        return Settings(**kwargs)

    return _make
