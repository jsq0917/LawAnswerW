# LawAnswer 改造方案：本地模型 API 化 + 轻量向量库

> 目标：把 bge-m3 / bge-reranker-v2-m3 从本地推理改为 API 调用，把 Milvus standalone 换成 milvus-lite，
> 使整套系统能跑在 **2 核 2G / 40G** 的阿里云 ECS 上。
>
> 本文档只做设计与计划，**不含实施**。经确认后再按阶段动手。

---

## 0. 三个问题的结论

| 问题 | 结论 | 关键依据 |
|---|---|---|
| 本地模型能否换成 API？ | **可以，且混合检索架构能完整保留** | DashScope `text-embedding-v4` 支持 `output_type=dense&sparse`，一次调用同时产出稠密(1024维)+稀疏，与现有 `DENSE_DIM=1024` 及双路 RRF 架构对齐 |
| Milvus 能否换更省内存的？ | **换 milvus-lite，代码改动几乎为零** | 它用同一套 `MilvusClient` API，只是 uri 从 `http://host:port` 变成文件路径；支持 partition key / HNSW / 稀疏 / hybrid+RRF |
| 能否塞进 2G 服务器？ | **改造后可以** | 常驻内存从 ≈7.6G 降到 ≈1.4–1.8G（详见 §1.3） |

### 两处必须更正的前期说法

1. **`gte-rerank-v2` 已于 2026-05-30 下线**（官方公告，今天 2026-10-26 已过期）→ 必须改用 **`qwen3-rerank`**。
2. **`text-embedding-v4` 单次批量上限是 10 条**（不是 25，那是 v2/v3 的旧值）→ 入库批处理必须调整。

---

## 1. 关键技术约束（均已核实官方文档）

### 1.1 Embedding：DashScope text-embedding-v4

| 项 | 值 | 对本项目的影响 |
|---|---|---|
| 支持维度 | 2048/1536/**1024**/768/512/256/128/64 | 取 1024，与 `DENSE_DIM` 一致，**无需改 collection schema** |
| `output_type` | `dense` / `sparse` / **`dense&sparse`** | 用 `dense&sparse` 保留双路检索；成本与单向量模式相同 |
| 单次批量 | **最多 10 条文本** | `EMBEDDING_BATCH_SIZE` 默认从 16 改 10；实现内部按 10 硬切分 |
| 单条长度 | 8192 token | 现有子块 ≤300 字，远未触顶 |
| `text_type` | `query` / `document`（默认 document） | **检索非对称任务建议区分**：入库用 document，查询用 query |
| 稀疏返回格式 | `[{index, value, token}]` | 转 `{index: value}` 即现有 `Embedding.sparse` 的 `dict[int, float]`，天然兼容 |

**硬限制（决定性）**：稀疏向量输出**只支持 DashScope 原生接口**。
OpenAI 兼容接口（`/compatible-mode/v1/embeddings`）传 `output_type=sparse` 会 **返回 HTTP 200 但结果是空的** —— 静默失败，极难排查。
因此必须走原生端点：`https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding`

### 1.2 Rerank：DashScope qwen3-rerank

| 项 | 值 |
|---|---|
| 单次最大文档数 | 500（现有候选池最大 32 条，远够用） |
| 单条最大 token | 4,000 |
| 请求最大 token | 120,000（建议值） |
| 支持 `instruct` | ✅ 可设排序策略（如 `Retrieve semantically similar text.`） |
| 端点 | DashScope 原生 `/api/v1/services/rerank/text-rerank/text-rerank`，或 OpenAI 兼容 `/compatible-api/v1/reranks` |
| 返回格式 | `output.results: [{index, relevance_score}]`，**按相关性降序** |

**注意**：返回的是排好序的列表，而现有 `Reranker.score()` 契约要求**返回与输入等长的、按输入顺序的分数**。
适配层必须按 `index` 还原位置，否则排序会错乱（这是最容易埋 bug 的一处）。

### 1.3 内存账（改造后 2G 可行）

| 组件 | 改造前 | 改造后 |
|---|---|---|
| 向量库 | 2.0–2.5 G（Milvus + etcd + MinIO 三容器） | ~0.3–0.5 G（milvus-lite 进程内） |
| bge-m3 | 2.3 G | **0**（走 API） |
| bge-reranker-v2-m3 | 2.3 G | **0**（走 API） |
| MySQL + Redis | 0.5 G | 0.5 G |
| FastAPI + 系统开销 | 0.5 G | 0.5 G |
| **合计** | **≈ 7.6 G** | **≈ 1.4–1.8 G** ✅ |

附带收益：venv 从约 3.5 G 降到约 0.5 G（可卸掉 torch / transformers / FlagEmbedding）；
容器从 5 个降到 2 个（MySQL + Redis）；重排延迟可能低于 CPU 本地（本地 20 条约 5–14 s，API 约 0.2–0.5 s）。

**代价**：每次问答新增 2 次外部 API 调用（embedding + rerank），引入网络依赖与按量费用；
改造后**偏离 `REPORT.md` 已验收的评估口径**（Recall@5 0.7071 依赖本地 reranker），需重跑评估建立新基线。

### 1.4 milvus-lite 能力与限制

**已确认支持**（milvus-lite 3.2.2，pymilvus 2.5.18 已依赖）：partition key、多 database、HNSW 索引、
`SPARSE_FLOAT_VECTOR` + `SPARSE_INVERTED_INDEX`、hybrid search + `RRFRanker`/`WeightedRanker`、
`upsert`/`delete by filter`/`query`。

**已知限制（需规避）**：

| 限制 | 对本项目的影响 | 对策 |
|---|---|---|
| 同一 data_dir **单进程**，写操作需串行 | FastAPI 当前是单进程 `uvicorn.run`（无 workers），**无影响** | 禁止改成多 worker；如需并发只能换回 standalone |
| 官方定位是"本地开发/小规模"，有社区反馈长驻 Web 服务空闲后嵌入引擎可能退出 | **最大风险点** | §7 列为阶段 0 的验证项，先行压测；退路是 Qdrant 或阿里云托管向量检索 |
| 不支持 schema alteration、分区级 load/release | 本项目 schema 固定，重建走 `--drop` 全量重建 | 无影响 |
| 无 RBAC / TLS | 仅本机进程内访问，不暴露端口 | 无影响 |
| `pymilvus` 对 Windows 用 marker 排除了 milvus-lite | **本地 Windows 可能无法验证 lite** | 见 §3.3「双轨连接」设计 |
| 稀疏向量维度须为 `0 ~ 2^32-2` 的整数 ID，权重为有限非负 float32 | DashScope 稀疏输出正是 `{int: float}` 且非负 | 无影响 |

---

## 2. 改造设计总览

核心思路：**用 Protocol（接口）做抽象，新增两个 API 实现类，不改上层调用方**。

现有代码已经具备良好抽象 —— `app/ingest/embedder.py` 定义了 `Embedder` Protocol（要求 `dim` + `encode()`），
`app/retrieval/rerank.py` 定义了 `Reranker` Protocol（要求 `score()`）。新增实现类即可，检索/入库/评估链路**零改动**。

```
app/ingest/embedder.py
├── Embedder (Protocol)         ← 不变
├── DummyEmbedder               ← 不变（单测离线用）
├── BgeM3Embedder               ← 保留（本地开发仍可用）
└── DashScopeEmbedder           ← 新增：dense&sparse 一次调用

app/retrieval/rerank.py
├── Reranker (Protocol)         ← 不变
├── DummyReranker               ← 不变
├── BgeReranker                 ← 保留
└── DashScopeReranker           ← 新增

get_embedder() / get_reranker()  ← 按配置分发到本地或 API 实现
```

### 2.1 Embedding API 化

新增 `DashScopeEmbedder`：

- 走 DashScope **原生** HTTP 端点（不能走 OpenAI 兼容，否则稀疏为空）
- 入参：`model` / `dimension=1024` / `output_type="dense&sparse"` / `text_type` / `instruct`
- 内部按 **10 条**切批（忽略或 `min(batch_size, 10)`），结束后按原顺序拼回
- 解析响应：`embedding` → dense；`sparse_embedding` → `{item["index"]: item["value"]}`
- 稀疏侧沿用现有 `SPARSE_MIN_WEIGHT=0.05` 过滤
- 空稀疏时复用 `embeddings_to_milvus` 的占位逻辑（`{0: 0.0}`）
- 失败重试沿用 `scripts/check_llm.py` / `LLMClient` 的指数退避风格

**`text_type` 语义要贯穿到调用点**（这是质量关键，也比较容易漏）：

| 场景 | 调用点 | text_type |
|---|---|---|
| 入库（法律条文） | `app/ingest/pipeline.py:322` | `document` |
| 单块重新向量化 | `app/ingest/service.py:187` | `document` |
| 检索查询 | `app/retrieval/service.py:67` | **`query`** |
| 高频问答对召回 | `app/orchestration/qa_cache.py:372` | `query`（对照已有问题） |
| 评估脚本 | `app/eval/*.py` | 按 query/document 区分 |

实现上给 `Embedder.encode()` 增加可选参数 `text_type`（Protocol 里给默认值），默认 `document`，检索侧显式传 `query`。

### 2.2 Rerank API 化

新增 `DashScopeReranker`：

- 端点：DashScope 原生 `text-rerank`
- 入参：`model="qwen3-rerank"` / `query` / `documents` / `top_n`（**不传**，取全部以便还原顺序）
- **关键**：把返回的 `[{index, relevance_score}]` 还原成 **按输入顺序** 的等长分数列表
- 保留 `max_text_chars` 裁剪（控制 token 成本与延迟）
- 沿用现有开关语义：`use_rerank=False` 时完全跳过（消融对照仍可用）

### 2.3 向量库：milvus-lite

**改动极小**，只动 `app/db/milvus_client.py` 的 uri 构造：

```python
# 现状：只支持远程
@property
def uri(self) -> str:
    return f"http://{self.host}:{self.port}"

# 改造：支持本地文件（milvus-lite）或远程（standalone）
@property
def uri(self) -> str:
    return self.uri_override or f"http://{self.host}:{self.port}"
```

新增配置 `MILVUS_URI`（空 = 用 host:port 拼；填文件路径 = 启用 Milvus Lite）。
`MilvusClient(uri=...)` 会**自动识别**：`.db` 结尾当作 Lite，`http(s)://` 当作远程 —— 无需改其他代码。

`app/db/milvus_schema.py` 基本不动（HNSW、稀疏索引、partition key 在 lite 下都支持）。
唯一需要确认：`ensure_collection()` 里的 `create_database` + `using_database` + `load_collection` 在 lite 下的行为（§7 验证项）。

### 2.4 配置变更（`config.py`）

新增：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `EMBEDDING_PROVIDER` | `local` | `local` \| `dashscope`；用于分发实现类 |
| `EMBEDDING_API_MODEL` | `text-embedding-v4` | |
| `EMBEDDING_API_BASE` | `https://dashscope.aliyuncs.com/api/v1` | 原生端点，**非** compatible-mode |
| `RERANK_PROVIDER` | `local` | `local` \| `dashscope` |
| `RERANK_API_MODEL` | `qwen3-rerank` | |
| `RERANK_API_INSTRUCT` | `""` | 可选排序指令 |
| `MILVUS_URI` | `""` | 空=远程；文件路径=Lite |

调整：`EMBEDDING_BATCH_SIZE` 默认 `16 → 10`（API 硬限制）；`EMBEDDING_DEVICE` 在 API 模式下无意义（保留但忽略）。

密钥沿用现有机制：**只从系统环境变量读 `DASHSCOPE_API_KEY`**，不进 `.env`（`assert_secrets_not_in_dotenv` 会拒绝）。
注意：图片 OCR 已经依赖 `DASHSCOPE_API_KEY`，所以生产环境本来就需要这个 key，**不额外增加密钥管理负担**。

### 2.5 依赖变更（`pyproject.toml`）

| 操作 | 包 | 说明 |
|---|---|---|
| 新增 | `dashscope` | 官方 SDK（也可只用 httpx 手写，避免新依赖；建议先用 httpx，因为项目已有） |
| 保留可选 | `torch` / `transformers` / `flagembedding` | **建议移入独立的 `local` 依赖组**，服务器不装（省约 3 G），本地开发仍可装 |
| 保留 | `pymilvus` | 已含 milvus-lite 依赖（Linux） |

> 用 `httpx` 手写调用的好处：项目已依赖 httpx、零新增依赖、避免 dashscope SDK 的版本耦合；
> 代价是要自己拼请求体（结构简单，就是 `model/input/parameters` 三个字段）。两者都可行，实施时先按 httpx 走。

---

## 3. 代码改动清单

| 文件 | 改动 | 规模 |
|---|---|---|
| `app/ingest/embedder.py` | 新增 `DashScopeEmbedder`；`Embedder.encode()` 加 `text_type` 参数；`get_embedder()` 按配置分发 | 中 |
| `app/retrieval/rerank.py` | 新增 `DashScopeReranker`；`get_reranker()` 按配置分发 | 中 |
| `app/db/milvus_client.py` | uri 支持 `uri_override`（Lite 文件路径） | 小 |
| `config.py` | 新增 §2.4 配置项；`MilvusConfig` 加 `uri_override`；`EmbeddingConfig` 加 provider | 小 |
| `app/retrieval/service.py` | 检索时 `encode(..., text_type="query")` | 小 |
| `app/ingest/pipeline.py` | 入库时 `text_type="document"`（显式）；批大小取 min(10, config) | 小 |
| `app/ingest/service.py` | 单块重新向量化传 `text_type="document"` | 小 |
| `app/orchestration/qa_cache.py` | 问答对召回传 `text_type="query"` | 小 |
| `app/eval/*.py` | 按 query/document 区分（评估口径） | 小 |
| `pyproject.toml` | 依赖分组调整 | 小 |
| `deploy/*` | 服务器 compose 去掉 Milvus/etcd/MinIO；env 模板更新 | 中 |

**不改动**：`app/retrieval/hybrid.py`、`app/ingest/writer.py`、`app/db/milvus_schema.py`、
`app/generation/*`、`app/api/*`、前端 —— 这正是选 milvus-lite 而非 Qdrant 的价值所在。

---

## 4. 测试计划

项目约定：每个功能同步单测、改完必跑全量且全绿。

### 4.1 新增单测（全离线，mock HTTP）

| 用例 | 断言要点 |
|---|---|
| `DashScopeEmbedder` 正常解析 | dense 长度=1024；sparse 是 `{int: float}`；顺序与输入一致 |
| 稀疏返回缺失/为空 | 走占位 `{0: 0.0}`，不抛异常 |
| **批量 > 10 自动切分** | 传 25 条 → 断言发 3 次请求（10/10/5），结果顺序正确 |
| `text_type` 透传 | 检索场景请求体里 `text_type=query` |
| HTTP 200 但 `embedding` 为空 | **必须抛错**，不能静默返回空向量（对应 OpenAI 兼容端点那个坑） |
| `DashScopeReranker` 顺序还原 | 返回乱序 `[{index:2,score:0.9},{index:0,score:0.1}]` → 输出 `[0.1, ?, 0.9]` 按输入序 |
| rerank 返回条数不匹配 | 抛错或补齐，不能静默错位 |
| `MilvusConfig.uri` | 空 URI → `http://host:port`；给定文件路径 → 原样返回 |

### 4.2 现有测试的兼容性

- `DummyEmbedder` / `DummyReranker` **保留不变** → `test_retrieval.py`、`test_orchestration_*` 等**不受影响**
- `test_embedder.py` 的 `TestDeviceResolution` 测的是 `BgeM3Embedder`，**该类保留** → 不受影响
- `test_milvus_schema.py` 用 `FakeMilvusClient` → 不受影响
- **新增**：`test_embedder.py` 加 API 实现用例；新增 `test_rerank_api.py`
- 需要 `monkeypatch.delenv` 清 `DASHSCOPE_API_KEY` 的降级用例，沿用现有惯例

### 4.3 集成测试（需真实依赖，`RUN_INTEGRATION=1`）

- milvus-lite：建库 → 建 collection → upsert → hybrid_search → 验证 6 路由
- DashScope：真实调 embedding + rerank，验证稀疏非空、rerank 分数有序

### 4.4 评估回归（**必做**）

```bash
uv run python -m app.eval.recall_eval      # 对比 Recall@5 / MRR
uv run python -m app.eval.intent_eval
uv run python -m app.eval.ragas_eval
uv run python -m app.eval.loadtest --levels 1 5 10
```

对照 `REPORT.md` 现有基线（Recall@5 0.7071 / MRR 0.6886），**记录 API 化后的新基线**并解释差异来源。

---

## 5. 分阶段实施计划

按项目约定「一次只做一个阶段，做完等验收」。

### 阶段 0：可行性验证（**先做，不写业务代码**）

先花最小成本验证两个不确定项，避免方案落空：

1. **milvus-lite 长驻可靠性**（最大风险）
   - 在服务器上跑一个长驻进程：建 collection → upsert 1465 条 → 间隔调用 `hybrid_search` → 观察数小时/隔夜后是否仍可用
   - 同时验证 `create_database` / `using_database` / `load_collection` / HNSW / 稀疏索引在 lite 下的实际行为
2. **API 连通性与返回结构**
   - 用 10 条真实法条调 `text-embedding-v4` 的 `dense&sparse`，确认稀疏非空
   - 调 `qwen3-rerank`，确认返回结构与排序语义
   - 记录单次耗时与 token 用量（用于成本估算）

**产出**：一份验证报告（含原始响应样本、耗时、结论）。**若 milvus-lite 不达标，此阶段就要改选 Qdrant。**

### 阶段 1：Embedding API 化
实现 `DashScopeEmbedder` + 配置 + 单测；入库链路接入（`text_type=document`）。

### 阶段 2：Rerank API 化
实现 `DashScopeReranker` + 配置 + 单测；检索链路接入。

### 阶段 3：向量库切换 milvus-lite
`milvus_client.py` uri 改造 + 配置 + 建库脚本调整 + 集成测试。

### 阶段 4：本地全链路回归
关掉两个本地模型，本地（或 Linux 环境）跑通 6 路由 demo + 全量单测 + 评估回归。

### 阶段 5：服务器部署
按 §6 执行。

> 每个阶段结束跑 `uv run --no-sync python -m pytest tests/ -v` 全量，全绿才进入下一阶段。

---

## 6. 服务器部署（改造后）

改造后部署显著简化。

**Docker 只需 2 个容器**（MySQL + Redis），compose 删除 `milvus` / `etcd` / `minio` 三个服务，
`deploy/docker-compose.server.yml` 相应精简。

**关键配置**（`/etc/lawanswer/lawanswer.env`）：

```ini
APP_ENV=prod
SERVER_HOST=127.0.0.1
SERVER_PORT=8101

# ---- 本地模型全部关闭 ----
EMBEDDING_PROVIDER=dashscope
EMBEDDING_API_MODEL=text-embedding-v4
EMBEDDING_API_BASE=https://dashscope.aliyuncs.com/api/v1
EMBEDDING_BATCH_SIZE=10          # API 硬限制

RERANK_PROVIDER=dashscope
RERANK_API_MODEL=qwen3-rerank

# ---- 向量库：本地文件，进程内 ----
MILVUS_URI=/opt/lawanswer/volumes/milvus_lite/lawanswer.db

# ---- 密钥：只从系统环境变量，此处不写 ----
# 需要 DASHSCOPE_API_KEY（同时供 embedding / rerank / 图片 OCR）
```

**依赖安装**：服务器上 `uv sync` **不装 local 依赖组** → 省约 3 G 磁盘、且无需下载模型（省 4.4 G 下载）。

**入库**：`data/raw` 三部法律首次入库约 1465 条 → 按 10 条/批 ≈ 147 次 API 调用，预计数分钟。

**仍需注意**（沿用已交付的部署方案，见 `docs/部署方案-阿里云ECS.md`）：
- 安全组只放行 22 / 80 / 443；MySQL(3307)、Redis(6380)、FastAPI(8101) 只绑 `127.0.0.1`
- nginx：SSE 关缓冲（`proxy_buffering off`）、history 回退（`try_files ... /index.html`）、`client_max_body_size`
- systemd 单元 + `JWT_SECRET` 强随机
- **禁止把 uvicorn 改成多 worker**（milvus-lite 是单进程文件锁，多 worker 会冲突）
- milvus-lite 的 `.db` 文件纳入备份（与 `volumes/` 一起）

---

## 7. 风险与待验证项

| 风险 | 等级 | 说明与对策 |
|---|---|---|
| milvus-lite 长驻 Web 服务可靠性 | **高** | 社区有"嵌入式引擎空闲退出"反馈。**阶段 0 必须验证**；退路：Qdrant（单容器）或阿里云托管向量检索 |
| 本地 Windows 无法验证 milvus-lite | 中 | pymilvus 用 marker 排除了 win32。**对策：双轨连接** —— 本地继续用 Docker Milvus standalone，服务器用 lite，靠 `MILVUS_URI` 切换，代码零差异 |
| API 化后检索质量变化 | 中 | 稀疏向量由另一模型产出，召回分布会变。**必须重跑评估**并记录新基线，不能沿用 0.7071 |
| API 限流 / 失败 | 中 | 需重试 + 降级（embedding 失败则整次检索失败；rerank 失败可降级为纯 RRF 排序，保持链路可用） |
| 成本 | 低 | 每次问答 2 次 API 调用；rerank 约 0.8 元/百万 token（qwen3-rerank 价格以控制台为准），单次问答重排约 4500 token。建议在百炼控制台设额度上限 |
| 网络依赖 | 低 | 服务器与百炼同属阿里云，内网质量好；但仍是外部依赖，`/api/health` 应增加 provider 可用性检查 |
| `text-embedding-v4` 批量=10 拖慢入库 | 低 | 1465 条 ≈ 147 次请求，可接受；必要时并发（注意限流） |

---

## 8. 待确认事项

1. **本地开发是否保留本地模型？** 建议保留（`local` 依赖组 + `EMBEDDING_PROVIDER=local`），
   这样本地能离线开发调试，服务器走 API。
2. **是否接受重跑评估、更新 `REPORT.md` 基线？** 这是 API 化的必然代价。
3. **阶段 0 的验证在哪做？** 若本机 Windows 跑不了 milvus-lite，建议直接在 ECS 上验证（需要你提供 SSH 访问方式，或你按脚本执行）。

---

## 附：与既有文档的关系

- `docs/部署方案-阿里云ECS.md`：部署流程仍然适用，但 §4.2 起的基础设施与模型相关章节需按本方案更新
- `deploy/docker-compose.server.yml`：删除 milvus / etcd / minio 三个服务
- `deploy/production.env.example`：按 §6 新增 provider 与 MILVUS_URI 配置
- `REPORT.md`：阶段 4 评估回归后需新增"API 化后基线"小节