# wechat-gateway —— 微信小程序薄网关容器镜像
FROM python:3.12-slim

WORKDIR /app/wechat-gateway

# 默认使用阿里云 PyPI 镜像加速，海外部署可 --build-arg PIP_INDEX=https://pypi.org/simple/
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

# 代码位于 /app/wechat-gateway，core_client.load_env_token 会向上读 /app/.env，
# 故运行时需将仓库 .env 挂载到容器的 /app/.env（compose 已配置）
COPY __init__.py core_client.py server.py ./

EXPOSE 18090

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "18090"]
