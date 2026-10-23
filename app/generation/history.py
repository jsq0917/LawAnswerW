"""历史对话窗口裁剪（设计方案 §6.5）。

裁剪优先级（从轻到重，任何一步回到预算内即停）：
1. **轮数裁剪**：只保留最近 ``max_rounds`` 轮（一轮 = 一条 user + 其后到
   下一条 user 之前的消息）。
2. **丢最旧整轮**：超预算时先丢最旧的整轮，但**保留最近 2 轮不动**。
3. **压缩旧轮**：仍超预算时，自旧向新把各轮的助手回答丢掉，只留用户问句
   （"更早的只保留用户问句"）——指代消解主要靠用户自己的问题；最近一轮
   永不压缩。
4. **硬保底**：还超就从头丢整条消息，但**最近一条用户消息永不过预算线外**，
   保证当前问题不丢。
"""

from __future__ import annotations

from typing import Sequence

from app.generation.prompts import estimate_tokens


def split_rounds(messages: Sequence[dict]) -> list[list[dict]]:
    """把消息序列切成轮：以 user 消息开头为一轮。

    容忍历史以 assistant 开头（残缺数据）——并入第一轮，不丢弃。
    """
    rounds: list[list[dict]] = []
    for msg in messages or []:
        role = msg.get("role", "user")
        if role == "user" or not rounds:
            rounds.append([msg])
        else:
            rounds[-1].append(msg)
    return rounds


def _rounds_tokens(rounds: Sequence[Sequence[dict]]) -> int:
    return sum(estimate_tokens(m.get("content") or "") for r in rounds for m in r)


def _msgs_tokens(messages: Sequence[dict]) -> int:
    return sum(estimate_tokens(m.get("content") or "") for m in messages)


def trim_history(
    messages: Sequence[dict] | None,
    *,
    max_rounds: int = 5,
    token_budget: int = 2000,
) -> list[dict]:
    """按轮数 + token 预算裁剪历史，返回按时间正序的 messages。

    :param messages: 原始历史 ``[{"role": "user"|"assistant", "content": "..."}]``
    :param max_rounds: 最多保留几轮（0 = 不带历史）
    :param token_budget: 历史 token 预算（估算口径，见 ``estimate_tokens``）
    """
    cleaned = [
        {"role": m.get("role", "user"), "content": (m.get("content") or "").strip()}
        for m in (messages or [])
        if (m.get("content") or "").strip()
    ]
    if not cleaned or max_rounds <= 0:
        return []

    rounds = split_rounds(cleaned)[-max_rounds:]

    # 2) 超预算先丢最旧整轮（保留最近 2 轮不动）
    while len(rounds) > 2 and _rounds_tokens(rounds) > token_budget:
        rounds.pop(0)

    # 3) 仍超预算：自旧向新压缩各轮（只留用户问句），最近一轮永不压缩
    if _rounds_tokens(rounds) > token_budget:
        for i in range(len(rounds) - 1):
            rounds[i] = [m for m in rounds[i] if m.get("role") == "user"]
            if _rounds_tokens(rounds) <= token_budget:
                break

    # 4) 硬保底：还超就从头丢整条（但最近一条 user 永不过预算线外）
    flat: list[dict] = [m for r in rounds for m in r]
    while len(flat) > 1 and _msgs_tokens(flat) > token_budget:
        if all(m.get("role") != "user" for m in flat[1:]):
            break
        flat.pop(0)

    return [{"role": m["role"], "content": m["content"]} for m in flat]
