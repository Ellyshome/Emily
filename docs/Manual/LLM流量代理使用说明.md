# LLM 流量代理使用说明

## 概述

mitmproxy 作为独立容器拦截 emily-core ↔ DeepSeek API 的 HTTPS 通讯，在 wire 层看到 SDK 真实发出的请求/响应，包括 HTTP headers、完整 messages 请求体、原始 JSON 响应、SDK 重试行为等。

## 两种查看方式

### 方式一：mitmweb 实时 UI（浏览器）

地址：`http://localhost:8081`，密码 `emily_proxy_2026`

- 流量列表自动刷新（WebSocket 推送）
- 点开条目可查看完整 Request/Response headers 和 body
- 已过滤仅显示 `api.deepseek.com` 流量（可在 UI 中临时去掉过滤查看全量）
- **注意**：UI 中含明文 API key，端口已绑定 `127.0.0.1`，仅宿主机可访问

### 方式二：jsonl 日志文件（AI 工具读取）

文件：`emily-data\logs\llm_trace.jsonl`

每行一条记录，包含完整请求/响应：

```json
{"timestamp":"2026-07-24T15:49:59","url":"https://api.deepseek.com/chat/completions",
 "request_headers":{...},"request_body":"{\"messages\":[...]}",
 "response_headers":{...},"response_body":"{\"choices\":[...]}"}
```

- 追加写入，不破坏已有数据
- 与 emily-core 共享 `emily-data/logs` 目录
- 支持逐行流式读取

## 开关控制

| 操作 | 方法 |
|------|------|
| 关闭代理 | 注释 `docker-compose-napcat.yml` 中 emily-core 的 `HTTPS_PROXY` 和 `SSL_CERT_FILE` 两行，`docker compose restart emily-core` |
| 关闭 jsonl 落盘 | 注释 mitmproxy 的 `LLM_TRACE_ENABLED=1` 环境变量，`docker compose up -d mitmproxy` |

关闭 jsonl 不影响代理功能，mitmweb UI 仍可正常查看流量。

## 容器状态

```powershell
docker compose -f docker-compose-napcat.yml ps mitmproxy
```

mitmproxy 配置了 `restart: always`，随 docker compose 启动自动运行。

## 安全提示

- :8081 端口绑 `127.0.0.1`，仅宿主机可访问
- mitmweb UI 含明文 API key，不要暴露到公网或局域网
- 生产环境建议关闭代理（注释 HTTPS_PROXY + 重启 emily-core）
- mitmproxy CA 证书持久化在 `emily-data/mitmproxy/`，容器重启不丢失
