"""问答缓存预热：把 qa_pairs 的标准问法与变体写入 Redis 精确缓存。

为什么需要
----------
``qa:exact:{sha1}`` 只在"BM25 命中并通过判据"之后才写入，所以部署新版本、
Redis 数据卷重建、或缓存 TTL 到期后，第一个提问的人仍要走完整 RAG。
预热把 65 条问答对的所有问法一次性铺进缓存，常见问题**首次**提问即秒回。

用法::

    uv run --no-sync python -m scripts.warm_qa_cache          # 写入
    uv run --no-sync python -m scripts.warm_qa_cache --dry-run # 只看会写多少条
    uv run --no-sync python -m scripts.warm_qa_cache --check   # 只校验命中率
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import text

from app.core.logger import get_logger
from app.db.mysql import get_engine
from app.db.redis_client import get_redis
from app.orchestration.qa_cache import _row_to_qa, cache_key, normalize_question
from config import get_settings

logger = get_logger("lawanswer.warm_qa_cache")

_SELECT = (
    "SELECT id, category, question_std, question_variants, answer, law_refs, "
    "       kb_id, intent, keywords, hit_count "
    "FROM qa_pairs WHERE is_active = 1 ORDER BY id"
)


def warm(*, dry_run: bool = False) -> dict[str, int]:
    """把每条问答对的标准问法与变体写进 ``qa:exact``，返回统计。"""
    s = get_settings()
    redis = get_redis(s)
    with get_engine(s).connect() as conn:
        rows = conn.execute(text(_SELECT)).fetchall()

    stats = {"qa_pairs": len(rows), "phrasings": 0, "written": 0, "skipped": 0}
    for row in rows:
        qa = _row_to_qa(row)
        payload = json.dumps(qa.to_dict(), ensure_ascii=False)
        # 标准问法 + 全部变体，归一化后可能撞 key（同义写法），用 set 去重
        norms = {normalize_question(t) for t in [qa.question_std, *qa.question_variants]}
        for norm in sorted(n for n in norms if n):
            stats["phrasings"] += 1
            if dry_run:
                continue
            try:
                redis.set(cache_key(norm), payload, ex=s.cache_qa_ttl)
                stats["written"] += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其余
                stats["skipped"] += 1
                logger.warning("预热失败 qa_id=%s: %s", qa.id, exc)
    return stats


def check() -> dict[str, int]:
    """校验缓存现状：多少条问法能命中。"""
    s = get_settings()
    redis = get_redis(s)
    with get_engine(s).connect() as conn:
        rows = conn.execute(text(_SELECT)).fetchall()
    hit = miss = 0
    for row in rows:
        qa = _row_to_qa(row)
        norms = {normalize_question(t) for t in [qa.question_std, *qa.question_variants]}
        for norm in (n for n in norms if n):
            if redis.exists(cache_key(norm)):
                hit += 1
            else:
                miss += 1
    return {"hit": hit, "miss": miss, "keys_in_redis": redis.dbsize()}


def main() -> None:
    parser = argparse.ArgumentParser(description="问答精确缓存预热")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    parser.add_argument("--check", action="store_true", help="只校验命中情况")
    args = parser.parse_args()

    if args.check:
        result = check()
        print(f"命中 {result['hit']} 条问法，未命中 {result['miss']} 条，Redis 现有 {result['keys_in_redis']} 个键")
        return

    stats = warm(dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}qa_pairs={stats['qa_pairs']} 问法={stats['phrasings']} "
        f"写入={stats['written']} 失败={stats['skipped']}"
    )


if __name__ == "__main__":
    main()
