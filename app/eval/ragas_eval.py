"""RAGAS 五项评估（设计方案 §7.4）。

分两步，互不阻塞：

1. **采集**（``--collect``）：对 40 条样本打真实 SSE 接口，落 ``eval/results/qa_answers.jsonl``
   （answer / sources / route / ttft / tokens），并把来源还原成完整法条正文作为 retrieved_contexts。
   已存在的结果会跳过（``--force`` 重采），中断可续跑。
2. **打分**（默认）：读答案文件，用 RAGAS 跑五项指标，写 ``eval/results/ragas.json``。

    uv run python -m app.eval.ragas_eval --collect        # 需要服务已启动
    uv run python -m app.eval.ragas_eval                  # 需要 uv sync --group eval

judge 用配置里的大模型（本机为 DeepSeek `deepseek-flash`，设计稿写的是 qwen-max），
embedding 复用本地 bge-m3（与检索同一模型，避免语义空间不一致）。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from app.eval.qa_dataset import load_cases

ROOT = Path(__file__).resolve().parents[2]
ANSWERS_PATH = ROOT / "eval" / "results" / "qa_answers.jsonl"
RAGAS_PATH = ROOT / "eval" / "results" / "ragas.json"

METRIC_TARGETS = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.85,
    "context_precision": 0.80,
    "context_recall": 0.85,
    "context_relevance": 0.80,
}


# ---------------------------------------------------------------------------
# 第一步：采集真实回答
# ---------------------------------------------------------------------------
def _sources_to_contexts(sources: list[dict[str, Any]]) -> list[str]:
    """来源 → RAGAS 的 retrieved_contexts：优先用完整法条正文，退化用 snippet。"""
    from sqlalchemy import text

    from app.db.mysql import get_engine

    keys = []
    for src in sources:
        law = (src.get("law") or "").strip()
        article = (src.get("article_no") or "").strip()
        if law and article:
            keys.append((law, article))
    texts: dict[tuple[str, str], str] = {}
    if keys:
        stmt = text(
            "SELECT title, article_no, content FROM document_chunks "
            "WHERE chunk_type = 'parent' AND is_enabled = 1 "
            "AND article_no IN :arts"
        )
        arts = tuple({a for _, a in keys})
        from sqlalchemy import bindparam

        stmt = stmt.bindparams(bindparam("arts", expanding=True))
        try:
            with get_engine().connect() as conn:
                for title, article_no, content in conn.execute(stmt, {"arts": arts}):
                    law = (title or "").split("·")[0].strip()
                    texts[(law, article_no)] = content or ""
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] 回表取正文失败，退化用 snippet：{exc}")

    contexts = []
    for src in sources:
        key = ((src.get("law") or "").strip(), (src.get("article_no") or "").strip())
        body = texts.get(key)
        if body:
            contexts.append(f"《{key[0]}》{key[1]}：{body}")
        elif src.get("snippet"):
            contexts.append(src["snippet"])
    return contexts


def collect(url: str, *, force: bool = False) -> None:
    import httpx

    cases = load_cases()
    done: dict[str, dict[str, Any]] = {}
    if ANSWERS_PATH.exists() and not force:
        for line in ANSWERS_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["id"]] = row
        print(f"已有 {len(done)} 条结果，跳过重采（--force 可强制重采）")

    ANSWERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ANSWERS_PATH.open("a" if done else "w", encoding="utf-8") as fh:
        for i, case in enumerate(cases, 1):
            if case["id"] in done:
                continue
            body: dict[str, Any] = {"query": case["question"]}
            if case.get("kb_ids"):
                body["kb_ids"] = case["kb_ids"]
            started = time.perf_counter()
            answer, sources, meta, usage, error = "", [], {}, {}, None
            kind = None
            with httpx.stream("POST", url, json=body, timeout=300.0) as resp:
                if resp.status_code != 200:
                    resp.read()
                    error = f"HTTP {resp.status_code}"
                else:
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        if line.startswith("event: "):
                            kind = line[7:].strip()
                            continue
                        if not line.startswith("data: "):
                            continue
                        ev = json.loads(line[6:])
                        if kind == "meta":
                            meta = ev
                        elif kind == "sources":
                            sources = ev.get("sources") or []
                        elif kind == "delta":
                            answer += ev.get("text") or ""
                        elif kind == "error":
                            error = f"{ev.get('code')}: {ev.get('message')}"
                        elif kind == "done":
                            usage = ev.get("token_usage") or {}
            elapsed = int((time.perf_counter() - started) * 1000)
            row = {
                "id": case["id"],
                "type": case["type"],
                "question": case["question"],
                "reference": case["reference_answer"],
                "expected_articles": case["expected_articles"],
                "answer": answer,
                "sources": [
                    {k: s.get(k) for k in ("law", "chapter_path", "article_no", "score", "kind")}
                    for s in sources
                ],
                "contexts": _sources_to_contexts(sources),
                "route": meta.get("route"),
                "cached": meta.get("cached"),
                "degraded": meta.get("degraded"),
                "token_usage": usage,
                "elapsed_ms": elapsed,
                "error": error,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(
                f"  [{i:02d}/{len(cases)}] {case['id']} route={row['route']} "
                f"{elapsed}ms 答案 {len(answer)} 字 上下文 {len(row['contexts'])} 条"
                + (f"  ERROR {error}" if error else "")
            )
    print(f"采集完成 → {ANSWERS_PATH}")


# ---------------------------------------------------------------------------
# 第二步：RAGAS 打分
# ---------------------------------------------------------------------------
def _load_answers() -> list[dict[str, Any]]:
    if not ANSWERS_PATH.exists():
        raise SystemExit(f"缺少答案文件 {ANSWERS_PATH}，先跑 --collect")
    rows = [json.loads(line) for line in ANSWERS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def _build_llm_and_embeddings():
    """judge LLM 走 OpenAI 兼容接口；embedding 用本地 bge-m3。"""
    from langchain_core.embeddings import Embeddings
    from langchain_openai import ChatOpenAI
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from config import get_llm_api_key, get_settings

    s = get_settings()

    class BgeM3Embeddings(Embeddings):
        """把项目里的 bge-m3 embedder 适配成 LangChain Embeddings。"""

        @property
        def _model(self):
            from app.ingest.embedder import get_embedder

            return get_embedder()

        def embed_documents(self, texts):
            return [e.dense for e in self._model.encode(list(texts))]

        def embed_query(self, text):
            return self._model.encode([text])[0].dense

    llm = ChatOpenAI(
        model=s.llm_default_model,
        base_url=s.llm_base_url,
        api_key=get_llm_api_key(),
        temperature=0.0,
        timeout=180,
        max_retries=3,
    )
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(BgeM3Embeddings())


def score(limit: int = 0) -> None:
    from ragas import EvaluationDataset, evaluate

    rows = _load_answers()
    if limit > 0:
        rows = rows[:limit]
    rows = [r for r in rows if (r.get("answer") or "").strip() and r.get("contexts")]
    print(f"待打分样本 {len(rows)} 条")

    samples = [
        {
            "user_input": r["question"],
            "response": r["answer"],
            "retrieved_contexts": r["contexts"],
            "reference": r["reference"],
        }
        for r in rows
    ]
    dataset = EvaluationDataset.from_list(samples)

    import ragas.metrics as ragas_metrics

    # ragas 0.3.0 里前四项以函数/实例形式导出，context_relevance 只有 CamelCase 类名
    metric_objs: dict[str, Any] = {
        "faithfulness": ragas_metrics.faithfulness,
        "answer_relevancy": ragas_metrics.answer_relevancy,
        "context_precision": ragas_metrics.context_precision,
        "context_recall": ragas_metrics.context_recall,
        "context_relevance": getattr(ragas_metrics, "ContextRelevance", None),
    }
    metrics = []
    missing = []
    wanted = list(metric_objs)
    columns: dict[str, str] = {}  # 逻辑指标名 → 结果表列名（ragas 版本间命名有差异）
    for name, obj in metric_objs.items():
        if obj is None:
            missing.append(name)
            continue
        inst = obj() if callable(obj) else obj
        metrics.append(inst)
        columns[name] = getattr(inst, "name", name)
    if missing:
        print(f"[WARN] ragas 未导出这些指标（版本差异）：{missing}")
    print(f"实际打分指标：{[(n, columns[n]) for n in wanted if n in columns]}")

    llm, embeddings = _build_llm_and_embeddings()
    from ragas.run_config import RunConfig

    # judge 是远程 API：并发 8 折中（太高容易被限流重试，太低 40 条要跑一小时）
    run_config = RunConfig(timeout=180, max_retries=6, max_workers=8)
    started = time.perf_counter()
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        run_config=run_config,
        raise_exceptions=False,
        show_progress=True,
    )
    elapsed = round(time.perf_counter() - started, 1)

    df = result.to_pandas()
    per_metric: dict[str, float] = {}
    for name in wanted:
        col = columns.get(name)
        if col and col in df.columns:
            values = [v for v in df[col].tolist() if v == v]  # 过滤 NaN
            per_metric[name] = round(statistics.fmean(values), 4) if values else 0.0

    per_sample = []
    for i, r in enumerate(rows):
        item = {"id": r["id"], "type": r["type"], "question": r["question"], "route": r.get("route")}
        for name in wanted:
            col = columns.get(name)
            if col and col in df.columns:
                item[name] = None if df[col].iloc[i] != df[col].iloc[i] else round(float(df[col].iloc[i]), 4)
        per_sample.append(item)

    verdict = {
        name: {
            "score": per_metric.get(name),
            "target": target,
            "passed": (per_metric.get(name) or 0) >= target,
        }
        for name, target in METRIC_TARGETS.items()
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "samples": len(rows),
        "elapsed_seconds": elapsed,
        "metrics": per_metric,
        "verdict": verdict,
        "per_sample": per_sample,
        "failed_samples": [r for r in per_sample if any(v is not None and v < 0.5 for k, v in r.items() if k in wanted)][:10],
    }
    RAGAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAGAS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    for name, info in verdict.items():
        flag = "OK " if info["passed"] else "MISS"
        print(f"  {flag} {name:<20} {info['score']}  （目标 ≥{info['target']}）")
    print(f"\n耗时 {elapsed}s，结果 → {RAGAS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAGAS 五项评估")
    parser.add_argument("--collect", action="store_true", help="只采集真实回答")
    parser.add_argument("--force", action="store_true", help="重采已有结果")
    parser.add_argument("--url", default="http://127.0.0.1:8101/api/chat/stream")
    parser.add_argument("--limit", type=int, default=0, help="只打分前 N 条（调试）")
    args = parser.parse_args()

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    if args.collect:
        collect(args.url, force=args.force)
    else:
        score(args.limit)


if __name__ == "__main__":
    main()
