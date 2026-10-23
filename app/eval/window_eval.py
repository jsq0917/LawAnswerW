"""消融 D 组：固定窗口 500/50 vs 按条分块（设计方案 §7.3 最后一行）。

为什么单独实现：主链路的分块以「第X条」为硬边界（父子块），固定窗口必须**无视条边界**切，
无法复用 Milvus 里现有的按条块。本脚本在内存里重建一份固定窗口语料并就地检索：

    MySQL parent 块（整条正文，按 chunk_index 排序）
      → 拼接成每部法律的连续文本，同时记录「字符区间 → 条号」
      → 500 字窗口 / 50 字重叠切分（跨条切，正是要对比的点）
      → bge-m3 编码（dense + sparse）
      → 稠密余弦 + 稀疏内积 → RRF(k=60) → bge-reranker 重排 top20
      → 窗口命中的条号集合参与 Recall 计算（窗口覆盖到该条即算命中）

与 L3 的口径差异（报告里要写清）：
- 召回在内存里算，不经 Milvus，**延迟不可比**，只比召回；
- 窗口不含"法名·章节"标题前缀（Milvus 里的块有），对稠密召回略有不利；
- 一个窗口可能覆盖多条法条，命中集合取并集。

    uv run python -m app.eval.window_eval
    uv run python -m app.eval.window_eval --chunk-size 500 --overlap 50
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from app.eval.qa_dataset import load_cases
from app.retrieval.hybrid import SPARSE_DROP_RATIO, Hit

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "eval" / "results" / "recall_window.json"

K_LIST = (1, 3, 5, 8)
POOL = 20
TOP_K = 8
RRF_K = 60


def _load_parents() -> list[tuple[str, str, str, int, str]]:
    """(doc_id, law, article_no, chunk_index, content)，按文档与序号排序。"""
    from sqlalchemy import text

    from app.db.mysql import get_engine

    stmt = text(
        "SELECT doc_id, title, article_no, chunk_index, content FROM document_chunks "
        "WHERE chunk_type = 'parent' AND is_enabled = 1 "
        "ORDER BY doc_id, chunk_index"
    )
    rows = []
    with get_engine().connect() as conn:
        for doc_id, title, article_no, idx, content in conn.execute(stmt):
            law = (title or "").split("·")[0].strip()
            rows.append((doc_id, law, article_no or "", int(idx or 0), content or ""))
    return rows


def build_windows(parents: list[tuple[str, str, str, int, str]], size: int, overlap: int) -> list[dict[str, Any]]:
    """按文档拼接后切固定窗口，并记录每个窗口覆盖的条号。"""
    from app.ingest.chunker import split_sentences

    windows: list[dict[str, Any]] = []
    by_doc: dict[str, list[tuple[str, str, int, str]]] = {}
    for doc_id, law, article_no, idx, content in parents:
        by_doc.setdefault(doc_id, []).append((law, article_no, idx, content))

    for doc_id, items in by_doc.items():
        items.sort(key=lambda x: x[2])
        law = items[0][0]
        flat = ""
        spans: list[tuple[int, int, str]] = []  # (start, end, article_no)
        for _law, article_no, _idx, content in items:
            start = len(flat)
            flat += content.strip() + "\n"
            spans.append((start, len(flat), article_no))
        step = max(1, size - overlap)
        for start in range(0, max(1, len(flat)), step):
            chunk = flat[start : start + size]
            if len(chunk.strip()) < 30:
                continue
            end = start + len(chunk)
            covered = [art for (s, e, art) in spans if art and s < end and e > start]
            if not covered:
                continue
            windows.append(
                {
                    "law": law,
                    "articles": sorted(set(covered)),
                    "content": chunk.strip(),
                    "start": start,
                }
            )
            if end >= len(flat):
                break
    return windows


def _sparse_dot(q: dict[str, float], d: dict[str, float]) -> float:
    if len(q) > len(d):
        q, d = d, q
    return sum(w * d.get(k, 0.0) for k, w in q.items())


def _rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def run(size: int, overlap: int, limit: int = 0) -> dict[str, Any]:
    import numpy as np

    from app.ingest.embedder import get_embedder
    from app.retrieval.rerank import get_reranker

    cases = [c for c in load_cases() if c["expected_articles"]]
    if limit:
        cases = cases[:limit]

    print(f"加载按条父块并切 {size}/{overlap} 固定窗口 …")
    parents = _load_parents()
    windows = build_windows(parents, size, overlap)
    print(f"父块 {len(parents)} 条 → 窗口 {len(windows)} 个")

    embedder = get_embedder()
    started = time.perf_counter()
    emb = embedder.encode([w["content"] for w in windows])
    dense = np.array([e.dense for e in emb], dtype="float32")
    dense /= np.linalg.norm(dense, axis=1, keepdims=True) + 1e-9
    sparse_vecs = []
    for e in emb:
        idx, val = e.sparse_arrays()
        pairs = [(i, v) for i, v in zip(idx, val) if v >= 0.05]
        if pairs:
            pairs.sort(key=lambda x: -x[1])
            pairs = pairs[: int(len(pairs) * (1 - SPARSE_DROP_RATIO)) or 1]
        sparse_vecs.append({str(i): float(v) for i, v in pairs})
    print(f"编码完成 {round(time.perf_counter() - started, 1)}s")

    reranker = get_reranker()
    rows = []
    for i, case in enumerate(cases, 1):
        expected = set(case["expected_articles"])
        q_emb = embedder.encode([case["question"]])[0]
        q = np.asarray(q_emb.dense, dtype="float32")
        q /= np.linalg.norm(q) + 1e-9
        dense_scores = dense @ q
        dense_rank = list(np.argsort(-dense_scores)[:POOL])

        qi, qv = q_emb.sparse_arrays()
        q_sparse = {str(a): float(b) for a, b in zip(qi, qv)}
        sparse_scores = [_sparse_dot(q_sparse, sv) for sv in sparse_vecs]
        sparse_rank = list(np.argsort(-np.asarray(sparse_scores))[:POOL])

        fused = _rrf([dense_rank, sparse_rank])[:POOL]
        candidates = [windows[idx] for idx, _ in fused]
        if candidates:
            scores = reranker.score(case["question"], [c["content"] for c in candidates])
            order = sorted(range(len(candidates)), key=lambda j: scores[j], reverse=True)
            candidates = [candidates[j] for j in order]

        hits: list[Hit] = []
        for c in candidates[:TOP_K]:
            for art in c["articles"]:
                hits.append(
                    Hit(
                        chunk_id=f"{c['start']}",
                        parent_id="",
                        kb_id="",
                        doc_id="",
                        content=c["content"],
                        title="",
                        article_no=art,
                        chapter_path="",
                        law_name=c["law"],
                        source_file="",
                        score=1.0,
                    )
                )
        # 固定窗口的一个"命中"覆盖多条法条：按窗口计 Recall（top-k 窗口覆盖的条号并集），
        # 与按条切分的 top-k 条不是同一粒度，故单独标注口径。
        r_at = {}
        for k in K_LIST:
            covered = set()
            for c in candidates[:k]:
                covered.update(f"{c['law']}#{art}" for art in c["articles"])
            r_at[f"@{k}"] = round(len(covered & expected) / len(expected), 4) if expected else 0.0
        first_rank = 0
        for rank, c in enumerate(candidates[:TOP_K], start=1):
            if {f"{c['law']}#{art}" for art in c["articles"]} & expected:
                first_rank = rank
                break
        rows.append(
            {
                "id": case["id"],
                "type": case["type"],
                "expected": sorted(expected),
                "retrieved": [f"{h.law_name}#{h.article_no}" for h in hits[:TOP_K]],
                "recall": r_at,
                "mrr": round(1.0 / first_rank, 4) if first_rank else 0.0,
            }
        )
        print(f"  [{i:02d}/{len(cases)}] {case['id']} R@5={r_at['@5']:.2f} {case['question'][:24]}")

    summary = {
        "group": f"D(fixed {size}/{overlap})",
        "label": f"固定窗口 {size}/{overlap} + 混合 + 重排（内存模拟，不经 Milvus）",
        "samples": len(rows),
        "windows": len(windows),
        "recall": {f"@{k}": round(statistics.fmean(r["recall"][f"@{k}"] for r in rows), 4) for k in K_LIST},
        "mrr": round(statistics.fmean(r["mrr"] for r in rows), 4),
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


def main() -> None:
    parser = argparse.ArgumentParser(description="固定窗口消融（D 组）")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--overlap", type=int, default=50)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    summary = run(args.chunk_size, args.overlap, args.limit)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    r = summary["recall"]
    print("\n" + "=" * 60)
    print(f"{summary['group']}  Recall@1={r['@1']} @3={r['@3']} @5={r['@5']} @8={r['@8']}  MRR={summary['mrr']}")
    for typ, v in summary["by_type"].items():
        print(f"  {typ:<11} n={v['samples']:2d} R@5={v['recall@5']:.3f} MRR={v['mrr']:.3f}")
    print(f"结果 → {OUT}")


if __name__ == "__main__":
    main()
