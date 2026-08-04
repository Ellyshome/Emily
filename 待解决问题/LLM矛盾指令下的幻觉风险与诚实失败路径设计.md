# LLM 矛盾指令下的幻觉风险与诚实失败路径设计

> **状态**：待解决
> **日期**：2026-07-31
> **分类**：架构设计 / Agent Loop 可靠性

---

## 一、问题描述

### 触发场景

WorkItem 执行流程中，当节点被分配了任务但**实际没有任何可用业务工具**时（如 `session_api_ids` 为空、tool_registry 表未初始化、用户无任何工具权限），会出现以下情况：

```
SOP 指导：    "用 record_event 记录事件"     ← 告诉 LLM 该做什么
可用工具：    （无可用工具）                  ← 告诉 LLM 没有能力做
行为规则：    "必须调用工具，禁止纯文本"       ← 告诉 LLM 不能认输
```

这三条指令构成**不可同时满足的矛盾**。

### 已出现的实际后果

此前修复过一个 bug：节点没有拿到任何工具，却被安排了去使用工具干活。LLM 在此场景下的行为不可预测，可能表现为：

1. **幻觉工具名** — 编造不存在的工具名调用 → node3 执行失败 → error_analysis
2. **纯文本伪装完成** — 输出 "好的，事件已记录" → type=text 兜底转为 `complete_work(status="success")` → **系统性故障被伪装成成功**
3. **循环重试** — 反复失败 → replan → 再失败 → 触及 iteration cap / max_replan 上限才终止

---

## 二、根因分析：LLM 的元认知边界

### 2.1 LLM 是模式补全器，不是真相探求器

LLM 的训练目标函数是"最大化下一个 token 的似然度"。当面临矛盾指令时，其默认倾向是**补全它认为"应该出现"的模式**，而非指出矛盾。

在训练语料中，诚实认输的模式远少于成功完成的模式。因此 LLM 在"无法完成任务"场景下天然处于劣势。

### 2.2 已知的 LLM 元认知能力边界

| 场景 | LLM 能否识别 | 原因 |
|------|-------------|------|
| 信息不足（缺参数） | ✅ 较好 | "缺少 project_id"是可显式检测的缺失 |
| 工具缺失（需要但不可用） | ⚠️ 中等 | 取决于 prompt 是否明确教它比较"需要 vs 可用" |
| 矛盾指令（SOP 要求 X，工具列表无 X） | ❌ 较差 | 不会做逻辑一致性检查，倾向于忽略矛盾 |
| 幻觉自己解决（编一个工具/参数） | ❌ 很差 | 幻觉对 LLM 来说"感觉和真的一样" |
| 部分失败后诚实结束 | ⚠️ 中等 | 有失败反馈时能识别，但会倾向于反复重试 |

### 2.3 关键结论

**LLM 擅长识别"明确的缺失"（缺参数、缺数据），但不擅长识别"逻辑矛盾"（SOP 说要 X 但我没有 X），也不擅长在压力指令下主动认输。**

---

## 三、当前系统的证据

以下现有机制的存在，从反方向证实了上述问题：

### 证据 1：`complete_work.issues` 字段存在但描述极弱

[control_tools.py:46-50](file:///d:/app/Emily/emily-core/emily_core/workitem/langgraph_engine/agent/control_tools.py#L46-L50)

```python
"issues": {
    "type": "array",
    "description": "执行中遇到的问题（无则空数组）",  # 仅 12 字，无场景引导
},
```

字段已预留，但 LLM 不知道什么时候该填、填什么。

### 证据 2：`type=text` 兜底机制会将失败伪装成成功

[loop.py] 中当 LLM 输出纯文本不调工具时，系统将其包装为 `complete_work(status="success")`。这意味着 LLM 的纯文本幻觉会被**静默吞噬**，开发人员无法感知。

### 证据 3：`quality_gate` 检测"对话承诺式文本"

[nodes.py:416-419](file:///d:/app/Emily/emily-core/emily_core/workitem/langgraph_engine/nodes.py#L416-L419)

```python
_PROMISE_PATTERNS = [
    "正在查询", "请稍候", "我来帮您", "正在为您", ...
]
```

LLM 在无法实际完成任务时，会输出承诺式文本来伪装完成 — 这是幻觉的温和形式。

### 证据 4：多层硬上限防护

`agent_loop_max_iterations=12`、`max_replan=1`、error_analysis 连续 3 次硬上限。这些机制的存在说明 **LLM 在困难场景下确实会陷入循环**。

### 证据 5：prompt 中只有成功范式

[prompt_builder.py:117-118](file:///d:/app/Emily/emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py#L117-L118)

```
正确示例（必须）：
  调用 complete_work(status="success", summary=["共查到3条事件记录"], ...)
```

只有一个 `success` 示例，没有 `failed` 示例。LLM 缺乏"失败也是正确行为"的认知范式。

---

## 四、解决方案分析

### 4.1 备选方案

| 方案 | 可行性 | 说明 |
|------|--------|------|
| A. 新增 `record_issue` 控制工具 | ⚠️ 部分有效 | 光加工具不够，LLM 不知道何时该用 |
| B. 强化 `complete_work.issues` + prompt 引导 | ✅ 推荐 | 复用已有基础设施，只需补 prompt |
| C. 重构 prompt 失败路径地位 + 显性化矛盾检测 | ✅ 最佳 | 消除 LLM 心理矛盾，给失败路径合法地位 |

### 4.2 推荐方向：方案 B + C 结合

**不需要新增工具**，只需在 prompt 层面做三件事：

#### (1) 增加矛盾检测步骤

在工作方式中插入显性交叉检查指令，让 LLM 比较 SOP 要求的工具和可用工具列表：

```
2. ⚠️ 交叉检查：SOP 要求的工具是否在可用工具表中？
   若 SOP 要求调用某工具但该工具不在列表中，这是系统性障碍——
   不要试图编造工具名，直接跳至步骤 7（失败结束）
```

#### (2) 增加失败路径的工作步骤

```
7. 遇到无法通过重试解决的系统性障碍（工具缺失、权限不足、数据不可达）时，
   调用 complete_work(status="failed", issues=[...]) 诚实结束
```

#### (3) 增加失败示例

```
失败示例（合法且正确）：
  调用 complete_work(
    status="failed",
    summary=["无法完成工作要求"],
    issues=["SOP-002-REC 要求调用 record_event 工具，但该工具不在可用列表中。
            可用工具：无。可能原因：tool_registry 表未初始化或用户无相关权限。"]
  )
```

#### (4) 强化 `issues` 字段描述

[control_tools.py](file:///d:/app/Emily/emily-core/emily_core/workitem/langgraph_engine/agent/control_tools.py) 中 `issues` 字段的 description 需要包含明确的使用场景：

```
"执行中遇到的系统性障碍，供开发人员排错。包括但不限于：
需要的工具不在可用列表、权限不足无法执行、数据不可达、
SOP 指导与可用工具不匹配等。无问题时留空数组。"
```

---

## 五、涉及文件

| 文件 | 改动类型 | 改动内容 |
|------|----------|----------|
| [prompt_builder.py](file:///d:/app/Emily/emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py) | 修改 | 增加矛盾检测步骤、失败路径步骤、失败示例 |
| [control_tools.py](file:///d:/app/Emily/emily-core/emily_core/workitem/langgraph_engine/agent/control_tools.py) | 修改 | 强化 `issues` 字段 description，明确排错场景 |

---

## 六、核心设计原则

1. **不要只加工具，要先消除矛盾** — LLM 无法在矛盾指令下做出理性决策
2. **给失败路径"合法地位"** — 让 LLM 觉得调用 `complete_work(status="failed")` 和 `status="success"` 一样是正确完成任务
3. **显性化隐性矛盾** — LLM 不会自动比较"需要的工具"和"可用的工具"，需要在 prompt 中指令它做交叉检查
4. **复用已有基础设施** — `complete_work.issues` 字段已存在，强化其语义比新增工具更自然、更低成本
