# RealGuardian轻量审核 — AI 执行计划

> **基于需求**：[RealGuardian轻量审核-实施计划.md](RealGuardian轻量审核-实施计划.md)
> **计划版本**：V1
> **目标**：实现极致轻量的 Guardian 输出审核模块，单次 LLM 调用，并进非阻塞，只标记不拦截，完成后清理全部残留 Mock/冷备代码

---

## 你的角色

你是 **Emily v0.6.0** 开发者。严格按以下步骤执行，逐步骤验证，验证不通过不进入下一步。所有操作在 `d:\app\Emily` 根目录下进行。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：`node1_intent`、`node2_plan`、`_real_execute`、`_llm_plan`、`node_handlers`、`_resolve_mode` 的签名和返回值类型不可变
2. **分层不可跳**：Guardian 在 WorkItemAgent 层内部，不穿透到 SessionAgent / SessionPool / PipelineBUS / EmilyCore
3. **不 import astrbot 包**：`emily_core` 不依赖任何 `astrbot.*` 或 AstrBot 运行时
4. **新增文件统一下划线命名**：`real_guardian.py`（非 `RealGuardian.py`）
5. **每步验证**：每个步骤的验证命令必须通过，否则停止并报告
6. **Python 环境**：用 `uv run python` 而非裸 `python`

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `LLMClient` | `emily-core/emily_core/infrastructure/llm/client.py` | `chat_json(system_prompt: str, user_message: str) -> dict` | 直接调用 — Guardian 的两个审核方法通过 `self._llm.chat_json()` 获取 LLM 判定结果 |
| `GuardianStepVerdict` | `emily-core/emily_core/workitem/pipeline/interfaces/execution.py:86-96` | dataclass — `verdict: str`, `reason: str`, `suggestions: list[str]` | 直接使用 — 审核发现问题后构建 `GuardianStepVerdict(verdict="FLAG", reason="...")` 写入 `StepResult.guardian` |
| `StepResult` | `emily-core/emily_core/workitem/pipeline/interfaces/execution.py:100-121` | dataclass — `.guardian: GuardianStepVerdict \| None`、`.output`、`.tool_calls`、`.rag_results`、`.db_results` | 读取字段构建审核 prompt |
| `WorkItem.add_warning()` | `emily-core/emily_core/workitem/workitem.py:97-99` | `add_warning(msg: str) -> None` — 追加到 `self.warnings` 列表 | 直接调用 — Guardian 发现问题后 `wi.add_warning(f"[{source}] {issue}")` |
| `WorkItem.warnings` | `emily-core/emily_core/workitem/workitem.py:62` | `list[str]` | node4 组装回复时从 `wi.warnings` 读取并追加到末尾 |
| `BusContext` | `emily-core/emily_core/workitem/pipeline/context.py` | `context.work_item`、`context.agent_reply`、`context.verified_reply` | 不新增字段，仅通过已有属性访问 |

### 架构决策

**为何不用 ABC 接口 + 模式切换？** 当前需求明确：Guardian 是极致轻量的单次 LLM 判定器，只有一个实现。ABC + mock/real/review/agent 四种模式的复杂度远大于收益。YAGNI 原则：LLM 可用则启用，不可用则跳过，不需要模式开关。

**为何不建 `real/` 子目录？** 只有一个 `real_guardian.py` 文件（~150行），不值得建目录。直接放在 `workitem/pipeline/` 下与 `context.py`、`hook.py`、`bus.py` 并列。

**为何保留 `GuardianStepVerdict` 但移除 `Guardian` ABC？** `GuardianStepVerdict` 是 StepResult 的已有字段类型，被 mock_execution.py 和 workitem_agent.py 引用。移除它会导致连锁改动。`Guardian` ABC 则只有 `MockGuardian` 一个实现者，删除 MockGuardian 后接口无存在意义。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| 数据类 | `emily-core/emily_core/workitem/pipeline/interfaces/execution.py:86-96` | `@dataclass` + `field(default_factory=list)` 风格 |
| Logging | `emily-core/emily_core/workitem/workitem_agent.py:38` | `logger = logging.getLogger("emily.xxx")` 命名前缀 |
| 异常处理 | `emily-core/emily_core/workitem/workitem_agent.py:210-215` | `try/except Exception` → `logger.error/warning` → 降级处理 |
| LLM 调用 | `emily-core/emily_core/workitem/workitem_agent.py:211` | `data = await self._llm.chat_json(prompt, user_message)` |
| Module docstring | `emily-core/emily_core/workitem/pipeline/mocks/mock_guardian.py:1-4` | 第一行 """简要说明""" |

---

## Phase 1: 创建 RealGuardian 类 + 接口清理

**前置检查**：此阶段无依赖，可直接开始。

**交付物**：`real_guardian.py` 就位，`interfaces/guardian.py` 简化为仅保留 `GuardianVerdict` 枚举，`interfaces/__init__.py` 不再导出 `Guardian` ABC。

### Step 1.1: 创建 `real_guardian.py`

**目标**：新建 Guardian 具体类，包含两个审核方法 + system prompts。

**操作**：

在 `emily-core/emily_core/workitem/pipeline/` 下新建文件 `real_guardian.py`，写入以下完整代码：

```python
# emily-core/emily_core/workitem/pipeline/real_guardian.py
"""RealGuardian — 轻量 LLM 输出审核。只标记不拦截，并发非阻塞。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("emily.guardian")


# ══════════════════════════════════════════════════════════════════════════════
# System Prompts
# ══════════════════════════════════════════════════════════════════════════════

_STEP_REVIEW_PROMPT = """\
你是 Emily 系统的轻量输出审核员。你的任务是快速扫描一个执行步骤的输出，判断是否存在明显问题。

审核维度：
1. 虚构数据：输出中提到的编号、名称、数量是否可能为编造（与工具返回对比）
2. 错误引用：RAG 检索到的内容是否被错误转述或断章取义
3. 逻辑矛盾：输出结论是否与工具返回结果矛盾

上下文：
- 步骤ID: {step_id}
- 步骤输出: {output}
- 工具调用记录: {tool_info}
- RAG引用片段: {rag_info}

注意：
- 你是轻量扫描，不是深度审计。只报告明显、确定的问题。
- 如果没有发现问题，返回空列表。
- 不要建议如何修正，只指出问题。

返回 JSON 格式：
{{"issues": ["问题描述1", "问题描述2"]}}
如果无问题：{{"issues": []}}
"""

_REPLY_REVIEW_PROMPT = """\
你是 Emily 系统的轻量输出审核员。你的任务是快速扫描最终回复草稿，判断是否存在明显问题。

审核维度：
1. 幻觉：回复是否编造了用户没问过的事实、不存在的项目名/编号
2. 矛盾：回复是否与执行步骤的结果矛盾（如步骤失败却说成功）
3. 越权泄露：回复是否暴露了用户无权查看的信息
4. 敏感信息：是否包含疑似密钥、密码、内部IP等

上下文：
- 用户原始消息: {user_input}
- 匹配的SOP: {sop_id}
- 执行步骤摘要: {steps_summary}
- 最终回复草稿: {draft_reply}

注意：
- 你是轻量扫描，不是深度审计。只报告明显、确定的问题。
- 如果没有发现问题，返回空列表。
- 不要建议如何修正，只指出问题。

返回 JSON 格式：
{{"issues": ["问题描述1", "问题描述2"]}}
如果无问题：{{"issues": []}}
"""


# ══════════════════════════════════════════════════════════════════════════════
# Data Class
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class GuardianNote:
    """审核发现的问题标记。"""
    source: str = ""  # "step:step-02" | "reply"
    issues: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# RealGuardian
# ══════════════════════════════════════════════════════════════════════════════

class RealGuardian:
    """轻量输出审核 —— 单次 LLM chat_json，只标记不拦截。

    设计原则：
    - LLM 可用则自动启用；不可用时 review_*() 返回 None（静默跳过）
    - 只返回问题文本，不做 PASS/FLAG/REJECT 三态决策
    - node3 中通过 asyncio.create_task() 并进执行，不阻塞下一步
    """

    def __init__(self, llm_client: Any, config: Any = None) -> None:
        self._llm = llm_client
        self._config = config

    # ── node3 陪跑：审核单个步骤 ──

    async def review_step(self, step_result: Any) -> GuardianNote | None:
        """审核单个 StepResult。返回问题列表或 None。

        作为 asyncio.create_task() 的目标函数使用，
        在后台与下一步执行并行跑。
        """
        if not self._llm:
            return None
        prompt = self._build_step_prompt(step_result)
        try:
            data = await self._llm.chat_json(
                prompt,
                f"审核步骤: {getattr(step_result, 'step_id', '?')}",
            )
            issues: list[str] = data.get("issues", []) if isinstance(data, dict) else []
            if issues:
                return GuardianNote(
                    source=f"step:{getattr(step_result, 'step_id', '?')}",
                    issues=issues,
                )
            return None
        except Exception as e:
            logger.debug("Guardian review_step failed (silent skip): %s", e)
            return None

    # ── node4 出站：审核最终回复 ──

    async def review_reply(self, draft_reply: str, work_item: Any) -> GuardianNote | None:
        """审核最终回复草稿。返回问题列表或 None。

        node4 是最后一个节点，此处直接 await（无需后台化）。
        """
        if not self._llm or not draft_reply:
            return None
        prompt = self._build_reply_prompt(draft_reply, work_item)
        try:
            data = await self._llm.chat_json(
                prompt,
                f"审核回复: {draft_reply[:100]}",
            )
            issues: list[str] = data.get("issues", []) if isinstance(data, dict) else []
            if issues:
                return GuardianNote(source="reply", issues=issues)
            return None
        except Exception as e:
            logger.debug("Guardian review_reply failed (silent skip): %s", e)
            return None

    # ── prompt 构建 ──

    def _build_step_prompt(self, sr: Any) -> str:
        """构建 step 审核的 system prompt + 内嵌 user message。

        将上下文信息直接 format 进 system prompt，user_message 仅为简短标签。
        """
        output = (getattr(sr, "output", "") or "")[:1500]
        tool_info = ""
        for tc in getattr(sr, "tool_calls", []) or []:
            tool_info += (
                f"工具: {getattr(tc, 'tool_name', '?')}\n"
                f"输入: {json.dumps(getattr(tc, 'tool_input', {}), ensure_ascii=False)}\n"
                f"输出: {json.dumps(getattr(tc, 'tool_output', {}), ensure_ascii=False)}\n"
            )
        rag_info = ""
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in (getattr(rr, "chunks", []) or [])[:3]:
                rag_info += (
                    f"引用: 《{getattr(chunk, 'doc_name', '?')}》"
                    f"{getattr(chunk, 'content', '')[:200]}\n"
                )

        return _STEP_REVIEW_PROMPT.format(
            step_id=getattr(sr, "step_id", "?"),
            output=output,
            tool_info=tool_info[:2000],
            rag_info=rag_info[:1000],
        )

    def _build_reply_prompt(self, draft: str, wi: Any) -> str:
        """构建 reply 审核的 system prompt + 内嵌 user message。"""
        sop_id = getattr(wi, "sop_id", "") or "无"
        user_input = (getattr(wi, "user_input", "") or "")[:500]
        steps_summary = ""
        for sr in getattr(wi, "step_results", []) or []:
            steps_summary += (
                f"[{getattr(sr, 'step_id', '?')}] "
                f"{'OK' if getattr(sr, 'success', True) else 'FAIL'} "
                f"{(getattr(sr, 'output', '') or '')[:200]}\n"
            )

        return _REPLY_REVIEW_PROMPT.format(
            draft_reply=draft[:2000],
            sop_id=sop_id,
            user_input=user_input,
            steps_summary=steps_summary[:1500],
        )
```

**验证**：

```powershell
uv run python -c "from emily_core.workitem.pipeline.real_guardian import RealGuardian, GuardianNote; print('RealGuardian imported OK'); print('GuardianNote fields:', [f.name for f in __import__('dataclasses').fields(GuardianNote)])"
```

→ 预期输出：
```
RealGuardian imported OK
GuardianNote fields: ['source', 'issues']
```

**失败处理**：如果 import 失败，检查文件路径是否正确（`emily-core/emily_core/workitem/pipeline/real_guardian.py`），检查 Python 语法是否有误（`uv run python -m py_compile emily-core/emily_core/workitem/pipeline/real_guardian.py`）。

---

### Step 1.2: 简化 `interfaces/guardian.py` —— 移除 Guardian ABC

**目标**：`guardian.py` 从"ABC + 枚举"简化为"仅枚举"。`Guardian` ABC 类删除，`GuardianVerdict` 枚举保留。

**操作**：

1. 打开 `emily-core/emily_core/workitem/pipeline/interfaces/guardian.py`
2. 替换**全部内容**为以下代码：

```python
# emily-core/emily_core/workitem/pipeline/interfaces/guardian.py
"""Guardian 相关数据结构。"""

from __future__ import annotations

from enum import Enum


class GuardianVerdict(Enum):
    """守护审核标记。"""
    PASS = "pass"
    FLAG = "flag"
    REJECT = "reject"  # 保留，当前不使用
```

> 删除了 `ABC`、`abstractmethod`、`Guardian` 抽象类（L20-62）。`GuardianVerdict` 枚举保留不变。

**验证**：

```powershell
uv run python -c "from emily_core.workitem.pipeline.interfaces.guardian import GuardianVerdict; print(list(GuardianVerdict))"
```

→ 预期输出：
```
[<GuardianVerdict.PASS: 'pass'>, <GuardianVerdict.FLAG: 'flag'>, <GuardianVerdict.REJECT: 'reject'>]
```

**失败处理**：确认文件内容替换完整，没有残留的 `class Guardian(ABC):` 片段。

---

### Step 1.3: 更新 `interfaces/__init__.py` —— 移除 Guardian 导出

**目标**：`__init__.py` 不再导出已被删除的 `Guardian` ABC。

**操作**：

1. 打开 `emily-core/emily_core/workitem/pipeline/interfaces/__init__.py`
2. 第 21 行：将 `from .guardian import GuardianVerdict, Guardian` 替换为 `from .guardian import GuardianVerdict`
3. 第 37 行 `__all__` 列表中：删除 `"Guardian",` 这一项（保留 `"GuardianVerdict",`）

修改后的代码片段：

```python
# emily-core/emily_core/workitem/pipeline/interfaces/__init__.py L21
from .guardian import GuardianVerdict

# __all__ 中删除 "Guardian",  (原 L37)
__all__ = [
    "IntentType",
    "SubTask",
    "RouteDecision",
    "PlanStep",
    "ExecutionPlan",
    "ToolCallRecord",
    "RagChunk",
    "RagResult",
    "DbResult",
    "GuardianStepVerdict",
    "StepResult",
    "WorkAgent",
    "GuardianVerdict",
]
```

**验证**：

```powershell
uv run python -c "from emily_core.workitem.pipeline.interfaces import GuardianVerdict; print('GuardianVerdict OK')" && uv run python -c "from emily_core.workitem.pipeline.interfaces import Guardian; print('FAIL: should not import')" 2>&1
```

→ 预期输出：
```
GuardianVerdict OK
ImportError: cannot import name 'Guardian' from 'emily_core.workitem.pipeline.interfaces'
```

**失败处理**：确认 `interfaces/__init__.py` 中 `Guardian` 的 import 和 `__all__` 条目均已移除。

---

### Phase 1 最终验证

```powershell
uv run python -c "
from emily_core.workitem.pipeline.real_guardian import RealGuardian, GuardianNote
from emily_core.workitem.pipeline.interfaces.guardian import GuardianVerdict
from emily_core.workitem.pipeline.interfaces import GuardianVerdict as GV

# 确认 Guardian ABC 已移除
try:
    from emily_core.workitem.pipeline.interfaces import Guardian
    print('FAIL: Guardian ABC still importable')
except ImportError:
    print('OK: Guardian ABC removed')

print('RealGuardian:', RealGuardian)
print('GuardianNote:', GuardianNote)
print('GuardianVerdict:', list(GuardianVerdict))
print('Phase 1 complete')
"
```

→ 预期输出：
```
OK: Guardian ABC removed
RealGuardian: <class 'emily_core.workitem.pipeline.real_guardian.RealGuardian'>
GuardianNote: <class 'emily_core.workitem.pipeline.real_guardian.GuardianNote'>
GuardianVerdict: [<GuardianVerdict.PASS: 'pass'>, <GuardianVerdict.FLAG: 'flag'>, <GuardianVerdict.REJECT: 'reject'>]
Phase 1 complete
```

全部通过后进入 Phase 2。

---

## Phase 2: 修改 WorkItemAgent 接入 RealGuardian

**前置检查**（必须全部通过才进入此阶段）：

```bash
uv run python -c "from emily_core.workitem.pipeline.real_guardian import RealGuardian; print('OK')"
→ 预期输出：OK
```

```bash
uv run python -c "from emily_core.workitem.pipeline.interfaces.guardian import GuardianVerdict; print('OK')"
→ 预期输出：OK
```

**交付物**：`workitem_agent.py` 中 Guardian 逻辑完全替换：构造函数按 LLM 可用性启用、node3 并进非阻塞、node4 追加式标记。

### Step 2.1: 修改 import 区

**目标**：移除 `MockGuardian` 和 `GuardianVerdict` 的 import，新增 `RealGuardian` 和 `asyncio` 的 import。

**操作**：

1. 打开 `emily-core/emily_core/workitem/workitem_agent.py`
2. **第 22 行后**：在 `import time as _time` 之后插入 `import asyncio`
3. **第 30 行**：删除 `from .pipeline.interfaces.guardian import GuardianVerdict`（此行不再需要）
4. **第 32-36 行**：将

```python
from .pipeline.mocks import (
    MockPlanner,
    MockWorkAgent,
    MockGuardian,
)
```

替换为：

```python
from .pipeline.mocks import MockPlanner, MockWorkAgent
from .pipeline.real_guardian import RealGuardian, GuardianNote
```

修改后的 import 区（L20-36）应为：

```python
from __future__ import annotations

import logging
import time as _time
import asyncio

from .injector import KnowledgeInjector
from .pipeline.context import BusContext
from .pipeline.interfaces.routing import RouteDecision, SubTask
from .pipeline.interfaces.planning import ExecutionPlan, PlanStep
from .pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult, RagResult, RagChunk
from .pipeline.interfaces.auth import AuthResult, AuthDecision
from .pipeline.mocks import MockPlanner, MockWorkAgent
from .pipeline.real_guardian import RealGuardian, GuardianNote
```

**验证**：

```powershell
uv run python -c "from emily_core.workitem.workitem_agent import WorkItemAgent; print('WorkItemAgent imported OK')"
```

→ 预期输出：
```
WorkItemAgent imported OK
```

**失败处理**：检查 import 语句是否与上述完全一致；确认 `real_guardian.py` 存在且语法正确。

---

### Step 2.2: 修改构造函数 —— 替换硬编码 MockGuardian

**目标**：`self._guardian` 不再总是 `MockGuardian()`，改为 LLM 可用则 `RealGuardian`，不可用则 `None`。

**操作**：

1. 打开 `emily-core/emily_core/workitem/workitem_agent.py`
2. **第 120 行** `self._guardian = MockGuardian()` 替换为以下代码：

```python
# Guardian: LLM 可用则自动启用，不可用则为 None（静默跳过）
if self._llm:
    self._guardian = RealGuardian(llm_client=self._llm, config=config)
    logger.info("Guardian enabled: RealGuardian (lightweight LLM review)")
else:
    self._guardian = None
    logger.info("LLM not available — Guardian disabled (silent skip)")
```

3. **第 117-119 行**：确认 `self._planner = MockPlanner()` 和 `self._work_agent = MockWorkAgent()` 保留不变。

修改后的构造函数尾段（L117-120+）应为：

```python
        # Mock 大脑（Phase C 保留作为 fallback）
        self._planner = MockPlanner()
        self._work_agent = MockWorkAgent()

        # Guardian: LLM 可用则自动启用，不可用则为 None（静默跳过）
        if self._llm:
            self._guardian = RealGuardian(llm_client=self._llm, config=config)
            logger.info("Guardian enabled: RealGuardian (lightweight LLM review)")
        else:
            self._guardian = None
            logger.info("LLM not available — Guardian disabled (silent skip)")
```

**验证**：

```powershell
uv run python -c "
from emily_core.config import Config
from emily_core.workitem.workitem_agent import WorkItemAgent
# 无 LLM
agent = WorkItemAgent(config=Config())
print('Guardian (no LLM):', agent._guardian)
"
```

→ 预期输出：
```
Guardian (no LLM): None
```

**失败处理**：检查 `self._llm` 访问路径和 `RealGuardian` 导入是否正确。

---

### Step 2.3: 重写 `node3_execute` —— 并进非阻塞模式

**目标**：替换第 247-279 行整个 `node3_execute` 方法。Guardian 检查改为 `asyncio.create_task()` 并进执行。

**操作**：

1. 打开 `emily-core/emily_core/workitem/workitem_agent.py`
2. **第 247-279 行**：将 `node3_execute` 方法**完整替换**为以下代码：

```python
    async def node3_execute(self, context: BusContext) -> None:
        """Node 3 [执行+验收] —— 真实执行引擎 + Guardian 并进审核。"""
        wi = context.work_item
        if wi.execution_plan is None:
            return

        executor_mode = self._resolve_mode("executor")

        if executor_mode == "real":
            step_results = await self._real_execute(wi.execution_plan, context)
        else:
            step_results = await self._work_agent.execute(wi.execution_plan, context)

        # Guardian 并进审核：每个 step 的 review 作为后台 Task，
        # 在全部步骤执行完后 gather() 汇合，不阻塞主链路
        guardian_tasks: list[asyncio.Task] = []
        if self._guardian:
            for sr in step_results:
                task = asyncio.create_task(
                    self._guardian.review_step(sr),
                    name=f"guardian_step_{sr.step_id}",
                )
                guardian_tasks.append(task)

        # 汇合点：等待所有 guardian task 完成（大部分早已自然完成）
        if guardian_tasks:
            try:
                notes = await asyncio.gather(*guardian_tasks, return_exceptions=True)
                for sr, note in zip(step_results, notes):
                    if isinstance(note, GuardianNote) and note.issues:
                        for issue in note.issues:
                            wi.add_warning(f"[{note.source}] {issue}")
                        # 写入 StepResult 的 guardian 字段（已有结构）
                        sr.guardian = GuardianStepVerdict(
                            verdict="FLAG",
                            reason="; ".join(note.issues),
                        )
            except Exception as e:
                logger.warning("Guardian gather failed: %s", e)

        for sr in step_results:
            wi.add_step_result(sr)

        wi.llm_call_count += len(step_results)
        if step_results:
            context.agent_result = step_results[-1]
            context.agent_reply = step_results[-1].output
        logger.debug(
            "WI %s node3: %d steps, executor=%s guardian=%s",
            wi.id, len(step_results), executor_mode,
            "enabled" if self._guardian else "disabled",
        )
```

**验证**：

```powershell
uv run python -c "
import ast, inspect
from emily_core.workitem.workitem_agent import WorkItemAgent
src = inspect.getsource(WorkItemAgent.node3_execute)
# 确认不再有 guardian_mode
assert 'guardian_mode' not in src, 'FAIL: guardian_mode still in node3_execute'
# 确认有 create_task
assert 'create_task' in src, 'FAIL: asyncio.create_task not in node3_execute'
# 确认有 gather
assert 'gather' in src, 'FAIL: asyncio.gather not in node3_execute'
print('node3_execute OK')
"
```

→ 预期输出：
```
node3_execute OK
```

**失败处理**：检查替换是否完整——新方法必须覆盖旧的 L247-279。确认 `GuardianNote` 已从 `real_guardian` 导入。

---

### Step 2.4: 重写 `node4_summary` —— 追加式标记

**目标**：替换第 393-449 行整个 `node4_summary` 方法。移除旧的三态决策（PASS/FLAG/REJECT）逻辑，改为追加式标记。

**操作**：

1. 打开 `emily-core/emily_core/workitem/workitem_agent.py`
2. **第 393-449 行**：将 `node4_summary` 方法**完整替换**为以下代码：

```python
    async def node4_summary(self, context: BusContext) -> None:
        """Node 4 [成果总结] —— 组装回复 + Guardian 出站审核（追加标记）。"""
        wi = context.work_item
        summary = wi.to_summary()
        steps = summary.get("steps_executed", 0)
        rag_hits = summary.get("rag_hits", 0)
        tool_calls = summary.get("tool_calls", 0)

        # Phase C: executor_mode=real 时无 Mock 前缀
        executor_mode = self._resolve_mode("executor")
        mock_prefix = "" if executor_mode == "real" else "[Mock 模式] "

        if rag_hits > 0:
            rag_texts = []
            for sr in wi.step_results:
                for rag_result in getattr(sr, "rag_results", []):
                    for chunk in getattr(rag_result, "chunks", []):
                        doc_name = getattr(chunk, "doc_name", "") or "未知来源"
                        rag_texts.append(f"根据《{doc_name}》：{chunk.content}")
            if rag_texts:
                draft = mock_prefix + "根据知识库检索，找到以下相关信息：\n\n" + "\n".join(rag_texts[:5])
            else:
                draft = mock_prefix + f"已完成知识库查询，共找到 {rag_hits} 条相关信息。"
        elif tool_calls > 0:
            draft = (
                f"{mock_prefix}操作已完成！共执行 {steps} 个步骤，"
                f"调用 {tool_calls} 个工具，数据库操作 {summary.get('db_operations', 0)} 次。"
            )
        else:
            draft = mock_prefix + "Emily 已处理完毕。"

        # Guardian 出站审核 —— 只标记不拦截
        if self._guardian:
            try:
                note = await self._guardian.review_reply(draft, wi)
                if note and note.issues:
                    for issue in note.issues:
                        wi.add_warning(f"[reply] {issue}")
            except Exception as e:
                logger.debug("Guardian review_reply failed (silent skip): %s", e)

        # 将 warnings 追加到回复末尾（只标记，不替换）
        if wi.warnings:
            warning_text = (
                "\n\n⚠️ Emily 提醒（系统自动审核标记，供参考）：\n"
                + "\n".join(f"  • {w}" for w in wi.warnings[-5:])
            )
            wi.result_text = draft + warning_text
        else:
            wi.result_text = draft

        wi.llm_call_count += 1
        context.verified_reply = wi.result_text
        logger.debug(
            "WI %s node4: reply_len=%d guardian=%s warnings=%d",
            wi.id, len(wi.result_text),
            "enabled" if self._guardian else "disabled",
            len(wi.warnings),
        )
```

**验证**：

```powershell
uv run python -c "
import inspect
from emily_core.workitem.workitem_agent import WorkItemAgent
src = inspect.getsource(WorkItemAgent.node4_summary)
# 确认不再有 guardian_mode
assert 'guardian_mode' not in src, 'FAIL: guardian_mode still in node4_summary'
# 确认不再有 verdict_val
assert 'verdict_val' not in src, 'FAIL: verdict_val still in node4_summary'
# 确认有 warning_text 追加逻辑
assert 'warning_text' in src, 'FAIL: warning_text append not found'
# 确认有 wi.warnings 检查
assert 'wi.warnings' in src, 'FAIL: wi.warnings check not found'
print('node4_summary OK')
"
```

→ 预期输出：
```
node4_summary OK
```

**失败处理**：检查替换是否完整——新方法必须覆盖旧的 L393-449。确认 `getattr` 调用风格与原代码一致。

---

### Step 2.5: 添加 `GuardianStepVerdict` import

**目标**：node3 中需要 `GuardianStepVerdict`（来自 `interfaces/execution.py`）。

**操作**：

1. 打开 `emily-core/emily_core/workitem/workitem_agent.py`
2. **第 29 行**：在现有 `from .pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult, RagResult, RagChunk` 的 import 列表末尾追加 `, GuardianStepVerdict`

修改后（L29）：

```python
from .pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult, RagResult, RagChunk, GuardianStepVerdict
```

**验证**：

```powershell
uv run python -c "from emily_core.workitem.workitem_agent import WorkItemAgent; print('WorkItemAgent imported OK')"
```

→ 预期输出：
```
WorkItemAgent imported OK
```

**失败处理**：确认 `GuardianStepVerdict` 在 `interfaces/execution.py` 中存在（L86）。

---

### Phase 2 最终验证

```powershell
uv run python -c "
import inspect
from emily_core.workitem.workitem_agent import WorkItemAgent

src3 = inspect.getsource(WorkItemAgent.node3_execute)
src4 = inspect.getsource(WorkItemAgent.node4_summary)

# 所有旧逻辑已移除
assert 'guardian_mode' not in src3, 'node3: guardian_mode still present'
assert 'guardian_mode' not in src4, 'node4: guardian_mode still present'
assert 'MockGuardian' not in src3, 'node3: MockGuardian still present'
assert 'verdict_val' not in src4, 'node4: verdict_val still present'

# 新逻辑已就位
assert 'create_task' in src3, 'node3: create_task missing'
assert 'gather' in src3, 'node3: gather missing'
assert 'GuardianNote' in src3, 'node3: GuardianNote check missing'
assert 'warning_text' in src4, 'node4: warning_text missing'
assert 'wi.warnings' in src4, 'node4: wi.warnings check missing'

print('Phase 2 complete — all checks passed')
"
```

→ 预期输出：
```
Phase 2 complete — all checks passed
```

全部通过后进入 Phase 3。

---

## Phase 3: 清理残留代码

**前置检查**（必须全部通过才进入此阶段）：

```bash
uv run python -c "from emily_core.workitem.workitem_agent import WorkItemAgent; print('WorkItemAgent OK')"
→ 预期输出：WorkItemAgent OK
```

**交付物**：`MockGuardian` 文件删除、冷备 `.pyc` 删除、`mocks/__init__.py` 清理、`guardian_mode` 配置移除。

### Step 3.1: 删除 `mock_guardian.py`

**目标**：移除占位 Mock 实现。

**操作**：

```powershell
Remove-Item -Force "emily-core\emily_core\workitem\pipeline\mocks\mock_guardian.py"
```

**验证**：

```powershell
if (Test-Path "emily-core\emily_core\workitem\pipeline\mocks\mock_guardian.py") { Write-Output "FAIL: file still exists" } else { Write-Output "OK: mock_guardian.py deleted" }
```

→ 预期输出：
```
OK: mock_guardian.py deleted
```

---

### Step 3.2: 更新 `mocks/__init__.py`

**目标**：移除 MockGuardian 的 import 和导出。

**操作**：

1. 打开 `emily-core/emily_core/workitem/pipeline/mocks/__init__.py`
2. **第 12 行**：删除 `from .mock_guardian import MockGuardian`
3. **第 17 行** `__all__` 列表中：删除 `"MockGuardian",`

修改后的文件：

```python
# emily-core/emily_core/workitem/pipeline/mocks/__init__.py
"""Mock 实现 — Phase 0 占位模块。

每个 mock 返回确定性结果，标注 _source: "mock"。
总线跑通后，替换为真实实现只需改 __init__.py 中的一行 import。

已移除: MockAuthEngine（EmilyCore.auth 模块直接放行）, MockRouter（SessionAgent 意图识别替代）,
MockRiskGrader（workitem_agent.grade_risk 直接返回 L2）, MockGuardian（RealGuardian 已实现）
"""

from .mock_planning import MockPlanner
from .mock_execution import MockWorkAgent, MockWorkAgentQuery  # MockWorkAgentQuery: 冷备（暂无调用者）

__all__ = [
    "MockPlanner",
    "MockWorkAgent",
]
```

**验证**：

```powershell
uv run python -c "from emily_core.workitem.pipeline.mocks import MockPlanner, MockWorkAgent; print('mocks OK')"
```

→ 预期输出：
```
mocks OK
```

```powershell
uv run python -c "from emily_core.workitem.pipeline.mocks import MockGuardian; print('FAIL')" 2>&1
```

→ 预期输出：包含 `ImportError` —— 无法导入 MockGuardian。

---

### Step 3.3: 删除冷备 `.pyc` 文件

**目标**：移除 `agent/__pycache__/` 下的冷备 Guardian pyc 残骸。

**操作**：

```powershell
Remove-Item -Force "emily-core\emily_core\agent\__pycache__\guardian_agent.cpython-312.pyc" -ErrorAction SilentlyContinue; Remove-Item -Force "emily-core\emily_core\agent\__pycache__\guardian_review.cpython-312.pyc" -ErrorAction SilentlyContinue; Write-Output "pyc cleanup done"
```

**验证**：

```powershell
$files = Get-ChildItem -Path "emily-core\emily_core\agent\__pycache__" -Filter "guardian_*" -ErrorAction SilentlyContinue; if ($files) { Write-Output "FAIL: guardian pyc files still exist: $($files.Name)" } else { Write-Output "OK: no guardian pyc files" }
```

→ 预期输出：
```
OK: no guardian pyc files
```

---

### Step 3.4: 移除 `guardian_mode` 配置

**目标**：删除 `config.py` 中的 `guardian_mode` 字段和 `bootstrap.py` 中的环境变量映射。

**操作**：

1. 打开 `emily-core/emily_core/config.py`
2. **删除第 196-197 行**：

```python
    guardian_mode: str = "mock"
    """守护大脑模式: mock（MockGuardian 兜底）"""
```

3. 打开 `emily-core/emily_core/bootstrap.py`
4. **删除第 62 行**：删除 `"EMILY_GUARDIAN_MODE": "guardian_mode",`（注意保留行尾逗号与上下行一致不产生语法错误）

删除后的 env_map 对照区域（L59-64）应为：

```python
        # Phase C: Pipeline node brain mode switches
        "EMILY_EXECUTOR_MODE": "executor_mode",
        "EMILY_PLANNER_MODE": "planner_mode",
        "EMILY_AUTH_MODE": "auth_mode",
        "EMILY_RISK_MODE": "risk_mode",
```

**验证**：

```powershell
uv run python -c "
from emily_core.config import Config
c = Config()
assert not hasattr(c, 'guardian_mode') or c.guardian_mode is None, 'FAIL: guardian_mode still exists'
print('config.py OK')
"
```

→ 预期输出：
```
config.py OK
```

**失败处理**：如果 `hasattr` 检查失败，说明字段未完全删除。确认 `config.py` 中不再有 `guardian_mode` 字样。

---

### Phase 3 最终验证

```powershell
uv run python -c "
# 1. mock_guardian.py 不可导入
try:
    from emily_core.workitem.pipeline.mocks.mock_guardian import MockGuardian
    print('FAIL: mock_guardian still importable')
except (ImportError, ModuleNotFoundError):
    print('OK: mock_guardian removed')

# 2. mocks/__init__.py 不含 MockGuardian
from emily_core.workitem.pipeline.mocks import __all__ as mocks_all
assert 'MockGuardian' not in mocks_all, 'FAIL: MockGuardian still in __all__'
print('OK: mocks/__init__.py clean')

# 3. Guardian ABC 不可导入
try:
    from emily_core.workitem.pipeline.interfaces.guardian import Guardian
    print('FAIL: Guardian ABC still importable')
except ImportError:
    print('OK: Guardian ABC removed')

# 4. guardian_mode 配置不存在
from emily_core.config import Config
c = Config()
if hasattr(c, 'guardian_mode'):
    print('FAIL: guardian_mode still in config')
else:
    print('OK: guardian_mode removed from config')

print('Phase 3 complete')
"
```

→ 预期输出：
```
OK: mock_guardian removed
OK: mocks/__init__.py clean
OK: Guardian ABC removed
OK: guardian_mode removed from config
Phase 3 complete
```

全部通过后进入 Phase 4。

---

## Phase 4: 组装验证 + 烟雾测试

**前置检查**：

```bash
uv run python -c "from emily_core.workitem.workitem_agent import WorkItemAgent; from emily_core.workitem.pipeline.real_guardian import RealGuardian; print('all imports OK')"
→ 预期输出：all imports OK
```

**交付物**：端到端烟雾测试通过——Mock 全链路无 LLM Guardian 自动跳过、真实 LLM 时 Guardian 自动启用。

### Step 4.1: 无 LLM 烟雾测试（Guardian 自动跳过）

**目标**：无 LLM API key 时，Guardian 自动跳过，不影响现有全 Mock 编排链路。

**操作**：

执行 smoke_test.py（无 `--with-db`，全 Mock 路径）：

```powershell
uv run python scripts/smoke_test.py
```

**验证**：

→ 预期：所有测试用例通过，日志中出现以下行（之一）：
```
LLM not available — Guardian disabled (silent skip)
```
或
```
guardian=disabled
```

**失败处理**：如果 smoke_test 崩溃，检查 traceback 定位问题源。常见问题：
- `real_guardian.py` 中有语法错误 → `uv run python -m py_compile emily-core/emily_core/workitem/pipeline/real_guardian.py`
- import 链断裂 → 逐行执行上述 Phase 2 的 import 验证
- node3/node4 中 `self._guardian is None` 时调用方法 → 确认所有调用点都有 `if self._guardian:` 保护

### Step 4.2: 完整模块 import 链验证

**目标**：确认所有 Guardian 相关模块可正常导入，无循环引用。

**操作**：

```powershell
uv run python -c "
# 完整 import 链
from emily_core.workitem.pipeline.interfaces.guardian import GuardianVerdict
from emily_core.workitem.pipeline.interfaces import __all__ as ifaces_all
from emily_core.workitem.pipeline.mocks import __all__ as mocks_all
from emily_core.workitem.pipeline.real_guardian import RealGuardian, GuardianNote
from emily_core.workitem.workitem_agent import WorkItemAgent
from emily_core.config import Config
from emily_core import EmilyCore

# 检查接口导出
assert 'GuardianVerdict' in ifaces_all, 'FAIL: GuardianVerdict not exported'
assert 'Guardian' not in ifaces_all, 'FAIL: Guardian still exported'

# 检查 mocks 导出
assert 'MockGuardian' not in mocks_all, 'FAIL: MockGuardian still exported'
assert 'MockPlanner' in mocks_all, 'MockPlanner should still be exported'
assert 'MockWorkAgent' in mocks_all, 'MockWorkAgent should still be exported'

# 无 LLM 创建 EmilyCore
core = EmilyCore(Config())
print('EmilyCore created OK')

print('All checks passed')
"
```

→ 预期输出：
```
EmilyCore created OK
All checks passed
```

---

### Phase 4 最终验证

对 Guardian 模块做一次完整的构造和调用路径检查：

```powershell
uv run python -c "
from emily_core.config import Config

# 1. 无 LLM → Guardian 为 None
from emily_core.workitem.workitem_agent import WorkItemAgent
agent_no_llm = WorkItemAgent(config=Config())
assert agent_no_llm._guardian is None, 'FAIL: guardian should be None without LLM'
print('OK: no LLM → guardian is None')

# 2. 有 LLM → Guardian 为 RealGuardian 实例
from emily_core.infrastructure.llm.client import LLMClient
from emily_core.workitem.pipeline.real_guardian import RealGuardian
# 用假 LLM client（不会发真实请求）
class FakeLLM:
    async def chat_json(self, system_prompt, user_message):
        return {'issues': []}
agent_with_llm = WorkItemAgent(llm_client=FakeLLM(), config=Config())
assert isinstance(agent_with_llm._guardian, RealGuardian), 'FAIL: guardian should be RealGuardian with LLM'
print('OK: with LLM → guardian is RealGuardian')

# 3. review_step 返回 None（无问题）
import asyncio
note = asyncio.run(agent_with_llm._guardian.review_step.__wrapped__(agent_with_llm._guardian, None))
# Note: 由于 FakeLLM 返回空 issues，review_step 返回 None
print('OK: review_step callable')

# 4. node_handlers 返回 4 个节点
handlers = agent_no_llm.node_handlers()
assert len(handlers) == 4, f'FAIL: expected 4 handlers, got {len(handlers)}'
print('OK: 4 node handlers')

print('Phase 4 complete — all integration checks passed')
"
```

→ 预期输出：
```
OK: no LLM → guardian is None
OK: with LLM → guardian is RealGuardian
OK: review_step callable
OK: 4 node handlers
Phase 4 complete — all integration checks passed
```

---

## 阶段反思指令

每完成一个 Phase，在进入下一个 Phase 之前，执行以下反思：

1. **检查产物**：列出本 Phase 所有新建/修改/删除的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应 Phase，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "V1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成计划

---

## 附录 A: 改动文件总清单

| # | 文件 | 操作 | Phase |
|---|------|------|-------|
| 1 | `emily-core/emily_core/workitem/pipeline/real_guardian.py` | **新建** | P1 |
| 2 | `emily-core/emily_core/workitem/pipeline/interfaces/guardian.py` | **替换**（移除 ABC） | P1 |
| 3 | `emily-core/emily_core/workitem/pipeline/interfaces/__init__.py` | **修改**（移除 Guardian 导出） | P1 |
| 4 | `emily-core/emily_core/workitem/workitem_agent.py` | **修改**（import + 构造 + node3 + node4） | P2 |
| 5 | `emily-core/emily_core/workitem/pipeline/mocks/mock_guardian.py` | **删除** | P3 |
| 6 | `emily-core/emily_core/workitem/pipeline/mocks/__init__.py` | **修改**（移除 MockGuardian 导入导出） | P3 |
| 7 | `emily-core/emily_core/agent/__pycache__/guardian_agent.cpython-312.pyc` | **删除** | P3 |
| 8 | `emily-core/emily_core/agent/__pycache__/guardian_review.cpython-312.pyc` | **删除** | P3 |
| 9 | `emily-core/emily_core/config.py` | **修改**（删除 guardian_mode 字段） | P3 |
| 10 | `emily-core/emily_core/bootstrap.py` | **修改**（删除 EMILY_GUARDIAN_MODE 映射） | P3 |

**不碰的文件**（按需求明确排除）：
- `session/session_agent.py`
- `adapters/session/session_pool.py`
- `adapters/session/session_factory.py`
- `workitem/pipeline/bus.py`
- `workitem/pipeline/hook.py`
- `workitem/pipeline/context.py`
- `emily-core/emily_core/__init__.py`
- `hook_config.json`（本次不涉及 Hook）

---

## 附录 B: 设计决策记录

| 决策 | 理由 | 替代方案（含为什么拒绝） |
|------|------|------------------------|
| Guardian 不设模式开关 | LLM 可用则启用，不可用则跳过。模式开关（mock/real/review/agent）是为"可能有多种实现"设计的，此处只有一种 | 保留 mock/real 二态开关——拒绝：增加配置维度却不增加能力 |
| Guardian 为具体类非 ABC | 只有一个实现，不需要抽象接口。YAGNI | ABC + Mock/Real 两实现——拒绝：MockGuardian 已删除，ABC 无实现者 |
| `GuardianNote` 而非直接返回 `GuardianVerdict` | `GuardianVerdict` 是三态枚举（PASS/FLAG/REJECT），而新设计只需要"问题列表"和"来源标签"两个信息 | 复用 `GuardianVerdict`——拒绝：语义不匹配，会造成混淆 |
| 冷备 `.pyc` 一并删除 | 总线已深度重构，冷备代码的签名和调用路径已不可用。留之无益 | 保留 pyc 作参考——拒绝：pyc 不可读，参考价值为零 |
| `GuardianStepVerdict` 保留 | 被 StepResult.guardian 字段和 mock_execution.py 引用，属于 execution 协议层，非 Guardian 接口层 | 一并删除并重构 StepResult——拒绝：连锁改动过大，超出本次范围 |

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
