"""LawAnswer 自动化演示脚本（阶段 8 交付物）。

跑 10 个典型问题，覆盖设计方案 §6.3 的全部 6 条路由分支，
每个问题都真实走一遍「意图识别 → 路由 → 检索 → 生成」全链路：

    greeting         问候闲聊：固定话术，不调 LLM、不调数据源
    pure_structured  高频问答精确命中：MySQL 标准答案回放，不碰 Milvus
    pure_vector      纯法条问题：仅 Milvus 混合检索 + 重排
    hybrid           命中话题但无精确答案：结构化答案作结论 + 法条作依据
    cross_law        跨法域问题：多知识库检索，prompt 要求分法条陈述
    no_data          与法律无关：引导话术，不调任何数据源（不编造）

用法::

    # 后端已在 http://127.0.0.1:8101 运行
    uv run python -m scripts.demo

    # 指定地址 / 只看摘要 / 打印完整回答 / 导出 JSON
    uv run python -m scripts.demo --base-url http://127.0.0.1:8101
    uv run python -m scripts.demo -v
    uv run python -m scripts.demo --json eval/results/demo.json

    # 只跑第 3 条（调试用）
    uv run python -m scripts.demo --only 3

说明：阶段 8 优化后 ``/api/intent/preview`` 要求登录（JWT）。脚本启动时会
自动用演示账号（可 ``--username/--password`` 或 ``LAWANSWER_DEMO_USER/PASS``
环境变量覆盖）走「验证码 → 登录/注册」流程拿到 token，问答与预览均携带。

校验口径（三条全过才算这条用例通过）：
    1. SSE 流正常收敛到 done，且过程中没有 error 事件；
    2. 回答正文非空；
    3. 实际路由 == 期望路由（``--loose`` 模式下降级为仅提示）。
另外按期望断言来源数下界（向量类路由必须带来源，no_data/greeting 必须为 0）。

退出码：全部通过 0，否则 1。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8101"

# Windows 控制台默认 GBK，中文回答直接打印会 UnicodeEncodeError
try:  # pragma: no cover - 环境相关
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 - 非 Windows 或已包装的流没有 reconfigure
    pass


# ---------------------------------------------------------------------------
# 用例定义
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DemoCase:
    """一条演示用例。

    :param query: 用户输入
    :param expect_route: 期望命中的路由分支
    :param min_sources: 来源数下界（0 表示不校验）
    :param max_sources: 来源数上界（None 表示不校验）；用于断言 no_data 不返回来源
    :param point: 这条用例要演示的能力点（打印到报告里）
    """

    query: str
    expect_route: str
    min_sources: int = 0
    max_sources: int | None = None
    point: str = ""


#: 10 条用例，6 条路由全覆盖（greeting 1 / no_data 2 / pure_structured 2 /
#: pure_vector 2 / hybrid 2 / cross_law 1）
CASES: tuple[DemoCase, ...] = (
    DemoCase(
        "你好",
        "greeting",
        max_sources=0,
        point="问候直接固定话术，零 LLM 调用、零检索",
    ),
    DemoCase(
        "试用期最长可以约定多久？",
        "pure_structured",
        min_sources=1,
        point="高频问答精确命中：MySQL 标准答案回放，完全不碰 Milvus",
    ),
    DemoCase(
        "加班费怎么算？",
        "pure_structured",
        min_sources=1,
        point="同上，走缓存链路（Redis 命中后 ≤50ms）",
    ),
    DemoCase(
        "哺乳期能不能安排加班？",
        "hybrid",
        min_sources=1,
        point="命中话题但无精确答案：结构化答案作结论段 + 法条作依据段",
    ),
    DemoCase(
        "用人单位违法解除劳动合同要赔多少钱？",
        "hybrid",
        min_sources=1,
        point="混合链路：需计算的赔偿标准 + 法条依据双源合并",
    ),
    DemoCase(
        "民法典关于格式条款无效是怎么规定的？",
        "pure_vector",
        min_sources=1,
        point="纯法条问题：仅向量检索（bge-m3 稠密+稀疏 RRF 融合 + 重排）",
    ),
    DemoCase(
        "离婚后子女抚养权怎么确定？",
        "pure_vector",
        min_sources=1,
        point="民法典库检索，验证法域预过滤只查 kb_civil",
    ),
    DemoCase(
        "劳动合同法关于试用期的规定和民法典违约责任有什么关系？",
        "cross_law",
        min_sources=2,
        point="跨法域：两库同时检索，prompt 要求分法条陈述",
    ),
    DemoCase(
        "帮我推荐一部电影",
        "no_data",
        max_sources=0,
        point="与法律无关：引导话术，不调任何数据源（不编造）",
    ),
    DemoCase(
        "今天天气怎么样？",
        "no_data",
        max_sources=0,
        point="同上，验证越界问题不会误触发检索与生成",
    ),
)


@dataclass
class CaseResult:
    """单条用例的执行结果。"""

    index: int
    query: str
    expect_route: str
    actual_route: str = ""
    sources: int = 0
    ttft_ms: int | None = None
    latency_ms: int | None = None
    degraded: Any = False
    answer: str = ""
    errors: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures and not self.errors


# ---------------------------------------------------------------------------
# HTTP / SSE 客户端
# ---------------------------------------------------------------------------
def _post_json(url: str, body: dict, timeout: float, token: str | None = None) -> dict:
    """POST JSON 并返回解析后的 dict（可选携带 Bearer token）。"""
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _get_json(url: str, timeout: float) -> dict:
    """GET 并返回解析后的 dict。"""
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _decode(line: bytes) -> str:
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


def consume_sse(
    base_url: str, query: str, timeout: float, token: str | None = None
) -> dict:
    """消费 ``POST /api/chat/stream`` 的 SSE 流，汇总成结构化结果。

    :return: ``{meta, sources, answer, done, errors, degraded}``
    """
    url = f"{base_url}/api/chat/stream"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": query}, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    out: dict[str, Any] = {
        "meta": {},
        "sources": [],
        "answer": "",
        "done": {},
        "errors": [],
        "degraded": False,
    }
    deltas = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        event, data_lines = "", []
        for raw_line in resp:
            line = _decode(raw_line)
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data_lines.append(line[6:])
            elif line == "" and event:
                payload = "\n".join(data_lines)
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"text": payload}
                if event == "meta":
                    # Milvus 降级时会追加第二条 meta，后者更权威，直接覆盖
                    out["meta"] = data
                    out["degraded"] = data.get("degraded", out["degraded"])
                elif event == "sources":
                    out["sources"] = data.get("sources", [])
                elif event == "delta":
                    out["answer"] += data.get("text", "")
                    deltas += 1
                elif event == "done":
                    out["done"] = data
                elif event == "error":
                    out["errors"].append(f"{data.get('code')}: {data.get('message')}")
                event, data_lines = "", []
    out["deltas"] = deltas
    return out


# ---------------------------------------------------------------------------
# 演示账号登录（阶段 8 起 /api/intent/preview 要求 JWT）
# ---------------------------------------------------------------------------
DEMO_USER = "demo_user"
DEMO_PASS = "demo-pass-2026"


def _get_captcha(base_url: str, timeout: float) -> tuple[str, str]:
    """取一张验证码并解出 4 位数字：SVG 里每个字符就是一个 ``<text>`` 节点。

    与前端走查脚本同一解码方式（不做 OCR，也顺带验证了出题链路）。
    """
    resp = _post_json(f"{base_url}/api/auth/captcha", {}, timeout)
    raw = resp.get("image", "")
    if "base64," not in raw:
        raise RuntimeError("验证码接口未返回 base64 SVG")
    svg = base64.b64decode(raw.split("base64,", 1)[1]).decode("utf-8", "ignore")
    code = "".join(re.findall(r">([0-9])</text>", svg))
    if len(code) != 4:
        raise RuntimeError(f"验证码解析失败: {code!r}")
    return resp["captcha_id"], code


def _http_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:200]
    except Exception:  # noqa: BLE001
        return ""


def ensure_token(base_url: str, username: str, password: str, timeout: float) -> str:
    """确保演示账号存在并返回 JWT；账号不存在则先注册（走真实验证码链路）。"""
    try:
        check = _get_json(f"{base_url}/api/auth/check-username?username={username}", timeout)
    except urllib.error.HTTPError as exc:
        print(f"查询账号状态失败：HTTP {exc.code} {_http_detail(exc)}")
        raise
    if check.get("available"):
        cid, code = _get_captcha(base_url, timeout)
        try:
            _post_json(
                f"{base_url}/api/auth/register",
                {
                    "username": username,
                    "password": password,
                    "captcha_id": cid,
                    "captcha_code": code,
                },
                timeout,
            )
            print(f"已注册演示账号：{username}")
        except urllib.error.HTTPError as exc:
            print(f"注册演示账号失败：HTTP {exc.code} {_http_detail(exc)}")
            raise
    cid, code = _get_captcha(base_url, timeout)
    try:
        resp = _post_json(
            f"{base_url}/api/auth/login",
            {
                "username": username,
                "password": password,
                "captcha_id": cid,
                "captcha_code": code,
            },
            timeout,
        )
    except urllib.error.HTTPError as exc:
        print(f"演示账号登录失败：HTTP {exc.code} {_http_detail(exc)}")
        raise
    return resp["access_token"]


# ---------------------------------------------------------------------------
# 执行与校验
# ---------------------------------------------------------------------------
def run_case(
    index: int,
    case: DemoCase,
    base_url: str,
    timeout: float,
    *,
    with_preview: bool = True,
    token: str | None = None,
) -> CaseResult:
    """跑单条用例：先取路由预览，再走真实 SSE 问答。"""
    res = CaseResult(index=index, query=case.query, expect_route=case.expect_route)
    started = time.perf_counter()

    if with_preview:
        try:
            plan = _post_json(
                f"{base_url}/api/intent/preview",
                {"query": case.query},
                timeout,
                token=token,
            )
            res.actual_route = plan.get("route", {}).get("route", "")
        except Exception as exc:  # noqa: BLE001 - 预览失败不阻断，以 SSE meta 为准
            res.errors.append(f"preview: {type(exc).__name__}: {exc}")

    try:
        streamed = consume_sse(base_url, case.query, timeout, token=token)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        res.errors.append(f"stream HTTP {exc.code}: {detail}")
        res.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return res
    except Exception as exc:  # noqa: BLE001 - 网络/超时统一收敛为用例失败
        res.errors.append(f"stream: {type(exc).__name__}: {exc}")
        res.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return res

    meta = streamed["meta"]
    # SSE meta 是链路真实采用的路由，优先级高于 preview
    if meta.get("route"):
        res.actual_route = meta["route"]
    res.sources = len(streamed["sources"])
    res.answer = streamed["answer"].strip()
    res.degraded = streamed["degraded"]
    done = streamed["done"]
    res.ttft_ms = done.get("ttft_ms")
    res.latency_ms = done.get("latency_ms")
    res.errors.extend(streamed["errors"])
    res.elapsed_ms = int((time.perf_counter() - started) * 1000)

    # ---- 校验 ----
    if not done:
        res.failures.append("SSE 未收敛到 done 事件")
    if res.errors:
        res.failures.append("链路出现 error 事件")
    if not res.answer:
        res.failures.append("回答为空")
    if res.sources < case.min_sources:
        res.failures.append(f"来源数 {res.sources} < 期望下界 {case.min_sources}")
    if case.max_sources is not None and res.sources > case.max_sources:
        res.failures.append(f"来源数 {res.sources} > 期望上界 {case.max_sources}")
    return res


def apply_route_check(res: CaseResult, case: DemoCase, *, loose: bool) -> None:
    """路由一致性校验；``loose`` 模式下只警告不判失败。"""
    if res.actual_route and res.actual_route != case.expect_route:
        msg = f"路由 {res.actual_route} != 期望 {case.expect_route}"
        if loose:
            res.errors.append(f"[warn] {msg}")
        else:
            res.failures.append(msg)


def print_result(res: CaseResult, case: DemoCase, *, verbose: bool) -> None:
    """打印单条结果。"""
    mark = "PASS" if res.passed else "FAIL"
    route_ok = "==" if res.actual_route == case.expect_route else "!="
    print(
        f"[{mark}] {res.index:>2} {res.query[:30]:<32} "
        f"route {res.actual_route or '-':<16} {route_ok} {case.expect_route:<16} "
        f"src={res.sources:<2} ttft={_fmt(res.ttft_ms)} total={_fmt(res.latency_ms)}"
    )
    print(f"       演示点：{case.point}")
    if res.failures:
        print(f"       失败：{'; '.join(res.failures)}")
    if verbose:
        preview = res.answer.replace("\n", " ")
        print(f"       回答：{preview[:400]}{'…' if len(preview) > 400 else ''}")
    print()


def _fmt(value: int | None) -> str:
    return "-" if value is None else f"{value}ms"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LawAnswer 自动化演示（10 问覆盖 6 路由）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"后端地址（默认 {DEFAULT_BASE_URL}）")
    parser.add_argument("--timeout", type=float, default=120.0, help="单条用例超时秒数（默认 120）")
    parser.add_argument("--only", type=int, default=0, help="只跑指定编号的用例（1-based）")
    parser.add_argument("--loose", action="store_true", help="路由不一致只警告不判失败")
    parser.add_argument("--no-preview", action="store_true", help="跳过 /api/intent/preview，只跑 SSE")
    parser.add_argument("--username", default=os.environ.get("LAWANSWER_DEMO_USER", DEMO_USER),
                        help=f"演示账号（默认 {DEMO_USER}，可用 LAWANSWER_DEMO_USER 覆盖）")
    parser.add_argument("--password", default=os.environ.get("LAWANSWER_DEMO_PASS", DEMO_PASS),
                        help="演示账号密码（默认内置，可用 LAWANSWER_DEMO_PASS 覆盖）")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印回答正文")
    parser.add_argument("--json", dest="json_out", default="", help="结果写入 JSON 文件")
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    cases = [c for c in CASES]
    if args.only:
        if not 1 <= args.only <= len(CASES):
            print(f"--only 需在 1..{len(CASES)} 之间", file=sys.stderr)
            return 2
        cases = [CASES[args.only - 1]]
        indices = [args.only]
    else:
        indices = list(range(1, len(CASES) + 1))

    # 服务可达性预检，避免 10 条全超时才暴露问题
    try:
        with urllib.request.urlopen(f"{base_url}/api/health/live", timeout=10) as r:
            health = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"后端不可达：{base_url}（{type(exc).__name__}: {exc}）", file=sys.stderr)
        print("请先启动后端：uv run python -m app.server", file=sys.stderr)
        return 2
    print(f"后端健康：{health.get('status')}（{base_url}）\n")

    token = ensure_token(base_url, args.username, args.password, args.timeout)
    print(f"已登录演示账号：{args.username}\n")

    started = time.perf_counter()
    results: list[CaseResult] = []
    for idx, case in zip(indices, cases):
        res = run_case(
            idx, case, base_url, args.timeout,
            with_preview=not args.no_preview,
            token=token,
        )
        apply_route_check(res, case, loose=args.loose)
        results.append(res)
        print_result(res, case, verbose=args.verbose)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    elapsed = time.perf_counter() - started

    print("=" * 92)
    print(f"演示结果：{passed}/{total} 通过   总耗时 {elapsed:.1f}s")
    if passed < total:
        print("失败用例：")
        for r in results:
            if not r.passed:
                print(f"  #{r.index} {r.query} -> {'; '.join(r.failures + r.errors)}")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "index": r.index,
                "query": r.query,
                "expect_route": r.expect_route,
                "actual_route": r.actual_route,
                "sources": r.sources,
                "ttft_ms": r.ttft_ms,
                "latency_ms": r.latency_ms,
                "degraded": r.degraded,
                "passed": r.passed,
                "failures": r.failures,
                "errors": r.errors,
                "answer": r.answer,
            }
            for r in results
        ]
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果已写入：{out_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
