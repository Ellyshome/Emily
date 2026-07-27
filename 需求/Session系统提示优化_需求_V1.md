# Session 系统提示优化需求

> 痛点：SessionAgent 载入的 system prompt 内容多、任务重，大量系统架构/运转逻辑/公司架构/基础知识每次拉起会话都重复灌注，token 重复消耗大。
>
> 基于 2026-07-25 架构讨论。核心结论：**真正的瓶颈不是"重复发送"（LLM 无状态无法回避），而是 DeepSeek Context Caching 红利完全没用上 + 目录类参考材料占了 system prompt 大头。**

***

## 一、现状

### 当前 system prompt 构成

[session.md](../emily-data/prompts/session.md)（142 行模板）通过 `.replace()` 渲染十余个占位符后注入为 LLM system message：

| 占位符                       | 内容性质          | 单次渲染规模         |
| ------------------------- | ------------- | -------------- |
| `{project_world_book}`    | 项目世界书（事实性知识）  | 大文本，可达数千 token |
| `{rule_book}`             | 规则书（SOP 全文）   | 大文本            |
| `{system_description}`    | 认知书（能力边界）     | 中等             |
| `{available_tools}`       | 工具清单 + schema | 中大             |
| `{visible_schema}`        | 可查询数据库表结构     | 中大             |
| `{visible_files}`         | 可访问文件清单       | 中              |
| `{sop_catalog}`           | SOP 目录        | 中              |
| `{node_template_catalog}` | 节点模板目录        | 中              |
| `{rag_info}`              | 知识库说明         | 小              |
| `{user_memory}`           | 长期记忆          | 小              |
| `{current_datetime}`      | 当前时间          | 小（但每秒变化）       |
| 身份/项目上下文若干字段              | 会话级           | 小              |

### 加载链路

1. [session\_agent.py:54-59](../emily-core/emily_core/session/session_agent.py#L54-L59) `_load_session_prompt()` 进程级模块变量 `_SESSION_SYSTEM_PROMPT` 缓存模板原文
2. [session\_context.py:317-358](../emily-core/emily_core/session/session_context.py#L317-L358) `build_llm_messages()` 每轮 LLM 调用前用 `.replace()` 循环渲染 Session 级变量
3. [client.py:33](../emily-core/emily_core/infrastructure/llm/client.py#L33) `base_url=https://api.deepseek.com`，模型 `deepseek-v4-flash`
4. 渲染后的完整 system prompt 作为 `messages[0]` 发给 DeepSeek

***

## 二、问题诊断

### 问题 1：前缀被易变字段污染，DeepSeek cache 永远 miss

**事实**：DeepSeek 自 2024.8 起自动开启 Context Caching（无需任何参数），前缀完全一致的部分按 **0.1x 计费**。OpenAI 兼容 API 同样支持。

<br />

**观测盲区**：[client.py:221-223](../emily-core/emily_core/infrastructure/llm/client.py#L221-L223) 的 trace 只读 `prompt_tokens` / `completion_tokens` / `total_tokens`，**未读 DeepSeek 返回的** **`prompt_cache_hit_tokens`** **/** **`prompt_cache_miss_tokens`** **字段**——即使现在偶尔命中也无法发现。

### 问题 2：目录类参考材料占了 system prompt 大头

[session.md:84-93](../emily-data/prompts/session.md#L84-L93) 灌入的 `{sop_catalog}` `{available_tools}` `{visible_schema}` `{visible_files}` `{node_template_catalog}` 本质是**目录索引**（参考材料），不是**行为指令**。LLM 在意图识别（路由）阶段根本不需要全部工具 schema，只需知道"有这些能力域"。

全量灌入的代价：

- token 浪费（每次调用都重发数千 token 的目录）
- 稀释行为指令（路由规则、输出规范）的注意力
- 目录内容变化（新增 SOP / 工具）会进一步破坏 cache 前缀稳定性

### 问题 3：每轮重新渲染，渲染结果未缓存

[session\_context.py:335-338](../emily-core/emily_core/session/session_context.py#L335-L338) 每轮 LLM 调用都对模板做一遍 `.replace()` 循环。虽然模板原文已模块级缓存，但**渲染后的字符串没缓存**——而 Session 级变量在一次 Session 内基本稳定，重复渲染纯属浪费。

### 问题 4：路由与执行共用同一套重 prompt

`_recognize_intent`（[session\_agent.py:243](../emily-core/emily_core/session/session_agent.py#L243)）是路由场景，只需判断"这是哪类业务"，不需要三书全文。但当前用同一套完整 system prompt + 默认模型。

[client.py:37](../emily-core/emily_core/infrastructure/llm/client.py#L37) 已有 `router_model` 字段，但 `_recognize_intent` 路径未启用——路由场景本可用更便宜更快的 flash 模型 + 精简 prompt。

***

## 三、设计原则：信息分层与能力树

> P1-1（system prompt 瘦身）的前置设计依据。在划"哪些信息进 prompt / 哪些走工具"前，先明确分类框架，避免凭经验划边界。

### 3.1 核心直觉

- **无孤儿**：每个能力必须挂在能力树某个节点下，LLM 看到树 = 知道系统全部能力边界。某个能力 LLM 不知道存在，就永远不会被调用。
- **骨架 ≠ 清单**：system prompt 描述的是"分类结构 + 发现机制"（地图），不是"全部功能详情"（地点）。骨架要全，叶子按需取。
- **稳定性优先**：进 prompt 的内容必须低频变化（保证 cache 命中），高频变的内容走工具或动态注入。

### 3.2 三层信息架构

| 层       | 内容                          | 位置                                  | 稳定性        |
| ------- | --------------------------- | ----------------------------------- | ---------- |
| **L1 地图** | 能力域分类 + 每域一句话 + 发现机制        | system prompt（全集静态）                 | 跨所有用户不变    |
| **L2 索引** | 每域下的能力名 + 一句话摘要             | `list_capabilities(domain)` 工具（按角色裁剪） | Session 级稳定 |
| **L3 详情** | SOP 全文 / 工具 schema / 表结构 / 世界书 | 工具按需取                               | 高频变 / 大文本  |

L1 全集静态 → DeepSeek cache 全用户共享命中；L2 按角色裁剪返回；L3 一律工具取。

### 3.3 信息归位三问

一个信息该进 prompt 还是走工具，回答三个问题：

1. **它变不变？** 高频变 → 工具/动态注入；低频变 → 可进 prompt
2. **LLM 每次都需要它吗？** 每次都需要（路由规则、能力边界）→ prompt；偶尔需要（表结构、SOP 全文）→ 工具
3. **它是地图还是地点？** 地图（结构/分类/规则/边界）→ prompt；地点（具体数据/全文/详情）→ 工具

三问都指向 prompt → 进；三问都指向工具 → 走；分歧时默认走工具（瘦身为先）。

### 3.4 能力树骨架（L1 蓝本）

基于 Emily 当前 SOP 编号（SYS/REC/FILE/QRY）和工具分簇，能力树五大域：

```
Emily 能力树（L1 骨架，进 system prompt）
├── REC 记录类 — 把现场发生的事录入并留痕
│   └── 事件 / 任务 / 会议 / 文件归档 / 用户记忆
├── QRY 查询类 — 查项目数据与历史
│   └── 项目概况 / 节点 / 事件 / 任务 / 会议 / 文件 / 会话历史
├── KB 知识类 — 基于知识库回答领域问题
│   └── 知识库检索（maxkb 向量）
├── SYS 流程类 — 系统级流程与管理
│   └── 标准协议 / 待办事项 / 确认取消 / 节点管理 / 兜底
└── DOC 文档处理类 — 解析上传文件为结构化数据
    └── OCR / 文档解析 / 表格抽取 / 分块 / 向量化 / DB 结构化读写
```

system prompt 里只放骨架 + 发现机制："查某域能力用 `list_capabilities(domain)`，查参数用 `describe_capability(name)`"。

这棵树本身就是"认知书"的核心——声明 Emily 的能力边界，LLM 看到树就知道"能做什么/不能做什么"，替代当前 `{system_description}` 长篇大论。

### 3.5 角色裁剪：方案 B

L1 全集静态（cache 全命中），角色差异下沉到 L2：

- **L1**：所有用户看到同一棵完整能力树骨架 → DeepSeek cache 全用户共享
- **L2**：`list_capabilities(domain)` 按当前用户角色裁剪返回（低权限用户在 SYS 域只看到"待办事项/确认取消"，看不到"节点管理"）
- **可见性软约束**：prompt 里声明"低权限用户不应尝试调用管理类能力"
- **可调性硬拦截**：调用链仍是模式 X（SOP 驱动，见 3.6），现有 `sop_allow` + Skill YAML `tools` 白名单 + Hook Auth deny-wins 兜底

软约束减少误调用，硬拦截兜底，两者互补。**不扩展权限模块**（坚持模式 X，见 3.6）。

### 3.6 调用链不变（模式 X，SOP 驱动）

能力树是**认知层**，不改变现有调用链：

```
当前模式（M14，保持不变）：
  LLM 看 L1 能力树 → 路由输出 sop_id → 框架按 sop_id 找 Skill YAML
  → YAML tools 白名单 → SkillExecutor 调 BusinessFlowTool.handler(params)

执行阶段需要工具详情时：
  list_capabilities(domain) → describe_capability(name) → 取参数 schema
```

不采用模式 Y（LLM 直接 function-calling 绕过 SOP）——那样需要把权限从 SOP 级下沉到工具级，扩大工作量且违背 M14 约束（[CLAUDE.md](../CLAUDE.md) 约束 5）。

DB 工具只暴露**结构化业务工具**（`create_event` / `update_task` 等），**不暴露裸 SQL**——LLM 幻觉一条 `DELETE FROM` 权限模块拦不住语义错误。

### 3.7 无孤儿审计机制

光设计树不够，要保证没有树外孤儿：

1. **声明式注册**：每个 SOP / 工具注册时强制声明所属 `domain`，未声明的不进 LLM 可见集
2. **启动时校验**：进程启动时扫描所有 SOP / 工具，未挂到树某个节点的 → 报错或告警
3. **L1 树自动生成**：L1 骨架从注册表聚合生成，而非手写——保证树和实际能力永远一致

参考现有 [scripts\_registry.yaml](../emily-data/config/scripts_registry.yaml) 声明式管理脚本的模式。

***

## 四、改进项

### P0-1：DeepSeek Context Caching 前缀稳定化 + 命中率观测

**现状**：system prompt 中段含 `{current_datetime}` 等易变字段，前缀每秒变化，DeepSeek 自动 cache 永远 miss；trace 未埋点 cache 命中字段。

**方案**：

1. 重排 system prompt 为三层稳定结构（从稳定到易变）：
   - **L1 全局静态**：角色定义、三书框架说明、路由规则、输出规范——跨 Session 都一样
   - **L2 Session 级半静态**：用户身份、项目上下文、三书内容、工具目录摘要——Session 内不变
   - **L3 轮次级动态**：`{current_datetime}`、pending\_context——从 system prompt 移除
2. `{current_datetime}` 挪到 user message 末尾拼接，或独立的"会话状态"system 段放在 history 之后
3. [client.py](../emily-core/emily_core/infrastructure/llm/client.py) trace 补读 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，落盘到 `emily-data/logs/llm_trace.jsonl`

**关键文件**：

- `emily-data/prompts/session.md` — 模板重排
- `emily-core/emily_core/session/session_context.py` — `build_llm_messages()` 调整消息顺序
- `emily-core/emily_core/infrastructure/llm/client.py` — trace 字段补全（约 L221）

**预期效果**：system prompt 部分命中 cache 后按 0.1x 计费，**单次调用 token 成本立降 60-80%**。几乎不改架构的纯收益。

***

### P0-2：Session 级 prompt 预渲染缓存

**现状**：每轮 LLM 调用都重跑 `.replace()` 循环渲染模板，渲染结果未缓存。

**方案**：SessionAgent 创建时渲染一次 L1+L2 层 system prompt，缓存到 `self._rendered_system_prompt`；后续 `build_llm_messages` 直接复用，仅拼接 L3 动态段。

**关键文件**：

- `emily-core/emily_core/session/session_agent.py` — `__init__` 增加预渲染
- `emily-core/emily_core/session/session_context.py` — `build_llm_messages()` 改用缓存

**预期效果**：省每次 replace 循环开销；配合 P0-1 后 L1+L2 在 Session 内完全不变，cache 命中率进一步提升。

***

### P0-3：路由阶段启用 router\_model

**现状**：[client.py:37](../emily-core/emily_core/infrastructure/llm/client.py#L37) 已有 `router_model` 字段但 `_recognize_intent` 路径未启用，路由仍用默认模型。

**方案**：`_recognize_intent` 调用 LLM 时显式传 `model=router_model`（deepseek-v4-flash，便宜快）。配合 P1-1 的精简 prompt 收益更大。

**关键文件**：

- `emily-core/emily_core/session/session_agent.py` — `_recognize_intent()` 调用处

**预期效果**：路由调用单价更低、延迟更低。flash 模型对路由场景足够。

***

### P1-1：目录类内容工具化 / RAG 化（system prompt 瘦身）

**设计依据**：详见 §三 设计原则——信息分层与能力树。信息归位按 §3.2 三层架构 + §3.3 三问准则推导，不凭经验划边界。

**现状**：[session.md:84-93](../emily-data/prompts/session.md#L84-L93) 全量灌入工具/schema/SOP/模板目录，占了 system prompt 大头。

**方案**：按三层架构重构信息归位：

| 当前全量注入                    | 改造后归位                                                              |
| ------------------------- | ------------------------------------------------------------------ |
| `{available_tools}`       | L1 只留五大域骨架；L2 `list_capabilities(domain)`；L3 `describe_capability(name)` |
| `{visible_schema}`        | L3 `describe_table(name)` 工具按需取                                    |
| `{sop_catalog}`           | L1 只留域级骨架；L2 `list_capabilities("SYS")` 返回 SOP 清单；L3 `get_sop(id)` 取全文 |
| `{node_template_catalog}` | L3 `list_node_templates(industry)` 工具按需取                           |
| `{project_world_book}`    | 走 maxkb RAG（已有 Qwen3-Embedding + pgvector）                          |
| `{rule_book}`             | L3 `get_sop(id)` 按 SOP 取全文，不再全量注入                                  |
| `{system_description}`    | 由 L1 能力树骨架替代（树本身即能力边界声明，见 §3.4）                                    |

system prompt 只保留 L1：角色定义 + 路由规则 + 输出规范 + 能力树骨架 + 发现机制 + 当前会话身份摘要。

**关键文件**：

- `emily-data/prompts/session.md` — 模板重构为 L1 骨架
- `emily-core/emily_core/tools/` — 新增 `list_capabilities` / `describe_capability` / `describe_table` / `list_node_templates` 检索类工具
- `emily-core/emily_core/session/session_context.py` — `get_prompt_variables()` 移除目录类占位符
- SOP / 工具注册表 — 增加 `domain` 声明字段（无孤儿审计，见 §3.7）

**预期效果**：system prompt 从数千 token 降到几百 token；LLM 注意力聚焦行为指令，路由准确率提升；L1 全集静态 → cache 全用户共享命中。

**代价**：执行阶段可能多 1-2 轮 tool call（`list_capabilities` → `describe_capability`）。路由阶段不受影响（只需 L1 骨架即可判断 domain）。

**前置依赖**：P0-1（前缀稳定化）先行，P1-1 在 L1 骨架稳定后再做，避免反复调整模板破坏 cache。

***

### P2-1：双模型分层（路由 vs 执行）

**现状**：路由（意图识别）与执行（WorkItem 阶段）共用同一套完整 system prompt + 同一模型。

**方案**：

- `_recognize_intent` 用 `router_model` + 精简 prompt（仅 L1 骨架 + 路由规则，不含三书全文）
- 业务执行（WorkItem 阶段）才拉 reasoner 模型 + 完整 prompt

**关键文件**：

- `emily-data/prompts/` — 新增 `session_router.md` 精简模板
- `emily-core/emily_core/session/session_agent.py` — `_recognize_intent()` 切换模板与模型

**预期效果**：路由调用成本再降一档；执行阶段保留完整上下文不影响业务质量。

**前置依赖**：P0-3 启用 router\_model；P1-1 精简 prompt 后收益更显著。

***

## 五、推荐实施路径

### 第一刀（1-2 天）：P0-1 + P0-2 + P0-3

几乎不改架构，纯收益：

- system prompt 前缀稳定 → DeepSeek cache 命中 → 0.1x 计费
- 渲染结果缓存 → 省每次 replace 循环
- 路由用 flash 模型 → 单价更低 + 延迟更低

### 第二刀（观察 1 周后）：视数据决定 P1-1

落地第一刀后，用 `emily-data/logs/llm_trace.jsonl` 看 `usage.prompt_cache_hit_tokens` 占比：

- 命中率稳定 >80% → P1-1 边际收益不大，可缓做
- 命中率仍低 → 前缀还有不稳定因素，P1-1 顺势把大块头移走，既瘦身又稳前缀

P1-1 实施前先落地 §三 设计原则：能力树骨架定义 + 注册表 `domain` 字段 + 无孤儿审计，再做工具化迁移。

### 第三刀（可选）：P2-1

P1-1 精简 prompt 后，路由/执行分层的收益才显著。如需进一步降本再做。

***

## 六、验证方法

**唯一可信指标**：[client.py](../emily-core/emily_core/infrastructure/llm/client.py) trace 补全后，观察 `emily-data/logs/llm_trace.jsonl` 中每条调用的 `prompt_cache_hit_tokens / prompt_tokens` 比值。

**验证命令**：

```powershell
# 查看最近 LLM 调用的 cache 命中情况（jsonl 已含完整 usage）
docker exec mitmproxy tail -5 /app/logs/llm_trace.jsonl
```

**预期对比**：

| 阶段   | 单次调用 prompt 计费            | cache 命中率 |
| ---- | ------------------------- | --------- |
| 优化前  | 100% 全价                   | ~0%       |
| 第一刀后 | 30-40%（system 部分按 0.1x）   | >80%      |
| 第二刀后 | 15-25%（system 瘦身 + cache） | >90%      |
