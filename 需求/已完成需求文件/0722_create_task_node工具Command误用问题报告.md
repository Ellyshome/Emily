# `create_task_node` 工具 Command 误用问题报告

> 日期：2026-07-22
> 发现背景：执行 [0722调度器半接线修复计划.md](0722调度器半接线修复计划.md) 时，修复 PeriodicNodeHandler 的同类问题（见 [docs/技术踩坑备忘录.md](../docs/技术踩坑备忘录.md) 2.14）后，发现 `handle_create_task_node` 工具存在完全相同的 Command 误用 bug。属 M14 LLM 工具范畴，超出调度器修复计划范围，单独报告。

## 现象

用户消息命中「创建任务」类 SOP，LLM 经 `chat_json` 输出 `{tool: "create_task_node", params: {...}}`，框架直调 `handle_create_task_node` handler，执行报错：

```
AttributeError: 'CreateTaskNodeCommand' object has no attribute 'node_id'
```

任务节点未创建，创建任务功能完全不可用。

## 根因

`NodeService.create_node` 签名要求 `cmd: CreateNodeCommand`（[node_service.py:132](../emily-core/emily_core/services/node_service.py)），内部多处直接访问 `cmd.node_id`（第 144/154/173/186/211/217 行等）：

- `CreateNodeCommand`（[node_commands.py:14](../emily-core/emily_core/services/node_commands.py)）含 `node_id` 必填字段（第 17 行）。
- `CreateTaskNodeCommand`（[node_commands.py:225](../emily-core/emily_core/services/node_commands.py)）是**独立 dataclass，未继承 `CreateNodeCommand`**，字段为 `project_id / node_name / responsible_user_id / deadline / parent_node_id / owner_dept_id / description / creator_id`，**无 `node_id`**。

`handle_create_task_node`（[node_task_tool.py:27-38](../emily-core/emily_core/tools/node_task_tool.py)）构造 `CreateTaskNodeCommand` 后直接调 `node_service.create_node(cmd)`，`create_node` 内访问 `cmd.node_id` 即抛 `AttributeError`。

```python
# node_task_tool.py 现状（有 bug）
cmd = CreateTaskNodeCommand(project_id=..., node_name=..., ...)
result = await node_service.create_node(cmd)   # ← create_node 访问 cmd.node_id 报错
```

## 影响范围

- **创建任务功能不可用**：M14 结构化输出路径（CLAUDE.md 约束 5：命中 SOP → `chat_json` → 框架直调 `BusinessFlowTool.handler`）下的 `create_task_node` 工具一旦被 LLM 选中即报错。
- **注册正常**：工具在 `register_all`（[registry.py:209](../emily-core/emily_core/tools/registry.py)）正常注册到 BusinessFlowToolRegistry（`_node_service` 就绪后），`permission_flag="write"`、`category="business"`。bug 仅在 handler 执行时暴露。
- **同类工具不受影响**：`submit_node_deliverable` / `confirm_node_deliverable` / `return_node_deliverable` / `query_my_nodes` 用各自的 Command（`SubmitNodeDeliverableCommand` 等），不调 `create_node`，无此问题。
- **`CreateTaskNodeCommand` 已无其他消费者**：PeriodicNodeHandler 已在本次调度器修复中改用 `CreateNodeCommand`，全仓 Grep 确认 `CreateTaskNodeCommand` 现仅 `node_task_tool.py` 一处使用。

## 修复方案

### 方案 A（推荐）：工具改用 CreateNodeCommand + generate_node_id

对齐 PeriodicNodeHandler 的修复模式（见 [periodic_node.py](../emily-core/emily_core/scheduler/jobs/periodic_node.py)）：

```python
# node_task_tool.py handle_create_task_node 改造
from ..services.node_commands import CreateNodeCommand
from ..services.node_batch import generate_node_id

project_id = params.get("project_id", "")
node_name = params.get("title", params.get("node_name", ""))
node_id = params.get("node_id", "") or generate_node_id(node_name, project_id)

cmd = CreateNodeCommand(
    project_id=project_id,
    node_id=node_id,
    node_name=node_name,
    responsible_user_id=params.get("executor_id", params.get("responsible_user_id", "")),
    deadline=params.get("deadline_at", ""),
    parent_node_id=params.get("parent_node_id", ""),
    owner_dept_id=params.get("owner_dept_id", "项目总"),
    remark=params.get("description", ""),
    creator_id=user_id,
    node_type="TASK",
)
result = await node_service.create_node(cmd)
```

要点：
- `node_id` 优先用 LLM 传入的 `params["node_id"]`（若用户指定了父节点挂载场景），否则 `generate_node_id` 自动生成 `NODE-{hash4}`。
- `description` → `remark`（CreateNodeCommand 无 description 字段）。
- `node_type="TASK"`（工具语义是创建 TASK 叶子节点）。
- 建议补幂等检查：`ProjectNodeRepo.get_by_node_id(node_id)` 已存在则跳过（与 PeriodicNodeHandler 一致），避免 LLM 重复调用产生 IntegrityError 或重复节点。

### 方案 B（顺带）：废弃 CreateTaskNodeCommand

方案 A 落地后 `CreateTaskNodeCommand` 全仓无消费者，可从 `node_commands.py` 删除，避免后续再被误用。删除前全仓 Grep 确认无引用。

### 方案 C（不推荐）：create_node 兼容 CreateTaskNodeCommand

在 `create_node` 开头 `getattr(cmd, 'node_id', '')` 为空时自动生成。**不推荐**——`create_node` 内 `node_type=getattr(cmd, 'node_type', 'WORK_PACKAGE')` 会把 TASK 创建误判为 WORK_PACKAGE，需额外补 `node_type` 兼容逻辑，污染核心方法、与 CreateNodeCommand 语义混淆。方案 A 改调用方更干净。

## 权限校验注意

`create_node` 对 `creator_id`（工具场景即 `user_id`）有校验（[node_service.py:139-176](../emily-core/emily_core/services/node_service.py)）：
- 责任人 / 创建人必须在 `users` 表存在，否则返回 `error_code=40002`。
- 非管理员（level < 5）仅「建设单位」人员可创建，否则 `error_code=40301`。

这是正常权限逻辑，非 bug。修复方案 A 后工具能正常走权限校验——管理员自动激活（`CONDITIONS_NOT_MET`），普通建设单位人员走 `NOT_ACTIVATED` 待审批。

## 涉及文件

| 文件 | 改动 |
|------|------|
| [tools/node_task_tool.py](../emily-core/emily_core/tools/node_task_tool.py) | `handle_create_task_node` 改用 `CreateNodeCommand` + `generate_node_id` + 幂等检查 |
| [services/node_commands.py](../emily-core/emily_core/services/node_commands.py) | （方案 B）删除 `CreateTaskNodeCommand` |

## 验证

- **单元**：emy-test 发「帮我创建任务：外墙真石漆施工，负责人刘大勇」类消息，命中创建任务 SOP → `create_task_node` 工具执行 → `project_nodes` 表新增 TASK 节点、`pipeline_execution_logs` 记录 success。
- **幂等**：同一任务名重复创建应跳过（`generate_node_id` 同名同项目同 node_id）。
- **权限**：普通用户（非建设单位）创建应返回 `40301`；管理员创建应自动激活。
- **回归**：`submit_node_deliverable` 等同类工具不受影响。

## 关联

- 调度器同源修复：[docs/技术踩坑备忘录.md](../docs/技术踩坑备忘录.md) 2.14、[docs/开发记录.md](../docs/开发记录.md) ADR-E10。
- `generate_node_id` 已在本次调度器修复中从 `node_batch._generate_node_id` 公开（去下划线），工具可直接 import。
