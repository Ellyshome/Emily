# Emily 主系统架构

> Version 0.6.0 — Session 主线架构 + 核心容器化独立部署
>
> 本文档是 Emily 项目的权威架构参考，以 `session主线策略.md` 为骨架，融合当前代码库全部已实现模块。

---

## 目录

1. [架构全景](#1-架构全景)
2. [通信模块 — 插件与核心的解耦桥梁](#2-通信模块--插件与核心的解耦桥梁)
3. [Adapter 层 — Session 池与消息路由](#3-adapter-层--session-池与消息路由)
4. [Session 层 — Session-Agent 调度核心](#4-session-层--session-agent-调度核心)
5. [WorkItem 层 — Pipeline BUS 任务执行](#5-workitem-层--pipeline-bus-任务执行)
6. [基础设施层 — 数据与能力基座](#6-基础设施层--数据与能力基座)
7. [横切关注点 — Hook 声明式挂载](#7-横切关注点--hook-声明式挂载)
8. [鉴权体系](#8-鉴权体系)
9. [数据流全景 — 端到端消息处理时序](#9-数据流全景--端到端消息处理时序)
10. [配置与部署](#10-配置与部署)
11. [模块映射 — 蓝图概念 ↔ 现有实现](#11-模块映射--蓝图概念--现有实现)
12. [演化路线 — Mock → 真实实现](#12-演化路线--mock--真实实现)

---

## 1. 架构全景

### 1.1 容器部署全景

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Docker 容器编排全景                            │
│                                                                      │
│  ┌──────────────────────┐  ┌─────────────────────────────────────┐ │
│  │  astrbot 容器          │  │  emily-core 容器 (独立业务核心)     │ │
│  │  (Plugin Host)        │  │                                     │ │
│  │                       │  │  ┌─────────────────────────────┐   │ │
│  │  ┌─────────────────┐  │  │  │   HTTP API Server (入口)     │   │ │
│  │  │ Emily 插件      │  │  │  │   POST /message/send        │   │ │
│  │  │ (薄通信层)       │──┼──┼─▶│   POST /file/send           │   │ │
│  │  │                 │  │  │  │   POST /session/terminate    │   │ │
│  │  │ · 消息去重       │  │  │  │   GET  /health              │   │ │
│  │  │ · 格式转换       │  │  │  └─────────────┬───────────────┘   │ │
│  │  │ · 事件→API调用   │  │  │                │                    │ │
│  │  │ · API回复→IM发送  │◀─┼──┼─  SSE / WebSocket (出站推送)     │ │
│  │  └─────────────────┘  │  │                │                    │ │
│  │                       │  │                ▼                    │ │
│  └───────────────────────┘  │  ┌─────────────────────────────┐   │ │
│                              │  │  emily_core (业务内核)   │   │ │
│                              │  │                             │   │ │
│  ┌──────────────────────┐  │  │  ┌───────────────────────┐   │   │ │
│  │  napcat 容器           │  │  │  │ SessionPoolManager    │   │   │ │
│  │  (QQ 协议桥)           │  │  │  │ SessionAgent          │   │   │ │
│  │  :6098-6099           │  │  │  │ WorkItemAgent         │   │   │ │
│  └───────────────────────┘  │  │  │ Pipeline BUS (4 节点)  │   │   │ │
│                              │  │  │ Hook 系统              │   │   │ │
│  ┌──────────────────────┐  │  │  └───────────────────────┘   │   │ │
│  │  maxkb 容器            │  │  │                             │   │ │
│  │  (RAG 知识库)          │  │  │  ┌───────────────────────┐   │   │ │
│  │  :8080                │  │  │  │ Infrastructure         │   │   │ │
│  └───────────────────────┘  │  │  │ · LLM Client           │   │   │ │
│                              │  │  │ · RAG Provider         │   │   │ │
│  ┌──────────────────────┐  │  │  │ · Repository (10)       │   │   │ │
│  │  emily-postgres 容器  │  │  │  │ · Service (15)         │   │   │ │
│  │  (业务数据库)          │◀─┼──┼──│ · 22 表 ORM             │   │   │ │
│  │  :15432               │  │  │  └───────────────────────┘   │   │ │
│  └───────────────────────┘  │  └─────────────────────────────┘   │ │
│                              └─────────────────────────────────────┘ │
│                                                                      │
│  ───── 容器边界 (Docker Network: astrbot_network) ────              │
│  所有容器通过内网 Docker bridge 通信，仅暴露必要端口到宿主机           │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 逻辑分层架构

```
┌─────────────────────────────────────────────────────────────────┐
│                       IM 平台层 (External)                        │
│          QQ ←→ NapCat (WebSocket) ←→ AstrBot (Plugin Host)        │
└──────────────────────────────┬──────────────────────────────────┘
                               │ AstrMessageEvent
                               ▼
╔═══════════════════════════════════════════════════════════════════╗
║              通信模块 (AstrBot Plugin — 薄通信层)                   ║
║                                                                   ║
║  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐  ║
║  │ InboundAdapter   │  │ EmilyApiClient   │  │ OutboundSender  │  ║
║  │ (消息去重+转换)   │  │ (HTTP → EmilyCore)  │  │ (SSE → IM 推送) │  ║
║  └────────┬────────┘  └────────┬─────────┘  └────────┬───────┘  ║
║           │                    │                      ▲           ║
║           ▼                    ▼                      │           ║
║  StandardMessage ──▶ POST /message/send ─────────────┘           ║
║           │                    │                                   ║
║           │                    │  EmilyCore 通过 SSE/WebSocket      ║
║           │                    │  主动推送出站消息到插件            ║
║           │                    │                                   ║
║           │              【容器边界: HTTP / SSE / WebSocket】       ║
╚═══════════╪════════════════════╪══════════════════════════════════╝
            │                    │
            │                    ▼  (内网 HTTP)
            │         ┌─────────────────────────┐
            │         │  Emily Core 容器         │
            │         │  HTTP API Server          │
            │         │  (FastAPI / aiohttp)      │
            │         └──────────┬───────────────┘
            │                    │
            ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│               Emily Core — 业务内核 (独立容器)                     │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   ADAPTER 层 (core/adapters/)               │   │
│  │                                                              │   │
│  │  SessionPoolManager ──▶ Session 池查找 / 创建 / 注销         │   │
│  │  StandardMessage  ←  HTTP 请求体反序列化                     │   │
│  │  ReplyMessage      →  SSE 事件推送给插件                      │   │
│  └──────────────────────────────┬─────────────────────────────┘   │
│                                 │                                  │
│                                 ▼                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   SESSION 层 (session/)                     │   │
│  │                                                              │   │
│  │  Session-Agent (调度主脑)                                    │   │
│  │  ├── 意图识别 + WorkItem 拆分                                │   │
│  │  ├── 多任务交互排队 + 优先级调度                              │   │
│  │  ├── 附件下载管理 + 出站审核                                  │   │
│  │  └── 知识灌注 (7 类上下文)                                    │   │
│  │                                                              │   │
│  │  Session 状态机: CREATED → ACTIVE → WAITING_CONFIRM → CLOSED│   │
│  │  (注: 状态机细节待后续根据使用需求逐步完善)                       │   │
│  └──────────────────────────────┬─────────────────────────────┘   │
│                                 │                                  │
│                                 ▼                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  WORKITEM 层 (workitem/)                     │   │
│  │                                                              │   │
│  │  Session-Scheduler → Pipeline BUS (公共总线) → WorkItem      │   │
│  │  WorkItem-Agent (全局单例, 异步处理所有 W切项)                  │   │
│  │  WorkItem 状态机: CREATED → PLANNING → EXECUTING → DONE     │   │
│  └──────────────────────────────┬─────────────────────────────┘   │
│                                 │                                  │
│                                 ▼                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               基础设施层 (Infrastructure)                    │   │
│  │  LLM Client │ RAG Provider │ DB (22 表) │ File Storage     │   │
│  │  Repository (10) + Service (15)                            │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**关键架构变更**：AstrBot 内的插件代码 (`main.py` + `adapters/astrbot/*`) 退化为**薄通信层**，仅负责与 AstrBot 框架对接、消息去重、格式转换和 HTTP 转发。全部业务逻辑（Session 管理、Agent 推理、Pipeline 执行、数据持久化）跑在独立的 `emily-core` Docker 容器中。两者通过内网 HTTP + SSE 解耦通信。

### 1.3 核心设计原则

| 原则 | 说明 |
|------|------|
| **Session 为调度单元** | 每个用户会话是一个独立的 Session，由专属 Session-Agent 调度 |
| **WorkItem 为执行单元** | 每个具体任务拆分为一个 WorkItem，由 Session-Scheduler 分配到公共 Pipeline BUS 执行 |
| **WorkItem-Agent 单例服务** | 全局唯一 WorkItem-Agent，异步处理所有 WorkItem；新需求进来时增量灌注缺失的工具/SOP，最小化上下文污染 |
| **Pipeline BUS 公共执行总线** | Session-Scheduler → Pipeline BUS → WorkItem：Pipeline 是系统级公共总线，不属于单个 WorkItem 私有 |
| **Session 初始化最小化** | Session 创建时仅灌入最近对话 + 用户摘要；SOP/工具/数据库等只拿高度压缩的一级摘要，详细内容懒加载 |
| **分层不可跳** | 通信层 → Adapter → Session → WorkItem → Infrastructure，不跨层调用 |
| **EmilyCore 独立容器化** | `emily_core/` 运行在独立 Docker 容器中，不 import `astrbot.*`，与 AstrBot 仅通过内网 HTTP+SSE 通信 |
| **插件退化为薄通信层** | AstrBot 插件代码仅负责消息去重、格式转换、HTTP 转发和 SSE 出站推送，不含业务逻辑 |
| **Hook 声明式注册** | 横切关注点通过配置文件声明，不改 Pipeline 核心代码 |
| **热插拔工具模块** | SOP 业务流、数据库、基座工具均为独立可插拔的可热重载模块 |

### 1.4 容器间通信拓扑

```
                    astrbot_network (Docker Bridge)
                    
                          ┌──────────────┐
                          │    napcat    │  QQ 协议桥
                          │   :6098      │
                          └──────┬───────┘
                                 │ WebSocket
                                 ▼
                          ┌──────────────┐
┌──────────────────────┐  │   astrbot    │  AstrBot 插件宿主
│   emily-core 容器    │  │ :6185 :6199  │
│                      │  └──┬─────┬─────┘
│  HTTP API Server     │     │     │
│  监听 :18080 (内网)   │◄────┘     │
│                      │  POST      │
│  SSE 出站推送         │  /message  │
│  ────────────────────┼───────────►│
│                      │  SSE push  │
│                      │           │
│  ┌──────────────────┐│           │
│  │ emily_core   ││  ┌────────┴──────┐
│  │ (全量业务逻辑)    ││  │ Emily 插件    │
│  └──────────────────┘│  │ (薄通信层)     │
│                      │  │ · 去重/转换    │
│  ┌──────────────────┐│  │ · HTTP Client  │
│  │ PostgreSQL Client││  │ · SSE Listener │
│  └────────┬─────────┘│  └───────────────┘
└───────────┼──────────┘
            │
            ▼
  ┌──────────────────┐    ┌──────────────┐
  │ emily-postgres  │    │    maxkb     │  RAG 知识库
  │ :5432 (内网)     │    │ :8080 (内网)  │
  │ :15432 (宿主机)   │    │ :5433 (内网)  │
  └──────────────────┘    └──────────────┘
```

**通信协议明细：**

| 方向 | 协议 | 端点 | 说明 |
|------|------|------|------|
| 插件 → Core | HTTP POST | `POST /api/v1/message/send` | 发送标准消息，请求体为 StandardMessage JSON |
| 插件 → Core | HTTP POST | `POST /api/v1/session/terminate` | 强制终止指定 Session |
| Core → 插件 | SSE | `GET /api/v1/events/outbound` | 出站消息事件流，推送给插件由 OutboundSender 发送到 IM |
| Core → 插件 | SSE | `GET /api/v1/events/progress` | 前导消息推送（"处理中..."） |
| Core → 插件 | HTTP POST | (回调) Webhook URL | 文件发送请求回调（插件挂载时注册） |
| 内部 | — | 共享 PostgreSQL | 两个容器共用同一数据库（同网络内直连） |

---

## 2. 通信模块 — 插件与核心的解耦桥梁

### 2.1 定位

通信模块是 AstrBot 插件（薄通信层）与 Emily Core（独立容器）之间的解耦桥梁。它是重构的第一优先事项：**先将 Core 从插件中物理剥离为独立容器，在此基础上再实施 Session 主线架构重构**。

### 2.2 模块划分

```
                    AstrBot 容器内                        Emily Core 容器内
                    
  ┌──────────────────────────────────┐    ┌──────────────────────────────┐
  │  data/plugins/emily_agent/ │    │  emily-core/                │
  │                                  │    │                              │
  │  main.py                         │    │  api/                        │
  │  ├── Main.__init__()             │    │  ├── server.py  ← FastAPI    │
  │  │   (读取配置, 初始化 API 客户端)  │    │  ├── routes/               │
  │  │                               │    │  │   ├── message.py         │
  │  │                               │    │  │   ├── session.py         │
  │  ├── Main.on_message()           │    │  │   └── health.py          │
  │  │   (去重 → 转换 → HTTP POST)   │    │  │                          │
  │  │                               │    │  ├── sse/                   │
  │  │                               │    │  │   └── outbound.py        │
  │  ├── adapters/astrbot/           │    │  │                          │
  │  │   ├── inbound_adapter.py      │    │  └── middleware/             │
  │  │   │   (AstrMessageEvent→StdMsg)│   │      └── auth.py            │
  │  │   │                           │    │                              │
  │  │   └── outbound_sender.py      │    │  emily_core/  (迁移后)   │
  │  │       (接收 SSE 推送→IM 发送)  │    │  ├── __init__.py            │
  │  │                               │    │  │   EmilyCore           │
  │  ├── adapters/standard/          │    │  ├── adapters/              │
  │  │   (迁移到 Core, 插件仅保留副本) │    │  │   └── standard/          │
  │  │                               │    │  ├── application/           │
  │  └── api_client.py  【新建】     │    │  ├── agent/                 │
  │      ├── EmilyApiClient         │    │  ├── pipeline/              │
  │      │   · send_message()        │    │  ├── services/              │
  │      │   · send_file()           │    │  ├── repositories/          │
  │      │   · terminate_session()   │    │  ├── infrastructure/        │
  │      │                           │    │  ├── tools/                 │
  │      └── SSEListener 【新建】    │    │  ├── providers/             │
  │          · on_reply()            │    │  └── config.py              │
  │          · on_progress()         │    │                              │
  │          · on_file_send()        │    │                              │
  └──────────────────────────────────┘    └──────────────────────────────┘
```

### 2.3 AstrBot 插件端（薄通信层）

```python
# main.py — 重构后（仅剩 ~80 行）

class Main(star.Star):
    """Emy 插件入口 —— 薄通信层，不包含业务逻辑。"""
    
    def __init__(self, context, config=None):
        super().__init__(context, config=config)
        self.inbound = AstrBotInboundAdapter()
        self.outbound = AstrBotOutboundSender()
        self.api = EmilyApiClient(base_url=config.get("emycore_url", "http://emily-core:18080"))
        self.sse = SSEListener(self.outbound)
        self._seen = deque(maxlen=200)
    
    async def initialize(self):
        # 启动 SSE 监听（接收 Core 推送到出站消息）
        asyncio.create_task(self.sse.listen(self.api.get_sse_url()))
        # 健康检查
        await self.api.health_check()
    
    async def on_message(self, event: AstrMessageEvent):
        # 1. 去重
        event_id = _event_fingerprint(event)
        if event_id in self._seen:
            return
        self._seen.append(event_id)
        
        # 2. AstrMessageEvent → StandardMessage (纯数据转换)
        msg = self.inbound.to_standard_message(event)
        
        # 3. HTTP 转发到 Core（含文件回调 Webhook URL）
        reply = await self.api.send_message(msg)
        
        # 4. 同步回复直接发送（异步回复走 SSE 通道）
        if reply:
            await self.outbound.send(reply, event)
```

### 2.4 EmilyApiClient（插件到 Core 的 HTTP 客户端）

```python
# adapters/api_client.py 【新建】

class EmilyApiClient:
    """Emily Core 的 HTTP API 客户端。
    
    在 AstrBot 插件内运行，负责将所有业务请求转发给独立 Core 容器。
    """
    
    def __init__(self, base_url: str = "http://emily-core:18080"):
        self.base_url = base_url
        self._session: aiohttp.ClientSession | None = None
    
    async def send_message(self, msg: StandardMessage) -> ReplyMessage | None:
        """发送入站消息到 Core，同步等待回复（短路回复场景）。
        
        POST /api/v1/message/send
        Body: StandardMessage (JSON)
        Response: ReplyMessage (JSON) or 204 No Content
        """
        ...
    
    async def terminate_session(self, conversation_id: str) -> bool:
        """强制终止指定 Session。
        
        POST /api/v1/session/terminate
        Body: {"conversation_id": "..."}
        """
        ...
    
    async def health_check(self) -> dict:
        """检查 Core 健康状态。
        
        GET /api/v1/health
        Response: {"status": "ok", "sessions": 12, "uptime": 3600}
        """
        ...
```

### 2.5 SSEListener（Core 到插件的出站推送）

```python
# adapters/sse_listener.py 【新建】

class SSEListener:
    """监听 Emily Core 的 SSE 出站事件流。
    
    Core 异步生成的所有出站消息（Agent 回复、前导进度、文件发送请求）
    均通过 SSE 推送给插件，由插件调用 AstrBot OutboundSender 发送到 IM。
    """
    
    def __init__(self, outbound: AstrBotOutboundSender):
        self.outbound = outbound
        self._event_handlers = {
            "reply": self._handle_reply,          # 文本回复
            "progress": self._handle_progress,     # "处理中..."前导
            "file_send": self._handle_file_send,   # 文件发送
            "session_closed": self._handle_session_closed,  # 会话关闭通知
        }
    
    async def listen(self, sse_url: str):
        """连接 SSE 端点，持续接收事件。"""
        async with aiohttp.ClientSession() as session:
            async with session.get(sse_url) as resp:
                async for line in resp.content:
                    event = parse_sse_line(line)  # event: reply\ndata: {...}
                    handler = self._event_handlers.get(event.type)
                    if handler:
                        await handler(event.data)
```

### 2.6 Core 端 HTTP API（emily-core 容器入口）

```python
# api/server.py 【新建】— FastAPI 应用入口

app = FastAPI(title="Emily Core API", version="1.0")

@app.post("/api/v1/message/send")
async def handle_message(msg: StandardMessage):
    """插件转发入站消息。
    
    短路回复（如问候/确认）在此同步返回。
    异步处理结果（Agent 多轮推理后回复）通过 SSE 推送。
    """
    reply = await core.handle_message(msg)
    if reply:
        return reply  # 同步返回
    return Response(status_code=204)  # 异步处理中，回复将通过 SSE 推送

@app.get("/api/v1/events/outbound")
async def outbound_events(request: Request):
    """SSE 端点：推送出站消息到插件。"""
    async def event_generator():
        queue = outbound_event_bus.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
        finally:
            outbound_event_bus.unsubscribe(queue)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### 2.7 Container-to-Plugin 通信完整流程

```
  AstrBot 容器                    Emily Core 容器
  ────────────                    ────────────────
  
  用户发消息 "创建事件"
      │
      ▼
  Main.on_message()
      │
      ├─ 去重 (SHA256)
      ├─ AstrMessageEvent → StandardMessage
      │
      ├─ POST /api/v1/message/send ──────────▶ HTTP API Server
      │                                             │
      │  ◄── 204 No Content ─────────────────      ├─ EmilyCore.handle_message()
      │   (异步处理中)                               │     │
      │                                             │     ├─ SessionPool.lookup()
      │                                             │     ├─ Session-Agent 意图分析
      │                                             │     ├─ WorkItem → Pipeline BUS
      │                                             │     └─ 结果出站
      │                                             │
      │  ◄── SSE: event=progress ─────────────     ├─ "正在处理你的请求..."
      │  Main.on_progress()  →  IM 发送 "处理中..." │
      │                                             │
      │  ◄── SSE: event=reply ────────────────     ├─ "已记录事件: EVT-2026..."
      │  Main.on_reply()  →  AstrBotOutboundSender  │
      │      →  IM 发送最终回复                      │
      ▼                                             │
  用户看到:                                         │
  "处理中..."                                       │
  "已记录事件: EVT-2026..."                          │
```

### 2.8 通信层职责边界

| 职责 | AstrBot 插件 (薄层) | Emily Core (容器) |
|------|-------------------|--------------------|
| 消息去重 | ✅ SHA256 指纹 | — |
| AstrMessageEvent → StandardMessage 转换 | ✅ InboundAdapter | — |
| StandardMessage → ReplyMessage 转换 | ✅ OutboundSender | — |
| AstrBot API 调用 (event.send/set_result/stop_event) | ✅ OutboundSender | — |
| IM 文件上传 / 下载 | ✅ 通过 AstrBot API | — |
| HTTP 请求构造 / SSE 解析 | ✅ EmilyApiClient + SSEListener | — |
| **以下全部在 Core 容器内** | | |
| Session 池管理 / TTL | — | ✅ SessionPoolManager |
| 意图识别 / WorkItem 拆分 | — | ✅ Session-Agent |
| Pipeline BUS 执行 | — | ✅ WorkItem-Agent |
| LLM 推理 | — | ✅ LLMClient |
| 数据库 CRUD | — | ✅ Repository + Service |
| RAG 检索 | — | ✅ RagProvider |
| Hook 审计 / 鉴权 / 追踪 | — | ✅ Hook 系统 |
| 用户记忆 / 项目日志 | — | ✅ Memory + Journal Service |
| SOP 解析 / 路由 | — | ✅ SOPIntentRegistry |

### 2.9 StandardMessage 的跨容器传输

`StandardMessage` 及 `ReplyMessage` 作为跨容器序列化协议的关键对象，需保持两份同步副本：
- **插件端**：`adapters/standard/message.py` — 用于入站转换和 HTTP 请求体序列化
- **Core 端**：`emily_core/adapters/standard/message.py` — 用于 FastAPI 自动反序列化

两个副本完全一致，由 CI/构建脚本确保同步。当前 `StandardMessage` 已是纯 dataclass（无 `astrbot` 依赖），满足跨容器传输要求。

---

## 3. Adapter 层 — Session 池与消息路由

### 3.1 定位

作为 Emily Core 容器内部的消息入口层。负责：Session 池维护、入站消息路由、原始消息归档。入站消息通过 HTTP API 从 AstrBot 插件转发而来，Adapter 层接收后分流至 Session 层。

### 3.2 模块概览

```
adapters/
├── standard/                 # 跨平台标准协议对象
│   ├── message.py           # StandardMessage — 统一入站消息 (13 字段)
│   ├── reply.py             # ReplyMessage — 统一出站回复
│   ├── route_decision.py    # RouteDecision — 接管决策结果
│   ├── result.py            # RouteResult / HandlerResult / AgentResult
│   └── command.py           # EventCommand / TaskCommand / MeetingCommand / FileCommand
│
├── astrbot/                  # AstrBot 平台适配器
│   ├── inbound_adapter.py   # AstrBotInboundAdapter — AstrMessageEvent → StandardMessage
│   └── outbound_sender.py   # AstrBotOutboundSender — ReplyMessage → IM 发送
│
└── session/                  # 【新建】Session 池管理层
    ├── session_pool.py      # SessionPoolManager — Session 生命周期管理
    ├── session_factory.py   # SessionFactory — Session 创建 + Agent 灌注
    └── session_config.py    # SessionConfig — TTL / 最大并发 / 清理策略
```

### 3.3 StandardMessage（统一入站消息）

```python
# adapters/standard/message.py
@dataclass
class StandardMessage:
    platform: str              # 平台标识 (qq / wechat / dingtalk)
    conversation_type: str     # 会话类型 (private / group)
    conversation_id: str       # 会话 ID
    sender_id: str             # 发送者 IM ID
    sender_name: str           # 发送者昵称
    content: str               # 消息正文
    message_id: str            # 平台消息 ID（去重用）
    timestamp: str             # 消息时间戳 (ISO8601)
    attachments: list          # 附件列表 [{type, url, filename, size}]
    is_at_bot: bool            # 是否 @机器人
    group_id: str | None       # 群 ID（群聊场景）
    replied_message: dict | None  # 被回复的消息
    raw_event: Any             # 原始平台事件（适配器内部使用）
```

### 3.4 SessionPoolManager（Session 池）

> **状态：新建模块** — 当前 M15 阶段尚无 Session 池，每条消息独立创建 PipelineContext 处理。

**核心职责：**

1. **Session 池维护**
   - 以 `conversation_id` 为 key 维护活跃 Session 的哈希表
   - 每个 Session 持有：Session-Agent 实例 + Session 状态机 + 活跃 WorkItem 列表

2. **消息路由**
   ```
   InboundMessage
       │
       ▼
   SessionPool.lookup(conversation_id)
       │
       ├── 命中 → 路由至对应 Session.handle_message()
       │
       └── 未命中 → SessionFactory.create(conversation_id, user_id)
                        │
                        ├── 灌入 Session-Agent (知识灌注)
                        ├── 创建 Session 状态机
                        ├── SessionPool.add(session)
                        └── session.handle_message(message)
   ```

3. **Session TTL 管理**
   - 默认 TTL：10 分钟无新消息
   - 后台定时器扫描过期 Session
   - 过期触发注销流程（不直接销毁）

4. **并发控制**
   - 同一 `conversation_id` 的消息串行处理（Session 内锁）
   - 不同 Session 完全并行，无锁竞争

### 3.5 Session 注销流程

```
Adapter 检测 Session TTL 过期
    │
    ▼
发送注销命令给 Session
    │
    ▼
Session-Agent 执行注销归档 SOP (SOP-010-SYS-session_archive)
    ├── 更新用户长期记忆 — 历史对话摘要
    ├── Session 通信记录数据库归档
    ├── 更新 Session 状态机 → ARCHIVED
    └── 通知 SessionPool 可安全销毁
    │
    ▼
SessionPool.remove(session_id) → 销毁 Session-Agent 实例
```

### 3.6 现有模块映射

| 策略概念 | 现有模块 | 文件位置 | 状态 |
|---------|---------|---------|------|
| 消息去重 + 格式转换 | `main.py` + `AstrBotInboundAdapter` | `main.py:on_message()` / `adapters/astrbot/inbound_adapter.py` | ✅ 已实现 |
| 入站消息原始归档 | `MessageRepository.create_from_standard()` | `repositories/message_repo.py` | ✅ 已实现 |
| Session 池管理 | 尚无 | — | ❌ 新建 |
| Session 创建 / 注销 | 尚无 | — | ❌ 新建 |
| 出站消息发送 | `AstrBotOutboundSender` | `adapters/astrbot/outbound_sender.py` | ✅ 已实现 |

---

## 4. Session 层 — Session-Agent 调度核心

### 4.1 定位

Session 层是每条用户会话的主脑。以 **Session-Agent** 为核心，负责：
- 用自然语言与用户交互
- 对入站消息做意图识别 + WorkItem 拆分
- 管理多 WorkItem 并发、优先级调度、待确认队列
- 出站消息审核
- Session 注销归档

### 4.2 模块概览

```
session/                              # 【新建】Session 层模块
├── session_agent.py                 # SessionAgent — 会话调度主 Agent
├── session_state.py                 # SessionState — 会话状态机
├── session_context.py               # SessionContext — 知识灌注数据类
├── focus_lock.py                    # FocusLock — 交互优先级调度器
├── confirm_queue.py                 # ConfirmQueue — 待确认任务队列
└── session_archive.py               # SessionArchive — 注销归档 SOP 执行

# 现有模块（融入 Session 层）
agent/
├── master_agent.py                  # MasterAgent → 升级为 SessionAgent
├── conversation_context.py          # ConversationContext ← 滑动窗口上下文
└── intent_registry.py               # SOPIntentRegistry ← SOP 目录
```

### 4.3 Session-Agent（会话主 Agent）

> **状态：演化升级** — 当前 `MasterAgent` 承担了部分 Session-Agent 职责（意图识别 + 路由），需升级为完整的 Session-Agent。

**核心职责：**

#### 4.3.1 知识灌注（Session 创建时 — 最小化原则）

Session 创建时只加载**必须立即获取**的上下文，其余全部懒加载。原则：
- ✅ 直接拿：最近对话（滑动窗口）、用户摘要（偏好+历史摘要）
- ✅ 一级压缩摘要：SOP 目录、工具目录（仅大类文件夹名 + 一句话功能描述）
- ❌ 不拿：SOP 全文、完整 schema、详细工具参数说明 → 懒加载

```
SessionAgent 初始化
    │
    ├── ┌─────────────────────────────────────────────┐
    │   │ 1. 用户最近对话 (滑动窗口, 最近 20 轮)         │  ← ConversationContext
    │   │ 2. 用户长期记忆 — 压缩摘要版:                  │  ← UserMemoryService
    │   │    · 用户偏好 (例: "喜欢简洁回复, 负责消防")    │
    │   │    · 历史对话摘要 (例: "上周讨论过材料进场")    │
    │   │                                             │
    │   │ ── 以下全部为高度压缩的一级摘要 ──              │
    │   │ 3. SOP 目录摘要 (仅大类名+一句话描述):          │  ← SOPIntentRegistry.summary()
    │   │    · REC  — 记录类 (事件/任务/会议/文件)       │
    │   │    · QRY  — 查询类 (跨实体数据查询)            │
    │   │    · FLOW — 流程类 (守护审计/业务流程)         │
    │   │    · SYS  — 系统类 (用户记忆/待处理问题)       │
    │   │                                             │
    │   │ 4. 工具目录摘要 (仅大类名+一句话描述):          │  ← ToolRegistry.summary()
    │   │    · 写入工具: record_event/task/meeting/file  │
    │   │    · 查询工具: query_data                     │
    │   │    · 检索工具: knowledge_search              │
    │   │    · 文件工具: read_local_file/send_file      │
    │   │                                             │
    │   │ 5. 数据库结构摘要 (懒加载, 仅一级表名列表):     │  ← 【新增】SchemaSummaryBuilder.lazy()
    │   │    · 核心表: events/tasks/meetings/files      │
    │   │    · 流程表: business_flow/instruction/plan   │
    │   │    · 详情按需加载                              │
    │   │                                             │
    │   │ 6. 当前日期时间                              │  ← datetime.now()
    │   └─────────────────────────────────────────────┘
    │
    ▼
组装 System Prompt → 初始化 LLM 对话
(预估 token 消耗: ~500-800，远低于完整灌入的 3000+)
```

**懒加载触发时机：**

| 触发条件 | 加载内容 | 加载方式 |
|---------|---------|---------|
| 路由匹配到具体 SOP | 该 SOP 全文 | `SOPLoader.load_full_text(sop_id)` |
| WorkItem 需要写入某表 | 该表完整 schema | `SchemaSummaryBuilder.load_table(table_name)` |
| Agent 调用具体工具 | 工具完整参数说明 | `ToolRegistry.get_tool_detail(tool_name)` |
| 用户问到特定项目详情 | 项目状态详情 | `QueryService.summary(project_id)` |

#### 4.3.2 入站消息处理

```
InboundMessage
    │
    ▼
┌─────────────────────────────────────────────────────┐
│           Session-Agent 意图分析                      │
│                                                       │
│  是短路指令？                                          │
│  ├── 闲聊 / 问候 / 简单的信息确认                      │
│  │   → Session-Agent 直接组织自然语言回复               │
│  │                                                      │
│  ├── WorkItem 交互响应（确认/拒绝/修改）                 │
│  │   → 更新对应 WorkItem 状态机 + 恢复执行               │
│  │                                                      │
│  └── 兜底（无法理解 / 超出能力）                         │
│      → 给出引导性回复                                   │
│                                                       │
│  非短路指令？                                          │
│  ├── 单一任务 → 创建 1 个 WorkItem                     │
│  └── 复合任务 → 拆分为 N 个 WorkItem，规划执行顺序      │
└─────────────────────────────────────────────────────┘
```

#### 4.3.3 多任务交互排队

```
待确认队列 (PriorityQueue)
    │
    ├── WorkItem-A 需要用户确认参数 → 排队序号 #1
    ├── WorkItem-B 执行完成需审核   → 排队序号 #2
    └── WorkItem-C 遇到异常需决策   → 排队序号 #3
    │
    ▼
Session-Agent 逐项向用户呈现，等待响应
    │
    ▼
用户回复 → 焦点匹配 → 投递到对应 WorkItem
```

#### 4.3.4 交互优先级调度 (FocusLock)

```
┌──────────────────────────────────────────────────────────────────┐
│              FocusLock — 交互优先级调度器                           │
│                                                                    │
│  本质: 不是硬锁，是优先级队列 + 上下文感知的调度决策                  │
│                                                                    │
│  调度规则:                                                          │
│     1. 用户最新消息指向的主题 → 自动提升为当前焦点 (priority boost)   │
│     2. 未完成的 WorkItem 待确认项 → 按紧急性排队                    │
│     3. 用户显式切换话题 (如"先不管X，说Y") → 手动焦点切换            │
│     4. 同主题多条消息 → 合并序列，不打断                             │
│     5. 旧焦点未确认超时 → 温和提醒，不强制阻塞新消息                  │
│                                                                    │
│  用户发送消息                                                        │
│      │                                                              │
│      ├── 消息关联到当前焦点 WorkItem → 直接投递，继续交互            │
│      │                                                              │
│      ├── 消息关联到其他 WorkItem (排队中) → 提问用户:                │
│      │    "你之前提到的事还没处理完，先处理哪个？"                     │
│      │    → 用户选择 → 焦点切换                                     │
│      │                                                              │
│      ├── 完全新话题 → 加入待确认队列，不丢失                          │
│      │    → 当前焦点交互完成后，自动拉取下一个                        │
│      │                                                              │
│      └── 用户说"等一下" / "先处理这个" → 直接切换焦点                │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘
```

### 4.4 Session 状态机

> **注：状态机细节尚未完善，后期需根据使用需求逐步补充丰富。**

```
                    ┌──────────┐
                    │  CREATED  │  ← Session 创建，最小化灌注
                    └────┬─────┘
                         │ 灌注完成
                         ▼
               ┌─────────────────┐
               │     ACTIVE      │  ← 正常工作状态
               └───────┬─────────┘
                       │
          ┌────────────┼────────────┐
          │            │            │
    用户消息       WorkItem       TTL 超时
          │      需要确认交互         │
          ▼            ▼            ▼
   ┌──────────┐ ┌──────────────┐ ┌─────────────┐
   │ (继续)    │ │ WAITING_      │ │ ARCHIVING   │
   │           │ │ CONFIRM      │ │ (注销归档中)  │
   └──────────┘ └──────┬───────┘ └──────┬──────┘
                       │ 用户回复        │ 归档完成
                       ▼                ▼
               ┌──────────────┐ ┌─────────────┐
               │   ACTIVE     │ │   CLOSED     │ ← 安全销毁
               │  (恢复执行)   │ │  (等待销毁)   │
               └──────────────┘ └─────────────┘
```

> 后续待完善：子状态定义、过渡条件细化、异常恢复路径、超时回退策略

### 4.5 出站消息流程

```
WorkItem 产出结果
    │
    ▼
Session-Agent 审核
    ├── 格式审核: 是否符合 IM 平台格式要求
    ├── 内容审核: 是否包含敏感信息 / 错误内容
    ├── 鉴权审核: 回复内容是否在用户权限范围内
    └── 通过 → 编排自然语言 → 加入发送队列
    │
    ▼
出站消息存档 (MessageRepository.create_outbound)
    │
    ▼
AstrBotOutboundSender.send(reply_message)
```

### 4.6 附件管理

Session 层统一管理入站消息的附件下载：

```
InboundMessage 含附件
    │
    ▼
FileStorageService.download_attachments()
    ├── 从 IM URL 下载到本地 files/message_attachments/
    ├── SHA256 去重检测
    ├── 生成 file_no (F开头的唯一编号)
    └── 存储 File 记录到数据库
    │
    ▼
WorkItem-Agent 可按需读取/发送文件
    (通过 read_local_file / send_file 工具)
```

### 4.7 现有模块映射

| 策略概念 | 现有模块 | 文件位置 | 状态 |
|---------|---------|---------|------|
| Session-Agent | MasterAgent (部分) | `agent/master_agent.py` | ⚠️ 需升级 |
| 意图识别 + 路由 | SOPIntentRegistry + MasterAgent ReAct | `agent/intent_registry.py` | ✅ 已有 |
| 滑动窗口对话 | ConversationContext | `agent/conversation_context.py` | ✅ 已有 |
| 用户长期记忆 | UserMemoryService | `services/user_memory_service.py` | ✅ 已有 |
| 项目事件日志 | EventJournal | `services/event_journal.py` | ✅ 已有 |
| Session 状态机 | 尚无 | — | ❌ 新建 |
| 优先级调度 | 尚无 | — | ❌ 新建 |
| 待确认队列 | CheckpointService (部分) | `services/checkpoint_service.py` | ⚠️ 需扩展 |
| 附件下载管理 | FileStorageService | `services/file_storage_service.py` | ✅ 已有 |
| 出站审核 | GuardianReview | `agent/guardian_review.py` | ✅ 已有 |
| 出站存档 | ChatArchiveService | `services/chat_archive_service.py` | ✅ 已有 |

---

## 5. WorkItem 层 — Pipeline BUS 任务执行

### 5.1 定位

WorkItem 是最小任务执行单元。**全局唯一** `WorkItem-Agent` 异步处理所有 WorkItem。新的 WorkItem 需求进来时，对当前 WorkItem-Agent **增量灌注**执行目标 WorkItem 所缺失的工具/SOP/数据 schema，遵循最小化原则，避免上下文污染。

**架构关系：Session-Scheduler → Pipeline BUS（公共总线）→ WorkItem**。Pipeline 不是某个 WorkItem 的私有执行框架，而是系统级公共执行总线。

### 5.2 模块概览

```
workitem/                             # 【演化升级】WorkItem 层模块
├── workitem_agent.py                # WorkItemAgent — 全局单例, 异步处理所有 W切项
├── workitem_state.py                # WorkItemState — 任务状态机
├── workitem_context.py              # WorkItemContext — 任务上下文 (权限+资源)
│
├── pipeline/                         # 【公共总线】系统级 Pipeline，不属于单 WorkItem
│   ├── bus.py                        # PipelineBUS — 公共执行总线
│   ├── scheduler.py                  # SessionScheduler — 调度器, 分配 WorkItem 到总线
│   ├── node.py                       # PipelineNode — 单节点 + 3 HOOK 挂载点
│   ├── hook.py                       # Hook 基类 (复用 M15)
│   └── hook_registry.py             # HookRegistry (复用 M15)
│
└── injector.py                       # 【新建】KnowledgeInjector — 增量灌注引擎
    └── 按需加载缺失的 SOP/工具/DB schema
```

### 5.3 WorkItem-Agent（全局单例，异步处理）

> **核心设计：不是每个 WorkItem 创建独立 Agent，而是全局唯一 Agent 实例，异步处理所有 WorkItem。新 WorkItem 进来时，增量注入执行该 WorkItem 缺失的知识（SOP/工具/schema），最小化上下文污染。**

```
                         ┌──────────────────────────┐
                         │   WorkItem-Agent (全局单例) │
                         │                            │
    WorkItem Queue       │  ┌──────────────────────┐ │
    ┌──────────────┐     │  │ 当前上下文 Context     │ │
    │ WI-A (执行中) │────▶│  │ · SOP: SOP-002 全文   │ │
    ├──────────────┤     │  │ · Tools: record_event  │ │
    │ WI-B (排队)   │     │  │ · DB: events 表 schema │ │
    ├──────────────┤     │  └──────────────────────┘ │
    │ WI-C (排队)   │     │                            │
    └──────────────┘     │  KnowledgeInjector          │
                         │  ┌──────────────────────┐ │
    WI-D 新进             │  │ 增量灌注引擎            │ │
    ──────────────▶      │  │                        │ │
    "创建任务:           │  │ WI-D 需求:              │ │
     材料验收"           │  │ · SOP: SOP-003 (未加载) → 加载全文  │ │
                         │  │ · Tools: record_task (未加载)→加参数 │ │
                         │  │ · DB: tasks 表 (未加载)→加载 schema │ │
                         │  │ · SOP-002/events 已加载 → 跳过      │ │
                         │  └──────────────────────┘ │ │
                         │                            │
                         │  被注入知识 = WI-D 需求 - 现有上下文 │
                         └────────────────────────────┘
```

**增量灌注流程：**

```
新 WorkItem 到达
    │
    ▼
Session-Scheduler 分配到 WorkItem-Agent
    │
    ▼
KnowledgeInjector.analyze(workitem)
    ├── 计算所需资源: SOP ID / Tool Names / DB Tables
    ├── 与 WorkItem-Agent 当前上下文求差集
    ├── 仅加载缺失部分:
    │   ├── SOP 全文 (若该 SOP 尚未加载)
    │   ├── 工具参数详情 (若该工具尚未加载)
    │   └── 数据库表 schema (若该表尚未加载)
    └── 注入 → WorkItem-Agent 上下文 → 执行
    │
    ▼
执行完成 → 释放增量资源（保留高频复用的基础上下文）
```

**核心职责：**

1. **上下文管理** — 维护当前灌注的知识集合，新 WI 进来增量追加
2. **执行循环** — 接收 Session-Scheduler 分配，在 Pipeline BUS 上执行 WI
3. **成果输出** — 编排结果，通知 Session-Agent
4. **上下文回收** — WI 完成后释放不再使用的知识，避免无限膨胀

### 5.4 Session-Scheduler → Pipeline BUS（公共执行总线）

> **架构关系：Session-Scheduler 将 WorkItem 分配到公共 Pipeline BUS 执行。Pipeline 是系统级公共总线，不属于单个 WorkItem 私有。**

```
┌──────────────────────────────────────────────────────────────────┐
│                 Session-Scheduler (调度器)                         │
│                                                                    │
│  每个 Session 一个 Scheduler，负责该 Session 下的所有 WorkItem 调度  │
│                                                                    │
│  职责:                                                              │
│  ├── 管理 WorkItem 创建/排队/优先级                                │
│  ├── 分配 WorkItem 到公共 Pipeline BUS                            │
│  ├── 监测 WorkItem 执行状态                                       │
│  └── 处理 WorkItem 挂起/恢复/终止                                  │
└──────────────────────────┬───────────────────────────────────────┘
                           │ 分配 WorkItem
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                 Pipeline BUS (公共执行总线)                         │
│                                                                    │
│  系统级公共总线，所有 Session 的 WorkItem 共享此 BUS 执行            │
│  每个节点有 3 个 HOOK 挂载点: [before] [after] [on_error]           │
│  Hook 三态决策: ALLOW / WARN / BLOCK                               │
│  deny always wins (before hook 异常 → BLOCK)                       │
│                                                                    │
│  ┌──────────────────┐                                              │
│  │ NODE 1: 意图+拆分 │  ← 输入: 用户原始意图                         │
│  │  before: 鉴权     │  ← 输出: task 列表 + 依赖关系                  │
│  │  after:  审计日志  │                                              │
│  │  on_error:错误记录 │                                              │
│  └────────┬─────────┘                                              │
│           │                                                        │
│           ▼                                                        │
│  ┌──────────────────┐                                              │
│  │ NODE 2: 计划+标准 │  ← 输入: task 列表                            │
│  │  before: 资源检查 │  ← 输出: 执行计划 + 验收标准                    │
│  │  after:  审计日志  │                                              │
│  │  on_error:错误记录 │                                              │
│  └────────┬─────────┘                                              │
│           │                                                        │
│           ▼                                                        │
│  ┌──────────────────┐                                              │
│  │ NODE 3: 执行+验收 │  ← 输入: 执行计划                              │
│  │  before: 权限核实 │  ← 输出: 执行结果 (数据+状态)                   │
│  │  after:  审计日志  │                                              │
│  │  on_error:错误恢复 │                                              │
│  └────────┬─────────┘                                              │
│           │                                                        │
│           ▼                                                        │
│  ┌──────────────────┐                                              │
│  │ NODE 4: 成果总结  │  ← 输入: 执行结果汇总                           │
│  │  before: 审核     │  ← 输出: 最终成果 (人类可读)                     │
│  │  after:  审计日志  │                                              │
│  │  on_error:降级输出 │                                              │
│  └──────────────────┘                                              │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘
```

### 5.5 WorkItem 状态机

```
┌──────────┐
│ CREATED  │  ← WorkItem 创建，加入 Session-Scheduler 队列
└────┬─────┘
     │ 调度器分配 + KnowledgeInjector 增量灌注完成
     ▼
┌──────────┐
│ PLANNING │  ← Node 1+2: 意图分析 + 制定计划
└────┬─────┘
     │ 计划就绪, 公共 Pipeline BUS 可用
     ▼
┌──────────────┐
│  EXECUTING   │  ← Node 3: 在公共 Pipeline 上执行 + 验收循环
└──┬───┬───┬───┘
   │   │   │
   │   │   └── 不可恢复错误 → FAILED (终态)
   │   │
   │   └── 需要用户交互
   │        │
   │        ▼
   │   ┌──────────────────┐
   │   │ WAITING_CONFIRM  │  ← 挂起, 等 Session-Agent 代理交互
   │   └────────┬─────────┘
   │       用户回复 → Session-Scheduler 恢复
   │        │
   │        ▼
   │   ┌──────────────┐
   │   │  EXECUTING   │ (恢复执行, 重新入队到 Pipeline BUS)
   │   └──────────────┘
   │
   └── 全部 task 完成
        │
        ▼
   ┌──────────┐
   │   DONE   │  ← Node 4: 成果总结 → Session-Agent 完工确认 → 销毁
   └──────────┘
```

### 5.6 WorkItem 销毁

```
WorkItem 状态 = DONE
    │
    ▼
Session-Agent 收到完工通知
    ├── 审核最终成果
    ├── 确认用户无需再交互
    ├── WorkItem-Agent 上下文回收 (释放该 WI 独占的知识)
    └── 销毁 WorkItem → 释放资源
```

### 5.7 与 M15 Pipeline 的关系

| 维度 | M15 PipelineScheduler (当前) | Pipeline BUS (目标) |
|------|---------------------------|-------------------|
| 阶段数 | 8 阶段 | 4 节点 (意图→计划→执行→总结) |
| 作用域 | 整个消息处理流程（全局单例） | **系统级公共总线**，所有 Session/WorkItem 共享 |
| 调度器 | PipelineScheduler 自驱动 | **Session-Scheduler** 分配 WorkItem 入队 |
| Agent | Mock 组件 | **全局单例 WorkItem-Agent**（非每 WI 创建） |
| Hook | 12 个 Hook（跨 8 阶段） | 12 个 Hook 挂载点（4 节点 × 3），公共总线统一管理 |
| 知识加载 | 无 | **KnowledgeInjector 增量灌注**（按需加载缺失项） |
| 关系 | — | M15 的 Hook 系统/Context 模式完整复用 |

### 5.8 现有模块映射

| 策略概念 | 现有模块 | 文件位置 | 状态 |
|---------|---------|---------|------|
| WorkItem-Agent (全局单例) | BusinessFlowAgent | `agent/business_flow_agent.py` | ⚠️ 需升级为全局单例 + 增量灌注 |
| Pipeline BUS (公共总线) | PipelineScheduler (8 阶段) | `pipeline/pipeline_scheduler.py` | ⚠️ 需简化为 4 节点公共总线 |
| Session-Scheduler | 尚无 | — | ❌ 新建 |
| KnowledgeInjector | 尚无 | — | ❌ 新建 (增量灌注引擎) |
| Hook 挂载点 | Hook + HookRegistry | `pipeline/hook.py` + `hook_registry.py` | ✅ 可直接复用 |
| 鉴权注入 | pipeline_config_m15.json | `data/config/pipeline_config_m15.json` | ✅ 声明式模式可用 |
| WorkItem 状态机 | 尚无 | — | ❌ 新建 |
| 结构化工具执行 | BusinessFlowToolRegistry | `tools/business_flow_tools.py` | ✅ 已有 |
| 任务/事件/会议/文件 | Tool handler 函数 | `tools/event_tool.py` 等 | ✅ 已有 |

---

## 6. 基础设施层 — 数据与能力基座

### 6.1 定位

为上层（Adapter / Session / WorkItem）提供：LLM 推理、知识检索、数据持久化、文件存储、服务编排。

### 6.2 模块全景

```
emily_core/
├── infrastructure/
│   ├── llm/
│   │   └── client.py              # LLMClient — AsyncOpenAI 封装
│   │                              #   · chat() / chat_json() / chat_with_tools()
│   │                              #   · reasoning_content 支持 (DeepSeek)
│   │                              #   · M11 追踪回调
│   └── database/
│       ├── session.py             # DB 连接管理 (PostgreSQL, pool)
│       └── models.py              # 22 表 ORM (SQLAlchemy 2.0)
│
├── repositories/                   # 数据访问层 (10 个)
│   ├── message_repo.py            # 消息 CRUD (最大, 含 FK 解析)
│   ├── user_repo.py               # 用户 + IM 绑定
│   ├── event_repo.py              # 事件 CRUD + 编号生成
│   ├── task_repo.py               # 任务 CRUD
│   ├── meeting_repo.py            # 会议 CRUD
│   ├── file_repo.py               # 文件 CRUD + 去重
│   ├── chat_archive_repo.py       # M11 聊天归档查询
│   ├── agent_reasoning_repo.py    # M11 Agent 推理日志
│   ├── llm_interaction_repo.py    # M11 LLM 交互日志
│   └── tool_call_repo.py          # M11 工具调用日志
│
├── services/                       # 业务服务层 (15 个)
│   ├── domain_takeover_service.py # 接管决策 (observe/collaborate/managed)
│   ├── message_service.py         # 消息持久化服务
│   ├── user_binding_service.py    # 用户自动创建 + IM 绑定
│   ├── event_service.py           # 事件业务逻辑
│   ├── task_service.py            # 任务业务逻辑
│   ├── meeting_service.py         # 会议业务逻辑
│   ├── file_service.py            # 文件业务逻辑
│   ├── query_service.py           # 跨实体查询 (9 种 query_type)
│   ├── pending_issues.py          # M8a 待处理问题追踪
│   ├── event_journal.py           # M8c 项目事件日志
│   ├── user_memory_service.py     # M8c 用户长期记忆
│   ├── chat_archive_service.py    # M11 全量聊天存档
│   ├── agent_trace_service.py     # M11 Agent 全链路追踪
│   ├── file_storage_service.py    # M13 附件下载管理
│   └── checkpoint_service.py      # M12b SOP 状态检查点
│
├── providers/                      # 外部服务提供者
│   └── rag/
│       ├── base.py                # RagProvider 抽象基类
│       ├── maxkb_provider.py      # MaxKB 向量检索 (Qwen3-Embedding-0.6B)
│       └── local_fallback.py      # 本地 TF-IDF 关键词回退
│
├── config.py                       # Config dataclass (~60 字段)
├── bootstrap.py                    # 启动初始化 (DB + Config + RagProvider)
└── tools/                          # 工具注册表
    ├── __init__.py                 # create_all_tools() + create_business_flow_tools()
    ├── business_flow_tools.py     # BusinessFlowToolRegistry (框架直接执行)
    ├── event_tool.py              # record_event handler
    ├── task_tool.py               # record_task handler
    ├── meeting_tool.py            # record_meeting handler
    ├── file_tool.py               # record_file + send_file + read_local_file handler
    ├── query_tool.py              # query_data handler (9 种查询)
    ├── knowledge_search_tool.py   # RAG 知识库搜索
    ├── chat_archive_tool.py       # 聊天记录搜索
    ├── memory_tool.py             # 用户记忆读写
    └── pending_issue_tool.py      # 待处理问题管理
```

### 6.3 数据库 — 22 表模型

#### 核心业务表 (9)

| 表名 | 模型 | 关键字段 | 用途 |
|------|------|---------|------|
| `users` | User (20 字段) | perm_list, grouping, company | 系统身份 + 人事档案合并 |
| `user_im_bindings` | UserImBinding | im_platform, im_user_id | IM 账号绑定 |
| `conversations` | Conversation | im_platform, conversation_id | IM 会话记录 |
| `messages` | Message (23 字段) | direction, content, attachments | 入站/出站消息记录 |
| `projects` | Project (13 字段) | code, lifecycle_stage | 项目主数据 |
| `events` | Event (16 字段) | event_no, category, payload | 事件记录 |
| `tasks` | Task (14 字段) | task_no, status, due_date | 任务管理 |
| `meetings` | Meeting (20 字段) | meeting_no, attendees, conclusion | 会议纪要 |
| `files` | File (26 字段) | file_no, file_hash, confidentiality | 文件管理 |

#### 业务流程表 (5)

| 表名 | 模型 | 关键字段 | 用途 |
|------|------|---------|------|
| `company_info` | CompanyInfo | unified_code, business_desc | 企业基本信息 |
| `project_indicator_details` | ProjectIndicatorDetail | indicator_name, indicator_value | 项目指标数据 |
| `business_flow_orders` | BusinessFlowOrder | flow_no, flow_type, metrics | 业务流程单 |
| `instruction_orders` | InstructionOrder | instruction_no, issuer_id, executor_ids | 指令单 |
| `project_plans` + `plan_items` | ProjectPlan + PlanItem | plan_no, progress | 项目计划 + 明细 |

#### 追踪与日志表 (7)

| 表名 | 模型 | 关键字段 | 用途 |
|------|------|---------|------|
| `sop_routing_logs` | SOPRoutingLog | matched_sop_id, match_confidence | M9 SOP 路由决策 |
| `agent_reasoning_logs` | AgentReasoningLog | iteration_count, steps_json | M11 Agent 推理全链路 |
| `llm_interaction_logs` | LLMInteractionLog | prompt_tokens, latency_ms | M11 LLM 交互日志 |
| `tool_call_logs` | ToolCallLog | tool_name, tool_arguments | M11 工具调用日志 |
| `message_attachments` | MessageAttachment | attachment_type, local_path | 消息附件关联 |
| `hook_execution_logs` | HookExecutionLog | hook_name, mount_point, decision | Hook 执行审计 |
| `sop_checkpoints` | SOPCheckpoint | sop_id, node_name, status | M12b SOP 状态检查点 |

#### 数据库设计规范

- PK: `String(36)` UUID，部分使用前缀缩写 (`chk`→checkpoint, `evt`→event)
- 时间: ISO8601 字符串 (非 native datetime)
- JSON: `Text` 字段存储，默认 `"[]"` 或 `"{}"`
- 软删除: `is_deleted` 字段
- 自动时间: `created_at` (default), `updated_at` (onupdate)

### 6.4 LLM Client

```python
# infrastructure/llm/client.py
class LLMClient:
    # 三核心方法
    async chat(messages, **kwargs) -> ChatResponse
        # 单轮文本对话
    
    async chat_json(messages, **kwargs) -> dict
        # 强制 JSON 输出 (response_format={"type": "json_object"})
        # M14: BusinessFlowAgent 使用此方法做结构化工具调用
    
    async chat_with_tools(messages, tools, **kwargs) -> ChatResponse
        # 多轮工具调用 (function calling)
        # 支持 reasoning_content (DeepSeek 思考链)
        # M11: trace_callback 追踪每次 LLM 调用
```

### 6.5 RAG 知识检索

```
knowledge_search(query, top_k, stage?, role?)
    │
    ├── kb_enabled=true → MaxKBRagProvider
    │   ├── Admin login (JWT token)
    │   ├── hit_test API (纯向量检索)
    │   │   search_mode: embedding / keywords / blend
    │   │   similarity_threshold: 0.3 (默认)
    │   └── 401 → 自动重试登录
    │
    └── kb_enabled=false → LocalFileRagProvider
        ├── TF-IDF 关键词匹配
        ├── stage/role 过滤
        └── 返回文件名 + 片段
```

### 6.6 现有模块映射
| 知识检索 | MaxKB + LocalFallback | `providers/rag/` | ✅ 已有 |
| 文件存储 | FileStorageService | `services/file_storage_service.py` | ✅ 已有 |
| 记忆系统 | UserMemoryService | `services/user_memory_service.py` | ✅ 已有 |
| SOP 目录 | SOPIntentRegistry + SOPrepository/ | `agent/intent_registry.py` | ✅ 已有 |
| Schema 摘要 | 尚无 | — | ❌ 新建 (灌入用) |
| 项目状态机摘要 | QueryService.summary | `services/query_service.py` | ⚠️ 需扩展 |

---

## 7. 横切关注点 — Hook 声明式挂载

### 7.1 设计理念

所有横切关注点（鉴权、审计、核验、追踪、进度通知）通过 **Hook 声明式配置**挂载到 Pipeline 节点上，不改 Pipeline 核心代码。Hook 系统完整复用 M15 的实现，适配到 WorkItem 层的 4 节点 Pipeline。

### 7.2 Hook 挂载点体系

```
┌─────────────────────────────────────────────────────────────────┐
│                         Hook 挂载点地图                           │
│                                                                   │
│  Adapter 层                                                       │
│  ├── before:session_create    → 创建 Session 前鉴权                │
│  ├── after:session_create     → 审计日志                          │
│  ├── before:session_destroy   → 注销前归档确认                     │
│  └── after:session_destroy    → 审计日志                          │
│                                                                   │
│  Session 层                                                       │
│  ├── before:message_handle    → 入站消息鉴权                      │
│  ├── after:message_handle     → 审计日志 + 追踪                   │
│  ├── before:reply_send        → 出站审核 (GuardianReview)         │
│  └── after:reply_send         → 出站存档 + 追踪                   │
│                                                                   │
│  WorkItem 层 (每 Pipeline 节点)                                    │
│  ├── Node 1 (意图+拆分)                                           │
│  │   ├── before  → 鉴权: 用户是否有权执行此 SOP                     │
│  │   ├── after   → 审计: 记录意图识别决策                          │
│  │   └── on_error → 错误追踪                                      │
│  ├── Node 2 (计划+标准)                                           │
│  │   ├── before  → 资源鉴权: 检查所需数据库/工具权限                │
│  │   ├── after   → 审计: 记录执行计划                              │
│  │   └── on_error → 错误追踪                                      │
│  ├── Node 3 (执行+验收)                                           │
│  │   ├── before  → 权限核实 + 追踪开始                             │
│  │   ├── after   → 审计: SOP 执行完成 + 追踪结束                   │
│  │   └── on_error → 错误恢复/降级                                  │
│  └── Node 4 (成果总结)                                            │
│      ├── before  → 审核: GuardianReview 深审                       │
│      ├── after   → 审计: 成果记录                                  │
│      └── on_error → 降级输出                                       │
└─────────────────────────────────────────────────────────────────┘
```

### 7.3 Hook 三态决策

```python
# pipeline/hook.py (M15 复用)
class HookDecision(enum.Enum):
    ALLOW = "allow"   # 放行
    WARN  = "warn"    # 非致命警告，继续执行
    BLOCK = "block"   # 立即终止管道

# 核心规则:
# 1. before hook 异常 → BLOCK (安全第一原则)
# 2. after hook 异常 → 不阻断 (已执行的操作不能回滚)
# 3. deny always wins → 多个 hook 任一返回 BLOCK 则终止
```

### 7.4 Hook 类型

| Hook 类型 | 用途 | 挂载时机 | 决策倾向 |
|-----------|------|---------|---------|
| `auth` | 鉴权: 用户身份/权限验证 | before | 可 BLOCK |
| `audit` | 审计: 操作日志记录 | after | 仅 ALLOW/WARN |
| `verify` | 核验: GuardianReview 内容审核 | before | 可 WARN |
| `deep_audit` | 深审: GuardianAgent 全面调查 | before | 可 WARN |
| `trace` | 追踪: Agent 推理过程记录 | before/after | 仅 ALLOW |
| `progress` | 进度: 发送 "处理中..." 给用户 | after | 仅 ALLOW |

### 7.5 声明式配置

```jsonc
// data/config/hook_config.json (从 pipeline_config_m15.json 演化)
{
  "hooks": [
    // === Adapter 层 ===
    { "mount": "before:session_create",  "type": "auth",   "name": "auth.session_create",   "enabled": true },
    { "mount": "after:session_destroy",  "type": "audit",  "name": "audit.session_destroy",  "enabled": true },
    
    // === Session 层 ===
    { "mount": "before:reply_send",      "type": "verify", "name": "guardian.reply_review", "enabled": true },
    { "mount": "after:reply_send",       "type": "audit",  "name": "audit.outbound_sent",   "enabled": true },
    
    // === WorkItem 层 ===
    { "mount": "before:wi_node1",        "type": "auth",   "name": "auth.sop_access",       "enabled": true },
    { "mount": "after:wi_node1",         "type": "audit",  "name": "audit.intent_result",   "enabled": true },
    { "mount": "before:wi_node2",        "type": "auth",   "name": "auth.resource_check",   "enabled": true },
    { "mount": "after:wi_node2",         "type": "audit",  "name": "audit.plan_created",    "enabled": true },
    { "mount": "before:wi_node3",        "type": "trace",  "name": "trace.execution_start", "enabled": true },
    { "mount": "after:wi_node3",         "type": "audit",  "name": "audit.sop_completed",   "enabled": true },
    { "mount": "on_error:wi_node3",      "type": "audit",  "name": "audit.execution_error", "enabled": true },
    { "mount": "before:wi_node4",        "type": "verify", "name": "guardian.deep_audit",   "enabled": false },
    { "mount": "before:wi_node4",        "type": "verify", "name": "guardian.reply_review", "enabled": true },
    { "mount": "after:wi_node4",         "type": "audit",  "name": "audit.result_final",    "enabled": true }
  ]
}
```

### 7.6 现有模块映射

| 策略概念 | 现有模块 | 状态 |
|---------|---------|------|
| Hook 基类 + 三态决策 | `pipeline/hook.py` (6 种 Hook) | ✅ 复用 |
| HookRegistry | `pipeline/hook_registry.py` | ✅ 复用 |
| Hook 配置 | `pipeline_config_m15.json` | ⚠️ 适配到新挂载点 |
| GuardianReview | `agent/guardian_review.py` | ✅ 复用 |
| DeepAuditHook | GuardianAgent | ✅ 复用 |
| 追踪日志 | AgentTraceService | ✅ 复用 |

---

## 8. 鉴权体系

### 8.1 设计原则

- **创建时注入，执行时检查** — 权限在 Session/WorkItem 创建时注入状态机，执行时由 Hook 检查
- **最小权限** — WorkItem-Agent 仅能访问任务必需的表/SOP/工具
- **声明式管理** — 权限清单维护在数据库中 (`users.perm_list`)，不在代码中硬编码

### 8.2 鉴权流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        鉴权全景流程                               │
│                                                                   │
│  Step 0: 系统启动                                                 │
│  users 表 ──→ perm_list (JSON 数组，定义用户可访问的资源)           │
│                                                                   │
│  Step 1: Session 创建 (Adapter 层)                                │
│  before:session_create Hook                                      │
│      ├── 查询 users.perm_list                                    │
│      ├── 注入 Session 状态机                                       │
│      └── 未授权用户 → BLOCK (无 Session 创建)                      │
│                                                                   │
│  Step 2: WorkItem 创建 (Session 层)                               │
│  Session-Agent 分析任务需求                                        │
│      ├── 确定所需: SOP / 数据库表 / 工具                           │
│      ├── 与用户 perm_list 交集 → 仅注入授权资源                    │
│      └── 权限不足但可降级 → WARN + 降级执行                        │
│          权限完全不足 → 向用户说明，不创建 WorkItem                 │
│                                                                   │
│  Step 3: WorkItem 执行 (Pipeline 节点)                             │
│  before:wi_node2 (资源鉴权)                                       │
│      ├── 核实 WorkItem-Agent 使用的资源在授权范围内                │
│      ├── 越权访问 → BLOCK + 审计告警                               │
│      └── 通过 → ALLOW                                             │
│                                                                   │
│  Step 4: 出站审核 (Session 层)                                    │
│  before:reply_send (GuardianReview)                               │
│      ├── 审核回复内容是否包含越权信息                              │
│      ├── 敏感数据泄露 → WARN + 自动脱敏                            │
│      └── 通过 → ALLOW                                             │
└─────────────────────────────────────────────────────────────────┘
```

### 8.3 权限清单结构

```jsonc
// users.perm_list 示例
[
  // 数据库表权限
  { "type": "table", "name": "events",        "access": "read_write" },
  { "type": "table", "name": "tasks",         "access": "read_write" },
  { "type": "table", "name": "projects",      "access": "read" },
  
  // SOP 业务流权限
  { "type": "sop",   "name": "SOP-002-REC-event_record",  "access": "execute" },
  { "type": "sop",   "name": "SOP-003-REC-task_manage",   "access": "execute" },
  
  // 基座工具权限
  { "type": "tool",  "name": "record_event",               "access": "execute" },
  { "type": "tool",  "name": "query_data",                 "access": "execute" },
  { "type": "tool",  "name": "knowledge_search",           "access": "execute" }
]
```

### 8.4 现有模块映射

| 策略概念 | 现有模块 | 状态 |
|---------|---------|------|
| 用户权限存储 | `users.perm_list` (ORM) | ✅ 已有 |
| 管理员检查 | auth.admin_check Hook | ✅ 已有 |
| 工具白名单 | BusinessFlowAgent.allowed_tool_names | ✅ 已有（SOP 驱动） |
| 表级鉴权 | 尚无 | ❌ 新建 |
| SOP 鉴权 | SOPIntentRegistry (SOP 目录可见性) | ⚠️ 需扩展 |

---

## 9. 数据流全景 — 端到端消息处理时序

```
用户 (QQ)                Adapter 层                 Session 层              WorkItem 层          基础设施
   │                        │                          │                       │                  │
   │  "帮我创建事件:        │                          │                       │                  │
   │   样板段放线完成"       │                          │                       │                  │
   │ ──────────────────────▶│                          │                       │                  │
   │                        │                          │                       │                  │
   │                   ┌────┴────┐                     │                       │                  │
   │                   │ 消息去重  │                    │                       │                  │
   │                   │ SHA256   │                    │                       │                  │
   │                   └────┬────┘                     │                       │                  │
   │                        │                          │                       │                  │
   │                   ┌────┴────┐                     │                       │                  │
   │                   │ 格式转换  │                    │                       │                  │
   │                   │ →StdMsg  │                    │                       │                  │
   │                   └────┬────┘                     │                       │                  │
   │                        │                          │                       │                  │
   │                   ┌────┴────────────────────┐     │                       │                  │
   │                   │ 入站原始归档 (DB 写入)    │     │                       │        DB        │
   │                   └────┬────────────────────┘ ──────────────────────────────────────────────▶│
   │                        │                          │                       │                  │
   │                   ┌────┴────┐                     │                       │                  │
   │                   │Session   │                     │                       │                  │
   │                   │Pool      │                     │                       │                  │
   │                   │Lookup    │                     │                       │                  │
   │                   └────┬────┘                     │                       │                  │
   │                        │                          │                       │                  │
   │              ┌─────────┴─────────┐                │                       │                  │
   │              │ 未命中 → 创建新     │                │                       │                  │
   │              │ Session            │                │                       │                  │
   │              └─────────┬─────────┘                │                       │                  │
   │                        │                          │                       │                  │
   │              ┌─────────┴──────────────────────────┴───────────────────────┴──────────────────┐
   │              │ SessionFactory.create()                                                            │
   │              │   ├── 查询用户权限 (DB)                                                             │
   │              │   ├── 获取用户长期记忆 (UserMemoryService)                                          │
   │              │   ├── 获取 SOP 目录 (SOPIntentRegistry)                                             │
   │              │   ├── 获取项目摘要 (QueryService)                                                    │
   │              │   ├── 获取最近对话 (ConversationContext) ← 20 轮滑动窗口                              │
   │              │   ├── 组装 System Prompt                                                            │
   │              │   ├── 初始化 LLM 对话                                                                │
   │              │   └── 创建 Session-Agent 实例                                                       │
   │              └──────────────────────────────────────────────────────────────────────────────────┘
   │                        │
   │              ┌─────────┴─────────┐
   │              │ SessionPool.add() │
   │              └─────────┬─────────┘
   │                        │
   │                        │  StandardMessage
   │                        ▼
   │              ┌─────────────────────┐
   │              │ Session-Agent 接收   │
   │              │                     │
   │              │  意图分析:            │
   │              │  "创建事件" →         │
   │              │  匹配 SOP-002-REC    │
   │              │  非短路指令 →          │
   │              │  创建 WorkItem        │
   │              └─────────┬───────────┘
   │                        │
   │              ┌─────────┴──────────────────────────┐
   │              │ WorkItemFactory.create()            │
   │              │   ├── 加载 SOP-002-REC 全文          │
   │              │   ├── 加载 events 表 schema          │
   │              │   ├── 白名单工具: record_event 等    │
   │              │   ├── 注入用户权限列表                │
   │              │   ├── 任务要求: "记录样板段放线完成"   │
   │              │   └── 创建 WorkItem-Agent            │
   │              └─────────────────────────────────────┘
   │                        │
   │                        │  WorkItem 加入 Session 管理
   │                        ▼
   │              ┌─────────────────────────────────────────┐
   │              │        Pipeline BUS 执行                 │
   │              │                                          │
   │              │  Node 1 [意图+拆分]                       │
   │              │    before: auth.sop_access → ALLOW       │
   │              │    执行: 确定单线任务: "记录事件"          │
   │              │    after:  audit.intent_result           │
   │              │         │                                │
   │              │         ▼                                │
   │              │  Node 2 [计划+标准]                       │
   │              │    before: auth.resource_check → ALLOW   │
   │              │    执行: 计划: 1) 解析事件内容             │
   │              │              2) 验证必填字段              │
   │              │              3) 写入 events 表            │
   │              │              4) 返回结果给用户            │
   │              │    验收: event_no 存在, 字段完整           │
   │              │    after:  audit.plan_created            │
   │              │         │                                │
   │              │         ▼                                │
   │              │  Node 3 [执行+验收]                       │
   │              │    before: trace.execution_start         │
   │              │    执行: LLM → 结构化 JSON               │
   │              │      {tool: "record_event",              │
   │              │       params: {title: "样板段放线完成",   │
   │              │               event_type: "施工节点",     │
   │              │               project_name: "默认项目"}}  │
   │              │    框架调用 handle_record_event() ──────────────────────────▶ events 表
   │              │    GuardianReview: 审核通过               │
   │              │    自验收: event_no EVT-20260623-0001 ✓  │
   │              │    after:  audit.sop_completed           │
   │              │         │                                │
   │              │         ▼                                │
   │              │  Node 4 [成果总结]                        │
   │              │    before: guardian.reply_review → PASS  │
   │              │    执行: 编排自然语言回复                  │
   │              │    "已记录事件: EVT-20260623-0001         │
   │              │     样板段放线完成，2026-06-23"           │
   │              │    after:  audit.result_final            │
   │              │    状态 → DONE                           │
   │              └─────────────┬───────────────────────────┘
   │                            │
   │                    通知 Session-Agent
   │                            │
   │              ┌─────────────┴───────────┐
   │              │ Session-Agent 审核回复    │
   │              │   before:reply_send      │
   │              │   GuardianReview → PASS  │
   │              └─────────────┬───────────┘
   │                            │
   │              ┌─────────────┴───────────┐
   │              │ 出站消息存档 (DB 写入)    │ ──────────────▶ DB
   │              └─────────────┬───────────┘
   │                            │
   │              ┌─────────────┴───────────┐
   │              │ OutboundSender.send()   │
   │              │ AstrBot → NapCat → QQ   │
   │              └─────────────────────────┘
   │                            │
   │  "已记录事件:               │
   │   EVT-20260623-0001        │
   │   样板段放线完成"            │
   │  ◀─────────────────────────┘
```

---

## 10. 配置与部署

### 10.1 Docker Compose 架构

```yaml
# docker-compose-napcat.yml (重构后)
services:
  napcat:           # QQ 协议桥
    image: mlikiowa/napcat-docker:latest
    ports: 6098-6099
    networks:
      - astrbot_network
    
  astrbot:          # 插件宿主 (仅运行薄通信层插件)
    image: soulter/astrbot:latest
    ports: 6185, 6199
    volumes:
      - ./data:/AstrBot/data   # Emily 插件代码挂载
    networks:
      - astrbot_network
    
  # ── 【新建】Emily 业务核心独立容器 ──
  emily-core:
    build:
      context: ./emily-core
      dockerfile: Dockerfile
    image: emily-core:latest
    container_name: emily-core
    restart: always
    ports:
      - "127.0.0.1:18080:18080"       # HTTP API (仅宿主机访问)
    environment:
      - EMILY_DATABASE_URL=postgresql://emily:emily_secret_2026@emily-postgres:5432/emily
      - EMILY_LLM_API_KEY=${EMILY_LLM_API_KEY}
      - EMILY_LLM_BASE_URL=https://api.deepseek.com
      - EMILY_LLM_MODEL=deepseek-chat
      - EMILY_MAXKB_URL=http://maxkb:8080
      - EMILY_MAXKB_ADMIN_PASSWORD=${EMILY_MAXKB_ADMIN_PASSWORD}
      - EMILY_MAXKB_KNOWLEDGE_ID=${EMILY_MAXKB_KNOWLEDGE_ID}
      - EMILY_STORAGE_ROOT=/data/storage
      - EMILY_ASTRBOT_WEBHOOK_URL=http://astrbot:6185/api/emy/outbound
      - EMILY_DATA_ROOT=/app/data              # 挂载卷根路径
    volumes:
      # ── 人类编辑 (只读) ──
      - ./emily-data/sops:/app/sops:ro
      - ./emily-data/prompts:/app/prompts:ro
      - ./emily-data/config:/app/config:ro
      - ./emily-data/baseknowledge:/app/baseknowledge:ro
      - ./emily-data/tools:/app/tools:ro
      # ── Core 写入, 人类查阅 ──
      - ./emily-data/notebooks:/app/notebooks
      - ./emily-data/logs:/app/logs
      - ./emily-data/attachments:/app/attachments
      - ./emily-data/user_memory:/app/user_memory
      - ./emily-data/journal:/app/journal
      # ── 数据库种子 ──
      - ./emily-data/db_seeds:/app/db_seeds:ro
      # ── 代码 (只读) ──
      - ./emily-core/emily_core:/app/emily_core:ro
      - ./emily-core/api:/app/api:ro
    networks:
      - astrbot_network
    depends_on:
      - emily-postgres
      - maxkb
    
  maxkb:            # RAG 知识库
    image: 1panel/maxkb:latest
    ports: 8080, 5433
    environment:
      - MAXKB_EMBEDDING_MODEL_PATH=/opt/maxkb-app/model/embedding
      - MAXKB_EMBEDDING_MODEL_NAME=/opt/maxkb-app/model/embedding/Qwen3-Embedding-0.6B
    volumes:
      - D:\app\pgdata:/var/lib/postgresql/data
      - D:\app\Qwen\Qwen3-Embedding-0___6B:/opt/maxkb-app/model/embedding/Qwen3-Embedding-0.6B:ro
      - D:\app\Emily\emily-data\baseknowledge:/opt/maxkb-app/data/emily:ro
    networks:
      - astrbot_network
    
  emily-postgres:  # 业务数据库 (两个容器共享)
    image: postgres:16-alpine
    ports:
      - "127.0.0.1:15432:5432"
    environment:
      - POSTGRES_USER=emily
      - POSTGRES_PASSWORD=emily_secret_2026
      - POSTGRES_DB=emily
    volumes:
      - D:\app\pgdata\emily:/var/lib/postgresql/data
    networks:
      - astrbot_network

networks:
  astrbot_network:
    driver: bridge
```

### 10.2 数据目录与 Docker 挂载全景

```
D:\app\Emily\emily-data/         # 【Core 容器全部可挂载数据的统一根目录】
│
├── baseknowledge/                 # 原始知识文档 (PDF/DOC/MD) — 供 MaxKB RAG 索引
├── tools/                         # 项目原子化工具定义/配置
├── sops/                          # SOP 业务流 Markdown 文件
├── prompts/                       # 提示词模板仓库
│   ├── master_agent.txt           #   MasterAgent system prompt
│   ├── 守护Agent.md               #   Guardian agent prompt
│   ├── domain_knowledge.md        #   L1 领域知识
│   └── flows/                     #   Mermaid 决策树
│       ├── main.md
│       └── unmatched.md
├── config/                        # 配置文件仓库
│   ├── core_config.json           #   Core 运行时配置
│   └── hook_config.json           #   Hook 声明式配置
├── notebooks/                     # 守护Agent 调查笔记本
├── logs/                          # Core 运行日志 (宿主机可 tail -f)
├── attachments/                   # IM 附件文件 (按日期子目录)
├── user_memory/                   # 用户长期记忆 (按 user_id 子目录)
├── journal/                       # 项目事件日志 (EventJournal)
└── db_seeds/                      # 数据库种子数据/迁移脚本
```

**挂载模式说明：**

| 目录 | 挂载模式 | 原因 |
|------|---------|------|
| `sops/` `prompts/` `config/` `baseknowledge/` `tools/` `db_seeds/` | `:ro` 只读 | 人类编辑，Core 只读取 |
| `notebooks/` `logs/` `attachments/` `user_memory/` `journal/` | `rw` 读写 | Core 写入，人类查阅 |
| `emily_core/` `api/` | `:ro` 只读 | 代码，开发阶段可热更新 |

### 10.3 emily-core 容器 Dockerfile

```dockerfile
# emily-core/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码 (运行时通过卷挂载)
COPY api/ ./api/
COPY emily_core/ ./emily_core/

# 数据目录 (运行时通过卷挂载)
RUN mkdir -p /app/sops /app/prompts /app/config /app/baseknowledge /app/tools \
             /app/notebooks /app/logs /app/attachments /app/user_memory \
             /app/journal /app/db_seeds

EXPOSE 18080
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "18080"]
```

### 10.4 关键配置项

| 配置组 | 字段 | 默认值 | 说明 |
|--------|------|--------|------|
| Core | `emycore_url` | `http://emily-core:18080` | Core 容器 API 地址 (**新**) |
| Core | `emycore_sse_url` | `http://emily-core:18080/api/v1/events/outbound` | SSE 出站推送地址 (**新**) |
| LLM | `llm_model` | `deepseek-chat` | 主推理模型 |
| LLM | `llm_temperature` | `0.1` | 低温度保证路由精度 |
| Agent | `agent_max_iterations` | 10 | Master Agent 最大 ReAct 轮数 |
| Agent | `agent_context_max_turns` | 10 | 会话上下文滑动窗口大小 |
| Agent | `agent_context_ttl_seconds` | 600 | 会话上下文过期时间 |
| Session | `session_ttl_seconds` | 600 | Session 无消息过期时间 (**新**) |
| Session | `session_max_concurrent` | 100 | 最大 Session 并发数 (**新**) |
| WorkItem | `workitem_max_per_session` | 5 | 每 Session 最大 WorkItem 数 (**新**) |
| Pipeline | `pipeline_mode` | `scheduler` | 管道模式 |
| Checkpoint | `checkpoint_ttl_seconds` | 300 | SOP 检查点过期时间 |
| RAG | `kb_top_k` | 5 | 向量检索返回条数 |
| RAG | `maxkb_search_mode` | `embedding` | embedding / keywords / blend |

### 10.5 技能系统 (Skills)

```
.claude/skills/emy-test/          # Emily 测试技能
├── SKILL.md                      # 技能定义
├── cli.py                        # 命令行测试接口
├── tester.py                     # 测试核心 (EmysTester)
├── daemon.py                     # 测试守护进程
├── config_loader.py              # 测试配置加载
├── emys_tester.py                # 快速 CLI 包装
└── emy_web/
    └── app.py                    # Web 测试控制台
```

---

## 11. 模块映射 — 蓝图概念 ↔ 现有实现

### 11.1 完整映射表

| session主线策略 概念 | 现有实现模块 | 文件 | 状态 | 说明 |
|---------------------|-------------|------|------|------|
| **Adapter 层** | | | | |
| 消息去重+格式转换 | `main.py` + `AstrBotInboundAdapter` | `main.py`, `adapters/astrbot/inbound_adapter.py` | ✅ 就绪 | |
| Session 池管理 | 尚无 | — | ❌ 新建 | 核心新建模块 |
| Session 创建/注销 | 尚无 | — | ❌ 新建 | 含知识灌注逻辑 |
| 入站原始归档 | `MessageRepository` | `repositories/message_repo.py` | ✅ 就绪 | |
| 出站消息发送 | `AstrBotOutboundSender` | `adapters/astrbot/outbound_sender.py` | ✅ 就绪 | |
| **Session 层** | | | | |
| Session-Agent | `MasterAgent` (部分) | `agent/master_agent.py` | ⚠️ 升级 | 需增加 多 WorkItem 编排、优先级调度、出站审核 |
| 意图识别+路由 | `SOPIntentRegistry` + `MasterAgent` ReAct | `agent/intent_registry.py`, `agent/master_agent.py` | ✅ 就绪 | M9 发现式路由 |
| 滑动窗口上下文 | `ConversationContext` | `agent/conversation_context.py` | ✅ 就绪 | TTL + 线程安全 |
| 用户长期记忆 | `UserMemoryService` | `services/user_memory_service.py` | ✅ 就绪 | 需增加 "历史对话摘要" 模块 |
| 项目状态摘要 | `EventJournal` + `QueryService` | `services/event_journal.py`, `services/query_service.py` | ⚠️ 扩展 | 需增加摘要生成器 |
| Session 状态机 | 尚无 | — | ❌ 新建 | 细节待后续按需补充 ||
| 焦点调度 | 尚无 | — | ❌ 新建 | |
| 待确认队列 | `CheckpointService` (部分) | `services/checkpoint_service.py` | ⚠️ 扩展 | 需增加优先级队列 |
| 附件管理 | `FileStorageService` | `services/file_storage_service.py` | ✅ 就绪 | M13 |
| 出站存档 | `ChatArchiveService` | `services/chat_archive_service.py` | ✅ 就绪 | M11 |
| Session 注销归档 SOP | 尚无 | — | ❌ 新建 | SOP-010-SYS-session_archive |
| **WorkItem 层** | | | | |
| WorkItem-Agent (全局单例) | `BusinessFlowAgent` (部分) | `agent/business_flow_agent.py` | ⚠️ 升级 | 需改为全局单例 + 增量灌注 |
| Pipeline BUS (公共总线) | `PipelineScheduler` (8 阶段) | `pipeline/pipeline_scheduler.py` | ⚠️ 重构 | 8 阶段 → 4 节点公共总线，Session-Scheduler 调度 |
| Session-Scheduler | 尚无 | — | ❌ 新建 | WI 分配/排队/优先级 |
| KnowledgeInjector | 尚无 | — | ❌ 新建 | 增量灌注引擎 |
| 结构化工具执行 | `BusinessFlowToolRegistry` | `tools/business_flow_tools.py` | ✅ 就绪 | M14 |
| WorkItem 状态机 | 尚无 | — | ❌ 新建 | |
| 鉴权注入 | `pipeline_config_m15.json` | `data/config/pipeline_config_m15.json` | ⚠️ 适配 | 声明式模式可用，挂载点需调整 |
| **基础设施** | | | | |
| 数据库 | PostgreSQL 16 + 22 表 ORM | `infrastructure/database/` | ✅ 就绪 | |
| LLM Client | `LLMClient` | `infrastructure/llm/client.py` | ✅ 就绪 | |
| RAG 检索 | `MaxKBRagProvider` + `LocalFileRagProvider` | `providers/rag/` | ✅ 就绪 | |
| 工具注册表 | `ToolRegistry` + `BusinessFlowToolRegistry` | `tools/` | ✅ 就绪 | |
| 领域知识 | `domain_knowledge.md` (L1) + RAG (L2) | `prompts/domain_knowledge.md` | ✅ 就绪 | M10 |
| 守护审计 | `GuardianAgent` + `GuardianReview` | `agent/guardian_agent.py`, `agent/guardian_review.py` | ✅ 就绪 | |

### 11.2 Core 容器化模块归属

**停留在 AstrBot 插件内的模块（薄通信层）：**

| 模块 | 位置 | 说明 |
|------|------|------|
| `adapters/astrbot/` — AstrBot 适配器 | 插件内 | InboundAdapter + OutboundSender，含 AstrBot API 调用 |
| `main.py` — 插件入口 (薄通信层) | 插件内 | 去重 → 转换 → HTTP 转发 → SSE 监听 |
| EmilyApiClient + SSEListener | 插件内 (新建) | HTTP 客户端和 SSE 监听器 |

**迁移到 Core 容器内的模块（原已设计为无 `astrbot` 依赖，物理迁移即可）：**

| 模块 | 原因 |
|------|------|
| `emily_core/` — 全部业务逻辑 | 已无 `astrbot` import，直接移动 |
| `adapters/standard/` — 标准协议对象 | Core 容器内保留副本，用于请求反序列化 |
| `SOPrepository/` | Core 自己管理，不再从插件目录读取 |
| `prompts/` | 同上 |
| `infrastructure/` — 全部基础设施 | DB/LLM/RAG，连接串从环境变量读取 |
| `repositories/` — 全部 10 个仓库 | 数据访问层不改 |
| `services/` — 全部 15 个服务 | 业务逻辑层不改 |
| `tools/` — 全部工具 handler | 基座工具不改，新增 Session 层工具 |
| `providers/` — RAG 提供者 | 不改 |
| `config.py` + `bootstrap.py` | 新增 Session + API 相关字段即可 |

---

## 12. 演化路线 — Mock → 真实实现

### 12.1 当前状态 (M15 Phase 0)

```
当前路径: main.py → EmilyCore → MessageApplication → PipelineScheduler (8 阶段)
                                    ↑
                             全 Mock 阶段 2-7
                             (MockRouter / MockAuthEngine / MockPlanner / MockWorkAgent / MockGuardian)
                             
Core 运行位置: AstrBot 插件内 (Python 包导入，与 AstrBot 共享进程)
当前不存在: Session 池 / Session-Agent / WorkItem-Agent (单例) / 优先级调度 / 独立容器
```

### 12.2 演化 4 阶段

#### Phase 0: Core 容器化剥离 (当前 → Core 独立运行)  【最高优先】

```
目标: 将 emily_core 从 AstrBot 插件中物理剥离为独立 Docker 容器

  ├── Phase 0.1: 创建 API 层
  │     ├── 新建 api/server.py (FastAPI 应用入口)
  │     ├── POST /api/v1/message/send — 消息发送端点
  │     ├── GET  /api/v1/events/outbound — SSE 出站推送端点
  │     └── GET  /api/v1/health — 健康检查
  │
  ├── Phase 0.2: 插件端退化为薄通信层
  │     ├── 新建 EmilyApiClient (HTTP 客户端)
  │     ├── 新建 SSEListener (出站消息监听器)
  │     ├── main.py 简化: 去重 → 转换 → HTTP POST → SSE 监听
  │     └── 删除 main.py 中 LLM/RAG 初始化代码（转移到 Core）
  │
  ├── Phase 0.3: 创建 Core 容器
  │     ├── 编写 emily-core/Dockerfile
  │     ├── 编写 requirements.txt
  │     ├── docker-compose-napcat.yml 新增 emily-core 服务
  │     └── 配置网络: astrbot_network — 内网通信
  │
  ├── Phase 0.4: 配置迁移
  │     ├── LLM/RAG/DB 配置从 AstrBot 插件配置 → Core 环境变量
  │     ├── Core 通过内网直连 emily-postgres (不再通过宿主机端口转发)
  │     └── Core 通过内网连接 maxkb:8080
  │
  └── Phase 0.5: 验证
        ├── Core 容器正常启动，健康检查通过
        ├── 插件 → Core HTTP 通信正常
        ├── Core → 插件 SSE 推送正常
        ├── 消息端到端: QQ → NapCat → AstrBot → 插件 → Core → Agent → 插件 → IM
        └── 现有验收测试全部通过

不变:
  │  现有 8 阶段 PipelineScheduler 保持运行
  │  Mock 组件保持不变
  │  数据库 / 工具 / 服务层不改
  │  所有业务逻辑代码不修改（仅移动 + 新增 HTTP/SSE 通信层）
```

#### Phase A: Session 池 + Pipeline 公共总线 (容器独立 → 骨架就绪)

```
目标:
  ├── 新建 SessionPoolManager + SessionFactory
  ├── 新建 Session 状态机 (基础骨架，细节待后续完善)
  ├── 新建 SessionContext (最小化灌注数据类)
  ├── 新建 Session-Scheduler (WI 分配/排队/优先级)
  ├── PipelineScheduler 重构为公共 Pipeline BUS (4 节点)
  │     所有 WorkItem 共享此总线，非 WI 私有
  └── Adapter 层接管入站路由

不变:
  │  Mock 组件保持不变
  │  数据库 / 工具 / 服务层不改
```

#### Phase B: WorkItem-Agent 单例化 + 增量灌注 (骨架就绪 → 执行层完整)

```
目标:
  ├── BusinessFlowAgent → WorkItem-Agent 全局单例化
  │     ├── 不再为每 WI 创建新 Agent
  │     ├── KnowledgeInjector 增量灌注缺失的工具/SOP/schema
  │     └── 上下文回收机制（WI 完成后释放独占知识）
  ├── MasterAgent → SessionAgent 升级
  │     ├── 增加多 WorkItem 编排
  │     ├── 增加出站审核
  │     └── 增加优先级调度
  ├── 新建 WorkItem 状态机
  ├── 新建 FocusLock + ConfirmQueue
  ├── UserMemoryService 增加 "历史对话摘要" 模块
  └── 新建 Session 注销归档 SOP (SOP-010)

替换:
  ├── MockRouter → Session-Agent 意图识别
  ├── MockAuthEngine → Hook 鉴权 (perm_list)
  └── MockPlanner  → WorkItem-Agent 自主规划

保持:
  │  MockWorkAgent / MockGuardian 仍可用

保持:
  │  MockWorkAgent / MockGuardian 仍可用
```

#### Phase C: 全真实实现 + 集成验证 (执行层完整 → 全面就绪)

```
目标:
  ├── MockWorkAgent → WorkItem-Agent 全局单例真实执行
  ├── MockGuardian → GuardianAgent + GuardianReview 真实审核
  ├── Hook 配置从 pipeline_config_m15.json 迁移到新 hook_config.json
  ├── KnowledgeInjector 增量灌注全链路验证
  ├── 公共 Pipeline BUS 多 WI 并发测试
  └── 全链路集成测试

完成标志:
  ├── 所有 Mock 组件被真实实现替换
  ├── 公共 Pipeline BUS 4 节点正常运行
  ├── WorkItem-Agent 增量灌注正确（不重复加载，不遗漏）
  ├── Hook 三态决策全线覆盖
  ├── Session 完整生命周期 (创建→执行→归档→销毁)
  └── 多 WI 异步处理无上下文交叉污染
```

### 12.3 演化期间的兼容性保证

```
每个 Phase 期间:
  ├── 新代码与旧代码共存 (Feature Flag 控制切换)
  ├── Phase 0 期间: 插件内同时支持 "container" 和 "embedded" 两种 Core 运行模式
  │     通过 EMILY_CORE_MODE 环境变量切换: "container" / "embedded"
  ├── pipeline_mode 配置控制: "scheduler" (旧) / "session" (新)
  ├── 数据库 schema 只增不改 (新表添加, 不影响旧查询)
  └── 现有验收测试全部保持通过
```

### 12.4 演化里程碑总览

```
  ┌─────────┐    ┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
  │  当前     │    │  Phase 0    │    │   Phase A     │    │   Phase B     │    │   Phase C     │
  │ M15      │───▶│ 容器剥离     │───▶│ Pipeline 公共 │───▶│ WI-Agent 单例│───▶│ 全真实实现    │
  │ Mock 总线 │    │ Core 独立    │    │ Session 池    │    │ + 增量灌注    │    │ 集成验证      │
  └─────────┘    └─────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
  
  关键交付:       Docker 容器         SessionPoolMgr     WorkItem-Agent 全局单例  全 Hook 覆盖
                  HTTP API            Session 状态机      KnowledgeInjector     Mock→真实全替换
                  SSE 出站推送        公共 Pipeline BUS   增量灌注引擎            WI 上下文回收
                                      Session-Scheduler  SOP-010 归档           多 WI 并发验证
    
  不改动:         全部业务逻辑         全部工具/服务       全部仓库/基础设施        —
                  全部基础设施         全部数据库/LLM      Mock 部分可用
```

---

## 附录

### A. 关键文件索引

| 文件 | 角色 |
|------|------|
| `CLAUDE.md` | AI 辅助开发指令 |
| `README.md` | 项目定位/架构/数据流 |
| `session主线策略.md` | Session 主线架构蓝图 |
| `Emily_主系统架构.md` | 本文档 — 完整架构参考 |
| `tem_log/开发记录.md` | 里程碑进度/ADR/操作速查 |
| `nodebook.md` | 开发笔记本 |

### B. 术语表

| 术语 | 英文 | 说明 |
|------|------|------|
| 适配器 | Adapter | IM 平台 ↔ Emily 格式转换层 |
| 会话 | Session | 一个用户在一个对话中的完整生命周期 |
| 工作项 | WorkItem | 一个独立任务的执行单元 |
| 管道 | Pipeline | WorkItem 内部的任务执行总线 |
| 钩子 | Hook | 横切关注点的挂载点 |
| 灌注 | Inject | Agent 初始化时注入知识/权限/上下文 |
| 短路 | Short-circuit | 无需创建 WorkItem 的直接回复 |
| 焦点调度 | Focus Lock | 优先级调度器：用户关心的主题优先交互，不强制单焦点阻塞 |
| 守护 | Guardian | 审核/审计 Agent |
| SOP | Standard Operating Procedure | 标准业务流文件 |

### C. 开发约束速查

1. **EmilyCore 独立**：`emily_core/` 不 import `astrbot.*`
2. **注册表模式**：新增意图只改 `__init__.py` 注册，不改 `message_app.py` 的 `_dispatch()`
3. **Prompt 独立文件**：LLM Prompt 放 `prompts/` 目录
4. **分层不跳**：Adapter → Session → WorkItem → Infrastructure
5. **Hook 声明式注册**：横切关注点通过 JSON 配置
6. **M14 业务流工具**：核心写操作走 `BusinessFlowToolRegistry`（框架直接执行，不走 LLM function calling）
7. **提交规范**：`M<n>: <变更摘要>`
