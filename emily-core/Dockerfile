# Emily Core 容器镜像（蓝图 §10.3）
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码（运行时亦可通过卷挂载覆盖以热更新）
COPY api/ ./api/
COPY emily_core/ ./emily_core/

# 数据目录挂载点（运行时由 docker-compose 卷挂载，蓝图 §10.2）
RUN mkdir -p /app/sops /app/prompts /app/config /app/baseknowledge /app/tools \
             /app/notebooks /app/logs /app/attachments /app/user_memory \
             /app/journal /app/db_seeds

EXPOSE 18080

# api.server:app 内 lifespan 会 bootstrap EmilyCore
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "18080"]
