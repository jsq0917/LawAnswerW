"""图形验证码：SVG 生成 + Redis 一次性校验（设计方案 §4.2 P1）。

为什么用**手写 SVG** 而不是 Pillow 画位图：
* 矢量图在高分屏不糊，体积约 1KB，直接 base64 内联给前端 ``<img>``；
* 不需要额外字体资源，也不引入图像生成的编码开销；
* 干扰线/噪点/字符抖动都在 SVG 里表达，防机器识别的效果与位图等价（对这个规模的系统足够）。

存储与生命周期：
* ``cap:{captcha_id}`` → 4 位数字，TTL 120s（Redis 过期即失效）；
* **校验即消费**：无论对错都删除，防止对同一张图暴力试错（错误后前端自动刷新）；
* Redis 不可用时：生成报 ``UpstreamError``（无法出题），校验**降级放行**并记 warning
  ——本地开发/演示环境不让“缓存故障”把登录彻底锁死，生产部署时应把该降级关掉（见验收记录遗留）。
"""

from __future__ import annotations

import base64
import random
import secrets

from app.core.logger import get_logger
from config import Settings, get_settings

logger = get_logger("lawanswer.captcha")

CAPTCHA_LEN = 4
CAPTCHA_TTL_S = 120
_KEY_PREFIX = "cap:"

#: 字符配色（深色系，保证与浅底对比度）
_COLORS = ("#2B4ACB", "#1C2333", "#0B7285", "#5F3DC4", "#C2255C")

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="0 0 120 40">'
    '<rect width="120" height="40" rx="8" fill="#F2F5FC"/>'
    "{noise}"
    "{chars}"
    "</svg>"
)


def _redis(settings: Settings | None = None):
    from app.db.redis_client import get_redis

    try:
        return get_redis(settings or get_settings())
    except Exception as exc:  # noqa: BLE001 - Redis 故障不应在导入期炸掉
        logger.warning("验证码 Redis 不可用：%s", str(exc)[:150])
        return None


def _noise(rng: random.Random) -> str:
    parts: list[str] = []
    for _ in range(3):
        x1, y1 = rng.randint(0, 20), rng.randint(4, 36)
        x2, y2 = rng.randint(100, 120), rng.randint(4, 36)
        parts.append(
            f'<path d="M{x1} {y1} Q{rng.randint(40, 80)} {rng.randint(0, 40)} {x2} {y2}" '
            f'stroke="{rng.choice(_COLORS)}" stroke-opacity="0.35" stroke-width="1.4" fill="none"/>'
        )
    for _ in range(18):
        parts.append(
            f'<circle cx="{rng.randint(2, 118)}" cy="{rng.randint(2, 38)}" r="{rng.uniform(0.6, 1.6):.1f}" '
            f'fill="{rng.choice(_COLORS)}" fill-opacity="0.28"/>'
        )
    return "".join(parts)


def _chars(code: str, rng: random.Random) -> str:
    parts: list[str] = []
    for idx, ch in enumerate(code):
        x = 16 + idx * 26
        y = rng.randint(26, 32)
        rotate = rng.randint(-16, 16)
        size = rng.randint(22, 26)
        parts.append(
            f'<text x="{x}" y="{y}" font-family="ui-monospace,Consolas,monospace" font-size="{size}" '
            f'font-weight="700" fill="{rng.choice(_COLORS)}" '
            f'transform="rotate({rotate} {x} {y})">{ch}</text>'
        )
    return "".join(parts)


def render_svg(code: str, *, seed: int | None = None) -> str:
    """把验证码文本渲染成 SVG 字符串（纯函数，测试可注入 seed）。"""
    rng = random.Random(seed if seed is not None else secrets.randbelow(10**9))
    return _SVG_TEMPLATE.format(noise=_noise(rng), chars=_chars(code, rng))


def new_captcha(*, settings: Settings | None = None) -> tuple[str, str]:
    """生成验证码，返回 ``(captcha_id, data_uri)``。

    ``data_uri`` 形如 ``data:image/svg+xml;base64,...``，前端直接塞进 ``<img src>``。
    出题失败（Redis 不可用）抛 ``UpstreamError``——没有存储就无法校验，不能假装成功。
    """
    from app.core.errors import UpstreamError

    s = settings or get_settings()
    r = _redis(s)
    if r is None:
        raise UpstreamError("验证码服务暂不可用，请稍后重试")
    captcha_id = secrets.token_urlsafe(12)
    code = "".join(secrets.choice("0123456789") for _ in range(CAPTCHA_LEN))
    try:
        r.setex(_KEY_PREFIX + captcha_id, s.captcha_ttl_s, code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("验证码写入 Redis 失败：%s", str(exc)[:150])
        raise UpstreamError("验证码服务暂不可用，请稍后重试") from exc
    svg = render_svg(code)
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return captcha_id, uri


def verify_captcha(captcha_id: str, code: str, *, settings: Settings | None = None) -> bool:
    """校验验证码（**一次性**：校验即删除，防同一张图暴力试错）。

    Redis 不可用时降级放行（记 warning），避免缓存故障把登录彻底锁死。
    """
    s = settings or get_settings()
    r = _redis(s)
    if r is None:
        logger.warning("验证码校验降级放行（Redis 不可用）")
        return True
    key = _KEY_PREFIX + (captcha_id or "")
    try:
        expected = r.get(key)
        r.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("验证码校验降级放行（Redis 异常）：%s", str(exc)[:150])
        return True
    if not expected:
        return False
    if isinstance(expected, bytes):
        expected = expected.decode("utf-8", "ignore")
    return secrets.compare_digest(str(expected), (code or "").strip())
