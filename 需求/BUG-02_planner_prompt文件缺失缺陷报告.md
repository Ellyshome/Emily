# BUG-02: planner.md 提示词文件缺失，始终使用硬编码兜底

| 属性 | 值 |
|------|-----|
| **缺陷编号** | BUG-02 |
| **严重级别** | P3 — 功能正常但有运维隐患（代码即配置，无法热更新） |
| **发现日期** | 2026-07-29 |
| **发现方式** | 编译/导入静态验证 |
| **影响范围** | node2 规划器 prompt（仅 `WorkItemAgent._llm_plan` 消费） |
| **责任人** | 待指定 |

---

## 现象

emily-core 启动时在 Docker 日志中输出：

```
Prompt 'planner' file not found, using hardcoded default
```

这意味着对 planner prompt 的任何调整都必须修改 Python 源码并重新部署，无法像 `session.md` / `workitem.md` 等其他 prompt 文件一样通过编辑 Markdown 文件热更新。

---

## 根因分析

### 提示词加载机制

[prompt_loader.py](file:///d:/app/Emily/emily-core/emily_core/infrastructure/llm/prompt_loader.py) 采用四层路径查找链（`_find_prompt_path()`）：

1. 显式传入的 `prompts_dir` 参数
2. 环境变量 `EMILY_PROMPTS_DIR`
3. 容器默认路径 `/app/prompts/`
4. 开发环境的相对路径：`Path(__file__).parents[4] / "emily-data" / "prompts" / "{name}.md"`

对于 `"planner"` 名称，第 4 层解析为：

```
d:\app\Emily\emily-core\emily_core\infrastructure\llm\prompt_loader.py
  → .parents[4] = d:\app\Emily\          （项目根目录）
  → d:\app\Emily\emily-data\prompts\planner.md   ← 这个文件不存在
```

### 现有 prompt 文件对比

`emily-data/prompts/` 目录下现有 15 个 `.md` 文件：

| 存在 | 缺失 |
|------|------|
| error_analysis.md | **planner.md** |
| evolution_insight.md | |
| evolution_patch.md | |
| evolution_problem_report.md | |
| evolution_rule.md | |
| guardian_reply.md | |
| guardian_step.md | |
| ops_monitor_brief.md | |
| ops_monitor_review.md | |
| param_extraction.md | |
| project.md | |
| session.md | |
| session_reply.md | |
| sop_to_skill.md | |
| workitem.md | |

### 硬编码兜底当前值

[prompt_loader.py L93-L119](file:///d:/app/Emily/emily-core/emily_core/infrastructure/llm/prompt_loader.py#L93-L119)：

**注意**：此默认值已通过 `result_constraints_计划_V1` 更新，包含了第 7 条规划规则和 `{result_constraints}` 模板变量。如果将来 planner.md 被创建但内容不包含这些更新，会导致 `result_constraints` 功能退化。

---

## 影响分析

| 维度 | 当前 | 修复后 |
|------|------|--------|
| 功能正确性 | 正常 — 硬编码值与预期一致 | 不变 |
| prompt 迭代 | 必须改 Python 源码 + 重新部署 | 编辑 .md 文件即可，容器自动加载 |
| 多环境管理 | 无法按环境定制 | 可挂载不同的 planner.md |
| 版本管理 | prompt 变更与代码变更耦合 | prompt 独立 Git 追踪 |
| 灰度/AB 测试 | 不支持 | 支持 |

---

## 修复方案

### 操作步骤

**第一步**：创建文件

```
d:\app\Emily\emily-data\prompts\planner.md
```

**第二步**：将以下内容写入文件（从硬编码默认值提取，确保与当前行为完全一致）：

```markdown
你是 Emily 的执行规划器。根据业务流程（SOP）和用户输入，制定逐步的执行计划。

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
{"risk_level": "L1|L2|L3", "steps": [{"step_id": "step-01", "description": "步骤描述", "tool_name": "record_event|null", "tool_params": {"title": "事件标题", "event_type": "施工节点", "description": "详细描述"}}, "expected_output": "预期产出", "depends_on": []}], "acceptance_criteria": ["验收标准1"], "estimated_steps": N}
```

**第三步**：重启 emily-core 容器或等待 prompt 自动重载。

---

## 验证方法

修复后：

```powershell
# 确认文件存在
ls d:\app\Emily\emily-data\prompts\planner.md

# 确认 emily-core 加载了文件而非硬编码
docker logs emily-core --tail 50 | Select-String "planner"

# 确认 result_constraints 模板变量存在于文件中
Select-String -Path d:\app\Emily\emily-data\prompts\planner.md -Pattern "{result_constraints}"

# 发送一条 queries 确认 node2 规划正常运行
uv run python .claude/skills/emy-test/cli.py --managed \
  --qq 123456001 --sender 王建国 \
  --message "看看翠湖庭院的进度"
```

**预期**：
- Docker 日志中不再出现 `Prompt 'planner' file not found` 警告
- 查询功能正常返回
- 后续修改 `planner.md` 后，容器内 prompt 自动使用新内容
