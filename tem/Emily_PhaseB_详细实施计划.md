# Emily Phase B 详细实施计划（修订版）

> **版本**: v2.0  
> **日期**: 2026-06-24  
> **依据**: [Emily 主系统架构 §12.2](tem/0623Emily_主系统架构.md) Phase B 定义  
> **前置**: Phase 0 (容器分离) ✅ | Phase A (Session骨架+Pipeline BUS) ✅  
> **目标**: SessionAgent 升级 + WorkItemAgent 自主规划 + 知识灌注完善 + Session 生命周期完整

---

## 目录

1. [架构澄清：旧 Agent 文件的定位](#一 and设计原则)
2. [Phase B 范围定义](#二phase-b-范围定义)
3. [B-1: SessionAgent 意图识别升级](#三b-1-sessionagent-意图识别升级)
4. [B-2: KnowledgeInjector 增量灌注引擎](#四b-2-knowledgeinjector-增量灌注引擎)
5. [B-3: WorkItemAgent 自主规划](#五b-3-workitemagent-自主规划)
6. [B-4: Session 交互调度完善](#六b-4-session-交互调度完善)
7. [B-5: SessionFactory 上下文填充](#七b-5-sessionfactory-上下文填充)
8. [B-6: Session 归档 + SOP-010](#八b-6-session-归档--sop-010)
9. [B-7: 鉴权 Hook 完善](#九b-7-鉴权-hook-完善)
10. [实施依赖与顺序](#十实施依赖与顺序)
11. [文件变更总览](#十一文件变更总览)
12. [验收标准总表](#十二验收标准总表)

---

## 一、架构澄清：旧 Agent 文件的定位

### 1.1 旧文件状态

```
emily_core/agent/
├── master_agent.py          (~800行) — 冷储备：ReAct 循环 + SOP 发现路由
├── business_flow_agent.py   (~400行) — 冷储备：双模式 SOP 执行
├── guardian_agent.py        (~500行) — 冷储备：深度审计 ReAct
├── guardian_review.py       (~200行) — 冷储备：轻量单次LLM验证
├── intent_registry.py       (~400行) — ✅ 热路径复用：SOP 目录加载
├── sop_parser.py            (共用)   — ✅ 热路径复用：SOP Markdown 解析
├── tool_registry.py         (~300行) — ✅ 热路径复用：工具注册基础设施
├── conversation_context.py  (共用)   — 冷储备：旧对话上下文模型
├── flow_renderer.py         (共用)   — 冷储备：决策树渲染
└── mermaid_flow.py          (~600行) — 冷储备：Mermaid 流程图
```

### 1.2 正确的处理方式

| 旧文件 | 处理方式 | 说明 |
|--------|----------|------|
| `master_agent.py` | **提取吸收** | SOP 路由决策逻辑提取到 SessionAgent；ReAct 循环模式参考，不直接调用 |
| `business_flow_agent.py` | **Phase C 吸收** | M14 结构化执行模式参考；Phase B 保持 MockWorkAgent |
| `guardian_agent.py` | **Phase C 使用** | Phase B 保持 MockGuardian |
| `guardian_review.py` | **Phase C 使用** | Phase B 保持 MockGuardian |
| `intent_registry.py` | **直接复用** | 已在工具层使用，SessionAgent 也直接调用 |
| `tool_registry.py` | **直接复用** | 工具注册基础设施，全架构共享 |

### 1.3 核心原则

- **不创建适配器包装旧 Agent** —— 旧 Agent 是旧架构的产物，接口与新 Pipeline 协议不兼容
- **提取有用逻辑，融入新架构类** —— SessionAgent / WorkItemAgent 直接实现所需能力
- **旧文件保留不动** —— 作为参考代码和 Phase C 改造素材，不修改、不 import 到热路径

---

## 二、Phase B 范围定义

### 2.1 架构蓝图原文（§12.2）

```
Phase B: WorkItem-Agent 单例化 + 增量灌注 (骨架就绪 → 执行层完整)

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
```

### 2.2 Phase B 变更范围

```
Phase B 变更的组件:
  ✅ MockRouter      → 删除（SessionAgent 自己做意图识别）
  ✅ MockPlanner     → 替换为 WorkItemAgent 内 LLM 规划
  ✅ MockAuthEngine  → 替换为 Hook 鉴权（基于 perm_list）

Phase B 不动的组件:
  ✋ MockWorkAgent   → 保持（Phase C 才替换）
  ✋ MockGuardian    → 保持（Phase C 才替换）
  ✋ MockRiskGrader  → 保持（Phase C 评估是否需要）

Phase B 新增/升级的组件:
  🆕 SessionAgent 意图识别能力（从 MasterAgent 提取 LLM 路由逻辑）
  🆕 WorkItemAgent 自主规划能力（LLM 生成 ExecutionPlan）
  🆕 KnowledgeInjector 真实增量注入
  🆕 Session 上下文懒加载 + 摘要填充
  🆕 FocusLock 接入 + ConfirmQueue 接入
  🆕 Session 归档逻辑 + SOP-010
  🆕 AuthHook 鉴权逻辑（基于 perm_list）
```

### 2.3 不做什么

- ❌ 不创建 `RealRouter` 适配器类
- ❌ 不创建 `RealPlanner` 适配器类
- ❌ 不调用 `MasterAgent.run()`
- ❌ 不调用 `BusinessFlowAgent.execute()`
- ❌ 不创建 `RealWorkAgent`
- ❌ 不创建 `RealGuardian`
- ❌ 旧 `agent/` 目录不新增 import 到热路径

---

## 三、B-1: SessionAgent 意图识别升级

**优先级**: 🔴 P0  
**依赖**: Phase A SessionAgent 骨架  
**预估**: 3-4 天

### 3.1 设计说明

当前 `SessionAgent._split_into_workitems()` 固定创建 1 个 WorkItem，意图识别依赖 `WorkItemAgent` 节点 1 中的 `MockRouter`（固定返回 SOP-002-REC）。

升级后：SessionAgent 在拆分 WorkItem **之前** 做 LLM 意图识别，确定 SOP 匹配结果，再据此创建 WorkItem。Pipeline 节点 1 的职责从"路由"变为"验证+注入"。

### 3.2 修改 `session_agent.py`

**文件**: `emily-core/emily_core/session/session_agent.py`

#### 3.2.1 新增导入与方法

```python
# 新增导入
from ..agent.intent_registry import SOPIntentRegistry
from ..agent.tool_registry import ToolRegistry

class SessionAgent:
    def __init__(self, conversation_id, context, bus=None,
                 # Phase B: 意图识别依赖
                 llm_client=None,
                 sop_intent_registry=None,
                 tool_registry=None,
                 config=None):
        # ... 现有初始化 ...
        
        # Phase B: 意图识别
        self._llm = llm_client
        self._sop_intent_registry = sop_intent_registry
        self._tool_registry = tool_registry
        self._config = config
```

#### 3.2.2 新增意图识别方法

```python
# ── Phase B: 意图识别（从 MasterAgent 提取的核心逻辑）──

ROUTING_SYSTEM_PROMPT = """你是 Emily 的意图路由器，负责将用户消息匹配到对应的业务流程（SOP）。

## 当前时间
{current_datetime}

## 可用业务流程目录
{sop_catalog}

## 输出要求
仅输出一个 JSON 对象：
{{"sop_id": "SOP-XXX-YYY" | null, "confidence": "high|medium|low|none", "reasoning": "匹配理由", "is_compound": false, "sub_tasks": [], "fallback": false}}
"""

async def _recognize_intent(self, message: "StandardMessage") -> dict:
    """LLM 意图识别：返回匹配的 SOP 信息。

    从 MasterAgent 的路由逻辑提取——单次 chat_json() 调用，
    不做 ReAct 循环。

    Returns:
        dict: {"sop_id": str|None, "confidence": str, "is_compound": bool, 
               "sub_tasks": list, "fallback": bool}
    """
    content = message.content or ""
    
    # 无 LLM → 回退到默认路由
    if not self._llm or not self._sop_intent_registry:
        return {"sop_id": None, "confidence": "none", "is_compound": False,
                "sub_tasks": [], "fallback": True}
    
    # 空消息 → 回退
    if not content.strip():
        return {"sop_id": None, "confidence": "none", "is_compound": False,
                "sub_tasks": [], "fallback": True}
    
    sop_catalog = self._sop_intent_registry.dump_as_text()
    prompt = ROUTING_SYSTEM_PROMPT.format(
        sop_catalog=sop_catalog,
        current_datetime=_beijing_now_str(),
    )
    
    try:
        result = await self._llm.chat_json(prompt, content)
        logger.debug("SessionAgent intent: %s", result)
        return result
    except Exception as e:
        logger.warning("SessionAgent intent recognition failed: %s", e)
        return {"sop_id": None, "confidence": "none", "is_compound": False,
                "sub_tasks": [], "fallback": True}
```

#### 3.2.3 升级 `_split_into_workitems()`

```python
async def _split_into_workitems(self, message: "StandardMessage") -> list[WorkItem]:
    """Phase B: 基于 LLM 意图识别的 WorkItem 拆分。"""
    content = message.content or ""
    
    # 先做意图识别
    intent = await self._recognize_intent(message)
    
    sop_id = intent.get("sop_id")
    is_compound = intent.get("is_compound", False)
    sub_tasks = intent.get("sub_tasks", [])
    
    # 回退：无匹配 SOP
    if intent.get("fallback") or not sop_id:
        return [WorkItem(
            session_id=self.conversation_id,
            user_input=content,
            user_id=self.context.user_id,
            sop_id=None,
            intent_type="fallback",
            priority=1,
        )]
    
    # 复合请求：多个 WorkItem
    if is_compound and sub_tasks:
        max_n = getattr(self.context, "workitem_max_per_session", 5) or 5
        items = []
        for i, st in enumerate(sub_tasks[:max_n]):
            items.append(WorkItem(
                session_id=self.conversation_id,
                user_input=st.get("user_input", content),
                user_id=self.context.user_id,
                sop_id=st.get("sop_id", sop_id),
                intent_type="sop",
                priority=st.get("priority", 1),
            ))
        return items
    
    # 单 SOP 匹配
    return [WorkItem(
        session_id=self.conversation_id,
        user_input=content,
        user_id=self.context.user_id,
        sop_id=sop_id,
        intent_type="sop",
        priority=1,
    )]
```

#### 3.2.4 handle() 改为 async 拆分流

`handle()` 中拆分调用改为 `await`：

```python
# 旧: work_items = self._split_into_workitems(message)
# 新:
work_items = await self._split_into_workitems(message)
```

### 3.3 修改 `workitem_agent.py` —— 简化节点 1

节点 1 不再调用 MockRouter，改为验证 SessionAgent 已设置的路由信息：

```python
async def node1_intent(self, context: BusContext) -> None:
    """Node 1 [意图验证+注入] —— Phase B: 路由已在 SessionAgent 完成。"""
    wi = context.work_item
    
    # 增量灌注
    self.injector.analyze(wi)
    
    # SessionAgent 已设置 sop_id 和 intent_type
    # 节点 1 仅做验证：如果有 route_decision 则用，否则从 wi 字段构建
    if wi.route_decision is None:
        from .pipeline.interfaces.routing import RouteDecision, SubTask
        wi.route_decision = RouteDecision(
            intent_type=getattr(wi, "intent_type", "fallback") or "fallback",
            sop_id=wi.sop_id,
            confidence="medium" if wi.sop_id else "none",
            is_compound=False,
            sub_tasks=[SubTask(
                id="subtask-001",
                sop_id=wi.sop_id or "",
                user_input=wi.user_input,
            )] if wi.sop_id else [],
            _source="session_agent",
        )
    
    wi.llm_call_count += 1
    context.intent = wi.route_decision
```

### 3.4 验收标准

- [ ] SessionAgent 有 LLM 时对非快捷消息做意图识别
- [ ] 发送"记录事件：安全检查通过" → sop_id 匹配 SOP-002-REC
- [ ] 发送"查一下项目进度" → sop_id 匹配 QRY 类 SOP
- [ ] 发送"今天天气真好" → fallback=true
- [ ] 无 LLM 时优雅回退（创建 1 个 fallback WorkItem）
- [ ] Pipeline 节点 1 不再调用 MockRouter（`_router` 字段可废弃）

---

## 四、B-2: KnowledgeInjector 增量灌注引擎

**优先级**: 🔴 P0  
**依赖**: B-1（SOPIntentRegistry 可用）  
**预估**: 2 天

### 4.1 设计说明

当前 `KnowledgeInjector.analyze()` 只做集合差算法，不加载实际内容。升级后：
- 加载 SOP 全文（通过 SOPLoader）
- 加载工具参数 Schema（通过 ToolRegistry）
- 加载数据库表结构摘要（通过 ORM 反射或预定义映射）
- Token 预算控制（总上下文不超过 32K tokens 估算值）
- WorkItem 完成时回收独占知识

### 4.2 修改 `injector.py`

**文件**: `emily-core/emily_core/workitem/injector.py`

核心变更：
1. 构造函数接收 `SOPLoader`、`ToolRegistry`
2. `analyze()` 加载实际内容到内存
3. 新增 `get_context_text()` 输出注入的上下文
4. `release()` 实现基本回收（从 loaded 集合移除）
5. Token 预算超限时 LRU 回收

详细代码参见 [Emily_PhaseB_详细实施计划.md §五](Emily_PhaseB_详细实施计划.md)（KnowledgeInjector 部分直接可用，该部分设计不涉及旧 Agent）。

### 4.3 验收标准

- [ ] `analyze(wi)` 在 sop_id 存在时加载 SOP 全文
- [ ] `get_context_text()` 返回非空注入内容
- [ ] 重复分析相同 sop_id 不重新加载
- [ ] Token 预算超限触发回收
- [ ] `release(wi)` 从 loaded 集合移除对应资源

---

## 五、B-3: WorkItemAgent 自主规划

**优先级**: 🟡 P1  
**依赖**: B-1（sop_id 已设置）、B-2（SOP 全文可注入）  
**预估**: 2 天

### 5.1 设计说明

当前 `MockPlanner.plan()` 固定返回 3 步计划。升级后：WorkItemAgent 节点 2 使用 LLM 生成动态 `ExecutionPlan`。

**这是 WorkItemAgent 自身的能力增强**，不是引入外部 Planner。LLM 调用逻辑直接写在 WorkItemAgent 或内嵌的轻量方法中。

### 5.2 修改 `workitem_agent.py`

新增配置开关 `EMILY_PLANNER_MODE`（默认 `mock`），当设为 `real` 且有 LLM 时，节点 2 走 LLM 规划。

```python
# workitem_agent.py 新增

PLANNER_PROMPT = """你是 Emily 的执行规划器。根据 SOP 和用户输入，制定逐步执行计划。

## SOP 参考
{sop_text}

## 用户输入
{user_input}

## 输出格式
仅输出 JSON：{{"risk_level":"L1|L2|L3","steps":[{{"step_id":"step-01","description":"...","tool_name":"record_event|null","expected_output":"...","depends_on":[]}}],"acceptance_criteria":["..."],"estimated_steps":N}}
"""

async def _llm_plan(self, wi, context) -> ExecutionPlan:
    """LLM 动态规划（Phase B）。"""
    sop_text = ""
    if self.injector and hasattr(self.injector, 'get_context_text'):
        sop_text = self.injector.get_context_text()
    
    prompt = PLANNER_PROMPT.format(
        sop_text=sop_text[:4000] if sop_text else f"SOP: {wi.sop_id or '未知'}",
        user_input=wi.user_input,
    )
    
    try:
        data = await self._llm.chat_json(prompt, f"Plan: {wi.user_input[:200]}")
    except Exception:
        # 回退到 3 步计划
        from .pipeline.mocks.mock_planning import MockPlanner
        return await MockPlanner().plan(wi.route_decision, context)
    
    steps = []
    for i, s in enumerate(data.get("steps", [])):
        steps.append(PlanStep(
            step_id=s.get("step_id", f"step-{i+1:02d}"),
            description=s.get("description", ""),
            tool_name=s.get("tool_name"),
            expected_output=s.get("expected_output", ""),
            depends_on=s.get("depends_on", []),
        ))
    
    return ExecutionPlan(
        risk_level=data.get("risk_level", "L2"),
        steps=steps[:8],  # 最多 8 步
        acceptance_criteria=data.get("acceptance_criteria", []),
        estimated_steps=len(steps),
        _source="llm_planner",
    )

async def node2_plan(self, context: BusContext) -> None:
    wi = context.work_item
    
    # 根据模式选择规划方式
    if self._resolve_mode("planner") == "real" and self._llm:
        plan = await self._llm_plan(wi, context)
    else:
        plan = await self._planner.plan(wi.route_decision, context)
    
    wi.execution_plan = plan
    wi.risk_level = plan.risk_level
    wi.acceptance_criteria = list(getattr(plan, "acceptance_criteria", []))
    wi.llm_call_count += 1
```

### 5.3 不再需要 MockPlanner 时

当 `EMILY_PLANNER_MODE=real` 且 LLM 可用时，`_planner` 字段不再被调用。保留 `MockPlanner` 作为 fallback。

### 5.4 验收标准

- [ ] `EMILY_PLANNER_MODE=real` 时节点 2 走 LLM 规划
- [ ] 不同 SOP 产生不同步骤数（非固定 3 步）
- [ ] 步骤绑定正确工具名（record_event / query_data 等）
- [ ] LLM 失败时回退到 3 步兜底计划

---

## 六、B-4: Session 交互调度完善

**优先级**: 🟡 P1  
**依赖**: B-1（SessionAgent 升级完成）  
**预估**: 2-3 天

### 6.1 FocusLock 接入

**文件**: `emily-core/emily_core/session/focus_lock.py`

当前 FocusLock 已实现基础结构（`set_focus` / `clear_focus` / `wants_switch`），但未接入 `handle()`。

**接入方式**（在 `session_agent.py` 的 `handle()` 中）：

```python
# 在 ① 短路回复之后、② 拆分之前
if FocusLock.wants_switch(content):
    self.focus.clear_focus()
    
# 在拆分后设置焦点
if work_items:
    self.focus.set_focus(work_items[0].id)
```

### 6.2 ConfirmQueue 接入

**文件**: `emily-core/emily_core/session/confirm_queue.py`

同理，ConfirmQueue 已实现但未接入。

```python
# 在 run_all() 之后检查待确认
def _collect_pending_confirms(self, done_workitems: list) -> str | None:
    from ..workitem.workitem_state import WorkItemState
    needs_confirm = [
        wi for wi in done_workitems
        if wi.state == WorkItemState.WAITING_CONFIRM
    ]
    for wi in needs_confirm:
        self.confirm_queue.add(
            workitem_id=wi.id,
            prompt=f"关于「{wi.user_input[:50]}...」需要你的确认",
            priority=wi.priority,
        )
    if not self.confirm_queue.is_empty:
        entry = self.confirm_queue.pop()
        if entry:
            return entry.prompt
    return None
```

### 6.3 验收标准

- [ ] 消息含"先不管"、"先处理"等关键词时清除当前焦点
- [ ] WorkItem 进入 WAITING_CONFIRM 状态时加入 ConfirmQueue
- [ ] 待确认项在下一轮消息处理时返回给用户

---

## 七、B-5: SessionFactory 上下文填充

**优先级**: 🟢 P2  
**依赖**: B-2（SOPIntentRegistry 可用）  
**预估**: 1 天

### 7.1 修改 `session_factory.py`

**文件**: `emily-core/emily_core/adapters/session/session_factory.py`

在 `_build_context()` 中填充占位摘要字段（当前全为空）：

```python
def _build_context(self, message, user_id: str) -> SessionContext:
    ctx = SessionContext(
        conversation_id=message.conversation_id,
        user_id=user_id,
        user_name=message.sender_name or "",
        current_datetime=datetime.now(timezone.utc).isoformat(),
    )
    
    core = self._core
    if core is None:
        return ctx
    
    # SOP 目录摘要
    if core._sop_intent_registry:
        sops = core._sop_intent_registry.list_loaded_sops()
        if sops:
            ctx.sop_catalog_summary = f"可用业务流程 ({len(sops)}): {', '.join(sops[:15])}"
    
    # 工具目录摘要
    if core._tool_registry:
        tools = core._tool_registry.tool_names
        if tools:
            ctx.tool_catalog_summary = f"可用工具 ({len(tools)}): {', '.join(tools[:20])}"
    
    # 用户记忆
    if core._user_memory_service and user_id:
        try:
            memory = core._user_memory_service.load_memory_context(message.sender_name or "")
            if memory:
                ctx.history_summary = memory[:2000]
                ctx.user_preferences = memory[:500]
        except Exception:
            pass
    
    return ctx
```

### 7.2 验收标准

- [ ] `sop_catalog_summary` 在 SOP 目录可用时非空
- [ ] `tool_catalog_summary` 在工具注册表可用时非空
- [ ] 依赖不可用时优雅降级

---

## 八、B-6: Session 归档 + SOP-010

**优先级**: 🟢 P2  
**依赖**: B-4（ConfirmQueue 接入完成）  
**预估**: 1-2 天

### 8.1 修改 `session_agent.py` —— 实现 `archive()`

当前 `archive()` 方法体为空（只有 TODO 注释）。实现：

```python
async def archive(self) -> None:
    """会话归档：状态推进 + 资源清理 + SOP-010。"""
    if self.state in (SessionState.CLOSED, SessionState.ARCHIVING):
        return
    
    self.state = SessionState.ARCHIVING
    logger.info("Session[%s] archiving (turns=%d)...",
                self.conversation_id, len(self.context.recent_turns))
    
    try:
        # 1. 清空待确认队列
        self.confirm_queue.clear()
        
        # 2. 标记活跃 WorkItem 为失败
        for wi in list(self.scheduler._active.values()):
            if not wi.is_terminal:
                try:
                    wi.transition_to(WorkItemState.FAILED)
                except ValueError:
                    pass
        
        # 3. 生成会话摘要（供用户记忆服务使用）
        #    实际持久化在 Phase C 通过 UserMemoryService 完成
        
        logger.info("Session[%s] archived successfully", self.conversation_id)
    except Exception as e:
        logger.warning("Session[%s] archive error: %s", self.conversation_id, e)
    finally:
        self.state = SessionState.CLOSED
```

### 8.2 验收标准

- [ ] `archive()` 将状态从 ACTIVE → ARCHIVING → CLOSED
- [ ] 活跃 WorkItem 被标记为 FAILED
- [ ] 重复调用 archive() 不重复执行

---

## 九、B-7: 鉴权 Hook 完善

**优先级**: 🔵 P3  
**依赖**: B-1（SessionAgent 设置路由信息）  
**预估**: 1 天

### 9.1 替换 MockAuthEngine

当前 `MockAuthEngine` 永远返回 ALLOW。Phase B 将其替换为基于 `users.perm_list` 的角色鉴权。

**不需要新建适配器文件**——直接修改 Hook 配置中 `auth` 类型 Hook 的逻辑，在 `hook.py` 的 `AuthHook` 中实现：

```python
# hook.py 中 AuthHook.run() 升级
async def run(self, context: BusContext) -> HookResult:
    wi = context.work_item
    sop_id = getattr(wi, 'sop_id', None)
    user_id = context.user_id
    
    # 基础：所有已验证用户放行
    if not sop_id:
        return HookResult(decision=HookDecision.ALLOW)
    
    # 检查 SOP allow_roles（通过注入的 sop_intent_registry）
    registry = self._injected.get("sop_intent_registry")
    if registry:
        spec = registry.get_spec(sop_id)
        if spec and "all" not in spec.allow_roles:
            # 获取用户角色（通过注入的 user_repo）
            user_repo = self._injected.get("user_repo")
            if user_repo:
                user = await user_repo.get_by_id(user_id)
                user_roles = user.perm_list if user else []
                if not set(user_roles) & set(spec.allow_roles):
                    return HookResult(
                        decision=HookDecision.BLOCK,
                        reason=f"需要角色 {spec.allow_roles}"
                    )
    
    return HookResult(decision=HookDecision.ALLOW)
```

### 9.2 验收标准

- [ ] 用户无对应 SOP 权限时 AuthHook 返回 BLOCK
- [ ] SOP `allow_roles` 含 "all" 时全部放行

---

## 十、实施依赖与顺序

```
B-1: SessionAgent 意图识别
  ├── 依赖: Phase A SessionAgent 骨架
  └── 产出: LLM 路由 + WorkItem 拆分 + 节点1简化

B-2: KnowledgeInjector 完善
  ├── 依赖: B-1 (SOPIntentRegistry 可用)
  └── 产出: 真实 SOP/工具/Schema 加载

B-3: WorkItemAgent 自主规划
  ├── 依赖: B-1 (sop_id) + B-2 (SOP 全文可注入)
  └── 产出: LLM 动态 ExecutionPlan

B-4: FocusLock + ConfirmQueue
  ├── 依赖: B-1 (SessionAgent 升级完成)
  └── 产出: 焦点调度 + 待确认队列

B-5: SessionFactory 上下文
  ├── 依赖: B-2 (SOPIntentRegistry 可用)
  └── 产出: 摘要字段填充

B-6: Session 归档
  ├── 依赖: B-4 (ConfirmQueue 接入)
  └── 产出: archive() 实现

B-7: 鉴权 Hook
  ├── 依赖: B-1 (路由信息)
  └── 产出: 角色鉴权
```

**建议执行顺序**：B-1 → B-2 → (B-3 ∥ B-4 ∥ B-5) → B-6 → B-7

---

## 十一、文件变更总览

| 文件 | 操作 | 阶段 | 说明 |
|------|------|------|------|
| `session/session_agent.py` | **修改** | B-1, B-4, B-6 | 意图识别、FocusLock/ConfirmQueue 接入、archive |
| `workitem/workitem_agent.py` | **修改** | B-3 | 节点2 LLM 规划、节点1 简化 |
| `workitem/injector.py` | **修改** | B-2 | 真实加载 SOP/工具/Schema |
| `adapters/session/session_factory.py` | **修改** | B-5 | 摘要字段填充 |
| `workitem/pipeline/hook.py` | **修改** | B-7 | AuthHook 鉴权逻辑 |
| `config.py` | **修改** | 全局 | 新增 `planner_mode` 开关 |
| `__init__.py` (EmilyCore) | **修改** | 全局 | 注入 sop_intent_registry 到 SessionFactory + Hook |

**不新建任何文件。不修改 `agent/` 目录下任何文件。**

---

## 十二、验收标准总表

| 阶段 | 核心验收标准 |
|------|-------------|
| B-1 | 发送"记录事件：xxx" → SessionAgent 识别为 SOP-002-REC；Pipeline 节点1 不再调用 MockRouter |
| B-2 | KnowledgeInjector 加载 SOP 全文；`get_context_text()` 非空 |
| B-3 | `EMILY_PLANNER_MODE=real` → 动态步骤数（非固定 3 步）；MockPlanner 保持为 fallback |
| B-4 | 含"先不管"消息触发焦点切换；WAITING_CONFIRM 入队 |
| B-5 | SessionContext 摘要字段非空 |
| B-6 | archive() 状态转换 ACTIVE→ARCHIVING→CLOSED |
| B-7 | 无权限用户操作受限制 SOP 被 BLOCK |
| 全局 | 热路径不 import `agent/master_agent.py` / `business_flow_agent.py` / `guardian_agent.py` |
| 全局 | `[Mock mode]` 前缀不再出现在路由/规划阶段（仅执行+守护保留） |

---

*计划结束 — Phase B 聚焦 SessionAgent 升级 + WorkItemAgent 自主规划 + 知识灌注，不动旧 agent/ 文件*
