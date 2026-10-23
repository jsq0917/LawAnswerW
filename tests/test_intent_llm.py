"""意图识别 LLM 路径单测：正常解析、字段校正、非法 JSON 降级、超时降级。

全部用假 LLM 客户端，不调真实 DashScope。
"""

from __future__ import annotations

import json

import pytest

from app.orchestration.intent import (
    INTENT_SYSTEM_PROMPT,
    _build_messages,
    _coerce_sub_queries,
    _strip_json_fences,
    _validate,
    recognize_intent,
)
from app.orchestration.models import IntentResult

FENCE = "`" * 3


def _llm_response(content: str):
    """假 LLM 响应（与 app.generation.llm_client.LLMResponse 同构）。"""
    from app.generation.llm_client import LLMResponse

    return LLMResponse(content=content, model="qwen-max", latency_ms=100, usage={})


class FakeClient:
    """返回固定内容的假客户端；可模拟异常。"""

    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self.content = content
        self.exc = exc
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.exc is not None:
            raise self.exc
        return _llm_response(self.content)


VALID_JSON = json.dumps(
    {
        "intent": "labor_law",
        "rewrite_type": "direct",
        "sub_queries": ["试用期最长期限"],
        "complexity": "simple",
        "hit_structured": True,
        "structured_topic": "试用期",
        "law_hints": ["中华人民共和国劳动合同法"],
        "need_history": False,
    },
    ensure_ascii=False,
)


class TestStripJsonFences:
    def test_plain_json(self):
        assert _strip_json_fences('{"a": 1}') == '{"a": 1}'

    def test_markdown_fenced(self):
        text = FENCE + "json\n{\"a\": 1}\n" + FENCE
        assert _strip_json_fences(text) == '{"a": 1}'

    def test_with_surrounding_prose(self):
        text = "好的，结果如下：\n{\"a\": 1}\n希望有帮助"
        assert _strip_json_fences(text) == '{"a": 1}'

    def test_no_braces_returns_stripped(self):
        assert _strip_json_fences("  not json  ") == "not json"


class TestCoerceSubQueries:
    def test_string_becomes_single(self):
        assert _coerce_sub_queries("一个问题", "原问题") == ["一个问题"]

    def test_dedupe_and_cap_at_three(self):
        assert _coerce_sub_queries(["a", "a", "b", "c", "d"], "原问题") == ["a", "b", "c"]

    def test_empty_falls_back_to_query(self):
        assert _coerce_sub_queries([], "原问题") == ["原问题"]
        assert _coerce_sub_queries(None, "原问题") == ["原问题"]

    def test_blank_items_dropped(self):
        assert _coerce_sub_queries(["  ", "b"], "原问题") == ["b"]


class TestValidate:
    def test_valid_passthrough(self):
        result = _validate(json.loads(VALID_JSON), "原问题")
        assert result.intent == "labor_law"
        assert result.rewrite_type == "direct"
        assert result.sub_queries == ["试用期最长期限"]
        assert result.complexity == "simple"
        assert result.hit_structured is True
        assert result.structured_topic == "试用期"
        assert result.law_hints == ["中华人民共和国劳动合同法"]
        assert result.source == "llm"

    def test_invalid_enum_falls_back(self):
        data = {
            "intent": "martian",
            "rewrite_type": "translate",
            "complexity": "very_hard",
            "sub_queries": ["ok"],
            "law_hints": ["中华人民共和国劳动合同法"],
        }
        result = _validate(data, "原问题")
        assert result.intent == "general"
        assert result.rewrite_type == "direct"
        assert result.complexity == "medium"

    def test_unknown_topic_nulled(self):
        data = {
            "intent": "labor_law",
            "hit_structured": True,
            "structured_topic": "不存在的话题",
            "law_hints": ["中华人民共和国劳动合同法"],
        }
        result = _validate(data, "原问题")
        assert result.structured_topic is None
        # topic 不可信但 hit 标记保留（调用方仍会做一次廉价问答查询）
        assert result.hit_structured is True

    def test_missing_law_hints_backfilled_by_intent(self):
        data = {"intent": "labor_law", "sub_queries": ["q"]}
        result = _validate(data, "原问题")
        assert result.law_hints == ["中华人民共和国劳动合同法", "中华人民共和国劳动法"]

    def test_fake_law_filtered(self):
        data = {
            "intent": "civil_code",
            "law_hints": ["中华人民共和国劳动合同法", "假法律"],
        }
        result = _validate(data, "原问题")
        assert "假法律" not in result.law_hints


class TestRecognizeIntentLLM:
    def test_success(self):
        client = FakeClient(VALID_JSON)
        result = recognize_intent("试用期最长多久", client=client)
        assert result.source == "llm"
        assert result.intent == "labor_law"
        assert result.sub_queries == ["试用期最长期限"]
        # JSON 模式必须开启
        assert client.calls[0]["json_mode"] is True

    def test_history_included_in_messages(self):
        client = FakeClient(VALID_JSON)
        recognize_intent(
            "那个多久",
            history=[
                {"role": "user", "content": "试用期最长多久"},
                {"role": "assistant", "content": "六个月"},
            ],
            client=client,
        )
        messages = client.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert "试用期最长多久" in messages[1]["content"]
        assert "那个多久" in messages[1]["content"]

    def test_invalid_json_degrades_to_rules(self):
        client = FakeClient(FENCE + "json\n{not valid")
        result = recognize_intent("试用期最长多久", client=client)
        assert result.source == "rules"
        assert result.intent == "labor_law"
        assert result.structured_topic == "试用期"

    def test_json_not_object_degrades(self):
        client = FakeClient("[1, 2, 3]")
        result = recognize_intent("试用期最长多久", client=client)
        assert result.source == "rules"

    def test_timeout_degrades(self):
        client = FakeClient(exc=TimeoutError("read timeout"))
        result = recognize_intent("试用期最长多久", client=client)
        assert result.source == "rules"

    def test_network_error_degrades(self):
        client = FakeClient(exc=ConnectionError("connection refused"))
        result = recognize_intent("试用期最长多久", client=client)
        assert result.source == "rules"

    def test_empty_content_degrades(self):
        client = FakeClient("")
        result = recognize_intent("试用期最长多久", client=client)
        assert result.source == "rules"

    def test_fenced_valid_json_parsed(self):
        payload = json.dumps(
            {
                "intent": "civil_code",
                "rewrite_type": "direct",
                "sub_queries": ["离婚财产分割"],
                "complexity": "medium",
                "hit_structured": False,
                "structured_topic": None,
                "law_hints": ["中华人民共和国民法典"],
                "need_history": False,
            },
            ensure_ascii=False,
        )
        client = FakeClient(FENCE + "json\n" + payload + "\n" + FENCE)
        result = recognize_intent("离婚财产怎么分", client=client)
        assert result.source == "llm"
        assert result.intent == "civil_code"

    def test_empty_query_uses_rules(self):
        client = FakeClient(VALID_JSON)
        result = recognize_intent("   ", client=client)
        assert result.source == "rules"
        assert client.calls == []


class TestBuildMessages:
    def test_no_history(self):
        messages = _build_messages("问题", None, max_turns=2)
        assert len(messages) == 2
        assert messages[0]["content"] == INTENT_SYSTEM_PROMPT
        assert "问题" in messages[1]["content"]
        assert "最近对话" not in messages[1]["content"]

    def test_history_window_truncated(self):
        history = [{"role": "user", "content": f"第{i}轮问题"} for i in range(10)]
        messages = _build_messages("当前问题", history, max_turns=2)
        # 只带最近 2 轮（4 条消息）：第 6-9 轮
        assert "第9轮问题" in messages[1]["content"]
        assert "第6轮问题" in messages[1]["content"]
        assert "第0轮问题" not in messages[1]["content"]
        assert "第5轮问题" not in messages[1]["content"]


class TestIntentClient:
    """意图客户端必须与主链路一致地下发 thinking（DeepSeek 默认为 enabled）。"""

    @staticmethod
    def _settings(**kwargs):
        from types import SimpleNamespace

        base = {
            "llm_base_url": "https://api.deepseek.com",
            "llm_default_model": "deepseek-flash",
            "intent_timeout_s": 5,
            "intent_max_retries": 1,
            "llm_thinking": "disabled",
        }
        base.update(kwargs)
        return SimpleNamespace(**base)

    def _client(self, monkeypatch, settings):
        import app.orchestration.intent as intent_mod

        monkeypatch.setattr(intent_mod, "get_llm_api_key", lambda: "sk-test")
        monkeypatch.setattr(intent_mod, "get_settings", lambda: settings)
        intent_mod.reset_intent_client()
        try:
            return intent_mod.get_intent_client()
        finally:
            intent_mod.reset_intent_client()

    def test_thinking_propagated(self, monkeypatch):
        client = self._client(monkeypatch, self._settings())
        assert client._thinking == "disabled"

    def test_thinking_blank_when_unset(self, monkeypatch):
        """DashScope/OpenAI 无该参数，留空时不能凭空发送。"""
        client = self._client(monkeypatch, self._settings(llm_thinking=""))
        assert client._thinking == ""
