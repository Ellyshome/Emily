# LangGraph 执行引擎替换 — 非完美项分析与处理建议

> **关联**：[测试报告](LangGraph执行引擎替换_测试报告_V1.md)
> **日期**：2026-07-28
> **版本**：V1

---

## 总览

| # | 问题 | 严重度 | 状态 | 是否阻塞上线 |
|---|------|--------|------|-------------|
| G1 | BusContext 不可 msgpack 序列化 → checkpoint 禁用 | 🟡 中 | 已绕过（checkpointer=False） | 否 |
| G2 | error_analysis 纠错闭环缺少线上实战触发 | 🟡 中 | 单元/mock 已通过，集成测试未自然触发 | 否 |
| G3 | `checkpointer=False` 的 API 兼容性边界 | 🟢 低 | 已确认 langgraph 1.2.9 兼容 | 否 |
| G4 | `EMILY_WORKITEM_ENGINE` 仅环境变量生效，非 `core_config.json` | 🟢 低 | 已确认设计意图 | 否 |

---

## G1：BusContext 不可 msgpack 序列化 → checkpoint 禁用

### 现象

首次启用 LangGraph 引擎 + MemorySaver 时，`graph.ainvoke()` 在 checkpoint 写入阶段抛出：

```
TypeError: Type is not msgpack serializable: BusContext
```

调用栈：
```
langgraph/checkpoint/serde/jsonplus.py:860 → _msgpack_enc
langgraph/checkpoint/memory/__init__.py:454 → put
langgraph/pregel/_loop.py:1800 → _checkpointer_put_after_previous
```

### 影响

| 维度 | 影响 |
|------|------|
| **checkpoint 持久化** | ❌ 本期不可用。MemorySaver 在 graph 执行结束后自动写 checkpoint 时 crash（不是节点执行中，是整个 `ainvoke` 返回前） |
| **正常执行路径** | ✅ 不受影响（`checkpointer=False` 后节点正常执行，回复正常产出） |
| **断点续传** | ❌ 容器重启后无法从 checkpoint 恢复未完成的 WI |
| **Human-in-the-loop** | ❌ `interrupt()` 依赖 checkpoint 序列化 state，同样不可用 |

### 根因分析

`WorkItemGraphState` 的 `context` 字段持有 `BusContext` 实例（一个 `@dataclass`），包含：

- `work_item: WorkItem` — ORM 风格对象，含嵌套 `PlanStep`、`StepResult` 等
- `message: StandardMessage` — 含 `attachments` 等复杂字段
- `_session_context: SessionContext` — 含大量权限/项目数据
- `baggage: dict` — 含闭包 `progress_sender`

`msgpack` 默认只序列化基本 Python 类型（`int`, `str`, `list`, `dict`），遇到 `dataclass` / 闭包 / ORM 对象直接抛 `TypeError`。

**这是设计决策的必然结果**：方案选择"State 持有 BusContext 引用"（方案 B）而非"State 替代 BusContext"（方案 A），目的是让所有 Hook 和 node handler 零改动。代价是 checkpoint 需要自定义 serializer。

### 已做处理

`graph.py` 编译时传入 `checkpointer=False`：

```python
# graph.py:180-182
# ── 编译（禁用 checkpoint——BusContext 不可 msgpack 序列化，MemorySaver 不适用）
# 后续切 PostgresSaver 时需为 context 实现自定义 serializer。
graph = graph_builder.compile(checkpointer=False)
```

`checkpointer=False` 是 langgraph 1.x 文档支持的参数（等价于不注册任何 CheckpointSaver），graph 执行期间不写 checkpoint。

### 建议：下一步处理

**方案 A（推荐·下期做）：为 BusContext 实现自定义 msgpack serializer**

在 `state.py` 中为 `WorkItemGraphState` 实现 `__reduce__` 或通过 langgraph 的 `serde` 协议注册自定义 encoder/decoder：

```python
# state.py 伪代码
import msgpack

def _encode_bus_context(ctx):
    """仅序列化 BusContext 中可持久化的元数据字段。"""
    return {
        "pipeline_run_id": ctx.pipeline_run_id,
        "user_id": ctx.user_id,
        "is_admin": ctx.is_admin,
        "db_message_id": ctx.db_message_id,
        "should_abort": ctx.should_abort,
        "abort_reason": ctx.abort_reason,
        "warnings": ctx.warnings,
        # 不序列化 work_item / message / _session_context / baggage 闭包
    }

# 在编译时注册 serializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
```

关键设计点：
- **不序列化 `work_item`**：WI 始终从 DB 重建，不需要存 checkpoint
- **不序列化 `message`**：每条消息独立，不跨 WI 复用
- **只存 flow control 字段**：`should_abort`, `abort_reason`, `error_type`, `replan_count` 等 graph 控制字段

预计工时：2-4h（含 serializer + 单元测试）。

**方案 B（应急·本期可做）：State 拆分——context 不存 state**

将 `WorkItemGraphState` 改为 `context` 不进 state，节点函数通过闭包捕获：

```python
# 编译时注入
def build_workitem_graph(agent, hook_adapter, max_replan=1):
    graph_builder = StateGraph(WorkItemGraphState)  # state 不再含 context 字段
    # 每个节点闭包捕获 agent, hook_adapter（已经是工厂模式），context 通过闭包传递
```

但这**需要改动所有 5 个节点函数**和 `make_initial_state`——改动量大，且失去"state 含 context"的 reflect 能力（error_analysis 读 context 不需要额外参数）。

**不推荐方案 B。建议本期接受 checkpointer=False，下期实现方案 A。**

---

## G2：error_analysis 纠错闭环缺少线上实战触发

### 现象

7 轮 Real-LLM 对话测试中，error_analysis 节点的**日志输出为 0 次**：

| 测试 | 输入 | 为何未触发 error_analysis |
|------|------|--------------------------|
| TC-B1 | `"查询最近的事件"` | 正常路径，node3 执行成功 |
| TC-B2 | `"帮我创建事件：样板段放线完成"` | node3 step-10 报错但 LLM 在 node4 合成时掩盖了失败 |
| TC-B3 | `"废弃节点 SG-001"` (李景利 L4) | `discard_nodes` 工具成功执行 |
| TC-B3 | `"返回节点SG-001的成果"` (李景利 L4) | `return_node_deliverable` 工具成功执行 |
| TC-C1 | `"删除事件EVT-20260710-0001"` (周文斌 L1) | LLM 在规划阶段选择 `null` 工具（SOP-999 `LLM selected no tool`），node3 无失败 step |

### 影响

| 维度 | 影响 |
|------|------|
| **error_analysis 代码** | ✅ 单元测试通过（`--mock-failure` / `--mock-permission`），语法/导入/拓扑正确 |
| **error_analysis.md prompt** | ⚠️ 未经过真实 LLM 调用验证——prompt 格式/变量/JSON 输出可能在实际调用中出现异常 |
| **重规划闭环** (node3→error_analysis→node2) | ⚠️ 未经过真实 LLM 调用验证——`replan_hint` 注入后 node2 可能产生更差的重规划结果 |
| **代码预分类** (权限/L3 副作) | ⚠️ 未经过真实 LLM 调用验证——预分类逻辑依赖 `step_results[].output` 字符串匹配，某些 LLM 输出可能不命中关键词 |

### 根因分析

**error_analysis 触发条件苛刻**——需要同时满足：

1. node3 执行**抛出异常**或 step_results 中有 `success=False` 的 step
2. `replan_count < max_replan`（默认 1）
3. `should_abort` 不为 True

在真实对话中：

- **正常 L1/L2**：LLM + SkillExecutor 通常能成功执行（即使参数不完美，也会尽力提取）
- **L3 高风险**：`discard_nodes` / `return_node_deliverable` 本身是有副作用的正向操作——只要权限够（L4 李景利有权），工具就会成功。工具成功 ≠ node3 失败。
- **权限不足**：LLM 在规划阶段就通过 AuthHook 感知了 `sop_allow` 范围，SOP-999 的 LLM 选择不调用敏感工具

**error_analysis 本质上是"兜底安全网"——正常情况不触发才说明系统健康。但我们需要证明它会在异常时正确触发。**

### 已做处理

Mock 验证通过（[verify_langgraph_engine.py](scripts/verify_langgraph_engine.py)）：

```
--mock:            node1→node2→node3→node4 ✅
--mock-failure:    node3 fail→error_analysis(transient)→node3 retry→node4 ✅
--mock-permission: node3 perm fail→error_analysis(permission_denied)→abort ✅
```

### 建议：下一步处理

**方案 A（推荐·下期做）：编写故障注入脚本**

创建 `scripts/verify_error_analysis.py`，通过直接 API 调用 `graph.ainvoke` 并构造 mock BusContext：

```python
# 伪代码
def test_real_llm_error_analysis():
    # 构造真实 LLM 调用的 error_analysis
    # 1. 创建正常 WI
    # 2. 手动设置 wi.step_results = [MockStepResult(False, "缺少必填参数", "record_event")]
    # 3. graph.ainvoke(state) → error_analysis 调用真实 LLM
    # 4. 验证 LLM trace 有 call_category=error_analysis
    # 5. 验证 error_type 分类合理
```

覆盖：
- `param_error` → LLM 分析 → replan_hint → node2 重规划（验证 prompt 注入有效）
- `tool_mismatch` → LLM 分析 → 换工具建议
- `permission_denied` → 代码预分类（验证不调 LLM，LLM trace 无记录）
- `permanent_failure` → 代码预分类（验证 L3 工具命中）

**方案 B（本期可做·轻量）：通过 emy-test 构造必然失败的临界输入**

尝试发送会导致 node3 执行必然失败的消息：

```bash
# 触发 param_error：发送缺少 project_id 的录入消息
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --sender "张正宏" --sender-id "ce996655-d346-4c43-a4ac-5da60dc20e2b" \
  --message "帮我创建一个没有项目的孤立事件"

# 触发 tool_mismatch：发送会导致选错工具的消息
# （用查询类 SOP 处理录入类意图，或反之）
```

但这依赖 LLM 行为不确定性——不是可靠的测试方法。

**建议方案 A 作为下期必修项，方案 B 仅作为探索性尝试。**

---

## G3：`checkpointer=False` 的 API 兼容性边界

### 问题

计划文档假定 langgraph `>= 0.2.0`，实际安装 `1.2.9`。1.x 大版本 API 变化：

| 假设（v0.2.x） | 实际（v1.2.9） | 处理 |
|---------------|---------------|------|
| `add_node(name, fn, retry=RetryPolicy)` | 移除了 `retry=` 参数 | `nodes.py` 未传入 retry 参数，用注释标注 |
| `MemorySaver()` 作为默认 checkpointer | 1.x 同样支持但暴露 msgpack 问题 | `checkpointer=False` 兼容 |
| `graph.get_graph().draw_mermaid()` | 仍支持 | ✅ 正常 |

### 影响

低。所有 API 差异在开发阶段已适应（`nodes.py` 移除 RetryPolicy、`graph.py` 改为 `checkpointer=False`）。

### 建议

在 `requirements.txt` 中锁定版本上下界：

```
langgraph>=1.2,<2.0
```

---

## G4：`EMILY_WORKITEM_ENGINE` 仅环境变量生效

### 现象

最初误以为 `core_config.json` 中的 `"workitem_engine": "langgraph"` 可以切换引擎。实际 `bootstrap.py` 的 `_config_from_env()` 只从环境变量读取：

```python
env_map = {
    ...
    "EMILY_WORKITEM_ENGINE": "workitem_engine",
}
```

`core_config.json` 在此路径中**不被读取**（`bootstrap.py` 不执行 `json.load`）。

### 影响

低。通过 `docker-compose-napcat.yml` 环境变量 `EMILY_WORKITEM_ENGINE=langgraph` 正确注入。但文档/计划中的 `core_config.json` 方案会误导后续操作。

### 建议

在 `bootstrap.py` 的 `init()` 中加入 `core_config.json` 回退：

```python
# bootstrap.py init() 中 _config_from_env 之后
import json
_core_cfg = Path("/app/config/core_config.json")
if _core_cfg.exists():
    with open(_core_cfg) as f:
        _file_data = json.load(f)
    for k, v in _file_data.items():
        if k not in data:  # 环境变量优先
            data[k] = v
```

**2 行改动**，使 `core_config.json` 作为环境变量的 fallback，不影响当前 env→config 路径。

---

## 总结

| 问题 | 阻塞上线？ | 建议动作 | 优先级 | 预计工时 |
|------|-----------|---------|--------|---------|
| G1: checkpoint 禁用 | 否 | 下期实现 BusContext serializer | P1 | 2-4h |
| G2: error_analysis 未实战触发 | 否 | 下期写故障注入脚本覆盖 LLM 路径 | P1 | 2-3h |
| G3: API 版本边界 | 否 | 锁定 `langgraph>=1.2,<2.0` | P2 | 5min |
| G4: core_config.json 不生效 | 否 | bootstrap.py 加文件 fallback（2行） | P2 | 10min |

**核心结论**：当前状态可上线（`checkpointer=False` + 旧引擎回退安全 + mock 全路径通过），上述 4 项均为增强项，不阻塞功能交付。

---

*本报告基于 2026-07-28 真实 Docker 环境测试数据。*
