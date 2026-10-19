# Emily Core 容器镜像
FROM python:3.12-slim

WORKDIR /app

# 系统依赖（camelot 需要 ghostscript，pdf2image 需要 poppler-utils）
# 默认使用阿里云镜像加速，海外部署可通过 --build-arg APT_MIRROR=deb.debian.org 切回官方源
ARG APT_MIRROR=mirrors.aliyun.com
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    ghostscript poppler-utils curl ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# OfficeCLI —— 单文件二进制（Word/Excel/PPT 读取），供 SOP-015-QRY 苗木表审核
# 版本固定；优先官方镜像站，失败回退 GitHub Releases；下载后按 SHA256SUMS 校验。
ARG OFFICECLI_VERSION=1.0.157
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      x86_64|amd64) asset="officecli-linux-x64" ;; \
      aarch64|arm64) asset="officecli-linux-arm64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    mirror="https://d.officecli.ai/releases/download/v${OFFICECLI_VERSION}"; \
    github="https://github.com/iOfficeAI/OfficeCLI/releases/download/v${OFFICECLI_VERSION}"; \
    curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 "${mirror}/${asset}" -o "/tmp/${asset}" \
      || curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 "${github}/${asset}" -o "/tmp/${asset}"; \
    curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 "${mirror}/SHA256SUMS" -o /tmp/officecli-SHA256SUMS \
      || curl -fsSL --retry 3 --connect-timeout 20 --max-time 300 "${github}/SHA256SUMS" -o /tmp/officecli-SHA256SUMS; \
    tr -d '\r' < /tmp/officecli-SHA256SUMS > /tmp/officecli-SHA256SUMS.clean; \
    (cd /tmp && grep "  ${asset}$" officecli-SHA256SUMS.clean | sha256sum -c -); \
    install -m 0755 "/tmp/${asset}" /usr/local/bin/officecli; \
    rm -f "/tmp/${asset}" /tmp/officecli-SHA256SUMS /tmp/officecli-SHA256SUMS.clean; \
    officecli --version

# 安装 Python 依赖（默认使用阿里云 PyPI 镜像加速）
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

# MCP 搜索工具（独立 venv 隔离：duckduckgo-mcp-server 依赖 mcp 2.x，与主环境 1.x 不冲突）
# --timeout 120：cryptography 等大轮子从阿里云镜像下载易超时，放宽重试窗口
RUN python -m venv /opt/mcp-search \
    && /opt/mcp-search/bin/pip install --no-cache-dir --timeout 120 -i ${PIP_INDEX} duckduckgo-mcp-server

# 复制代码（运行时亦可通过卷挂载覆盖以热更新）
COPY api/ ./api/
COPY emily_core/ ./emily_core/

# 前端静态文件
COPY static/ ./static/

# 数据目录挂载点
# /app/data 需预先存在：resolve_data_path 以 container_path.parent.exists() 判定容器态，
# 缺失会静默回退到开发路径，导致容器内读不到 /app/data/node_templates 等挂载目录。
RUN mkdir -p /app/sops /app/prompts /app/config /app/baseknowledge /app/tools \
             /app/notebooks /app/logs /app/attachments /app/user_memory \
             /app/journal /app/db_seeds /app/data

EXPOSE 18080

# 容器健康检查（镜像级单点定义，所有 compose 变体共用）
# 说明：Taskfile 的 wait:healthy 依赖 docker inspect 的 Health.Status，缺此定义时状态恒为
# unknown，会导致 `task deploy:*` 轮询超时后以 exit 1 结束（容器其实已就绪）。
# 鉴权：/api/v1/health 受 AuthMiddleware 的 X-Emily-Token 约束，故探测时带上容器自身的
# EMILY_API_TOKEN（与中间件读同一个环境变量）；token 未配置时中间件放行，探测同样成立。
# 用 python 直接探测，镜像内无 curl，无需额外装包。
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=15 \
  CMD python -c "import os,urllib.request,sys;r=urllib.request.Request('http://127.0.0.1:18080/api/v1/health',headers={'X-Emily-Token':os.environ.get('EMILY_API_TOKEN','')});sys.exit(0 if urllib.request.urlopen(r,timeout=5).status==200 else 1)"

# 启动业务 API 服务（含 emy-console /console/）
CMD ["python", "-m", "api.run"]
