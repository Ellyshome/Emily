# Emily Core 容器镜像
FROM python:3.12-slim

WORKDIR /app

# 系统依赖（camelot 需要 ghostscript，pdf2image 需要 poppler-utils）
# 默认使用阿里云镜像加速，海外部署可通过 --build-arg APT_MIRROR=deb.debian.org 切回官方源
ARG APT_MIRROR=mirrors.aliyun.com
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    ghostscript poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（默认使用阿里云 PyPI 镜像加速）
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

# 复制代码（运行时亦可通过卷挂载覆盖以热更新）
COPY api/ ./api/
COPY emily_core/ ./emily_core/

# 前端静态文件
COPY static/ ./static/

# 数据目录挂载点
RUN mkdir -p /app/sops /app/prompts /app/config /app/baseknowledge /app/tools \
             /app/notebooks /app/logs /app/attachments /app/user_memory \
             /app/journal /app/db_seeds

EXPOSE 18080 18081

# 双服务器启动：18080(业务API) + 18081(监控看板)
CMD ["python", "-m", "api.run"]
