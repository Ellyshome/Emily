# BUG-03: WAITING_FOR_INPUT 流程端到端适配缺口

| 属性 | 值 |
|------|-----|
| **缺陷编号** | BUG-03 |
| **严重级别** | P2 — 多轮对话续接功能框架已就绪但无法触发（2 个子问题） |
| **发现日期** | 2026-07-29 |
| **发现方式** | emy-test 生产环境实战测试 + 静态代码审查 |
| **影响范围** | 多轮对话续接特性（`多轮对话续接_计划_V1`）的端到端链路 |
| **责任人** | 待指定 |

---

## 现象

`多轮对话续接_计划_V1` 已实现完整的框架链路：

```
node3 检测 needs_input → WAITING_FOR_INPUT → SessionAgent 挂起 → 
用户回复 → 续接判断 → 复用 WI → 注入 additional_input → 恢复执行
```

状态机、scheduler 分支、SessionAgent fork 均已通过静态验证（编译/导入/状态转换断言全部通过），但端到端测试中**无法触发 `WAITING_FOR_INPUT`**，原因有两个独立的代码缺口。

---

## 子问题 A：无工具 handler 返回 needs_input

### 描述

node3 的 `WAITING_FOR_INPUT` 检测在 [workitem_agent.py L452-L460](file:///d:/app/Emily/emily-core/emily_core/workitem/workitem_agent.py#L452-L460)：

```python
# 多轮续接：检测最后一个 step 是否请求用户输入
last_sr = step_results[-1] if step_results else None
if last_sr:
    output = getattr(last_sr, "output", None)
    if isinstance(output, dict) and output.get("needs_input"):
        wi.question = output.get("question", "")
        ...
        wi.state = WorkItemState.WAITING_FOR_INPUT
        return
```

此检测期望工具 handler 返回一个包含 `needs_input: True` 和 `question` 的 dict。

**当前所有已实现的工具 handler 均不存在此字段**。以三个核心业务工具为例：

| 工具 | 文件 | 返回字段 |
|------|------|----------|
| `record_event` | [event_tool.py](file:///d:/app/Emily/emily-core/emily_core/tools/event_tool.py) | `success`, `object_type`, `object_id`, `reply`, `error_code`, `needs_review` |
| `record_task` | [task_tool.py](file:///d:/app/Emily/emily-core/emily_core/tools/task_tool.py) | 同上 |
| `record_meeting` | [meeting_tool.py](file:///d:/app/Emily/emily-core/emily_core/tools/meeting_tool.py) | 同上 |

工具 handler 接口定义在 [business_flow_tools.py](file:///d:/app/Emily/emily-core/emily_core/tools/business_flow_tools.py) 中，返回 `dict`，但未在接口文档中声明 `needs_input` / `question` 为可选字段。

### 典型需求场景（哪些工具应该返回 needs_input）

| 场景 | 工具 | 何时返回 needs_input |
|------|------|---------------------|
| 项目名模糊 | record_event | LLM 无法从上下文解析具体项目，需要用户指定 |
| 负责人缺失 | record_task | 用户说"帮我建个任务"但未说明指派给谁 |
| 日期缺失 | record_event | 事件描述缺少时间信息 |
| 确认性询问 | query_project | 查询返回多项结果，需用户选择其中一项 |

---

## 子问题 B：output 类型强制转换为 str，needs_input 检测永远为 False

### 描述

**这是比子问题 A 更根本的代码缺陷。** 即使某个工具 handler 返回了包含 `needs_input` 的 dict，也无法通过 `node3_execute` 的检测，因为所有执行路径都将 `output` 强制转换为字符串。

### 强制转换位置（共 3 处）

**位置 1**：[workitem_agent.py L672](file:///d:/app/Emily/emily-core/emily_core/workitem/workitem_agent.py#L669-L677) — `_real_execute` 路径：

```python
output = handler_dict.get("reply", step.description)
success = handler_dict.get("success", True)

sr = StepResult(
    step_id=step.step_id,
    success=success,
    output=str(output),          # ← 强制 str() 转换！dict → 字符串
    tool_calls=[tool_call],
    db_results=db_results,
    rag_results=rag_results,
    business_data=handler_dict,
)
```

**位置 2**：[skill/executor.py L177](file:///d:/app/Emily/emily-core/emily_core/skill/executor.py#L175-L186) — Skill 路径（静态步骤）：

```python
sr = StepResult(
    step_id=step.id,
    success=success,
    output=str(output),          # ← 强制 str() 转换
    ...
    business_data=handler_dict,
)
```

**位置 3**：[skill/executor.py L479](file:///d:/app/Emily/emily-core/emily_core/skill/executor.py#L477-L488) — Skill 路径（动态步骤）：

```python
sr = StepResult(
    step_id=step.id,
    success=success,
    output=str(output),          # ← 强制 str() 转换
    ...
    business_data=handler_dict,
)
```

### 为什么检测永远失败

`node3_execute` 的检测代码（[workitem_agent.py L455-L456](file:///d:/app/Emily/emily-core/emily_core/workitem/workitem_agent.py#L455-L456)）：

```python
output = getattr(last_sr, "output", None)
if isinstance(output, dict) and output.get("needs_input"):
```

即使 handler 返回 `{"reply": {"needs_input": True, "question": "..."}}`，到了 `StepResult` 中 `output` 已被转为 `str({"needs_input": True, ...})`，成了字符串 `"{'needs_input': True, ...}"`。`isinstance(output, dict)` **永远为 False**。

### 此外：类型注解不一致

[StepResult 定义](file:///d:/app/Emily/emily-core/emily_core/workitem/pipeline/interfaces/execution.py#L115)：

```python
@dataclass
class StepResult:
    step_id: str
    success: bool = True
    output: str = ""             # ← 类型标注为 str
    ...
```

`output` 字段的类型标注就是 `str`，但 `node3_execute` 中用 `isinstance(output, dict)` 来检测——即使类型系统层面就是不兼容的。Python 不会在运行时校验类型注解，但代码意图自相矛盾。

---

## 修复方案

### 推荐方案：通过 business_data 传递 needs_input（零破坏性）

**核心思路**：不改 `output: str` 的类型约定，而是从 `business_data`（已经是 `dict` 类型）读取 `needs_input` 信号。`business_data` 在所有执行路径中都不被 `str()` 转换，且语义上适合承载业务级别的控制信号。

**修改位置 1**：[workitem_agent.py L452-L460](file:///d:/app/Emily/emily-core/emily_core/workitem/workitem_agent.py#L452-L460) — 改检测逻辑：

```python
# 多轮续接：检测最后一个 step 是否请求用户输入（从 business_data 读取）
last_sr = step_results[-1] if step_results else None
if last_sr:
    bd = getattr(last_sr, "business_data", {}) or {}
    if isinstance(bd, dict) and bd.get("needs_input"):
        wi.question = bd.get("question", "") or bd.get("reply", "")
        logger.info("WI %s node3: WAITING_FOR_INPUT — question=%s", wi.id, wi.question[:60])
        wi.state = WorkItemState.WAITING_FOR_INPUT
        return  # 跳过 Guardian 审核和 step_results 收集
```

**修改位置 2**：在 `BusinessFlowTool` 接口文档中声明可选协议：

```python
"""
工具 handler 可选返回字段（按需提供）：
  needs_input: bool  — 设为 True 时，node3 将 WI 挂起到 WAITING_FOR_INPUT
  question: str       — 向用户提问的内容（与 needs_input 配对使用）
"""
```

**修改位置 3**：至少为 1-2 个高频工具添加 `needs_input` 返回示例（如 `record_event` 在参数缺失时）。

### 备选方案：改 output 类型为 Union[str, dict]

直接修改 `StepResult.output` 为 `Union[str, dict]`，并在 `_real_execute` 和 `SkillExecutor` 中移除 `str()` 转换。此方案更激进，可能影响所有 `output` 的消费者。

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `emily_core/workitem/workitem_agent.py` L455-L456 | `output` → `business_data`，改检测源 |
| `emily_core/tools/business_flow_tools.py` | 接口文档增加 needs_input / question 字段说明 |
| `emily_core/tools/event_tool.py` | （推荐）为参数缺失场景增加 needs_input 返回 |

---

## 验证方法

修复后需验证以下场景：

### 静态验证

```powershell
cd d:\app\Emily\emily-core
uv run python -c "
from emily_core.workitem.workitem_state import WorkItemState, TRANSITIONS
# 验证状态转换不变
assert WorkItemState.WAITING_FOR_INPUT in TRANSITIONS[WorkItemState.EXECUTING]
print('State machine: OK')
import compileall
compileall.compile_dir('emily_core', quiet=1)
print('Compile: OK')
"
```

### 端到端验证（需在工具适配后执行）

```powershell
# 发送一个参数不完整的录入请求
uv run python .claude/skills/emy-test/cli.py --managed \
  --qq 123456001 --sender 王建国 \
  --message "帮我记录一个事件"

# 预期：Emily 返回追问（如"请问是什么事件？在哪个项目？"），而非超时或失败
# 然后回复追问
uv run python .claude/skills/emy-test/cli.py --managed \
  --qq 123456001 --sender 王建国 \
  --message "样板段放线完成，翠湖庭院项目"

# 预期：继续执行并完成录入
```

### 日志验证

修复后检查 Docker 日志中应出现以下模式：

```
WI xxx node3: WAITING_FOR_INPUT — question=请问是在哪个项目？
Scheduler[xxx] WI xxx WAITING_FOR_INPUT: 请问是在哪个项目？
Session[xxx] WI xxx waiting for input: 请问是在哪个项目？
```

以及续接时的：

```
Session[xxx] continuing paused WI xxx with input: 样板段放线完成，翠湖庭院项目
```
