# 13 MCP 工具与外部接入

> **本类目标**：验证外部 MCP（Model Context Protocol）工具从**接入 → 发现 → 注册 → 可见 → 对话触发**全链路可用，并覆盖可见范围、档位门禁与配置变更边界。
> **用例数**：10　|　**编号前缀**：`MCP-*`
> **执行通道**：A（对话模拟）+ B（HTTP 接口）+ E（库/日志核对）
> **来源**：2026-09-13 `web_search`（DuckDuckGo）接入联调实录 + 代码锚点（见 §五）
> **建立日期**：2026-09-13

## 前置

| 项 | 说明 |
|----|------|
| MCP 配置 | `emily-data/config/mcp_servers.json`（容器内 `/app/config/mcp_servers.json`）；每个 server 含 `transport` / `command` / `tool_prefix` / `category` / `permission_flag` / `write_mode` / `enabled` |
| 现网 sample | `name=web_search`，`transport=stdio`，`command=/opt/mcp-search/bin/duckduckgo-mcp-server`，`tool_prefix=ddg_`，`category=base`，`permission_flag=all`，`write_mode=read`，`enabled=true` |
| 运行时依赖 | emily-core 容器内独立 venv `/opt/mcp-search`（与主环境隔离，见 `emily-core/Dockerfile`）；缺失时 `load_mcp_tools` 仅告警不阻断启动 |
| 生效方式 | 改 `mcp_servers.json` 后需 `docker compose restart emily-core`；**bind-mount 不刷新 `__pycache__`**，改代码需先清缓存 |
| 工具命名 | 内存/DB 中的工具名 = `tool_prefix + MCP 原始工具名`（如 `ddg_search`），`handler_module` 标记为 `mcp:<server_name>` |

**本类工具清单（`web_search` server，3 个）**：`search` / `fetch_content` / `expand_link` → 注册名 `ddg_search` / `ddg_fetch_content` / `ddg_expand_link`。

---

## 一、连通性与发现（`MCP-01`~`02`）

| 编号 | 目标特性 | 操作 | 预期（判定标准） | 断言 | 来源 |
|------|---------|------|----------------|------|------|
| MCP-01 | MCP Server 在线探测（传输 + 握手） | `POST /api/v1/console/mcp/probe?name=web_search` | 返回 `online: true`、`tool_count: 3`；工具名为 `search` / `fetch_content` / `expand_link`；无 `error` 字段 | log_contains | 2026-09-13 联调 |
| MCP-02 | 工具可被真实调用（工具执行） | 以 `mcp.manager._session` 起 stdio 会话，`list_tools()` + `call_tool("search", {...})` | `list_tools` 命中 3 个工具；`call_tool` 返回 `isError=False`，文本含 "search results" 与真实 URL | reply_contains | 2026-09-13 联调 |

> **通道说明**：MCP-01 走通道 B（可加 `X-Emily-Token`）；MCP-02 为容器内一次性探测（`docker exec emily-core python ...`），用于隔离"传输层是否通"与"上层是否接线"。

---

## 二、注册与可见（`MCP-03`~`04`、`MCP-10`）

| 编号 | 目标特性 | 操作 | 预期（判定标准） | 断言 | 来源 |
|------|---------|------|----------------|------|------|
| MCP-03 | **工具写入可见集（双写）** | `SELECT id, category, permission_flag, is_active, handler_module FROM tool_registry WHERE handler_module LIKE 'mcp:%'` | 3 行 `is_active=t`；`category`/`permission_flag` 与 server 配置一致（`base`/`all`）；`handler_module=mcp:web_search` | db_effect | 2026-09-13 联调（**回归重点**） |
| MCP-04 | **LLM 装配可见** | 发一条普通消息，核对装配日志与 `llm_trace.jsonl` | 启动日志 `load_mcp_tools: 3 个 MCP 工具已注册`；装配日志 `build_tool_specs: 43 business tools + 1 resolvers + 2 control = 46 specs`（= 40 内置 + 3 MCP）；trace 的 `tools` 列表含 `ddg_search` | log_contains | 2026-09-13 联调（**回归重点**） |
| MCP-10 | **访客（L1）不可见 MCP 工具**（装配断言） | `docker exec emily-core python -c "from emily_core.session.fetchers.fetch_available_tools import fetch; print([t['api_id'] for t in fetch({'level':1})])"`，并与 `level=3` / `level=5` 对照 | `level=1` 结果**不含** `ddg_search` / `ddg_fetch_content` / `ddg_expand_link`；`level=3` / `level=5` **仍含**三者（不误伤内部档位）。实现口径：`tool_registry.handler_module LIKE 'mcp:%'` 标记 + `level <= L1` 过滤 | db_effect | 2026-09-14 修复（访客无 MCP 调用权限；与 05 类 FR-G10 同源） |

---

## 三、对话触发与档位（`MCP-05`~`07`）

| 编号 | 目标特性 | 用户 | 提问 | 预期（判定标准） | 断言 | 来源 |
|------|---------|------|------|----------------|------|------|
| MCP-05 | 普通对话触发联网检索（管理档） | 王建国(L6) | 帮我上网搜索一下：2026年建筑业有哪些新政策？ | 回复为**联网检索结论**（含具体政策/文件名等网上信息）；`llm_trace` 出现 `ddg_search` 的 tool_call；**不得**回复"我这边没有联网搜索的能力" | reply_contains, log_contains | 2026-09-13 联调 |
| MCP-06 | **口径变更：访客（L1）无 MCP 权限** | 周文斌(L1) / 未登记访客 | 帮我上网搜索一下：装配式建筑最新的国家标准有哪些？ | **不再触发** `ddg_search`：访客可见能力中**不含**任何 `handler_module=mcp:*` 的工具（见 MCP-10）；回复为礼貌的"暂不便提供/无法联网检索"，**不出现**联网检索结论与外部 URL。另见 05 类 FR-G11（访客话术，≤100 字） | permission_block | 2026-09-14 口径变更（原预期为"L1 也能触发"，已被"访客无 MCP 调用权限"取代） |
| MCP-07 | 只读 MCP 工具过档位门禁 | L2 及以上（持有 MCP 工具的档位；L1 访客已不可见，见 MCP-06/10） | 同 MCP-05，检索 `ddg_*` 调用日志 | 工具执行**不被** `FallbackPolicy` 拦截；回复中**不出现**"该操作在当前档位不可用" | permission_block（反向） | 2026-09-13 联调 |

> **MCP-07 反向用例**：当 `write_mode != read`（写语义 MCP 工具）时，普通对话直调**应被拒**（"该操作在当前档位不可用，请走对应标准流程或联系管理员"），须走 SOP 专属流程。当前现网无写语义 MCP server，本项属**实现推导**，构造写 server 后补测。

---

## 四、配置变更边界（`MCP-08`~`09`）

| 编号 | 目标特性 | 操作 | 预期（判定标准） | 断言 | 来源 |
|------|---------|------|----------------|------|------|
| MCP-08 | 禁用 server 不暴露 | 将 `web_search` 置 `enabled:false` 后重启 | 启动日志无新增 MCP 工具；内存不注册；`llm_trace` 工具列表**无** `ddg_*`；DB 残留行**不产生幽灵暴露**（可见要求"内存有 **且** DB 行活跃"） | log_contains | 实现推导（`tool_adapter.build_tool_specs`） |
| MCP-09 | 一致性检查无 MCP 误报 | `docker exec -e PYTHONPATH=/app -e EMILY_DATABASE_URL=... emily-core python /app/scripts/check_tools_consistency.py` | `[V13] tool_registry 表: DB 40 条 \| 内存缺 DB 0 \| DB 缺内存 0`（`handler_module=mcp:*` 行被排除）；无新增 V13a/V13b 告警 | log_contains | 2026-09-13 联调 |

---

## 五、本类断言的实现锚点（追溯用）

| 环节 | 实现位置 | 关键行为 |
|------|---------|---------|
| 连接与发现 | `emily_core/mcp/manager.py` `_session` / `_discover_server` | 支持 stdio/sse/streamable_http；每次调用独立连接 |
| 内存注册 + 双写 | `emily_core/mcp/manager.py` `load_mcp_tools` / `_sync_to_registry` | 注册进 `_business_flow_tools` 同时 upsert `tool_registry`；`handler_module=mcp:<server>` |
| 可见性过滤 | `emily_core/workitem/langgraph_engine/agent/tool_adapter.py` `build_tool_specs` | fail-closed：仅"内存有 **且** 在 `session_api_ids`（来自 DB）"才暴露 |
| 可见范围规则 | `emily_core/repositories/tool_registry_repo.py` `get_available` | base→全员；business→all/write(L3+)/admin(L5+)；project→仅 L5-L6 |
| 访客排除 MCP | `emily_core/session/fetchers/fetch_available_tools.py` `fetch` | `level <= L1` 时剔除 `handler_module LIKE 'mcp:%'` 的工具（FR-G10 / MCP-10 的实现口径） |
| 档位门禁 | `emily_core/workitem/langgraph_engine/agent/fallback_policy.py` `resolve` / `register_dynamic_read_tools` | 只读 MCP 工具登记进兜底白名单；写类受 `assert_write_allowed` 约束 |
| 一致性检查 | `emily_core/infrastructure/tools_consistency.py` `_check_tool_registry` | V13 排除 `handler_module=mcp:*`，避免动态工具误报 |

## 六、回归背景（本类立项动因）

接入 `web_search` 时暴露的**半接线缺陷**，是 MCP-03/04/05/07 的回归靶点：

1. **仅内存注册**：`load_mcp_tools` 只写内存 `_business_flow_tools`，未写 `tool_registry` 表 → 被 `build_tool_specs` 的 fail-closed 过滤 → **LLM 完全看不到**该工具，普通对话无法触发。
2. **档位门禁拦截**：即使补上 DB 行，`SessionLoop._execute_business_tool` 仍用 `FallbackPolicy.resolve` 白名单裁剪读工具 → 未登记的 MCP 只读工具被拒（"档位不可用"）。

> 判定口径：**连通性 PASS ≠ 可用性 PASS**。MCP-01/02 通只说明"管道通"，必须 MCP-03~07 全通过才算"普通对话可用"。

## 七、执行命令参考

```powershell
# 前置：确认容器健康、取真实用户（禁止伪造 sender_id）
docker compose -f docker-compose-napcat.yml ps
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, level FROM users WHERE is_deleted=false AND status='active' ORDER BY level DESC LIMIT 10;"

# MCP-01 在线探测（通道 B）
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18080/api/v1/console/mcp/probe?name=web_search" | ConvertTo-Json -Depth 4

# MCP-03 注册核对（通道 E）
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, category, permission_flag, is_active, handler_module FROM tool_registry WHERE handler_module LIKE 'mcp:%' ORDER BY id;"

# MCP-04 装配可见（通道 E：日志 + 流量）
docker logs emily-core --since 3m 2>&1 | Select-String "load_mcp_tools|build_tool_specs: 43"
docker exec mitmproxy sh -c "grep -c ddg_search /app/logs/llm_trace.jsonl"

# MCP-05/06 对话触发（通道 A）—— MCP-06 自 2026-09-14 起为反向断言（访客无 MCP 权限）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我上网搜索一下：2026年建筑业有哪些新政策？" --sender "王建国"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我上网搜索一下：装配式建筑最新的国家标准有哪些？" --sender "周文斌"

# MCP-10 访客不可见 MCP 工具（通道 E：装配断言，对照 L1 / L3 / L5）
docker exec emily-core python -c "from emily_core.session.fetchers.fetch_available_tools import fetch; print('L1', [t['api_id'] for t in fetch({'level':1})]); print('L3', [t['api_id'] for t in fetch({'level':3}) if t['api_id'].startswith('ddg_')])"

# MCP-09 一致性检查
docker exec -e PYTHONPATH=/app -e EMILY_DATABASE_URL=postgresql://emily:emily_secret_2026@emily-postgres:5432/emily emily-core python /app/scripts/check_tools_consistency.py

# 改代码后热更新：清缓存 + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core
```

## 八、口径变更记录

| 日期 | 用例 | 变更 | 原因 |
|------|------|------|------|
| 2026-09-14 | `MCP-06` | 预期反转：L1 访客**不再**可触发 `ddg_search` | 业务口径变更——**访客用户不应有 MCP 调用权限**；实现见 `fetch_available_tools.fetch` 的 L1 过滤，新增装配断言 `MCP-10` 与 05 类 `FR-G10` |
| 2026-09-14 | `MCP-07` | 适用面由「任意档位」收窄为「L2 及以上」 | 同上：L1 已不持有 MCP 工具，该用例不再覆盖 L1 |
