# Emily

> Session 主线架构 + 核心容器化独立部署 (v0.6.0)
>
> 由 `EmyBot` 重构而来。完整架构参考见 [`Emily_主系统架构.md`](Emily_主系统架构.md)。

## 这是什么

Emily 是企业项目管理智能助手。本仓库是对 `EmyBot` 的**编排层重构 + 容器化拆分**：

- **业务核心剥离为独立容器** `emily-core`（FastAPI `api/` + `emily_core/` 业务包），不依赖 AstrBot。
- **AstrBot 插件退化为薄通信层** `data/plugins/emily_agent/`（去重 / 格式转换 / HTTP 转发 / SSE 出站）。
- **全新 Session 主线编排**：`SessionPool → SessionAgent → WorkItem → 公共 Pipeline BUS（4 节点）`。
- **暂缺的 Agent 大脑用 Mock 占位**（沿用 M15 Mock 模式），真实推理接线属演化 Phase B/C。
- **干净空数据**：不含任何运行时存量数据，首次启动对空 PostgreSQL 自动建表。

## 目录结构

```
Emily/
├── docker-compose-napcat.yml     # 5 服务编排：napcat / astrbot / emily-core / maxkb / emily-postgres
├── Emily_主系统架构.md            # 权威架构蓝图
├── emily-core/                   # 独立业务核心容器
│   ├── Dockerfile  requirements.txt
│   ├── api/                      # HTTP/SSE 入口（server / routes / sse / middleware）
│   └── emily_core/               # 业务内核包
│       ├── adapters/session/     # SessionPoolManager / SessionFactory（消息路由）
│       ├── session/              # SessionAgent / 状态机 / FocusLock / ConfirmQueue
│       ├── workitem/             # WorkItem / WorkItemAgent / Scheduler / KnowledgeInjector
│       │   └── pipeline/         # 公共 Pipeline BUS（4 节点 + Hook + interfaces + mocks）
│       ├── infrastructure/ repositories/ services/ tools/ providers/ agent/   # 迁移复用资产
│       ├── outbound_bus.py  config.py  bootstrap.py  __init__.py(EmilyCore)
├── emily-data/                   # 容器挂载数据根（sops/prompts/config 人类可编辑；其余 Core 写入）
├── data/plugins/emily_agent/     # AstrBot 薄通信层插件
└── scripts/smoke_test.py         # 离线端到端冒烟测试
```

## 数据流（蓝图 §9）

```
QQ → NapCat → AstrBot → emily_agent(薄插件)
                            │  POST /api/v1/message/send
                            ▼
                       emily-core 容器
   SessionPool.route → SessionAgent.handle → WorkItem → Pipeline BUS(4 节点)
       │ wi_node1 意图+拆分 → wi_node2 计划+标准 → wi_node3 执行+验收 → wi_node4 成果总结
                            │  SSE: event=reply
                            ▼
                       emily_agent → AstrBot → NapCat → QQ
```

## 快速开始

### 离线冒烟测试（无需 LLM / 容器）

```bash
cd emily-core
python ../scripts/smoke_test.py            # Mock 大脑，验证编排骨架
```

### 单独跑 Core API（需 Python 依赖）

```bash
cd emily-core
pip install -r requirements.txt
# 需要一个空 PostgreSQL（或先起 docker compose 的 emily-postgres）
export EMILY_DATABASE_URL=postgresql://emily:emily_secret_2026@localhost:15432/emily
uvicorn api.server:app --host 0.0.0.0 --port 18080
curl http://localhost:18080/api/v1/health
```

### 全栈容器部署

```bash
export EMILY_LLM_API_KEY=sk-...           # DeepSeek/OpenAI 兼容
docker compose -f docker-compose-napcat.yml up -d
```

## API（蓝图 §2.6）

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/message/send` | 薄插件转发入站消息；短路回复同步返回，异步走 SSE |
| POST | `/api/v1/session/terminate` | 强制终止指定 Session |
| GET  | `/api/v1/events/outbound` | SSE 出站事件流（reply/progress/file_send/session_closed） |
| GET  | `/api/v1/health` | 健康检查 |

## 演化路线（蓝图 §12）

当前为 **Phase 0 + Phase A 骨架**：容器拆分完成 + Session 池/公共 Pipeline BUS 就绪，
Agent 大脑为 Mock。后续 Phase B/C 将 Mock 替换为真实 WorkItem-Agent（增量灌注）、
SessionAgent（LLM 意图识别 + 多任务编排）、GuardianAgent 真实审核。
真实大脑代码（`emily_core/agent/`）已随包迁移备用。

## 开发约束

1. **业务内核独立**：`emily_core/` 不 import 任何 `astrbot.*`（薄插件除外）。
2. **分层不可跳**：薄插件 → Adapter(Session 池) → Session → WorkItem → Infrastructure。
3. **Hook 声明式注册**：横切关注点经 `emily-data/config/hook_config.json` 挂载，不改 BUS 核心代码。
4. **跨容器协议同步**：`adapters/standard/` 的 StandardMessage/ReplyMessage 在 Core 与插件保持一致副本。
