"""问答落库（chat_messages）——流式中断容错的底座（设计方案 §6.5）。

* 只有 ``session_id`` 对应会话真实存在才落库（外键约束 ``fk_message_session``）；
  会话不存在/已软删时记 warning 后跳过，**绝不让落库失败打断 SSE 流**。
* 用户消息与助手消息同一事务写入，``seq`` 按会话内最大值 +1 递增。
* ``intent/route/cached/ttft_ms/token_usage/error`` 全量落库——
  阶段 7 的意图准确率、路由命中率、TTFT P95 都从这张表出（§2.3）。
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy import text

from app.core.logger import get_logger
from config import Settings, get_settings

logger = get_logger("lawanswer.chat_store")


def persist_exchange(
    session_id: str | None,
    *,
    user_content: str,
    assistant_content: str,
    sources: Sequence[dict] | None = None,
    intent: str | None = None,
    rewrite_type: str | None = None,
    route: str | None = None,
    cached: bool = False,
    latency_ms: int | None = None,
    ttft_ms: int | None = None,
    token_usage: dict | None = None,
    error: str | None = None,
    settings: Settings | None = None,
) -> bool:
    """把一轮问答写入 ``chat_messages``。任何失败只记日志，返回 ``False``。"""
    if not session_id:
        return False
    if not (user_content or "").strip() and not (assistant_content or "").strip():
        return False

    s = settings or get_settings()
    try:
        from app.db.mysql import get_engine

        with get_engine(s).connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM chat_sessions WHERE id = :sid AND deleted_at IS NULL"),
                {"sid": session_id},
            ).fetchone()
            if not exists:
                logger.warning("会话 %s 不存在或已删除，跳过落库", session_id)
                return False
            seq = _next_seq(conn, session_id)
            rows: list[dict[str, Any]] = []
            if user_content.strip():
                rows.append({"seq": seq, "role": "user", "content": user_content.strip()})
            if assistant_content.strip():
                rows.append(
                    {
                        "seq": seq + (1 if user_content.strip() else 0),
                        "role": "assistant",
                        "content": assistant_content.strip(),
                        "sources": sources or None,
                        "intent": intent,
                        "rewrite_type": rewrite_type,
                        "route": route,
                        "cached": 1 if cached else 0,
                        "latency_ms": latency_ms,
                        "ttft_ms": ttft_ms,
                        "token_usage": token_usage or None,
                        "error": (error or "")[:255] or None,
                    }
                )
            for row in rows:
                conn.execute(
                    text(
                        "INSERT INTO chat_messages "
                        "(session_id, seq, role, content, sources, intent, rewrite_type, "
                        " route, cached, latency_ms, ttft_ms, token_usage, error) "
                        "VALUES (:sid, :seq, :role, :content, "
                        "CAST(:sources AS JSON), :intent, :rewrite_type, "
                        ":route, :cached, :latency_ms, :ttft_ms, "
                        "CAST(:token_usage AS JSON), :error)"
                    ),
                    {
                        "sid": session_id,
                        "seq": row["seq"],
                        "role": row["role"],
                        "content": row["content"],
                        "sources": json.dumps(row.get("sources"), ensure_ascii=False) if row.get("sources") else None,
                        "intent": row.get("intent"),
                        "rewrite_type": row.get("rewrite_type"),
                        "route": row.get("route"),
                        "cached": row.get("cached", 0),
                        "latency_ms": row.get("latency_ms"),
                        "ttft_ms": row.get("ttft_ms"),
                        "token_usage": json.dumps(row.get("token_usage"), ensure_ascii=False) if row.get("token_usage") else None,
                        "error": row.get("error"),
                    },
                )
            conn.commit()
        logger.info(
            "落库成功 session=%s route=%s cached=%s 用户=%d 字 助手=%d 字",
            session_id,
            route,
            cached,
            len(user_content or ""),
            len(assistant_content or ""),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 落库失败绝不能打断问答流
        logger.warning("落库失败（已忽略，不影响回答）: %s: %s", type(exc).__name__, str(exc)[:200])
        return False


def _next_seq(conn, session_id: str) -> int:
    """会话内下一个 seq。

    ``FOR UPDATE`` 锁住该会话的 seq 索引区间直到事务提交：同一会话并发
    落库时串行取号，避免两条消息算出同一个 ``MAX(seq)+1`` 撞号。
    （跨会话互不影响——session_id 上有 FK 索引，锁范围按会话隔离。）
    """
    row = conn.execute(
        text(
            "SELECT COALESCE(MAX(seq), 0) FROM chat_messages "
            "WHERE session_id = :sid FOR UPDATE"
        ),
        {"sid": session_id},
    ).fetchone()
    return int(row[0]) + 1
