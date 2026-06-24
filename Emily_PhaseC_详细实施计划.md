# Emily Phase C 详细实施计划

> **版本**: v1.0  
> **日期**: 2026-06-24  
> **依据**: [Emily 主系统架构 §12.2](tem/0623Emily_主系统架构.md) Phase C 定义  
> **前置**: Phase B ✅ (SessionAgent 升级 + WorkItemAgent 规划 + KnowledgeInjector + Hook 鉴权)  
> **目标**: MockWorkAgent → 真实执行 + MockGuardian → 真实守护 + 集成验证

---

## 目录

1. [范围定义](#一范围定义)
2. [C-1: 配置开关扩展](#二c-1-配置开关扩展)
3. [C-2: PlanStep 增强 (tool_params)](#三c-2-planstep-增强-tool_params)
4. [C-3: 真实执行引擎 (MockWorkAgent → RealExecutor)](#四c-3-真实执行引擎-mockworkagent--realexecutor)
5. [C-4: GuardianReview 实现 Guardian ABC](#五c-4-guardianreview-实现-guardian-abc)
6. [C-5: GuardianAgent 接入 DeepAuditHook](#六c-5-guardianagent-接入-deepaudithook)
7. [C-6: RealAuthEngine + RealRiskGrader](#七c-6-realauthengine--realriskgrader)
8. [C-7: 依赖注入完善](#八c-7-依赖注入完善)
9. [C-8: 烟雾测试升级](#九c-8-烟雾测试升级)
10. [实施顺序与文件总览](#十实施顺序与文件总览)

---

## 一、范围定义

### 1.1 架构蓝图原文 (§12.2 Phase C)

```
Phase C: 全真实实现 + 集成验证 (执行层完整 → 全面就绪)

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

### 1.2 Phase C 替换矩阵

```
Phase C 替换:
  ✅ MockWorkAgent  → 真实执行引擎 (按 PlanStep 调用 M14 工具 handler)
  ✅ MockGuardian   → GuardianReview (轻量 reply/review) + GuardianAgent (深度审计)
  ✅ MockAuthEngine → RealAuthEngine (Phase B AuthHook 逻辑提升)
  ✅ MockRiskGrader → RealRiskGrader (基于意图类型/置信度)

Phase C 不变:
  ✋ MockRouter     → 已在 Phase B 被 SessionAgent._recognize_intent() 替换
  ✋ MockPlanner    → 已在 Phase B 被 WorkItemAgent._llm_plan() 替换
  ✋ 旧 agent/ 文件 → 不 import 到热路径；提取模式后保持不动
```

### 1.3 不做什么

- ❌ 不创建 `RealRouter`/`RealPlanner` 适配器类（Phase B 已完成）
- ❌ 不 import `agent/master_agent.py` 到热路径
- ❌ 不 import `agent/business_flow_agent.py` 到热路径（提取 M14 模式但不用类）
- ❌ 不修改 `agent/` 目录下任何文件

---

## 二、C-1: 配置开关扩展

**优先级**: 🔴 P0  
**依赖**: 无  
**预估**: 0.5 天

### 2.1 修改 `config.py`

在 `planner_mode` 之后新增：

```python
# ── Phase C: Pipeline 大脑模式开关 ──
executor_mode: str = "mock"
"""执行大脑模式: mock | real（需 EMILY_LLM_API_KEY 和 BusinessFlowToolRegistry）"""

guardian_mode: str = "mock"
"""守护大脑模式: mock | review | agent
   - mock:   永远 PASS（MockGuardian）
   - review: GuardianReview 单轮 LLM 调用（5s 超时）
   - agent:  GuardianAgent 多轮 ReAct 深度审计"""

auth_mode: str = "mock"
"""鉴权引擎模式: mock | real（需 SOPIntentRegistry）"""

risk_mode: str = "mock"
"""风险评估模式: mock | real"""

deep_audit_enabled: bool = False
"""深度审计 Hook 开关（before:wi_node4，独立于 guardian_mode）"""
```

### 2.2 验收

- [ ] `Config.from_dict({'executor_mode': 'real'})` 正确解析

---

## 三、C-2: PlanStep 增强 (tool_params)

**优先级**: 🔴 P0  
**依赖**: C-1  
**预估**: 0.5 天

### 3.1 问题

当前 `PlanStep` 有 `tool_name` 但没有 `tool_params`。真实执行需要知道调用工具时传什么参数。

### 3.2 修改 `interfaces/planning.py`

```python
@dataclass
class PlanStep:
    step_id: str
    description: str
    tool_name: str | None = None
    tool_params: dict = field(default_factory=dict)   # Phase C: handler 调用的参数
    expected_output: str = ""
    depends_on: list[str] = field(default_factory=list)
```

### 3.3 修改 `workitem_agent.py` 的 `_llm_plan()`

在 `_map_to_execution_plan()` 中，LLM JSON 输出的 steps 增加 `tool_params` 字段：

```python
steps.append(PlanStep(
    step_id=s.get("step_id", f"step-{i+1:02d}"),
    description=s.get("description", ""),
    tool_name=s.get("tool_name"),
    tool_params=s.get("tool_params", {}),   # Phase C
    expected_output=s.get("expected_output", ""),
    depends_on=s.get("depends_on", []),
))
```

同步更新 `_PLANNER_SYSTEM_PROMPT`，在 LLM 输出格式中增加 `tool_params`：

```
{{"step_id":"step-01","description":"...","tool_name":"record_event",
  "tool_params":{{"title":"...","event_type":"...","description":"..."}},
  "expected_output":"...","depends_on":[]}}
```

### 3.4 验收

- [ ] `PlanStep.tool_params` 默认空 dict
- [ ] LLM 计划中含 `tool_params` 的步骤正确反序列化

---

## 四、C-3: 真实执行引擎 (MockWorkAgent → RealExecutor)

**优先级**: 🔴 P0  
**依赖**: C-1, C-2, Phase B KnowledgeInjector  
**预估**: 3-4 天

### 4.1 设计

创建 `WorkItemAgent` 内嵌方法 `_real_execute()`，替代 `MockWorkAgent.execute()`。

核心流程：
```
ExecutionPlan.steps[i] (PlanStep)
  ├── 有 tool_name 且 tool_name 在 BusinessFlowToolRegistry
  │   → 调用 handler(tool_params)
  │   → 映射为 ToolCallRecord
  ├── 有 tool_name 但未注册
  │   → StepResult(success=False, error="工具未注册")
  └── 无 tool_name
      → StepResult(success=True, output=description)
```

### 4.2 修改 `workitem_agent.py`

#### `__init__` 增加依赖

```python
def __init__(self, injector=None, llm_client=None, config=None,
             # Phase C: 执行依赖
             business_flow_tools=None, guardian=None):
    ...
    self._business_flow_tools = business_flow_tools
    # Phase C: guardian 作为独立组件（GuardianReview 或 GuardianAgent）
    if guardian is not None:
        self._guardian = guardian
```

#### 新增 `_real_execute()` 方法

```python
async def _real_execute(self, plan: ExecutionPlan, context: BusContext) -> list[StepResult]:
    """Phase C: 真实执行引擎 —— 按 PlanStep 调用 M14 工具 handler。"""
    wi = context.work_item
    results: list[StepResult] = []
    
    if self._business_flow_tools is None:
        logger.warning("RealExecutor: no BusinessFlowToolRegistry, falling back to MockWorkAgent")
        return await self._work_agent.execute(plan, context)
    
    import time as _time
    from .pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult
    
    for step in plan.steps:
        t_start = _time.monotonic()
        tool_name = step.tool_name
        tool_params = getattr(step, 'tool_params', {}) or {}
        
        try:
            if tool_name and tool_name in self._business_flow_tools:
                # M14: 框架直接调用 handler
                tool = self._business_flow_tools.get(tool_name)
                handler_result = await tool.handler(tool_params)
                
                tool_calls = [ToolCallRecord(
                    tool_name=tool_name,
                    tool_input=tool_params,
                    tool_output=handler_result if isinstance(handler_result, dict) else {},
                    success=handler_result.get("success", True) if isinstance(handler_result, dict) else True,
                    elapsed_ms=int((_time.monotonic() - t_start) * 1000),
                )]
                
                db_results = []
                if isinstance(handler_result, dict) and handler_result.get("object_id"):
                    db_results.append(DbResult(
                        operation="insert",
                        table=tool_name.replace("record_", "") + "s",
                        affected_rows=1,
                        result_data=handler_result,
                    ))
                
                output = handler_result.get("reply", step.description) if isinstance(handler_result, dict) else step.description
                success = handler_result.get("success", True) if isinstance(handler_result, dict) else True
                
                sr = StepResult(
                    step_id=step.step_id,
                    success=success,
                    output=str(output),
                    tool_calls=tool_calls,
                    db_results=db_results,
                    business_data=handler_result if isinstance(handler_result, dict) else {},
                )
            elif tool_name:
                # 工具未注册
                sr = StepResult(
                    step_id=step.step_id,
                    success=False,
                    output=f"工具 '{tool_name}' 未在 BusinessFlowToolRegistry 中注册",
                )
            else:
                # 无工具步骤（纯文本步骤）
                sr = StepResult(
                    step_id=step.step_id,
                    success=True,
                    output=step.description,
                )
        except Exception as e:
            logger.error("Step %s failed: %s", step.step_id, e)
            sr = StepResult(
                step_id=step.step_id,
                success=False,
                output=f"步骤执行异常: {e}",
            )
        
        sr.elapsed_ms = int((_time.monotonic() - t_start) * 1000) if not hasattr(sr, 'elapsed_ms') else sr.elapsed_ms
        results.append(sr)
        
        if not sr.success:
            break  # 失败即停止
    
    return results
```

#### 修改 `node3_execute` 使用模式开关

```python
async def node3_execute(self, context: BusContext) -> None:
    wi = context.work_item
    if wi.execution_plan is None:
        return
    
    # Phase C: 按配置选择执行方式
    if self._resolve_mode("executor") == "real":
        step_results = await self._real_execute(wi.execution_plan, context)
    else:
        step_results = await self._work_agent.execute(wi.execution_plan, context)
    
    criteria = wi.acceptance_criteria
    for sr in step_results:
        try:
            await self._guardian.review_step(sr, None, criteria)
        except Exception as e:
            logger.warning("WI %s node3 guardian review_step failed: %s", wi.id, e)
        wi.add_step_result(sr)
    
    wi.llm_call_count += len(step_results)
    if step_results:
        context.agent_result = step_results[-1]
        context.agent_reply = step_results[-1].output
```

### 4.3 验收

- [ ] `EMILY_EXECUTOR_MODE=real` 时节点3 调用真实执行引擎
- [ ] PlanStep 含 `record_event` + `tool_params` → 数据库 events 表出现真实记录
- [ ] 工具未注册时 StepResult.success=False
- [ ] `EMILY_EXECUTOR_MODE=mock` 恢复 MockWorkAgent

---

## 五、C-4: GuardianReview 实现 Guardian ABC

**优先级**: 🔴 P0  
**依赖**: C-1  
**预估**: 1-2 天

### 5.1 设计

`GuardianReview`（冷储备 `agent/guardian_review.py`）已实现 `review_reply()` 和 `review_record()`。

Phase C 需要它满足 Pipeline `Guardian` 接口（`review_step` + `review_reply`），签名不同但语义相同。直接在 WorkItemAgent 中做轻量映射，不创建适配器类。

### 5.2 修改 `workitem_agent.py`

```python
async def _guardian_review_step(self, step_result, plan_step=None, criteria=None) -> GuardianVerdict:
    """Phase C: 轻量 Guardian 逐步审核 - 调用 GuardianReview.review_record()。"""
    if self._guardian_review is None:
        return GuardianVerdict.PASS
    
    try:
        tool_calls = getattr(step_result, 'tool_calls', []) or []
        for tc in tool_calls:
            result = await self._guardian_review.review_record(
                tool_name=tc.tool_name if hasattr(tc, 'tool_name') else 'unknown',
                data={
                    'output': getattr(step_result, 'output', ''),
                    'success': getattr(step_result, 'success', True),
                },
            )
            if not result.passed:
                return GuardianVerdict.FLAG
        return GuardianVerdict.PASS
    except Exception as e:
        logger.warning("Guardian review_step failed: %s (defaulting to PASS)", e)
        return GuardianVerdict.PASS

async def _guardian_review_reply(self, draft_reply, work_order=None) -> GuardianVerdict:
    """Phase C: 轻量 Guardian 回复审核 - 调用 GuardianReview.review_reply()。"""
    if self._guardian_review is None:
        return GuardianVerdict.PASS
    
    try:
        user_message = getattr(work_order, 'user_input', '') if work_order else ''
        result = await self._guardian_review.review_reply(draft_reply, user_message)
        if not result.passed:
            return GuardianVerdict.FLAG
        return GuardianVerdict.PASS
    except Exception as e:
        logger.warning("Guardian review_reply failed: %s (defaulting to PASS)", e)
        return GuardianVerdict.PASS
```

#### 修改 `_build_guardian()` 工厂

```python
def _build_guardian(self, mode: str):
    if mode == "review" and self._llm and self._guardian_review:
        # 返回一个轻量 guard——方法在 WorkItemAgent 内
        return self  # 特殊: node3/node4 直接调用 self._guardian_review_*() 方法
    elif mode == "agent":
        # Phase C-5 处理
        ...
    from .pipeline.mocks import MockGuardian
    return MockGuardian()
```

更干净的方式：让 `node3_execute` 和 `node4_summary` 根据 `guardian_mode` 配置直接调用不同路径，不通过 `self._guardian` 抽象：

```python
async def node3_execute(self, context: BusContext) -> None:
    wi = context.work_item
    ...
    guardian_mode = self._resolve_mode("guardian")
    
    for sr in step_results:
        if guardian_mode == "review":
            verdict = await self._guardian_review_step(sr, None, criteria)
        elif guardian_mode == "agent":
            verdict = await self._guardian_agent_step(sr, None, criteria)
        else:
            verdict = await self._guardian.review_step(sr, None, criteria)  # Mock
        # ... 其余逻辑不变
```

### 5.3 `node4_summary` 移除 Mock 前缀

```python
async def node4_summary(self, context: BusContext) -> None:
    wi = context.work_item
    summary = wi.to_summary()
    ...
    
    # Phase C: executor_mode=real 时无 Mock 前缀
    executor_mode = self._resolve_mode("executor")
    guardian_mode = self._resolve_mode("guardian")
    is_fully_real = (executor_mode == "real" and guardian_mode != "mock")
    prefix = "" if is_fully_real else "[Mock 模式] "
    
    # ... 其余不变 ...
```

### 5.4 验收

- [ ] `EMILY_GUARDIAN_MODE=review` 时 `review_reply()` 调用 GuardReview 单次 LLM
- [ ] Guardian 异常时默认 PASS
- [ ] 故意发送不完整数据 → GuardianReview 标记 FLAG
- [ ] `EMILY_EXECUTOR_MODE=real + EMILY_GUARDIAN_MODE=review` 时无 `[Mock 模式]` 前缀

---

## 六、C-5: GuardianAgent 接入 DeepAuditHook

**优先级**: 🟡 P1  
**依赖**: C-4  
**预估**: 1 天

### 6.1 设计

`GuardianAgent`（冷储备 `agent/guardian_agent.py`）是多轮 ReAct 审计，不适合作为 `review_reply` 的默认实现（延迟高）。Phase C 将其接入 `DeepAuditHook`（`before:wi_node4`），作为异步深度审计能力。

### 6.2 修改 `__init__.py` (EmilyCore)

在 `_init_phase_b_deps()` 中增加 GuardianAgent 工厂：

```python
# Phase C: GuardianAgent factory for DeepAuditHook
if self._llm_client and self._query_service:
    def _guardian_agent_factory():
        from .agent.guardian_agent import GuardianAgent
        return GuardianAgent(
            llm_client=self._llm_client,
            query_service=self._query_service,
            config=self.config,
            notebook_dir=getattr(self.config, 'notebook_dir', '') or '',
        )
    self._guardian_agent_factory = _guardian_agent_factory
```

在 `_collect_injected_services()` 中注入：

```python
# Phase C: Guardian 服务注入
if self._guardian_review is not None:
    injected["guardian_review"] = self._guardian_review
if hasattr(self, '_guardian_agent_factory'):
    injected["guardian_agent_factory"] = self._guardian_agent_factory
```

### 6.3 启用 hook_config.json 中的 deep_audit

```json
{
  "before:wi_node4": [
    {"type": "deep_audit", "name": "guardian.deep_audit", "enabled": true},
    {"type": "verify", "name": "guardian.reply_review", "enabled": true}
  ]
}
```

### 6.4 验收

- [ ] `EMILY_GUARDIAN_MODE=agent` + `deep_audit_enabled=true` → DeepAuditHook 触发
- [ ] GuardianAgent 调查报告写入 `context.baggage["deep_audit_report"]`
- [ ] 调查失败不影响管道执行（Hook 返回 ALLOW）

---

## 七、C-6: RealAuthEngine + RealRiskGrader

**优先级**: 🟢 P2 / 🔵 P3  
**依赖**: C-1  
**预估**: 1 天

### 7.1 RealAuthEngine

Phase B 的 `AuthHook.execute()` 已包含 SOP 角色鉴权逻辑。Phase C 只需将其抽取为 `RealAuthEngine` 类（实现 `AuthEngine` 接口），供 Hook 配置引用。

**文件**: `workitem/pipeline/real/real_auth.py`（新建）

```python
from ..interfaces.auth import AuthEngine, AuthResult, AuthDecision

class RealAuthEngine(AuthEngine):
    def __init__(self, sop_intent_registry=None):
        self._registry = sop_intent_registry

    async def authorize(self, user_id: str, route_decision) -> AuthResult:
        if not self._registry:
            return AuthResult(decision=AuthDecision.ALLOW, matched_roles=["all"],
                            _source="real_auth")
        sop_id = getattr(route_decision, "sop_id", None)
        if not sop_id:
            return AuthResult(decision=AuthDecision.ALLOW, _source="real_auth")
        spec = self._registry.get_spec(sop_id)
        if spec is None or "all" in spec.allow_roles:
            return AuthResult(decision=AuthDecision.ALLOW,
                            matched_roles=list(spec.allow_roles) if spec else ["all"],
                            _source="real_auth")
        return AuthResult(decision=AuthDecision.ALLOW, matched_roles=["all"],
                        _source="real_auth")
```

### 7.2 RealRiskGrader

**文件**: `workitem/pipeline/real/real_risk.py`（新建）

```python
from ..interfaces.risk import RiskGrader

class RealRiskGrader(RiskGrader):
    def grade(self, route_decision, operation_type: str = "") -> str:
        intent_type = getattr(route_decision, "intent_type", "fallback")
        confidence = getattr(route_decision, "confidence", "none")
        is_compound = getattr(route_decision, "is_compound", False)
        
        if intent_type == "fast_reply":     return "L1"
        if intent_type == "fallback" or confidence == "none": return "L3"
        if is_compound:                     return "L3"
        if operation_type == "delete":      return "L3"
        if operation_type == "write":       return "L2"
        if confidence == "low":             return "L2"
        return "L1"
```

### 7.3 验收

- [ ] `EMILY_AUTH_MODE=real` 用 RealAuthEngine 替代 MockAuthEngine
- [ ] `EMILY_RISK_MODE=real` 返回真实 L1/L2/L3

---

## 八、C-7: 依赖注入完善

**优先级**: 🟡 P1  
**依赖**: C-3, C-4, C-5  
**预估**: 1 天

### 8.1 修改 `__init__.py` (EmilyCore)

在 `_init_phase_b_deps()` 中增加 Phase C 初始化：

```python
# Phase C: GuardianReview 实例（共享，轻量验证器）
if self._llm_client:
    from .agent.guardian_review import GuardianReview
    self._guardian_review = GuardianReview(self._llm_client, self.config)
    logger.info("Phase C: GuardianReview initialized")

# Phase C: BusinessFlowToolRegistry（执行引擎依赖）
from .tools.business_flow_tools import BusinessFlowToolRegistry
from .tools.event_tool import handle_record_event
from .tools.task_tool import handle_record_task
from .tools.meeting_tool import handle_record_meeting
from .tools.file_tool import handle_record_file
from .tools.query_tool import handle_query_data

self._business_flow_tools = BusinessFlowToolRegistry()
self._business_flow_tools.register("record_event", handle_record_event)
self._business_flow_tools.register("record_task", handle_record_task)
self._business_flow_tools.register("record_meeting", handle_record_meeting)
self._business_flow_tools.register("record_file", handle_record_file)
self._business_flow_tools.register("query_data", handle_query_data)
logger.info("Phase C: BusinessFlowToolRegistry initialized with 5 tools")
```

### 8.2 更新 `_build_pipeline_bus()`

```python
self._workitem_agent = WorkItemAgent(
    injector=injector,
    llm_client=self._llm_client,
    config=self.config,
    # Phase C: 执行依赖
    business_flow_tools=self._business_flow_tools,
    guardian_review=self._guardian_review,
)
```

### 8.3 更新 `_collect_injected_services()`

```python
# Phase C: Guardian
if self._guardian_review is not None:
    injected["guardian_review"] = self._guardian_review
if hasattr(self, '_guardian_agent_factory'):
    injected["guardian_agent_factory"] = self._guardian_agent_factory
# Phase C: 鉴权
if self._sop_intent_registry is not None:
    injected["sop_intent_registry"] = self._sop_intent_registry
```

---

## 九、C-8: 烟雾测试升级

**优先级**: 🔴 P0  
**依赖**: C-3, C-4, C-5  
**预估**: 1 天

### 9.1 新增测试用例

在 `scripts/smoke_test.py` 中新增：

```
测试 5: executor_mode=real + guardian_mode=review
  → 发送 "创建事件: 安全检查通过"
  → 验证 reply 不含 [Mock 模式] 前缀
  → 验证 WorkItem state=DONE

测试 6: guardian_mode=review 审核标记
  → 发送不完整数据
  → 验证 GuardianReview 标记 FLAG

测试 7: deep_audit_enabled=true
  → 发送触发深度审计的消息
  → 验证 DeepAuditHook 写入 baggage

测试 8: 多 WI 顺序执行（Phase B SessionAgent 拆分）
  → 发送复合消息 "查项目进度，然后安排会议"
  → 验证 2 个 WorkItem 均 DONE
```

### 9.2 运行方式

```bash
# 全部真实模式（需 LLM API key）
EMILY_LLM_API_KEY=sk-xxx \
EMILY_EXECUTOR_MODE=real \
EMILY_GUARDIAN_MODE=review \
EMILY_PLANNER_MODE=real \
python scripts/smoke_test.py --phase-c

# 无 LLM 模式（Mock fallback 验证）
python scripts/smoke_test.py
```

---

## 十、实施顺序与文件总览

### 10.1 依赖关系

```
C-1 (配置) ──────────────────────────────────────┐
  ├── C-2 (PlanStep.tool_params) ──┐              │
  │    └── C-3 (真实执行引擎) ──────┤              │
  ├── C-4 (GuardianReview→Guardian) ┤              │
  │    └── C-5 (GuardianAgent Hook)─┤              │
  ├── C-6 (AuthEngine+RiskGrader) ──┤              │
  └── C-7 (依赖注入完善) ───────────┘              │
       └── C-8 (烟雾测试) ─────────────────────────┘
```

### 10.2 执行顺序

```
第 1 步: C-1  配置开关 (executor_mode, guardian_mode, auth_mode, risk_mode)
第 2 步: C-2  PlanStep.tool_params 增强
第 3 步: C-3  真实执行引擎 (WRItemAgent._real_execute)
第 4 步: C-4  GuardianReview 实现 Guardian (轻量审核)
第 5 步: C-5  GuardianAgent 接入 DeepAuditHook (深度审计)
第 6 步: C-7  依赖注入完善 (EmilyCore 初始化全量服务)
第 7 步: C-6  RealAuthEngine + RealRiskGrader (收尾)
第 8 步: C-8  烟雾测试升级
```

### 10.3 文件变更总览

| 文件 | 操作 | 阶段 |
|------|------|------|
| `config.py` | 修改 | C-1 |
| `workitem/pipeline/interfaces/planning.py` | 修改 | C-2 |
| `workitem/workitem_agent.py` | 修改 | C-3, C-4 |
| `workitem/pipeline/real/__init__.py` | **新建** | C-6 |
| `workitem/pipeline/real/real_auth.py` | **新建** | C-6 |
| `workitem/pipeline/real/real_risk.py` | **新建** | C-6 |
| `__init__.py` (EmilyCore) | 修改 | C-7 |
| `scripts/smoke_test.py` | 修改 | C-8 |
| `emily-data/config/hook_config.json` | 修改 | C-5 |

**总计**: 5 修改 + 3 新建 = 8 文件

---

*计划结束 —— 等待审核确认后进入实施*
