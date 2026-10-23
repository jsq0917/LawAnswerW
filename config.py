"""LawAnswer 全局配置。

单一配置源：所有可调参数集中在这里，通过 pydantic-settings 从 ``.env`` 加载
（字段名大写即环境变量名，如 ``MYSQL_HOST``）。

安全红线
--------
大模型密钥**只从系统环境变量读取**，不写入 ``.env``、不写入代码、不写入仓库：

* 读取入口：:func:`get_llm_api_key`（按 ``LLM_API_KEY`` → ``DEEPSEEK_API_KEY``
  → ``DASHSCOPE_API_KEY`` 的顺序取第一个非空值，兼容多家 OpenAI 兼容服务：
  换厂商只需改 ``LLM_BASE_URL`` 与 ``LLM_DEFAULT_MODEL``）
* 启动自检：:func:`assert_secrets_not_in_dotenv`，若 ``.env`` 中出现上述任一
  密钥赋值，应用直接拒绝启动（不是警告），从机制上杜绝"图省事写进配置文件"。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

#: 只允许存在于系统环境变量中的密钥名
SECRET_ENV_NAMES = (
    "LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "API_KEY",
    "OPENAI_API_KEY",
)

#: 匹配"真正的赋值行"，注释里提到变量名不算
_SECRET_ASSIGN_RE = re.compile(
    r"^\s*(?:export\s+)?(" + "|".join(SECRET_ENV_NAMES) + r")\s*=",
)

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(RuntimeError):
    """配置非法，启动阶段就应失败。"""


class SecretInDotEnvError(ConfigError):
    """密钥被写进了 .env —— 违反安全红线。"""


# ---------------------------------------------------------------------------
# 分组视图（只读，便于 settings.mysql.host 这样使用）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    pool_size: int
    connect_timeout: int
    charset: str = "utf8mb4"

    @property
    def dsn(self) -> str:
        pwd = quote_plus(self.password)
        return (
            f"mysql+pymysql://{self.user}:{pwd}@{self.host}:{self.port}"
            f"/{self.database}?charset={self.charset}"
        )


@dataclass(frozen=True)
class RedisConfig:
    host: str
    port: int
    db: int
    password: str = ""

    @property
    def url(self) -> str:
        auth = f":{quote_plus(self.password)}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


@dataclass(frozen=True)
class MilvusConfig:
    host: str
    port: int
    database: str
    collection: str
    timeout: int = 5

    @property
    def uri(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    default_model: str
    timeout_s: int
    max_retries: int


@dataclass(frozen=True)
class IntentConfig:
    """意图识别专用：短超时（3s）+ 重试 1 次，失败即走规则兜底。"""

    timeout_s: int
    max_retries: int
    history_turns: int


@dataclass(frozen=True)
class EmbeddingConfig:
    model_path: str
    device: str
    fp16: bool
    batch_size: int
    dense_dim: int = 1024


@dataclass(frozen=True)
class RerankerConfig:
    model_path: str
    batch_size: int
    max_length: int


@dataclass(frozen=True)
class RetrievalConfig:
    rrf_k: int
    pool_factor: int
    pool_min: int
    topk_simple: int
    topk_medium: int
    topk_complex: int
    ranker: str = "weighted"
    dense_weight: float = 0.75
    sparse_weight: float = 0.25

    def topk_for(self, complexity: str) -> int:
        return {
            "simple": self.topk_simple,
            "medium": self.topk_medium,
            "complex": self.topk_complex,
        }.get(complexity, self.topk_medium)

    def pool_for(self, topk: int) -> int:
        return max(self.pool_factor * topk, self.pool_min)


@dataclass(frozen=True)
class GenerationConfig:
    """生成链路参数（阶段 5）。上下文预算与历史预算均按字符/token 估算口径。"""

    context_max_chars: int      # 参考资料总字符预算，超预算丢低分资料
    history_max_rounds: int     # 历史窗口轮数（对齐 chat_sessions.history_rounds 默认 5）
    history_token_budget: int   # 历史 token 预算（估算口径）
    max_tokens: int             # 生成长度上限
    temperature: float          # 采样温度（会话默认 0.30）
    replay_chunk_chars: int     # 结构化答案回放的分片大小（打字机效果）
    sources_limit: int          # SSE sources / prompt 资料条数上限


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """全部配置项。字段默认值即可跑通本地开发环境。"""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "LawAnswer"
    app_env: str = "dev"
    app_version: str = "0.1.0"
    debug: bool = False
    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8101, ge=1, le=65535)
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    # ---- 鉴权 ----
    jwt_secret: str = "dev-only-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = Field(default=24, ge=1, le=24 * 30)
    # 验证码与登录失败锁定（阶段 6）
    captcha_ttl_s: int = Field(default=120, ge=30, le=600)
    login_fail_limit: int = Field(default=5, ge=1, le=100)
    login_fail_window_s: int = Field(default=60, ge=10, le=3600)

    # ---- MySQL ----
    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=3307, ge=1, le=65535)
    mysql_user: str = "lawanswer"
    mysql_password: str = "lawanswer_dev_pwd"
    mysql_root_password: str = "root_dev_pwd"
    mysql_db: str = "lawanswer"
    mysql_pool_size: int = Field(default=10, ge=1, le=100)
    mysql_connect_timeout: int = Field(default=5, ge=1, le=60)

    # ---- Redis ----
    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6380, ge=1, le=65535)
    redis_db: int = Field(default=3, ge=0, le=15)
    redis_password: str = ""

    # ---- Milvus ----
    milvus_host: str = "127.0.0.1"
    milvus_port: int = Field(default=19531, ge=1, le=65535)
    milvus_db: str = "lawanswer"
    milvus_collection: str = "law_chunks"
    milvus_timeout: int = Field(default=5, ge=1, le=60)

    # ---- LLM ----
    llm_base_url: str = "https://api.deepseek.com"
    llm_default_model: str = "deepseek-flash"
    llm_thinking: str = ""  # DeepSeek: enabled/disabled；空=不传（DashScope/OpenAI 无此参数）
    llm_timeout_s: int = Field(default=60, ge=5, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    # ---- 意图识别（短超时，失败走规则兜底）----
    # 5s 而非设计初稿的 3s：关掉 thinking 后 DeepSeek 实测 p50≈1.0s / p95≈1.1s，
    # 留 4 倍余量抗网络抖动；超时即降级规则兜底（准确率 1.00→0.55，故别设太紧）。
    intent_timeout_s: int = Field(default=5, ge=1, le=30)
    intent_max_retries: int = Field(default=1, ge=0, le=3)
    intent_history_turns: int = Field(default=2, ge=0, le=10)

    # ---- Embedding / Rerank ----
    embedding_model_path: str = (
        r"C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-m3"
    )
    embedding_device: str = "cuda"
    embedding_fp16: bool = True
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    reranker_model_path: str = "models/bge-reranker-v2-m3"
    reranker_batch_size: int = Field(default=16, ge=1, le=128)
    reranker_max_length: int = Field(default=256, ge=64, le=8192)
    reranker_max_text_chars: int = Field(default=200, ge=50, le=2000)

    # ---- 分块 / 检索 ----
    chunk_default_size: int = Field(default=300, ge=50, le=2000)
    chunk_default_overlap: int = Field(default=50, ge=0, le=500)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    retrieval_pool_factor: int = Field(default=4, ge=1, le=20)
    retrieval_pool_min: int = Field(default=12, ge=1, le=200)
    retrieval_topk_simple: int = Field(default=3, ge=1, le=50)
    retrieval_topk_medium: int = Field(default=5, ge=1, le=50)
    retrieval_topk_complex: int = Field(default=8, ge=1, le=50)
    # 混合检索融合策略：rrf（等权，默认）/ weighted（稠密主导，实测无收益，保留作对照）
    retrieval_ranker: str = "rrf"
    retrieval_dense_weight: float = Field(default=0.75, ge=0.0, le=1.0)
    retrieval_sparse_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    # 用意图层的 law_hints 做检索法域预过滤（false = 全库检索，用于消融对照）
    retrieval_law_filter: bool = True
    qa_similarity_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    qa_bm25_topn: int = Field(default=3, ge=1, le=20)
    cache_qa_ttl: int = Field(default=604800, ge=60)

    # ---- 生成链路（阶段 5）----
    gen_context_max_chars: int = Field(default=6000, ge=500, le=50000)
    gen_history_max_rounds: int = Field(default=5, ge=0, le=20)
    gen_history_token_budget: int = Field(default=2000, ge=100, le=50000)
    gen_max_tokens: int = Field(default=2048, ge=256, le=8192)
    gen_temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    gen_replay_chunk_chars: int = Field(default=30, ge=5, le=500)
    gen_sources_limit: int = Field(default=10, ge=1, le=50)

    # ---- 日志 ----
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    log_backup_count: int = Field(default=5, ge=1, le=50)

    # ---- 存储 ----
    data_dir: str = "data"
    upload_dir: str = "data/uploads"
    minio_enabled: bool = True
    minio_endpoint: str = "127.0.0.1:9002"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "lawanswer"

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, v: str) -> str:
        v = (v or "").upper()
        if v not in _LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL 必须是 {sorted(_LOG_LEVELS)} 之一，收到 {v!r}")
        return v

    @field_validator("server_host", "mysql_host", "redis_host", "milvus_host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("主机地址不能为空")
        return v

    @model_validator(mode="after")
    def _check_cross_fields(self) -> "Settings":
        if self.chunk_default_overlap >= self.chunk_default_size:
            raise ValueError(
                f"CHUNK_DEFAULT_OVERLAP({self.chunk_default_overlap}) 必须小于 "
                f"CHUNK_DEFAULT_SIZE({self.chunk_default_size})"
            )
        if not (self.retrieval_topk_simple <= self.retrieval_topk_medium <= self.retrieval_topk_complex):
            raise ValueError("topK 必须满足 simple <= medium <= complex")
        if self.app_env not in {"dev", "test", "prod"}:
            raise ValueError(f"APP_ENV 只能是 dev/test/prod，收到 {self.app_env!r}")
        return self

    # ------------------------------------------------------------------
    # 分组视图
    # ------------------------------------------------------------------
    @property
    def mysql(self) -> MySQLConfig:
        return MySQLConfig(
            host=self.mysql_host,
            port=self.mysql_port,
            user=self.mysql_user,
            password=self.mysql_password,
            database=self.mysql_db,
            pool_size=self.mysql_pool_size,
            connect_timeout=self.mysql_connect_timeout,
        )

    @property
    def redis(self) -> RedisConfig:
        return RedisConfig(
            host=self.redis_host,
            port=self.redis_port,
            db=self.redis_db,
            password=self.redis_password,
        )

    @property
    def milvus(self) -> MilvusConfig:
        return MilvusConfig(
            host=self.milvus_host,
            port=self.milvus_port,
            database=self.milvus_db,
            collection=self.milvus_collection,
            timeout=self.milvus_timeout,
        )

    @property
    def llm(self) -> LLMConfig:
        return LLMConfig(
            base_url=self.llm_base_url,
            default_model=self.llm_default_model,
            timeout_s=self.llm_timeout_s,
            max_retries=self.llm_max_retries,
        )

    @property
    def intent(self) -> IntentConfig:
        return IntentConfig(
            timeout_s=self.intent_timeout_s,
            max_retries=self.intent_max_retries,
            history_turns=self.intent_history_turns,
        )

    @property
    def embedding(self) -> EmbeddingConfig:
        return EmbeddingConfig(
            model_path=self.embedding_model_path,
            device=self.embedding_device,
            fp16=self.embedding_fp16,
            batch_size=self.embedding_batch_size,
        )

    @property
    def reranker(self) -> RerankerConfig:
        return RerankerConfig(
            model_path=self.reranker_model_path,
            batch_size=self.reranker_batch_size,
            max_length=self.reranker_max_length,
        )

    @property
    def retrieval(self) -> RetrievalConfig:
        return RetrievalConfig(
            rrf_k=self.retrieval_rrf_k,
            pool_factor=self.retrieval_pool_factor,
            pool_min=self.retrieval_pool_min,
            topk_simple=self.retrieval_topk_simple,
            topk_medium=self.retrieval_topk_medium,
            topk_complex=self.retrieval_topk_complex,
            ranker=self.retrieval_ranker,
            dense_weight=self.retrieval_dense_weight,
            sparse_weight=self.retrieval_sparse_weight,
        )

    @property
    def generation(self) -> GenerationConfig:
        return GenerationConfig(
            context_max_chars=self.gen_context_max_chars,
            history_max_rounds=self.gen_history_max_rounds,
            history_token_budget=self.gen_history_token_budget,
            max_tokens=self.gen_max_tokens,
            temperature=self.gen_temperature,
            replay_chunk_chars=self.gen_replay_chunk_chars,
            sources_limit=self.gen_sources_limit,
        )

    # ------------------------------------------------------------------
    # 路径（相对路径一律相对项目根）
    # ------------------------------------------------------------------
    @property
    def log_path(self) -> Path:
        return self._abs(self.log_dir)

    @property
    def data_path(self) -> Path:
        return self._abs(self.data_dir)

    @property
    def upload_path(self) -> Path:
        return self._abs(self.upload_dir)

    @property
    def embedding_model_dir(self) -> Path:
        return Path(self.embedding_model_path).expanduser()

    @property
    def reranker_model_dir(self) -> Path:
        """相对路径相对项目根，绝对路径原样返回。"""
        p = Path(self.reranker_model_path).expanduser()
        return p if p.is_absolute() else (BASE_DIR / p)

    def _abs(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (BASE_DIR / p)

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        """创建运行时目录（日志、上传、数据）。"""
        for p in (self.log_path, self.upload_path, self.data_path):
            p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 密钥：只认系统环境变量
# ---------------------------------------------------------------------------
def assert_secrets_not_in_dotenv(path: Path | None = None) -> None:
    """若 ``.env`` 中出现密钥赋值行，抛 :class:`SecretInDotEnvError`。

    只检查非注释行，因此 `.env.example` 里"密钥应放环境变量"的说明文字不会误报。
    """
    env_path = Path(path) if path is not None else ENV_FILE
    if not env_path.exists():
        return
    for lineno, line in enumerate(
        env_path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
    ):
        if line.lstrip().startswith("#"):
            continue
        m = _SECRET_ASSIGN_RE.match(line)
        if m:
            raise SecretInDotEnvError(
                f"{env_path.name} 第 {lineno} 行出现了 {m.group(1)} 赋值。"
                "密钥必须只放在系统环境变量里（LLM_API_KEY / DEEPSEEK_API_KEY / "
                "DASHSCOPE_API_KEY 任选其一），禁止写入配置文件。"
            )


#: 密钥环境变量按优先级排列：通用名优先，其次各厂商约定名。
#: 之所以支持多个名字：底层是 OpenAI 兼容协议，换厂商只需换 base_url + 模型名，
#: 密钥变量名沿用各厂商习惯即可（DeepSeek 用户直接用 DEEPSEEK_API_KEY）。
LLM_API_KEY_ENV_NAMES: tuple[str, ...] = (
    "LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
)


def get_llm_api_key() -> str:
    """读取大模型密钥（任一家 OpenAI 兼容服务）；未配置返回空串。"""
    for name in LLM_API_KEY_ENV_NAMES:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def get_llm_api_key_env() -> str:
    """返回实际生效的密钥变量名（只暴露名字、不暴露值，供健康检查/日志展示）。"""
    for name in LLM_API_KEY_ENV_NAMES:
        if os.getenv(name, "").strip():
            return name
    return ""


def require_llm_api_key() -> str:
    """需要真正调用大模型时使用：缺失则明确报错，而不是静默降级。"""
    key = get_llm_api_key()
    if not key:
        raise ConfigError(
            "未检测到大模型密钥。请设置下列任一系统环境变量后重启服务："
            "LLM_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY；"
            "并确认 .env 中 LLM_BASE_URL 与 LLM_DEFAULT_MODEL 与该厂商匹配"
            "（DeepSeek: https://api.deepseek.com + deepseek-flash，并设 LLM_THINKING=disabled；"
            "DashScope: https://dashscope.aliyuncs.com/compatible-mode/v1 + qwen-max）。"
        )
    return key


def get_dashscope_api_key() -> str:
    """兼容旧调用点：等价于 :func:`get_llm_api_key`（现已支持 DeepSeek 等）。"""
    return get_llm_api_key()


def require_dashscope_api_key() -> str:
    """兼容旧调用点：等价于 :func:`require_llm_api_key`。"""
    return require_llm_api_key()
    return key


def mask_secret(value: str, keep: int = 4) -> str:
    """日志里打印密钥时使用，如 ``sk-1****cdef``。"""
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}****{value[-keep:]}"


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """测试或热更新时重建单例。"""
    get_settings.cache_clear()
    return get_settings()
