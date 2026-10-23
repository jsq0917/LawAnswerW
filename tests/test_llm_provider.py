"""大模型 provider 配置测试（阶段 6 补充，全离线）。

背景：项目原先只认 DashScope 的 ``DASHSCOPE_API_KEY``；用户只有 DeepSeek key，
而两者都是 OpenAI 兼容协议，因此把密钥解析改成"通用名优先、厂商名兜底"，
这里锁定三件事：优先级、缺失提示、以及 .env 里写密钥仍被拒绝。
"""

from __future__ import annotations

import pytest

import config
from config import (
    LLM_API_KEY_ENV_NAMES,
    SecretInDotEnvError,
    assert_secrets_not_in_dotenv,
    get_llm_api_key,
    get_llm_api_key_env,
    require_llm_api_key,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """清掉三个密钥变量，避免本机环境干扰断言。"""
    for name in LLM_API_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield


class TestKeyResolution:
    def test_empty_when_nothing_set(self):
        assert get_llm_api_key() == ""
        assert get_llm_api_key_env() == ""

    @pytest.mark.parametrize("name", LLM_API_KEY_ENV_NAMES)
    def test_each_name_supported(self, monkeypatch, name):
        monkeypatch.setenv(name, "sk-from-" + name.lower())
        assert get_llm_api_key() == "sk-from-" + name.lower()
        assert get_llm_api_key_env() == name

    def test_priority_llm_first(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        monkeypatch.setenv("LLM_API_KEY", "sk-generic")
        assert get_llm_api_key() == "sk-generic"
        assert get_llm_api_key_env() == "LLM_API_KEY"

    def test_priority_deepseek_over_dashscope(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        assert get_llm_api_key() == "sk-deepseek"
        assert get_llm_api_key_env() == "DEEPSEEK_API_KEY"

    def test_blank_value_ignored(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "   ")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        assert get_llm_api_key() == "sk-deepseek"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "  sk-deepseek  ")
        assert get_llm_api_key() == "sk-deepseek"

    def test_require_raises_with_actionable_hint(self):
        with pytest.raises(config.ConfigError) as exc:
            require_llm_api_key()
        msg = str(exc.value)
        assert "DEEPSEEK_API_KEY" in msg
        assert "LLM_BASE_URL" in msg
        assert "deepseek-flash" in msg
        assert "LLM_THINKING" in msg

    def test_require_returns_key(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
        assert require_llm_api_key() == "sk-deepseek"


class TestDotEnvStillRejectsKeys:
    """密钥只能放系统环境变量：任意一种变量名写进 .env 都要拒绝启动。"""

    @pytest.mark.parametrize("name", LLM_API_KEY_ENV_NAMES)
    def test_rejects_key_lines(self, tmp_path, name):
        env = tmp_path / ".env"
        env.write_text(f"{name}=sk-should-not-be-here\n", encoding="utf-8")
        with pytest.raises(SecretInDotEnvError) as exc:
            assert_secrets_not_in_dotenv(env)
        assert name in str(exc.value)

    def test_comment_is_fine(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# DEEPSEEK_API_KEY=sk-xxx 密钥请放系统环境变量\nLLM_BASE_URL=x\n", encoding="utf-8")
        assert_secrets_not_in_dotenv(env)  # 不抛


class TestClientWiring:
    def test_client_uses_settings_and_key(self, monkeypatch):
        """密钥、base_url、模型三者按配置装配到 OpenAI 客户端。"""
        from app.generation import llm_client as mod

        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(mod, "OpenAI", FakeOpenAI)
        mod.reset_llm_client()
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")

        from config import Settings

        settings = Settings(
            _env_file=None,
            llm_base_url="https://api.deepseek.com/v1",
            llm_default_model="deepseek-chat",
        )
        client = mod.get_llm_client(settings)
        assert captured["api_key"] == "sk-deepseek"
        assert captured["base_url"] == "https://api.deepseek.com/v1"
        assert captured["max_retries"] == 0  # 重试由本层统一负责
        assert client._model == "deepseek-chat"
        mod.reset_llm_client()

    def test_missing_key_raises_config_error(self, monkeypatch):
        from app.generation import llm_client as mod

        mod.reset_llm_client()
        with pytest.raises(config.ConfigError):
            mod.get_llm_client()
        mod.reset_llm_client()


class TestHealthSurface:
    def test_health_exposes_provider_without_secret(self, monkeypatch):
        """健康检查只暴露 base_url/模型/来源变量名，不暴露密钥。"""
        from fastapi.testclient import TestClient

        from app.api import health as health_mod
        from app.main import app

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-value-should-not-leak")

        def fake_check():
            return {"status": "ok", "latency_ms": 1}

        monkeypatch.setattr(health_mod, "check_mysql", fake_check)
        monkeypatch.setattr(health_mod, "check_redis", fake_check)
        monkeypatch.setattr(health_mod, "check_milvus", fake_check)

        body = TestClient(app).get("/api/health").json()
        assert body["llm_key_configured"] is True
        assert body["llm_key_source"] == "DEEPSEEK_API_KEY"
        assert "llm_base_url" in body and "llm_model" in body
        assert "sk-secret-value-should-not-leak" not in str(body)


class TestDeepSeekQuirks:
    """DeepSeek 与 OpenAI 的三处差异（官方文档实测对齐）。

    1. 模型名是 ``deepseek-flash`` / ``deepseek-v4-pro``（旧的 deepseek-chat 已不在文档枚举内）；
    2. ``thinking`` 默认 **enabled**：思考模式下 temperature 无效、默认输出 8K→64K，
       故 RAG 场景显式传 ``disabled``；
    3. 流式 usage 挂在**最后一个内容块**（choices 非空），不是独立的空块。
    """

    def _client(self, thinking: str = ""):
        from app.generation.llm_client import LLMClient

        client = LLMClient(api_key="sk-test", base_url="https://api.deepseek.com", model="deepseek-flash", thinking=thinking)
        captured: dict = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return iter([])

        class FakeChat:
            def __init__(self):
                self.completions = FakeCompletions()

        class FakeInner:
            def __init__(self):
                self.chat = FakeChat()

        client._client = FakeInner()
        return client, captured

    def test_thinking_disabled_sent_as_extra_body(self):
        client, captured = self._client("disabled")
        try:
            client.chat([{"role": "user", "content": "hi"}])
        except Exception:
            pass  # 空流会在解析处中止，但 kwargs 已捕获
        assert captured.get("extra_body") == {"thinking": {"type": "disabled"}}
        assert captured["model"] == "deepseek-flash"

    def test_thinking_empty_omits_param(self):
        """DashScope/OpenAI 不认识 thinking，留空即不发送。"""
        client, captured = self._client("")
        try:
            list(client.chat_stream([{"role": "user", "content": "hi"}]))
        except Exception:
            pass
        assert "extra_body" not in captured
        assert captured["stream"] is True

    def test_stream_usage_on_last_content_chunk(self):
        """DeepSeek 风格：usage 与 finish_reason 同处最后一个内容块。"""
        from types import SimpleNamespace

        from app.generation.llm_client import LLMClient

        client = LLMClient(api_key="sk-test", base_url="https://api.deepseek.com", model="deepseek-flash")

        def chunk(text=None, finish=None, usage=None, empty_choices=False):
            choices = [] if empty_choices else [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)]
            return SimpleNamespace(choices=choices, usage=usage)

        usage = SimpleNamespace(prompt_tokens=120, completion_tokens=8, total_tokens=128)
        stream = [
            chunk(text="结论："),
            chunk(text="六个月"),
            chunk(text="", finish="stop", usage=usage),  # DeepSeek：这一块同时带 usage
        ]

        class FakeCompletions:
            def create(self, **kwargs):
                return iter(stream)

        client._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        out = list(client.chat_stream([{"role": "user", "content": "hi"}]))
        assert "".join(c.text for c in out) == "结论：六个月"
        assert out[-1].usage["total_tokens"] == 128
        assert out[-1].finish_reason == "stop"

    def test_stream_usage_on_separate_block(self):
        """OpenAI 风格：独立的 usage-only 块也要能读到（兼容路径）。"""
        from types import SimpleNamespace

        from app.generation.llm_client import LLMClient

        client = LLMClient(api_key="sk-test", base_url="https://api.openai.com/v1", model="gpt-4o-mini")
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12)

        def chunk(text=None, finish=None, usage=None, empty_choices=False):
            choices = [] if empty_choices else [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)]
            return SimpleNamespace(choices=choices, usage=usage)

        stream = [chunk(text="好"), chunk(finish="stop"), chunk(empty_choices=True, usage=usage)]

        class FakeCompletions:
            def create(self, **kwargs):
                return iter(stream)

        client._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        out = list(client.chat_stream([{"role": "user", "content": "hi"}]))
        assert out[-1].usage["total_tokens"] == 12

    def test_defaults_target_deepseek(self):
        """代码默认值与 .env 保持一致，避免"配置写 qwen、实际打 DeepSeek"。

        注意：uv run 会自动把 .env 注入进程环境，所以这里读**字段默认值**，
        而不是实例值（实例值会被 .env 覆盖，属于另一回事）。
        """
        from config import Settings

        fields = Settings.model_fields
        assert fields["llm_base_url"].default == "https://api.deepseek.com"
        assert fields["llm_default_model"].default == "deepseek-flash"
        # thinking 代码默认留空（只有 DeepSeek 需要，由 .env 显式开启）
        assert fields["llm_thinking"].default == ""
