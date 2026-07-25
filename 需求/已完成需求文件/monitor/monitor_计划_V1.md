# monitor 模块 — AI 执行计划

> **基于需求**：[monitor_需求_V2.md](monitor_需求_V2.md)
> **计划版本**：v1.0
> **目标**：在 emily-core 内嵌只读运维看板，通过 18081 端口暴露监控 API + 静态页面，覆盖容器状态、Session 池、全景节点、管控文件、人员列表 5 大面板

---

## 你的角色

你作为 **Emily开发者资深架构师** + **实施计划编制专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：除非计划明确标注"修改方法签名"，否则只能在已有类中新增方法，不修改现有方法
2. **分层不可跳**：监控 API 走 API → Core → Service → Repo → DB 分层。Session 池数据从 Adapter 层 `SessionPoolManager` 获取，DB 数据走 Service → Repo
3. **Sync repo + asyncio.to_thread**：Repository 全 sync，async Service 用 `asyncio.to_thread()` 包裹——与项目现有模式一致
4. **不 import AstrBot**：emily-core 不依赖任何 `astrbot.*` 包
5. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
6. **参照模式**：所有新代码必须参照下方"代码模式参照表"中的源文件。风格不一致视为失败
7. **不新建 DB 表**：全部查询现有表，无 schema 变更
8. **18081 仅挂载监控路由**：监控端口不挂载业务路由（message/session/permission 等）

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `SessionPoolManager` | `emily-core/emily_core/adapters/session/session_pool.py` | `size`, `uptime_seconds`, `_sessions` | 新增 `get_status()` 方法暴露池状态 |
| `ProjectNodeRepo` | `emily-core/emily_core/repositories/node_repo.py` | `get_by_id()`, `list_by_project()` | 参照 Repo 模式，MonitorRepo 风格一致 |
| `FileRepository` | `emily-core/emily_core/repositories/file_repo.py` | `get_by_id()`, `get_by_file_no()` | 参照 Repo 模式 |
| `UserRepository` | `emily-core/emily_core/repositories/user_repo.py` | `get_by_im()` | 参照 Repo 模式 |
| `get_session()` | `emily-core/emily_core/infrastructure/database/session.py` | context manager | MonitorRepo 复用同一 DB session 工厂 |
| `EmilyCore` | `emily-core/emily_core/__init__.py` | `_session_pool`, `health()`, `_ensure_initialized()` | 监控 Service 通过 `get_core()` 访问池状态 |
| `AuthMiddleware` | `emily-core/api/middleware/auth.py` | `dispatch()` | 修改以放行 `/api/v1/monitor/*` 路由 |
| `aiohttp` | `requirements.txt` 已有 | `UnixConnector` | 用于 Docker Unix Socket 请求，零新增依赖 |

### 架构决策

选择方案 A（emily-core 内嵌监控路由）而非独立容器，原因：Session 池是内存对象，跨容器需额外 API；当前规模 1-5 人访问，单容器足够。

双端口方案（18080 业务 + 18081 监控）而非单端口绑定 0.0.0.0，原因：安全隔离——业务 API（消息发送等）不应暴露到局域网。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| Repository | `emily-core/emily_core/repositories/node_repo.py` | `@staticmethod` + `with get_session()` + sync |
| Service | `emily-core/emily_core/services/node_service.py` | `async def` + `asyncio.to_thread()` 包裹 sync repo |
| API 路由 | `emily-core/api/routes/node.py` | `APIRouter` + lazy `_get_service()` + `set_xxx_service()` 注入 + `HTTPException(503)` |
| Pydantic Schema | `emily-core/api/routes/node_schemas.py` | `BaseModel` + `Field(...)` + `ApiResponse` 包装 |
| 中间件 | `emily-core/api/middleware/auth.py` | `BaseHTTPMiddleware` + `dispatch()` |
| Dockerfile | `emily-core/Dockerfile` | slim 镜像 + COPY + EXPOSE + CMD |
| 入口启动 | `emily-core/api/server.py` | `FastAPI` + `lifespan` + `get_core()` |

---

## 模块依赖图

```
M1(数据层: SessionPool.get_status + MonitorRepo + DockerClient)
  │
  ├──→ M2(API层: MonitorService + Schemas + Routes + MonitorApp + DualServer)
  │       │
  │       └──→ M3(部署集成: EmilyCore注入 + Dockerfile + DockerCompose + AuthMW)
  │               │
  │               └──→ M4(前端: HTML + CSS + JS)
  │
  └───────────────────────────────────────────────────────────────────
      M1 无外部依赖，M2 依赖 M1，M3 依赖 M2，M4 依赖 M3
```

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心类/函数 |
|------|----------|----------|------------|
| M1 | `emily-core/emily_core/adapters/session/session_pool.py` | 修改 | 新增 `get_status()` |
| M1 | `emily-core/emily_core/repositories/monitor_repo.py` | 新增 | `MonitorRepository` |
| M1 | `emily-core/emily_core/infrastructure/docker/client.py` | 新增 | `get_container_status()` |
| M2 | `emily-core/emily_core/services/monitor_service.py` | 新增 | `MonitorService` |
| M2 | `emily-core/api/routes/monitor_schemas.py` | 新增 | Pydantic 响应模型 |
| M2 | `emily-core/api/routes/monitor.py` | 新增 | `router` |
| M2 | `emily-core/api/monitor_app.py` | 新增 | `app`（18081 FastAPI） |
| M2 | `emily-core/api/run.py` | 新增 | `main()`（双服务器启动） |
| M3 | `emily-core/emily_core/__init__.py` | 修改 | 新增 `_monitor_service` |
| M3 | `emily-core/api/middleware/auth.py` | 修改 | 放行 `/monitor/` 路由 |
| M3 | `emily-core/Dockerfile` | 修改 | 新 CMD + EXPOSE 18081 + static 目录 |
| M3 | `docker-compose-napcat.yml` | 修改 | 端口映射 + docker.sock |
| M4 | `emily-core/static/index.html` | 新增 | 看板主页面 |
| M4 | `emily-core/static/style.css` | 新增 | 样式 |
| M4 | `emily-core/static/app.js` | 新增 | 前端逻辑 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/adapters/session/session_pool.py` | 扩展 | 在 `SessionPoolManager` 类中新增 `get_status()` 方法 |
| `emily-core/emily_core/__init__.py` | 扩展 | 在 `EmilyCore.__init__` 中新增 `_monitor_service = None`；在 `_ensure_initialized` 中初始化 MonitorService |
| `emily-core/api/middleware/auth.py` | 修改 | `dispatch()` 方法中放行 `/monitor/` 前缀路由 |
| `emily-core/Dockerfile` | 修改 | CMD 改为 `api.run:main`；新增 `EXPOSE 18081`；新增 `static/` 目录 |
| `docker-compose-napcat.yml` | 修改 | emily-core 新增 18081 端口映射 + docker.sock 卷挂载 |
| `emily-core/api/server.py` | 不变 | — |

---

## 脚本结构约定

> 本模块为 Web 看板功能，不涉及数据处理/业务流程，不适用"独立脚本+聚合薄壳"模式。所有功能以 API + 静态页面形式内嵌于 emily-core 容器。

---

## M1: 数据层（SessionPool 状态 + MonitorRepo + Docker 客户端）

**依赖**：无（本模块为首建模块）

**职责**：提供监控所需的全部底层数据访问能力——内存中的 Session 池状态、DB 中的节点/文件/人员/消息查询、Docker 容器运行状态。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | SessionPool 状态暴露 | `emily-core/emily_core/adapters/session/session_pool.py`（修改） |
| 2 | 监控数据 Repository | `emily-core/emily_core/repositories/monitor_repo.py`（新建） |
| 3 | Docker 容器状态客户端 | `emily-core/emily_core/infrastructure/docker/client.py`（新建） |

### 代码

#### `emily-core/emily_core/adapters/session/session_pool.py` — 在 `SessionPoolManager` 类的 `uptime_seconds` 属性后追加

```python
# emily-core/emily_core/adapters/session/session_pool.py

    # ── 监控 API 支持 ──

    def get_status(self) -> dict:
        """返回 Session 池状态摘要（供监控 API 调用）。

        Returns:
            {
                "total": int,           # 活跃 Session 数
                "uptime_seconds": int,  # 池运行时长
                "sessions": [           # 各 Session 摘要
                    {
                        "conversation_id": str,
                        "last_active_ts": float,   # Unix 时间戳
                        "idle_seconds": int,        # 空闲秒数
                    },
                    ...
                ]
            }
        """
        now = time.time()
        sessions = []
        for conv_id, entry in self._sessions.items():
            sessions.append({
                "conversation_id": conv_id,
                "last_active_ts": entry.last_active,
                "idle_seconds": int(now - entry.last_active),
            })
        return {
            "total": len(self._sessions),
            "uptime_seconds": self.uptime_seconds,
            "sessions": sessions,
        }
```

#### `emily-core/emily_core/repositories/monitor_repo.py` — 新建

```python
# emily-core/emily_core/repositories/monitor_repo.py

"""监控数据 Repository —— 只读查询（节点/文件/人员/消息）。

参照模式：repositories/node_repo.py（@staticmethod + with get_session() + sync）。
所有方法为 @staticmethod + sync，Service 层用 asyncio.to_thread() 包裹。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    ProjectNode,
    File,
    User,
    CompanyInfo,
    Message,
    Conversation,
)

logger = logging.getLogger("emily.repo.monitor")


# ══════════════════════════════════════════════════════════════════════════════
# 全景节点业务字段白名单（需求 V2 §4.3）
# ══════════════════════════════════════════════════════════════════════════════

NODE_BUSINESS_FIELDS = [
    "project_id", "node_id", "node_name", "owner_dept_id",
    "related_company_id", "deadline", "land_parcel_id", "remark",
    "parent_node_id", "stage_id", "child_weight", "startup_doc_id",
    "progress", "status",
]


class MonitorRepository:
    """监控只读查询。"""

    # ── 全景节点 ──

    @staticmethod
    def list_nodes(
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询全景节点列表（仅业务字段，排除 is_discarded）。

        Args:
            project_id: 按项目筛选（为空则全部）
            limit: 分页大小
            offset: 偏移量

        Returns:
            字典列表，每个字典仅包含 NODE_BUSINESS_FIELDS 中的字段。
        """
        with get_session() as session:
            q = session.query(ProjectNode).filter(
                ProjectNode.is_discarded == False  # noqa: E712
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            q = q.order_by(ProjectNode.sort_order, ProjectNode.node_id)
            q = q.limit(limit).offset(offset)
            rows = q.all()
            result = []
            for row in rows:
                item = {}
                for field in NODE_BUSINESS_FIELDS:
                    val = getattr(row, field, None)
                    # DECIMAL 字符串字段转 float 便于 JSON 序列化
                    if field in ("child_weight", "progress") and val is not None:
                        try:
                            val = float(val)
                        except (ValueError, TypeError):
                            pass
                    item[field] = val
                result.append(item)
            return result

    @staticmethod
    def get_node_detail(node_id: str) -> Optional[dict]:
        """查询单节点完整业务字段（按业务编号 node_id 查询）。

        Args:
            node_id: 节点业务编号（如 SG-JG-01-2026）

        Returns:
            业务字段字典，未找到返回 None。
        """
        with get_session() as session:
            row = (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.node_id == node_id,
                    ProjectNode.is_discarded == False,  # noqa: E712
                )
                .first()
            )
            if row is None:
                return None
            item = {}
            for field in NODE_BUSINESS_FIELDS:
                val = getattr(row, field, None)
                if field in ("child_weight", "progress") and val is not None:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        pass
                item[field] = val
            return item

    # ── 管控文件 ──

    @staticmethod
    def list_files(
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询管控文件列表（仅最新版本，排除已删除）。

        Returns:
            字典列表，含 file_no/filename/file_type/version/uploaded_by_name/
            created_at/confidentiality/is_latest。
        """
        with get_session() as session:
            q = session.query(File).filter(
                File.is_deleted == False,  # noqa: E712
                File.is_latest == True,     # noqa: E712
            )
            if project_id:
                q = q.filter(File.project_id == project_id)
            q = q.order_by(File.created_at.desc())
            q = q.limit(limit).offset(offset)
            rows = q.all()
            result = []
            for row in rows:
                # 关联查询上传者姓名
                uploader_name = ""
                if row.uploaded_by:
                    user = session.query(User).filter(User.id == row.uploaded_by).first()
                    if user:
                        uploader_name = user.username or ""
                result.append({
                    "file_no": row.file_no,
                    "filename": row.filename,
                    "file_type": row.file_type or "",
                    "version": row.version or "V1.0",
                    "uploaded_by_name": uploader_name,
                    "created_at": row.created_at,
                    "confidentiality": row.confidentiality or 0,
                    "is_latest": row.is_latest,
                })
            return result

    # ── 人员列表 ──

    @staticmethod
    def list_users(
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询人员列表（活跃用户，排除已删除）。

        company 字段是 JSON 数组存 company_info.id，需关联查名称。
        若 company_info 无匹配则原样展示 ID。

        Returns:
            字典列表，含 id/username/company_names/permission_level。
        """
        with get_session() as session:
            q = session.query(User).filter(
                User.is_deleted == False,  # noqa: E712
                User.status == "active",
            )
            q = q.order_by(User.permission_level.desc(), User.username)
            q = q.limit(limit).offset(offset)
            rows = q.all()

            # 批量加载 company_info 名称映射
            all_companies = session.query(CompanyInfo).filter(
                CompanyInfo.is_deleted == False  # noqa: E712
            ).all()
            company_map = {c.id: c.company_name for c in all_companies}

            result = []
            for row in rows:
                # 解析 company JSON
                company_names = []
                try:
                    company_ids = json.loads(row.company or "[]")
                    for cid in company_ids:
                        name = company_map.get(cid, cid)
                        company_names.append(name)
                except (json.JSONDecodeError, TypeError):
                    company_names = []

                result.append({
                    "id": row.id,
                    "username": row.username or "",
                    "company_names": company_names,
                    "permission_level": row.permission_level or 0,
                })
            return result

    # ── 会话最近消息 ──

    @staticmethod
    def list_recent_messages(
        conversation_id: str,
        limit: int = 5,
    ) -> list[dict]:
        """查询指定会话的最近 N 条消息摘要。

        Args:
            conversation_id: IM 会话 ID（业务 ID，非 UUID）
            limit: 返回条数

        Returns:
            字典列表，含 sender_name/direction/content_summary/created_at。
        """
        with get_session() as session:
            # 先查 Conversation UUID
            conv = session.query(Conversation).filter(
                Conversation.conversation_id == conversation_id,
            ).first()
            if conv is None:
                return []

            messages = (
                session.query(Message)
                .filter(Message.conversation_id == conv.id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )
            result = []
            for msg in reversed(messages):  # 按时间正序返回
                content = msg.content or ""
                result.append({
                    "sender_name": msg.sender_name or "",
                    "direction": msg.direction or "",
                    "content_summary": content[:80] + ("..." if len(content) > 80 else ""),
                    "created_at": msg.created_at,
                })
            return result
```

#### `emily-core/emily_core/infrastructure/docker/client.py` — 新建

```python
# emily-core/emily_core/infrastructure/docker/client.py

"""Docker Engine API 客户端 —— 通过 Unix Socket 获取容器运行状态。

使用 aiohttp.UnixConnector（项目已有依赖）访问 Docker Engine API，
无需额外安装 docker SDK。

仅在 docker.sock 被挂载时可用，否则返回空列表（fail-open）。
"""

from __future__ import annotations

import logging
import os

import aiohttp

logger = logging.getLogger("emily.docker")

# 需要监控的容器名（与 docker-compose 中 container_name 一致）
MONITORED_CONTAINERS = [
    "napcat",
    "astrbot",
    "emily-core",
    "maxkb",
    "emily-postgres",
]

# IM 账号占位（需求 V2 §4.1）
IM_ACCOUNTS = [
    {"platform": "qq", "label": "QQ", "status": "active", "webui_url": "/napcat-webui"},
    {"platform": "wechat", "label": "微信", "status": "no_account"},
    {"platform": "dingtalk", "label": "钉钉", "status": "no_account"},
    {"platform": "feishu", "label": "飞书", "status": "no_account"},
]


def _docker_socket_available() -> bool:
    """检查 Docker Unix Socket 是否可用。"""
    return os.path.exists("/var/run/docker.sock")


async def get_container_status() -> list[dict]:
    """通过 Docker Engine API 获取受监控容器的运行状态。

    Returns:
        [
            {
                "name": "napcat",
                "status": "running" | "stopped",
                "image": "mlikiowa/napcat-docker:latest",
            },
            ...
        ]
    """
    if not _docker_socket_available():
        logger.debug("Docker socket not available — returning all containers as unknown")
        return [
            {"name": name, "status": "unknown", "image": ""}
            for name in MONITORED_CONTAINERS
        ]

    try:
        connector = aiohttp.UnixConnector("/var/run/docker.sock")
        async with aiohttp.ClientSession(connector=connector) as client:
            async with client.get(
                "http://localhost/containers/json?all=true"
            ) as resp:
                if resp.status != 200:
                    logger.warning("Docker API returned status %d", resp.status)
                    return _fallback_status()
                containers = await resp.json()

        # 构建 name → info 映射
        container_map = {}
        for c in containers:
            names = c.get("Names", [])
            name = names[0].lstrip("/") if names else ""
            container_map[name] = {
                "name": name,
                "status": "running" if c.get("State") == "running" else "stopped",
                "image": c.get("Image", ""),
            }

        # 按监控列表顺序返回
        result = []
        for name in MONITORED_CONTAINERS:
            if name in container_map:
                result.append(container_map[name])
            else:
                result.append({"name": name, "status": "stopped", "image": ""})
        return result

    except Exception as e:
        logger.warning("Docker API call failed: %s", e)
        return _fallback_status()


def _fallback_status() -> list[dict]:
    """Docker API 不可用时的降级返回。"""
    return [
        {"name": name, "status": "unknown", "image": ""}
        for name in MONITORED_CONTAINERS
    ]


async def get_im_accounts(host_ip: str = "") -> list[dict]:
    """返回 IM 账号状态列表。

    当前仅 QQ 已实现，其余预留"无账号、无连接"占位。
    QQ 状态取自 NapCat 容器运行状态。

    Args:
        host_ip: 宿主机 IP（用于拼 NapCat WebUI URL）

    Returns:
        IM 账号状态列表。
    """
    containers = await get_container_status()
    napcat_running = any(
        c["name"] == "napcat" and c["status"] == "running"
        for c in containers
    )

    accounts = []
    for acc in IM_ACCOUNTS:
        entry = {"platform": acc["platform"], "label": acc["label"]}
        if acc["platform"] == "qq":
            entry["status"] = "connected" if napcat_running else "disconnected"
            entry["webui_url"] = f"http://{host_ip}:6099" if host_ip and napcat_running else ""
        else:
            entry["status"] = acc["status"]
            entry["webui_url"] = ""
        accounts.append(entry)
    return accounts
```

### 模块验收检测

```bash
# 验收 1：SessionPoolManager.get_status() 方法存在
docker exec emily-core python -c "from emily_core.adapters.session.session_pool import SessionPoolManager; print(hasattr(SessionPoolManager, 'get_status'))"
→ 预期输出：True

# 验收 2：MonitorRepository 类可 import
docker exec emily-core python -c "from emily_core.repositories.monitor_repo import MonitorRepository; print('OK')"
→ 预期输出：OK

# 验收 3：Docker client 模块可 import
docker exec emily-core python -c "from emily_core.infrastructure.docker.client import get_container_status, get_im_accounts; print('OK')"
→ 预期输出：OK

# 验收 4：MonitorRepository 可直接调用（DB 查询）
docker exec emily-core python -c "
from emily_core.repositories.monitor_repo import MonitorRepository
nodes = MonitorRepository.list_nodes(limit=3)
print(f'nodes: {len(nodes)}')
files = MonitorRepository.list_files(limit=3)
print(f'files: {len(files)}')
users = MonitorRepository.list_users(limit=3)
print(f'users: {len(users)}')
"
→ 预期输出：nodes/files/users 各输出条数（数字，非报错）
```

**失败处理**：如果 import 失败，检查文件是否已复制到容器内（Docker bind-mount 不自动刷新，需清除 `__pycache__` 或重启容器）。如果 DB 查询报错，检查 `init_db()` 是否已执行。

---

## M2: API 层（MonitorService + Schemas + Routes + MonitorApp + DualServer）

**依赖**：M1

**职责**：构建监控 API 的完整调用链——Service（异步编排）→ Pydantic Schema（响应格式）→ FastAPI Router（HTTP 端点）→ 独立 FastAPI 应用（18081 端口）→ 双服务器启动脚本。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | MonitorService | `emily-core/emily_core/services/monitor_service.py`（新建） |
| 2 | 监控 API Schema | `emily-core/api/routes/monitor_schemas.py`（新建） |
| 3 | 监控 API 路由 | `emily-core/api/routes/monitor.py`（新建） |
| 4 | 监控 FastAPI 应用 | `emily-core/api/monitor_app.py`（新建） |
| 5 | 双服务器启动入口 | `emily-core/api/run.py`（新建） |

### 代码

#### `emily-core/emily_core/services/monitor_service.py` — 新建

```python
# emily-core/emily_core/services/monitor_service.py

"""MonitorService —— 监控数据异步编排层。

参照模式：services/node_service.py（async def + asyncio.to_thread 包裹 sync repo）。
编排三类数据源：
  1. Session 池状态（内存，直接读取）
  2. DB 数据（sync repo，asyncio.to_thread 包裹）
  3. Docker 容器状态（aiohttp 异步调用）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..repositories.monitor_repo import MonitorRepository
from ..infrastructure.docker.client import get_container_status, get_im_accounts

logger = logging.getLogger("emily.service.monitor")


class MonitorService:
    """监控数据编排服务。

    通过 get_core() 延迟获取 SessionPoolManager，避免循环导入。
    """

    def __init__(self, core=None):
        self._core = core

    def _get_pool(self):
        """获取 SessionPoolManager。"""
        if self._core is not None:
            return self._core._session_pool
        # 延迟获取
        try:
            from api.server import get_core
            core = get_core()
            return core._session_pool
        except Exception:
            return None

    # ── 容器状态 ──

    async def get_containers(self) -> list[dict]:
        """获取受监控容器运行状态。"""
        return await get_container_status()

    # ── IM 账号状态 ──

    async def get_im_accounts(self, host_ip: str = "") -> list[dict]:
        """获取 IM 账号状态列表。"""
        return await get_im_accounts(host_ip=host_ip)

    # ── Session 池 ──

    async def get_session_pool(self) -> dict:
        """获取 Session 池状态。"""
        pool = self._get_pool()
        if pool is None:
            return {"total": 0, "uptime_seconds": 0, "sessions": []}
        # get_status() 是 sync，但在同进程内直接调用（非阻塞，纯内存读取）
        return pool.get_status()

    # ── 会话消息 ──

    async def get_session_messages(self, conversation_id: str, limit: int = 5) -> list[dict]:
        """获取指定会话最近 N 条消息。"""
        return await asyncio.to_thread(
            MonitorRepository.list_recent_messages,
            conversation_id=conversation_id,
            limit=limit,
        )

    # ── 全景节点 ──

    async def list_nodes(
        self,
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询全景节点列表（仅业务字段）。"""
        return await asyncio.to_thread(
            MonitorRepository.list_nodes,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )

    async def get_node_detail(self, node_id: str) -> Optional[dict]:
        """查询单节点完整业务字段。"""
        return await asyncio.to_thread(
            MonitorRepository.get_node_detail,
            node_id=node_id,
        )

    # ── 管控文件 ──

    async def list_files(
        self,
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询管控文件列表。"""
        return await asyncio.to_thread(
            MonitorRepository.list_files,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )

    # ── 人员列表 ──

    async def list_users(
        self,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询人员列表。"""
        return await asyncio.to_thread(
            MonitorRepository.list_users,
            limit=limit,
            offset=offset,
        )
```

#### `emily-core/api/routes/monitor_schemas.py` — 新建

```python
# emily-core/api/routes/monitor_schemas.py

"""监控 API Pydantic Schemas —— 响应体定义。

参照模式：api/routes/node_schemas.py（ApiResponse + BaseModel + Field）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# 通用响应
# ══════════════════════════════════════════════════════════════════════════════

class MonitorApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: dict | list | None = None


# ══════════════════════════════════════════════════════════════════════════════
# 容器状态
# ══════════════════════════════════════════════════════════════════════════════

class ContainerItem(BaseModel):
    name: str = Field(..., description="容器名")
    status: str = Field(..., description="running / stopped / unknown")
    image: str = Field(default="", description="镜像名")


class IMAccountItem(BaseModel):
    platform: str = Field(..., description="IM 平台标识")
    label: str = Field(..., description="显示名")
    status: str = Field(..., description="connected / disconnected / no_account")
    webui_url: str = Field(default="", description="WebUI 外链地址")


# ══════════════════════════════════════════════════════════════════════════════
# Session 池
# ══════════════════════════════════════════════════════════════════════════════

class SessionItem(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    last_active_ts: float = Field(..., description="最后活跃时间戳")
    idle_seconds: int = Field(..., description="空闲秒数")


class SessionPoolResponse(BaseModel):
    total: int = Field(0, description="活跃 Session 数")
    uptime_seconds: int = Field(0, description="池运行时长")
    sessions: list[SessionItem] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 消息
# ══════════════════════════════════════════════════════════════════════════════

class MessageItem(BaseModel):
    sender_name: str = Field(default="", description="发送者")
    direction: str = Field(default="", description="user_to_agent / agent_to_user")
    content_summary: str = Field(default="", description="内容摘要（80字截断）")
    created_at: str = Field(default="", description="时间")
```

#### `emily-core/api/routes/monitor.py` — 新建

```python
# emily-core/api/routes/monitor.py

"""监控 API 路由 —— 只读运维看板。

端点：
    GET /api/v1/monitor/containers                  — 容器运行状态
    GET /api/v1/monitor/im-accounts                 — IM 账号状态
    GET /api/v1/monitor/sessions                    — Session 池状态
    GET /api/v1/monitor/sessions/{conversation_id}/messages — 会话最近消息
    GET /api/v1/monitor/nodes                       — 全景节点列表
    GET /api/v1/monitor/nodes/{node_id}             — 节点详情
    GET /api/v1/monitor/files                       — 管控文件列表
    GET /api/v1/monitor/users                       — 人员列表

参照模式：api/routes/node.py（lazy _get_service + set_xxx_service 注入）。
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Query

from .monitor_schemas import MonitorApiResponse

logger = logging.getLogger("emily.api.monitor")

router = APIRouter(prefix="/monitor", tags=["monitor"])

# 延迟初始化（与 node 路由同模式）
_service = None


def set_monitor_service(service) -> None:
    """由 EmilyCore 注入 MonitorService 实例。"""
    global _service
    _service = service


def _get_service():
    """惰性获取 MonitorService。"""
    global _service
    if _service is not None:
        return _service
    try:
        from api.server import get_core
        core = get_core()
        core._ensure_initialized()
        _service = core._monitor_service
    except Exception:
        pass
    if _service is None:
        raise HTTPException(status_code=503, detail="Monitor module not initialized")
    return _service


def _get_host_ip() -> str:
    """获取宿主机 IP（用于拼 NapCat WebUI URL）。"""
    return os.environ.get("EMILY_HOST_IP", "")


# ══════════════════════════════════════════════════════════════════════════════
# 容器状态
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/containers")
async def get_containers():
    """获取受监控容器运行状态。"""
    svc = _get_service()
    containers = await svc.get_containers()
    im_accounts = await svc.get_im_accounts(host_ip=_get_host_ip())
    return MonitorApiResponse(data={
        "containers": containers,
        "im_accounts": im_accounts,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Session 池
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sessions")
async def get_sessions():
    """获取活跃 Session 池状态。"""
    svc = _get_service()
    data = await svc.get_session_pool()
    return MonitorApiResponse(data=data)


@router.get("/sessions/{conversation_id}/messages")
async def get_session_messages(
    conversation_id: str,
    limit: int = Query(default=5, ge=1, le=20),
):
    """获取指定会话最近 N 条消息。"""
    svc = _get_service()
    messages = await svc.get_session_messages(conversation_id, limit=limit)
    return MonitorApiResponse(data=messages)


# ══════════════════════════════════════════════════════════════════════════════
# 全景节点
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/nodes")
async def list_nodes(
    project_id: str | None = Query(default=None, description="按项目筛选"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """查询全景节点列表（仅业务字段，排除已废弃）。"""
    svc = _get_service()
    nodes = await svc.list_nodes(project_id=project_id, limit=limit, offset=offset)
    return MonitorApiResponse(data=nodes)


@router.get("/nodes/{node_id}")
async def get_node_detail(node_id: str):
    """查询单节点完整业务字段。"""
    svc = _get_service()
    node = await svc.get_node_detail(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return MonitorApiResponse(data=node)


# ══════════════════════════════════════════════════════════════════════════════
# 管控文件
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/files")
async def list_files(
    project_id: str | None = Query(default=None, description="按项目筛选"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """查询管控文件列表（仅最新版本，排除已删除）。"""
    svc = _get_service()
    files = await svc.list_files(project_id=project_id, limit=limit, offset=offset)
    return MonitorApiResponse(data=files)


# ══════════════════════════════════════════════════════════════════════════════
# 人员列表
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users(
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """查询人员列表（活跃用户）。"""
    svc = _get_service()
    users = await svc.list_users(limit=limit, offset=offset)
    return MonitorApiResponse(data=users)
```

#### `emily-core/api/monitor_app.py` — 新建

```python
# emily-core/api/monitor_app.py

"""监控专用 FastAPI 应用 —— 运行在 18081 端口。

仅挂载 /api/v1/monitor/* 路由 + 静态文件（前端看板）。
不挂载任何业务路由（message/session/permission 等）。

与 api/server.py 共享同一个 EmilyCore 实例（通过 get_core()）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

logger = logging.getLogger("emily.api.monitor")


@asynccontextmanager
async def monitor_lifespan(app: FastAPI):
    """监控应用生命周期——确保 EmilyCore 已初始化。"""
    # 复用 server.py 的 lifespan 已初始化的 EmilyCore
    # 此处仅确保 get_core() 可用
    try:
        from api.server import get_core
        core = get_core()
        logger.info("Monitor app: EmilyCore reference acquired")
    except RuntimeError:
        # server.py 可能还未完成 lifespan，等待首次请求时 lazy init
        logger.info("Monitor app: EmilyCore not yet ready, will lazy-init on request")
    yield
    logger.info("Monitor app shutting down")


app = FastAPI(
    title="Emily Monitor API",
    version="1.0",
    lifespan=monitor_lifespan,
)

# 注册监控路由
from .routes import monitor  # noqa: E402

app.include_router(monitor.router, prefix="/api/v1")

# 静态文件（前端看板）
# 优先使用容器内 /app/static，开发环境回退到 emily-core/static/
_static_dir = Path("/app/static")
if not _static_dir.exists():
    _static_dir = Path(__file__).resolve().parent.parent / "static"

if _static_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_static_dir)), name="assets")

    @app.get("/")
    async def serve_index():
        """提供前端看板主页。"""
        index_file = _static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Emily Monitor — static files not found", "static_dir": str(_static_dir)}
else:
    @app.get("/")
    async def no_static():
        return {"message": "Emily Monitor — static directory not found"}
```

#### `emily-core/api/run.py` — 新建

```python
# emily-core/api/run.py

"""双服务器启动入口 ——

  - 18080: 业务 API（api.server:app），绑定 0.0.0.0
  - 18081: 监控 API + 静态页面（api.monitor_app:app），绑定 0.0.0.0

两个 uvicorn 实例共享同一个 Python 进程和 EmilyCore 实例。
docker-compose 中通过端口映射控制访问范围：
  - 127.0.0.1:18080 → 仅宿主机
  - 0.0.0.0:18081  → 局域网
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

logger = logging.getLogger("emily.run")


async def main():
    """同时启动业务 API 和监控 API。"""
    config_main = uvicorn.Config(
        "api.server:app",
        host="0.0.0.0",
        port=18080,
        log_level="info",
    )
    config_monitor = uvicorn.Config(
        "api.monitor_app:app",
        host="0.0.0.0",
        port=18081,
        log_level="info",
    )

    server_main = uvicorn.Server(config_main)
    server_monitor = uvicorn.Server(config_monitor)

    logger.info("Starting dual servers: :18080 (business) + :18081 (monitor)")

    await asyncio.gather(
        server_main.serve(),
        server_monitor.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

### 模块验收检测

```bash
# 验收 1：MonitorService 可 import
docker exec emily-core python -c "from emily_core.services.monitor_service import MonitorService; print('OK')"
→ 预期输出：OK

# 验收 2：monitor_schemas 可 import
docker exec emily-core python -c "from api.routes.monitor_schemas import MonitorApiResponse; print('OK')"
→ 预期输出：OK

# 验收 3：monitor 路由可 import
docker exec emily-core python -c "from api.routes.monitor import router; print(f'routes: {len(router.routes)}')"
→ 预期输出：routes: 8

# 验收 4：monitor_app 可 import
docker exec emily-core python -c "from api.monitor_app import app; print(f'app title: {app.title}')"
→ 预期输出：app title: Emily Monitor API

# 验收 5：run.py 可 import
docker exec emily-core python -c "from api.run import main; print('OK')"
→ 预期输出：OK
```

**失败处理**：import 失败则检查文件路径和 `__pycache__`；路由数量不符则检查 `@router.get` 装饰器是否正确。

---

## M3: 部署集成（EmilyCore 注入 + Dockerfile + DockerCompose + AuthMW）

**依赖**：M2

**职责**：将 M1-M2 产出的模块接入 EmilyCore 生命周期、修改 Dockerfile 支持双服务器、更新 docker-compose 端口映射和卷挂载、调整 AuthMiddleware 放行监控路由。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | EmilyCore 初始化 | `emily-core/emily_core/__init__.py`（修改） |
| 2 | AuthMiddleware 放行 | `emily-core/api/middleware/auth.py`（修改） |
| 3 | Dockerfile | `emily-core/Dockerfile`（修改） |
| 4 | Docker Compose | `docker-compose-napcat.yml`（修改） |

### 代码

#### `emily-core/emily_core/__init__.py` — 两处追加

**位置 1**：在 `EmilyCore.__init__` 方法中，`# 元认知模块` 注释行之前，追加：

```python
        # 监控模块（Monitor Dashboard）
        self._monitor_service = None
```

**位置 2**：在 `EmilyCore._ensure_initialized` 方法中，`# ── 元认知模块 ──` 注释行之前，追加：

```python
        #  ── 监控模块（Monitor Dashboard）──
        self._init_monitor_module()

```

**位置 3**：在 `EmilyCore` 类中，`_init_meta_cognition` 方法定义之前，追加整个新方法：

```python
    def _init_monitor_module(self) -> None:
        """初始化监控模块：MonitorService。fail-open。"""
        try:
            from .services.monitor_service import MonitorService
            self._monitor_service = MonitorService(core=self)

            # 注入到 API 路由
            try:
                from api.routes.monitor import set_monitor_service
                set_monitor_service(self._monitor_service)
            except ImportError:
                pass  # 非 API 场景

            logger.info("Monitor module initialized")
        except Exception as e:
            logger.warning("Monitor module init failed: %s", e)
            self._monitor_service = None

```

#### `emily-core/api/middleware/auth.py` — 修改 `dispatch` 方法

将现有的 `dispatch` 方法替换为：

```python
# emily-core/api/middleware/auth.py

"""鉴权中间件（占位，蓝图 §2.2 middleware/auth.py）。

emily-core 仅监听内网（astrbot_network），当前默认放行。
真实的请求级鉴权（如插件 ↔ Core 的共享密钥校验）属后续增强。
监控路由（/api/v1/monitor/）始终放行，不受 EMILY_API_TOKEN 约束。
"""

from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("emily.api.auth")


class AuthMiddleware(BaseHTTPMiddleware):
    """请求鉴权中间件（占位）。

    若设置环境变量 EMILY_API_TOKEN，则校验请求头 X-Emily-Token；否则放行。
    健康检查端点 + 监控路由始终放行。
    """

    # 始终放行的路径前缀
    _PUBLIC_PREFIXES = ("/health", "/api/v1/monitor/", "/assets/", "/")

    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("EMILY_API_TOKEN", "")

        # 始终放行的路由
        path = request.url.path
        for prefix in self._PUBLIC_PREFIXES:
            if path == prefix or path.startswith(prefix):
                return await call_next(request)

        # 需要 Token 校验的路由
        if expected:
            token = request.headers.get("X-Emily-Token", "")
            if token != expected:
                from starlette.responses import JSONResponse
                return JSONResponse({"detail": "unauthorized"}, status_code=401)

        return await call_next(request)
```

#### `emily-core/Dockerfile` — 整体替换

```dockerfile
# Emily Core 容器镜像
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
```

#### `docker-compose-napcat.yml` — emily-core 服务修改

在 emily-core 服务的 `ports` 部分追加 18081 映射，在 `volumes` 部分追加 docker.sock 挂载，追加环境变量 `EMILY_HOST_IP`。

具体改动：

1. `ports` 部分，在 `"127.0.0.1:18080:18080"` 之后追加一行：
```yaml
      - "0.0.0.0:18081:18081"           # 监控看板（局域网可访问）
```

2. `volumes` 部分，在最后一行卷挂载之后追加：
```yaml
      - /var/run/docker.sock:/var/run/docker.sock:ro   # Docker API（容器状态查询）
```

3. `environment` 部分，追加：
```yaml
      - EMILY_HOST_IP=${EMILY_HOST_IP:-}
```

### 模块验收检测

```bash
# 验收 1：EmilyCore 包含 _monitor_service 属性
docker exec emily-core python -c "
from emily_core import EmilyCore
from emily_core.config import Config
c = EmilyCore(Config())
print(hasattr(c, '_monitor_service'))
"
→ 预期输出：True

# 验收 2：AuthMiddleware 放行 /api/v1/monitor/ 前缀
docker exec emily-core python -c "
from api.middleware.auth import AuthMiddleware
mw = AuthMiddleware(None)
print('/api/v1/monitor/' in [p for p in dir(mw) if 'PUBLIC' in p.upper()] or hasattr(mw, '_PUBLIC_PREFIXES'))
"
→ 预期输出：True

# 验收 3：Dockerfile EXPOSE 包含 18081
grep "18081" emily-core/Dockerfile
→ 预期输出：包含 EXPOSE 18081 的行

# 验收 4：docker-compose 包含 18081 端口映射
grep "18081" docker-compose-napcat.yml
→ 预期输出：包含 18081 映射的行

# 验收 5：重启后监控端口可访问
docker compose -f docker-compose-napcat.yml restart emily-core
# 等待 10 秒启动
Start-Sleep -Seconds 10
curl -s http://localhost:18081/api/v1/monitor/containers | python -m json.tool
→ 预期输出：JSON 响应，包含 containers 和 im_accounts 字段
```

**失败处理**：如果 18081 端口不可访问，检查 Dockerfile CMD 是否为 `python -m api.run`；如果容器启动失败，检查 `docker logs emily-core` 日志。

---

## M4: 前端看板（HTML + CSS + JS）

**依赖**：M3

**职责**：实现只读运维看板的前端页面——核心状态区（5 容器 + IM 账号）+ 4 个 Tab 面板（Session 池 / 全景节点 / 文件 / 人员），手动刷新。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 看板主页 | `emily-core/static/index.html`（新建） |
| 2 | 样式 | `emily-core/static/style.css`（新建） |
| 3 | 前端逻辑 | `emily-core/static/app.js`（新建） |

### 代码

#### `emily-core/static/index.html` — 新建

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Emily 运维看板</title>
    <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
    <div class="container">
        <!-- 顶栏 -->
        <header class="header">
            <h1>Emily 运维看板</h1>
            <button id="btn-refresh-all" class="btn-refresh" title="全部刷新">🔄 全部刷新</button>
        </header>

        <!-- 核心状态区 -->
        <section class="core-status" id="core-status">
            <h2>⬤ 核心状态</h2>
            <div class="containers" id="containers"></div>
            <div class="im-accounts" id="im-accounts"></div>
        </section>

        <!-- Tab 导航 -->
        <nav class="tab-nav">
            <button class="tab-btn active" data-tab="sessions">Session池</button>
            <button class="tab-btn" data-tab="nodes">全景节点</button>
            <button class="tab-btn" data-tab="files">文件</button>
            <button class="tab-btn" data-tab="users">人员</button>
        </nav>

        <!-- Tab 内容 -->
        <section class="tab-content">
            <!-- Session 池 -->
            <div class="tab-panel active" id="tab-sessions">
                <div class="panel-header">
                    <h3>Session 池</h3>
                    <button class="btn-refresh-sm" data-refresh="sessions">刷新</button>
                </div>
                <div id="session-summary" class="summary"></div>
                <table class="data-table" id="session-table">
                    <thead><tr><th>会话ID</th><th>空闲时间</th><th>操作</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>

            <!-- 全景节点 -->
            <div class="tab-panel" id="tab-nodes">
                <div class="panel-header">
                    <h3>全景节点</h3>
                    <div class="filter-group">
                        <select id="node-project-filter"><option value="">全部项目</option></select>
                        <button class="btn-refresh-sm" data-refresh="nodes">刷新</button>
                    </div>
                </div>
                <table class="data-table" id="node-table">
                    <thead><tr><th>节点编号</th><th>节点名称</th><th>主责条线</th><th>截止时间</th><th>进度</th><th>状态</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>

            <!-- 文件 -->
            <div class="tab-panel" id="tab-files">
                <div class="panel-header">
                    <h3>管控文件</h3>
                    <div class="filter-group">
                        <select id="file-project-filter"><option value="">全部项目</option></select>
                        <button class="btn-refresh-sm" data-refresh="files">刷新</button>
                    </div>
                </div>
                <table class="data-table" id="file-table">
                    <thead><tr><th>文件名</th><th>类型</th><th>版本</th><th>上传者</th><th>时间</th><th>密级</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>

            <!-- 人员 -->
            <div class="tab-panel" id="tab-users">
                <div class="panel-header">
                    <h3>人员列表</h3>
                    <button class="btn-refresh-sm" data-refresh="users">刷新</button>
                </div>
                <table class="data-table" id="user-table">
                    <thead><tr><th>ID</th><th>姓名</th><th>所属企业</th><th>等级</th></tr></thead>
                    <tbody></tbody>
                </table>
            </div>
        </section>
    </div>

    <!-- 消息详情弹窗 -->
    <div class="modal-overlay" id="modal-overlay">
        <div class="modal">
            <div class="modal-header"><h3 id="modal-title">会话消息</h3><button class="modal-close" id="modal-close">&times;</button></div>
            <div class="modal-body" id="modal-body"></div>
        </div>
    </div>

    <!-- 节点详情弹窗 -->
    <div class="modal-overlay" id="node-modal-overlay">
        <div class="modal modal-wide">
            <div class="modal-header"><h3 id="node-modal-title">节点详情</h3><button class="modal-close" id="node-modal-close">&times;</button></div>
            <div class="modal-body" id="node-modal-body"></div>
        </div>
    </div>

    <script src="/assets/app.js"></script>
</body>
</html>
```

#### `emily-core/static/style.css` — 新建

```css
/* Emily 运维看板样式 */

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    line-height: 1.6;
}

.container { max-width: 1200px; margin: 0 auto; padding: 16px; }

/* 顶栏 */
.header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 0;
    border-bottom: 1px solid #333;
    margin-bottom: 16px;
}
.header h1 { font-size: 20px; color: #00d4ff; }

/* 按钮 */
.btn-refresh {
    background: #0f3460; color: #e0e0e0; border: 1px solid #00d4ff;
    padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px;
}
.btn-refresh:hover { background: #16537e; }
.btn-refresh-sm {
    background: transparent; color: #00d4ff; border: 1px solid #00d4ff;
    padding: 4px 12px; border-radius: 3px; cursor: pointer; font-size: 12px;
}
.btn-refresh-sm:hover { background: #0f3460; }

/* 核心状态区 */
.core-status {
    background: #16213e; border-radius: 8px; padding: 16px; margin-bottom: 16px;
}
.core-status h2 { font-size: 16px; margin-bottom: 12px; color: #00d4ff; }

.containers {
    display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px;
}
.container-item {
    display: flex; align-items: center; gap: 6px;
    background: #1a1a2e; padding: 6px 12px; border-radius: 4px; font-size: 14px;
}
.status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.status-dot.running { background: #4caf50; }
.status-dot.stopped { background: #f44336; }
.status-dot.unknown { background: #ff9800; }

.im-accounts {
    display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
}
.im-item {
    display: flex; align-items: center; gap: 6px;
    background: #1a1a2e; padding: 6px 12px; border-radius: 4px; font-size: 14px;
}
.im-item a { color: #00d4ff; text-decoration: none; }
.im-item a:hover { text-decoration: underline; }
.im-status-connected { color: #4caf50; }
.im-status-disconnected { color: #f44336; }
.im-status-no_account { color: #666; }

/* Tab 导航 */
.tab-nav {
    display: flex; gap: 0; border-bottom: 2px solid #333; margin-bottom: 0;
}
.tab-btn {
    padding: 10px 20px; background: transparent; color: #999; border: none;
    cursor: pointer; font-size: 14px; border-bottom: 2px solid transparent;
    margin-bottom: -2px;
}
.tab-btn.active { color: #00d4ff; border-bottom-color: #00d4ff; }
.tab-btn:hover { color: #e0e0e0; }

/* Tab 内容 */
.tab-panel { display: none; background: #16213e; padding: 16px; border-radius: 0 0 8px 8px; }
.tab-panel.active { display: block; }

.panel-header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;
}
.panel-header h3 { font-size: 16px; color: #00d4ff; }

.filter-group { display: flex; gap: 8px; align-items: center; }
.filter-group select {
    background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
    padding: 4px 8px; border-radius: 3px; font-size: 13px;
}

.summary { color: #999; font-size: 13px; margin-bottom: 8px; }

/* 数据表格 */
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
    text-align: left; padding: 8px 12px; border-bottom: 1px solid #333;
    color: #999; font-weight: normal; white-space: nowrap;
}
.data-table td { padding: 8px 12px; border-bottom: 1px solid #222; }
.data-table tr:hover td { background: #1a1a2e; }

.cell-link { color: #00d4ff; cursor: pointer; text-decoration: none; }
.cell-link:hover { text-decoration: underline; }

/* 进度条 */
.progress-bar {
    display: inline-block; width: 60px; height: 8px; background: #333;
    border-radius: 4px; overflow: hidden; vertical-align: middle;
}
.progress-fill { height: 100%; background: #4caf50; border-radius: 4px; }
.progress-text { font-size: 12px; margin-left: 4px; vertical-align: middle; }

/* 状态标签 */
.status-tag {
    display: inline-block; padding: 2px 8px; border-radius: 3px;
    font-size: 12px;
}
.status-tag-NOT_ACTIVATED { background: #333; color: #999; }
.status-tag-CONDITIONS_NOT_MET { background: #ff9800; color: #000; }
.status-tag-IN_PROGRESS { background: #2196f3; color: #fff; }
.status-tag-COMPLETED { background: #4caf50; color: #fff; }

/* 密级标签 */
.conf-tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 12px; }
.conf-0 { background: #333; color: #999; }
.conf-1 { background: #2196f3; color: #fff; }
.conf-2 { background: #ff9800; color: #000; }
.conf-3 { background: #f44336; color: #fff; }

/* 弹窗 */
.modal-overlay {
    display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.7); z-index: 100; justify-content: center; align-items: center;
}
.modal-overlay.active { display: flex; }
.modal {
    background: #16213e; border-radius: 8px; width: 600px; max-height: 80vh;
    overflow-y: auto; padding: 20px;
}
.modal-wide { width: 800px; }
.modal-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 16px; border-bottom: 1px solid #333; padding-bottom: 8px;
}
.modal-header h3 { color: #00d4ff; font-size: 16px; }
.modal-close {
    background: none; border: none; color: #999; font-size: 24px; cursor: pointer;
}
.modal-close:hover { color: #fff; }

.msg-item {
    padding: 8px 0; border-bottom: 1px solid #222;
}
.msg-item:last-child { border-bottom: none; }
.msg-direction { font-size: 11px; color: #999; }
.msg-user { color: #00d4ff; font-size: 13px; }
.msg-agent { color: #4caf50; font-size: 13px; }
.msg-content { font-size: 13px; margin-top: 2px; }
.msg-time { font-size: 11px; color: #666; }

/* 节点详情表格 */
.detail-table { width: 100%; font-size: 13px; }
.detail-table td { padding: 6px 12px; border-bottom: 1px solid #222; }
.detail-table td:first-child { color: #999; width: 120px; }
```

#### `emily-core/static/app.js` — 新建

```javascript
// Emily 运维看板 — 前端逻辑

const API_BASE = '/api/v1/monitor';

// ── 工具函数 ──

async function apiFetch(path) {
    const resp = await fetch(API_BASE + path);
    if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
    const json = await resp.json();
    if (json.code !== 0) throw new Error(json.message || 'API error');
    return json.data;
}

function formatIdle(seconds) {
    if (seconds < 60) return `${seconds}秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`;
    return `${Math.floor(seconds / 3600)}小时`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

const CONF_LABELS = ['公开', '内部', '机密', '绝密'];
const LEVEL_LABELS = ['访客', '普通用户', '高级用户', '管理员', '高级管理员', '系统管理员', '超级管理员'];

// ── 核心状态区 ──

async function loadContainers() {
    try {
        const data = await apiFetch('/containers');
        renderContainers(data.containers || []);
        renderImAccounts(data.im_accounts || []);
    } catch (e) {
        console.error('loadContainers failed:', e);
    }
}

function renderContainers(containers) {
    const el = document.getElementById('containers');
    el.innerHTML = containers.map(c => `
        <div class="container-item">
            <span class="status-dot ${c.status}"></span>
            <span>${escapeHtml(c.name)}</span>
            <span style="color:#666;font-size:12px">${c.status === 'running' ? '运行中' : c.status === 'stopped' ? '已停止' : '未知'}</span>
        </div>
    `).join('');
}

function renderImAccounts(accounts) {
    const el = document.getElementById('im-accounts');
    el.innerHTML = accounts.map(a => {
        const statusClass = a.status === 'connected' ? 'im-status-connected'
            : a.status === 'disconnected' ? 'im-status-disconnected'
            : 'im-status-no_account';
        const statusText = a.status === 'connected' ? '已连接'
            : a.status === 'disconnected' ? '已断开'
            : '无账号、无连接';
        const link = a.webui_url
            ? ` <a href="${escapeHtml(a.webui_url)}" target="_blank">[WebUI →]</a>` : '';
        return `<div class="im-item">
            <span>${escapeHtml(a.label)}:</span>
            <span class="${statusClass}">${statusText}</span>${link}
        </div>`;
    }).join('');
}

// ── Session 池 ──

async function loadSessions() {
    try {
        const data = await apiFetch('/sessions');
        const summary = document.getElementById('session-summary');
        summary.textContent = `活跃会话: ${data.total} 个 | 池运行: ${formatIdle(data.uptime_seconds)}`;

        const tbody = document.querySelector('#session-table tbody');
        tbody.innerHTML = (data.sessions || []).map(s => `
            <tr>
                <td class="cell-link" onclick="showMessages('${escapeHtml(s.conversation_id)}')">${escapeHtml(s.conversation_id.substring(0, 20))}...</td>
                <td>${formatIdle(s.idle_seconds)}</td>
                <td><span class="cell-link" onclick="showMessages('${escapeHtml(s.conversation_id)}')">[消息]</span></td>
            </tr>
        `).join('');

        if (!data.sessions || data.sessions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" style="color:#666">暂无活跃会话</td></tr>';
        }
    } catch (e) {
        console.error('loadSessions failed:', e);
    }
}

async function showMessages(conversationId) {
    try {
        const messages = await apiFetch(`/sessions/${encodeURIComponent(conversationId)}/messages?limit=5`);
        const modal = document.getElementById('modal-overlay');
        document.getElementById('modal-title').textContent = `会话消息 — ${conversationId.substring(0, 20)}...`;
        const body = document.getElementById('modal-body');
        body.innerHTML = messages.map(m => {
            const dirClass = m.direction === 'agent_to_user' ? 'msg-agent' : 'msg-user';
            const dirLabel = m.direction === 'agent_to_user' ? 'Emily → 用户' : '用户 → Emily';
            return `<div class="msg-item">
                <div class="msg-direction">${dirLabel} | <span class="${dirClass}">${escapeHtml(m.sender_name)}</span></div>
                <div class="msg-content">${escapeHtml(m.content_summary)}</div>
                <div class="msg-time">${escapeHtml(m.created_at)}</div>
            </div>`;
        }).join('') || '<div style="color:#666">暂无消息</div>';
        modal.classList.add('active');
    } catch (e) {
        console.error('showMessages failed:', e);
    }
}

// ── 全景节点 ──

async function loadNodes(projectId) {
    try {
        const params = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
        const nodes = await apiFetch('/nodes' + params);
        const tbody = document.querySelector('#node-table tbody');
        tbody.innerHTML = nodes.map(n => `
            <tr>
                <td class="cell-link" onclick="showNodeDetail('${escapeHtml(n.node_id)}')">${escapeHtml(n.node_id)}</td>
                <td>${escapeHtml(n.node_name)}</td>
                <td>${escapeHtml(n.owner_dept_id)}</td>
                <td>${escapeHtml(n.deadline)}</td>
                <td>
                    <span class="progress-bar"><span class="progress-fill" style="width:${n.progress || 0}%"></span></span>
                    <span class="progress-text">${(n.progress || 0).toFixed(1)}%</span>
                </td>
                <td><span class="status-tag status-tag-${n.status}">${escapeHtml(n.status)}</span></td>
            </tr>
        `).join('');

        if (nodes.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="color:#666">暂无节点数据</td></tr>';
        }
    } catch (e) {
        console.error('loadNodes failed:', e);
    }
}

async function showNodeDetail(nodeId) {
    try {
        const node = await apiFetch(`/nodes/${encodeURIComponent(nodeId)}`);
        const modal = document.getElementById('node-modal-overlay');
        document.getElementById('node-modal-title').textContent = `节点详情 — ${node.node_name}`;
        const body = document.getElementById('node-modal-body');
        const fields = [
            ['项目归属', node.project_id], ['节点编号', node.node_id],
            ['节点名称', node.node_name], ['主责条线', node.owner_dept_id],
            ['关联单位', node.related_company_id], ['截止时间', node.deadline],
            ['关联地块', node.land_parcel_id], ['备注', node.remark],
            ['父节点', node.parent_node_id], ['所属阶段', node.stage_id],
            ['子节点权重', node.child_weight], ['启动文档', node.startup_doc_id],
            ['进度', `${(node.progress || 0).toFixed(1)}%`],
            ['状态', node.status],
        ];
        body.innerHTML = `<table class="detail-table">${fields.map(([k, v]) =>
            `<tr><td>${k}</td><td>${escapeHtml(String(v || ''))}</td></tr>`
        ).join('')}</table>`;
        modal.classList.add('active');
    } catch (e) {
        console.error('showNodeDetail failed:', e);
    }
}

// ── 文件 ──

async function loadFiles(projectId) {
    try {
        const params = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
        const files = await apiFetch('/files' + params);
        const tbody = document.querySelector('#file-table tbody');
        tbody.innerHTML = files.map(f => `
            <tr>
                <td>${escapeHtml(f.filename)}</td>
                <td>${escapeHtml(f.file_type)}</td>
                <td>${escapeHtml(f.version)}</td>
                <td>${escapeHtml(f.uploaded_by_name)}</td>
                <td>${escapeHtml(f.created_at)}</td>
                <td><span class="conf-tag conf-${f.confidentiality}">${CONF_LABELS[f.confidentiality] || '未知'}</span></td>
            </tr>
        `).join('');

        if (files.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="color:#666">暂无文件数据</td></tr>';
        }
    } catch (e) {
        console.error('loadFiles failed:', e);
    }
}

// ── 人员 ──

async function loadUsers() {
    try {
        const users = await apiFetch('/users');
        const tbody = document.querySelector('#user-table tbody');
        tbody.innerHTML = users.map(u => `
            <tr>
                <td style="font-size:11px;color:#666">${escapeHtml(u.id.substring(0, 8))}...</td>
                <td>${escapeHtml(u.username)}</td>
                <td>${(u.company_names || []).map(c => escapeHtml(c)).join(', ')}</td>
                <td>${LEVEL_LABELS[u.permission_level] || `L${u.permission_level}`}</td>
            </tr>
        `).join('');

        if (users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="color:#666">暂无人员数据</td></tr>';
        }
    } catch (e) {
        console.error('loadUsers failed:', e);
    }
}

// ── 项目筛选器 ──

async function loadProjectFilters() {
    try {
        const nodes = await apiFetch('/nodes');
        const projects = [...new Set(nodes.map(n => n.project_id).filter(Boolean))];
        const nodeSelect = document.getElementById('node-project-filter');
        const fileSelect = document.getElementById('file-project-filter');
        projects.forEach(p => {
            nodeSelect.innerHTML += `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`;
            fileSelect.innerHTML += `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`;
        });
    } catch (e) {
        console.error('loadProjectFilters failed:', e);
    }
}

// ── Tab 切换 ──

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
        });
    });
}

// ── 刷新按钮 ──

function initRefreshButtons() {
    // 全部刷新
    document.getElementById('btn-refresh-all').addEventListener('click', refreshAll);

    // 单面板刷新
    document.querySelectorAll('.btn-refresh-sm').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.refresh;
            if (target === 'sessions') loadSessions();
            else if (target === 'nodes') loadNodes(document.getElementById('node-project-filter').value);
            else if (target === 'files') loadFiles(document.getElementById('file-project-filter').value);
            else if (target === 'users') loadUsers();
        });
    });

    // 项目筛选
    document.getElementById('node-project-filter').addEventListener('change', e => loadNodes(e.target.value));
    document.getElementById('file-project-filter').addEventListener('change', e => loadFiles(e.target.value));
}

// ── 弹窗 ──

function initModals() {
    document.getElementById('modal-close').addEventListener('click', () => {
        document.getElementById('modal-overlay').classList.remove('active');
    });
    document.getElementById('node-modal-close').addEventListener('click', () => {
        document.getElementById('node-modal-overlay').classList.remove('active');
    });
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', e => {
            if (e.target === overlay) overlay.classList.remove('active');
        });
    });
}

// ── 主入口 ──

function refreshAll() {
    loadContainers();
    loadSessions();
    loadNodes(document.getElementById('node-project-filter').value);
    loadFiles(document.getElementById('file-project-filter').value);
    loadUsers();
}

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initRefreshButtons();
    initModals();
    refreshAll();
    loadProjectFilters();
});
```

### 模块验收检测

```bash
# 验收 1：静态文件存在
ls -la emily-core/static/index.html emily-core/static/style.css emily-core/static/app.js
→ 预期输出：3 个文件均存在

# 验收 2：重建容器后监控端口可访问
docker compose -f docker-compose-napcat.yml build emily-core
docker compose -f docker-compose-napcat.yml up -d emily-core
Start-Sleep -Seconds 15

# 验证主页加载
curl -s http://localhost:18081/ | Select-String "Emily"
→ 预期输出：包含 "Emily 运维看板" 的 HTML

# 验证 API 可用
curl -s http://localhost:18081/api/v1/monitor/containers | python -m json.tool
→ 预期输出：JSON 包含 containers 和 im_accounts

# 验证业务端口仍受限
curl -s http://localhost:18080/health | python -m json.tool
→ 预期输出：健康检查 JSON 正常返回

# 验证各 API 端点
curl -s http://localhost:18081/api/v1/monitor/sessions | python -m json.tool
curl -s http://localhost:18081/api/v1/monitor/nodes | python -m json.tool
curl -s http://localhost:18081/api/v1/monitor/files | python -m json.tool
curl -s http://localhost:18081/api/v1/monitor/users | python -m json.tool
→ 预期输出：各端点返回 JSON，data 字段含对应列表
```

**失败处理**：
- 静态文件 404：检查 Dockerfile 是否 `COPY static/ ./static/`，检查 `monitor_app.py` 中 `_static_dir` 路径
- API 503：检查 `_monitor_service` 是否被注入（看 `docker logs emily-core` 中是否有 "Monitor module initialized"）
- 18081 端口不通：检查 `docker-compose` 端口映射、Dockerfile CMD 是否为 `python -m api.run`

---

## 组装验证

所有模块完成后，运行端到端组装验证：

```bash
# 1. 清除 pycache + 重建容器
docker exec emily-core find /app -name '__pycache__' -type d -exec rm -rf {} + 2>$null
docker compose -f docker-compose-napcat.yml build emily-core
docker compose -f docker-compose-napcat.yml up -d emily-core
Start-Sleep -Seconds 15

# 2. 验证双端口启动
docker logs --tail 30 emily-core 2>&1 | Select-String "18080|18081|Monitor"
→ 预期输出：包含 "Starting dual servers: :18080 (business) + :18081 (monitor)"

# 3. 验证监控主页完整加载
$env:PYTHONIOENCODING="utf-8"
curl -s http://localhost:18081/ | Select-String "Emily"
→ 预期输出：HTML 包含 "Emily 运维看板"

# 4. 验证全部 API 端点返回正常
$endpoints = @("/containers","/sessions","/nodes","/files","/users")
foreach ($ep in $endpoints) {
    $resp = curl -s "http://localhost:18081/api/v1/monitor$ep" | python -m json.tool
    Write-Output "GET $ep → $($resp.Substring(0, [Math]::Min(80, $resp.Length)))..."
}
→ 预期输出：5 个端点均返回 JSON，code=0

# 5. 验证业务端口不受影响
curl -s http://localhost:18080/health | python -m json.tool
→ 预期输出：{"status": "ok", ...}

# 6. 在浏览器中打开 http://<宿主机IP>:18081 验证页面展示
```

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成计划

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
