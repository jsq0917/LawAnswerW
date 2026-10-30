# LawAnswer 部署到阿里云 ECS 完整方案

> 目标机器：**Alibaba Cloud Linux / CentOS · 2 核 2G · 40G 存储 · 无 GPU · 域名已备案**
> 项目路径规划：代码与模型在 `/opt/lawanswer`，密钥与生产配置在 `/etc/lawanswer/lawanswer.env`

---

## 0. 先说结论：2 核 2G 跑不动这套栈

这不是保守估计，是把每个组件的真实内存占用加起来算的账：

| 组件 | 常驻内存 | 依据 |
|---|---|---|
| Milvus standalone（含 etcd + MinIO 两个依赖容器） | **2.0 – 2.5 G** | 官方 standalone 最低建议 8G |
| MySQL 8 | 0.4 G | 已调 `innodb-buffer-pool-size=128M` |
| Redis 7 | 0.1 G | 已限 `maxmemory 192mb` |
| FastAPI 进程（不含模型） | 0.2 G | |
| bge-m3（CPU 推理，首次检索懒加载） | **2.3 G** | 权重 2.2 G，CPU 上无 fp16 |
| bge-reranker-v2-m3（CPU 推理，懒加载） | **2.3 G** | 权重 2.2 G |
| **合计** | **≈ 7.3 – 7.8 G** | |

2G 物理内存下，**光是 Milvus + MySQL + Redis 就已经吃满**；两个模型一旦被首次检索触发加载，必然 OOM Kill。加 swap 只能防止进程被杀，代价是推理从「秒级」变成「十几秒级」，体验不可接受。

**因此本方案给出三条路线，请先选定再往下做。** 第 1 章其余内容与第 2 章起的所有步骤，对三条路线都是通用的。

### 路线 A：给 ECS 升配（推荐，最省事）

阿里云控制台 → 实例 → **变更配置**（支持在线/停机变配，包月实例当天生效）。建议档位：

| 档位 | 能否跑通 | 体验 |
|---|---|---|
| 4 核 8G | ✅ 刚好够 | 检索约 1–3s/次，QPS ≈ 1–2 |
| **8 核 16G** | ✅ **推荐** | 并发 10 时 QPS ≈ 2–3，与本地 4060 同量级（本项目瓶颈是两次 LLM 调用，不是 CPU） |
| 4 核 4G | ❌ | Milvus 起不来 |

参考成本：8 核 16G 通用型（g7）包月约 ¥300–400/月；**如果只是简历演示用，建议选「按量付费 + 用完释放」**，演示几小时成本仅几元。

> 无需 GPU。本项目 CPU 与 GPU 的差别只在"向量化 250ms vs 28ms"，对单次问答的端到端延迟影响很小（瓶颈在 LLM）。

### 路线 B：架构拆分（2G 保留，但要改代码）

保留 2G 机器跑应用，把存储层换成阿里云托管服务：

- MySQL → 阿里云 RDS（最低 1 核 1G 规格，约 ¥30/月）
- Redis → 阿里云 Tair/Redis 社区版（最低 1G，约 ¥20/月）
- Milvus → 阿里云向量检索服务 Milvus 版，或自建在另一台机器

**但还不够**：应用侧两个模型仍要 4.6 G 内存。必须再做下面至少一项改造，都要动代码：

- 把 bge-m3 换成 API 调用（DashScope `text-embedding-v3`，1024 维可直接对齐）→ 改 `app/ingest/embedder.py`
- 彻底关掉 reranker（`app/retrieval/service.py` 里 `use_rerank` 目前是函数参数、无环境变量开关，需加一个配置项 + 改调用点）

改造成本高、且会偏离 `REPORT.md` 里已验收的评估口径（Recall@5 0.7071 依赖 reranker）。**除非有特殊理由，不建议。**

### 路线 C：仅作展示（成本最低）

把前端 `dist` 静态页 + 少量预生成样例数据部署到 2G 机器做展示，完整问答链路仍在本机跑。适合"放个链接给别人看界面"。

---

**以下内容默认按路线 A（升配到 8G+）编写。** 如果是路线 B/C，存储层地址换成托管服务的连接串即可，其余步骤一致。

---

## 1. 部署前准备：本地要做什么

### 1.1 确认代码已提交

项目已接 GitHub 远端：`git@github.com:jsq0917/LawAnswerW.git`。上传前先在本地提交干净：

```bash
cd E:/project/LawAnswer
git status                  # 确认无未提交改动（当前工作区是干净的）
git log --oneline -3
```

### 1.2 本地构建前端（重要，别在服务器上构建）

服务器只有 2 核，`npm install` + `vue-tsc` 会非常慢，且 `node_modules` 有 7500+ 文件。**在本地构建好再上传 `dist`**：

```bash
cd E:/project/LawAnswer/frontend
npm install
npm run build               # 产物输出到 frontend/dist
ls dist/                    # 应有 index.html + assets/
```

> 为什么必须构建：`frontend/src/api/http.ts` 的 `baseURL` 是 `/api`（相对路径），生产环境由 nginx 反代 `/api` 到后端；`router/index.ts` 用的是 `createWebHistory()`（history 模式），因此 nginx 必须配 `try_files ... /index.html` 回退，否则 `/chat` 刷新 404。这两点后文的 nginx 配置都已处理。

### 1.3 确认模型不进 Git

`models/`、`volumes/`、`.venv/`、`frontend/node_modules` 都在 `.gitignore` 里，**不会被上传**。模型共 4.4 G，走"服务器端下载"而非"从本地上传"（见 §3.3）。

### 1.4 打包代码（不含模型与体积产物）

```bash
cd E:/project/LawAnswer
# 在 Git Bash 下打包，排除大目录
tar --exclude='.venv' --exclude='models' --exclude='volumes' \
    --exclude='frontend/node_modules' --exclude='.git' \
    --exclude='logs' --exclude='__pycache__' --exclude='*.pyc' \
    -czf /tmp/lawanswer-src.tar.gz .
ls -lh /tmp/lawanswer-src.tar.gz    # 约 5–8 MB（含前端 dist）
```

> 注意：`data/raw/*.md`（三部法律，约 417 KB）和 `mysql/init/*.sql`（建表与种子数据）**必须包含**，它们是首次启动的必需数据。上面的排除清单里没有它们，是故意的。

---

## 2. 阿里云控制台侧配置

### 2.1 安全组规则（最容易出错的一步）

实例 → 安全组 → 配置规则 → 入方向：

| 端口 | 协议 | 授权对象 | 用途 |
|---|---|---|---|
| 22 | TCP | 你的本地公网 IP/32（**别用 0.0.0.0/0**） | SSH |
| 80 | TCP | 0.0.0.0/0 | HTTP / ACME 证书校验 |
| **443** | TCP | 0.0.0.0/0 | HTTPS |

**以下端口一律不要放行**，它们已全部绑定在 `127.0.0.1`，只允许本机访问：

```
3307 MySQL    6380 Redis    19531 Milvus    8101 FastAPI
9002/9003 MinIO               9092 Milvus metrics
```

> 为什么强调：`docker-compose.yml` 原始配置里端口是 `"3307:3306"`，**默认监听 0.0.0.0**。本方案提供的 `deploy/docker-compose.server.yml` 覆盖层已把它们改为 `"127.0.0.1:3307:3306"`，双保险。

### 2.2 域名解析

域名控制台 → 添加 A 记录：`lawanswer.example.com` → ECS 公网 IP。

```bash
# 本地验证解析生效
nslookup lawanswer.example.com
```

备案本身不需要额外操作，但**域名未备案时 80/443 会被阿里云拦截**——你已备案，此步跳过。若备案尚未同步完成，可先用 `http://IP:端口` 临时验证。

### 2.3 确认系统盘容量

```bash
df -h /
```

40 G 够用（估算：Docker 镜像 2.2 G + 模型 4.4 G + venv 约 3.5 G + 系统 5 G + 数据 0.5 G ≈ **15.6 G**）。剩下约 24 G 余量，可安全划 4 G 作 swap。

---

## 3. 服务器上的配置与文件上传

### 3.1 一键初始化系统环境

把 `deploy/server-init.sh` 上传后执行：

```bash
# 本地
scp E:/project/LawAnswer/deploy/server-init.sh root@<公网IP>:/root/

# 服务器
ssh root@<公网IP>
bash /root/server-init.sh
```

脚本幂等，做四件事：

1. **Docker + compose plugin**（走阿里云镜像源），并写入 `/etc/docker/daemon.json` 配置国内镜像加速 —— 不配这个，拉 `milvusdb/milvus`、`quay.io/coreos/etcd` 会超时；同时给容器日志设 20 M 上限，防止日志吃满磁盘。
2. **uv**（Python 3.13 由 uv 自动管理，无需系统装 Python 3.13）。
3. **nginx + certbot**（certbot 需 EPEL 源，脚本已处理）。
4. 创建**不可登录的系统用户 `lawanswer`** 与目录 `/opt/lawanswer`、`/etc/lawanswer`（权限 750）、`/var/www/certbot`。

脚本最后会提示加 swap，建议照做（4 G）：

```bash
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl -w vm.swappiness=10
```

### 3.2 上传代码

```bash
# 本地：上传压缩包
scp /tmp/lawanswer-src.tar.gz root@<公网IP>:/tmp/

# 服务器：解包到项目目录
cd /opt/lawanswer && tar -xzf /tmp/lawanswer-src.tar.gz
# 只改代码/配置，volumes/ 交给容器自己的 uid（redis=999、mysql=999）
# 切勿 chown -R /opt/lawanswer：属主变 994 后 redis bgsave 会 Permission denied
find /opt/lawanswer -maxdepth 1 -mindepth 1 ! -name volumes -exec chown -R lawanswer:lawanswer {} +
ls /opt/lawanswer        # 应看到 app/ config.py data/ frontend/dist mysql/ scripts/ 等
```

> 备选：直接在服务器 `git clone git@github.com:jsq0917/LawAnswerW.git`。但国内访问 GitHub 经常不稳，且需要配 SSH key，故推荐上面的打包上传方式。

### 3.3 下载模型（在服务器上下载，不要从本地上传）

**前置**：需要先完成 §3.4（写好 `lawanswer.env`）与 §4.1（装好 venv，否则没有 `huggingface_hub`）。如果你顺着往下读，先跳到那两节做完再回来执行本节。

共 4.4 G，用项目自带的下载脚本走 `hf-mirror.com` 镜像（服务器在国内，比从本地上传快得多）：

```bash
sudo -u lawanswer lawanswer-python -m scripts.download_models
```

> `lawanswer-python` 会从 `lawanswer.env` 读 `EMBEDDING_MODEL_PATH` / `RERANKER_MODEL_PATH`（已指向 `/opt/lawanswer/models/...`），无需手工传路径。
> 脚本自动三级回退：`hf-mirror.com` → 官方 → 提示 ModelScope；下载后**校验 safetensors 是否被截断**（只比文件大小不可靠，中断会留下"看着有大小、张量不全"的文件）。

下载完成后必须校验：

```bash
sudo -u lawanswer lawanswer-python -m scripts.download_models --check
# 期望输出：[ok] bge-m3 ... 就绪 / [ok] reranker ... 就绪
```

若下载失败（网络问题），手动方案：浏览器访问 `https://hf-mirror.com/BAAI/bge-m3` 下载全部文件（**跳过 `onnx/` 目录**），放到 `/opt/lawanswer/models/bge-m3`；reranker 同理放到 `/opt/lawanswer/models/bge-reranker-v2-m3`。放好后 `chown -R lawanswer:lawanswer /opt/lawanswer/models`。

**注意路径必须改**：`.env.example` 里的 `EMBEDDING_MODEL_PATH` 是 `C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-m3`（Windows 路径），在 Linux 上会直接加载失败。本方案的模板已改成 Linux 路径。

### 3.4 写生产配置（含密钥）

```bash
# 上传模板
scp E:/project/LawAnswer/deploy/production.env.example root@<公网IP>:/tmp/
ssh root@<公网IP>

# 安装到 /etc/lawanswer/，权限 600、属主 root
install -o root -g root -m 600 /tmp/production.env.example /etc/lawanswer/lawanswer.env
vi /etc/lawanswer/lawanswer.env
```

**必须替换的占位项**：

| 配置项 | 怎么填 |
|---|---|
| `JWT_SECRET` | `openssl rand -hex 32` 的输出。**`APP_ENV=prod` 时若仍是默认值，应用会直接拒绝启动**（`config.py` 的 model_validator 强制校验） |
| `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD` | 强密码 |
| `LLM_API_KEY` | 你的大模型密钥（或改用 `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY`） |
| `CORS_ORIGINS` | `https://你的域名`（不要尾斜杠） |
| `EMBEDDING_MODEL_PATH` | `/opt/lawanswer/models/bge-m3` |
| `RERANKER_MODEL_PATH` | `/opt/lawanswer/models/bge-reranker-v2-m3` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 强口令（MinIO 是 Milvus 内部依赖，仍需一致） |

**关于密钥位置（关键）**：项目有启动自检 `config.assert_secrets_not_in_dotenv()` —— 只要**项目根目录的 `.env`** 里出现密钥赋值行，应用**拒绝启动**。所以：

- ✅ 密钥写在 `/etc/lawanswer/lawanswer.env`（systemd `EnvironmentFile` 注入为系统环境变量）
- ❌ 不要把密钥写进 `/opt/lawanswer/.env`
- 💡 最干净的做法：**生产环境根本不创建 `/opt/lawanswer/.env`**，所有配置都走 `/etc/lawanswer/lawanswer.env`（环境变量优先级高于 `.env` 文件）

### 3.5 让基础设施容器读到同一份密码

`docker compose` 从项目根目录的 `.env` 读变量（`${MYSQL_ROOT_PASSWORD:-...}` 这类）。但上面说了不能把密钥放项目 `.env` —— **MySQL/Redis/MinIO 的密码不属于启动自检拦截范围**（自检只认 `LLM_API_KEY` / `DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `API_KEY` / `OPENAI_API_KEY`），所以可以单独建一个只给 compose 用的文件：

```bash
cd /opt/lawanswer
cat > .env <<'EOF'
MYSQL_ROOT_PASSWORD=<与 lawanswer.env 一致>
MYSQL_PASSWORD=<与 lawanswer.env 一致>
MINIO_ACCESS_KEY=<与 lawanswer.env 一致>
MINIO_SECRET_KEY=<与 lawanswer.env 一致>
EOF
chown lawanswer:lawanswer .env && chmod 600 .env
```

> 注意：**这个文件里绝对不能出现 `LLM_API_KEY=` 之类行**，否则应用启动自检会拒绝。自检范围见 `config.py` 的 `SECRET_ENV_NAMES`。

---

## 4. 项目的启动与运行步骤

### 4.1 安装 Python 依赖

```bash
cd /opt/lawanswer
uv sync
```

**无 GPU 机器不需要任何特殊处理**：`pyproject.toml` 里 `torch>=2.14.1` 默认就是 CPU 版（约 200 MB），直接 `uv sync` 即可。本地那套 `--no-install-package torch` 的 GPU 保护逻辑**在服务器上不适用也不需要**（服务器上没有 GPU 版 torch 可保）。

> 磁盘占用：venv 约 3.5 G（torch CPU + transformers + FlagEmbedding 等），40 G 盘充裕。

### 4.2 启动基础设施容器

用**项目自带 compose + 服务器覆盖层**（覆盖层负责两件事：所有端口绑 `127.0.0.1`、用 `mem_limit` 给每个容器设内存上限）：

```bash
cd /opt/lawanswer
docker compose -f docker-compose.yml -f deploy/docker-compose.server.yml up -d
docker compose ps        # 等到 mysql / redis / milvus 三个都是 healthy
```

首次启动要拉 5 个镜像，约 2.2 G。若某个镜像拉取失败，重试即可（`daemon.json` 的镜像加速已在 §3.1 配好）。

验证内存限制真的生效（`LIMIT` 列应为你设的上限，而非整机内存）：

```bash
docker stats --no-stream
# 或
docker inspect --format '{{.HostConfig.Memory}}' lawanswer-milvus   # 应为 2147483648（2G），0 表示未生效
```

> 注意：`mem_limit` 是服务级字段，`docker compose up` 直接生效。它只是**上限**，不等于实际占用——设了限制不代表内存就够用，内存账见 §0。

观察内存水位（此时基础设施应已用掉 2 G 左右，8 G 机器无压力）：

```bash
free -h
docker stats --no-stream
```

### 4.3 等 Milvus 就绪

Milvus 首次启动较慢（要初始化 etcd + MinIO）：

```bash
# 反复执行直到返回 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9092/healthz

# 端口探活
ss -lntp | grep -E '3307|6380|19531'
```

### 4.4 建表 + 建向量集合

`server-init.sh` 已安装一个便捷执行器 `lawanswer-python`，它会自动读取 `/etc/lawanswer/lawanswer.env` 里的全部生产变量，再执行项目模块。下面都用它：

```bash
# ① MySQL 建表（幂等：CREATE TABLE IF NOT EXISTS）
#   说明：mysql/init/*.sql 会在容器「首次初始化」（数据目录为空）时自动执行一遍；
#   这里再跑一次是双保险，确保 users/chat_sessions/knowledge_bases 等表就位。
sudo -u lawanswer lawanswer-python -m app.db.init_db

# ② 建 Milvus collection（law_chunks）
sudo -u lawanswer lawanswer-python -m scripts.init_milvus
```

> `lawanswer-python` 内部用 `set -a; . /etc/lawanswer/lawanswer.env` 让 shell 自己正确解析引号，
> 比 `env $(grep ... | xargs)` 可靠（后者在密码含空格或特殊字符时会解析错乱）。
> 等 §4.6 装好 systemd 后，日常直接 `systemctl restart lawanswer-api` 即可，无需再手工注入。

### 4.5 首次启动服务并入库

先手工起一次服务，确认健康：

```bash
sudo -u lawanswer lawanswer-python -m app.server
```

另开一个终端验证：

```bash
curl -s http://127.0.0.1:8101/api/health | python3 -m json.tool
# 期望：status=ok，services 下 mysql/redis/milvus 均 ok，llm_key_configured=true
```

健康后，录入三部法律（**首次约 10–20 分钟**，CPU 向量化；三部法律共 1465 条）：

```bash
# 先确认知识库已存在：应含 kb_labor / kb_civil
curl -s "http://127.0.0.1:8101/api/kbs" | python3 -m json.tool

# 逐个入库（幂等：内容未变则按 SHA1 跳过）
cd /opt/lawanswer
for f in data/raw/*.md; do
  name=$(basename "$f")
  if [[ "$name" == *民法典* ]]; then kb=kb_civil; else kb=kb_labor; fi
  echo ">>> 入库 $name -> $kb"
  curl -s -X POST --get "http://127.0.0.1:8101/api/ingest/local" \
    --data-urlencode "kb_id=$kb" --data-urlencode "filename=$name" | python3 -m json.tool
done
```

> `data/raw` 下是 3 个 `.md` 文件（劳动合同法、劳动法、民法典），其子目录（`src/`、`uploads/`）不用管。
> 知识库 `kb_labor` / `kb_civil` 由 `mysql/init/02_seed_kb.sql` 随建表一起种入（`INSERT IGNORE`，幂等），
> 若上面列表为空，才需要手工 `POST /api/kbs` 创建。

入库完成后再跑一次冒烟演示，覆盖 6 条路由：

```bash
sudo -u lawanswer lawanswer-python -m scripts.demo --base-url http://127.0.0.1:8101
# 期望：10/10 通过
```

### 4.6 交给 systemd 托管（正式运行方式）

手工前台运行只用于首次验证。正式运行用 systemd，好处：开机自启、崩溃自动重启、日志进 journald、环境变量从 600 权限文件注入。

```bash
# 停止手工进程（Ctrl+C）
scp E:/project/LawAnswer/deploy/lawanswer-api.service root@<公网IP>:/etc/systemd/system/

# 服务器
systemctl daemon-reload
systemctl enable --now lawanswer-api
systemctl status lawanswer-api --no-pager

# 看日志
journalctl -u lawanswer-api -f
tail -f /opt/lawanswer/logs/app.log
```

单元文件要点（`deploy/lawanswer-api.service`）：

- `ExecStart` 直接用 `.venv/bin/python -m app.server`，不走 `uv run`（避免每次启动的环境解析/自动同步）
- `EnvironmentFile=/etc/lawanswer/lawanswer.env`（密钥从这里注入）
- `Restart=always` + `OOMScoreAdjust=-500`（降低被 OOM Killer 优先杀掉的概率）
- `After/Requires=docker.service`（基础设施是 compose 起的）

### 4.7 配置 nginx + HTTPS

```bash
scp E:/project/LawAnswer/deploy/nginx-lawanswer.conf root@<公网IP>:/etc/nginx/conf.d/lawanswer.conf
```

编辑 `/etc/nginx/conf.d/lawanswer.conf`，把所有 `lawanswer.example.com` 换成你的域名，并**先把 80 段里 `location / { return 301 ... }` 注释掉**（首签证书前需要 HTTP 可达）。

```bash
nginx -t && systemctl restart nginx

# 先验证 HTTP 能通（此时还没证书）
curl -I http://你的域名/
```

签发证书（certbot 会自动改 nginx 配置、加 443 段）：

```bash
certbot --nginx -d 你的域名
# 按提示填邮箱、同意条款、选择自动跳转 HTTPS

# 签发完回去启用强制跳转
vi /etc/nginx/conf.d/lawanswer.conf   # 取消 80 段 return 301 的注释
nginx -t && systemctl reload nginx

# 验证证书自动续期
certbot renew --dry-run
```

**nginx 配置里三个决定成败的点**（已在 `deploy/nginx-lawanswer.conf` 中处理）：

1. **SSE 必须关缓冲**：`proxy_buffering off; proxy_cache off; gzip off;` + `proxy_set_header X-Accel-Buffering no;`。漏掉的话，流式回答会被 nginx 攒成一整坨再返回，"打字机效果"消失。
2. **history 路由回退**：`location / { try_files $uri $uri/ /index.html; }`。漏掉的话 `/chat`、`/kb/xxx` 刷新 404。
3. **上传体积**：`client_max_body_size 50m;`（默认 1 M，上传 PDF 会 413）。

---

## 5. 对外访问验证清单

按顺序全绿才算部署成功：

```bash
# 1. 后端健康（本机）
curl -s http://127.0.0.1:8101/api/health | python3 -m json.tool

# 2. 前端静态页（本机）
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/            # 期望 301/200

# 3. 经公网 IP + 域名
curl -I https://你的域名/
curl -s https://你的域名/api/health | python3 -m json.tool

# 4. SSE 流式（关键：验证 nginx 没把流缓冲掉）
curl -N -X POST https://你的域名/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"query":"试用期最长可以约定多久？","kb_ids":["kb_labor"]}'
# 期望：事件逐条实时出现（meta → stage → sources → delta×N → done），
#      而不是停顿几秒后一次性吐完
```

浏览器打开 `https://你的域名`，注册账号 → 登录 → 在 `/chat` 提问，确认：
- 回答带 `（《中华人民共和国劳动合同法》第十九条）` 式引用
- 首 token 延迟合理（**首次提问会加载模型，需额外等 10–30 s**，之后转快）

---

## 6. 稳定运行的关键注意事项

### 6.1 内存与 OOM（小内存机器第一优先级）

- **务必做**：加 4 G swap（§3.1），`vm.swappiness=10`
- **务必做**：用 `deploy/docker-compose.server.yml` 覆盖层给每个容器设内存上限，防止单容器吃满整机
- 观察：`free -h`、`docker stats --no-stream`、`dmesg | grep -i oom`
- 首次检索会加载 bge-m3（2.3 G）+ reranker（2.3 G），**这一瞬间内存峰值最高**。如果 8 G 机器仍吃紧，两种降级办法：
  - 把 `RERANKER_BATCH_SIZE`、`EMBEDDING_BATCH_SIZE` 调到 4
  - 调整 `SERVER_THREAD_TOKENS=40`（并发降低，内存与线程占用同步下降）
- `systemd` 的 `OOMScoreAdjust=-500` 让后端比容器更不容易被杀

### 6.2 开机自启与自愈

```bash
# 三个都要确认 enabled
systemctl is-enabled docker nginx lawanswer-api
docker compose -f docker-compose.yml -f deploy/docker-compose.server.yml ps   # 各容器 restart: unless-stopped
```

容器 `restart: unless-stopped` + 服务 `Restart=always`，断电重启后应自动恢复。建议**实际重启一次验证**（`reboot` 后逐项检查）。

### 6.3 时钟同步（容易被忽略）

JWT 有效期、日志时间戳、限流窗口都依赖准确时钟。`server-init.sh` 已设时区并启用 chronyd。验证：

```bash
timedatectl
chronyc tracking | head -3
```

### 6.4 日志与磁盘

- 应用日志：`/opt/lawanswer/logs/app.log` 与 `error.log`，已配 10 M × 5 轮转
- Docker 容器日志：`daemon.json` 已设 20 M × 3
- 定期检查：`df -h /`、`du -sh /opt/lawanswer/logs /var/lib/docker`

### 6.5 密钥安全

- `/etc/lawanswer/lawanswer.env` 权限 **600 / root**
- 项目根 `.env` **不含任何 LLM 密钥**（否则启动自检拒绝启动）
- 日志与 `/api/health` 只输出"是否已配置"与来源变量名，不输出密钥内容
- 长期建议：改用阿里云 KMS 或环境变量管理系统注入；轮换密钥时 `systemctl restart lawanswer-api` 即可

### 6.6 备份

```bash
# MySQL 逻辑备份（建议 cron 每日）
docker exec lawanswer-mysql mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
  --single-transaction lawanswer | gzip > /opt/backup/lawanswer-$(date +%F).sql.gz

# Milvus 向量数据在 ./volumes/milvus（bind mount），随目录一起备份
tar -czf /opt/backup/volumes-$(date +%F).tar.gz -C /opt/lawanswer volumes
```

> 向量库可用 `curl -X POST .../api/ingest/local` 从 `data/raw` 重建（约 10–20 分钟），所以**最低限度保证 `data/raw` 与 MySQL 备份即可**。

### 6.7 监控与告警

- 阿里云云监控 → 添加站点监控，探测 `https://你的域名/api/health/live`（存活探针，不探依赖，开销小）
- 或自建 cron：`*/5 * * * * curl -sf https://你的域名/api/health/live || echo "LawAnswer DOWN" | mail ...`
- 关注指标：内存使用率、CPU 使用率、`/api/health` 的 `status` 是否变 `degraded`

### 6.8 限流与费用

- `RATE_LIMIT_ENABLED=true` **务必保持开启**（匿名 20 次/分、登录 60 次/分），否则 LLM 费用可能被刷爆
- 每次问答会调 LLM 两次（意图识别 + 生成），费用与 QPS 直接相关
- 建议在阿里云百炼/DeepSeek 控制台设消费额度上限

### 6.9 性能预期（8 G CPU 机器，不要和本地 4060 比）

| 指标 | 本地 4060 | 服务器 CPU（8G） |
|---|---|---|
| 向量化单条 | 28 ms | ~250 ms |
| 20 条重排 | 137 ms | 5–14 s |
| 端到端首 token | ~1 s | 2–5 s（首次含模型加载 10–30 s） |
| QPS（并发 10） | 1.99 | 1–1.5 |

瓶颈是**两次 LLM 调用**（`REPORT.md` §8.2 已结论），CPU 推理变慢不会让 QPS 崩掉，但重排耗时增加会拉长单次延迟。若延迟不可接受，可优先考虑降 `RERANKER_MAX_TEXT_CHARS` 或减 `RETRIEVAL_POOL_FACTOR`。

### 6.10 发版更新流程

```bash
# 本地重新打包（含新 dist）
tar --exclude='.venv' --exclude='models' --exclude='volumes' \
    --exclude='frontend/node_modules' --exclude='.git' \
    --exclude='logs' --exclude='__pycache__' --exclude='*.pyc' \
    -czf /tmp/lawanswer-src.tar.gz .
scp /tmp/lawanswer-src.tar.gz root@<公网IP>:/tmp/

# 服务器：解包（不覆盖模型与数据）+ 重启
cd /opt/lawanswer
tar -xzf /tmp/lawanswer-src.tar.gz
# 只改代码/配置，volumes/ 交给容器自己的 uid（redis=999、mysql=999）
# 切勿 chown -R /opt/lawanswer：属主变 994 后 redis bgsave 会 Permission denied
find /opt/lawanswer -maxdepth 1 -mindepth 1 ! -name volumes -exec chown -R lawanswer:lawanswer {} +
systemctl restart lawanswer-api
nginx -s reload          # 前端 dist 变了才需要
```

> 切记别用 `tar` 覆盖 `volumes/` 和 `models/`。上面打包时已排除这两个目录，故解包不会动它们。

**发版后的"点击没反应"陷阱**：`vite build` 会给每个懒加载页面产出带内容哈希的分块（如
`KbManageView-Dn-tspRA.js`），并按 `emptyOutDir` 清掉上一版。若用户浏览器里还是**旧的标签页**
（或 `index.html` 被中间层缓存），它手里仍是旧哈希清单：当前页能正常用，但点击一个尚未访问过的
导航（典型就是左侧"知识库"）会去取已被删除的旧分块，动态 import 失败 → vue-router 静默中止导航，
表现为"点了没反应"，只有控制台有 404。

前端已加兜底（`frontend/src/router/index.ts` 的 `router.onError`）：识别到分块加载失败会自动整页
重载到目标路由、换取最新资源清单，同一目标只重试一次，仍失败则提示 `Ctrl+F5`。部署侧同时保证：

1. `location = /index.html` 必须带 `Cache-Control: no-cache`（`deploy/nginx-lawanswer.conf` 已含），
   否则浏览器会长期沿用旧清单；
2. 若有 CDN/对象存储在前，务必给 `index.html` 配不缓存、给 `assets/*` 配长缓存；
3. 发版后自己先强刷一次再验收，别用发版前就开着的标签页点。

---

## 附：文件清单

本方案新增的部署文件都在 `deploy/` 目录：

| 文件 | 用途 | 服务器位置 |
|---|---|---|
| `deploy/server-init.sh` | 一键装 Docker/uv/nginx/certbot + 建用户目录 | `/root/`（执行后即可删） |
| `deploy/production.env.example` | 生产配置模板（含密钥项） | `/etc/lawanswer/lawanswer.env`（600） |
| `deploy/lawanswer-api.service` | systemd 单元 | `/etc/systemd/system/` |
| `deploy/nginx-lawanswer.conf` | nginx 站点（静态 + API 反代 + SSE + HTTPS） | `/etc/nginx/conf.d/` |
| `deploy/docker-compose.server.yml` | compose 覆盖层（绑 127.0.0.1 + 内存上限） | `/opt/lawanswer/deploy/` |

## 附：常见问题速查

| 现象 | 原因 | 处理 |
|---|---|---|
| 应用启动即退出，日志报"密钥写进了 .env" | 项目根 `.env` 里有 `LLM_API_KEY=` 之类行 | 删掉该行，密钥只放 `/etc/lawanswer/lawanswer.env` |
| 启动报"生产环境必须设置 JWT_SECRET" | `APP_ENV=prod` 但 `JWT_SECRET` 仍是默认值 | `openssl rand -hex 32` 后填入 |
| 模型加载报路径不存在 | `EMBEDDING_MODEL_PATH` 还是 Windows 路径 | 改为 `/opt/lawanswer/models/bge-m3` |
| 流式回答"憋一大坨" | nginx 缓冲未关 | 确认 `proxy_buffering off` 与 `X-Accel-Buffering no` |
| `/chat` 刷新 404 | nginx 缺 history 回退 | 加 `try_files $uri $uri/ /index.html` |
| 上传 PDF 报 413 | nginx 请求体上限 | `client_max_body_size 50m` |
| 服务反复被 kill | 内存不足触发 OOM | 升配、加 swap、调小 batch/线程数 |
| 问答报"生成服务未配置 API Key" | 密钥未注入 | `systemctl show lawanswer-api -p Environment` 检查；确认密钥在 `lawanswer.env` |
| 400 / model not found | `LLM_BASE_URL` 与模型不属同一厂商 | 按 README provider 表成套改 |
| 图片上传报"需要视觉模型" | 图片 OCR 固定走 DashScope | 另配 `DASHSCOPE_API_KEY` |
| 首次提问卡 10–30 s | 模型懒加载 | 正常，之后转快；可用预热请求（先问一次再对外） |