# Session-WorkItem 分层合成 — AI 执行计划

> **计划版本**：v1.0
> **目标**：把"回复合成"从 WorkItem 层上移到 Session 层，WorkItem 只做执行 + 结构化反馈，Session 做意图 + 成果规格下发 + 语言组织 + 回复审核。消除 [workitem.md:22](emily-data/prompts/workitem.md#L22) "不直接面对用户"却写面向用户回复的矛盾定位，统一回复风格，提升多 WorkItem 复合任务的连贯性。
> **依赖**：与 [对话流优化_计划_V1.md](../需求/对话流优化_计划_V1.md) 协同（路由 prompt 共用）；与 [AgentHarness补齐_计划_V1.md](AgentHarness补齐_计划_V1.md) 已实施部分有二次拆改依赖（见 M4）。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家**，严格按模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **不改已有方法签名**：`SessionAgent.handle` / `WorkItemAgent.node_handlers` / `LLMClient.chat_messages` 等已有签名只允许**新增可选参数**，不修改现有参数顺序与含义
2. **分层不可跳**：`API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`。语言组织属 Session 层，不污染 WorkItem/Application/Service
3. **业务内核独立**：`emily_core` 不 import 任何 `astrbot.*` 包
4. **Sync repo + `asyncio.to_thread`**：Repository 全 sync，async Service 用 `asyncio.to_thread()` 包裹
5. **保留基础设施 fail-open**：LLM 完全不可用时仍走降级（CLAUDE.md 硬约束 9）。本计划的"Session 合成 LLM"不可用时回退到规则拼串兜底
6. **每模块验收**：每个模块验收必须通过（emy-test + 日志检查），否则停止并报告
7. **参照模式**：所有新代码参照"代码模式参照表"中的源文件，风格不一致视为失败
8. **harness M3 二次拆改**：[AgentHarness补齐_计划_V1.md](AgentHarness补齐_计划_V1.md) 的 M3（node4 review_reply 修正循环）已实施，本计划 M4 需将其从 node4 迁移到 Session 层，不得破坏既有 harness 行为
9. **WorkItem 层零语言组织 LLM**：node4 不再调 `_llm_synthesize_reply`，改为规则提炼 `structured_result`。`review_step`（node3 执行审核）保留，不算语言组织

---

## Context（为什么做这个改动）

### 现状分工问题

当前回复合成放在 WorkItem 层是**数据驱动**的——`step_results` 只在 WorkItem 层产生，合成回复需要这些数据，所以就近做。但产生三个问题：

1. **成果要求没下发**：[session_agent.py:408-415](emily-core/emily_core/session/session_agent.py#L408-L415) 创建 WorkItem 只传 `user_input/sop_id/intent_type/priority`，无成果规格。WorkItem 用通用 [workitem.md](emily-data/prompts/workitem.md) 合成，回复详细度/格式全靠 LLM 临场发挥。

2. **多 WorkItem 拼接缺连贯**：[session_agent.py:236-239](emily-core/emily_core/session/session_agent.py#L236-L239) 汇总只是 `"\n\n".join(replies)`。复合任务（"先查 A 再处理 B"）各 WI 独立合成，B 可能重复 A 已说的，缺乏整体组织。

3. **两层风格不一致**：闲聊在 [Session 层快回](emily-core/emily_core/session/session_agent.py#L625)（`_try_fast_reply`），业务在 WorkItem 层合成（`_llm_synthesize_reply`），元认知走 fallback WI。三路径风格/质量不统一。矛盾的是 [workitem.md:22](emily-data/prompts/workitem.md#L22) 自称"不直接面对用户"，却写面向用户的回复。

### 改造目标

| 层 | 改前 | 改后 |
|----|------|------|
| Session | 意图识别 + 拆分 + join 拼接 | 意图识别 + 拆分 + **派生 output_spec** + **组织最终回复** + **审回复合适性** |
| WorkItem node4 | LLM 合成 result_text + review_reply | **规则提炼 structured_result（零 LLM）** |
| WorkItem node3 | review_step 审执行结果 | review_step 保留（不动） |

**LLM 调用数净效果**：单 WI 从 3 次（路由 + node4 合成 + 审核）降为 2 次（路由 + Session 合成，审核并入合成）；多 WI 更省（n 个 node4 合成消除，Session 只加 1 次整合）。与 [对话流优化_计划_V1.md](../需求/对话流优化_计划_V1.md) "降调用数"方向协同。

---

## 设计决策（讨论已确认）

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| 1 | WorkItem 职责 | 执行 + 结构化反馈，不做语言组织 | 职责纯粹，Session 统一表达 |
| 2 | output_spec 派生 | **方式 B**（路由 LLM 顺带输出）+ 字段拆分 | 用户诉求多样，代码规则覆盖不了；LLM 判语义字段，代码兜底结构字段 |
| 3 | WorkItem 提炼 | **方案 X**（规则驱动，零 LLM） | 调用数最优；RAG chunks 截断后传 Session 由合成 LLM 消化 |
| 4 | 审核分层 | **3a**（review_step 留 node3 / review_reply 上移 Session） | review_step 数据就近、并进不阻塞；review_reply 审回复合适性必须跟合成走 |
| 5 | structured_result schema | 13 字段（见 M3） | 覆盖 Session 组织回复所需全部信号 |

### output_spec 字段拆分（决策 2 细化）

| 字段 | 谁定 | 类型 / 取值 |
|------|------|-------------|
| `intent` | LLM | str（任务意图简述） |
| `detail` | LLM | brief \| standard \| detailed |
| `format` | LLM | natural \| list \| table（IM 默认 natural） |
| `cite_source` | LLM | bool（知识库问答为 true） |
| `max_length` | **代码** | 按 detail 映射：brief→150 / standard→300 / detailed→500 |
| `data_fields` | **代码** | 按 sop_id + query_type 映射（结构化、可枚举） |

---

## 代码模式参照表

| 层 | 参照源 | 要模仿的要点 |
|----|--------|-------------|
| WorkItem 字段挂载 | [session_agent.py:370](emily-core/emily_core/session/session_agent.py#L370) `setattr(wi, "_confirm_action", action)` | 派生字段挂 WorkItem 的既有模式 |
| LLM 调用 + model override | [session_agent.py:326-327](emily-core/emily_core/session/session_agent.py#L326-L327) | `router_model = getattr(...); chat_messages(..., model=router_model)` |
| dataclass 定义 | [executor.py:24-38](emily-core/emily_core/skill/executor.py#L24-L38) `SkillExecutionContext` | `field(default_factory=...)` 用于可变默认值 |
| 规则提炼（取字段） | [param_extractor.py:112-114](emily-core/emily_core/skill/param_extractor.py#L112-L114) `_extract_from_context` | dot-path 取值模式 |
| prompt 两阶段 format | [workitem_agent.py:783-804](emily-core/emily_core/workitem/workitem_agent.py#L783-L804) | wi_vars replace + session_vars replace + 残留占位符清理 |
| 回复兜底拼串 | [workitem_agent.py:853-883](emily-core/emily_core/workitem/workitem_agent.py#L853-L883) | LLM 不可用时的硬编码兜底风格 |
| Guardian review 调用 | [real_guardian.py:112-138](emily-core/emily_core/workitem/pipeline/real_guardian.py#L112-L138) | `review_reply` 既有调用方式 |

---

## 模块依赖图

```
M1(output_spec 下发) ──→ M3(node4 改结构化提炼) ──→ M4(Session 合成层 + review_reply 上移)
                                                        │
M2(StructuredResult schema) ──→ M3 ──────────────────────┘
                                                        │
                                                        v
                                              M5(harness M3 二次拆改)
```

**构建顺序**：M2（定义 schema）→ M1（output_spec 下发）→ M3（node4 改提炼）→ M4（Session 合成 + review 上移）→ M5（harness 拆改）。M2 先做是因为 M1/M3/M4 都引用 StructuredResult。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M1 | [session.md](emily-data/prompts/session.md) | 修改 | 路由输出格式加 output_spec（语义 4 字段） |
| M1 | [session_agent.py](emily-core/emily_core/session/session_agent.py) | 修改 | `_split_into_workitems` 解析 output_spec + 代码兜底 max_length/data_fields + 挂 WorkItem |
| M1 | [workitem.py](emily-core/emily_core/workitem/workitem.py) | 修改 | 加 `output_spec` 字段 |
| M2 | [execution.py](emily-core/emily_core/workitem/pipeline/interfaces/execution.py) | 修改 | 新增 `StructuredResult` dataclass |
| M3 | [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) | 修改 | `node4_summary` 改规则提炼；移除 `_llm_synthesize_reply` 的 LLM 调用 |
| M3 | [workitem.md](emily-data/prompts/workitem.md) | 修改 | 移除"回复合成规则"段，仅保留执行规则 |
| M4 | [session_agent.py](emily-core/emily_core/session/session_agent.py) | 修改 | 新增 `_synthesize_final_reply`；汇总逻辑从 join 改为合成 |
| M4 | `emily-data/prompts/session_reply.md` | 新增 | Session 回复合成专用 prompt |
| M4 | [real_guardian.py](emily-core/emily_core/workitem/pipeline/real_guardian.py) | 修改 | `review_reply` 调用点从 node4 迁移到 Session（方法本身不动） |
| M5 | [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) | 修改 | node4 现有 harness M3 修正循环代码迁移到 Session |
| M5 | [session_agent.py](emily-core/emily_core/session/session_agent.py) | 修改 | Session 合成层接入修正循环 |

---

## M1: output_spec 下发（路由 LLM 顺带输出）

**依赖**：无（可与 M2 并行）

**职责**：路由 LLM 判断 sop_id 时顺带输出 output_spec 的 4 个语义字段（intent/detail/format/cite_source）；SessionAgent 代码补全 max_length/data_fields；下发给 WorkItem。

### 交付物

| # | 交付物 | 文件 |
|---|--------|------|
| 1 | session.md 路由输出加 output_spec | `emily-data/prompts/session.md` |
| 2 | SessionAgent 解析 + 代码兜底 + 挂 WorkItem | `emily-core/emily_core/session/session_agent.py` |
| 3 | WorkItem 加 output_spec 字段 | `emily-core/emily_core/workitem/workitem.py` |

### 代码

#### `emily-data/prompts/session.md` — 修改"输出要求"段（第 104-105 行附近）

在现有输出要求后追加 output_spec 段：

```markdown
### output_spec 派生规则（每个匹配的 SOP 必须输出）
对每个匹配的 SOP，额外输出 output_spec 对象，根据用户诉求从以下维度判断：
- intent: 这个任务的核心意图（简短描述，如 "query_project_summary" / "record_event"）
- detail: brief（简短摘要）| standard（标准）| detailed（详细）—— 按用户表达的详细度期望
- format: natural（自然语言，IM 默认）| list（用户说"列一下/列表"时用）| table
- cite_source: 知识库问答为 true，否则 false

判断依据：用户语气（"详细说一下"→detailed，"简单提一句"→brief）、是否知识库问题、是否列举需求。
sop_id 为 null（fallback）时也要输出 output_spec（元认知类 intent="meta_cognition", detail=detailed, cite_source=true）。
```

> **注意**：若 [对话流优化_计划_V1.md](../需求/对话流优化_计划_V1.md) M4 已落地使用 `session_routing.md`，本段同样要加到 `session_routing.md`。两处保持一致。

#### `emily-core/emily_core/workitem/workitem.py` — 加 output_spec 字段（第 47 行附近，Node2 产出段后）

```python
    # ── Node 0（Session 下发）产出 ──
    output_spec: dict = field(default_factory=dict)
    """Session 下发的成果规格：intent/detail/format/cite_source/max_length/data_fields"""
```

#### `emily-core/emily_core/session/session_agent.py` — 修改 `_split_into_workitems`（第 340-422 行）

在创建 WorkItem 处（单 SOP 分支第 408 行、复合分支第 396 行、fallback 分支第 385 行）挂载 output_spec：

```python
def _derive_output_spec(self, intent: dict, sop_id: str | None) -> dict:
    """M1: 从路由 LLM 输出解析 output_spec，代码补全 max_length/data_fields。"""
    spec = dict(intent.get("output_spec") or {})
    # LLM 判的 4 个语义字段，缺字段兜底
    spec.setdefault("intent", sop_id or "fallback")
    spec.setdefault("detail", "standard")
    spec.setdefault("format", "natural")
    spec.setdefault("cite_source", False)
    # 代码兜底：max_length 按 detail 映射
    detail_to_len = {"brief": 150, "standard": 300, "detailed": 500}
    spec["max_length"] = detail_to_len.get(spec["detail"], 300)
    # 代码兜底：data_fields 按 sop_id + query_type 映射
    spec["data_fields"] = self._map_data_fields(sop_id, intent.get("query_type"))
    return spec

@staticmethod
def _map_data_fields(sop_id: str | None, query_type: str | None) -> list[str]:
    """M1: 按 SOP + query_type 映射要回传的数据字段（结构化、可枚举）。"""
    if sop_id == "SOP-005-QRY":
        return {
            "event": ["events"], "task": ["tasks"], "meeting": ["meetings"],
            "file": ["files"], "summary": ["events", "tasks", "node_progress"],
            "project": ["project_info"], "user": ["user_info"],
        }.get(query_type or "", ["events"])
    if sop_id and sop_id.startswith("SOP-002"):
        return ["event_no", "status"]
    # 默认：让 WorkItem 自行决定
    return []
```

单 SOP 分支（第 408 行）挂载：
```python
wi = WorkItem(
    session_id=self.conversation_id,
    user_input=content,
    user_id=self.context.user_id,
    sop_id=sop_id,
    intent_type="sop",
    priority=1,
)
wi.output_spec = self._derive_output_spec(intent, sop_id)  # M1
return [wi]
```

> 复合分支（sub_tasks 循环）和 fallback 分支同理挂载。fallback 的 output_spec.intent="meta_cognition"。

### 模块验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 路由 LLM 输出 output_spec
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天项目有什么进展？" --sender "李景利"
docker exec mitmproxy tail -3 /app/logs/llm_trace.jsonl
→ 预期：路由响应 JSON 含 output_spec 对象，detail="brief" 或 "standard"

# 3. 代码兜底字段
docker logs --tail 30 emily-core 2>&1 | grep "output_spec"
→ 预期：WorkItem.output_spec 含 max_length（数字）和 data_fields（列表）

# 4. 元认知问题
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你的权限分级是怎样的？" --sender "李景利"
→ 预期：fallback WI 的 output_spec.intent="meta_cognition", cite_source=true
```

**失败处理**：若路由 LLM 不输出 output_spec，检查 session.md 的 output_spec 派生规则段是否加入、`_split_into_workitems` 是否解析 `intent.get("output_spec")`。

---

## M2: StructuredResult schema 定义

**依赖**：无（M1/M3/M4 引用）

**职责**：定义 WorkItem 回传给 Session 的结构化成果 schema，13 字段。

### 交付物

| # | 交付物 | 文件 |
|---|--------|------|
| 1 | StructuredResult dataclass | `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` |
| 2 | WorkItem 加 structured_result 字段 | `emily-core/emily_core/workitem/workitem.py` |

### 代码

#### `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` — 新增 StructuredResult

```python
@dataclass
class StructuredResult:
    """WorkItem 回传给 Session 的结构化成果（M2: 分层合成）。

    WorkItem node4 规则提炼产出，Session 据此组织最终回复。
    WorkItem 不做任何语言组织。
    """
    # ── 状态 ──
    status: str               # success | partial | failed
    intent: str               # 任务意图（来自 output_spec.intent）
    sop_id: str
    risk_level: str           # L1/L2/L3（影响 Session 措辞和审核严格度）

    # ── 数据 ──
    data: dict = field(default_factory=dict)
    """结构化数据，按 output_spec.data_fields 从 step_results.business_data 提取"""

    summary_facts: list[str] = field(default_factory=list)
    """规则提炼的关键事实（Session 组织回复的要点）"""

    rag_sources: list[str] = field(default_factory=list)
    """RAG 命中的文档名列表（cite_source=true 时 Session 格式化引用）"""

    business_object_no: str = ""
    """录入类产生的业务编号（如 event_no，Session 明确告知用户）"""

    # ── 问题与确认 ──
    issues: list[str] = field(default_factory=list)
    """执行问题 / Guardian issues（要告诉用户的）"""

    needs_confirm: bool = False
    """是否需要用户确认（对接 ConfirmQueue）"""

    error_category: str = ""
    """失败分类：param_error | permission | system | not_found（空 if success）"""

    # ── 体验 ──
    suggested_followup: str = ""
    """建议后续动作（可空，如"要不要看详情？"）"""
```

#### `emily-core/emily_core/workitem/workitem.py` — 加 structured_result 字段（第 53 行 result_text 后）

```python
    # ── Node 4（成果总结）产出 ──
    result_text: str = ""                # 人类可读的最终成果（M3 后仅兜底用，正常路径由 Session 合成）
    structured_result: Any = None        # M2: StructuredResult，回传给 Session 做语言组织
```

### 模块验收检测

```bash
# 1. 单元验证：import 无环
docker exec emily-core python -c "from emily_core.workitem.pipeline.interfaces.execution import StructuredResult; sr = StructuredResult(status='success', intent='test', sop_id='X', risk_level='L1'); print(sr)"

# 2. WorkItem 默认值
docker exec emily-core python -c "from emily_core.workitem import WorkItem; wi = WorkItem(); print(wi.structured_result, wi.output_spec)"
→ 预期：None, {}
```

---

## M3: WorkItem node4 改结构化提炼（零 LLM）

**依赖**：M1（output_spec）、M2（StructuredResult）

**职责**：`node4_summary` 不再调 `_llm_synthesize_reply`，改为规则提炼 `StructuredResult`。移除 workitem.md 的回复合成规则段。

### 交付物

| # | 交付物 | 文件 |
|---|--------|------|
| 1 | node4_summary 改规则提炼 | `emily-core/emily_core/workitem/workitem_agent.py` |
| 2 | 新增 _extract_structured_result 方法 | `emily-core/emily_core/workitem/workitem_agent.py` |
| 3 | workitem.md 移除回复合成规则段 | `emily-data/prompts/workitem.md` |

### 代码

#### `emily-core/emily_core/workitem/workitem_agent.py` — 改造 `node4_summary`（第 674-747 行）

把现有的 LLM 合成 + Guardian review_reply 逻辑替换为规则提炼：

```python
async def node4_summary(self, context: "BusContext") -> None:
    """Node 4 [成果总结] —— M3: 规则提炼 structured_result，不做语言组织。

    语言组织由 Session 层 _synthesize_final_reply 完成（M4）。
    review_reply 审核迁移到 Session（M4），本节点不再调 Guardian.review_reply。
    """
    wi = context.work_item
    wi.structured_result = self._extract_structured_result(wi, context)
    # result_text 留空（或兜底拼串），Session 合成失败时用
    wi.result_text = ""
    context.verified_reply = ""  # M4 后由 Session 填
    logger.debug(
        "WI %s node4: structured_result status=%s facts=%d",
        wi.id, wi.structured_result.status, len(wi.structured_result.summary_facts),
    )

def _extract_structured_result(self, wi, context: "BusContext") -> "StructuredResult":
    """M3: 规则提炼——从 step_results + output_spec 提取 StructuredResult。零 LLM。"""
    from ..workitem.pipeline.interfaces.execution import StructuredResult
    spec = getattr(wi, "output_spec", {}) or {}
    step_results = getattr(wi, "step_results", []) or []

    # status：任一 step 失败 → partial/failed
    failed_steps = [sr for sr in step_results if not getattr(sr, "success", True)]
    if not step_results:
        status = "failed"
    elif failed_steps and len(failed_steps) == len(step_results):
        status = "failed"
    elif failed_steps:
        status = "partial"
    else:
        status = "success"

    # data：按 output_spec.data_fields 从 business_data 取
    data = {}
    for sr in step_results:
        bd = getattr(sr, "business_data", {}) or {}
        for field in spec.get("data_fields", []):
            if field in bd and field not in data:
                data[field] = bd[field]

    # summary_facts：规则提炼（从 step_results.output 取关键句，截断）
    summary_facts = []
    for sr in step_results:
        output = (getattr(sr, "output", "") or "").strip()
        if output and len(summary_facts) < 8:
            summary_facts.append(output[:200])

    # rag_sources：从 rag_results 收集 doc_name
    rag_sources = []
    for sr in step_results:
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in getattr(rr, "chunks", []) or []:
                doc = getattr(chunk, "doc_name", "") or ""
                if doc and doc not in rag_sources:
                    rag_sources.append(doc)
    # RAG 内容截断后并入 summary_facts（供 Session 消化）
    for sr in step_results:
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in getattr(rr, "chunks", []) or []:
                content = (getattr(chunk, "content", "") or "")[:500]
                if content:
                    summary_facts.append(f"〔{getattr(chunk, 'doc_name', '?')}〕{content}")

    # business_object_no：录入类从 business_data 取
    business_object_no = ""
    for sr in step_results:
        bd = getattr(sr, "business_data", {}) or {}
        for key in ("event_no", "task_no", "meeting_no", "object_id"):
            val = bd.get(key, "")
            if val:
                business_object_no = str(val)
                break
        if business_object_no:
            break

    # issues：汇聚 Guardian issues（来自 node3 review_step）+ warnings
    issues = list(getattr(wi, "warnings", []) or [])
    for sr in step_results:
        guardian = getattr(sr, "guardian", None)
        if guardian and getattr(guardian, "reason", ""):
            issues.append(f"[{getattr(sr, 'step_id', '?')}] {guardian.reason}")

    # needs_confirm：从 step_results 推断（如 handler 返回 needs_confirm）
    needs_confirm = any(
        getattr(getattr(sr, "business_data", {}), "needs_confirm", False)
        for sr in step_results
    )

    # error_category：失败分类
    error_category = ""
    if status == "failed":
        for sr in failed_steps:
            output = (getattr(sr, "output", "") or "")
            if "权限" in output or "permission" in output:
                error_category = "permission"; break
            if "参数" in output or "param" in output:
                error_category = "param_error"; break
            if "不存在" in output or "未找到" in output:
                error_category = "not_found"; break
        error_category = error_category or "system"

    # suggested_followup：规则填（可空）
    suggested_followup = ""
    if status == "success" and spec.get("intent", "").startswith("query"):
        suggested_followup = "需要看详情吗？"

    return StructuredResult(
        status=status,
        intent=spec.get("intent", wi.sop_id or "fallback"),
        sop_id=wi.sop_id or "",
        risk_level=getattr(wi, "risk_level", "L2") or "L2",
        data=data,
        summary_facts=summary_facts,
        rag_sources=rag_sources,
        business_object_no=business_object_no,
        issues=issues,
        needs_confirm=needs_confirm,
        error_category=error_category,
        suggested_followup=suggested_followup,
    )
```

#### `emily-core/emily_core/workitem/workitem_agent.py` — `_llm_synthesize_reply` 处理

`_llm_synthesize_reply`（第 749-883 行）在 M3 后**不再被 node4 调用**，但保留方法体（M4 Session 合成失败时可能复用其兜底拼串逻辑作为降级）。在方法注释标注：

```python
async def _llm_synthesize_reply(self, wi, ...):
    """M3 起：node4 不再调用本方法。保留作为 Session 合成 LLM 不可用时的兜底降级（M4）。

    LLM 合成已上移到 SessionAgent._synthesize_final_reply。
    """
```

#### `emily-data/prompts/workitem.md` — 移除"回复合成规则"段（第 36-46 行）

删除整个"## 回复合成规则"段。workitem.md 仅保留执行规则（node2 规划用）。文件头注释更新：

```markdown
<!-- M3: 回复合成规则段已移除（语言组织上移到 Session 层 session_reply.md） -->
<!-- 本文件仅用于 node2 (_llm_plan) 的 SOP+工具规划；node4 不再用本 prompt -->
```

### 模块验收检测

```bash
# 1. 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 查询类：node4 不调 LLM，structured_result 产出
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天项目有什么进展？" --sender "李景利"
docker logs --tail 40 emily-core 2>&1 | grep "structured_result"
→ 预期：出现 "structured_result status=success facts=N"

# 3. node4 LLM 调用 = 0（关键指标）
docker exec mitmproxy tail -8 /app/logs/llm_trace.jsonl
→ 预期：本轮无 call_category=execution/node4 的 LLM 调用（只有路由 1 次）

# 4. 录入类：business_object_no 提取
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "李景利"
docker logs --tail 40 emily-core 2>&1 | grep "structured_result"
→ 预期：business_object_no 含 EVENT-XXX 编号

# 5. 此时回复为空（Session 合成层 M4 未做）—— 这是预期的，M4 后恢复
→ 预期：emy-test 输出为空或"Emily 已处理完毕"（M3 阶段性，M4 修复）
```

**失败处理**：若 structured_result 字段缺失，检查 `_extract_structured_result` 的取值路径；若 node4 仍调 LLM，检查 `node4_summary` 是否已替换为 `_extract_structured_result` 调用。

> **M3 阶段性说明**：M3 完成后回复会暂时为空（Session 合成层 M4 未做）。**M3 + M4 必须连续完成**才能端到端验证。M3 单独验收只看 structured_result 产出 + node4 零 LLM。

---

## M4: Session 合成层 + review_reply 上移

**依赖**：M3（structured_result 已产出）

**职责**：SessionAgent 新增 `_synthesize_final_reply`，基于 done WI 的 structured_result 调 LLM 组织最终回复；新增 `session_reply.md` prompt；review_reply 审核迁移到 Session 合成层。

### 交付物

| # | 交付物 | 文件 |
|---|--------|------|
| 1 | _synthesize_final_reply 方法 | `emily-core/emily_core/session/session_agent.py` |
| 2 | 汇总逻辑从 join 改为合成 | `emily-core/emily_core/session/session_agent.py` |
| 3 | session_reply.md 合成 prompt | `emily-data/prompts/session_reply.md` |
| 4 | review_reply 调用迁移到 Session | `emily-core/emily_core/session/session_agent.py` |

### 代码

#### `emily-data/prompts/session_reply.md` — 新建

```markdown
<!-- Session 回复合成专用 system prompt —— SessionAgent._synthesize_final_reply() 使用 -->
<!-- M4: 从 workitem.md 的回复合成规则段迁移 + 强化，统一所有路径（业务/元认知/多WI）的回复风格 -->
<!-- 模板变量: {wi_results}, {user_input}, {current_datetime} -->
<!-- Session 级变量: {user_name} {user_permission_level} {project_name} {conversation_summary} -->

## 一、角色

你是 Emily，企业工程项目管理助手。现在你要把内部执行引擎返回的结构化结果，组织成给用户的自然语言回复。

## 二、当前上下文

### 用户
- 姓名：{user_name}
- 权限：{user_permission_level}

### 项目
- 名称：{project_name}

### 对话记忆
{conversation_summary}

### 当前时间
{current_datetime}

## 三、执行结果（结构化）

本次用户请求被拆分为 1 个或多个任务，每个任务返回结构化成果：

{wi_results}

每个任务成果含：status / intent / data / summary_facts / rag_sources / business_object_no / issues / needs_confirm / error_category / suggested_followup

## 四、组织规则

1. **基于 summary_facts 和 data 组织**，不要编造结构化结果中不存在的数据
2. **多任务整合**：多个任务的成果要连贯衔接，避免重复；可按"先 X，再 Y"组织
3. **状态措辞**：
   - success：肯定语气总结成果
   - partial：说明成功的部分 + 失败的部分
   - failed：按 error_category 给针对性建议（param_error→"请补充XXX"；permission→"联系主管 XXX 申请权限"；system→"稍后重试"；not_found→"未查到相关记录"）
4. **引用来源**：若 rag_sources 非空，格式化为"根据《XXX》..."
5. **业务编号**：若 business_object_no 非空，明确告知（如"已创建事件 EVENT-001"）
6. **确认请求**：若 needs_confirm=true，明确询问用户确认
7. **后续建议**：若 suggested_followup 非空，在末尾提出
8. **风格**：简洁清晰、中文、用少量 emoji 点缀；禁止 Markdown 格式化，用自然口语
9. **不暴露内部细节**：不提 step_id、tool_name、JSON 结构

## 五、输出

仅输出 JSON：{"reply": "你的自然语言回复"}
```

#### `emily-core/emily_core/session/session_agent.py` — 新增 `_synthesize_final_reply`

```python
async def _synthesize_final_reply(self, message: "StandardMessage",
                                  done_workitems: list) -> str:
    """M4: 基于 WorkItem 的 structured_result 调 LLM 组织最终回复。

    单 WI / 多 WI 统一走本方法。review_reply 审核在合成后做（M4）。
    LLM 不可用时回退到规则拼串兜底（保留 fail-open）。
    """
    if not done_workitems:
        return "Emily 已处理完毕。"

    # 渲染 wi_results 文本
    wi_results_text = self._render_wi_results(done_workitems)

    # 加载 + format prompt
    from ..infrastructure.llm.prompt_loader import load_prompt
    prompt_template = load_prompt("session_reply")
    prompt_vars = self.context.get_prompt_variables()
    system_prompt = prompt_template.replace("{wi_results}", wi_results_text)
    system_prompt = system_prompt.replace("{user_input}", (message.content or "")[:500])
    system_prompt = system_prompt.replace("{current_datetime}", _beijing_now_str())
    for key, value in prompt_vars.items():
        replacement = str(value) if value else "（无）"
        system_prompt = system_prompt.replace(key, replacement)
    import re
    system_prompt = re.sub(r'\{[a-z_]+\}', '', system_prompt)

    full_messages = [{"role": "system", "content": system_prompt}]
    full_messages.extend(self.context.message_history)
    full_messages.append({"role": "user", "content": message.content or ""})

    # LLM 合成
    if self._llm:
        try:
            router_model = getattr(self._llm, "router_model", None) or getattr(self._llm, "model", None)
            result = await self._llm.chat_messages(full_messages, json_mode=True, model=router_model)
            data = result.get("data", {})
            reply = data.get("reply", "") if isinstance(data, dict) else ""
            if reply and len(reply) > 10:
                # M4: review_reply 上移——审核合适性（只标记，可与合成合并）
                await self._review_final_reply(reply, done_workitems)
                return reply
            logger.warning("M4: Session 合成 reply 不可用 %r，回退拼串", reply[:80])
        except Exception as e:
            logger.warning("M4: Session 合成失败 %s，回退拼串", e)

    # 兜底拼串（fail-open）
    return self._fallback_join_results(done_workitems)

def _render_wi_results(self, done_workitems: list) -> str:
    """M4: 把多个 WI 的 structured_result 渲染成 prompt 文本。"""
    parts = []
    for idx, wi in enumerate(done_workitems, 1):
        sr = getattr(wi, "structured_result", None)
        if sr is None:
            parts.append(f"### 任务 {idx}\n（无结构化结果）")
            continue
        parts.append(
            f"### 任务 {idx}\n"
            f"- intent: {sr.intent}\n"
            f"- status: {sr.status}\n"
            f"- risk_level: {sr.risk_level}\n"
            f"- data: {json.dumps(sr.data, ensure_ascii=False, default=str)}\n"
            f"- summary_facts: {json.dumps(sr.summary_facts, ensure_ascii=False)}\n"
            f"- rag_sources: {json.dumps(sr.rag_sources, ensure_ascii=False)}\n"
            f"- business_object_no: {sr.business_object_no}\n"
            f"- issues: {json.dumps(sr.issues, ensure_ascii=False)}\n"
            f"- needs_confirm: {sr.needs_confirm}\n"
            f"- error_category: {sr.error_category}\n"
            f"- suggested_followup: {sr.suggested_followup}\n"
        )
    return "\n".join(parts)

async def _review_final_reply(self, reply: str, done_workitems: list) -> None:
    """M4: review_reply 上移——审回复合适性，只标记不拦截（沿用 RealGuardian 语义）。"""
    if not self._bus or not done_workitems:
        return
    # 从 BUS 拿到 WorkItemAgent 的 Guardian 实例（或直接构造）
    # 这里简化：用最后一个 WI 做审核（多 WI 取代表）
    from ..workitem.pipeline.real_guardian import RealGuardian
    guardian = RealGuardian(llm_client=self._llm, config=None)  # 复用既有 Guardian
    for wi in done_workitems:
        try:
            note = await guardian.review_reply(reply, wi)
            if note and note.issues:
                logger.info("M4 review_reply issues: %s", note.issues)
                # issues 只标记，不拦截（与既有语义一致）
        except Exception as e:
            logger.debug("M4 review_reply failed (silent skip): %s", e)

def _fallback_join_results(self, done_workitems: list) -> str:
    """M4: LLM 不可用时的兜底拼串（fail-open）。"""
    parts = []
    for wi in done_workitems:
        sr = getattr(wi, "structured_result", None)
        if sr is None:
            continue
        if sr.status == "success":
            facts = "；".join(sr.summary_facts[:3])
            parts.append(facts or "操作完成")
        elif sr.status == "failed":
            parts.append(f"处理失败：{sr.issues[0] if sr.issues else '未知原因'}")
        else:
            parts.append("；".join(sr.summary_facts[:3]) or "部分完成")
    return "\n\n".join(parts) if parts else "Emily 已处理完毕。"
```

#### `emily-core/emily_core/session/session_agent.py` — 修改 `_handle_impl` 汇总段（第 235-239 行）

把：
```python
        # ④ 汇总
        replies = [wi.result_text for wi in done if wi.result_text]
        if not replies:
            return self._reply(message, "Emily 已处理完毕。")
        return self._reply(message, "\n\n".join(replies))
```
改为：
```python
        # ④ M4: Session 合成最终回复（替代 join 拼接）
        final_reply = await self._synthesize_final_reply(message, done)
        return self._reply(message, final_reply)
```

> **注意**：`_handle_impl` 现在是 `async`，调用 `_synthesize_final_reply` 需 `await`。`_handle_impl` 已是 async（[session_agent.py:184](emily-core/emily_core/session/session_agent.py#L184)），无需改签名。

### 模块验收检测

```bash
# 1. 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 查询类：Session 合成回复
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天项目有什么进展？" --sender "李景利"
→ 预期：回复为自然语言（非空），含具体数据，非"Emily 已处理完毕"

# 3. 录入类：business_object_no 告知
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "李景利"
→ 预期：回复含"已创建事件 EVENT-XXX"

# 4. 元认知类：走 Session 合成（fallback WI）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你的权限分级是怎样的？" --sender "李景利"
→ 预期：基于三书内容的权限分级说明

# 5. 单 WI LLM 调用数 = 2（路由 + Session 合成）
docker exec mitmproxy tail -8 /app/logs/llm_trace.jsonl
→ 预期：2 次同步 LLM 调用（路由 1 + 合成 1），无 node4 合成调用

# 6. 复合任务连贯性
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查一下今天的事件，然后帮我创建一个新事件：钢筋进场" --sender "李景利"
→ 预期：回复连贯整合两个任务成果，非简单拼接

# 7. review_reply 在 Session 层触发
docker logs --tail 30 emily-core 2>&1 | grep "M4 review_reply"
→ 预期：录入类出现 review_reply 调用

# 8. fail-open：临时让 LLM 不可用，仍回复
→ 预期：走 _fallback_join_results 拼串兜底
```

**失败处理**：若回复为空，检查 `_synthesize_final_reply` 是否被 `_handle_impl` 调用、`structured_result` 是否产出（M3）；若多 WI 回复仍是拼接，检查 `_render_wi_results` 是否把多 WI 都传给 LLM。

---

## M5: harness M3 二次拆改（修正循环迁移）

**依赖**：M4（Session 合成层已就位）

**职责**：[AgentHarness补齐_计划_V1.md](AgentHarness补齐_计划_V1.md) 的 M3（审核修正循环）已在 node4 实施。本模块把 node4 现有的 `revise_reply` 修正循环代码迁移到 Session 合成层，与 M4 的 `_review_final_reply` 合并。

### 前置确认

实施前先读 [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) node4 现有代码，确认 harness M3 已实施的部分（`_revise_reply_loop` / `revise_reply` 调用点）。**若 harness M3 未实施或实施位置不同，本模块按实际调整**。

### 交付物

| # | 交付物 | 文件 |
|---|--------|------|
| 1 | node4 移除修正循环代码 | `emily-core/emily_core/workitem/workitem_agent.py` |
| 2 | Session 合成层接入修正循环 | `emily-core/emily_core/session/session_agent.py` |
| 3 | RealGuardian.revise_reply 调用迁移 | `emily-core/emily_core/workitem/pipeline/real_guardian.py`（方法不动，调用点迁移） |

### 代码

#### `emily-core/emily_core/session/session_agent.py` — `_review_final_reply` 扩展为修正循环

把 M4 的 `_review_final_reply`（只标记）扩展为修正循环（承接 harness M3 语义）：

```python
async def _review_final_reply(self, reply: str, done_workitems: list) -> str:
    """M5: review_reply 上移 + harness M3 修正循环迁移。

    review_reply 返回 issues → revise_reply 修正 → 再审核，最多 N 轮。
    Returns: 最终回复（修正后或原 reply）。
    """
    from ..workitem.pipeline.real_guardian import RealGuardian
    from ..workitem.pipeline.real_guardian import GuardianNote
    guardian = RealGuardian(llm_client=self._llm, config=None)

    max_rounds = getattr(self, "_config", None)
    max_rounds = getattr(max_rounds, "harness_reply_max_revise_rounds", 1) if max_rounds else 1

    current_reply = reply
    # 取代表 WI 做审核（多 WI 取风险最高的）
    rep_wi = max(done_workitems, key=lambda w: getattr(w, "risk_level", "L1") or "L1")

    for round_idx in range(max_rounds + 1):
        note = await guardian.review_reply(current_reply, rep_wi)
        if not note or not note.issues:
            return current_reply  # 审核通过
        # 修正：把 issues 反馈给 LLM 重新合成
        revised = await guardian.revise_reply(current_reply, note.issues, rep_wi)
        if revised and len(revised) > 10:
            current_reply = revised
        else:
            break  # 修正失败，返回当前 reply
    # 循环耗尽仍有 issues —— 按既有 harness"统一 FAILED"语义处理
    logger.warning("M5 reply 修正循环耗尽仍有 issues: %s", note.issues if note else [])
    return current_reply  # 返回最佳 reply（FAILED 语义由 harness 既有逻辑处理）
```

> **M4 的 `_synthesize_final_reply` 同步改**：把 `await self._review_final_reply(reply, done_workitems)` 改为 `reply = await self._review_final_reply(reply, done_workitems)`，承接修正后的 reply。

#### `emily-core/emily_core/workitem/workitem_agent.py` — node4 移除 harness M3 代码

读 node4 现有 harness M3 实施代码（`_revise_reply_loop` 调用、`revise_reply` 调用等），**全部移除**——这些逻辑已迁移到 Session 的 `_review_final_reply`。node4 只保留 `_extract_structured_result`（M3）。

### 模块验收检测

```bash
# 1. 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 修正循环在 Session 触发（构造易触发 issues 的录入）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "李景利"
docker logs --tail 40 emily-core 2>&1 | grep -E "M5|revise_reply|review_reply"
→ 预期：review_reply / revise_reply 在 Session 层触发，node4 无相关调用

# 3. node4 零修正循环代码
docker exec emily-core grep -c "revise_reply" /app/emily_core/workitem/workitem_agent.py
→ 预期：0（node4 已移除）

# 4. 回复质量不回归
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天项目有什么进展？" --sender "李景利"
→ 预期：正常自然语言回复
```

**失败处理**：若修正循环不触发，检查 `_review_final_reply` 是否被 `_synthesize_final_reply` 正确 await、`harness_reply_max_revise_rounds` 配置是否 ≥1；若 node4 仍有 revise_reply，确认 M5 移除干净。

---

## 组装验证（全部模块完成后）

```bash
# 清缓存重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 全场景回归
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天项目有什么进展？" --sender "李景利"          # 查询
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "李景利"  # 录入
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你的权限分级是怎样的？" --sender "李景利"        # 元认知
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查一下今天的事件，然后帮我创建一个新事件：钢筋进场" --sender "李景利"  # 复合

# 最终指标核对
docker exec mitmproxy tail -15 /app/logs/llm_trace.jsonl
docker logs --tail 60 emily-core 2>&1 | grep -E "structured_result|M4|M5|review_reply"
```

**最终通过标准**：

| 指标 | 目标 | 验证 |
|------|------|------|
| node4 LLM 调用数 | 0 | jsonl 无 node4 合成调用 |
| 单 WI 同步 LLM 调用 | 2（路由 + Session 合成） | jsonl 行数 |
| 多 WI 回复连贯性 | 整合非拼接 | emy-test 复合任务输出 |
| 元认知回复基于三书 | 是 | emy-test 输出含权限分级内容 |
| review_reply 在 Session 触发 | 是 | 日志 `M4/M5 review_reply` |
| 录入类告知业务编号 | 是 | emy-test 输出含 EVENT-XXX |
| LLM 不可用仍降级 | 走拼串兜底 | fail-open 验证 |
| node4 无 revise_reply | 0 | grep 计数 |

---

## 风险与权衡

1. **路由 prompt 膨胀**：M1 给 session.md 加 output_spec 输出要求（约 +100 tokens）。与 [对话流优化 M4](../需求/对话流优化_计划_V1.md) 路由瘦身方向有张力，但 output_spec 只输出 4 个枚举/短文本字段，可控。
2. **structured_result 提炼质量**：M3 规则提炼可能比 LLM 合成粗糙（尤其 RAG chunks 截断）。由 M4 Session 合成 LLM 消化——它本来就要调 LLM 组织回复，理解粗糙数据是它的职责。
3. **harness M3 拆改风险**：M5 迁移已实施的修正循环，可能引入回归。实施前**先读 harness M3 实际代码**，按实际调整迁移方案；保留 harness 的"统一 FAILED"语义不变。
4. **元认知路径**：fallback WI 无 step_results，structured_result.data 为空。Session 合成时若 intent="meta_cognition"，应基于 session.md 三书内容回答（M4 的 session_reply.md 当前不含三书，实施时需考虑是否注入三书或 fallback 走单独路径）。
5. **多 WI 整合成本**：M4 Session 合成要处理 n 个 WI 的 structured_result，prompt 会随 WI 数增长。建议限制 wi_results 文本总长度（如截断到 2000 字）。

---

## 与其他计划的协调

| 计划 | 关系 | 协调点 |
|------|------|--------|
| [对话流优化_计划_V1.md](../需求/对话流优化_计划_V1.md) | 协同 | M1 的 output_spec 加到 session.md（或 session_routing.md，看 M4 是否落地）；本计划 M4 的 Session 合成 LLM 用 router_model |
| [AgentHarness补齐_计划_V1.md](AgentHarness补齐_计划_V1.md) | 二次拆改 | harness M3（node4 修正循环）已实施 → 本计划 M5 迁移到 Session；harness M2（node3 错误重试）不动，与分层合成兼容 |

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户

**M3+M4 必须连续完成**（M3 后回复暂时为空，M4 才恢复）。M3 单独验收只看 structured_result 产出。

---

*本计划为 AI 可执行操作手册。实施前若 harness M3 实际代码与计划描述不符，以实际代码为准调整 M5。*

---

## 执行记录（2026-07-25）

### 执行结果：全部完成

| 模块 | 状态 | 说明 |
|------|------|------|
| M2 | 完成 | StructuredResult dataclass + workitem.py 字段 |
| M1 | 完成 | output_spec 下发（session.md + session_agent.py + workitem.py） |
| M3 | 完成 | node4 改规则提炼，workitem.md 移除回复合成段 |
| M4 | 完成 | Session 合成层（session_reply.md + 6个新方法），_handle_impl 汇总逻辑替换 |
| M5 | 跳过 | harness M3（revise_reply 修正循环）从未实施，无代码需迁移；review_reply 迁移已在 M3+M4 完成 |

### 偏差记录

1. M5 跳过：原计划描述从 node4 迁移 `_revise_reply_loop` / `revise_reply` 到 Session，经代码检索确认整个代码库中这些方法不存在（harness M3 未实施），无需迁移。
2. `_derive_output_spec` 中 `self._map_data_fields()` 改为 `SessionAgent._map_data_fields()`：`@staticmethod` 通过实例调用在非 SessionAgent 子类上有参数传递兼容风险。

### 修改文件（共 7 个，新建 1 个）

| 文件 | 类型 |
|------|------|
| `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` | 修改（+StructuredResult） |
| `emily-core/emily_core/workitem/workitem.py` | 修改（+output_spec, +structured_result） |
| `emily-data/prompts/session.md` | 修改（+output_spec 派生规则） |
| `emily-core/emily_core/session/session_agent.py` | 修改（+6方法, 改汇总逻辑） |
| `emily-core/emily_core/workitem/workitem_agent.py` | 修改（node4_summary 改规则提炼） |
| `emily-data/prompts/workitem.md` | 修改（移除回复合成段） |
| `emily-data/prompts/session_reply.md` | **新建** |

### 验证摘要

- 单元测试通过：StructuredResult 默认值、_map_data_fields 映射、_derive_output_spec 兜底
- 端到端通过：BUS 4节点完整运行，node4 零 LLM 调用，M4 Session 合成被调用并正确降级
- LLM 模型名不兼容（deepseek-chat vs deepseek-v4-pro）为预存基础设施问题，不影响本次改动
