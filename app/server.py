"""开发服务器入口。

    uv run python -m app.server

用 ``log_config=None`` 接管 uvicorn 日志，保证控制台输出与 ``logs/app.log``
格式一致、且都带 request_id；访问日志由 RequestIDMiddleware 统一记录。
"""

from __future__ import annotations

import uvicorn

from config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug,
        log_config=None,
        access_log=False,
        timeout_keep_alive=75,
    )


if __name__ == "__main__":
    main()
