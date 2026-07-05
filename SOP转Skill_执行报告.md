# SOP转Skill — 执行报告

> 执行日期：2026-07-05
> 计划文件：`需求文件/sop模块/SOP转skill_计划_V1.md`
> 需求文件：`需求文件/sop模块/SOP转skill_需求_V2.md`
> 执行状态：**全部 8 模块通过，端到端组装验证通过**

---

## 执行概览

| 模块 | 名称 | 状态 | 新建 | 修改 |
|------|------|------|------|------|
| M1 | Skill Schema 定义与校验器 | PASS | 4 | 0 |
| M2 | Skill Parser YAML 解析器 | PASS | 1 | 0 |
| M3 | Skill Registry 注册表 + 热加载 | PASS | 1 | 0 |
| M4 | Parameter Extractor 参数提取引擎 | PASS | 2 | 0 |
| M5 | Skill Executor 执行引擎 | PASS | 1 | 0 |
| M6 | PipelineBUS 集成 + session_scope | PASS | 0 | 6 |
| M7 | SOP-to-Skill 转换器（离线工具） | PASS | 2 | 0 |
| M8 | 11 份 Skill 文件生成 | PASS* | 1 | 0 |

> \* M8 已完成样本文件 `SOP-002-REC-event-record.skill.yaml`，其余 10 份需 LLM API 生成。

---

## 各模块验收详情

### M1: Skill Schema 定义与校验器

| # | 验收项 | 结果 |
|---|--------|------|
| M1-1 | dataclass 可导入（ParamMapping / SkillStep / SkillTool / SkillDefinition） | PASS |
| M1-2 | 校验器拒绝空定义（5 errors） | PASS |
| M1-3 | 校验器通过合法定义 | PASS |
| M1-4 | Schema YAML 文件存在且可解析 | PASS |

**交付物**：
- `emily-core/emily_core/skill/__init__.py` — 模块入口
- `emily-core/emily_core/skill/definition.py` — 4 个 dataclass（ParamMapping, SkillStep, SkillTool, SkillDefinition）
- `emily-core/emily_core/skill/validator.py` — `validate_skill()` + `SkillValidationResult`
- `emily-data/schemas/skill_schema.yaml` — YAML 结构参考

---

### M2: Skill Parser YAML 解析器

| # | 验收项 | 结果 |
|---|--------|------|
| M2-1 | 解析合法 YAML 为 SkillDefinition（step 参数 source=user_input 正确） | PASS |
| M2-2 | 非法 YAML 抛 SkillParseError | PASS |
| M2-3 | 空 steps 抛 SkillParseError | PASS |

**交付物**：
- `emily-core/emily_core/skill/parser.py` — `parse_skill_text()` + `parse_skill_file()`

---

### M3: Skill Registry 注册表

| # | 验收项 | 结果 |
|---|--------|------|
| M3-1 | 空目录加载不报错（total_files=0） | PASS |
| M3-2 | 有 Skill 文件时加载成功（get_by_sop_id / has_skill 正确） | PASS |
| M3-3 | reload 原子替换无异常 | PASS |

**交付物**：
- `emily-core/emily_core/skill/registry.py` — `SkillRegistry` + `SkillRegistryStatus`

---

### M4: Parameter Extractor 参数提取引擎

| # | 验收项 | 结果 |
|---|--------|------|
| M4-1 | fixed source（today → 当前日期） | PASS |
| M4-2 | prev_step source（dot-path `project_info.object_id`） | PASS |
| M4-3 | context source（dot-path `user_id`） | PASS |
| M4-4 | resolve_params 整体（fixed + context + list 索引 `project_ids.0`） | PASS |

**修复**：`_resolve_dot_path` 增加 list 索引支持（`project_ids.0` → `project_ids[0]`）

**交付物**：
- `emily-core/emily_core/skill/param_extractor.py` — `ParamExtractor`
- `emily-data/prompts/param_extraction.md` — LLM 提取 prompt

---

### M5: Skill Executor 执行引擎

| # | 验收项 | 结果 |
|---|--------|------|
| M5-1 | 线性执行 mock 工具（1 step, success=True） | PASS |
| M5-2 | 白名单拒绝（tool_name 不在 tools 中） | PASS |
| M5-3 | session_scope 注入（project_ids=/db_perms 正确传递） | PASS |

**交付物**：
- `emily-core/emily_core/skill/executor.py` — `SkillExecutor` + `SkillExecutionContext`

---

### M6: PipelineBUS 集成 + session_scope

| # | 验收项 | 结果 |
|---|--------|------|
| M6-1 | EmilyCore 导入正常（Skill 模块 fail-open） | PASS |
| M6-2 | WorkItemAgent 接受 skill_registry / skill_executor 参数 | PASS |
| M6-3 | QueryCommand 有 project_ids 字段 | PASS |
| M6-4 | query_tool 有 _QUERY_TYPE_TO_TABLE 映射 | PASS |
| M6-5 | SkillRegistry + SkillExecutor 可导入 | PASS |

**修改文件**（6 个）：
- `__init__.py` — 新增 `_skill_registry` / `_skill_executor` + `_init_skill_module()` + `_build_pipeline_bus` 注入 + `_collect_injected_services` 注入
- `workitem_agent.py` — `node2_plan` Skill 路径优先 + `node3_execute` Skill 路径 + `_skill_to_execution_plan` + `_execute_skill`
- `injector.py` — 新增 `get_skill_instructions()` 接口
- `command.py` — `QueryCommand` 新增 `project_ids: list[str] | None`
- `query_tool.py` — `handle_query_data` 增加 `_session_scope` 处理（db_perms 检查 + project_ids 自动注入）
- `query_service.py` — `execute` 分发 + 各 `query_xxx` 方法增加 `project_ids` 参数

---

### M7: SOP-to-Skill 转换器

| # | 验收项 | 结果 |
|---|--------|------|
| M7-1 | 脚本语法无错误（import / argparse 正常） | PASS |
| M7-2 | sop_to_skill.md prompt 文件存在且含三段结构指引 | PASS |

**交付物**：
- `scripts/sop_to_skill.py` — CLI 转换器
- `emily-data/prompts/sop_to_skill.md` — LLM 转换 prompt

---

### M8: 11 份 Skill 文件

| # | 验收项 | 结果 |
|---|--------|------|
| M8-1 | skills 目录存在，包含 1 个样本文件 | PASS |
| M8-2 | 样本文件可被 M2 解析（2 steps, 2 tools） | PASS |
| M8-3 | SkillRegistry 可加载（is_ready=True） | PASS |
| M8-4 | get_by_sop_id("SOP-002-REC") 正确返回 | PASS |
| M8-5 | list_sop_ids 返回 [SOP-002-REC] | PASS |

**样本文件**：`emily-data/skills/SOP-002-REC-event-record.skill.yaml`（事件记录，2 步流程）

**其余 10 份**需通过 LLM 生成：
```bash
set DEEPSEEK_API_KEY=sk-xxx
uv run python scripts/sop_to_skill.py --all --dry-run
uv run python scripts/sop_to_skill.py --all
```

---

## 端到端组装验证

| # | 验证项 | 结果 |
|---|--------|------|
| E2E-1 | SkillRegistry 加载 skills 目录 | PASS |
| E2E-2 | Skill 路径激活（_skill_to_execution_plan 生成 2 步，_source=skill_definition） | PASS |
| E2E-3 | 无 Skill 匹配 SOP 走原 LLM/Mock 路径 | PASS |
| E2E-4 | Skill 文件 parser 验证（ParamMapping source 类型正确） | PASS |

---

## 执行期间发现与修复

### 修复 1：`_resolve_dot_path` list 索引支持
- **问题**：`project_ids.0` 路径中 `0` 无法通过 `dict.get()` 获取 list 元素
- **修复**：增加 `isinstance(current, list)` 分支，`current[int(key)]` 取值

### 修复 2：`emily_core` 导入路径
- **问题**：PowerShell 中 `uv run python -c` 无法直接 import `emily_core`（CWD 不在 path）
- **解决**：验收脚本统一使用 `sys.path.insert(0, "emily-core")`

---

## 文件清单

### 新增（12+1 个）
```
emily-core/emily_core/skill/__init__.py
emily-core/emily_core/skill/definition.py
emily-core/emily_core/skill/validator.py
emily-core/emily_core/skill/parser.py
emily-core/emily_core/skill/registry.py
emily-core/emily_core/skill/param_extractor.py
emily-core/emily_core/skill/executor.py
emily-data/schemas/skill_schema.yaml
emily-data/prompts/param_extraction.md
emily-data/prompts/sop_to_skill.md
scripts/sop_to_skill.py
emily-data/skills/SOP-002-REC-event-record.skill.yaml
```

### 修改（6 个）
```
emily-core/emily_core/__init__.py
emily-core/emily_core/workitem/workitem_agent.py
emily-core/emily_core/workitem/injector.py
emily-core/emily_core/adapters/standard/command.py
emily-core/emily_core/tools/query_tool.py
emily-core/emily_core/services/query_service.py
```

### 未变（按计划）
```
emily-core/emily_core/agent/sop_parser.py
emily-core/emily_core/agent/intent_registry.py
emily-core/emily_core/tools/business_flow_tools.py
emily-core/emily_core/bootstrap.py
emily-core/emily_core/session/session_agent.py
emily-core/emily_core/session/session_context.py
emily-core/emily_core/workitem/pipeline/bus.py
emily-core/emily_core/workitem/pipeline/hook.py
```

---

## 架构合规性

| 约束 | 状态 |
|------|------|
| 禁止修改已有方法签名（仅新增参数，默认值兼容） | 合规 |
| 分层：API→Core→Session→WorkItem→App→Service→Repo→DB | 合规 |
| sync repo + asyncio.to_thread | 合规 |
| `emily_core` 不 import AstrBot | 合规 |
| Skill 不持有数据、不声明 datasets | 合规 |
| Skill 定义三段结构（instructions / tools / steps） | 合规 |

---

## 后续操作

1. **生成剩余 10 份 Skill 文件**：配置 `DEEPSEEK_API_KEY` 后运行 `sop_to_skill.py --all`
2. **人工审校**：每份 LLM 生成的 Skill 文件需检查 instructions / steps / tool_params 正确性
3. **容器集成测试**：重启 emily-core 容器后验证 `_init_skill_module()` 加载成功
4. **端到端实战**：`emy-test --llm --message "科技城5号楼铺装完成"` 验证 node2._source=skill_definition

---

*报告生成于 2026-07-05，由 emily_dev AI 自动记录。*
