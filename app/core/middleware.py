"""请求级中间件：请求 ID 注入 + 访问日志。"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logger import REQUEST_ID, get_logger, new_request_id

logger = get_logger("lawanswer.access")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求分配 request_id，写入日志上下文与响应头。

    网关/前端传了 ``X-Request-ID`` 就复用，便于端到端串联；否则生成 16 位短 ID。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = REQUEST_ID.set(rid)
        request.state.request_id = rid
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = rid
            return response
        except Exception:
            logger.exception("未捕获异常 %s %s", request.method, request.url.path)
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            # 健康检查噪音较大，降级为 DEBUG
            log = logger.debug if "/health" in request.url.path else logger.info
            log(
                "%s %s -> %s (%.1fms)",
                request.method,
                request.url.path,
                status_code,
                elapsed_ms,
            )
            REQUEST_ID.reset(token)
