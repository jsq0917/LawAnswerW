"""日志层测试：双写、ERROR 分流、request_id 注入、重复初始化不叠加 handler。"""

from __future__ import annotations

import logging

from app.core.logger import (
    REQUEST_ID,
    current_request_id,
    get_logger,
    new_request_id,
    reset_request_id,
    set_request_id,
    setup_logging,
    shutdown_logging,
)


def _read(path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


class TestDualWrite:
    def test_info_written_to_app_log(self, tmp_path):
        files = setup_logging(tmp_path, level="INFO", console=False)
        get_logger("lawanswer.test").info("劳动法知识库已就绪")
        for h in logging.getLogger().handlers:
            h.flush()
        content = _read(files.app)
        assert "劳动法知识库已就绪" in content
        assert "INFO" in content
        # 文件名与行号必须出现在格式里，排查时才有意义
        assert "test_logging.py" in content

    def test_error_written_to_both_files(self, tmp_path):
        files = setup_logging(tmp_path, level="INFO", console=False)
        get_logger("lawanswer.test").error("Milvus 连接失败")
        for h in logging.getLogger().handlers:
            h.flush()
        assert "Milvus 连接失败" in _read(files.app)
        assert "Milvus 连接失败" in _read(files.error)

    def test_info_not_written_to_error_log(self, tmp_path):
        files = setup_logging(tmp_path, level="INFO", console=False)
        log = get_logger("lawanswer.test")
        log.info("普通信息")
        log.error("真实错误")
        for h in logging.getLogger().handlers:
            h.flush()
        error_content = _read(files.error)
        assert "真实错误" in error_content
        assert "普通信息" not in error_content

    def test_level_filter(self, tmp_path):
        files = setup_logging(tmp_path, level="WARNING", console=False)
        log = get_logger("lawanswer.test")
        log.info("不应出现")
        log.warning("应出现")
        for h in logging.getLogger().handlers:
            h.flush()
        content = _read(files.app)
        assert "不应出现" not in content
        assert "应出现" in content


class TestRequestId:
    def test_request_id_injected_into_log(self, tmp_path):
        files = setup_logging(tmp_path, level="INFO", console=False)
        token = REQUEST_ID.set("req-abc123")
        try:
            get_logger("lawanswer.test").info("带请求 ID 的日志")
        finally:
            REQUEST_ID.reset(token)
        for h in logging.getLogger().handlers:
            h.flush()
        assert "req-abc123" in _read(files.app)

    def test_default_request_id_is_dash(self, tmp_path):
        files = setup_logging(tmp_path, level="INFO", console=False)
        get_logger("lawanswer.test").info("无请求上下文")
        for h in logging.getLogger().handlers:
            h.flush()
        assert "| - |" in _read(files.app)

    def test_set_and_reset_request_id(self):
        rid = set_request_id()
        assert len(rid) == 16
        assert current_request_id() == rid
        assert new_request_id() != rid

    def test_reset_request_id_tolerates_bad_token(self):
        """跨上下文 reset 不应把请求主流程搞崩。"""
        reset_request_id(object())
        assert current_request_id() == "-"


class TestIdempotency:
    @staticmethod
    def _marked() -> list[logging.Handler]:
        # pytest 自己也会往 root 挂 LogCaptureHandler，故只统计本模块安装的
        return [
            h for h in logging.getLogger().handlers if getattr(h, "_lawanswer_handler", False)
        ]

    def test_repeated_setup_does_not_duplicate_handlers(self, tmp_path):
        setup_logging(tmp_path, console=False)
        first = len(self._marked())
        setup_logging(tmp_path, console=False)
        second = len(self._marked())
        assert first == second == 2  # app.log + error.log

    def test_console_handler_can_be_disabled(self, tmp_path):
        setup_logging(tmp_path, console=False)
        assert len(self._marked()) == 2

    def test_console_handler_enabled_by_default(self, tmp_path):
        setup_logging(tmp_path, console=True)
        assert len(self._marked()) == 3

    def test_shutdown_removes_handlers(self, tmp_path):
        setup_logging(tmp_path, console=False)
        shutdown_logging()
        assert self._marked() == []

    def test_rotation_params_respected(self, tmp_path):
        files = setup_logging(tmp_path, max_bytes=2048, backup_count=2, console=False)
        log = get_logger("lawanswer.test")
        for i in range(200):
            log.info("滚动测试 %s %s", i, "x" * 50)
        for h in logging.getLogger().handlers:
            h.flush()
        assert files.app.exists()
        assert any(tmp_path.glob("app.log.*")), "超过 max_bytes 应产生滚动文件"


class TestUvicornAdoption:
    def test_uvicorn_loggers_propagate_to_root(self, tmp_path):
        """uvicorn 的日志要走我们的格式，否则文件里会出现两种格式。"""
        setup_logging(tmp_path, console=False)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            assert lg.propagate is True
            assert lg.handlers == []

    def test_uvicorn_access_is_silenced_to_avoid_duplicate(self, tmp_path):
        setup_logging(tmp_path, console=False)
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
