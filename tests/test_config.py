"""配置层测试：默认值、环境变量覆盖、跨字段校验、密钥安全红线。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import (
    BASE_DIR,
    ConfigError,
    SecretInDotEnvError,
    assert_secrets_not_in_dotenv,
    get_dashscope_api_key,
    mask_secret,
    require_dashscope_api_key,
)


class TestDefaults:
    def test_port_defaults_follow_docker_compose(self, settings_factory):
        """默认端口必须与 docker-compose 的偏移端口一致，否则起不来。"""
        s = settings_factory()
        assert s.server_port == 8101
        assert s.mysql_port == 3307
        assert s.redis_port == 6380
        assert s.redis_db == 3
        assert s.milvus_port == 19531
        assert s.milvus_db == "lawanswer"
        assert s.milvus_collection == "law_chunks"

    def test_retrieval_defaults(self, settings_factory):
        s = settings_factory()
        r = s.retrieval
        assert (r.topk_simple, r.topk_medium, r.topk_complex) == (3, 5, 8)
        assert r.rrf_k == 60
        assert r.pool_factor == 4 and r.pool_min == 12

    def test_pool_size_is_at_least_four_times_topk(self, settings_factory):
        """候选池必须 >= 4×topK 且 >= 12（设计约束，防止重排无从发挥）。"""
        r = settings_factory().retrieval
        for topk in (3, 5, 8):
            assert r.pool_for(topk) >= 4 * topk
            assert r.pool_for(topk) >= 12

    def test_topk_for_complexity(self, settings_factory):
        r = settings_factory().retrieval
        assert r.topk_for("simple") == 3
        assert r.topk_for("medium") == 5
        assert r.topk_for("complex") == 8
        # 未知复杂度回退到 medium，而不是抛错导致整条链路挂掉
        assert r.topk_for("unknown") == 5

    def test_chunk_defaults(self, settings_factory):
        s = settings_factory()
        assert s.chunk_default_size == 300
        assert s.chunk_default_overlap == 50
        assert s.chunk_default_overlap < s.chunk_default_size

    def test_paths_are_absolute(self, settings_factory):
        s = settings_factory()
        assert s.log_path.is_absolute()
        assert s.upload_path.is_absolute()
        assert s.log_path == BASE_DIR / "logs"

    def test_relative_reranker_path_resolves_to_project_root(self, settings_factory):
        s = settings_factory(reranker_model_path="models/bge-reranker-v2-m3")
        assert s.reranker_model_dir == BASE_DIR / "models" / "bge-reranker-v2-m3"

    def test_absolute_reranker_path_kept(self, settings_factory):
        s = settings_factory(reranker_model_path="D:/models/reranker")
        assert s.reranker_model_dir.as_posix() == "D:/models/reranker"

    def test_cors_origin_list(self, settings_factory):
        s = settings_factory(cors_origins="http://a.com, http://b.com,,")
        assert s.cors_origin_list() == ["http://a.com", "http://b.com"]


class TestEnvOverride:
    def test_env_var_overrides_default(self, settings_factory, monkeypatch):
        monkeypatch.setenv("MYSQL_PORT", "3310")
        monkeypatch.setenv("LLM_DEFAULT_MODEL", "qwen-plus")
        s = settings_factory()
        assert s.mysql_port == 3310
        assert s.llm.default_model == "qwen-plus"

    def test_env_value_wins_over_dotenv(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("MYSQL_PORT=3399\n", encoding="utf-8")
        monkeypatch.setenv("MYSQL_PORT", "3311")
        from config import Settings

        s = Settings(_env_file=str(env_file))
        assert s.mysql_port == 3311

    def test_dotenv_loaded_when_env_absent(self, tmp_path, monkeypatch):
        # 显式清掉环境变量，确保测的是"只有 .env 时能否读到"
        monkeypatch.delenv("MYSQL_PORT", raising=False)
        monkeypatch.delenv("LLM_DEFAULT_MODEL", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("MYSQL_PORT=3399\nLLM_DEFAULT_MODEL=qwen-turbo\n", encoding="utf-8")
        from config import Settings

        s = Settings(_env_file=str(env_file))
        assert s.mysql_port == 3399
        assert s.llm.default_model == "qwen-turbo"

    def test_unknown_keys_ignored(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("SOMETHING_UNRELATED=1\n", encoding="utf-8")
        from config import Settings

        assert Settings(_env_file=str(env_file)).app_name == "LawAnswer"


class TestValidation:
    def test_overlap_must_be_smaller_than_size(self, settings_factory):
        with pytest.raises(ValidationError) as exc:
            settings_factory(chunk_default_size=100, chunk_default_overlap=100)
        assert "CHUNK_DEFAULT_OVERLAP" in str(exc.value)

    def test_topk_must_be_ascending(self, settings_factory):
        with pytest.raises(ValidationError):
            settings_factory(
                retrieval_topk_simple=10,
                retrieval_topk_medium=5,
                retrieval_topk_complex=8,
            )

    def test_invalid_log_level(self, settings_factory):
        with pytest.raises(ValidationError):
            settings_factory(log_level="VERBOSE")

    def test_log_level_case_insensitive(self, settings_factory):
        assert settings_factory(log_level="debug").log_level == "DEBUG"

    def test_invalid_port(self, settings_factory):
        with pytest.raises(ValidationError):
            settings_factory(mysql_port=70000)

    def test_invalid_app_env(self, settings_factory):
        with pytest.raises(ValidationError):
            settings_factory(app_env="staging")

    def test_empty_host_rejected(self, settings_factory):
        with pytest.raises(ValidationError):
            settings_factory(mysql_host="   ")

    def test_similarity_threshold_range(self, settings_factory):
        with pytest.raises(ValidationError):
            settings_factory(qa_similarity_threshold=1.5)


class TestDsn:
    def test_mysql_dsn_encodes_special_chars(self, settings_factory):
        s = settings_factory(mysql_password="p@ss#word/1")
        dsn = s.mysql.dsn
        assert dsn.startswith("mysql+pymysql://lawanswer:")
        assert "p%40ss%23word%2F1" in dsn
        assert "@127.0.0.1:3307/lawanswer" in dsn
        assert "charset=utf8mb4" in dsn

    def test_redis_url_without_password(self, settings_factory):
        assert settings_factory(redis_password="").redis.url == "redis://127.0.0.1:6380/3"

    def test_redis_url_with_password(self, settings_factory):
        url = settings_factory(redis_password="pw").redis.url
        assert url == "redis://:pw@127.0.0.1:6380/3"

    def test_milvus_uri(self, settings_factory):
        assert settings_factory().milvus.uri == "http://127.0.0.1:19531"


class TestSecretRedLine:
    def test_secret_assignment_in_dotenv_is_rejected(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('DASHSCOPE_API_KEY=sk-123456\n', encoding="utf-8")
        with pytest.raises(SecretInDotEnvError) as exc:
            assert_secrets_not_in_dotenv(env_file)
        assert "系统环境变量" in str(exc.value)

    def test_api_key_alias_also_rejected(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('  export API_KEY=sk-abc\n', encoding="utf-8")
        with pytest.raises(SecretInDotEnvError):
            assert_secrets_not_in_dotenv(env_file)

    def test_comment_mentioning_secret_is_allowed(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# 密钥只放系统环境变量：DASHSCOPE_API_KEY=sk-xxx 不要写这里\n"
            "# API_KEY 同理\nMYSQL_PORT=3307\n",
            encoding="utf-8",
        )
        assert_secrets_not_in_dotenv(env_file)

    def test_missing_dotenv_is_ok(self, tmp_path):
        assert_secrets_not_in_dotenv(tmp_path / "not-exists.env")

    def test_repo_env_example_passes_guard(self):
        """仓库里的 .env.example 必须能通过自检（说明文字不算违规）。"""
        assert_secrets_not_in_dotenv(BASE_DIR / ".env.example")

    def test_get_api_key_reads_system_env(self, monkeypatch):
        from config import LLM_API_KEY_ENV_NAMES

        # 本机可能配了优先级更高的 LLM_API_KEY / DEEPSEEK_API_KEY，
        # 只留 DASHSCOPE 才能断言"从系统环境读到了它"
        for name in LLM_API_KEY_ENV_NAMES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-from-env")
        assert get_dashscope_api_key() == "sk-from-env"

    def test_require_api_key_raises_when_missing(self, monkeypatch):
        from config import LLM_API_KEY_ENV_NAMES

        for name in LLM_API_KEY_ENV_NAMES:  # 三个变量都清掉，避免本机环境干扰
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(ConfigError) as exc:
            require_dashscope_api_key()
        assert "DASHSCOPE_API_KEY" in str(exc.value)

    def test_mask_secret(self):
        assert mask_secret("") == "<empty>"
        assert mask_secret("abc") == "***"
        assert mask_secret("sk-1234567890abcdef") == "sk-1****cdef"
