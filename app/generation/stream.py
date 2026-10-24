"""生成链路编排：SSE 事件流 + 7 层容错（设计方案 §6.5）。

一次问答的事件序列::

    meta → stage(intent) → [stage(retrieval) → stage(rerank)] → sources
         → stage(generate) → delta×N → done
    异常路径：…… → delta×N（已产出的保留）→ error{code, message, retriable}

**7 层容错**（任一层的失败都不让整条链路断）：
1. 意图 LLM 失败 → 规则兜底（阶段 4 已实现，``meta.degraded=true`` 标记）；
2. 生成 LLM 首次连接失败 → 指数退避重试（``llm_max_retries``）；
3. 生成 LLM 上游断流 → 保留已产出 partial，发 ``error(retriable=true)`` 并落库；
4. 客户端断开（生成器被 ``close()``）→ 停止读上游、照常落库、不发 error；
5. Milvus 检索异常 → 追加 ``meta(degraded="vector_unavailable")``，
   有问答参考则降级回答，否则走无数据引导话术（§1.3 双链路独立降级）；
6. Redis 问答缓存异常 → qa_retriever 内部降级（阶段 4 已实现）；
7. 无任何资料 → 引导话术，**不编造**。

结构化命中（pure_structured）不走 LLM 全文生成：标准答案分片回放，
SSE 语义与真实流式一致（打字机效果），``done.cached`` 标记是否来自 Redis 精确缓存。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from app.core.logger import get_logger
from app.core.sse import sse_frame
from app.generation.context import (
    aggregate_sources,
    build_context_sources,
    qa_conclusion_block,
)
from app.generation.history import trim_history
from app.generation.llm_client import LLMClient, LLMError, LLMStreamInterrupted
from app.generation.persistence import persist_exchange
from app.generation.prompts import (
    GREETING_REPLY,
    NO_DATA_REPLY,
    build_context_block,
    build_messages,
)
from app.orchestration.qa_cache import QARetriever
from app.orchestration.service import QueryPlan, analyze_query
from config import ConfigError, Settings, get_settings

logger = get_logger("lawanswer.generation")


@dataclass
class StreamResult:
    """一次流式问答的累积状态（done 事件与落库的数据源）。"""

    content: str = ""
    sources: list[dict] = field(default_factory=list)
    ttft_ms: int | None = None
    latency_ms: int = 0
    token_usage: dict = field(default_factory=dict)
    cached: bool = False
    error: str | None = None
    persisted: bool = False


class AnswerEngine:
    """把一次查询变成 SSE 帧序列的**同步生成器**。

    API 层用 ``starlette.concurrency.iterate_in_threadpool`` 包装；
    同步实现让 TestClient 与单测可以直接逐帧消费，也避免在异步端点里
    混用阻塞调用（embedder/Milvus/LLM 全是同步 SDK）。
    """

    def __init__(
        self,
        *,
        qa_retriever: QARetriever | None = None,
        llm_client: LLMClient | None = None,
        retrieve_fn: Callable[..., Any] | None = None,
        persist_fn: Callable[..., bool] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._qa_retriever = qa_retriever
        self._llm_client = llm_client
        self._retrieve_fn = retrieve_fn
        self._persist_fn = persist_fn or persist_exchange
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def stream(
        self,
        query: str,
        *,
        history: Sequence[dict] | None = None,
        selected_kb_ids: list[str] | None = None,
        session_id: str | None = None,
    ) -> Iterator[str]:
        """产出 SSE 帧（``event: X\\ndata: {...}\\n\\n``）。"""
        s = self.settings
        gen_cfg = s.generation
        message_id = uuid.uuid4().hex
        started = time.perf_counter()
        result = StreamResult()
        plan: QueryPlan | None = None
        query = (query or "").strip()

        try:
            # ---- 意图 + 路由 + 结构化判定（阶段 4 编排入口，含 LLM 降级）----
            yield sse_frame("stage", {"stage": "intent"})
            plan = analyze_query(
                query,
                history=list(history) if history else None,
                selected_kb_ids=selected_kb_ids,
                qa_retriever=self._qa_retriever,
                settings=s,
            )
            route = plan.route.route

            yield sse_frame(
                "meta",
                {
                    "message_id": message_id,
                    "intent": plan.intent.intent,
                    "route": route,
                    "rewrite_type": plan.intent.rewrite_type,
                    "complexity": plan.intent.complexity,
                    "cached": False,
                    "degraded": plan.degraded,
                },
            )

            # ---- 分支 A：问候 → 固定话术，不调 LLM ----
            if route == "greeting":
                yield from self._replay_fixed(GREETING_REPLY, gen_cfg.replay_chunk_chars, result, started)
                yield self._done_frame(result, started)
                return

            # ---- 分支 B：纯结构化 → 标准答案回放，不碰 Milvus / 不调 LLM ----
            if route == "pure_structured":
                yield from self._structured_replay(plan, result, gen_cfg, started)
                yield self._done_frame(result, started)
                return

            # ---- 分支 B2：无数据（知识库不匹配/与法律无关）→ 引导话术，
            #      不调检索、不调 LLM（§6.3：no_data 不调用任何数据源）----
            if route == "no_data":
                logger.info("路由 no_data：引导话术 q=%.40s reason=%s", query, plan.route.reason)
                yield from self._replay_fixed(NO_DATA_REPLY, gen_cfg.replay_chunk_chars, result, started)
                yield self._done_frame(result, started)
                return

            # ---- 分支 C：检索（pure_vector / hybrid / cross_law）----
            yield sse_frame("stage", {"stage": "retrieval"})
            hits, degraded = self._retrieve(plan, s)
            if degraded:
                # 第 5 层容错：Milvus 故障 → 追加一条 meta 告知前端降级状态
                logger.warning("Milvus 检索不可用，按 vector_unavailable 降级回答")
                yield sse_frame(
                    "meta",
                    {
                        "message_id": message_id,
                        "intent": plan.intent.intent,
                        "route": route,
                        "rewrite_type": plan.intent.rewrite_type,
                        "complexity": plan.intent.complexity,
                        "cached": False,
                        "degraded": "vector_unavailable",
                    },
                )

            if hits:
                yield sse_frame("stage", {"stage": "rerank"})

            # 混合链路才带结构化素材：精确命中作结论段，同类参考作补充
            qa_hit = plan.qa.hit if route == "hybrid" else None
            similarity = plan.qa.similarity if route == "hybrid" else None
            references = plan.qa.references if route == "hybrid" else []

            result.sources = [
                r.to_dict()
                for r in aggregate_sources(
                    qa_hit=qa_hit,
                    similarity=similarity,
                    references=references,
                    hits=hits,
                    limit=gen_cfg.sources_limit,
                )
            ]

            # ---- 分支 D：无任何资料 → 引导话术，不编造（第 7 层）----
            if not result.sources:
                logger.info("无资料可用，走引导话术 q=%.40s route=%s", query, route)
                yield from self._replay_fixed(NO_DATA_REPLY, gen_cfg.replay_chunk_chars, result, started)
                yield self._done_frame(result, started)
                return

            context_sources = build_context_sources(
                qa_hit=qa_hit,
                similarity=similarity,
                references=references,
                hits=hits,
            )
            context_block = self._build_context(context_sources, qa_hit, gen_cfg)
            trimmed_history = trim_history(
                history,
                max_rounds=gen_cfg.history_max_rounds,
                token_budget=gen_cfg.history_token_budget,
            )
            messages = build_messages(query, context_block=context_block, history=trimmed_history)

            yield sse_frame("sources", {"sources": result.sources})
            yield sse_frame("stage", {"stage": "generate"})

            # ---- 流式生成（第 2/3 层容错）----
            yield from self._generate(messages, result, gen_cfg, started)
            yield self._done_frame(result, started)

        except LLMStreamInterrupted as exc:
            # 第 3 层：断流保留 partial（delta 已在 _generate 内产出）
            result.error = f"stream_interrupted: {exc}"
            logger.warning("生成断流，保留已产出 %d 字", len(exc.partial))
            yield sse_frame(
                "error", {"code": "stream_interrupted", "message": str(exc)[:200], "retriable": True}
            )
            yield self._done_frame(result, started)
        except ConfigError as exc:
            # 缺 DASHSCOPE_API_KEY：明确的错误码，前端可引导用户/管理员去配 key
            result.error = f"llm_not_configured: {exc}"
            logger.warning("LLM 未配置密钥：%s", str(exc)[:200])
            yield sse_frame(
                "error", {"code": "llm_not_configured", "message": "生成服务未配置 API Key，请联系管理员", "retriable": False}
            )
            yield self._done_frame(result, started)
        except LLMError as exc:
            # 第 2 层：重试后仍失败
            result.error = f"llm_error: {exc}"
            logger.warning("生成失败：%s", str(exc)[:200])
            yield sse_frame("error", {"code": "llm_error", "message": str(exc)[:200], "retriable": True})
            yield self._done_frame(result, started)
        except Exception as exc:  # noqa: BLE001 - 顶层兜底：链路绝不能裸奔
            result.error = f"internal_error: {exc}"
            logger.exception("问答链路未捕获异常")
            yield sse_frame("error", {"code": "internal_error", "message": "服务内部错误", "retriable": False})
            yield self._done_frame(result, started)
        finally:
            # 第 4 层：客户端断开（GeneratorExit）或任何路径，finally 保证落库一次
            self._persist(session_id, query, result, plan)

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------
    def _retrieve(self, plan: QueryPlan, settings: Settings) -> tuple[list, str | None]:
        """多子查询检索（并行分检 → 按法条合并去重 → 全局截断）。

        具体逻辑在 ``retrieval.service.retrieve_multi``——生产链路与
        recall_eval 共用同一实现，保证"评测口径 = 线上链路"。
        Milvus/检索异常 → 第 5 层容错：空列表 + degraded 标记，
        由调用方决定降级回答还是引导话术。
        """
        from app.retrieval.service import retrieve, retrieve_multi

        try:
            res = retrieve_multi(
                plan.sub_queries or [plan.query],
                kb_ids=list(plan.route.kb_ids) or None,
                complexity=plan.intent.complexity,
                law_hints=list(plan.intent.law_hints) or None,
                anchor_query=plan.query,
                settings=settings,
                retrieve_fn=self._retrieve_fn or retrieve,
            )
            return res.hits, None
        except Exception as exc:  # noqa: BLE001 - 检索故障降级，不打断链路
            logger.warning("检索失败，降级处理（%s: %s）", type(exc).__name__, str(exc)[:200])
            return [], "vector_unavailable"

    @staticmethod
    def _build_context(context_sources: Sequence, qa_hit, gen_cfg) -> str:
        """混合链路：标准答案作结论段置顶，法条作依据段（§6.3）。"""
        parts: list[str] = []
        if qa_hit is not None:
            parts.append(qa_conclusion_block(qa_hit))
        law_sources = [c for c in context_sources if c.kind == "law"]
        if law_sources:
            block, _ = build_context_block(law_sources, max_chars=gen_cfg.context_max_chars)
            if block:
                parts.append(f"【法条资料】\n{block}")
        # 无精确命中时的同类参考也进上下文（问+答成对给出）
        refs = [c for c in context_sources if c.kind == "qa" and c.law != "高频问答库"]
        for ref in refs[:3]:
            parts.append(f"【同类问题参考】{ref.content}")
        return "\n\n".join(parts)

    def _generate(self, messages: list[dict], result: StreamResult, gen_cfg, started: float) -> Iterator[str]:
        """流式生成：逐 chunk 产出 delta 帧，记录 TTFT（首 token）与用量。"""
        client = self._llm_client or self._default_llm_client()
        for chunk in client.chat_stream(
            messages,
            temperature=gen_cfg.temperature,
            max_tokens=gen_cfg.max_tokens,
        ):
            if chunk.usage:
                result.token_usage = dict(chunk.usage)
            if not chunk.text:
                continue
            if result.ttft_ms is None:
                result.ttft_ms = int((time.perf_counter() - started) * 1000)
            result.content += chunk.text
            yield sse_frame("delta", {"text": chunk.text})

    def _structured_replay(self, plan: QueryPlan, result: StreamResult, gen_cfg, started: float) -> Iterator[str]:
        """纯结构化分支：标准答案分片回放（防御：无命中则降级引导话术）。"""
        hit = plan.qa.hit
        if hit is None:
            # 理论不可达（路由已保证命中）；缺失时不碰 Milvus，直接引导话术
            logger.warning("pure_structured 但问答未命中，降级引导话术")
            yield from self._replay_fixed(NO_DATA_REPLY, gen_cfg.replay_chunk_chars, result, started)
            return
        yield sse_frame("stage", {"stage": "generate"})
        result.sources = [
            r.to_dict()
            for r in aggregate_sources(qa_hit=hit, similarity=plan.qa.similarity, limit=gen_cfg.sources_limit)
        ]
        result.cached = plan.qa.source == "redis_exact"
        yield sse_frame("sources", {"sources": result.sources})
        yield from self._replay_fixed(hit.answer, gen_cfg.replay_chunk_chars, result, started)

    def _replay_fixed(self, text: str, chunk_chars: int, result: StreamResult, started: float) -> Iterator[str]:
        """固定话术/标准答案分片回放（打字机效果，SSE 语义与流式一致）。"""
        text = (text or "").strip()
        if not text:
            return
        step = max(5, chunk_chars)
        for i in range(0, len(text), step):
            piece = text[i : i + step]
            if result.ttft_ms is None:
                # 回放路径同样记录 TTFT（缓存命中速度是结构化链路的核心指标）
                result.ttft_ms = int((time.perf_counter() - started) * 1000)
            result.content += piece
            yield sse_frame("delta", {"text": piece})

    @staticmethod
    def _done_frame(result: StreamResult, started: float) -> str:
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return sse_frame(
            "done",
            {
                "ttft_ms": result.ttft_ms or 0,
                "latency_ms": result.latency_ms,
                "token_usage": result.token_usage or {},
                "sources_count": len(result.sources),
                "cached": result.cached,
            },
        )

    def _default_llm_client(self) -> LLMClient:
        from app.generation.llm_client import get_llm_client

        return get_llm_client(self.settings)

    def _persist(self, session_id: str | None, query: str, result: StreamResult, plan: QueryPlan | None) -> None:
        """幂等落库：正常结束、断流、客户端断开都保证调用且只调一次。"""
        if result.persisted or not session_id:
            return
        result.persisted = True
        try:
            self._persist_fn(
                session_id,
                user_content=query,
                assistant_content=result.content,
                sources=result.sources,
                intent=plan.intent.intent if plan else None,
                rewrite_type=plan.intent.rewrite_type if plan else None,
                route=plan.route.route if plan else None,
                cached=result.cached,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
                token_usage=result.token_usage or None,
                error=result.error,
                settings=self.settings,
            )
        except Exception as exc:  # noqa: BLE001 - 双保险：落库永不影响流
            logger.warning("落库调用失败（已忽略）: %s", str(exc)[:200])
