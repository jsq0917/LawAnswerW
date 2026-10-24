"""问答 SSE 端点：``POST /api/chat/stream``（设计方案 §6.5）。

事件协议::

    event: meta     data: {message_id, intent, route, rewrite_type, complexity, cached, degraded}
    event: stage    data: {stage: intent|retrieval|rerank|generate}
    event: sources  data: {sources: [{law, chapter_path, article_no, score, snippet, kind}]}
    event: delta    data: {text: "..."}
    event: done     data: {ttft_ms, latency_ms, token_usage, sources_count, cached}
    event: error    data: {code, message, retriable}

工程要点：
* ``AnswerEngine`` 是同步生成器，用 ``iterate_in_threadpool`` 包装成异步流，
  不阻塞事件循环；
* 客户端断开（AbortController）→ 异步任务被取消 → 关闭同步生成器 →
  ``AnswerEngine`` 的 ``finally`` 照常落库、不发 error（§6.5 容错第 4 层）；
* ``session_id`` 可选：传了且会话存在才落库（阶段 6 接入登录/会话后即为默认路径）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from starlette.responses import StreamingResponse

from app.api.sessions import get_owned_session
from app.core.ratelimit import check_rate_limit
from app.core.security import CurrentUser, TokenError, get_current_user_optional
from app.core.sse import SSE_HEADERS, SSE_MEDIA_TYPE
from app.db.mysql import session_scope
from app.generation.stream import AnswerEngine

router = APIRouter(prefix="/api", tags=["chat"])


class ChatStreamRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list, description="最近对话 [{role, content}]")
    kb_ids: list[str] | None = Field(default=None, description="前端勾选的知识库，空=全部")
    session_id: str | None = Field(default=None, description="会话 ID（ULID）；传了才落库")


@router.post("/chat/stream", summary="SSE 流式问答（六段 Prompt + 双源路由）")
async def chat_stream(
    payload: ChatStreamRequest,
    request: Request,
    user: CurrentUser | None = Depends(get_current_user_optional),
) -> Any:
    # 会话模式必须登录且会话归属当前用户：否则任何人都能往任意会话里塞消息。
    # 校验用短连接完成，SSE 流期间不占 MySQL 连接池连接。
    if payload.session_id:
        if user is None:
            raise TokenError("会话模式需要登录")

        def _check_session() -> None:
            with session_scope() as db:
                get_owned_session(db, payload.session_id, user.id)  # type: ignore[union-attr]

        await run_in_threadpool(_check_session)

    # 限流：匿名按 IP 20 次/分，登录按 uid 60 次/分（防 LLM 费用被刷爆）。
    # Redis 是同步客户端，必须入线程池——直接调用会在 Redis 抖动时（最坏 5s）
    # 冻结整个事件循环，殃及所有请求。
    await run_in_threadpool(
        check_rate_limit, request, user_id=user.id if user else None
    )

    engine = AnswerEngine()
    frames = engine.stream(
        payload.query,
        history=payload.history or None,
        selected_kb_ids=payload.kb_ids,
        session_id=payload.session_id,
    )

    async def event_stream():
        try:
            async for frame in iterate_in_threadpool(frames):
                if await request.is_disconnected():
                    frames.close()  # 触发 AnswerEngine.finally → 落库
                    return
                yield frame
        finally:
            frames.close()

    return StreamingResponse(event_stream(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)
