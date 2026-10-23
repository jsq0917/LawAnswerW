"""检索召回与消融评估（设计方案 §7.3）。

    uv run python -m app.eval.recall_eval                 # L1/L2/L3/L4 全跑（真实 Milvus + bge-m3）
    uv run python -m app.eval.recall_eval --group L3      # 只跑一组
    uv run python -m app.eval.recall_eval --limit 5       # 调试

四组配置（固定候选池 20）：

| 组 | 配置 | 目的 |
|---|---|---|
| L1 | 仅稠密 + 不重排 | 基线，衡量稀疏信号的价值 |
| L2 | 混合（dense + sparse + RRF） + 不重排 | 衡量 RRF 融合的价值 |
| L3 | 混合 + bge-reranker-v2-m3 | 最终检索方案 |
| L4 | 混合 + 重排 + 父块回填 | 衡量整条法条进上下文的价值（不改排序，故召回同 L3） |

说明：§7.3 写"固定 topK=5"，但指标要求 Recall@8，故本轮统一按 topK=8 / pool=20 取候选，
Recall@1/3/5 由同一份结果截断得到，不影响组间可比性。

产出 ``eval/results/recall.json``：每组 Recall@1/3/5/8、MRR、延迟分位、逐条明细。
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Sequence

from app.eval.qa_dataset import load_cases
from app.ingest.embedder import Embedding, get_embedder
from app.retrieval.service import mrr, recall_at_k, retrieve

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "eval" / "results" / "recall.json"

TOP_K = 8
POOL = 20
K_LIST = (1, 3, 5, 8)

#: 组名 → retrieve() 参数
#: L1–L4 是阶段 7 首轮的四组（等权 RRF 时代），L5/L6 是落地"加权 RRF + 法域过滤"后的新配置：
#: L5 只换 ranker（稠密 0.75 / 稀疏 0.25），L6 再叠加意图层 law_hints 的检索法域过滤。
GROUPS: dict[str, dict[str, Any]] = {
    "L1": {"mode": "dense", "use_rerank": False, "expand_parent": False, "ranker": None,
           "law_filter": False, "label": "仅稠密（HNSW 单路）"},
    "L2": {"mode": "hybrid", "use_rerank": False, "expand_parent": False, "ranker": "rrf",
           "law_filter": False, "label": "混合 + 等权 RRF"},
    "L3": {"mode": "hybrid", "use_rerank": True, "expand_parent": False, "ranker": "rrf",
           "law_filter": False, "label": "混合 + 等权 RRF + 重排（阶段 7 首轮最终方案）"},
    "L4": {"mode": "hybrid", "use_rerank": True, "expand_parent": True, "ranker": "rrf",
           "law_filter": False, "label": "L3 + 父块回填"},
    "L5": {"mode": "hybrid", "use_rerank": True, "expand_parent": True, "ranker": "weighted",
           "law_filter": False, "label": "加权 RRF(0.75/0.25) + 重排 + 父块"},
    "L6": {"mode": "hybrid", "use_rerank": True, "expand_parent": True, "ranker": "weighted",
           "law_filter": True, "label": "加权 RRF + 法域过滤 + 重排 + 父块（新最终方案）"},
    "L7": {"mode": "hybrid", "use_rerank": True, "expand_parent": True, "ranker": "rrf",
           "law_filter": True, "label": "等权 RRF + 法域过滤 + 重排 + 父块（ranker 对照）"},
}

DEFAULT_GROUPS = ("L5", "L6")
HINTS_CACHE = ROOT / "eval" / "results" / "intent_hints.json"


class _CachingEmbedder:
    """同一问题在四组里只编码一次（bge-m3 前向是主要开销之一）。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._cache: dict[str, Embedding] = {}
        self.calls = 0

    def encode(self, texts: Sequence[str], **kwargs) -> list[Embedding]:
        missing = [t for t in texts if t not in self._cache]
        if missing:
            self.calls += 1
            for text, emb in zip(missing, self._inner.encode(missing, **kwargs)):
                self._cache[text] = emb
        return [self._cache[t] for t in texts]


def _pct(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def load_law_hints(cases: Sequence[dict[str, Any]], *, refresh: bool = False) -> dict[str, list[str]]:
    """用真实意图识别拿到每条样本的 law_hints，缓存到磁盘（LLM 调用 40 次约 1 分钟）。"""
    cached: dict[str, list[str]] = {}
    if HINTS_CACHE.exists() and not refresh:
        cached = json.loads(HINTS_CACHE.read_text(encoding="utf-8"))
        if all(c["id"] in cached for c in cases):
            print(f"复用意图 hints 缓存：{HINTS_CACHE.name}（--refresh-hints 可重算）")
            return cached

    from app.orchestration.intent import recognize_intent

    print("调用意图识别生成 law_hints（40 次 LLM 调用）…")
    for case in cases:
        result = recognize_intent(case["question"])
        cached[case["id"]] = list(result.law_hints)
        print(f"  {case['id']} intent={result.intent:<10} hints={result.law_hints}")
    HINTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    HINTS_CACHE.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
    return cached


def run_group(
    name: str,
    cases: Sequence[dict[str, Any]],
    embedder: Any,
    reranker: Any,
    *,
    hints: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    from config import get_settings

    cfg = GROUPS[name]
    base = get_settings()
    settings = base.model_copy(
        update={
            "retrieval_ranker": cfg["ranker"] or base.retrieval_ranker,
            "retrieval_law_filter": bool(cfg["law_filter"]),
        }
    )
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for i, case in enumerate(cases, 1):
        expected = {key for key in case["expected_articles"]}
        if not expected:
            continue  # no_answer 不计召回
        expected_set = set(expected)
        law_hints = (hints or {}).get(case["id"]) or None if cfg["law_filter"] else None
        started = time.perf_counter()
        result = retrieve(
            case["question"],
            kb_ids=case.get("kb_ids") or None,
            topk=TOP_K,
            mode=cfg["mode"],
            use_rerank=cfg["use_rerank"],
            expand_parent=cfg["expand_parent"],
            law_hints=law_hints,
            embedder=embedder,
            reranker=reranker if cfg["use_rerank"] else None,
            settings=settings,
        )
        elapsed = (time.perf_counter() - started) * 1000
        latencies.append(elapsed)
        hits = result.hits
        row = {
            "id": case["id"],
            "type": case["type"],
            "question": case["question"],
            "expected": sorted(expected_set),
            "retrieved": [h.law_article for h in hits],
            "recall": {f"@{k}": round(recall_at_k(hits, expected_set, k), 4) for k in K_LIST},
            "mrr": round(mrr(hits, expected_set), 4),
            "elapsed_ms": round(elapsed, 1),
            "context_chars": sum(len(h.parent_content or h.content) for h in hits),
        }
        rows.append(row)
        print(
            f"  [{name} {i:02d}/{len(cases)}] {case['id']} "
            f"R@5={row['recall']['@5']:.2f} MRR={row['mrr']:.2f} {elapsed:6.0f}ms "
            f"{case['question'][:24]}"
        )

    summary = {
        "group": name,
        "label": cfg["label"],
        "config": {"mode": cfg["mode"], "use_rerank": cfg["use_rerank"],
                   "expand_parent": cfg["expand_parent"], "ranker": cfg["ranker"],
                   "law_filter": cfg["law_filter"], "topk": TOP_K, "pool": POOL},
        "samples": len(rows),
        "recall": {f"@{k}": round(statistics.fmean(r["recall"][f"@{k}"] for r in rows), 4) for k in K_LIST},
        "mrr": round(statistics.fmean(r["mrr"] for r in rows), 4),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 1),
            "p50": _pct(latencies, 0.50),
            "p95": _pct(latencies, 0.95),
            "max": round(max(latencies), 1),
        },
        "avg_context_chars": round(statistics.fmean(r["context_chars"] for r in rows), 1),
        "by_type": {},
        "rows": rows,
    }
    for typ in ("single_hop", "multi_hop", "complex"):
        subset = [r for r in rows if r["type"] == typ]
        if subset:
            summary["by_type"][typ] = {
                "samples": len(subset),
                "recall@5": round(statistics.fmean(r["recall"]["@5"] for r in subset), 4),
                "mrr": round(statistics.fmean(r["mrr"] for r in subset), 4),
            }
    return summary


def corpus_stats() -> dict[str, Any]:
    """语料规模与"法条截断率"：父块中的句子是否都能在某个子块里找到。"""
    from sqlalchemy import text

    from app.ingest.chunker import split_sentences
    from app.db.mysql import get_engine

    with get_engine().connect() as conn:
        parents = conn.execute(
            text(
                "SELECT id, content FROM document_chunks "
                "WHERE chunk_type = 'parent' AND is_enabled = 1"
            )
        ).fetchall()
        children = conn.execute(
            text(
                "SELECT parent_id, content FROM document_chunks "
                "WHERE chunk_type = 'child' AND is_enabled = 1"
            )
        ).fetchall()
        by_kb = conn.execute(
            text(
                "SELECT kb_id, chunk_type, COUNT(*) FROM document_chunks "
                "WHERE is_enabled = 1 GROUP BY kb_id, chunk_type"
            )
        ).fetchall()

    child_text: dict[str, str] = {}
    for parent_id, content in children:
        child_text[parent_id] = child_text.get(parent_id, "") + "\n" + (content or "")

    truncated = 0
    checked = 0
    for parent_id, content in parents:
        if parent_id not in child_text:
            truncated += 1
            checked += 1
            continue
        joined = child_text[parent_id]
        sentences = [s for s in split_sentences(content or "") if len(s.strip()) >= 4]
        if not sentences:
            continue
        checked += 1
        if any(s.strip() not in joined for s in sentences):
            truncated += 1

    return {
        "parents": len(parents),
        "children": len(children),
        "by_kb": [
            {"kb_id": kb, "chunk_type": typ, "count": cnt} for kb, typ, cnt in by_kb
        ],
        "truncation_checked": checked,
        "truncation_rate": round(truncated / checked, 4) if checked else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检索召回与消融评估")
    parser.add_argument("--group", choices=list(GROUPS), action="append",
                        help=f"只跑指定组（可重复），默认 {list(DEFAULT_GROUPS)}")
    parser.add_argument("--all-groups", action="store_true", help="六组全跑（含 L1–L4 旧方案）")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试）")
    parser.add_argument("--refresh-hints", action="store_true", help="重算 law_hints 缓存")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    cases = load_cases()
    if args.limit > 0:
        cases = cases[: args.limit]
    names = args.group or (list(GROUPS) if args.all_groups else list(DEFAULT_GROUPS))

    print("=" * 78)
    print(f"检索消融评估  样本={len(cases)}（no_answer 不计召回）  组={names}  topK={TOP_K} pool={POOL}")
    print("=" * 78)

    hints = load_law_hints(cases, refresh=args.refresh_hints) if any(
        GROUPS[n]["law_filter"] for n in names
    ) else None

    embedder = _CachingEmbedder(get_embedder())
    reranker = None
    if any(GROUPS[n]["use_rerank"] for n in names):
        from app.retrieval.rerank import get_reranker

        reranker = get_reranker()

    started = time.perf_counter()
    groups = []
    for name in names:
        print(f"\n[{name}] {GROUPS[name]['label']}")
        groups.append(run_group(name, cases, embedder, reranker, hints=hints))

    total_s = round(time.perf_counter() - started, 1)
    print("\n" + "=" * 78)
    print(f"{'组':<4}{'Recall@1':>10}{'@3':>8}{'@5':>8}{'@8':>8}{'MRR':>8}{'p50(ms)':>10}{'p95(ms)':>10}")
    for g in groups:
        r = g["recall"]
        print(
            f"{g['group']:<4}{r['@1']:>10.4f}{r['@3']:>8.4f}{r['@5']:>8.4f}{r['@8']:>8.4f}"
            f"{g['mrr']:>8.4f}{g['latency_ms']['p50']:>10.0f}{g['latency_ms']['p95']:>10.0f}"
        )

    corpus = corpus_stats()
    print(f"\n语料：父块 {corpus['parents']} / 子块 {corpus['children']}，"
          f"法条截断率 {corpus['truncation_rate'] * 100:.2f}%（目标 ≤3%）")
    print(f"总耗时 {total_s}s，embedding 调用 {embedder.calls} 次")

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dataset": "eval/qa_test_cases.jsonl",
        "samples": len(cases),
        "topk": TOP_K,
        "pool": POOL,
        "targets": {"recall@5": 0.85, "recall@8": 0.92, "mrr": 0.75, "truncation_rate": 0.03},
        "groups": groups,
        "corpus": corpus,
        "total_seconds": total_s,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入 {args.out}")


if __name__ == "__main__":
    main()
