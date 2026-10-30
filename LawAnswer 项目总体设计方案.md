# LawAnswer 项目总体设计方案

> 版本：v1.0（待确认稿）
> 日期：2026-10-22
> 状态：**待你拍板** —— 第 11 章列出 9 个决策点，确认后从阶段 1 开始，一次只做一个阶段。
> 本文只做设计，不含实现代码。所有字段/索引/阈值均为最终设计意图，编码时按此实现。

---

## 0. 摘要与关键结论

### 0.1 一句话定位

LawAnswer 是一个**面向法律条文的多路 RAG 问答系统**：把三部法律（劳动合同法 98 条 / 劳动法 107 条 / 民法典 1260 条）按"编→章→节→条→款"结构化切块，用 bge-m3 的稠密+稀疏双向量存入 Milvus，配合"MySQL 高频问答对 + Redis 热缓存"的结构化链路做双源路由，经 RRF 融合与 bge-reranker-v2-m3 重排后，由 deepseek-v4.1-flash 流式生成**带法条引用**的中文回答。

### 0.2 本机环境实测结论（与你给的前提有 3 处偏差，需注意）

| 项目 | 你的描述 | 实测结果 | 影响 |
|---|---|---|---|
| Python / uv | 3.13+ / uv | Python 3.13.14，uv 0.12.11 ✅ | 无 |
| GPU | — | RTX 4060 Laptop **8 GB** | bge-m3 + reranker 同时常驻约 5 GB（fp16），需懒加载与显存预算控制 |
| bge-m3 缓存 | "已缓存于 `~/.cache/huggingface/hub/models--BAAI--bge-m3`" | ❌ **该目录只有 `refs/main`，无 `snapshots/`、无权重**，实际未下载 | 阶段 1 必须先下载（约 2.2 GB），见风险 R1 |
| Docker | 已装 MySQL/Milvus | ✅ MySQL 镜像 `mysql:8.0.43`、Milvus `v2.4.4`、Redis 7.2、MinIO 镜像均在本地 | 无需拉镜像，直接起容器 |
| 端口占用 | — | ❌ 19530/9091（shopmate-milvus）、6379（milvus-redis）、9000-9001（shopmate-minio）、5432、3000、5173 **均被占用**；8000 被系统进程占用 | 必须改用偏移端口，见 §1.5 |
| 数据语料 | 3 部法律 PDF + Markdown | ✅ 共 **145,596 字**，**1,465 条**；劳动法 Markdown 含 **1,122 个康熙部首变体字符**（`第⼀条` 的 `⼀` 为 U+2F00） | 归一化是硬需求，不归一化只能识别出 6/107 条 |

### 0.3 三个"设计上必须提前定"的量化结论

1. **法条极短**：1,465 条平均 **97 字**，中位数 76 字，P90 186 字，最长 454 字。
   → 若 child_size 取 500，子块数（1,466）几乎等于父块数（1,465），父子分块**退化为 1:1**。
   → 因此子块策略定为：**条内按"款/项"语义单元切，字符硬上限 300**，短条文不强行切（子块=父块）。这不是妥协，而是在法律语料上"法条完整性优先"的正确取舍，且作为答辩论点写进评估对比。
2. **固定窗口切分会截断法条**：500/50 固定窗口只能切出约 324 块，必然把"第四十七条"这类跨窗条文劈开。
   → 消融实验专设 **D 组：固定窗口 500/50 作 baseline**，用 Recall@5 与"法条截断率"证明父子分块的价值。
3. **三部法律结构不一致**：民法典是 **编→章→节→条**（且目录区与正文区各出现一次"第X编"），另两部是 **章→节→条**。
   → 解析器必须支持 4 级路径，且用"首个条号出现位置"判定正文起点，丢弃目录区。

---

## 1. 系统架构

### 1.1 分层架构（文字版）

```
┌───────────────────────────────────────────────────────────────────────┐
│ 表现层  Vue3 + Vite + TS  (http://localhost:5180)                     │
│   登录 / 注册 / 对话 / 知识库管理 / 知识库解析 / 知识库预览            │
│   职责：只做展示与交互；不持有业务规则；token 存 localStorage           │
└──────────────────────────┬────────────────────────────────────────────┘
                           │ JSON (REST) + text/event-stream (SSE)
┌──────────────────────────▼────────────────────────────────────────────┐
│ 接口层  FastAPI + Uvicorn (http://localhost:8101)                     │
│   · 路由：auth / sessions / chat / kbs / documents / chunks / meta     │
│   · 中间件：RequestID、统一异常、CORS、访问日志（控制台+文件）          │
│   · 鉴权：JWT Bearer，bcrypt 存储密码，图形验证码（内存态 + TTL）       │
│   · 依赖注入：DB Session / Milvus Client / Redis / 模型单例（懒加载）   │
└──────────────────────────┬────────────────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────────────────┐
│ 编排层  QueryOrchestrator                                             │
│   ① 归一化问题 → ② 缓存判定 → ③ 意图识别+改写（1 次 LLM，JSON 模式）    │
│   ④ 规则兜底 → ⑤ 数据源路由（结构化 / 向量 / 混合）→ ⑥ 并行检索        │
│   ⑦ 融合重排 → ⑧ 上下文预算控制 → ⑨ 交生成层                         │
└───┬──────────────────────┬────────────────────────┬───────────────────┘
    │                      │                        │
┌───▼──────────────┐ ┌─────▼──────────────────┐ ┌───▼──────────────────┐
│ 检索层 A：结构化  │ │ 检索层 B：向量          │ │ 生成层               │
│ MySQL qa_pairs    │ │ bge-m3 (dense+sparse)  │ │ 中文法律 Prompt       │
│ BM25 召回 + 余弦门 │ │ Milvus 双路 AnnSearch   │ │ DeepSeek V4.1-Flash   │
│ Redis 热缓存      │ │ RRF(k=60) → reranker    │ │ SSE 逐 token 推送     │
│                  │ │ 动态 topK → 父块回表     │ │ 历史 5 轮 / 来源列表  │
└───┬──────────────┘ └────────────────────────┘ └───┬──────────────────┘
    │                      │                        │
┌───▼──────────────────────▼────────────────────────▼───────────────────┐
│ 数据层                                                                │
│  MySQL 8.0.43 (3307)  ：用户/会话/消息/知识库/文档/块/高频问答对        │
│  Milvus 2.4.4 (19531) ：law_chunks（稠密 HNSW + 稀疏倒排）              │
│  Redis 7.2 (6380)     ：验证码 / 高频问答热缓存 / 会话短期上下文        │
│  MinIO (9002, 可选)   ：原始上传文件、图片；不启用则落 data/uploads       │
└───────────────────────────────────────────────────────────────────────┘
        ▲
        │ 离线链路（与在线链路解耦，可单独跑 CLI）
┌───────┴───────────────────────────────────────────────────────────────┐
│ 摄入层  Ingest Pipeline                                               │
│  加载(PDF/MD/PPT/图片) → 清洗 → 康熙部首归一 → 结构识别(编章节条)      │
│  → 父块(条) → 子块(款/项) → bge-m3 向量化 → Milvus 双写 → MySQL 元数据 │
│  增量：SHA1 比对，未变跳过；变更则按 doc_id 整篇删除重建               │
└───────────────────────────────────────────────────────────────────────┘
```

### 1.2 在线问答时序（一次请求的完整数据流）

```
用户输入
  │
  ├─ POST /api/chat/stream  {session_id, question, kb_ids[], params}
  │
  ▼
[1] 参数校验 + JWT 校验 + 会话归属校验（防越权改 session）
  │
[2] 问题归一化（去标点/全角转半角/停用词）
  │
[3] Redis 缓存精确命中？ key=qa:exact:{sha1(norm)}
  │    命中 → 直接 SSE 回放缓存答案（cached=true, 毫秒级）
  ▼ 未命中
[4] 意图识别 + Query 改写（1 次 LLM 调用，JSON 模式）
  │    输出：intent / rewrite_type / sub_queries[] / complexity
  │          / hit_structured / structured_topic / law_hints[]
  │    失败/超时(3s) → 关键词规则兜底
  ▼
[5] 数据源路由（见 §6.3 路由矩阵）
  │
  ├─ 结构化链路：BM25 召回 top3 → bge-m3 余弦 ≥0.90 → 命中则取标准答案
  │                （未命中但 hit_structured=true → 按 category 取 2 条同类参考）
  │
  ├─ 向量链路：每个 sub_query
  │      ├ 编码 1 次 → dense(1024) + sparse(词权重)
  │      ├ 并行 AnnSearch：dense / sparse，各取 pool
  │      ├ RRF 融合 → pool = max(4×topK, 12)
  │      ├ reranker 重排（query × title+child）
  │      ├ 取 topK（simple 3 / medium 5 / complex 8）
  │      └ 按 parent_id 批量回表取完整法条 → 去重
  │
  ▼
[6] 上下文装配（法名·编/章/节·条号 + 条文正文），token 预算 ≤ 6k
  │
[7] 生成：system(法律领域约束) + 历史(≤N 轮) + context + question
  │    SSE 事件：meta → sources → delta×N → done
  │
[8] 落库：chat_messages（含 sources/intent/耗时/token）
     + Redis 写缓存(TTL 7d) + qa_pairs.hit_count+1
```

### 1.3 关键工程约束

- **两条链路独立降级**：Milvus 不可用时结构化链路照常回答，SSE 中带 `degraded: "vector_unavailable"`；反之亦然。
- **模型懒加载 + 显存预算**：bge-m3(≈2.2 GB fp16) 与 reranker(≈2.2 GB fp16) 不同时常驻；reranker 首次使用时加载，空闲 10 分钟释放。8 GB 显存留 1.5 GB 余量。
- **测试全离线**：单测用 `DummyEmbedder`（确定性的哈希伪向量）与 mock LLM，不加载真实模型、不调 DashScope。只有 `eval/` 与手动问答才用真模型。

### 1.4 目录结构（规划）

```
LawAnswer/
├── docker-compose.yml            # MySQL/Redis/Milvus(+etcd+minio)/可选 MinIO
├── .env.example / .env           # 非敏感配置；API Key 不落 .env
├── pyproject.toml                # uv 管理；[dependency-groups] dev / eval
├── config.py                     # pydantic-settings，唯一配置入口
├── app/
│   ├── main.py                   # FastAPI 装配、生命周期、中间件
│   ├── core/                     # config_loader / logger / security / exceptions / sse
│   ├── db/                       # mysql(engine, session, models) / milvus(client, schema) / redis
│   ├── api/                      # auth / sessions / chat / kbs / documents / chunks / meta
│   ├── ingest/                   # loaders / cleaner / normalizer / structure / chunker
│   │                             # / embedder / writer / pipeline / incremental
│   ├── retrieval/                # hybrid(dense+sparse) / rrf / rerank / parent_expand / qa_retriever
│   ├── orchestration/            # intent(LLM) / rules(兜底) / router / context_builder
│   ├── generation/               # prompts / llm_client / stream / history
│   └── eval/                     # recall / intent_eval / ragas_runner / loadtest
├── frontend/                     # Vue3 + Vite + TS
├── data/{src,raw,uploads}
├── eval/{qa_test_cases.jsonl, intent_cases.jsonl, results/}
├── mysql/init/                   # 建表 + 高频问答对种子数据
├── logs/                         # app.log / qa.log / ingest.log / error.log
├── tests/                        # test_*.py
├── scripts/                      # download_models.py / bootstrap.ps1 / demo.py
└── REPORT.md                     # 评估结果留档（阶段 7 产出）
```

### 1.5 端口与容器规划（默认方案，可被 §11 的 Q1 覆盖）

| 服务 | 端口 | 容器名 | 数据卷 | 说明 |
|---|---|---|---|---|
| MySQL | **3307**→3306 | lawanswer-mysql | `lawanswer_mysql_data` | 库名 `lawanswer`，root 密码走 .env |
| Redis | **6380**→6379 | lawanswer-redis | `lawanswer_redis_data` | 使用 db 3，避免与 shopmate 混淆 |
| Milvus | **19531**→19530（gRPC）/ **9092**→9091 | lawanswer-milvus | `lawanswer_milvus_data` | database `lawanswer` |
| etcd | 不暴露 | lawanswer-etcd | 匿名卷 | Milvus 依赖 |
| MinIO | **9002**→9000 / **9003**→9001 | lawanswer-minio | `lawanswer_minio_data` | 可选，见 Q3 |
| FastAPI | **8101** | 本地 uv 运行 | — | 8000/8100 被 Windows 系统保留，实测无法 bind（WinError 10013） |
| Vue Dev | **5180** | 本地 vite | — | 5173 被占用 |

**不重复安装的判断依据**：本地已有 MySQL/Milvus/Redis/MinIO 镜像，但**现有容器均属 shopmate 项目且占用默认端口**。为保证 LawAnswer 独立可交付（可单机一键起、不受其他项目启停影响），采用**独立 compose + 偏移端口**；镜像直接复用，不重新拉取。

---

## 2. MySQL 表设计

库：`lawanswer`，字符集 `utf8mb4`，排序 `utf8mb4_0900_ai_ci`，引擎 InnoDB。所有表带 `created_at/updated_at`（`DATETIME(3)`，默认 `CURRENT_TIMESTAMP(3)`）。

### 2.1 `users` —— 用户

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | |
| username | VARCHAR(64) | NOT NULL, UNIQUE | 3-32 位，字母数字下划线 |
| password_hash | VARCHAR(100) | NOT NULL | bcrypt(cost=12)，自带盐 |
| status | TINYINT | NOT NULL DEFAULT 1 | 1 正常 / 0 禁用 |
| last_login_at | DATETIME(3) | NULL | |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：`uk_username (username)`。
说明：不存明文密码、不存盐字段（bcrypt 内置）。不做改密（任务明确不支持），但预留 `password_hash` 可更新。

### 2.2 `chat_sessions` —— 对话会话

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | CHAR(26) | PK | ULID，天然按时间有序，便于列表排序 |
| user_id | BIGINT UNSIGNED | NOT NULL | 归属用户 |
| title | VARCHAR(128) | NOT NULL DEFAULT '新对话' | 可自定义；首轮问题可自动补名 |
| kb_ids | JSON | NOT NULL | 勾选的知识库 id 数组，`["kb_labor","kb_civil"]` |
| model | VARCHAR(64) | NOT NULL DEFAULT 'deepseek-v4.1-flash' | 会话级模型 |
| temperature | DECIMAL(3,2) | NOT NULL DEFAULT 0.30 | 0.00–1.99 |
| top_p | DECIMAL(3,2) | NOT NULL DEFAULT 0.80 | |
| max_tokens | INT | NOT NULL DEFAULT 2048 | 256–8192 |
| history_rounds | TINYINT | NOT NULL DEFAULT 5 | 0–20 |
| message_count | INT | NOT NULL DEFAULT 0 | 冗余计数，列表页免 count |
| deleted_at | DATETIME(3) | NULL | 软删除 |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：`idx_user_updated (user_id, deleted_at, updated_at DESC)` —— 直接支撑"我的会话列表按最近活跃排序"。

### 2.3 `chat_messages` —— 消息（含溯源与可观测字段）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | |
| session_id | CHAR(26) | NOT NULL | |
| seq | INT | NOT NULL | 会话内序号，从 1 递增 |
| role | ENUM('user','assistant','system') | NOT NULL | |
| content | MEDIUMTEXT | NOT NULL | 回答正文（Markdown） |
| sources | JSON | NULL | `[{law, chapter_path, article_no, chunk_id, score, rank}]` |
| intent | VARCHAR(32) | NULL | 本条命中的意图 |
| rewrite_type | VARCHAR(16) | NULL | direct/history/hyde/simplify/decompose |
| route | VARCHAR(16) | NULL | structured / vector / hybrid / fallback |
| cached | TINYINT | NOT NULL DEFAULT 0 | 是否缓存命中 |
| latency_ms | INT | NULL | 首 token 前总耗时 |
| ttft_ms | INT | NULL | 首 token 时间 |
| token_usage | JSON | NULL | `{prompt, completion, total}` |
| error | VARCHAR(255) | NULL | 流式中断等异常信息 |
| created_at | DATETIME(3) | NOT NULL | |

索引：`idx_session_seq (session_id, seq)`；`idx_created (created_at)`（评估与统计用）。
**为什么落 `intent/route/ttft_ms/cached`**：这是简历上"可量化"的数据来源——意图识别准确率、路由命中率、TTFT P95 都从这张表出，不用另建埋点系统。

### 2.4 `qa_pairs` —— 高频问答对（结构化缓存）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT UNSIGNED | PK, AUTO_INCREMENT | |
| category | VARCHAR(32) | NOT NULL | 试用期/加班费/经济补偿/竞业限制/劳务派遣/裁员/三期保护/工伤/社保/仲裁时效/合同订立/合同解除 |
| question_std | VARCHAR(255) | NOT NULL | 标准问法 |
| question_variants | JSON | NOT NULL | 口语化变体 3–6 条，BM25 索引来源 |
| answer | TEXT | NOT NULL | 标准答案（含"《法律名》第X条"引用） |
| law_refs | JSON | NOT NULL | `[{"law":"中华人民共和国劳动合同法","article":"第十九条"}]` |
| kb_id | VARCHAR(32) | NULL | 归属知识库（多为 kb_labor） |
| intent | VARCHAR(32) | NOT NULL DEFAULT 'labor_law' | |
| keywords | VARCHAR(255) | NULL | 逗号分隔，规则兜底/同义扩展用 |
| hit_count | INT | NOT NULL DEFAULT 0 | 命中计数，用于"热问题"排序 |
| is_active | TINYINT | NOT NULL DEFAULT 1 | |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：`idx_category (category, is_active)`；`idx_hit (hit_count DESC)`；
**`FULLTEXT ft_question (question_std, question_variants) WITH PARSER ngram`** —— 中文全文检索必须用 ngram 解析器，否则按空格分词中文查不到。BM25 打分在应用层用 `MATCH...AGAINST` 拿到 ngram 相关度后再做归一化。
数据量：**60 条**（覆盖 §11-Q8，满足"至少 50"并留 10 条边界用例）。

### 2.5 `knowledge_bases` —— 知识库

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(32) | PK | 语义化 slug：`kb_labor` / `kb_civil`（同时作为 Milvus 分区键） |
| name | VARCHAR(64) | NOT NULL | 劳动法知识库 / 民法典知识库 |
| description | VARCHAR(255) | NULL | |
| embedding_model | VARCHAR(64) | NOT NULL DEFAULT 'bge-m3' | 便于未来换模型时隔离 |
| doc_count / chunk_count | INT | DEFAULT 0 | 冗余计数 |
| created_by | BIGINT UNSIGNED | NULL | |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：`pk_id`；`uk_name (name)`。

### 2.6 `documents` —— 文档（增量更新的锚点）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | CHAR(26) | PK | ULID |
| kb_id | VARCHAR(32) | NOT NULL | |
| filename | VARCHAR(255) | NOT NULL | 原始文件名 |
| file_type | VARCHAR(16) | NOT NULL | pdf/md/pptx/png/jpg |
| file_size | BIGINT | NOT NULL | 字节 |
| **content_sha1** | CHAR(40) | NOT NULL | **归一化正文的 SHA1，增量判定主键** |
| file_sha1 | CHAR(40) | NOT NULL | 原始字节 SHA1，防重复上传 |
| storage_path | VARCHAR(512) | NULL | 本地路径或 MinIO object key |
| status | ENUM('pending','parsing','chunking','embedding','ready','failed') | NOT NULL DEFAULT 'pending' | 前端进度条依据 |
| progress | TINYINT | NOT NULL DEFAULT 0 | 0–100 |
| error_msg | VARCHAR(512) | NULL | |
| parse_config | JSON | NOT NULL | `{chunk_size, overlap, retrieval_mode, parser_version}` |
| article_count / chunk_count | INT | DEFAULT 0 | |
| version | INT | NOT NULL DEFAULT 1 | 每次重建 +1 |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：`idx_kb_status (kb_id, status)`；`uk_kb_content (kb_id, content_sha1)`（同库内内容相同即跳过）。
**增量规则**：上传 → 解析 → 归一化 → 算 `content_sha1` → 若 `uk_kb_content` 已存在且 `status='ready'` 且 `parse_config` 一致 → **直接返回 `skipped`**；若内容变了（同 filename 不同 sha1）→ 旧文档行标记 superseded、Milvus 按 `doc_id` 删除、整篇重建。

### 2.7 `document_chunks` —— 块（MySQL 侧的事实源，支撑预览/编辑/删除）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(64) | PK | `{doc_id}:p{article_no}` / `{doc_id}:p{no}:c{i}` |
| doc_id | CHAR(26) | NOT NULL | |
| kb_id | VARCHAR(32) | NOT NULL | 冗余，便于按库查 |
| parent_id | VARCHAR(64) | NOT NULL | 子块指向父块；父块自指 |
| chunk_type | ENUM('parent','child') | NOT NULL | |
| chunk_index | INT | NOT NULL | 文档内顺序 |
| article_no | VARCHAR(32) | NULL | 第十九条 |
| chapter_path | VARCHAR(255) | NULL | 第一编·第二章·第一节 |
| title | VARCHAR(255) | NULL | 生成上下文用的标题行 |
| content | TEXT | NOT NULL | 父块=整条条文；子块=款/项 |
| char_count | INT | NOT NULL | |
| in_milvus | TINYINT | NOT NULL DEFAULT 1 | 编辑后置 0 → 异步重嵌入 |
| is_enabled | TINYINT | NOT NULL DEFAULT 1 | 软删/禁用，检索时过滤 |
| created_at / updated_at | DATETIME(3) | NOT NULL | |

索引：`idx_doc_index (doc_id, chunk_index)`；`idx_parent (parent_id)`；`idx_kb_article (kb_id, article_no)`。
**设计要点**：编辑块内容 → 更新 MySQL → 同步重新向量化并 upsert Milvus（`chunk_id` 为主键，天然覆盖）；删除块 → `is_enabled=0` + Milvus `delete` by `chunk_id`。这样"预览页可编辑可删除"不会让向量库与关系库漂移。

### 2.8 `ingest_jobs` —— 摄入任务（大文件异步进度）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | CHAR(26) PK | |
| doc_id | CHAR(26) | |
| stage | VARCHAR(32) | parse/clean/chunk/embed/write |
| status | ENUM('running','success','failed') | |
| processed / total | INT | 分块/向量化进度 |
| message | VARCHAR(512) | 失败原因 |
| started_at / finished_at | DATETIME(3) | |

索引：`idx_doc_started (doc_id, started_at DESC)`。
**为什么单独建**：民法典 1260 条解析+向量化预计 3–8 分钟，必须异步，前端轮询 `/documents/{id}` 拿 `status+progress`，而不是让 HTTP 请求挂 5 分钟。

---

## 3. Milvus Collection 设计

### 3.1 隔离方案选型

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. 一库一 collection | `kb_labor` / `kb_civil` | 隔离最彻底 | 新建知识库要建表、跨库检索要并发多路再合并，schema 演进要迁移 N 张表 | ✗ |
| B. 单 collection + `kb_id` 标量过滤 | `law_chunks` + `kb_id in [...]` | 简单，跨库一次查询 | 数据量增大后全表扫描风险 | △ |
| **C. 单 collection + `kb_id` 作 Partition Key** | `law_chunks`，`kb_id` 同时是 partition key + 标量索引 | 跨库检索一次请求完成；物理分区隔离；删除/重建单库高效；schema 只维护一份 | 分区上限 1024（够用） | **✓ 采用** |

Milvus database：`lawanswer`；collection：`law_chunks`；alias：`law_chunks_alias`（重建时可原子切换，避免线上抖动）。

### 3.2 Collection Schema

| 字段 | 类型 | 约束/参数 | 用途 |
|---|---|---|---|
| chunk_id | VARCHAR(64) | **主键** | 与 MySQL `document_chunks.id` 一致 |
| kb_id | VARCHAR(32) | **partition_key=True**, max_length=32 | 知识库隔离 |
| doc_id | VARCHAR(26) | max_length=26 | 增量重建时按 doc 删除 |
| parent_id | VARCHAR(64) | max_length=64 | 回表取整条法条 |
| content | VARCHAR(4096) | max_length=4096 | 子块正文（检索单元的原文，rerank 也要用） |
| title | VARCHAR(255) | enable_analyzer=False | "劳动合同法·第四章·第三十九条" |
| article_no | VARCHAR(32) | | 第十九条（标量过滤/评估对齐） |
| chapter_path | VARCHAR(255) | | 第一编·第二章·第一节 |
| source_file | VARCHAR(255) | | 溯源：原始文件名 |
| law_name | VARCHAR(64) | | 中华人民共和国劳动合同法 |
| chunk_index | INT32 | | 文档内序号 |
| char_count | INT32 | | |
| doc_sha1 | VARCHAR(40) | | 版本号 |
| updated_at | INT64 | | 毫秒时间戳 |
| dense_vector | FLOAT_VECTOR | dim=**1024** | bge-m3 稠密 |
| sparse_vector | SPARSE_FLOAT_VECTOR | — | bge-m3 稀疏（learned lexical weights） |

> 只存**子块**（检索单元）。父块正文留在 MySQL，检索命中后按 `parent_id` 一次 `IN` 查询批量回表。理由：① 避免整条法条在向量库重复存两份；② 父块不可检索，存了只占内存；③ MySQL 回表 <5ms，比多存 1465 条大文本更划算。`content` 保留在 Milvus 是为了 rerank 与"父块缺失时降级"不用再查库。

### 3.3 索引

| 字段 | 索引类型 | 参数 | 说明 |
|---|---|---|---|
| dense_vector | **HNSW** | `M=16, efConstruction=200`, metric **COSINE** | 语料仅 ~2k 条，HNSW 召回率接近 1，延迟 1–3ms；`ef=64` 查询 |
| sparse_vector | **SPARSE_INVERTED_INDEX** | metric **IP**, `drop_ratio_search=0.2` | 法律术语精确匹配（"代通知金""竞业限制"）靠它 |
| kb_id | INVERTED | — | 与 partition key 配合 |
| doc_id | INVERTED | — | 增量删除/重建 |
| article_no | INVERTED | — | 评估与调试时精确取条 |
| source_file | INVERTED | — | 溯源过滤 |

检索时的混合写法：两个 `AnnSearchRequest`（dense 取 pool、sparse 取 pool）+ `RRFRanker(k=60)`，`filter='kb_id in ["kb_labor"] and chunk_type_child'`。
注：本设计只把子块写入 Milvus，故无需 `chunk_type` 过滤字段，`chunk_type` 仅在 MySQL 保留。

### 3.4 稀疏向量的落库细节

bge-m3 输出 `lexical_weights: dict[token_id(str) -> weight(float)]`，词表 250k。
→ 写入前转成 `{indices: [int], values: [float]}`，过滤 `weight < 0.05` 的噪声维度（每条约保留 60–200 维，显著压小索引）。
→ 查询侧同样转换；空稀疏向量（极短 query）时降级为纯稠密检索（避免 Milvus 空向量报错）。

### 3.5 多知识库检索

前端勾选 N 个库 → `filter = kb_id in ["kb_labor","kb_civil"]` → 单次请求返回融合结果。
若勾选为空 → 默认全库（并在前端提示"未选择知识库，将检索全部"）。

---

## 4. 前端设计

技术栈：Vue 3.5 + TypeScript + Vite 7 + Vue Router 4 + Pinia 3 + Element Plus（按需引入，仅用表单/弹窗/上传/滑块）+ 自研 CSS 变量设计系统。不用 Tailwind（避免与 Element 样式权重打架，且页面量少）。

### 4.1 设计语言（全局）

| 维度 | 定义 |
|---|---|
| 基调 | 年轻、克制、专业感的"法务工具"，不玩花哨渐变 |
| 主色 | 深靛蓝 `#2B4ACB`（信任/法律），辅助薄荷绿 `#12B886`（命中/成功），警示珊瑚 `#F76C5E` |
| 中性色 | 背景 `#F7F8FB`，卡片 `#FFFFFF`，正文 `#1C2333`，次要 `#6B7280`，分割线 `#E8EBF2` |
| 圆角/阴影 | 卡片 16px、按钮 10px；阴影 `0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.06)` |
| 字体 | 标题 `Inter + 系统中文黑体`，正文 14px/1.75，法条引用块用等宽感 `ui-monospace` 行内标记 |
| 动效 | 全局 180ms `cubic-bezier(.22,1,.36,1)`；页面入场 `fade-up 8px`；回答**逐字流式**+光标闪烁；加载用三点脉冲而非转圈；列表项 `v-for` 交错 40ms 延迟进场 |
| 布局 | 左侧 240px 图标+文字导航（对话/知识库），右侧内容区；移动端折叠为抽屉 |
| 空态 | 每个页面都有插画级空态文案（"还没有对话，问一个劳动法问题试试"） |

### 4.2 页面清单与交互

#### P1 登录页 `/login`（3 字段）
- 字段：账号名、密码、图形验证码（4 位，canvas 绘制，含干扰线，点击刷新，TTL 120s，服务端存 Redis）。
- 交互：回车提交；密码显隐眼睛图标；错误用字段级红框 + 顶部 toast；成功写 token → 跳 `/chat`。
- 细节：验证码错误自动刷新并清空输入；连续失败 5 次锁定 60s（Redis 计数）。
- 路由守卫：未登录访问任何页 → 重定向 `/login?redirect=...`。

#### P2 注册页 `/register`（4 字段）
- 字段：账号名、密码、确认密码、图形验证码。
- 校验：账号 3–32 位字母数字下划线、实时查重（失焦触发）；密码 ≥8 位含字母+数字；**两次密码不一致时提交按钮禁用 + 行内提示"两次输入的密码不一致"**。
- 成功：toast"注册成功，请登录"→ 跳登录页并回填账号。

#### P3 对话页 `/chat`（核心页）
三栏结构：会话列表（左，可折叠）｜消息流（中）｜参数抽屉（右，默认收起）。
- **知识库多选**：顶部 chip 多选（劳动法知识库/民法典知识库），选中态实心，未选提示"将检索全部"。
- **会话管理**：`+ 新建对话` → 立即创建并聚焦输入框；列表项悬浮出现"重命名/删除"；重命名行内编辑（Enter 保存 / Esc 取消）；删除二次确认弹窗 → 乐观更新。
- **参数面板**：模型下拉（deepseek-v4.1-flash 默认 / qwen-plus / qwen-turbo）；温度滑块 0–1.5（默认 0.3）；topP 滑块 0.1–1（默认 0.8）；最长输出 token（数字输入，256–8192，默认 2048）；历史轮数（0–20，默认 **5**）。参数与会话绑定，切会话自动回填。
- **回答渲染**：Markdown + 法条引用高亮；流式打字（逐 token，非逐字，避免中文断字抖动）；底部"来源"折叠区，列出 `法名·章·条` + 相关度，点击展开该条原文。
- **状态**：思考中显示"正在识别意图…→ 正在检索法条…→ 正在生成"三段式状态条（由 SSE `meta`/`stage` 事件驱动），比单调 loading 更能体现系统能力。
- **容错**：流式中断 → 保留已生成内容 + 显示"回答中断，点击重试"；`degraded` 标记显示"向量库暂不可用，本次仅使用高频问答"。

#### P4 知识库解析页 `/kb/:kbId/parse`
- 上传区：拖拽/点击，支持 PDF/PPTX/MD/PNG/JPG，多文件；显示文件名+大小+删除。
- 解析参数（上传后、开始前配置）：文本切块大小（默认 300，滑杆 100–1000）、重叠大小（默认 50，滑杆 0–200，且强制 `< chunk_size`）、检索模式（单选：稠密向量 / 混合检索（推荐））。
- 开始解析 → 逐文件进度条（解析 20% → 清洗 40% → 分块 60% → 向量化 90% → 完成 100%），失败显示原因 + "重试"。
- 完成后就地显示"共 12 块，其中父块 3 / 子块 9"，附"去预览"按钮。

#### P5 知识库预览页 `/kb/:kbId/doc/:docId`
- 左侧：块列表（虚拟滚动），每项显示 `条号 + 前 40 字 + 类型标签(父/子)`；顶部搜索框（按条号/关键词过滤）、筛选（全部/父块/子块）。
- 右侧：块详情 —— 完整内容（只读态排版）、元数据（来源文件/章节路径/字符数/chunk_index/是否已入向量库）。
- **编辑**：点"编辑"→ 右侧变 textarea → 保存 → 后端重新向量化（前端显示"重新向量化中"角标，成功后转"已同步"）；保存前提示"修改将重新生成向量"。
- **删除**：二次确认 → 乐观移除 + 计数更新 + "已删除，可撤销"（5s 内撤销需后端支持软删恢复；若不做撤销则直接二次确认强删，二选一在 Q 中确认，默认**不做撤销**，改为二次确认 + 明确文案）。

#### P6 知识库管理页 `/kb`
- 卡片网格（每库一张）：库名、描述、文档数、块数、更新时间、进入按钮。
- 新建知识库：弹窗（名称+描述，slug 由名称生成并校验唯一）。
- 进入某库 → 文档列表（表格：文件名/类型/大小/块数/状态/上传时间/操作），行点击 → P5 预览页；操作列：重新解析、删除。
- 文档状态用彩色胶囊：pending 灰 / parsing 蓝闪 / ready 绿 / failed 红（可点开看错误）。

### 4.3 前端目录与状态管理

```
frontend/src/
├── api/            # axios 实例(拦截器注入 token/统一错误) + 各模块 api
├── stores/         # auth / chat / kb（Pinia）
├── router/         # 路由 + 守卫
├── components/     # ChatBubble / StreamMarkdown / SourceList / Captcha / ParamPanel
│                   # ChunkEditor / UploadDropzone / EmptyState / LoadingDots
├── views/          # Login / Register / Chat / KbManage / KbParse / KbPreview
├── composables/    # useSSE(fetch+ReadableStream) / useToast / useDebounce
└── styles/         # tokens.css / base.css / markdown.css
```
SSE 用 `fetch + ReadableStream` 而非 `EventSource`（需要带 Authorization 头 + 支持 POST body + 可中断）。

---

## 5. 数据入库方案

### 5.1 全流程

```
[1] 上传 → 落盘 data/uploads/{kb_id}/{ulid}_{filename} → documents(status=pending)
      │  计算 file_sha1；同库同 file_sha1 且 ready → 直接 skipped
      ▼
[2] 加载（按类型路由）
      PDF    → PyMuPDF(fitz) 抽文本层；若页面文本 < 50 字则判为扫描页 → 可选 OCR（DashScope qwen-vl-ocr）
      MD     → 纯文本读取（优先使用 data/raw/*.md，质量高于 PDF 抽取）
      PPTX   → python-pptx 抽 shape 文本框 + 备注页，按 slide 顺序拼接
      图片   → OCR → 文本
      ▼
[3] 清洗（法律版式专项）
      a. 页眉页脚：对全文行做"高频重复行"统计（出现 ≥3 次且长度 < 40 → 判为页眉/页脚，删）
      b. 页码：纯数字行、`12/34` 形态、"第 3 页 共 5 页"
      c. 网页噪音：`首页 | 宪法 | 人大机构...`、`来源：新华网 浏览字号`、`加入收藏/网站声明/京ICP/版权所有`
      d. 日期行：`2020/8/23`、`2020年06月02日 08:28:13`
      e. 目录区：从文件头到"首个合法条号出现处"之间的内容整体丢弃，仅用于抽取章节标题（民法典目录在 L31–L387，正文从 L388 起）
      f. 目录点线行：含 `......` 且行尾为数字
      g. 表格块：拆行取单元格，过滤分隔行与页眉行
      ▼
[4] 归一化（关键，缺一不可）
      a. 康熙部首变体 → 标准汉字：U+2F00–U+2FD5 批量 NFKC 映射 + 手工补 `⺠→民 ⻅→见 ⻓→长 ⻔→门`
         （实测：劳动法 Markdown 含 1122 个变体字符；不处理只能识别 6/107 条）
      b. 全角空格 U+3000 → 普通空格；制表符 → 空格
      c. 中文之间的多余空格删除（PDF 标题"总     则"→"总则"）
      d. PDF 折行合并：行尾汉字 + 行首汉字 → 直接拼；行尾标点/数字 + 行首字母数字 → 加空格拼
      e. 全角数字/字母 → 半角；去除零宽字符 U+200B/200C/FEFF
      ▼
[5] 结构识别（编→章→节→条）
      正则（在归一化后的 strip 行上匹配）：
        第X编  ^第([一二三四五六七八九十百零]+)编[\s　]+(.*)$
        第X章  ^第([...]+)章[\s　]+(.*)$
        第X节  ^第([...]+)节[\s　]+(.*)$
        第X条  ^第([...]+)条[\s　]+(.*)$
      三重防误判：
        ① 要求"条"后必须跟空白 → 排除正文折行处的交叉引用（"第三十九条和第四十条…"）
        ② 条号单调递增校验：新条号 < 当前条号 → 判为交叉引用，按正文处理
        ③ 正文起点判定：首个通过 ①② 的条号之前的全部内容视为目录/元数据
      ▼
[6] 父块切分（按条边界，绝不截断）
      父块 = 一条完整法条（含"第X条"头 + 全条款项）
      chapter_path = "第一编·第二章·第一节"（无编/节时自动省略）
      title = "中华人民共和国劳动合同法·第四章·第三十九条"
      ▼
[7] 子块切分（条内款/项）
      规则优先级：
        ① 条文长度 ≤ chunk_size → 子块 = 父块（内容完全相同，parent_id 自指，仅 1 条记录）
        ② 否则按"款"边界（换行/`（一）`/`1.`）切，累计长度逼近 chunk_size 时断
        ③ 单款仍超长 → 按句号/分号二次切
        ④ 相邻子块间取 overlap 字符（从尾部回退，对齐到句号）
      每条子块 content 前缀注入 title（"第三十九条〔用人单位单方解除〕"），提升检索语义
      ▼
[8] 向量化
      bge-m3.encode(batch, return_dense=True, return_sparse=True, return_colbert=False)
      批大小 16（8 GB 显存，fp16）；长文本 max_length=512 截断；进度按 batch 回调写 ingest_jobs
      ▼
[9] 双写
      事务顺序：MySQL 先写 document_chunks（事实源）→ Milvus upsert → 回写 documents.status=ready
      Milvus 写入 batch=256，flush 后校验 count == 期望（不一致则整篇回滚重来）
      ▼
[10] 完成：documents.article_count / chunk_count / content_sha1 落定，kb 冗余计数刷新
```

### 5.2 增量更新与溯源

| 场景 | 判定 | 动作 |
|---|---|---|
| 重复上传同一文件 | `uk_kb_content (kb_id, content_sha1)` 命中且 `ready` | 返回 `skipped`，不重建，前端提示"内容未变化，已跳过" |
| 同文件名内容更新 | filename 相同、`content_sha1` 不同 | 旧 `documents` 行置 `superseded` → Milvus `delete(doc_id==old)` → 整篇重建（新 doc_id） |
| 解析参数变更（如 chunk_size 300→500） | `parse_config` 不一致 | 视同内容变更，整篇重建（`version+1`） |
| 单块编辑 | 前端 PATCH | MySQL 更新 → 该块重新向量化 → Milvus upsert（同 chunk_id 覆盖） |
| 单块删除 | 前端 DELETE | `is_enabled=0` + Milvus delete by chunk_id |
| 文档删除 | 前端 DELETE | Milvus delete by doc_id → MySQL 级联删除块 → 刷新计数 |

**溯源链完整可查**：任意一条回答的每个来源都能反查到 `chunk_id → doc_id → filename → content_sha1 → 原始文件行号区间`。`source_file` 与 `article_no` 同时写入 Milvus，检索结果直接可展示，不需要回表才能知道出处。

### 5.3 分块参数对比设计（写进 REPORT.md 的消融）

基于实测语料（145,596 字 / 1,465 条 / 平均 97 字 / 最长 454 字）：

| 组 | 策略 | child_size / overlap | 预计子块数 | 设计意图 |
|---|---|---|---|---|
| A | 父子分块 | 200 / 40 | ≈1,700 | 细粒度，长条文被切细，召回更精准但上下文易碎片化 |
| **B** | **父子分块** | **300 / 50** | **≈1,550** | **推荐**：兼顾法条完整与检索精度（P90=186 字，多数条文仍为整条） |
| C | 父子分块 | 500 / 80 | ≈1,470 | 几乎等于父块，父子退化，作为"参数无效应"的对照 |
| D | 固定窗口（baseline） | 500 / 50 | ≈324 | 反例：必然截断跨窗法条，用来证明"按条切分"的必要性 |

评估口径统一为**条级 Recall@K**（命中任一子块即该条命中），四组跑同一套 40 条 QA，输出 §7 的对比表。预期结论（也是答辩时最有说服力的一段）：D 组 chunk 少、检索快，但 Recall@5 明显低于 B 组，因为"第四十七条"被切断后语义不完整；A 组精度略高但上下文碎片导致 RAGAS 忠实度下降。**最终选 B**。

---

## 6. 检索与生成链路

### 6.1 意图识别 + Query 改写（单次 LLM 调用）

一次调用完成 6 件事，JSON 模式强约束输出：

```json
{
  "intent": "labor_law | civil_code | general | greeting | other",
  "rewrite_type": "direct | history | hyde | simplify | decompose",
  "sub_queries": ["最多 3 条，口语化问题改写成法律术语表述"],
  "complexity": "simple | medium | complex",
  "hit_structured": true,
  "structured_topic": "试用期 | 加班费 | 经济补偿 | 竞业限制 | 劳务派遣 | 裁员 | 三期保护 | 工伤 | 社保 | 仲裁时效 | null",
  "law_hints": ["中华人民共和国劳动合同法"],
  "need_history": false
}
```

rewrite_type 判定规则（写进 prompt，让模型自判）：
- `direct`：问题完整、无指代、单跳
- `history`：含"那/这个/具体/继续/还有呢"等指代 → 结合最近 1–2 轮补全实体
- `hyde`：口语化且术语缺失（"老板不给钱怎么办"）→ 生成一段假设性法条表述用于检索
- `simplify`：问题过长且混杂多意图 → 抽主干
- `decompose`：含"分别/以及/和…怎么算"且涉及多法条 → 拆 2–3 个子查询

**规则兜底**（LLM 失败/超时 3s/JSON 解析失败时启用，保证链路不断）：

| 判定项 | 规则 |
|---|---|
| intent | 关键词表命中：`试用期/加班/工资/辞退/裁员/社保/工伤/竞业/年假/仲裁 → labor_law`；`合同/婚姻/继承/侵权/物权/借款/租赁/遗嘱 → civil_code`；`你好/在吗/谢谢 → greeting`；其余 → general |
| complexity | 长度 <15 字且无连接词 → simple；含"分别/以及/和…还/怎么算/依据"或长度 15–40 → medium；长度 >40 或多问号 → complex |
| rewrite_type | 有指代词 → history；否则 direct；sub_queries = [原问题] |
| hit_structured | 命中 `qa_pairs.keywords` 任一 → true |

**量化目标**：意图识别准确率 ≥ 0.90，macro-F1 ≥ 0.88（20 条样本，5 类）。

### 6.2 混合检索 + 重排 + 动态 topK

```
for q in sub_queries:
    dense, sparse = bge_m3.encode(q)                    # 1 次前向，同时产出
    pool = max(4 * topK, 12)                            # 3→12, 5→20, 8→32
    req_dense  = AnnSearch(dense_vector,  COSINE,  limit=pool, filter=kb_filter)
    req_sparse = AnnSearch(sparse_vector, IP,      limit=pool, filter=kb_filter)
    hits = milvus.hybrid_search([req_dense, req_sparse], RRFRanker(k=60), limit=pool)
    # RRF: score = Σ 1/(60 + rank_i)
    cands = dedupe_by_chunk_id(hits)
    scored = reranker.compute_score([[q, f"{h.title}｜{h.content}"] for h in cands])  # batch 16, fp16
    top = sorted(scored)[:topK]                         # simple 3 / medium 5 / complex 8
    parents = mysql.get_chunks([h.parent_id for h in top])   # 1 次 IN 查询
    merge, dedupe by parent_id（同条多子块命中只保留最高分，但拼接其子块以补全）
```

| complexity | topK | 候选池 | 典型场景 |
|---|---|---|---|
| simple | 3 | 12 | "试用期最长多久" |
| medium | 5 | 20 | "试用期被辞退有经济补偿吗" |
| complex | 8 | 32 | "公司裁员，经济补偿怎么算，试用期算在内吗" |

**为什么候选池 ≥ 4×topK 且 ≥12**：reranker 的输入质量决定上限，池太小时重排无从发挥；4 倍是"召回率已饱和、延迟仍可控"的经验平衡点（重排 32 条约 120–200ms on 4060）。

### 6.3 数据源路由矩阵

| 场景 | intent | hit_structured | MySQL | Milvus | 生成策略 |
|---|---|---|---|---|---|
| 纯结构化 | labor_law | ✔ | ✔ | ✘ | 标准答案作"结论"，仅做措辞适配；不走 LLM 全文生成（省时省钱），SSE 回放 |
| 纯法条 | labor_law / civil_code | ✘ | ✘ | ✔ | 标准 RAG |
| 混合 | labor_law | ✔ | ✔ | ✔ | 结构化答案作"结论段" + 法条作"依据段"，合并上下文后一次生成 |
| 跨法 | general | ✘ | ✘ | ✔（全部勾选库） | 多库检索，prompt 要求分法条陈述 |
| 问候 | greeting | ✘ | ✘ | ✘ | 固定引导话术，不调 LLM |
| 无数据 | any | ✘ | 空 | 空 | 引导话术（"暂未在所选知识库中找到依据，建议…"） |

**关键约束**：纯结构化问题**不碰 Milvus**（省 60–120ms 检索+重排）；纯法条问题**不查 MySQL qa_pairs**（省一次 ngram 全文检索）。

### 6.4 结构化链路细节

```
query → 归一化（去标点、去停用词）
      → MySQL: MATCH(question_std, question_variants) AGAINST (? IN NATURAL LANGUAGE MODE) LIMIT 3
      → 取 top1，做二道门：bge-m3 余弦相似度 ≥ 0.90 → 判定"同一问题"
      → 命中：返回标准答案（Redis 写入 qa:exact:{sha1} + qa:ans:{id}，TTL 7d）
      → 未命中但 hit_structured：按 structured_topic 取同类 2 条作参考，与法条检索结果合并
```

Redis key 设计：
| key | 内容 | TTL |
|---|---|---|
| `cap:{captcha_id}` | 验证码文本 | 120s |
| `qa:exact:{sha1(norm_q)}` | 完整答案 JSON | 7d |
| `qa:ans:{qa_id}` | 单条标准答案 | 1d |
| `qa:hot` | ZSET 热问题 id（按 hit_count） | 持久 |
| `login:fail:{username}` | 失败计数 | 60s |

### 6.5 生成层

**System Prompt 结构（中文，约 900 字，分 6 段）**：
1. 角色：中国法律咨询助手，服务对象是普通劳动者/HR。
2. 忠实性：**只能依据【参考资料】回答**；资料未覆盖时明确说"现有资料未涉及"，并给出下一步建议（咨询当地劳动监察/申请仲裁），**严禁编造条文号**。
3. 引用格式：每个结论后用 `（《中华人民共和国劳动合同法》第三十九条）` 形式标注；跨条综合时逐条标注。
4. 结构：先给**结论**（1–2 句），再给**依据**（法条原文摘录），再给**实务提示**（举证要点/时效/风险）；纯问候类不套此结构。
5. 计算类问题（经济补偿/加班费）：给出公式 + 代入示例 + 分情形（如 N、N+1、2N）。
6. 语气：平实、不承诺结果、不做诉讼代理建议；涉及争议提示"具体以裁判机关认定为准"。

**上下文装配格式**：
```
【资料1】《中华人民共和国劳动合同法》·第四章·第三十九条
第三十九条 〔用人单位单方解除劳动合同（过失性辞退）〕劳动者有下列情形之一的…
【资料2】…
```
`title` 里强制带**法律名**，防止两部法律同条号张冠李戴（劳动合同法第39条 ≠ 劳动法第39条）。

**SSE 协议**（`text/event-stream`，前端用 fetch 流读取）：

| event | data | 时机 |
|---|---|---|
| `meta` | `{message_id, intent, route, rewrite_type, complexity, cached, degraded}` | 立即 |
| `stage` | `{stage: "intent"\|"retrieval"\|"rerank"\|"generate"}` | 各阶段开始 |
| `sources` | `[{law, chapter_path, article_no, score, snippet}]` | 检索完成、生成前 |
| `delta` | `{text: "..."}` | 逐 token |
| `done` | `{ttft_ms, latency_ms, token_usage, sources_count}` | 结束 |
| `error` | `{code, message, retriable}` | 异常 |

**流式中断容错**：LLM 上游断流 → 已生成内容照常落库（`error` 字段记录）→ 发 `error(retriable=true)`；客户端断开（AbortController）→ 后端捕获 `ClientDisconnect`，停止读取上游、照常落库，不写 error。

**历史对话**：取当前 session 最近 `history_rounds` 轮（默认 5，即 10 条消息），按 token 预算裁剪（先丢最旧整轮）；超过预算时优先保留最近 2 轮 + 更早的只保留用户问句。

---

## 7. 评估方案

### 7.1 指标总览与目标值

| 类别 | 指标 | 口径 | 目标 |
|---|---|---|---|
| 检索 | Recall@1/3/5/8 | 条级：命中任一子块即该条命中 | @5 ≥ 0.85，@8 ≥ 0.92 |
| 检索 | MRR | 首个正确条排名倒数 | ≥ 0.75 |
| 检索 | 法条截断率 | 子块未覆盖条文全文的比例 | ≤ 3% |
| 意图 | Accuracy / macro-F1 | 5 类，20 条样本 | ≥ 0.90 / ≥ 0.88 |
| 生成 | RAGAS faithfulness | qwen-max 作 judge | ≥ 0.85 |
| 生成 | RAGAS answer_relevancy | bge-m3 作 embedding | ≥ 0.85 |
| 生成 | RAGAS context_precision | — | ≥ 0.80 |
| 生成 | RAGAS context_recall | — | ≥ 0.85 |
| 生成 | RAGAS context_relevance | — | ≥ 0.80 |
| 性能 | TTFT P50 / P95 | 缓存未命中的真实生成 | ≤ 1.2s / ≤ 2.5s |
| 性能 | 端到端 P95 | — | ≤ 8s（complex） |
| 性能 | QPS | 并发 10，30s 稳态 | ≥ 3（受 DashScope 限流约束，见 R7） |
| 性能 | 缓存命中延迟 | Redis 结构化直回 | ≤ 50ms |
| 稳定性 | 错误率 | 压测期间非 200/SSE 异常 | ≤ 1% |

### 7.2 QA 样本设计（40 条，`eval/qa_test_cases.jsonl`）

| 类型 | 数量 | 说明 | 示例 |
|---|---|---|---|
| single_hop 单跳 | 15 | 一问一条，答案在单条法条内 | "试用期最长可以约定多久？" → 劳动合同法第19条 |
| multi_hop 多跳 | 12 | 需 2 条以上法条组合推理 | "试用期被以不符合录用条件辞退，有经济补偿吗？" → 第39条 + 第46条 + 第21条 |
| complex 复杂 | 8 | 多问、需计算、跨法 | "公司裁员，经济补偿怎么算，试用期算在内吗？" → 第41/47/19条 |
| no_answer 无答案 | 5 | 语料未覆盖，考察"不编造" | "离婚时虚拟货币怎么分割？"（民法典未细化） |
| 口语化 / 指代 | （含在以上） | 至少 8 条为纯口语（"老板不给加班费咋整"） | 考察改写能力 |

每条字段：
```json
{"id":"eval_001","type":"single_hop","question":"...","reference_answer":"...",
 "expected_articles":["中华人民共和国劳动合同法#第十九条"],
 "expected_laws":["中华人民共和国劳动合同法"],"sub_questions":[],"difficulty":"easy"}
```
`expected_articles` 用 `法名#条号` 唯一标识，避免跨法同号歧义。

### 7.3 消融实验设计（核心产出）

同一套 40 条样本 × 4 组配置，固定 topK=5、候选池 20：

| 组 | 配置 | 目的 |
|---|---|---|
| L1 | 仅稠密（HNSW 单路） | 基线，衡量稀疏的价值 |
| L2 | 混合不重排（dense + sparse + RRF） | 衡量 RRF 融合的价值 |
| **L3** | **混合 + 重排（bge-reranker-v2-m3）** | **最终方案** |
| L4 | 混合 + 重排 + 父子扩展（完整链路） | 衡量"父块回填"的价值 |
| D | 固定窗口 500/50 + 混合 + 重排 | 衡量"按条切分"的价值 |

输出表：每组 Recall@1/3/5/8、MRR、平均延迟、RAGAS 五项。
预期结论：L3 相比 L1 Recall@5 提升 8–15 个点（法律术语的稀疏信号很强）；L4 相比 L3 在 faithfulness 上提升更明显（整条法条进上下文，模型不易断章取义）；D 组 Recall 最低。

### 7.4 RAGAS 落地细节（ragas==0.3.0）

- 依赖隔离：`[dependency-groups] eval = ["ragas==0.3.0", "langchain-community==0.4.0", "langchain-openai", "langchain-core"]`，**不装进主运行依赖**，避免与 FastAPI/pydantic 版本冲突。
- LLM judge：`LangchainLLMWrapper(ChatOpenAI(base_url=DashScope compatible, model="qwen-max"))`；embedding：`LangchainEmbeddingsWrapper(bge-m3 本地)`。
- 数据集：`EvaluationDataset.from_list([{user_input, response, retrieved_contexts, reference}])`。
- 五项指标：`faithfulness / answer_relevancy / context_precision / context_recall / context_relevance`。
- 缓存：`RunConfig(timeout=180, max_workers=4)` + 本地结果 json 缓存，避免重复付费；中断可续跑。
- 成本预估：40 条 × 5 指标 × 多次重试 ≈ 数百次 judge 调用，约 20–50 元（见 Q7）。

### 7.5 并发压测

自写 asyncio 脚本（比 locust 更贴合 SSE 语义，且能统计 TTFT）：
- 并发档位 1 / 5 / 10 / 20，每档预热 5 请求后跑 30 请求；
- 固定 10 条问题循环（避免缓存全命中导致数据失真 → 用问题变体注入随机后缀，或在压测前清 `qa:exact:*`）；
- 采集：TTFT、总延迟、tokens/s、错误码分布；
- 输出 P50/P95/P99 + QPS + 错误率，写入 `eval/results/loadtest_*.json` 与 REPORT.md。

### 7.6 REPORT.md 结构

```
1. 环境与版本（GPU/驱动/torch/模型/依赖锁版本）
2. 语料统计（1465 条，分块分布直方图）
3. 分块参数对比（A/B/C/D 四组）
4. 检索消融（L1/L2/L3/L4/D）表 + 分析
5. 意图识别混淆矩阵 + 分类报告
6. RAGAS 五项 + 逐条明细（附失败案例分析 3–5 条）
7. 压测（QPS/TTFT/P95 曲线）
8. 结论与后续优化（写明"哪一项没达标、为什么、下一步怎么做"）
```
**要求**：每类指标都保留明细文件（`eval/results/*.json`），REPORT.md 只放汇总 + 分析，可追溯。

---

## 8. 开发阶段计划（8 阶段，一次一个）

> 每阶段结束：跑 `uv run python -m pytest tests/ -v` 全绿 → 向你汇报"改了什么 + 测试结果"→ **等你验收** → 再进入下一阶段。

### 阶段 1：基础设施（1 天）
- **产出**：`pyproject.toml`（Python 3.13、uv、依赖分组 dev/eval）、`config.py`（pydantic-settings）、`app/core/logger.py`（控制台+文件双写、按天轮转、request_id 注入）、`docker-compose.yml`（MySQL 3307 / Redis 6380 / Milvus 19531 / etcd / 可选 MinIO）、`mysql/init/01_schema.sql`（§2 全部表）、Milvus `law_chunks` 建表脚本、`scripts/download_models.py`（bge-m3 + reranker 下载与校验）、`/api/health`。
- **安全**：启动自检 —— 若 `.env` 中出现 `DASHSCOPE_API_KEY` 则拒绝启动并提示"密钥只能放系统环境变量"。
- **测试**：`test_config.py`（配置加载/缺失必填项报错）、`test_logging.py`（双写落盘）、`test_health.py`（含 MySQL/Redis/Milvus 连通性探测，用 mock 隔离）。
- **验收标准**：`docker compose up -d` 三个容器 healthy；`/api/health` 返回 `{mysql:ok, redis:ok, milvus:ok}`；日志文件生成；测试全绿。

### 阶段 2：解析与入库（2–3 天）
- **产出**：loaders（md/pdf/pptx/image）、cleaner（§5.1-3 全部规则）、normalizer（康熙部首等）、structure（编章节条 + 三重心跳校验）、chunker（父子）、embedder（bge-m3 封装 + Dummy 实现）、writer（MySQL+Milvus 双写）、incremental（SHA1）、CLI `python -m app.ingest.pipeline --kb kb_labor`、上传/进度 API。
- **测试**：`test_normalizer.py`（用真实劳动法 md 断言 107 条、康熙部首全部消除）、`test_structure.py`（断章/节/条识别、目录丢弃、交叉引用不误判）、`test_chunker.py`（父块不被截断、短条文子块=父块、overlap 生效、字符上限）、`test_sha1_incremental.py`（同内容跳过 / 变内容重建）。
- **验收标准**：三部法律入库后 `article_count` = 98 / 107 / 1260，`chunk_count` 在预期区间；重复执行 pipeline 幂等（块数不变、不重复写）。

### 阶段 3：检索层（1–2 天）
- **产出**：hybrid retriever（dense+sparse 双路 + RRF）、reranker 封装（含 CPU/GPU 自适应与懒加载）、dynamic topK、parent expand、`/api/retrieval/preview`（调试用，返回召回的条与分数）。
- **测试**：`test_rrf.py`（融合排序正确性、k 值影响）、`test_hybrid_retriever.py`（Dummy embedder + mock Milvus，验证双路调用与去重）、`test_dynamic_topk.py`（三种复杂度 → 3/5/8 与池大小）、`test_parent_expand.py`（去重、缺父块降级）。
- **验收标准**：真实检索 10 个测试问题，Recall@5 ≥ 0.8（阶段内先行指标）；单次检索延迟（不含生成）≤ 400ms。

### 阶段 4：意图识别与路由（1–2 天）
- **产出**：intent LLM 调用（JSON 模式 + 3s 超时 + 重试 1 次）、规则兜底、router（§6.3 矩阵）、qa_retriever（BM25 ngram + 余弦门）、Redis 缓存层、60 条高频问答对种子数据。
- **测试**：`test_intent_llm.py`（mock 响应解析、非法 JSON 降级、超时降级）、`test_intent_rules.py`（10+ 关键词/复杂度用例）、`test_router.py`（6 种路由分支全覆盖）、`test_qa_cache.py`（精确命中/相似命中/不命中三态、TTL）。
- **验收标准**：意图 20 条样本准确率 ≥ 0.9（首次跑，后续阶段回归）；纯结构化问题不产生 Milvus 调用（用 mock 断言调用次数为 0）。

### 阶段 5：生成链路（1–2 天）
- **产出**：prompt 模板（6 段结构）、llm_client（DashScope OpenAI 兼容 + 重试 + 超时）、SSE 服务（§6.5 事件协议）、历史窗口裁剪、来源聚合、兜底话术、7 层容错。
- **测试**：`test_prompt.py`（上下文装配格式、法名不丢失、超预算裁剪）、`test_sse.py`（事件顺序 meta→stage→sources→delta→done、错误事件、中断容错）、`test_history.py`（轮数裁剪、指代补全）、`test_fallback.py`（无资料话术、degraded 标记）。
- **验收标准**：curl 能看到真实流式输出；回答含法条引用；知识库无相关内容时给出引导话术而非编造。

### 阶段 6：前端 6 页（2–3 天）
- **产出**：Vue3 工程 + 设计 tokens + 6 个页面 + 路由守卫 + SSE 流式渲染 + 全部交互态（加载/空/错误/禁用）。
- **测试**：`test_frontend_contract.py`（用 TestClient 校验前端调用的每个接口契约：字段名/类型/状态码，防止前后端漂移）；浏览器 MCP 手工走查登录→对话→上传→预览→编辑→删除全流程并截图留档。
- **验收标准**：6 页全部可走通；流式打字可见；无控制台报错；页签中文。

### 阶段 7：测试与评估（2 天）
- **产出**：`tests/` 全量补齐（目标 ≥60 个用例）、eval 脚本四件套（recall / intent / ragas / loadtest）、40 条 QA + 20 条意图样本、`REPORT.md` + `eval/results/*.json`。
- **验收标准**：`pytest -v` 全绿；REPORT.md §7.1 指标逐项对照目标值，未达标的写明原因。

### 阶段 8：交付与演示（1 天）
- **产出**：`README.md`（架构图 + 5 分钟跑通 + API 文档 + 常见问题）、`scripts/bootstrap.ps1`（一键起容器→建表→下载模型→种子数据）、`scripts/demo.py`（10 个典型问题的自动化演示，覆盖 6 种路由）、`.gitignore`（.env/logs/uploads/.venv）、简历项目描述（3 条 STAR）。
- **验收标准**：删掉容器与库后，按 README 从零跑通 ≤15 分钟；demo 脚本 10/10 通过。

---

## 9. 风险点与对策

| 编号 | 风险 | 概率 | 影响 | 对策 |
|---|---|---|---|---|
| R1 | **bge-m3 实际未下载**（缓存只有 refs），HF 直连不稳定 | 高 | 阻断阶段 2 | 阶段 1 先跑 `download_models.py`：优先 `HF_ENDPOINT=https://hf-mirror.com`，失败切 ModelScope `modelscope download --model BAAI/bge-m3`；下载后校验 `pytorch_model.bin`/`model.safetensors` 存在且 >1GB；仍失败则由你手动放本地目录（配置项指向） |
| R2 | 民法典 1260 条、124k 字，解析+向量化耗时 | 中 | 用户以为卡死 | 异步任务 + `ingest_jobs` 进度 + 前端进度条；预估 3–8 分钟并写在 UI 文案里；支持断点续跑（按 doc_id 查已写入块数跳过） |
| R3 | 法律 PDF 版式：康熙部首（劳动法 1122 处）、页眉页脚、目录、网页导航 | 高 | 条数识别不全、噪音进库 | §5.1 的 7 条清洗规则 + 归一化；用"条数断言"（98/107/1260）做入库硬校验，数量不符直接 failed 并报差异条号 |
| R4 | 端口冲突（19530/6379/9000/8000/5173 全被占） | 高 | 起不来 | 全量偏移端口（§1.5）；启动时端口预检，被占则明确报"请改 .env 中 MILVUS_PORT" |
| R5 | 8 GB 显存同时装 bge-m3 + reranker 可能 OOM | 中 | 崩溃 | fp16 + 懒加载 + 互斥释放 + 环境变量 `EMBED_DEVICE`/`RERANK_DEVICE` 可切 CPU；OOM 自动降级 CPU 并 warn 日志 |
| R6 | ragas 0.3.0 依赖与主应用冲突 | 中 | 装不上/跑不起来 | 独立 dependency-group；`uv sync --group eval` 才装；版本锁死 ragas==0.3.0 + langchain-community==0.4.0 |
| R7 | DashScope 限流与费用 | 中 | 压测/评估失败 | 客户端内置 QPS 限流（默认 3）+ 指数退避重试；评估结果本地缓存可续跑；压测用 20 并发上限并在报告中注明"QPS 受上游配额约束" |
| R8 | Milvus 2.4.4 稀疏向量写入维度/空向量报错 | 中 | 入库失败 | 过滤 `weight<0.05`；空稀疏 → 降级纯稠密；写入后 `count()` 校验 |
| R9 | Windows 路径/编码（长路径、GBK 控制台） | 中 | 中文乱码、FileNotFound | 全链路 `encoding="utf-8"`；路径统一 `pathlib.Path`；日志控制台强制 UTF-8；避开 260 字符长路径 |
| R10 | SSE 被缓冲/前端中断 | 中 | 打字机不生效 | 响应头 `Cache-Control: no-cache` + `X-Accel-Buffering: no`；`media_type="text/event-stream"`；前端 fetch 流式读 + AbortController |
| R11 | 编辑块后向量库与 MySQL 漂移 | 中 | 检索到旧内容 | `in_milvus` 标志 + 重嵌入任务；启动时做一致性巡检（抽样比对 chunk_id 是否存在） |
| R12 | 民法典"编"结构被解析成章 | 中 | 章节路径错、评估对不齐 | 4 级正则 + 单测断言"第一编·第二章·第一节"能正确产出 |

---

## 10. 简历亮点与可量化指标（按模块）

| 模块 | 简历亮点（一句话） | 可量化指标 |
|---|---|---|
| 文档解析与清洗 | 针对法律 PDF 的版式脏数据设计 7 类清洗规则与康熙部首归一，把"第⼀条"类变体字符还原为可识别条号 | 劳动法条号识别率 **6/107 → 107/107**；三部法律条数校验 **98/107/1260 全对** |
| 父子分块 | 以"第X条"为不可切割边界做父块、条内款项为子块，用真实语料分布（中位 76 字）反推 chunk_size，避免"为切而切" | 法条截断率 **≤3%**；相比固定窗口 Recall@5 **+8~15pt** |
| Milvus 设计 | 单 collection + partition key 实现多知识库隔离，稠密 HNSW 与稀疏倒排双索引，chunk 级溯源字段齐全 | 检索延迟 **≤60ms**（pool=20）；**2k 块**规模下 Recall@8 ≥0.92 |
| 混合检索 + 重排 | bge-m3 单次前向同时产出稠密/稀疏，RRF 融合后接 cross-encoder 重排，按问题复杂度动态调 topK 与候选池 | 消融四组对比：Recall@5 稠密 **X** → 混合 **Y** → +重排 **Z**；重排耗时 **≤200ms** |
| 意图识别 + 改写 | 一次 LLM 调用同时产出意图/改写类型/子查询/复杂度/结构化命中，配规则兜底保证 100% 可用 | 意图准确率 **≥0.90**、macro-F1 **≥0.88**；LLM 失败降级成功率 **100%** |
| 双源路由 | 结构化与向量链路按意图分流，纯结构化不触向量库、纯法条不查问答表，混合问题拆分后合并 | 结构化问题端到端 **≤50ms**；Milvus 故障时可用性 **100%** |
| 高频问答缓存 | MySQL ngram 全文 BM25 召回 + 向量余弦双门判定"同一问题"，Redis 缓存热数据 | 缓存命中 **≤50ms**；重复问题命中率 **≥30%**（40 条样本内） |
| 生成链路 | 六段式法律 Prompt（结论/依据/实务提示/计算示例/引用标注/不编造约束），SSE 流式 + 来源列表 | RAGAS faithfulness **≥0.85**；编造率（no_answer 组）**0** |
| 增量更新 | 归一化正文 SHA1 作版本号，未变跳过、变更整篇替换，编辑单块仅重嵌入该块 | 重复入库耗时 **↓95%**（幂等跳过）；单块编辑重嵌入 **≤1s** |
| 评估闭环 | 40 条分难度 QA + 4 组消融 + RAGAS 五项 + 压测，REPORT.md 留档可复现 | Recall@5、MRR、五项 RAGAS、QPS、TTFT P95 全部有数 |
| 工程化 | pydantic-settings 单一配置源、API Key 只读环境变量并做启动自检、日志双写带 request_id、测试全离线不烧钱 | 单测 **≥60 个全绿**；单测运行 **≤30s**（不加载真模型） |

---

## 11. 需要你拍板的决策点（一次问完，回编号即可；推荐项已标 ★）

**Q1 基础设施隔离方式**
现有 Milvus/Redis/MinIO 容器都属于 shopmate 项目且占用默认端口。
- ★ **A. 独立一套 compose，偏移端口**（MySQL 3307 / Redis 6380 / Milvus 19531 / MinIO 9002）：LawAnswer 可独立启停、独立交付，代价是同时跑两套 Milvus 多吃约 2–3 GB 内存
- B. 复用已在跑的栈（Milvus 19530 / Redis 6379），用独立 database 与 Redis db 索引隔离：省内存，但你会被 shopmate 的启停影响

**Q2 bge-m3 权重获取方式**（实测缓存为空，必须下载约 2.2 GB）
- ★ **A. 我写脚本走 hf-mirror.com，失败自动切 ModelScope**
- B. 只用 ModelScope
- C. 你手动下载好，告诉我本地路径

**Q3 MinIO 是否启用**
- ★ **A. 启用**（9002/9003），原始文件与图片入对象存储，`storage_path` 存 object key，置换存储后端只改配置
- B. 不启用，原文件落 `data/uploads/`（更简单，少一个容器）

**Q4 端口**
- ★ **A. 后端 8101、前端 5180**（8000/8100 被 Windows 保留、3000/5173 被 shopmate 占用）
- B. 你指定其他端口

**Q5 知识库划分与命名**
- ★ **A. 两个库：《劳动法知识库》(kb_labor = 劳动合同法 + 劳动法)、《民法典知识库》(kb_civil)** —— 符合"至少两个"且符合用户心智（打工人问的都是劳动法）
- B. 三个库：劳动合同法 / 劳动法 / 民法典，各一个
- C. 其他命名，你给

**Q6 前端技术细节**
- ★ **A. Vue3 + TS + Vite + Pinia + Element Plus（按需）+ 自研 CSS 设计系统**，SSE 用 fetch 流
- B. 不用 Element Plus，纯自研组件（更"年轻化"，但表单/上传/弹窗要多写约 1 天）
- C. 用 JS 不用 TS（省类型成本，但简历少一个亮点）

**Q7 评估期 DashScope 调用预算**（RAGAS 40 条 × 5 指标 + 4 组消融 + 压测，约数百次调用）
- ★ **A. 允许用 qwen-max 作 judge，预算 20–50 元**
- B. judge 降级用 qwen-plus（约省 70%，指标口径需在报告注明）

**Q8 高频问答对数量**
- ★ **A. 60 条**（覆盖 12 个主题，满足"≥50"并留边界用例）
- B. 50 条

**Q9 是否要 Docker 化后端/前端**
- ★ **A. 不需要**：基础设施跑 Docker，后端 `uv run uvicorn`、前端 `npm run dev`，开发调试最快
- B. 需要：额外产出后端与前端 Dockerfile + compose 一体化（多约 0.5 天，演示更"工程化"）

**Q10 块删除是否要"撤销"**
- ★ **A. 不要撤销**：二次确认弹窗 + 明确文案"删除后需重新解析该块"
- B. 要 5 秒撤销：后端保留软删窗口（多一个恢复接口）

---

## 附录 A：本机数据实测明细

| 文件 | 字符数 | 识别条数（归一化后） | 未归一化条数 | 康熙部首字符数 |
|---|---|---|---|---|
| 中华人民共和国劳动合同法.md | 11,753 | 98 | 98 | 0 |
| 中华人民共和国劳动法_….md | 9,739 | **107** | **6** | **1,122** |
| 中华人民共和国民法典.md | 124,104 | 1,260（7 编） | 1,260 | 0 |
| **合计** | **145,596** | **1,465** | 1,364 | 1,122 |

条文长度分布：均值 97 字 / 中位 76 字 / P90 186 字 / 最长 454 字。

## 附录 B：核心配置项清单（`config.py` + `.env`）

| 配置项 | 默认值 | 来源 | 说明 |
|---|---|---|---|
| `DASHSCOPE_API_KEY` | — | **仅系统环境变量** | 启动自检：.env 中出现即拒绝启动 |
| `llm.base_url` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | .env | OpenAI 兼容 |
| `llm.default_model` | `deepseek-v4.1-flash` | .env | 可选 qwen-plus / qwen-turbo |
| `llm.timeout_s / max_retries` | 60 / 2 | .env | |
| `embedding.model_path` | `C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-m3` | .env | **机器相关，可改** |
| `embedding.device / fp16 / batch_size` | `cuda` / true / 16 | .env | OOM 可切 cpu |
| `reranker.model_path` | `models/bge-reranker-v2-m3`（待下载） | .env | 见 Q2 |
| `reranker.top_n / batch_size` | 32 / 16 | .env | |
| `chunk.default_size / overlap` | 300 / 50 | .env | 前端可覆盖单次任务 |
| `retrieval.rrf_k` | 60 | config | |
| `retrieval.pool_factor / pool_min` | 4 / 12 | config | |
| `retrieval.topk_simple/medium/complex` | 3 / 5 / 8 | config | |
| `retrieval.rerank_threshold` | 0.0（不过滤，仅排序） | config | 便于观察 |
| `qa.similarity_threshold` | 0.90 | config | 结构化"同一问题"门限 |
| `qa.bm25_topn` | 3 | config | |
| `cache.qa_ttl` | 604800 | config | 7 天 |
| `mysql.host/port/db` | 127.0.0.1 / 3307 / lawanswer | .env | |
| `redis.host/port/db` | 127.0.0.1 / 6380 / 3 | .env | |
| `milvus.host/port/db/collection` | 127.0.0.1 / 19531 / lawanswer / law_chunks | .env | |
| `minio.*`（可选） | 127.0.0.1 / 9002 | .env | Q3 决定是否启用 |
| `server.port` | 8101 | .env | |
| `log.dir / level / rotation` | `logs/` / INFO / 10MB×5 | config | 双写 |
| `jwt.secret / expire_h` | 环境变量或 .env / 24 | .env | secret 建议同 API Key 走环境变量 |

## 附录 C：API 清单（设计契约，阶段 1–6 逐步落地）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 依赖健康检查 |
| GET | `/api/auth/captcha` | 返回 `{captcha_id, image_base64}` |
| POST | `/api/auth/register` | username/password/confirm/captcha |
| POST | `/api/auth/login` | 返回 JWT + 用户信息 |
| GET | `/api/auth/me` | 当前用户 |
| GET/POST | `/api/sessions` | 列表 / 新建 |
| PATCH/DELETE | `/api/sessions/{id}` | 重命名 / 删除 |
| GET | `/api/sessions/{id}/messages` | 历史消息（分页） |
| POST | `/api/chat/stream` | **SSE 流式问答** |
| GET/POST | `/api/kbs` | 知识库列表 / 新建 |
| PATCH/DELETE | `/api/kbs/{id}` | 改名 / 删除 |
| POST | `/api/kbs/{id}/documents` | 上传（multipart，可多文件） |
| GET | `/api/kbs/{id}/documents` | 文档列表（含状态/进度） |
| GET/DELETE | `/api/documents/{id}` | 文档详情 / 删除 |
| POST | `/api/documents/{id}/reingest` | 重新解析 |
| GET | `/api/documents/{id}/chunks` | 块列表（分页+搜索+筛选） |
| PATCH/DELETE | `/api/chunks/{id}` | 块编辑（触发重嵌入）/ 删除 |
| GET | `/api/meta/models` | 可选模型与参数范围 |
| GET | `/api/retrieval/preview` | 调试：返回召回条与分数（非前端必需） |

## 附录 D：术语与判定口径

- **条级 Recall@K**：检索结果前 K 个块，映射到 `法名#条号` 集合，与 `expected_articles` 求交集非空即命中。多跳问题要求**全部**期望条命中才算命中（严格口径，报告中同时给宽松口径）。
- **法条截断率**：子块 `char_count` 之和 < 父块 `char_count` 且子块未覆盖父块首尾的块数占比。
- **TTFT**：请求进入到首个 `delta` 事件到达的毫秒数（不包含前端渲染）。
- **缓存命中**：返回 `cached=true` 或 `route=structured` 且未调用 LLM。
- **编/章/节/条路径**：`第一编·第二章·第一节`，缺级自动省略；`law_name` 必须与 `chapter_path` 一起进 prompt 标题。

---

**下一步**：请回复第 11 章的 Q1–Q10（可直接写"全部按推荐"）。确认后我从**阶段 1：基础设施**开始，只做阶段 1，跑完全部测试后向你汇报并等你验收。
