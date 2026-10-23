"""SSE 并发压测（设计方案 §7.5）。

    uv run python -m app.eval.loadtest                    # 1/5/10/20 四档
    uv run python -m app.eval.loadtest --levels 1 5        # 指定档位
    uv run python -m app.eval.loadtest --requests 10       # 每档请求数（默认 30）

为什么不用 locust：SSE 的"首字延迟"必须解析帧才能算，asyncio + httpx 直接读流更贴合，
也能顺带校验 ``meta/error/done`` 事件是否完整。

两件避免数据失真的事：
1. 每档开始前清 ``qa:exact:*`` 缓存（否则高频问题全命中结构化回放，延迟失真）；
2. 问题加随机后缀变体，避免 30 次打同一句话。

产出 ``eval/results/loadtest_<level>.json`` + 汇总表。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "eval" / "results"

#: 10 条真实问题（覆盖结构化/向量/混合/问候），压测时循环使用
QUESTIONS: list[dict[str, Any]] = [
    {"query": "试用期最长可以约定多久？", "kb_ids": ["kb_labor"]},
    {"query": "加班费按几倍工资计算？", "kb_ids": ["kb_labor"]},
    {"query": "公司裁员，经济补偿怎么算？", "kb_ids": ["kb_labor"]},
    {"query": "竞业限制最长能约定几年？", "kb_ids": ["kb_labor"]},
    {"query": "被违法辞退的赔偿金怎么算？", "kb_ids": ["kb_labor"]},
    {"query": "未签书面劳动合同的二倍工资怎么算？", "kb_ids": ["kb_labor"]},
    {"query": "年休假天数怎么确定？", "kb_ids": ["kb_labor"]},
    {"query": "借款没约定利息还能要利息吗？", "kb_ids": ["kb_civil"]},
    {"query": "房屋租赁合同最长多少年？", "kb_ids": ["kb_civil"]},
    {"query": "你好", "kb_ids": None},
]

#: 随机后缀：改变 query 文本以绕开精确缓存，又不改变问题语义
SUFFIXES = ["", "，请简要回答", "，请说明依据", "，用一两句话说明", "，麻烦了", "，谢谢"]


def clear_qa_cache() -> int:
    """清空 qa 精确缓存，避免结构化回放把 TTFT 拉成个位数毫秒。"""
    try:
        from app.db.redis_client import get_redis

        client = get_redis()
        keys = list(client.scan_iter("qa:exact:*"))
        if keys:
            client.delete(*keys)
        return len(keys)
    except Exception as exc:  # noqa: BLE001 - 压测不因缓存清理失败而中断
        print(f"  [WARN] 清理 qa 缓存失败：{exc}")
        return 0


async def one_request(client: httpx.AsyncClient, url: str, body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    first_delta_ms = None
    route = cached = None
    kind = None
    chars = 0
    tokens: dict[str, int] = {}
    error: dict[str, Any] | None = None
    status = None
    try:
        async with client.stream("POST", url, json=body) as resp:
            status = resp.status_code
            if status != 200:
                text = (await resp.aread()).decode("utf-8", "ignore")[:200]
                return {
                    "status": status,
                    "error": {"code": "http_error", "message": text},
                    "ttft_ms": None,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                }
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    kind = line[7:].strip()
                    continue
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    continue
                ev = json.loads(payload)
                if kind == "meta":
                    route = ev.get("route")
                    cached = ev.get("cached")
                elif kind == "delta":
                    if first_delta_ms is None:
                        first_delta_ms = int((time.perf_counter() - started) * 1000)
                    chars += len(ev.get("text") or "")
                elif kind == "error":
                    error = {"code": ev.get("code"), "message": str(ev.get("message"))[:120]}
                elif kind == "done":
                    tokens = ev.get("token_usage") or {}
    except Exception as exc:  # noqa: BLE001 - 记录异常即可，不影响其他并发
        return {
            "status": status,
            "error": {"code": type(exc).__name__, "message": str(exc)[:120]},
            "ttft_ms": first_delta_ms,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    total_ms = int((time.perf_counter() - started) * 1000)
    completion = int(tokens.get("completion_tokens") or 0)
    return {
        "status": status,
        "route": route,
        "cached": cached,
        "ttft_ms": first_delta_ms,
        "latency_ms": total_ms,
        "chars": chars,
        "completion_tokens": completion,
        "tokens_per_s": round(completion / (total_ms / 1000), 2) if completion and total_ms else 0.0,
        "error": error,
    }


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def summarize(level: int, results: list[dict[str, Any]], wall_s: float, requests: int) -> dict[str, Any]:
    ok = [r for r in results if r["status"] == 200 and not r.get("error")]
    failed = [r for r in results if r not in ok]
    ttfts = [r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None]
    totals = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    routes: dict[str, int] = {}
    for r in ok:
        routes[r.get("route") or "unknown"] = routes.get(r.get("route") or "unknown", 0) + 1
    return {
        "concurrency": level,
        "requests": len(results),
        "planned_requests": requests,
        "wall_seconds": round(wall_s, 2),
        "qps": round(len(ok) / wall_s, 3) if wall_s else 0.0,
        "success": len(ok),
        "failed": len(failed),
        "error_rate": round(len(failed) / len(results), 4) if results else 0.0,
        "ttft_ms": {
            "mean": round(statistics.fmean(ttfts), 1) if ttfts else 0.0,
            "p50": _pct(ttfts, 0.50),
            "p95": _pct(ttfts, 0.95),
            "p99": _pct(ttfts, 0.99),
            "max": round(max(ttfts), 1) if ttfts else 0.0,
        },
        "latency_ms": {
            "mean": round(statistics.fmean(totals), 1) if totals else 0.0,
            "p50": _pct(totals, 0.50),
            "p95": _pct(totals, 0.95),
            "p99": _pct(totals, 0.99),
            "max": round(max(totals), 1) if totals else 0.0,
        },
        "tokens_per_s_mean": round(
            statistics.fmean([r["tokens_per_s"] for r in ok if r.get("tokens_per_s")]), 2
        ) if any(r.get("tokens_per_s") for r in ok) else 0.0,
        "routes": routes,
        "errors": [r.get("error") for r in failed][:10],
    }


async def run_level(level: int, *, url: str, requests: int, warmup: int) -> dict[str, Any]:
    cleared = clear_qa_cache()
    print(f"\n[并发 {level}] 清缓存 {cleared} 条，预热 {warmup} 次，正式 {requests} 次")
    limits = httpx.Limits(max_connections=level + 5, max_keepalive_connections=level + 5)
    timeout = httpx.Timeout(300.0, connect=10.0)
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        # 预热：让 embedder / reranker / 连接池就位
        for i in range(warmup):
            q = QUESTIONS[i % len(QUESTIONS)]
            await one_request(client, url, {"query": q["query"], "kb_ids": q["kb_ids"]})

        bodies = []
        for i in range(requests):
            q = QUESTIONS[i % len(QUESTIONS)]
            suffix = random.choice(SUFFIXES)
            body: dict[str, Any] = {"query": q["query"] + suffix}
            if q["kb_ids"]:
                body["kb_ids"] = q["kb_ids"]
            bodies.append(body)

        sem = asyncio.Semaphore(level)

        async def guarded(body: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await one_request(client, url, body)

        started = time.perf_counter()
        results = await asyncio.gather(*(guarded(b) for b in bodies))
        wall = time.perf_counter() - started

    summary = summarize(level, list(results), wall, requests)
    print(
        f"  QPS={summary['qps']}  成功={summary['success']}/{len(results)}  "
        f"TTFT p50={summary['ttft_ms']['p50']}ms p95={summary['ttft_ms']['p95']}ms  "
        f"总延迟 p95={summary['latency_ms']['p95']}ms  错误率={summary['error_rate']:.1%}"
    )
    if summary["routes"]:
        print(f"  路由分布：{summary['routes']}")
    if summary["errors"]:
        print(f"  失败样例：{summary['errors'][:3]}")
    return summary


async def async_main(levels: list[int], *, url: str, requests: int, warmup: int, out_dir: Path) -> None:
    summaries = []
    for level in levels:
        summary = await run_level(level, url=url, requests=requests, warmup=warmup)
        summaries.append(summary)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"loadtest_{level}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    (out_dir / "loadtest_summary.json").write_text(
        json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "levels": summaries},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\n" + "=" * 78)
    print(f"{'并发':<6}{'请求':>6}{'QPS':>8}{'TTFT p50':>10}{'TTFT p95':>10}{'总 p95':>10}{'错误率':>9}")
    for s in summaries:
        print(
            f"{s['concurrency']:<6}{s['requests']:>6}{s['qps']:>8.2f}"
            f"{s['ttft_ms']['p50']:>10.0f}{s['ttft_ms']['p95']:>10.0f}"
            f"{s['latency_ms']['p95']:>10.0f}{s['error_rate']:>9.1%}"
        )
    print(f"\n结果目录：{out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SSE 并发压测")
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 5, 10, 20])
    parser.add_argument("--requests", type=int, default=30, help="每档正式请求数")
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--url", default="http://127.0.0.1:8101/api/chat/stream")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    asyncio.run(
        async_main(args.levels, url=args.url, requests=args.requests,
                   warmup=args.warmup, out_dir=args.out_dir)
    )


if __name__ == "__main__":
    main()
