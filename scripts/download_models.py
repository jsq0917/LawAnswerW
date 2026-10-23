"""下载 bge-m3 与 bge-reranker-v2-m3。

    uv run python -m scripts.download_models --check          # 只检查本地是否就绪
    uv run python -m scripts.download_models                  # 下载全部（默认镜像优先）
    uv run python -m scripts.download_models --model reranker --endpoint hf
    uv run python -m scripts.download_models --model bge-m3 --endpoint modelscope

为什么要单独写脚本
------------------
1. 实测发现 `~/.cache/huggingface/hub/models--BAAI--bge-m3` 只有一个 refs 指针、
   没有任何权重（约 2.2 GB 缺失），必须显式下载并**校验文件确实存在**，
   否则到阶段 2 加载模型时才炸，排查成本高。
2. 国内直连 huggingface.co 经常超时，脚本按 镜像 → 官方 → ModelScope 顺序回退。
3. 下载到 `local_dir`（普通目录，不是 hub 缓存结构），这样配置项里的路径
   可以直接交给 FlagEmbedding / transformers 加载。
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from config import get_settings

HF_ENDPOINTS = {
    "mirror": "https://hf-mirror.com",
    "hf": "https://huggingface.co",
}

#: 权重文件候选名（safetensors 优先）
_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")

#: 下载时跳过的大文件：onnx 目录通常是主权重的 2-3 倍，本项目用不到
_IGNORE_PATTERNS = ["*.onnx", "onnx/*", "*.h5", "*.msgpack", "*.ot", "imgs/*", "*.onnx_data"]


@dataclass(frozen=True)
class ModelTarget:
    key: str
    repo_id: str
    dest: Path
    note: str


def _targets() -> list[ModelTarget]:
    s = get_settings()
    return [
        ModelTarget(
            key="bge-m3",
            repo_id="BAAI/bge-m3",
            dest=s.embedding_model_dir,
            note="稠密(1024)+稀疏 双向量，约 2.2 GB",
        ),
        ModelTarget(
            key="reranker",
            repo_id="BAAI/bge-reranker-v2-m3",
            dest=s.reranker_model_dir,
            note="cross-encoder 重排，约 2.2 GB",
        ),
    ]


def safetensors_intact(path: Path) -> tuple[bool, str]:
    """校验 safetensors 是否被截断。

    只比文件大小是不够的：下载中断会留下一个"大小看着还行、但张量数据不全"
    的文件，加载时才报 offset 越界，排查成本极高。这里读头部算出应有的数据长度，
    与真实文件长度比对，秒级完成且不需要 torch。
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            raw = f.read(8)
            if len(raw) < 8:
                return False, "文件头不完整"
            header_len = struct.unpack("<Q", raw)[0]
            if header_len <= 0 or header_len > 100 * 1024 * 1024:
                return False, f"头部长度异常（{header_len}）"
            header = f.read(header_len)
        if len(header) != header_len:
            return False, "头部被截断"
        try:
            meta = json.loads(header)
        except json.JSONDecodeError:
            return False, "头部不是合法 JSON"
        offsets = [
            v["data_offsets"][1]
            for k, v in meta.items()
            if k != "__metadata__" and "data_offsets" in v
        ]
        if not offsets:
            return False, "头部没有任何张量"
        expected = 8 + header_len + max(offsets)
        if size < expected:
            return False, f"权重被截断，缺少 {expected - size} 字节"
        return True, f"{len(offsets)} 个张量，{size / 1024 / 1024:.0f} MB"
    except OSError as exc:
        return False, f"读取失败：{exc}"


def inspect(target: ModelTarget) -> tuple[bool, str]:
    """检查目录是否已是可用模型目录（而不是空的 hub 缓存壳子）。"""
    dest = target.dest
    if not dest.exists():
        return False, f"目录不存在：{dest}"
    config = dest / "config.json"
    if not config.exists():
        # 兼容 "指向 hub 缓存根目录" 的情况：给出明确指引而不是让上层模型加载报错
        return False, f"缺少 config.json（{dest} 可能只是 hub 缓存指针，不是模型目录）"
    weight = next((dest / name for name in _WEIGHT_FILES if (dest / name).exists()), None)
    if weight is None and not (dest / "model.safetensors.index.json").exists():
        return False, f"缺少权重文件（{'/'.join(_WEIGHT_FILES)}）"
    if weight is not None:
        size_mb = weight.stat().st_size / 1024 / 1024
        if size_mb < 100:
            return False, f"权重文件异常偏小（{size_mb:.1f} MB）：{weight}"
        if weight.suffix == ".safetensors":
            intact, detail = safetensors_intact(weight)
            if not intact:
                return False, f"safetensors 损坏：{detail}（{weight}）"
            return True, f"就绪，{weight.name} {detail}，路径 {dest}"
        return True, f"就绪，{weight.name} = {size_mb:.0f} MB，路径 {dest}"
    return True, f"就绪（分片权重），路径 {dest}"


def _run_snapshot(repo_id: str, dest: Path, endpoint: str) -> int:
    """在子进程里下载：HF_ENDPOINT 必须在 import huggingface_hub 之前生效。"""
    dest.mkdir(parents=True, exist_ok=True)
    code = (
        "import json\n"
        "from huggingface_hub import snapshot_download\n"
        f"path = snapshot_download(repo_id={json.dumps(repo_id)}, "
        f"local_dir={json.dumps(str(dest))}, "
        f"ignore_patterns={json.dumps(_IGNORE_PATTERNS)}, max_workers=4)\n"
        "print('[download] OK ->', path)\n"
    )
    env = dict(os.environ)
    env["HF_ENDPOINT"] = endpoint
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    # huggingface_hub 2.x 默认走 Xet(CAS) 协议，镜像站不代理该域名，会报 401；
    # 关掉后回退到经典 HTTP 下载，镜像才能用。
    env["HF_HUB_DISABLE_XET"] = "1"
    print(f"[download] repo={repo_id} -> {dest}\n[download] endpoint={endpoint}")
    return subprocess.call([sys.executable, "-c", code], env=env)


def download(target: ModelTarget, endpoint: str, retries: int = 2, backoff: float = 3.0) -> bool:
    ok, message = inspect(target)
    if ok:
        print(f"[skip] {target.key}: {message}")
        return True

    if endpoint == "modelscope":
        print(
            "[download] ModelScope 通道需要额外安装 modelscope，请手动执行：\n"
            f"  uv pip install modelscope\n"
            f"  uv run modelscope download --model {target.repo_id} "
            f"--local_dir {target.dest}"
        )
        return False

    # endpoint 传的是别名（auto/mirror/hf），要转换成真实 URL 再给子进程
    if endpoint in HF_ENDPOINTS:
        endpoints = [HF_ENDPOINTS[endpoint]]
    else:
        endpoints = list(HF_ENDPOINTS.values())
    for ep in endpoints:
        for attempt in range(1, max(1, retries) + 1):
            rc = _run_snapshot(target.repo_id, target.dest, ep)
            if rc == 0:
                ok, message = inspect(target)
                print(f"[result] {target.key}: {message}")
                if ok:
                    return True
            print(
                f"[warn] {target.key} 从 {ep} 第 {attempt}/{retries} 次尝试失败（rc={rc}）"
            )
            if attempt < retries:
                time.sleep(backoff)
        print(f"[warn] {target.key} 放弃源 {ep}，尝试下一个源…")

    print(
        f"[error] {target.key} 下载失败。可手动处理：\n"
        f"  1) 浏览器下载 https://hf-mirror.com/{target.repo_id} 全部文件（跳过 onnx/）\n"
        f"     放到：{target.dest}\n"
        f"  2) 或使用 ModelScope：uv pip install modelscope && "
        f"uv run modelscope download --model {target.repo_id} --local_dir {target.dest}\n"
        f"  3) 完成后执行：uv run python -m scripts.download_models --check"
    )
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载 LawAnswer 所需的本地模型")
    parser.add_argument("--model", choices=["all", "bge-m3", "reranker"], default="all")
    parser.add_argument(
        "--endpoint",
        choices=["auto", "mirror", "hf", "modelscope"],
        default="auto",
        help="auto=镜像优先并回退官方；modelscope 只打印手动命令",
    )
    parser.add_argument("--retries", type=int, default=2, help="每个源的尝试次数（网络抖动时调大）")
    parser.add_argument("--check", action="store_true", help="只检查本地状态，不下载")
    args = parser.parse_args(argv)

    targets = [t for t in _targets() if args.model in ("all", t.key)]
    if args.check:
        all_ready = True
        for target in targets:
            ok, message = inspect(target)
            all_ready = all_ready and ok
            print(f"[{'ok' if ok else 'missing'}] {target.key} ({target.repo_id}): {message}")
            print(f"           {target.note}")
        return 0 if all_ready else 1

    results = [download(t, args.endpoint, retries=args.retries) for t in targets]
    print("\n===== 汇总 =====")
    for target, ok in zip(targets, results):
        print(f"{'✓' if ok else '✗'} {target.key}: {target.dest}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
