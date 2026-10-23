"""大模型连通性自检：确认"密钥 → 厂商 → 模型"三者对得上。

    uv run python -m scripts.check_llm

依次探测三项能力（任一失败说明配置或厂商能力不匹配）：
1. 普通对话（生成链路）；
2. 流式对话（SSE 打字机）；
3. JSON 模式（意图识别强依赖 ``response_format=json_object``）。

只打印密钥来源变量名与掩码，不打印密钥本身。
"""

from __future__ import annotations

import json
import sys
import time

from app.generation.llm_client import LLMClient, LLMError, get_llm_client
from config import get_llm_api_key, get_llm_api_key_env, get_settings, mask_secret


def _header() -> None:
    s = get_settings()
    print("=" * 64)
    print("provider 配置")
    print(f"  LLM_BASE_URL      : {s.llm_base_url}")
    print(f"  LLM_DEFAULT_MODEL : {s.llm_default_model}")
    print(f"  LLM_THINKING      : {s.llm_thinking or '（不传，适用于 DashScope/OpenAI）'}")
    print(f"  密钥来源变量      : {get_llm_api_key_env() or '（未配置）'}")
    print(f"  密钥（掩码）      : {mask_secret(get_llm_api_key()) if get_llm_api_key() else '（未配置）'}")
    print("=" * 64)
    if not get_llm_api_key():
        print("[FAIL] 未检测到密钥：请设置 LLM_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY")
        sys.exit(2)


def _probe_plain(client: LLMClient) -> bool:
    started = time.perf_counter()
    try:
        resp = client.chat(
            [{"role": "user", "content": "只回复两个字：可以"}],
            temperature=0,
            max_tokens=16,
        )
    except LLMError as exc:
        print(f"[FAIL] 普通对话：{str(exc)[:200]}")
        return False
    elapsed = int((time.perf_counter() - started) * 1000)
    print(f"[ OK ] 普通对话  model={resp.model} {elapsed}ms 内容={resp.content[:40]!r}")
    if resp.usage:
        print(f"       token 用量={resp.usage}")
    return True


def _probe_stream(client: LLMClient) -> bool:
    started = time.perf_counter()
    first_ms = None
    text = ""
    usage: dict = {}
    try:
        for chunk in client.chat_stream(
            [{"role": "user", "content": "用一句话说明试用期最长期限"}],
            temperature=0.3,
            max_tokens=64,
        ):
            if chunk.usage:
                usage = chunk.usage
            if chunk.text:
                if first_ms is None:
                    first_ms = int((time.perf_counter() - started) * 1000)
                text += chunk.text
    except LLMError as exc:
        print(f"[FAIL] 流式对话：{str(exc)[:200]}")
        return False
    total_ms = int((time.perf_counter() - started) * 1000)
    print(f"[ OK ] 流式对话  首字={first_ms}ms 总={total_ms}ms 长度={len(text)} 字")
    if not usage:
        print("       [WARN] 末 chunk 未带 usage（服务端未返回 stream_options 用量，不影响回答）")
    return True


def _probe_json(client: LLMClient) -> bool:
    started = time.perf_counter()
    try:
        data, resp = client.chat_json(
            [
                {"role": "system", "content": '只输出 JSON 对象，不要解释：{"intent": "labor_law"}'},
                {"role": "user", "content": '把这句话按示例输出 JSON：试用期最长多久'},
            ],
            temperature=0,
            max_tokens=64,
        )
    except LLMError as exc:
        print(f"[FAIL] JSON 模式：{str(exc)[:200]}")
        return False
    elapsed = int((time.perf_counter() - started) * 1000)
    print(f"[ OK ] JSON 模式 {elapsed}ms 解析结果={json.dumps(data, ensure_ascii=False)[:80]}")
    if not isinstance(data, dict) or not data:
        print("       [WARN] 未返回预期 JSON，意图识别会走规则兜底（不影响可用性）")
    return True


def main() -> None:
    _header()
    client = get_llm_client()
    results = {
        "plain": _probe_plain(client),
        "stream": _probe_stream(client),
        "json": _probe_json(client),
    }
    print("=" * 64)
    failed = [name for name, ok in results.items() if not ok]
    if failed:
        print(f"结论：{len(failed)} 项失败（{', '.join(failed)}）")
        print("排查顺序：1) 密钥是否有效；2) LLM_BASE_URL 是否与服务商匹配；")
        print("          3) LLM_DEFAULT_MODEL 是否是服务商支持的模型名。")
        sys.exit(1)
    print("结论：三项探测全部通过，可以直接跑阶段 5 的真实 LLM 验收与阶段 7 评估。")


if __name__ == "__main__":
    main()
