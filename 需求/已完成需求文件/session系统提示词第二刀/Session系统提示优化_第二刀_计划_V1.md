# Session 系统提示优化 — 第二刀实施计划

> **基于需求**：[需求/Session系统提示优化_需求_V1.md](../需求/Session系统提示优化_需求_V1.md) §三 设计原则 + §四 P1-1
> **计划版本**：v1.0
> **前置**：第一刀已执行（P0-1/P0-2/P0-3 + M5 fallback bug 修复），intent cache 命中 97%
> **目标**：把 session.md（intent 路由阶段）的全量占位符（三书/工具清单/schema/文件/模板目录）移除，嵌入 L1 能力树骨架，让 system prompt 从 ~7645 字符降到 ~1500，cache 命中率维持 >90%。

---

## Context

**为什么做这个改动**：第一刀让 system prompt 前缀稳定 + cache 命中（intent 阶段 97%），但 prompt 内容没瘦身——session.md 仍全量灌入三书 + 工具清单 + schema + 文件 + 模板目录（~7645 字符）。这些是"参考材料"不是"行为指令"，LLM 路由阶段根本不需要工具 schema / 表结构 / 文件清单，只需要能力树骨架（知道有哪几类业务）+ SOP 清单（路由匹配）。

**本计划范围（第二刀）**：§三 设计原则落地 + §四 P1-1。
- 含：dump_as_text 精简为 L1 能力树骨架 + session.md 重构（移除全量占位符）+ get_prompt_variables 清理 + 无孤儿审计
- 不含：P2-1（双模型分层，第三刀）、composer 盲区修复（独立任务）、project_world_book 主动 RAG 索引（若 maxkb 无项目世界书内容则作为后续任务）

**预期收益**：intent 阶段 system prompt 从 ~7645 字符降到 ~1500，单次 prompt token 从 ~3889 降到 ~800，叠加第一刀的 cache 命中，intent 调用 prompt 费用再降 ~80%。

---

## 探查发现的关键约束（影响实施）

1. **dump_as_text() 已是类型树输出**（[registry.py:120-214](../emily-core/emily_core/skill/registry.py#L120-L214)）——含 TYPE_DESC 映射（REC/FILE/QRY/FLOW/SYS）+ 第一部分类型树总览 + 第二部分详细匹配规则（工具/步骤/说明）。**L1 骨架雏形已在，只需精简第二部分**，不新建函数。
2. **SkillDefinition 无 domain 字段**（[definition.py:49-60](../emily-core/emily_core/skill/definition.py#L49-L60)），但 sop_id 含类型码（SOP-002-REC），`_extract_sop_type()`（[registry.py:22-34](../emily-core/emily_core/skill/registry.py#L22-L34)）已能推导。**复用 sop_id 类型码，不新增 domain 字段**。
3. **工具已有 category 字段**（[tools/registry.py:53](../emily-core/emily_core/tools/registry.py#L53) `_tool(category=...)`）——base/business/project。无孤儿审计基于 category，不新增字段。
4. **三书从 PermissionSnapshot 加载**（[session_context.py:201-203](../emily-core/emily_core/session/session_context.py#L201-L203)），通过 `get_prompt_variables()`（L387-389）注入。移除占位符后，三书字段保留在 dataclass（其他地方可能用），只清理 prompt 变量映射。
5. **knowledge_search 已有 maxkb 集成**（[knowledge_search_tool.py:59](../emily-core/emily_core/tools/knowledge_search_tool.py#L59) `handle_knowledge_search`）——project_world_book 移除后，执行阶段需要项目背景时走此工具，不新建 RAG 索引。
6. **session.md 第一刀后已重排**（[session.md](../emily-data/prompts/session.md)）——一、角色 + 二、行为规范（L1 静态）在前，三、三书 + 四、会话上下文 + 五、能力清单（L2 半静态）在后。第二刀继续在 L2 段做减法。
7. **M14 约束**（[CLAUDE.md](../CLAUDE.md) 约束 5）：不暴露 LLM function-calling。所以 list_capabilities/describe_capability 不作为 LLM 工具——L1 骨架由 dump_as_text 自动生成注入，L2/L3 由现有 Skill YAML 承担（框架按 sop_id 加载，现有流程不变）。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家**，严格按 M1→M4 顺序执行，逐模块验证，验证不通过不进入下一个模块。

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：`dump_as_text` / `get_prompt_variables` / `register_all` 签名不得变，只能改实现内部
2. **M14 约束**：不新建 LLM function-calling 工具，不新建 list_capabilities/describe_capability 工具。L1 骨架由 dump_as_text 自动生成，L2/L3 由现有 Skill YAML 承担
3. **业务内核独立**：`emily_core` 不 import 任何 `astrbot.*` 包
4. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
5. **不动 planner 的 {available_tools}**：[workitem.md](../emily-data/prompts/workitem.md) 的 `{available_tools}` 保留（planner 规划步骤需要工具清单）。第二刀只改 session.md（intent 路由阶段）
6. **不删三书 dataclass 字段**：session_context.py 的 `project_world_book` / `rule_book` / `system_description` 字段保留（PermissionSnapshot 加载逻辑不变），只清理 `get_prompt_variables()` 的占位符映射
7. **改完代码必须清 `__pycache__`**：`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`

## 代码模式参照表

| 层 | 参照源 | 要模仿的要点 |
|----|------|-------------|
| dump_as_text 类型树 | [registry.py:120-214](../emily-core/emily_core/skill/registry.py#L120-L214) | 按 sop_type 分组 + TYPE_DESC 映射 + 两部分输出结构 |
| prompt 变量映射 | [session_context.py:360-389](../emily-core/emily_core/session/session_context.py#L360-L389) | `"{占位符}": self.字段` 字典字面量 |
| session.md 模板 | [session.md](../emily-data/prompts/session.md) | `## 一、二、三` 章节结构 + HTML 注释元信息 + `{占位符}` |
| 工具注册 category | [tools/registry.py:53](../emily-core/emily_core/tools/registry.py#L53) | `_tool(category="base/business/project")` |
| 启动审计日志 | [registry.py:69-72](../emily-core/emily_core/skill/registry.py#L69-L72) | `logger.info("...: %d ...", count)` 模式 |

## 模块依赖图

```
M1(dump_as_text 精简为 L1 骨架) ──→ M2(session.md 重构) ──→ M3(get_prompt_variables 清理)
                                              │
                                              ↓
                                         M4(无孤儿审计)
```

- M1 独立（改 dump_as_text 输出）
- M2 依赖 M1（session.md 的 `{sop_catalog}` 用精简后的输出）
- M3 依赖 M2（session.md 移除的占位符，get_prompt_variables 对应清理）
- M4 依赖 M1（审计基于能力树定义），可与 M2/M3 并行但建议最后做（验证整体一致性）

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M1 | `emily-core/emily_core/skill/registry.py` | 修改 | `dump_as_text` 精简第二部分（移除工具/步骤详情）+ 补 DOC 域说明 |
| M2 | `emily-data/prompts/session.md` | 修改 | 移除三书段 + 全量占位符，精简为角色/规范/会话上下文/能力树骨架 |
| M3 | `emily-core/emily_core/session/session_context.py` | 修改 | `get_prompt_variables` 移除 7 个占位符映射 |
| M4 | `emily-core/emily_core/tools/registry.py` | 修改 | `register_all` 末尾新增 `_audit_capabilities()` 审计调用 |

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/skill/registry.py` | 修改 | `dump_as_text` 第二部分精简：移除每个 Skill 的工具列表 + 步骤摘要，只保留 sop_id + display_name + 说明首行；TYPE_DESC 补 DOC 域 |
| `emily-data/prompts/session.md` | 修改 | 移除"三、业务背景（三书）"段（{project_world_book}/{rule_book}/{system_description}）；"五、能力清单"段精简为只保留 {sop_catalog} + {rag_info}；移除 {available_tools}/{visible_schema}/{visible_files}/{node_template_catalog} |
| `emily-core/emily_core/session/session_context.py` | 修改 | `get_prompt_variables` 移除 7 个占位符映射（available_tools/visible_schema/visible_files/node_template_catalog/project_world_book/rule_book/system_description） |
| `emily-core/emily_core/tools/registry.py` | 修改 | `register_all` 末尾新增 `_audit_capabilities(reg, skill_registry)` 调用 + 新增 `_audit_capabilities` 函数 |
| `emily-core/emily_core/skill/definition.py` | 不变 | — |
| `emily-data/prompts/workitem.md` | 不变 | planner 的 {available_tools} 保留 |
| `emily-core/emily_core/session/session_context.py` 的 dataclass 字段 | 不变 | project_world_book/rule_book/system_description 字段保留（PermissionSnapshot 加载不变） |

---

## M1: dump_as_text 精简为 L1 能力树骨架

**依赖**：无（首建模块）

**职责**：把 `dump_as_text()` 第二部分（各类型详细匹配规则）从"工具列表 + 步骤摘要 + 说明首行"精简为"sop_id + display_name + 说明首行"，让 `{sop_catalog}` 从详细清单变为 L1 骨架 + 一句话清单。补 DOC 域类型说明。

### 代码

#### `emily-core/emily_core/skill/registry.py` — `dump_as_text` 方法整体替换

定位 [registry.py:120-214](../emily-core/emily_core/skill/registry.py#L120-L214) 的整个 `dump_as_text` 方法，替换为以下精简版本：

```python
    def dump_as_text(self) -> str:
        """将全部 Skill 以类型树格式导出为纯文本（供 LLM 消费）。

        P1-1 精简：第二部分移除工具列表 + 步骤摘要（执行阶段从 Skill YAML 取），
        只保留 sop_id + display_name + 说明首行，让 {sop_catalog} 成为 L1 能力树骨架。
        """
        with self._lock:
            skills = list(self._registry.values())

        if not skills:
            return "（暂无已加载的业务流程/Skill）"

        # 按 sop_type 分组（从 sop_id 推导类型）
        grouped: dict[str, list[SkillDefinition]] = {}
        for skill in skills:
            sop_type = _extract_sop_type(skill.sop_id)
            grouped.setdefault(sop_type, []).append(skill)

        # 类型描述映射（P1-1 补 DOC 域）
        TYPE_DESC = {
            "REC": "记录与录入（事件/任务/会议等）",
            "FILE": "文件管理（归档/查询/分享）",
            "QRY": "数据查询（项目/进度/人员等）",
            "FLOW": "深度调查（跨维度分析/审计）",
            "SYS": "系统管理（确认/取消/设置等）",
            "DOC": "文档处理（OCR/解析/表格抽取/向量化）",
        }

        # 类型级兜底策略
        FALLBACK_BY_TYPE = {
            "REC": "这是记录/录入类请求。若以上 REC 流程均不匹配，使用 record_event / record_task 原子工具自由推理录入。",
            "FILE": "这是文件管理类请求。若以上 FILE 流程均不匹配，使用 file_storage 原子工具处理。",
            "QRY": "这是数据查询类请求。若以上 QRY 流程均不匹配，使用 query_data 工具查询，query_type 根据用户意图选择 event/task/meeting/file/message/summary。",
            "FLOW": "这是深度调查类请求。若以上 FLOW 流程均不匹配，使用系统内置的守护调查Agent执行跨维度分析。",
            "SYS": "这是系统管理类请求。若以上 SYS 流程均不匹配，但仍需系统功能，使用对应原子工具自由推理。",
            "DOC": "这是文档处理类请求。若以上 DOC 流程均不匹配，使用 parse_document / extract_table / chunk_text / embed_and_index 原子工具处理。",
        }

        lines: list[str] = []

        # ── 第一部分：类型树总览（L1 骨架）──
        lines.append("## 一、业务类型树（先看这里，确定消息属于哪个类型）")
        lines.append("")
        lines.append("请先将用户消息归类到以下类型之一，再在该类型下精匹配具体流程：")
        lines.append("")

        for sop_type, type_skills in grouped.items():
            names = "、".join(s.display_name for s in type_skills)
            sop_ids = "、".join(s.sop_id for s in type_skills)
            desc = TYPE_DESC.get(sop_type, sop_type)
            lines.append(f"**{sop_type}** — {desc}")
            lines.append(f"  包含流程: {names}")
            lines.append(f"  编号: {sop_ids}")
            lines.append("")

        lines.append("---")
        lines.append("")

        # ── 第二部分：各类型流程清单（P1-1 精简：仅 sop_id + display_name + 说明首行）──
        lines.append("## 二、各类型流程清单（锁定类型后精匹配）")
        lines.append("")

        for sop_type, type_skills in grouped.items():
            desc = TYPE_DESC.get(sop_type, sop_type)
            lines.append(f"### {sop_type} — {desc}")
            lines.append("")

            for skill in type_skills:
                # P1-1: 移除工具列表 + 步骤摘要（执行阶段从 Skill YAML 取）
                lines.append(f"**[{skill.sop_id}] {skill.display_name}**")

                # instructions 首行摘要（路由匹配必需）
                if skill.instructions:
                    first_line = skill.instructions.strip().split("\n")[0][:100]
                    lines.append(f"  说明: {first_line}")

                lines.append("")

            # 类型级兜底
            fallback = FALLBACK_BY_TYPE.get(sop_type, "")
            if fallback:
                lines.append(f"> **{sop_type} 类型兜底**: {fallback}")
                lines.append("")

            lines.append("---")

        return "\n".join(lines)
```

### 模块验收检测

```powershell
# 验收 1：dump_as_text 不再输出工具列表 + 步骤摘要
Select-String -Path "emily-core\emily_core\skill\registry.py" -Pattern "tool_names|步骤:"
→ 预期输出：无匹配（空）——工具列表和步骤摘要的输出代码已移除

# 验收 2：TYPE_DESC 含 DOC 域
Select-String -Path "emily-core\emily_core\skill\registry.py" -Pattern '"DOC":'
→ 预期输出：1 行匹配

# 验收 3：重启后看 {sop_catalog} 渲染后字符数是否大幅下降
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
# 读 session_archive 看 sop_catalog 字数
Get-ChildItem "emily-data\session_archives" -Filter "*.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
→ 预期输出：归档文件"会话快照"段中 sop_catalog 摘要字数 < 1500（优化前 ~2766 字）
```

**失败处理**：若 sop_catalog 字数未降，检查 dump_as_text 是否被正确替换——`grep "tool_names" registry.py` 应无匹配；若仍有，说明替换未生效。

---

## M2: session.md 重构（移除全量占位符 + 嵌入能力树骨架）

**依赖**：M1（{sop_catalog} 已精简为 L1 骨架）

**职责**：移除 session.md 的"三、业务背景（三书）"段 + "五、能力清单"的全量占位符，精简为"角色/规范/会话上下文/能力树骨架"四段结构。system prompt 从 ~7645 字符降到 ~1500。

### 代码

#### `emily-data/prompts/session.md` — 整体替换为重构版本

定位 [session.md](../emily-data/prompts/session.md) 整个文件，替换为以下重构版本：

```markdown
<!-- SessionAgent 核心人格系统提示 —— 每次 LLM chat 注入为 system prompt -->
<!-- P1-1: 移除三书/工具清单/schema/文件/模板目录全量注入，{sop_catalog} 精简为 L1 能力树骨架 -->
<!-- 模板变量（阶段1 直接 replace）: {sop_catalog} -->
<!-- 模板变量（阶段2 Session 级，空值替换为"（无）"）: {user_name} {user_company} {user_company_type} {user_department} {user_position} {user_permission_level} {current_node_ids} {project_name} {project_type} {project_status} {user_memory} {rag_info} -->
<!-- 加载位置：SessionAgent._recognize_intent() -->

## 一、角色与定位

你是艾米（Emily），Emily 系统中负责与用户对话的服务 AI。你运行在即时通讯（IM）平台中，作为用户与企业公共大脑之间的自然语言交互界面。

你的职责：
- 记录与查询：记录现场事件、任务、会议、文件，查询项目数据，让协作有据可查
- 流程引导：通过 SOP（标准作业流程）引导用户规范地完成录入和查询
- 知识问答：基于知识库检索回答项目相关的领域问题

## 二、行为规范

### 回复要求
- 用自然口语表达，要简洁清晰、使用中文、可用少量 emoji 点缀
- 不确定时主动询问，不猜测；出错时诚实说明

### 路由规则
1. 分析用户**核心意图**，而非表面关键词
2. 闲聊（问候/感谢/告别/自我介绍）直接回复，不调工具
3. 多独立请求标记 is_compound=true，拆分为 sub_tasks
4. 无 SOP 匹配时设 fallback=true
5. 置信度：high（明确意图）/ medium（可推断）/ low（模糊）/ none（无法匹配）
6. 用户表达确认/取消意图（如"确认""好的""取消""算了"）时，输出 sop_id="SYS-confirm"，action 为 confirm 或 cancel
7. 用户询问 Emily 自身的能力/权限/分类（如"你能做什么""权限怎么分级"）时，直接基于下方"能力树"回答，设 fallback=true

### 输出要求
仅输出一个 JSON 对象：sop_id（匹配的 SOP 编号或 null）、confidence（high/medium/low/none）、is_compound（true/false）、sub_tasks（子任务数组）、fallback（无匹配时为 true）

### output_spec 派生规则（每个匹配的 SOP 必须输出）
对每个匹配的 SOP，额外输出 output_spec 对象，根据用户诉求从以下维度判断：
- intent: 这个任务的核心意图（简短描述，如 "query_project_summary" / "record_event"）
- detail: brief（简短摘要）| standard（标准）| detailed（详细）—— 按用户表达的详细度期望
- format: natural（自然语言，IM 默认）| list（用户说"列一下/列表"时用）| table
- cite_source: 知识库问答为 true，否则 false

判断依据：用户语气（"详细说一下"→detailed，"简单提一句"→brief）、是否知识库问题、是否列举需求。
sop_id 为 null（fallback）时也要输出 output_spec（元认知类 intent="meta_cognition", detail=detailed, cite_source=true）。

### query_type 派生规则（仅 SOP-005-QRY 命中时输出）
当 sop_id 为 "SOP-005-QRY" 时，必须额外输出 query_type 字段，根据用户查询意图从以下枚举中选择最匹配的一个：
- event：查询事件（如"今天有什么事件"、"最近的事件"）
- task：查询任务（如"有哪些待办任务"、"张三的任务"）
- meeting：查询会议（如"最近的会议"、"会议记录"）
- file：查询文件（如"有哪些文件"、"图纸"）
- message：查询通讯记录（如"刚才聊了什么"）
- conversation：查询会话（如"之前的对话"）
- user：查询用户（如"张三的信息"、"谁负责"）
- project：查询项目概况（如"项目概况"、"参建方"）
- summary：查询综合概况/进展（如"项目进展"、"整体情况"、"最近怎么样"）
- my_nodes：查询当前用户的全景节点（如"我在哪个节点""我负责/参与哪些节点""我的节点"）

判断不准时根据语义推断选最相关的。sop_id 非 SOP-005-QRY 时不要输出 query_type 字段。

## 三、当前会话上下文

### 用户身份
- 姓名：{user_name}
- 职位：{user_position}
- 部门：{user_department}
- 企业：{user_company}（{user_company_type}）
- 权限：{user_permission_level}
- 授权节点：{current_node_ids}

### 项目上下文
- 名称：{project_name}
- 类型：{project_type}
- 状态：{project_status}

### 长期记忆（用户的基本背景和偏好）
{user_memory}

### 往期对话历史（按需检索）
如需查询本次会话之前的对话历史，使用 chat_archive 工具：
- action="history"：查看指定会话的完整对话历史（参数 conversation_id）
- action="user"：查看用户的往期发言记录（参数 user_name 或 user_id）
- action="search"：按关键词搜索历史消息（参数 keyword）

## 四、能力树（你的能力边界 = 下方类型树覆盖的范围）

### 业务流程目录（按类型树路由）
{sop_catalog}

### 知识库
{rag_info}

注意：你的能力边界即上方类型树覆盖的范围。类型树未列出的能力，你不具备——如实告知用户。具体流程的工具与步骤详情在执行阶段由框架按匹配的 sop_id 加载，你无需在路由阶段关心。
```

### 模块验收检测

```powershell
# 验收 1：session.md 不再含已移除的占位符
Select-String -Path "emily-data\prompts\session.md" -Pattern "\{(available_tools|visible_schema|visible_files|node_template_catalog|project_world_book|rule_book|system_description)\}"
→ 预期输出：无匹配（空）

# 验收 2：session.md 仍含 {sop_catalog} 和 {rag_info}
Select-String -Path "emily-data\prompts\session.md" -Pattern "\{(sop_catalog|rag_info)\}"
→ 预期输出：2 行匹配

# 验收 3：session.md 不再含"三书体系"段
Select-String -Path "emily-data\prompts\session.md" -Pattern "三书|项目世界书|规则书|认知书"
→ 预期输出：无匹配（空）

# 验收 4：重启后 intent 调用的 system prompt 字符数大幅下降
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
# 用 trace 解析看 sys_len
docker exec mitmproxy tail -3 /app/logs/llm_trace.jsonl
→ 预期输出：intent 调用（model=deepseek-v4-flash）的 messages[0].content 长度 < 2000（优化前 ~7645）
```

**失败处理**：若 sys_len 未降，检查 session.md 是否被正确替换——`Select-String` 验收 1/2/3 必须通过；若占位符仍在，说明替换未生效或容器内 prompts 目录未更新（检查容器挂载）。

---

## M3: get_prompt_variables 清理

**依赖**：M2（session.md 已移除对应占位符）

**职责**：清理 `get_prompt_variables()` 中已从 session.md 移除的 7 个占位符映射，避免死代码 + 避免 `.replace()` 无效循环。

### 代码

#### `emily-core/emily_core/session/session_context.py` — `get_prompt_variables` 方法替换

定位 [session_context.py:360-389](../emily-core/emily_core/session/session_context.py#L360-L389) 的整个 `get_prompt_variables` 方法，替换为以下精简版本：

```python
    def get_prompt_variables(self) -> dict[str, str]:
        """返回 prompt 模板变量映射。

        P1-1: 移除 7 个已从 session.md 删除的占位符映射
        （available_tools/visible_schema/visible_files/node_template_catalog/
          project_world_book/rule_book/system_description）。
        三书字段保留在 dataclass（PermissionSnapshot 加载不变），仅清理 prompt 变量映射。
        """
        from ..permission.level import level_label as _level_label

        return {
            "{project_name}": self.project_name,
            "{project_type}": self.project_type,
            "{project_status}": self.project_status,
            "{user_name}": self.user_name,
            "{user_position}": self.user_position,
            "{user_company}": self.company_name,
            "{user_company_type}": self.company_type,
            "{user_department}": "、".join(self.department) if self.department else "",
            "{user_level}": _level_label(self.level),
            "{user_permission_level}": _level_label(self.level),
            "{current_node_ids}": "、".join(self.authorized_node_ids),
            "{user_memory}": self.long_term_memory,
            "{sop_catalog}": self.sop_catalog_summary,
            "{available_skills}": ", ".join(self.available_skills) or "（无）",
            "{recent_turns}": "",
            "{rag_info}": self._format_rag_summary(),
        }
```

**注意**：移除的 7 个键是 `{node_template_catalog}` / `{available_tools}` / `{visible_schema}` / `{visible_files}` / `{project_world_book}` / `{rule_book}` / `{system_description}`。保留 `{sop_catalog}`（M1 精简后仍注入）和 `{rag_info}`。dataclass 字段 `project_world_book` / `rule_book` / `system_description` / `node_template_catalog` / `visible_schema_summary` / `visible_files_summary` 等保留（PermissionSnapshot 加载逻辑 L201-203 不变）。

### 模块验收检测

```powershell
# 验收 1：get_prompt_variables 不再返回已移除的占位符
Select-String -Path "emily-core\emily_core\session\session_context.py" -Pattern '\{(available_tools|visible_schema|visible_files|node_template_catalog|project_world_book|rule_book|system_description)\}'
→ 预期输出：无匹配（空）——get_prompt_variables 方法内无这些键

# 验收 2：dataclass 字段保留（PermissionSnapshot 加载不变）
Select-String -Path "emily-core\emily_core\session\session_context.py" -Pattern 'project_world_book|rule_book|system_description'
→ 预期输出：至少 6 行匹配（L94-96 字段定义 + L201-203 加载 + L562-564 snapshot 导出），证明字段未删

# 验收 3：重启后无 KeyError / 模板占位符残留
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
docker logs --tail 30 emily-core 2>&1 | Select-String "error|traceback|KeyError"
→ 预期输出：无匹配（空）——无因占位符缺失导致的错误

# 验收 4：trace 中 system prompt 不含 {xxx} 残留占位符
docker exec mitmproxy tail -3 /app/logs/llm_trace.jsonl
→ 预期输出：intent 调用的 messages[0].content 不含 "{available_tools}" 等未替换占位符
```

**失败处理**：若有 KeyError，检查是否遗漏了某个占位符的清理——session.md 和 get_prompt_variables 必须同步。若有 `{xxx}` 残留，说明 session.md 仍有占位符但 get_prompt_variables 不再返回——回 M2 检查 session.md 是否所有占位符都在 get_prompt_variables 里有映射。

---

## M4: 无孤儿审计

**依赖**：M1（能力树定义已稳定）

**职责**：启动时扫描全部注册的 Skill 和工具，确保都在能力树（类型树）中归类，无树外孤儿。审计结果写日志，未归类的报警告。

### 代码

#### `emily-core/emily_core/tools/registry.py` — `register_all` 末尾追加审计调用

定位 [registry.py:43-48](../emily-core/emily_core/tools/registry.py#L43-L48) `register_all` 函数末尾（`logger.info("registry: %d tools ...")` 之后），追加审计调用：

```python
    _register_base(core, reg)
    _register_business(core, reg)
    _register_project(core, reg)

    logger.info("registry: %d tools (base=%d, business=%d, project=%d)",
                len(reg), _bc, _buc, _pjc)

    # M4: 无孤儿审计——扫描全部工具，确保 category 合法
    _audit_capabilities(reg, core)
```

#### `emily-core/emily_core/tools/registry.py` — 新增 `_audit_capabilities` 函数

在 `register_all` 函数之后（[registry.py:50](../emily-core/emily_core/tools/registry.py#L50) `_bc, _buc, _pjc = 0, 0, 0` 之前）追加新函数：

```python
# 合法的工具 category（与能力树五大域的映射关系）
_VALID_TOOL_CATEGORIES = {
    "base": "基座能力（query_data / knowledge_search / ocr）—— 对应 QRY / KB / DOC 域",
    "business": "业务工具（CRUD / 文件 / 文档处理）—— 对应 REC / FILE / DOC 域",
    "project": "项目级工具（节点 / 邮箱 / 归档）—— 对应 SYS 域",
}


def _audit_capabilities(reg, core) -> None:
    """无孤儿审计 —— 扫描全部注册工具，确保 category 合法（归类到能力树）。

    §3.7 无孤儿审计：每个工具必须挂在能力树某个节点下（通过 category 归类）。
    category 不合法的工具视为孤儿，报警告（不阻断启动）。

    Args:
        reg: BusinessFlowToolRegistry 注册表实例。
        core: EmilyCore 实例，用于获取 skill_registry 做 Skill 侧审计。

    Returns:
        None — 审计结果通过 logger 输出。
    """
    # ── 工具侧审计 ──
    tool_orphans: list[str] = []
    tool_count = 0
    try:
        for tool in reg.list_all():
            tool_count += 1
            cat = getattr(tool, "category", "") or ""
            if cat not in _VALID_TOOL_CATEGORIES:
                tool_orphans.append(f"{tool.name}(category={cat!r})")
    except Exception as e:
        logger.warning("audit_capabilities: tool scan failed: %s", e)

    # ── Skill 侧审计 ──
    skill_orphans: list[str] = []
    skill_count = 0
    skill_registry = getattr(core, "_skill_registry", None)
    if skill_registry is not None:
        try:
            from ..skill.registry import _extract_sop_type
            for skill in skill_registry.list_skills():
                skill_count += 1
                sop_type = _extract_sop_type(skill.sop_id)
                if sop_type == "UNKNOWN":
                    skill_orphans.append(f"{skill.sop_id}(无法推导类型)")
        except Exception as e:
            logger.warning("audit_capabilities: skill scan failed: %s", e)

    # ── 审计报告 ──
    logger.info(
        "audit_capabilities: %d tools, %d skills, orphan_tools=%d, orphan_skills=%d",
        tool_count, skill_count, len(tool_orphans), len(skill_orphans),
    )
    if tool_orphans:
        logger.warning("audit_capabilities: orphan tools (未归类到能力树): %s",
                       ", ".join(tool_orphans))
    if skill_orphans:
        logger.warning("audit_capabilities: orphan skills (sop_id 类型码无法识别): %s",
                       ", ".join(skill_orphans))
```

**注意**：此函数依赖 `reg.list_all()` 方法。若 `BusinessFlowToolRegistry` 无此方法，改用 `reg._tools.values()` 或现有迭代方式——执行前先 Read `business_flow_tools.py` 确认迭代接口。若接口不同，按实际接口调整 `for tool in reg.list_all()` 这一行，其余逻辑不变。

### 模块验收检测

```powershell
# 验收 1：_audit_capabilities 函数存在
Select-String -Path "emily-core\emily_core\tools\registry.py" -Pattern "def _audit_capabilities"
→ 预期输出：1 行匹配

# 验收 2：register_all 末尾调用了审计
Select-String -Path "emily-core\emily_core\tools\registry.py" -Pattern "_audit_capabilities\(reg, core\)"
→ 预期输出：1 行匹配

# 验收 3：重启后日志含审计报告
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
Start-Sleep -Seconds 5
docker logs --tail 50 emily-core 2>&1 | Select-String "audit_capabilities"
→ 预期输出：至少 1 行 "audit_capabilities: N tools, M skills, orphan_tools=0, orphan_skills=0"（0 孤儿为预期；若 >0，审计已检测到并报警告）

# 验收 4：若有孤儿，日志含警告（功能性验证——人为制造孤儿可选，不强制）
docker logs --tail 50 emily-core 2>&1 | Select-String "orphan tools|orphan skills"
→ 预期输出：无匹配（当前所有工具/Skill 都应合法归类）或警告行（若有未归类项）
```

**失败处理**：若 `reg.list_all()` 方法不存在（AttributeError），Read `business_flow_tools.py` 确认 `BusinessFlowToolRegistry` 的迭代接口，调整为实际方法名（如 `iter_tools()` / `_tools.values()`）。若审计报告 orphan > 0，检查对应工具/Skill 的 category/sop_id 是否合法——若不合法是真实孤儿需修复，若合法是审计逻辑误判需调整 `_VALID_TOOL_CATEGORIES` 或 `_extract_sop_type`。

---

## 组装验证

所有模块完成后，端到端验证 prompt 瘦身 + cache 命中 + 路由准确率：

```powershell
# 1. 清缓存 + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 先查真实用户
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 5;"

# 3. 同一 Session 连发 3 条消息（验证 cache 命中 + 路由准确）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
Start-Sleep -Seconds 3
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "真实用户名"
Start-Sleep -Seconds 3
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天有什么事件" --sender "真实用户名"

# 4. 查看 trace：system prompt 长度 + cache 命中 + 路由结果
docker exec mitmproxy tail -15 /app/logs/llm_trace.jsonl
```

**预期输出**：
- intent 调用的 `messages[0].content` 长度 **< 2000**（优化前 ~7645，降 ~75%）
- intent 调用的 `prompt_tokens` **< 1000**（优化前 ~3889，降 ~75%）
- 第 2/3 次 intent 调用 `prompt_cache_hit_tokens > 0`（cache 命中，第一刀基础保持）
- 所有调用 `sop_id` 非空或 fallback 正确（路由准确率未降——`{sop_catalog}` 仍含 SOP id + 说明首行，路由信息充分）
- 日志含 `audit_capabilities: N tools, M skills, orphan_tools=0, orphan_skills=0`

**失败处理**：
- sys_len 未降 → 检查 M1/M2 是否生效（session.md 是否替换、dump_as_text 是否精简）
- 路由退化（sop_id 全空）→ 检查 {sop_catalog} 是否仍含 SOP id + 说明首行（M1 精简不能移除路由必需信息）；必要时回退 M1 第二部分保留说明首行
- cache 命中率下降 → 检查 M2 重排后 L1 静态前缀是否仍稳定（角色+规范段不变）
- orphan > 0 → M4 审计检测到真实孤儿，按警告信息修复对应工具/Skill 的 category/sop_id

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户

---

## 关键提醒

1. **`__pycache__` 必须清**：每次改完代码重启 emily-core 前必须清，否则 Docker bind-mount 不刷新
2. **emy-test 禁用假 sender-id**：必须用真实用户名（`--sender`），否则自动创建用户污染 DB + 权限降级使结果不可信
3. **M2 不能移除 {sop_catalog}**：这是路由的核心信息（SOP id + 说明首行），移除会导致路由退化。M1 只是精简第二部分（移除工具/步骤详情），不能移除 SOP id 和说明首行
4. **M3 不能删 dataclass 字段**：`project_world_book` 等字段保留（PermissionSnapshot 加载 L201-203 不变），只清理 `get_prompt_variables()` 的占位符映射
5. **M4 的 `reg.list_all()` 接口需确认**：执行前先 Read `business_flow_tools.py` 确认迭代接口，若不同则按实际接口调整
6. **不动 workitem.md**：planner 阶段的 `{available_tools}` 保留（规划步骤需要工具清单），第二刀只改 session.md
7. **project_world_book 主动 RAG 索引不在本计划**：若 maxkb 无项目世界书内容，作为后续任务。当前移除注入后，执行阶段需要项目背景时走现有 `query_data` / `knowledge_search` 工具

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。基于 [需求/Session系统提示优化_需求_V1.md](../需求/Session系统提示优化_需求_V1.md) 第二刀范围（§三 设计原则 + §四 P1-1）。前置：第一刀已执行（cache 命中 97%）。*
