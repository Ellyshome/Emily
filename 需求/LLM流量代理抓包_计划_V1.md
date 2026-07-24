# LLM 流量代理抓包 — AI 执行计划

> **基于需求**：实时观察 emily 调用 LLM 的实际通讯内容，采用代理模式（wire-level 无加工），通过 mitmweb UI 实时展示 emily-core 与 DeepSeek API 的 HTTPS 通讯全文
> **计划版本**：v1.0
> **目标**：部署 mitmproxy 独立容器拦截 emily-core 的 LLM 流量，emily-core 代码零改动，仅通过环境变量 + docker-compose 配置实现透明代理，mitmweb 自带 UI 作为显示渠道（不集成 emy-test）

---

## 你的角色

你作为 **Emily DevOps 工程师** + **容器网络工程师**，严格按模块顺序执行，逐模块验证，验证不通过不进入下一个模块。每一步的验收检查表必须全部勾选才能进入下一模块。

---

## 硬约束（违反即失败）

1. **emily-core 代码零改动**：只改 `docker-compose-napcat.yml` + 环境变量，不修改 emily-core 任何 `.py` 文件
2. **代理常驻独立容器**：mitmproxy 作为 docker compose 托管的独立 service，`restart: always`，不依赖 emy-test 或任何测试进程的生命周期
3. **NO_PROXY 必须包含**：`localhost,127.0.0.1,emily-postgres,maxkb,mitmproxy` —— DB/RAG/代理自身流量不走代理
4. **CA 证书持久化**：mitmproxy CA 存到 `emily-data/mitmproxy/`，容器重启不丢
5. **不集成 emy-test**：显示渠道是 mitmweb 自带 UI（:8081），不改动 emy-test 任何代码
6. **生产可关闭**：通过注释 emily-core 的 `HTTPS_PROXY` 环境变量 + 重启即可关闭代理，emily-core 直连 DeepSeek
7. **API key 安全**：mitmweb UI 默认无认证，:8081 含明文 API key（Authorization header），仅本机/受信网络访问，不暴露公网

---

## 上下文（执行前必读）

### 问题背景

emily-core 调用 DeepSeek LLM 的通讯目前有两层应用层 trace，但都看不到 wire-level 真相：

| 现有机制 | 位置 | 状态 | 看不到的内容 |
|----------|------|------|-------------|
| ConsoleLLMTracer | `emily-core/emily_core/infrastructure/llm/console_tracer.py` | **类已写但从未挂载**（无调用者） | — |
| LLMInteractionLogger | `emily-core/emily_core/infrastructure/logging/llm_logger.py` | 已挂载，写 `evolution_llm_interaction_logs` 表 | 完整 messages 请求体、HTTP headers、SDK 重试行为、原始响应字段 |

应用层 trace 的共同盲区：依赖埋点完整性、看不到 SDK 实际发出的字节、看不到 HTTP headers、看不到 SDK 的回退重试（`client.py:140-157` 的 tools/json_mode 回退）。

**代理模式补充这个缺口**——在 wire 层拦截，看到 SDK 真实发出的请求/响应，包括 headers、原始 JSON body、重试行为。

### 架构决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 代理部署形态 | 独立容器（docker compose service） | 常驻稳定，不依赖测试进程；emy-test 关掉不影响 |
| 显示渠道 | mitmweb 自带 UI（:8081） | 零开发，专业 UI，WebSocket 实时推送；不集成 emy-test（已决议放弃 Gradio 控制台集成） |
| 流量导向方式 | `HTTPS_PROXY` 环境变量 | httpx 自动读取，emily-core 代码零改动 |
| HTTPS 解密 | mitmproxy CA 证书 + `SSL_CERT_FILE` | 一次性配置，emily-core 信任 mitmproxy CA |
| 代理开关 | 注释 `HTTPS_PROXY` 环境变量 + 重启 | 生产可关闭，无需改代码 |

### 已有的可复用组件

| 组件 | 位置 | 关键事实 | 本次怎么用 |
|------|------|----------|-----------|
| LLMClient | `emily-core/emily_core/infrastructure/llm/client.py:30-50` | 用 `AsyncOpenAI`（基于 httpx），httpx 自动读 `HTTPS_PROXY`/`SSL_CERT_FILE` 环境变量 | 设环境变量即生效，代码不改 |
| docker-compose-napcat.yml | 项目根 | 现有 5 容器拓扑，network 名 `emily_network`，environment 用 list 格式（`- KEY=value`） | 加第 6 个 service：mitmproxy |
| emily-data/ | 项目根，已 bind-mount 到容器 | 持久化数据目录 | 存 mitmproxy CA 证书 |

### 配置参照表

| 项 | 参照源（docker-compose-napcat.yml） | 要对齐的要点 |
|----|--------------------------------------|-------------|
| network 名 | 所有 service 都用 `networks: [emily_network]` | mitmproxy 加入同一 network，emily-core 用 `http://mitmproxy:8888` 解析 |
| environment 格式 | emily-core service（第 37-54 行），list 格式 `- KEY=value`，6 空格缩进 | 新增的 3 个环境变量用同样格式追加 |
| volumes 格式 | emily-core service（第 55-75 行），list 格式 `- ./host:/container:ro`，6 空格缩进 | CA 证书挂载用同样格式 |
| restart 策略 | 所有 service 都是 `restart: always` | mitmproxy 同样 |

---

## 模块依赖图

```
M1(mitmproxy 容器 + CA 证书生成) ──→ M2(emily-core 环境变量 + 证书挂载) ──→ M3(端到端验证)
```

**严格顺序**：M1 必须先完成（CA 证书生成 + mitmproxy 容器跑起来），否则 M2 的 `SSL_CERT_FILE` 指向不存在的证书，emily-core 启动后所有 HTTPS 调用失败。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M1 | `docker-compose-napcat.yml` | 修改 | 新增 mitmproxy service（mitmweb + CA 持久化） |
| M1 | `emily-data/mitmproxy/` | 新增目录 | CA 证书（首次启动自动生成） |
| M2 | `docker-compose-napcat.yml` | 修改 | emily-core service 加 `HTTPS_PROXY`/`SSL_CERT_FILE`/`NO_PROXY` 环境变量 + CA 证书 volume |
| M3 | 验证记录 | — | emy-test 触发 LLM 调用，mitmweb 看到流量 |

---

## M1：部署 mitmproxy 容器 + 生成 CA 证书

### M1.1 docker-compose 新增 mitmproxy service

在 `docker-compose-napcat.yml` 的 `services:` 段（建议放在 `emily-postgres` 之后、`networks:` 之前）新增：

```yaml
  mitmproxy:                   # LLM 流量代理（抓包 emily-core ↔ DeepSeek）
    image: mitmproxy/mitmproxy:latest
    container_name: mitmproxy
    command: >
      mitmweb
      --web-host 0.0.0.0
      --web-port 8081
      --listen-host 0.0.0.0
      --listen-port 8888
      --set view_filter=~d api.deepseek.com
    ports:
      - "127.0.0.1:8081:8081"   # mitmweb UI（仅宿主机访问，含明文 API key）
      - "8888:8888"             # 代理端口（容器间访问）
    volumes:
      - ./emily-data/mitmproxy:/home/mitmproxy/.mitmproxy
    networks:
      - emily_network
    restart: always
```

**说明**：
- `--set view_filter=~d api.deepseek.com`：UI 只显示 deepseek 域名流量，过滤噪音（调试时可在 UI 里临时改）
- `./emily-data/mitmproxy` 挂载到容器内 `/home/mitmproxy/.mitmproxy`，mitmproxy 首次启动会在此生成 CA 证书（`mitmproxy-ca-cert.pem` 等 5 个文件）
- `127.0.0.1:8081:8081`：UI 仅宿主机访问（含明文 API key，不暴露局域网）。若需局域网访问改成 `0.0.0.0:8081:8081` 但要加认证（见踩坑表）
- `restart: always` 保证代理常驻

### M1.2 首次启动生成 CA 证书

```powershell
# 启动 mitmproxy 容器
docker compose -f docker-compose-napcat.yml up -d mitmproxy

# 等待 5 秒让 mitmproxy 首次启动生成 CA
Start-Sleep -Seconds 5

# 验证 CA 证书已生成（应看到 5 个文件，关键是 mitmproxy-ca-cert.pem）
ls emily-data/mitmproxy/
# 预期：mitmproxy-ca-cert.cer  mitmproxy-ca-cert.pem  mitmproxy-ca-key.pem  mitmproxy-ca.p12  mitmproxy-dhparam.pem
```

### M1.3 验收 M1

- [ ] `docker compose -f docker-compose-napcat.yml ps mitmproxy` 显示 running
- [ ] 浏览器打开 `http://localhost:8081` 看到 mitmweb UI（空流量列表，顶部显示 "mitmproxy" 字样）
- [ ] `emily-data/mitmproxy/mitmproxy-ca-cert.pem` 文件存在
- [ ] `docker logs mitmproxy` 含 "Proxy server listening at" 和 "Web server listening at"，无报错

---

## M2：emily-core 环境变量 + CA 证书挂载

### M2.1 emily-core service 追加环境变量 + volume

在 `docker-compose-napcat.yml` 的 `emily-core` service 中：

**environment 段追加**（紧跟现有 `NAPCAT_WEBUI_TOKEN` 那一行之后，保持 list 格式 `- KEY=value`，6 空格缩进）：

```yaml
      # ── LLM 流量代理（mitmproxy）── 关闭时注释这 3 行 + 重启即可
      - HTTPS_PROXY=http://mitmproxy:8888
      - SSL_CERT_FILE=/app/certs/mitmproxy-ca-cert.pem
      - NO_PROXY=localhost,127.0.0.1,emily-postgres,maxkb,mitmproxy
```

**volumes 段追加**（建议放在 `./emily-data/config:/app/config:ro` 附近，保持 list 格式，6 空格缩进）：

```yaml
      - ./emily-data/mitmproxy/mitmproxy-ca-cert.pem:/app/certs/mitmproxy-ca-cert.pem:ro
```

**关键点**：
- `HTTPS_PROXY=http://mitmproxy:8888`：httpx 自动读此变量，所有 HTTPS 请求走代理。容器间用 service 名 `mitmproxy` 解析（同一 `emily_network`）
- `SSL_CERT_FILE=/app/certs/mitmproxy-ca-cert.pem`：让 httpx 信任 mitmproxy 签发的中间人证书
- `NO_PROXY`：DB（emily-postgres:5432）、RAG（maxkb:8080）、代理自身不走代理。**漏配会导致 emily-core 连 DB/RAG 也走代理，连接失败**
- CA 证书以只读方式（`:ro`）挂载到容器内 `/app/certs/`

### M2.2 重启 emily-core + 清缓存

```powershell
# 清 __pycache__（bind-mount 不自动刷新，本次虽不改代码，重启前清一次稳妥）
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +

# 重启 emily-core 使环境变量生效
docker compose -f docker-compose-napcat.yml restart emily-core

# 等待启动
Start-Sleep -Seconds 8

# 查看启动日志确认无 SSL/Proxy 错误
docker logs --tail 40 emily-core 2>&1
```

### M2.3 验收 M2

- [ ] `docker compose -f docker-compose-napcat.yml ps emily-core` 显示 running
- [ ] `docker logs emily-core` 无 SSL/proxy 相关报错（无 `SSLError`、`ProxyError`、`certificate verify failed`）
- [ ] 容器内验证环境变量：`docker exec emily-core env | grep -E "HTTPS_PROXY|SSL_CERT_FILE|NO_PROXY"`，3 个变量都存在
- [ ] 容器内验证 CA 证书可读：`docker exec emily-core ls -l /app/certs/mitmproxy-ca-cert.pem`
- [ ] emily-core 能正常连 DB：`docker logs emily-core` 含 "Database ready: True"（证明 NO_PROXY 生效，DB 没走代理）

---

## M3：端到端验证

### M3.1 触发 LLM 调用

```powershell
# 先查一个真实用户（不能用假 sender-id，会自动建用户污染 users 表 + 权限降级）
# 注意：实际列名是 level，不是 permission_level（CLAUDE.md 里的命令已过期）
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, level FROM users WHERE status = 'active' ORDER BY level DESC LIMIT 5;"

# 用 emy-test CLI 发一条会触发 LLM 的消息（--sender 传用户名，自动从 users 表解析 QQ 号）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "真实用户名"
```

> **若不知道用户名**：用上面 SQL 查到的 `username` 字段值作为 `--sender` 参数。

### M3.2 mitmweb UI 查看流量

1. 浏览器打开 `http://localhost:8081`
2. 流量列表应出现 `api.deepseek.com` 的 POST 请求（mitmweb 会自动刷新，WebSocket 推送）
3. 点开请求：
   - **Request 面板** → Headers 可见 `Authorization: Bearer sk-...`（**注意 API key 明文**）；Request body 可见完整 JSON（`model` / `messages` 数组含 system+user 全文 / `temperature` / `max_tokens` / `response_format`）
   - **Response 面板** → 响应 body 可见完整 JSON（`choices[0].message.content` / 若 reasoner 模型则有 `reasoning_content` 思维链 / `usage` token 统计）

### M3.3 验收 M3

- [ ] mitmweb UI 出现 `api.deepseek.com` 流量条目
- [ ] Request body 含完整 `messages` 数组（能看到 system prompt 和 user message 全文）
- [ ] Response body 含 LLM 返回的 `content`
- [ ] emy-test CLI 收到 emily-core 的正常回复（代理不破坏功能）
- [ ] emily-core 日志无 `ProxyError`
- [ ] **NO_PROXY 生效验证**：emily-core 连 emily-postgres 的流量**不**出现在 mitmweb UI 里（DB 不走代理；可在 mitmweb 临时去掉 `view_filter` 查看全部流量，确认无 postgres 流量）

---

## 踩坑预防

| 坑 | 现象 | 预防/解决 |
|----|------|----------|
| `__pycache__` 不刷新 | 代码改了但行为没变 | M2.2 已含清缓存命令；本次虽不改代码，重启前清一次 |
| NO_PROXY 漏配 | emily-core 连 DB/RAG 失败，报 `ProxyError` 或超时 | NO_PROXY 必须含 `emily-postgres`、`maxkb`、`mitmproxy`、`localhost`、`127.0.0.1` |
| CA 证书未挂载 | emily-core 报 `SSLError: certificate verify failed` | M2.1 的 volume 挂载必须先于 M2.2 重启；确认 M1.2 已生成证书文件 |
| `SSL_CERT_FILE` 替换默认 CA bundle | emily-core 直连其他 HTTPS（非 DeepSeek）验证失败 | 当前唯一 HTTPS 是 DeepSeek（走代理），暂无此问题。若未来加其他 HTTPS 直连服务，需把 mitmproxy CA 追加到 `/etc/ssl/certs/ca-certificates.crt` 而非用 `SSL_CERT_FILE` 替换 |
| API key 明文泄露 | mitmweb UI 显示 `Authorization` header 明文 | :8081 已绑 127.0.0.1（仅宿主机）；mitmweb 会话不分享；生产环境关闭代理（注释 HTTPS_PROXY） |
| mitmweb 无认证 | 局域网访问 :8081 看到明文流量 | 已用 `127.0.0.1:8081:8081` 仅宿主机访问；若需局域网访问，加 `--set web_password=xxx` 到 command |
| 代理容器挂了拖垮 emily-core | emily-core LLM 调用 `ProxyError` → 降级（走 `_fallback_steps`） | `restart: always` 保证自动恢复；这是代理模式的固有风险，接受 |
| Windows PowerShell GBK 乱码 | docker logs 输出乱码 | `$env:PYTHONIOENCODING="utf-8"` |
| users 表列名陷阱 | `SELECT permission_level FROM users` 报 `column does not exist` | 实际列名是 `level`；CLAUDE.md 里用 `permission_level` 的命令已过期，本计划 M3.1 已用 `level` |

---

## 回滚方案

若代理模式有问题，回滚步骤：

1. 编辑 `docker-compose-napcat.yml`，注释 emily-core 的 `HTTPS_PROXY`、`SSL_CERT_FILE` 两个环境变量（`NO_PROXY` 可留可删）
2. `docker compose -f docker-compose-napcat.yml restart emily-core`
3. 验证 emily-core 直连 DeepSeek 恢复正常：`docker logs --tail 20 emily-core` 无 ProxyError + emy-test 发消息能收到 LLM 回复
4. mitmproxy 容器可保留（不影响）或 `docker compose -f docker-compose-napcat.yml stop mitmproxy`

---

## 后续可选增强（非本次范围，仅记录供未来参考）

1. **mitmproxy addon 落盘 jsonl**：写一个 addon 把每个 flow 存到 `emily-data/logs/llm_trace.jsonl`（该目录已挂载到容器 `/app/logs`），支持回溯。约 20 行 Python
2. **应用层 ConsoleLLMTracer 挂载**：作为代理的补充，提供 `call_sequence` + `pipeline_run_id` 业务上下文（环境变量 `EMILY_LLM_CONSOLE_TRACE=1` 开关，需在 `emily_core/__init__.py:_ensure_initialized()` 加挂载代码）。与代理互补：代理看 wire 真相，应用层 trace 看业务关联
3. **mitmweb 认证**：加 `--set web_password=xxx` 到 mitmproxy command，防局域网访问
4. **流量过滤精细化**：mitmweb `view_filter` 按 WorkItem 节点过滤（需配合应用层 trace 的 stage 标注）

---

## 最终验收检查表

- [ ] mitmproxy 容器 running，`restart: always`
- [ ] mitmweb UI（`http://localhost:8081`）可访问
- [ ] emily-core 环境变量 `HTTPS_PROXY`/`SSL_CERT_FILE`/`NO_PROXY` 已配置
- [ ] emily-core 启动无 SSL/Proxy 报错
- [ ] emy-test 触发 LLM 调用后，mitmweb 出现 `api.deepseek.com` 流量
- [ ] 流量详情含完整 `messages` 请求体 + LLM 响应体
- [ ] emily-core 正常回复（功能不破坏）
- [ ] DB/RAG 流量不走代理（NO_PROXY 生效）
- [ ] 回滚方案验证（注释 HTTPS_PROXY 后 emily-core 直连恢复）
