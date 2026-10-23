"""初始化 Milvus：创建 database + collection + 索引（幂等）。

    uv run python -m scripts.init_milvus           # 不存在才创建
    uv run python -m scripts.init_milvus --drop    # 删掉重建（数据清空，慎用）
"""

from __future__ import annotations

import argparse
import sys

from app.db.milvus_client import get_milvus_client
from app.db.milvus_schema import INDEX_SPECS, describe_specs, ensure_collection
from config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化 Milvus collection")
    parser.add_argument("--drop", action="store_true", help="删除已有 collection 后重建")
    args = parser.parse_args(argv)

    settings = get_settings()
    spec = describe_specs()
    print(
        f"[init_milvus] 目标 {settings.milvus.uri} db={settings.milvus.database} "
        f"collection={settings.milvus.collection} dim={spec['dense_dim']}"
    )

    client = get_milvus_client()
    result = ensure_collection(
        client,
        settings.milvus.database,
        settings.milvus.collection,
        dim=spec["dense_dim"],
        drop=args.drop,
    )
    state = "已创建" if result["created"] else "已存在（跳过）"
    print(f"[init_milvus] collection {state}: {result['collection']}")
    print(f"[init_milvus] 字段 {len(spec['fields'])} 个，索引 {len(INDEX_SPECS)} 个：")
    for item in spec["indexes"]:
        metric = f" metric={item['metric_type']}" if item["metric_type"] else ""
        print(f"  - {item['field']}: {item['index_type']}{metric}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
