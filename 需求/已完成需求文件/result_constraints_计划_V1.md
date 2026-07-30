# SessionAgent → WorkItem 结果约束传递 — AI 执行计划

> **基于需求**：用户提出的"SessionAgent 在向 WorkItem 发布任务时应增加结果约束要求"
> **计划版本**：v1.0
> **目标**：SessionAgent 在 LLM 意图识别时额外提取用户的结果约束（scope / filters / must_include / must_not），结构化传递给 WorkItem，供 node2 规划、node3 执行和 node4 验证消费

---

## 你的角色

你作为 **Emily开发者资深架构师** + **实施计划编制专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：不修改 `_recognize_intent`、`_split_into_workitems`、`_llm_plan`、`_extract_structured_result` 的方法签名，只在内部新增逻辑
2. **不新增 LLM 调用**：`result_constraints` 来自 `_recognize_intent` 同一次 LLM 调用的输出，不额外发请求
3. **向后兼容**：新增字段均为可选，老 prompt 不带该字段时不崩溃
4. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
5. **参照已有模式**：新代码风格与现有代码一致，参照对应源文件

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `WorkItem` | `emily_core/workitem/workitem.py:22` | 纯 dataclass，30+ 字段 | 新增 `result_constraints: dict` 字段 |
| `_derive_output_spec` | `emily_core/session/session_agent.py:566` | 从 intent 字典提取结构化字段 | 参照模式新增 `_derive_constraints` |
| `_llm_plan` | `emily_core/workitem/workitem_agent.py:296` | 构建 planner prompt + 请求 LLM | 在 {variables} 中新增约束注入 |
| `_extract_structured_result` | `emily_core/workitem/workitem_agent.py:704` | 零 LLM → StructuredResult | 新增约束校验算 warnings |
| prompt `session.md` | `emily-data/prompts/session.md` | Session LLM 意图识别 system prompt | 新增 `result_constraints` 输出字段规则 |
| prompt 默认值 `planner` | `prompt_loader.py:93` | node2 规划 system prompt | 新增 `{result_constraints}` 模板变量 |

### 架构决策

选择 **SessionAgent 阶段一次 LLM 调用提取**而非 node2 单独调用。理由：SessionAgent 有完整对话上下文（历史消息、群上下文、用户记忆），node2 只有 `user_input` 原文。约束提取需要语境（如"刚才说的那个节点"指什么），放在 SessionAgent 侧准确度更高。

### 代码模式参照表

| 层 | 参照源 | 要模仿的要点 |
|----|--------|-------------|
| dataclass 字段新增 | `workitem.py` L30-39 | `output_spec` 字段的声明风格 + 默认值 pattern |
| prompt 变量注入 | `session_agent.py` L566-575 | `_derive_output_spec` 的 `dict()` + `setdefault` pattern |
| planner prompt 变量替换 | `workitem_agent.py` L332-337 | `.format()` 调用的变量注入模式 |
| 约束验证逻辑 | `workitem_agent.py` L709-717 | `_extract_structured_result` 中 status 判定的 pattern |

---

## 模块依赖图

```
M0(prompt 输出 schema)
  │
  ├──→ M1(WorkItem 字段)
  │
  └──→ M2(SessionAgent 提取)
         │
         ├──→ M3(planner prompt 模板变量)
         │
         └──→ M4(node2 注入约束)
                │
                └──→ M5(node4 校验)
```

M0 和 M1 互不依赖，可并行。M2 依赖 M0 + M1。M3/M4/M5 依次串行。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心类/函数 |
|------|----------|----------|------------|
| M0 | `emily-data/prompts/session.md` | 修改 | 新增 `result_constraints` 输出规则段落 |
| M1 | `emily_core/workitem/workitem.py` | 修改 | `WorkItem` 新增 `result_constraints` 字段 |
| M2 | `emily_core/session/session_agent.py` | 修改 | `_derive_constraints()` 新增；`_split_into_workitems()` 调用 |
| M3 | `emily_core/infrastructure/llm/prompt_loader.py` | 修改 | `planner` 硬编码 prompt 新增 `{result_constraints}` zone |
| M4 | `emily_core/workitem/workitem_agent.py` | 修改 | `_llm_plan()` 注入约束到 planner prompt |
| M5 | `emily_core/workitem/workitem_agent.py` | 修改 | `_extract_structured_result()` 新增约束校验块 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `session.md` | 扩展 | 在 `query_type` 规则段后新增"result_constraints 派生规则"段落 |
| `prompt_loader.py` | 修改 | `planner` 硬编码字符串新增 `{result_constraints}` 模板变量 |
| `workitem.py` | 扩展 | `WorkItem` dataclass 新增 1 个字段 |
| `session_agent.py` | 扩展 | 新增 `_derive_constraints()` 方法，`_split_into_workitems` 调用 |
| `workitem_agent.py` | 修改 | `_llm_plan` 注入变量，`_extract_structured_result` 新增校验 |
| 其余文件 | 不变 | — |

---

## 脚本结构约定

本次改动均为框架内嵌修改，不涉及独立脚本。所有改动直接集成到 EmilyCore 初始化链路，无需单独脚本。

---

## M0: session.md prompt — 新增 result_constraints 输出 schema

**依赖**：无

**职责**：在 SessionAgent 意图识别的 LLM system prompt 中，新增 `result_constraints` 输出字段定义，让 LLM 在一次调用中同时输出约束信息。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 新增 output schema 规则段落 | `emily-data/prompts/session.md` 在 `query_type 派生规则` 段落后追加 |

### 代码

#### `emily-data/prompts/session.md` — 在 L58（query_type 段落后）追加

在文件第 58 行之后（`## 三、当前会话上下文` 之前），追加以下段落：

```markdown
### result_constraints 派生规则（每个请求均输出）
从用户的表达中提取对执行结果的约束要求，结构化传递给下游执行链。输出 result_constraints 对象，包含：

- scope: 范围限定（如指定项目/节点/人员/时间范围）。示例：{"project": "翠湖庭院", "responsible_user": "王建国", "time_range": "本周"}
- filters: 过滤条件列表（如"不要已完成的""只看待办的""排除某类型"）。示例：["exclude_completed", "only_pending"]
- must_include: 结果中必须包含的信息维度（如"必须列出负责人""必须有截止日期"）。示例：["节点名称", "截止日期", "负责人"]
- must_not: 结果中不得出现的内容（如"不要列已完成的""不要提费用"）。示例：["不要列已完成的节点", "不要提预算"]

提取原则：
- 用户没有明确表达约束时，输出空对象 `{}`
- scope/filters/must_include/must_not 均为可选字段，有则输出，无则省略
- 约束应基于用户**明确表达**的需求，不要自行臆测或添加
- 结合对话上下文理解指代（如用户说"刚才那个项目"，应解析为具体项目名）

无约束时输出：{}

参考示例：
- 用户说"看看翠湖庭院的进度" → 提取 scope.project="翠湖庭院"
- 用户说"别列已完成的，只看王建国负责的" → 提取 filters=["exclude_completed"] + scope.responsible_user="王建国"
- 用户说"帮我记一下样板段放线完成" → 提取 {}（无额外约束，仅录入）
- 用户说"详细说说那个问题" → 提取 {}（依赖对话上下文，无法结构化为项目/人员/时间范围）

```

### 模块验收检测

```bash
# 验收 1：文件包含新增段落
grep -c "result_constraints 派生规则" emily-data/prompts/session.md
→ 预期输出：1

# 验收 2：scope / filters / must_include / must_not 关键字存在
grep -c "scope:" emily-data/prompts/session.md
→ 预期输出：≥ 1

grep -c "filters:" emily-data/prompts/session.md
→ 预期输出：≥ 1
```

**失败处理**：检查追加位置是否正确（在 query_type 段落和 `## 三、` 之间）；确认没有重复追加

---

## M1: WorkItem dataclass — 新增 result_constraints 字段

**依赖**：M0（概念依赖，非代码依赖——可并行执行）

**职责**：在 WorkItem 数据类上新增 `result_constraints` 字段，作为 SessionAgent → 执行链的结构化约束载体。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 新增 `result_constraints` 字段 | `emily-core/emily_core/workitem/workitem.py` |

### 代码

#### `emily-core/emily_core/workitem/workitem.py` — 在 `output_spec` 字段（L45-46）后追加

```python
# emily-core/emily_core/workitem/workitem.py
# 在 output_spec: dict = field(default_factory=dict) 之后（约 L47）追加：

    # ── M2: Session 下发的执行约束（scope/filters/must_include/must_not）──
    result_constraints: dict = field(default_factory=dict)
    """SessionAgent 从用户表达中提取的结果约束，供 node2 规划和 node4 验证使用。
    
    Structure:
        scope: dict       — {"project": "...", "responsible_user": "...", "time_range": "..."}
        filters: list[str] — ["exclude_completed", "only_pending"]
        must_include: list[str] — ["节点名称", "截止日期"]
        must_not: list[str] — ["不要已完成节点"]
    """
```

### 模块验收检测

```bash
# 验收 1：字段声明存在
grep "result_constraints" emily-core/emily_core/workitem/workitem.py
→ 预期输出：至少 2 行（字段声明 + 文档注释）

# 验收 2：import 正确
uv run python -c "from emily_core.workitem.workitem import WorkItem; w = WorkItem(); print(w.result_constraints)"
→ 预期输出：{}
```

**失败处理**：检查字段语法（dataclass field 声明）、确认缩进与相邻字段对齐

---

## M2: SessionAgent — 提取并写入 result_constraints

**依赖**：M0 + M1

**职责**：在 `_split_into_workitems()` 中，从 LLM 意图识别结果提取 `result_constraints`，写入 WorkItem。新增 `_derive_constraints()` 静态方法。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | `_derive_constraints()` 新方法 | `emily-core/emily_core/session/session_agent.py` |
| 2 | `_split_into_workitems()` 调用 | `emily-core/emily_core/session/session_agent.py` |

### 代码

#### `emily-core/emily_core/session/session_agent.py` — 在 `_derive_output_spec`（L565）之前追加新方法

```python
# emily-core/emily_core/session/session_agent.py
# 在 _derive_output_spec 方法定义之前（约 L566）追加：

    @staticmethod
    def _derive_constraints(intent: dict) -> dict:
        """从路由 LLM 输出解析 result_constraints，兜底返回空 dict。"""
        raw = intent.get("result_constraints") or {}
        if not isinstance(raw, dict):
            return {}
        # 仅保留已知四个子字段，过滤 LLM 可能产生的多余字段
        constraints = {}
        for key in ("scope", "filters", "must_include", "must_not"):
            val = raw.get(key)
            if key == "scope" and isinstance(val, dict) and val:
                constraints["scope"] = val
            elif key in ("filters", "must_include", "must_not") and isinstance(val, list) and val:
                constraints[key] = val
        return constraints
```

#### `emily-core/emily_core/session/session_agent.py` — 在 `_split_into_workitems` 中，`wi.output_spec = ...` 之后追加

在每个 WorkItem 构造后、`wi.output_spec = self._derive_output_spec(...)` 行之后，追加一行：

```python
# 在每个 wi.output_spec = ... 之后追加（共 4 处：L530/L546/L557/L564 附近）
        wi.result_constraints = self._derive_constraints(intent)
```

> 注意：fallback 路径的 intent 是局部 `intent` 变量（L477），compound 路径的 st 是子任务 dict。所有四个 WorkItem 构造点都需要追加。

### 模块验收检测

```bash
# 验收 1：新方法存在
grep "_derive_constraints" emily-core/emily_core/session/session_agent.py
→ 预期输出：≥ 2 行（定义 + 调用）

# 验收 2：import 正常
uv run python -c "from emily_core.session.session_agent import SessionAgent; print(hasattr(SessionAgent, '_derive_constraints'))"
→ 预期输出：True

# 验收 3：空约束解析正确
uv run python -c "from emily_core.session.session_agent import SessionAgent; r = SessionAgent._derive_constraints({}); print(r)"
→ 预期输出：{}
```

**失败处理**：检查所有 4 个 WorkItem 构造点是否都加了；确认 `_derive_constraints` 缩进与 `_derive_output_spec` 一致

---

## M3: planner prompt — 新增 {result_constraints} 模板变量

**依赖**：M2（逻辑依赖，需知道约束的数据结构）

**职责**：在 node2 规划 prompt 中新增约束注入区，让 LLM 规划时能遵守 SessionAgent 下发的约束。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 修改 planner prompt 字符串 | `emily-core/emily_core/infrastructure/llm/prompt_loader.py` |

### 代码

#### `emily-core/emily_core/infrastructure/llm/prompt_loader.py` — 修改 `"planner"` 字符串（L93-115）

在 `_DEFAULTS["planner"]` 中，`## 用户输入\n{user_input}` 之前插入新行：

```python
    "planner": """你是 Emily 的执行规划器。根据业务流程（SOP）和用户输入，制定逐步的执行计划。

## SOP 参考
{sop_text}

## 执行约束（来自上游意图识别）
{result_constraints}

## 用户输入
{user_input}

## 可用工具
{available_tools}

## 规划规则
1. 根据 SOP 中定义的流程阶段拆解步骤（通常 2-5 步）
2. 每个步骤绑定一个具体的工具（tool_name），从"可用工具"列表中选择
3. 如果需要查询领域知识（规范标准、施工工艺、政策法规等），应在执行业务工具之前先调用 knowledge_search 获取相关知识
4. 步骤间如有依赖关系，在 depends_on 中标明
5. 评估整体风险等级：L1(低风险-查询类) / L2(中风险-录入类) / L3(高风险-删除/批量修改)
6. 对于需要工具调用的步骤，在 tool_params 中提供完整的参数对象
7. 如果存在"执行约束"，必须在规划时考虑：scope 限定工具参数的查询范围，filters/must_not 在步骤中添加过滤条件

## 输出格式
仅输出一个 JSON 对象（不要包含其他文字）：
{{"risk_level": "L1|L2|L3", "steps": [{{"step_id": "step-01", "description": "步骤描述", "tool_name": "record_event|null", "tool_params": {{"title": "事件标题", "event_type": "施工节点", "description": "详细描述"}}, "expected_output": "预期产出", "depends_on": []}}], "acceptance_criteria": ["验收标准1"], "estimated_steps": N}}
""",
```

### 模块验收检测

```bash
# 验收 1：模板变量存在
grep -c "{result_constraints}" emily-core/emily_core/infrastructure/llm/prompt_loader.py
→ 预期输出：1

# 验收 2：字符串语法正确（无多余引号）
uv run python -c "from emily_core.infrastructure.llm.prompt_loader import load_prompt; p = load_prompt('planner'); print('result_constraints' in p)"
→ 预期输出：True

# 验收 3：格式化调用不爆炸
uv run python -c "from emily_core.infrastructure.llm.prompt_loader import load_prompt; p = load_prompt('planner'); print(p.format(sop_text='test', user_input='hello', available_tools='none', result_constraints='{}'))"
→ 预期输出：包含 "执行约束" 和 "{}"
```

**失败处理**：检查 `{{` 和 `}}` 转义是否正确，确认新增段落插入位置在 `{user_input}` 之前

---

## M4: node2 _llm_plan — 注入 result_constraints 到 planner prompt

**依赖**：M2 + M3

**职责**：在 `_llm_plan()` 构建 planner prompt 时，将 `wi.result_constraints` 作为变量注入。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | `_llm_plan()` 中 `planner_prompt.format()` 新增变量 | `emily-core/emily_core/workitem/workitem_agent.py` |

### 代码

#### `emily-core/emily_core/workitem/workitem_agent.py` — 修改 `_llm_plan()` 的 `.format()` 调用（L333-337）

```python
# emily-core/emily_core/workitem/workitem_agent.py
# 在 _llm_plan() 中，planner_prompt.format() 调用（L333）追加 result_constraints 变量：

        import json as _json
        rc = getattr(wi, "result_constraints", {}) or {}
        rc_text = _json.dumps(rc, ensure_ascii=False) if rc else "{}"
        
        planner_prompt = _load_planner_prompt()
        system_prompt = planner_prompt.format(
            sop_text=sop_text[:4000] if sop_text else f"SOP: {wi.sop_id or '未知'}（全文未加载）",
            user_input=wi.user_input,
            available_tools=tools_text,
            result_constraints=rc_text,   # ← 新增这一行
        )
```

### 模块验收检测

```bash
# 验收 1：format() 调用包含 result_constraints
grep "result_constraints" emily-core/emily_core/workitem/workitem_agent.py
→ 预期输出：≥ 2 行（变量获取 + format 参数）

# 验收 2：语法正确
uv run python -c "from emily_core.workitem.workitem_agent import WorkItemAgent; print('ok')"
→ 预期输出：ok
```

**失败处理**：检查 `_json` 别名是否与其他已有 import 冲突；确认 `rc_text` 在 `.format()` 之前定义

---

## M5: node4 _extract_structured_result — 约束校验

**依赖**：M4（逻辑依赖，需知道约束最终能传到执行层）

**职责**：在 node4 零 LLM 提炼阶段，校验执行结果是否满足 `result_constraints`，不满足时追加 warnings。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | `_extract_structured_result()` 中新增约束校验块 | `emily-core/emily_core/workitem/workitem_agent.py` |

### 代码

#### `emily-core/emily_core/workitem/workitem_agent.py` — 在 `_extract_structured_result()` 的 `issues` 收集之后（L769 之后）、`needs_confirm` 之前（L771 之前）追加

```python
# emily-core/emily_core/workitem/workitem_agent.py
# 在 issues 收集完成后（约 L769）、needs_confirm 之前（约 L771）追加约束校验块：

        # ── result_constraints 校验 ──
        rc = getattr(wi, "result_constraints", {}) or {}
        if rc:
            must_include = rc.get("must_include", [])
            if must_include:
                # 检查必须包含的维度是否在 summary_facts 中
                combined = " ".join(summary_facts) if summary_facts else ""
                for item in must_include:
                    if item not in combined:
                        issues.append(f"[constraint] 缺少必须信息: {item}")
            must_not = rc.get("must_not", [])
            if must_not:
                combined = " ".join(summary_facts) if summary_facts else ""
                for item in must_not:
                    # 简单关键词匹配
                    clean = item.replace("不要", "").replace("别", "").strip()
                    if clean and clean in combined:
                        issues.append(f"[constraint] 包含违规内容: {item}")
```

### 模块验收检测

```bash
# 验收 1：约束校验块存在
grep "result_constraints 校验" emily-core/emily_core/workitem/workitem_agent.py
→ 预期输出：1

# 验收 2：语法正确
uv run python -c "from emily_core.workitem.workitem_agent import WorkItemAgent; print('ok')"
→ 预期输出：ok

# 验收 3：空约束不影响正常流程
uv run python -c "
from emily_core.workitem.workitem import WorkItem
wi = WorkItem()
wi.result_constraints = {}
# 不抛异常即通过
print('PASS')
"
→ 预期输出：PASS
```

**失败处理**：检查缩进与 `issues` 收集块对齐；确认变量作用域（`summary_facts`、`issues` 在追加位置之前已定义）

---

## 组装验证

所有模块完成后，运行端到端验证：

```bash
# 验收 1：全链路 import 无异常
uv run python -c "
from emily_core.workitem.workitem import WorkItem
from emily_core.session.session_agent import SessionAgent
from emily_core.workitem.workitem_agent import WorkItemAgent
from emily_core.infrastructure.llm.prompt_loader import load_prompt

# WorkItem 含 result_constraints
wi = WorkItem()
assert hasattr(wi, 'result_constraints'), 'WorkItem missing result_constraints'
print(f'WorkItem.result_constraints: {wi.result_constraints}')

# SessionAgent._derive_constraints 存在
assert hasattr(SessionAgent, '_derive_constraints')
r = SessionAgent._derive_constraints({
    'result_constraints': {
        'scope': {'project': 'test'},
        'filters': ['exclude_completed'],
        'must_include': ['负责人'],
        'must_not': ['不要已完成'],
        'extra_noise': 'should be filtered'  # 多余字段
    }
})
assert 'extra_noise' not in r, f'多余字段未过滤: {r}'
assert r.get('scope') == {'project': 'test'}
assert r.get('filters') == ['exclude_completed']
print(f'_derive_constraints OK: {r}')

# planner prompt 含模板变量
p = load_prompt('planner')
assert '{result_constraints}' in p, 'planner missing {result_constraints}'
print(f'planner prompt contains result_constraints: OK')

print('ALL PASSED')
"
→ 预期输出：ALL PASSED
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
