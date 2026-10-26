#!/usr/bin/env bash
# ============================================================================
# LawAnswer 服务器首次初始化脚本（Alibaba Cloud Linux / CentOS 系）
#
#   在服务器上以 root 执行：
#     sudo bash deploy/server-init.sh
#
# 做四件事（幂等，可重复执行）：
#   1) 安装 Docker + compose plugin（走阿里云镜像源）
#   2) 安装 uv（Python 包管理器，用于建 venv）
#   3) 安装 nginx + certbot
#   4) 创建运行用户 lawanswer 与目录 /opt/lawanswer、/etc/lawanswer、/var/www/certbot
#
# 注意：本脚本不配置 .env、不启动服务——那些在文档 §5、§6 手动做，
#       因为需要你先填入密码与域名。
# ============================================================================
set -euo pipefail

log()  { printf '\n\033[36m[init] %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 执行：sudo bash $0" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 0. 系统信息确认 + 时间同步
# ---------------------------------------------------------------------------
log "系统信息"
cat /etc/os-release | grep -E '^(NAME|VERSION)=' || true
echo "  CPU 核数：$(nproc)"
echo "  内存：$(free -h | awk '/Mem:/{print $2" 总量 / "$7" 可用"}')"
echo "  磁盘：$(df -h / | awk 'NR==2{print $2" 总量 / "$4" 可用"}')"

log "时间同步（JWT 有效期、日志时间戳依赖准确时钟）"
timedatectl set-timezone Asia/Shanghai
systemctl enable --now chronyd >/dev/null 2>&1 || systemctl enable --now ntpd >/dev/null 2>&1 || warn "未找到 chronyd/ntpd，请手动确认时间同步"
ok "时区已设为 Asia/Shanghai"

# ---------------------------------------------------------------------------
# 1. Docker
# ---------------------------------------------------------------------------
log "Docker"
if command -v docker >/dev/null 2>&1; then
  ok "docker 已安装：$(docker --version)"
else
  dnf install -y dnf-utils device-mapper-persistent-data lvm2 >/dev/null
  dnf config-manager --add-repo=https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
  # 若无 dnf config-manager，回退到 yum-config-manager
  dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
  ok "docker 安装完成：$(docker --version)"
fi

# 国内镜像加速（拉取 mysql/redis/milvus 等公共镜像必需，否则极易超时）
mkdir -p /etc/docker
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://mirror.ccs.tencentyun.com"
  ],
  "log-driver": "json-file",
  "log-opts": {"max-size": "20m", "max-file": "3"}
}
JSON
  ok "已写入 /etc/docker/daemon.json（镜像加速 + 日志上限，防止磁盘被容器日志吃满）"
else
  warn "/etc/docker/daemon.json 已存在，未覆盖，请自行确认含 registry-mirrors"
fi

systemctl enable --now docker
docker compose version >/dev/null && ok "compose plugin 就绪：$(docker compose version --short)"

# ---------------------------------------------------------------------------
# 2. uv（Python 3.13 由 uv 自动管理，无需系统装 python3.13）
# ---------------------------------------------------------------------------
log "uv"
if command -v uv >/dev/null 2>&1; then
  ok "uv 已安装：$(uv --version)"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # 安装到 /root/.local/bin，软链到系统路径便于 systemd 之外的手工执行
  ln -sf /root/.local/bin/uv  /usr/local/bin/uv
  ln -sf /root/.local/bin/uvx /usr/local/bin/uvx
  ok "uv 安装完成：$(uv --version)"
fi

# ---------------------------------------------------------------------------
# 3. nginx + certbot
# ---------------------------------------------------------------------------
log "nginx / certbot"
if command -v nginx >/dev/null 2>&1; then
  ok "nginx 已安装：$(nginx -v 2>&1)"
else
  dnf install -y nginx >/dev/null
  ok "nginx 安装完成：$(nginx -v 2>&1)"
fi

if command -v certbot >/dev/null 2>&1; then
  ok "certbot 已安装"
else
  # certbot 在 EPEL 源
  dnf install -y epel-release >/dev/null 2>&1 || true
  dnf install -y certbot python3-certbot-nginx >/dev/null || warn "certbot 安装失败，可改用阿里云免费证书手动配置"
  command -v certbot >/dev/null 2>&1 && ok "certbot 安装完成"
fi

systemctl enable nginx
ok "nginx 已设为开机自启（暂不启动，等配置就绪）"

# ---------------------------------------------------------------------------
# 4. 用户与目录
# ---------------------------------------------------------------------------
log "运行用户与目录"
if id lawanswer >/dev/null 2>&1; then
  ok "用户 lawanswer 已存在"
else
  useradd -r -m -d /home/lawanswer -s /sbin/nologin lawanswer
  ok "已创建系统用户 lawanswer（不可登录，仅用于跑服务）"
fi

# 服务需要读取 docker.sock 之外的东西很少；这里主要给项目目录权限
install -d -o lawanswer -g lawanswer -m 755 /opt/lawanswer
install -d -o root -g lawanswer -m 750 /etc/lawanswer
install -d -m 755 /var/www/certbot
ok "/opt/lawanswer、/etc/lawanswer(750)、/var/www/certbot 已就绪"

# ---------------------------------------------------------------------------
# 4.1 便捷执行器：带生产环境变量跑项目内 Python 模块
#
#   为什么需要它：项目的配置来自 /etc/lawanswer/lawanswer.env，而手工执行
#   `python -m app.db.init_db` 之类命令时必须先把这些变量注入进程。
#   常见的 `env $(grep -v '^#' file | xargs)` 写法在密码含空格/特殊字符时会
#   解析错乱；这里用 shell 的 `set -a; . file` 让 shell 自己正确解析引号，
#   再 exec 目标命令，是更可靠的做法。
#
#   用法：sudo -u lawanswer lawanswer-python -m app.db.init_db
# ---------------------------------------------------------------------------
cat > /usr/local/bin/lawanswer-python <<'HELPER'
#!/usr/bin/env bash
# 带生产环境变量执行 LawAnswer 内的 Python 模块
set -euo pipefail
ENV_FILE=/etc/lawanswer/lawanswer.env
if [[ ! -r "$ENV_FILE" ]]; then
  echo "无法读取 $ENV_FILE（检查是否存在、权限是否为 750/600）" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
cd /opt/lawanswer
exec .venv/bin/python "$@"
HELPER
chmod 755 /usr/local/bin/lawanswer-python
ok "已安装 /usr/local/bin/lawanswer-python（带生产环境变量执行项目模块）"

# ---------------------------------------------------------------------------
# 5. 交换空间（仅作兜底，不能替代物理内存）
# ---------------------------------------------------------------------------
log "Swap"
if swapon --show | grep -q .; then
  ok "已有 swap：$(swapon --show --noheadings | awk '{print $1" "$3}')"
else
  warn "未启用 swap。2G 内存机型建议加 4G swap 兜底（防 OOM kill，但会显著拖慢推理）："
  warn "  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile"
  warn "  echo '/swapfile none swap sw 0 0' >> /etc/fstab && sysctl -w vm.swappiness=10"
fi

log "初始化完成"
cat <<'NEXT'

接下来（见 docs/部署方案-阿里云ECS.md）：
  1. 上传代码与模型到 /opt/lawanswer
  2. 写 /etc/lawanswer/lawanswer.env（含密钥，权限 600）
  3. 装依赖：cd /opt/lawanswer && uv sync
  4. 起基础设施：docker compose -f docker-compose.yml -f deploy/docker-compose.server.yml up -d
  5. 装 systemd 单元、nginx 配置
  6. 签证书：certbot --nginx -d 你的域名

NEXT