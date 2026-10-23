"""SSE（Server-Sent Events）编码与响应头（设计方案 §6.5 / §1.4 core/sse）。

事件帧格式::

    event: meta
    data: {"message_id": "..."}

    event: delta
    data: {"text": "结论："}

要点：
* JSON 用 ``ensure_ascii=False``，中文直出，前端无需二次解码；
* 含换行的 data 拆成多行 ``data:``（SSE 协议要求）；
* 响应头关缓存 + ``X-Accel-Buffering: no``，防止 Nginx/代理缓冲把
  打字机效果攒成一次性输出（风险 R10）。
"""

from __future__ import annotations

import json
from typing import Any

#: SSE 响应必须带的头（防缓冲）
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

SSE_MEDIA_TYPE = "text/event-stream; charset=utf-8"


def sse_frame(event: str, data: Any) -> str:
    """把一个事件编码为 SSE 帧（含结尾空行）。"""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    lines = [f"event: {event}"]
    for line in payload.split("\n"):
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def parse_sse_frames(raw: str) -> list[tuple[str, dict]]:
    """测试辅助：把 SSE 文本解析回 ``[(event, data_dict), ...]``。

    只解析 ``event:`` + ``data:``（单行 JSON）的规范帧，供离线断言使用。
    """
    frames: list[tuple[str, dict]] = []
    event_name = ""
    data_lines: list[str] = []
    for line in raw.split("\n"):
        if line.startswith("event: "):
            event_name = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            data_lines.append(line[len("data: ") :])
        elif line == "" and event_name:
            frames.append((event_name, json.loads("\n".join(data_lines))))
            event_name, data_lines = "", []
    return frames
