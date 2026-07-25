# monitor 模块需求文档 V2

> 基于 [monitor_审核_V1.md](monitor_审核_V1.md) 审核意见修订。原文档仅 13 行，V2 补齐背景、功能规格、技术方案、部署架构等内容。

---

## 1. 背景与目标

Emily 部署在项目本地电脑，接入局域网供项目团队通过 QQ 与 Emily 交互。当前运维人员只能通过 `docker logs` + `psql` 命令行手段观察系统状态，缺乏直观的运行状态视图。尤其是 QQ/NapCat 掉线需重新扫码是实际频发问题，需要快速发现。

**目标**：提供一个只读运维看板 Web 页面，让管理员通过浏览器即可直观了解 Emily 系统运行状态、当前会话池、全景节点、管控文件和人员信息。

**定位**：只读运维看板，不是通用监控系统（不含告警、指标采集、日志聚合）。

---

## 2. 目标用户与使用场景

| 项 | 说明 |
|----|------|
| **用户** | 项目运维管理员（1-5 人） |
| **设备** | 局域网内电脑浏览器（手机暂不适配） |
| **场景** | 日常巡检"Emily 是否正常"；QQ 掉线快速发现；排查对话卡住时查看 Session 池；查看全景节点/文件/人员状态 |
| **访问方式** | 浏览器访问 `http://{宿主机IP}:18081` |

---

## 3. 架构方案

**方案 A：emily-core 内嵌监控路由**

- 在 emily-core FastAPI 中新增 `/api/v1/monitor/*` 路由组，同时托管前端静态文件
- 新增监控专用端口 18081（局域网可访问），原有 18080 端口（仅宿主机）不变
- Session 池状态可直接从内存获取，DB 数据通过 ORM 层查询

```
局域网浏览器 → :18081 → emily-core FastAPI
                            ├── /                    → 静态页面（index.html）
                            ├── /api/v1/monitor/*    → 监控 API
                            └── :18080               → 原有业务 API（不变）
```

**docker-compose 端口映射变更**：

```yaml
emily-core:
  ports:
    - "127.0.0.1:18080:18080"   # 原有 API（仅宿主机，不变）
    - "0.0.0.0:18081:18081"     # 新增监控端口（局域网可访问）
```

---

## 4. 功能需求

### 4.1 核心状态区（始终可见）

展示 5 个容器运行状态 + QQ/NapCat 外链入口。

| 监控项 | 数据源 | 展示内容 | 交互 |
|--------|--------|---------|------|
| napcat 容器 | Docker Engine API `/containers/json` | 容器名 + 状态（运行中● / 已停止○） | — |
| astrbot 容器 | 同上 | 同上 | — |
| emily-core 容器 | 同上 | 同上 | — |
| maxkb 容器 | 同上 | 同上 | — |
| emily-postgres 容器 | 同上 | 同上 | — |
| QQ 账号状态 | NapCat WebUI 外链 | 显示"NapCat WebUI"文字 | 点击跳转 `http://{host}:6099`，管理员可扫码登录 |
| 微信/钉钉/飞书 | 预留接口位 | "无账号、无连接" | 无交互 |

**Docker API 访问方式**：emily-core 容器内通过 Docker Unix Socket（`/var/run/docker.sock`）访问，需在 docker-compose 中挂载：

```yaml
emily-core:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
```

### 4.2 Session 池面板

| 监控项 | 数据源 | 展示内容 | 交互 |
|--------|--------|---------|------|
| Session 池摘要 | `SessionPoolManager` 内存对象 | 活跃 Session 数量、池运行时长 | — |
| Session 列表 | 同上，遍历 `_sessions` 字典 | 会话ID、IM 平台、会话类型、最后活跃时间 | 点击展开最近 5 条消息摘要 |
| 消息摘要 | `messages` 表，按 `conversation_id` 查最近 5 条 | 发送者、方向、内容摘要（截断 80 字）、时间 | — |

**需新增 API**：

- `GET /api/v1/monitor/sessions` — 返回活跃 Session 列表 + 池摘要
- `GET /api/v1/monitor/sessions/{conversation_id}/messages` — 返回指定会话最近 5 条消息

**API 数据获取路径**：

- Session 列表：API → Core → `SessionPoolManager._sessions`（内存对象，直接遍历 `_Entry` 列表）
- 消息摘要：API → Core → Service → Repository → `messages` 表

**Session 池对外暴露字段**（API 返回）：

| 字段 | 来源 | 说明 |
|------|------|------|
| `conversation_id` | `message.conversation_id` | 会话标识 |
| `im_platform` | `SessionAgent` 中获取 | qq / wechat / ... |
| `conversation_type` | `SessionAgent` 中获取 | private / group |
| `last_active` | `_Entry.last_active` | 最后活跃时间戳 |
| `uptime` | 计算 `now - created` | 会话存活时长 |

### 4.3 全景节点面板

| 监控项 | 数据源 | 展示内容 | 交互 |
|--------|--------|---------|------|
| 节点列表 | `project_nodes` 表 | 业务字段（见下方白名单） | 支持按 `project_id` 筛选；点击行展开完整业务字段 |
| 节点详情 | 同上 | 全部业务字段 | — |

**业务字段白名单**（14 个字段，含 `progress` 和 `status`）：

| 字段 | 说明 | 是否列表展示 |
|------|------|------------|
| `project_id` | 项目归属 | 是 |
| `node_id` | 节点编号 | 是 |
| `node_name` | 节点名称 | 是 |
| `owner_dept_id` | 主责条线 | 是 |
| `related_company_id` | 关联单位 | 否（详情） |
| `deadline` | 截止时间 | 是 |
| `land_parcel_id` | 关联地块 | 否（详情） |
| `remark` | 备注 | 否（详情） |
| `parent_node_id` | 父节点 | 否（详情） |
| `stage_id` | 所属阶段 | 是 |
| `child_weight` | 子节点权重 | 否（详情） |
| `startup_doc_id` | 启动文档 | 否（详情） |
| `progress` | 整体进度 | 是 |
| `status` | 当前状态 | 是 |

**系统字段不展示**：`creator_id`, `created_at`, `approver_id`, `approved_at`, `completed_at`, `is_discarded`, `sort_order`, `responsible_user_id`, `updated_at`, `node_type`, `visibility_mode`

**需新增 API**：

- `GET /api/v1/monitor/nodes` — 返回节点列表（支持 `?project_id=` 筛选，默认排除 `is_discarded=true`）
- `GET /api/v1/monitor/nodes/{node_id}` — 返回单个节点完整业务字段

**API 数据获取路径**：API → Core → Service → Repository → `project_nodes` 表

### 4.4 管控文件面板

| 监控项 | 数据源 | 展示内容 | 交互 |
|--------|--------|---------|------|
| 文件列表 | `files` 表 | 文件名、文件类型、版本、上传者、上传时间、保密级别 | 支持按 `project_id` 筛选 |

**展示字段**：

| 字段 | 说明 |
|------|------|
| `file_no` | 文件编号 |
| `filename` | 文件名 |
| `file_type` | 文件类型 |
| `version` | 版本号 |
| `uploaded_by` → `users.username` | 上传者姓名（关联查询） |
| `created_at` | 上传时间 |
| `confidentiality` | 保密级别（0 公开 / 1 内部 / 2 机密 / 3 绝密） |
| `is_latest` | 是否最新版本 |

**需新增 API**：

- `GET /api/v1/monitor/files` — 返回文件列表（支持 `?project_id=` 筛选，默认排除 `is_deleted=true`，仅返回 `is_latest=true`）

**API 数据获取路径**：API → Core → Service → Repository → `files` 表 + `users` 表关联

### 4.5 人员列表面板

| 监控项 | 数据源 | 展示内容 | 交互 |
|--------|--------|---------|------|
| 人员列表 | `users` 表 | ID、姓名、所属企业、人员等级 | — |

**展示字段**：

| 字段 | 说明 | 映射 |
|------|------|------|
| `id` | 用户 UUID | 直接取 |
| `username` | 姓名 | 直接取 |
| `company` | 所属企业 | JSON 数组，存的是 `company_info.id`，需关联 `company_info` 表取 `name` 拼接；若 `company_info` 无匹配则原样展示 ID |
| `permission_level` | 人员等级 | 直接取（0 访客 / 1 普通用户 / ... / 4 系统管理员） |

**需新增 API**：

- `GET /api/v1/monitor/users` — 返回人员列表（默认排除 `is_deleted=true` 和 `status != 'active'`）

**API 数据获取路径**：API → Core → Service → Repository → `users` 表 + `company_info` 表关联

---

## 5. 刷新策略

**当前版本：静态页面 + 手动刷新**

- 页面加载时拉取全部数据
- 每个面板提供"刷新"按钮，点击后重新请求 API
- 无自动刷新、无 SSE 推送、无 WebSocket
- 后续版本可按需升级为 SSE 自动推送

---

## 6. 页面布局

```
┌──────────────────────────────────────────────────┐
│  Emily 运维看板                      [全部刷新]   │
├──────────────────────────────────────────────────┤
│  ⬤ 核心状态                                      │
│  napcat: ● 运行中   astrbot: ● 运行中             │
│  emily-core: ● 运行中  maxkb: ● 运行中            │
│  postgres: ● 运行中                               │
│  QQ: [NapCat WebUI →]  微信: 无账号  钉钉: 无账号 │
│  飞书: 无账号                                     │
├──────────────────────────────────────────────────┤
│  [Session池]  [全景节点]  [文件]  [人员]           │
├──────────────────────────────────────────────────┤
│  (Tab 内容区域)                                   │
│                                                   │
│  Session池: 3 个活跃会话                          │
│  ┌──────────────────────────────────────────────┐ │
│  │ 会话ID | 平台 | 类型 | 最后活跃 | [详情]      │ │
│  │ ...    | QQ   | 群聊 | 2分钟前  | [展开]      │ │
│  └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

- 核心状态区始终可见（5 容器状态 + IM 账号状态 + NapCat 外链）
- 下方 Tab 切换四个数据面板（Session池 / 全景节点 / 文件 / 人员）
- 每个面板有独立"刷新"按钮
- 全景节点和文件面板支持按 `project_id` 下拉筛选
- Session 详情展开最近 5 条消息摘要
- 节点详情展开完整业务字段

---

## 7. 非功能需求

| 项 | 要求 |
|----|------|
| 并发用户 | 1-5 人 |
| 页面加载时间 | < 3 秒（局域网） |
| 数据量级 | 节点 100-500 条，文件 100-1000 条，人员 50-200 条，Session 池 < 20 个 |
| 浏览器 | Chrome / Edge 最新版 |
| 移动端 | 暂不适配 |

---

## 8. 安全策略（当前版本暂不实施）

以下策略记录在案，后续版本按需启用：

- HTTP Basic Auth（18081 端口全局认证）
- 文件列表按保密级别过滤（`confidentiality >= 2` 标记 `[已隐藏]`）
- 访问日志记录
- 页面标注"管理员专用"

**当前版本安全边界**：能接入项目局域网的设备即可访问监控页面。18081 端口仅暴露监控路由，不暴露业务 API。

---

## 9. 实施约束

| # | 约束 | 说明 |
|---|------|------|
| 1 | **分层合规** | 监控 API 走 API → Core → Service → Repo → DB 分层，Session 池数据从 Adapter 层 `SessionPoolManager` 获取 |
| 2 | **不新建 DB 表** | 全部查询现有表，无 schema 变更 |
| 3 | **监控端口隔离** | 18081 仅挂载 `/api/v1/monitor/*` + 静态文件，不挂载业务路由 |
| 4 | **只读** | 监控页面无任何写入操作 |
| 5 | **静态前端** | 纯 HTML + CSS + JavaScript，无需 Node.js 构建 |

---

## 10. 分阶段计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| **Phase 1** | 核心状态区（容器状态 + QQ/NapCat 外链 + IM 占位）+ Session 池面板 | 待实施 |
| **Phase 2** | 全景节点面板 + 文件面板 + 人员面板 | 待实施 |
| **Phase 3** | 安全策略（Basic Auth + 数据过滤 + 访问日志） | 待实施 |
| **Phase 4** | 信息流（会话消息流、IM 后台信息流） | 待实施 |

---

## 11. 需新增 API 清单

| API | 方法 | 说明 | 数据源 |
|-----|------|------|--------|
| `/api/v1/monitor/containers` | GET | 5 容器运行状态 | Docker Engine API |
| `/api/v1/monitor/sessions` | GET | 活跃 Session 列表 + 池摘要 | `SessionPoolManager` 内存 |
| `/api/v1/monitor/sessions/{conversation_id}/messages` | GET | 指定会话最近 5 条消息 | `messages` 表 |
| `/api/v1/monitor/nodes` | GET | 全景节点列表（支持 `?project_id=`） | `project_nodes` 表 |
| `/api/v1/monitor/nodes/{node_id}` | GET | 单节点完整业务字段 | `project_nodes` 表 |
| `/api/v1/monitor/files` | GET | 管控文件列表（支持 `?project_id=`） | `files` 表 |
| `/api/v1/monitor/users` | GET | 人员列表 | `users` + `company_info` 表 |
| `/` | GET | 静态页面 `index.html` | 静态文件 |

---

## 12. 需新增/修改的代码区域

| 区域 | 变更 |
|------|------|
| `emily-core/api/routes/` | 新增 `monitor.py`（监控 API 路由） |
| `emily-core/api/server.py` | 新增 monitor 路由注册 + 端口区分逻辑 |
| `emily-core/emily_core/services/` | 新增 `monitor_service.py`（监控数据查询 Service） |
| `emily-core/emily_core/adapters/session/session_pool.py` | 新增 `get_status()` 方法（暴露池状态） |
| `docker-compose-napcat.yml` | 新增 18081 端口映射 + docker.sock 挂载 |
| `emily-core/static/` | 新增 `index.html` + CSS + JS（前端看板页面） |

---

*本文档基于 `monitor/monitor.md` 原始需求 + `monitor/monitor_审核_V1.md` 审核意见修订。审核中提出的 IM 状态降级、端口隔离、安全策略暂缓、信息流暂缓等决策均已纳入。*
