"""日志：控制台 + 文件双写。

设计要点
--------
* **双写**：同一个格式化器同时挂到控制台与文件，保证两边内容一致、便于对照排查。
* **双文件**：``app.log``（全量，滚动）+ ``error.log``（只收 ERROR 及以上），
  排查时不用在几万行里 grep。
* **request_id**：通过 ``contextvars`` 注入，每条日志自动带上请求 ID，
  一个用户请求跨多个模块（编排/检索/生成）的日志可以串起来。
* **幂等**：重复调用 :func:`setup_logging` 不会叠加 handler（否则日志会翻倍）。
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: 当前请求 ID；未处于请求上下文时为 "-"
REQUEST_ID: ContextVar[str] = ContextVar("lawanswer_request_id", default="-")

#: 本模块安装的 handler 打上这个标记，便于识别与清理
_HANDLER_MARK = "_lawanswer_handler"

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(request_id)s | %(name)s "
    "%(filename)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 这些 logger 由 uvicorn 自带 handler，接管后统一走本模块格式
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@dataclass(frozen=True)
class LogFiles:
    app: Path
    error: Path


class RequestIdFilter(logging.Filter):
    """把 contextvar 里的请求 ID 填进 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = REQUEST_ID.get()
        return True


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(value: str | None = None) -> str:
    """设置当前上下文的请求 ID，返回最终使用的值。"""
    rid = value or new_request_id()
    REQUEST_ID.set(rid)
    return rid


def reset_request_id(token) -> None:
    """与 :func:`set_request_id` 配对使用（contextvars token）。"""
    try:
        REQUEST_ID.reset(token)
    except (ValueError, RuntimeError, TypeError):  # 跨上下文/非法 token：忽略，不影响请求主流程
        REQUEST_ID.set("-")


def current_request_id() -> str:
    return REQUEST_ID.get()


def _utf8_console_stream():
    """Windows 控制台默认 GBK，中文日志会乱码或抛 UnicodeEncodeError。"""
    stream = sys.stdout
    try:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass
    return stream


def _remove_own_handlers(logger: logging.Logger) -> None:
    for h in list(logger.handlers):
        if getattr(h, _HANDLER_MARK, False):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001 - 关闭失败不应中断启动
                pass


def setup_logging(
    log_dir: str | Path = "logs",
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
) -> LogFiles:
    """初始化全局日志，返回日志文件路径（便于测试与启动日志打印）。"""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    app_file = log_path / "app.log"
    error_file = log_path / "error.log"

    root = logging.getLogger()
    _remove_own_handlers(root)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    rid_filter = RequestIdFilter()

    console_handler = logging.StreamHandler(_utf8_console_stream())
    console_handler.setFormatter(formatter)
    console_handler.addFilter(rid_filter)
    setattr(console_handler, _HANDLER_MARK, True)

    app_handler = RotatingFileHandler(
        app_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    app_handler.setFormatter(formatter)
    app_handler.addFilter(rid_filter)
    setattr(app_handler, _HANDLER_MARK, True)

    error_handler = RotatingFileHandler(
        error_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    error_handler.addFilter(rid_filter)
    setattr(error_handler, _HANDLER_MARK, True)

    handlers: list[logging.Handler] = [app_handler, error_handler]
    if console:
        handlers.insert(0, console_handler)
    for h in handlers:
        root.addHandler(h)

    _adopt_uvicorn_loggers()
    return LogFiles(app=app_file, error=error_file)


def _adopt_uvicorn_loggers() -> None:
    """让 uvicorn 的日志走根 logger，避免格式不一致、请求 ID 丢失。"""
    for name in _UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.propagate = True
    # 访问日志由 RequestIDMiddleware 统一记录（带 request_id 与耗时），
    # uvicorn 自带的那一条是重复的，压到 WARNING 以下直接丢弃。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def shutdown_logging() -> None:
    """移除本模块安装的 handler（测试清理用）。"""
    _remove_own_handlers(logging.getLogger())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
