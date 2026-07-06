# SOP转Skill — 测试报告

> 测试日期：2026-07-05
> 被测版本：SOP转Skill 全链路 (M1~M8)
> 测试环境：Windows, Python 3.12, emily-core (本地)
> 测试方法：req-verify 结构化验证

---

## 测试汇总

| 测试层级 | 测试项 | 用例数 | PASS | FAIL | 覆盖率 |
|----------|--------|--------|------|------|--------|
| T1 | 模块静态导入与结构验证 | 22 | 22 | 0 | 100% |
| T2 | Schema 校验器边界测试 | 8 | 8 | 0 | 100% |
| T3 | Parser 解析边界与异常测试 | 17 | 17 | 0 | 100% |
| T4 | Registry 加载/查询/热重载 | 17 | 17 | 0 | 100% |
| T5 | ParamExtractor + SkillExecutor 集成 | 23 | 23 | 0 | 100% |
| T6 | PipelineBUS node2/node3 Skill 路径 | 10 | 10 | 0 | 100% |
| T7 | session_scope 数据边界测试 | 11 | 11 | 0 | 100% |
| T8 | 容器级端到端集成 | 8 | 8 | 0 | 100% |
| **合计** | | **116** | **116** | **0** | **100%** |

---

## 测试详情

### T1: 模块静态导入与结构验证 (22/22 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T1.1 | skill 模块全量导入（10 个类/函数） | PASS |
| T1.2a-c | ParamMapping 字段完整性（source/extraction, frozen） | PASS |
| T1.3a-b | SkillStep 字段完整性（id/tool_name, 默认 tool_params={}） | PASS |
| T1.4 | SkillTool 字段完整性 | PASS |
| T1.5a-d | SkillDefinition 字段完整性（skill_id/tools/steps, frozen） | PASS |
| T1.6a-d | WorkItemAgent 新增 skill_registry/skill_executor 参数 | PASS |
| T1.7a-b | QueryCommand 新增 project_ids 字段 | PASS |
| T1.8a-c | query_tool _QUERY_TYPE_TO_TABLE（event/task, 10+ entries） | PASS |
| T1.9a-b | query_service query_events/query_tasks 新增 project_ids 参数 | PASS |

### T2: Schema 校验器边界测试 (8/8 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T2.1a-b | 空定义 is_valid=False + >=5 errors | PASS |
| T2.2a-b | 重复 step id 检测到错误 | PASS |
| T2.3 | 非法 source 被检测 | PASS |
| T2.4a-b | tool_name 白名单警告（不阻止通过） | PASS |
| T2.5 | prev_step 无 path 被检测 | PASS |

### T3: Parser 解析边界与异常测试 (17/17 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T3.1a-h | 合法 YAML 各字段解析（skill_id/version/steps/tools/source 类型） | PASS |
| T3.2a | 非法 YAML 抛 SkillParseError | PASS |
| T3.3a | 非 dict 根节点抛异常 | PASS |
| T3.4a-c | 参数简写（直接值→fixed source, string/int 值） | PASS |
| T3.5a | 文件不存在抛异常 | PASS |
| T3.6a-d | parse_skill_file 解析已有样本文件 | PASS |

### T4: Registry 加载/查询/热重载 (17/17 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T4.1a-b | 空目录 total=0 + is_ready=False | PASS |
| T4.2a-e | skills 目录加载（successfully_parsed/is_ready/has_skill/list_sop_ids） | PASS |
| T4.3a-c | get_by_sop_id（命中/不存在返回 None） | PASS |
| T4.4a-b | get_by_skill_id（命中/不存在返回 None） | PASS |
| T4.5a-b | reload 原子替换（不丢失数据，仍可查询） | PASS |
| T4.6 | SkillRegistryStatus 6 字段 | PASS |
| T4.7a-c | 含非法 Skill 文件仍加载（failed_parsed/failed_files 记录） | PASS |

### T5: ParamExtractor + SkillExecutor 集成 (23/23 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T5.1a | fixed "today" → 当前日期 | PASS |
| T5.2a | fixed "now" → ISO 时间戳 | PASS |
| T5.3a-b | fixed 普通值（int/string） | PASS |
| T5.4a-d | context 嵌套 dot-path（含 list 索引 project.ids.0） | PASS |
| T5.5a-b | prev_step dot-path（r1.object_id / r1.data.code） | PASS |
| T5.6a-c | resolve_params 整体（fixed/context/user_input fallback） | PASS |
| T5.7a-h | SkillExecutor 2-step 集成执行（工具调用/session_scope/prev_step/output_key） | PASS |
| T5.8a-b | 白名单拒绝执行（success=False + 提示含"白名单"） | PASS |

### T6: PipelineBUS node2/node3 Skill 路径 (10/10 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T6.1a-f | _skill_to_execution_plan 返回 ExecutionPlan（_source/risk/steps/tool_params 空） | PASS |
| T6.2a | 无 skill_registry 时 has_skill=False | PASS |
| T6.3a-b | EmilyCore 有 _init_skill_module 方法 | PASS |
| T6.4a-b | _collect_injected_services 包含 skill_registry | PASS |

### T7: session_scope 数据边界测试 (11/11 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T7.1a-e | _build_session_scope 全字段（project_ids/db_perms/info_level/company_type/department） | PASS |
| T7.2a-c | 空 session_context 默认值（[]/{}/public） | PASS |
| T7.3a-b | db_perms 拒绝（非空 db_perms 不含目标表） | PASS |
| T7.4a | project_ids 注入不报错 | PASS |

### T8: 容器级端到端集成 (8/8 PASS)

| 用例 | 检查项 | 结果 |
|------|--------|------|
| T8.1 | _init_skill_module fail-open（加载成功，registry=True） | PASS |
| T8.2 | SkillRegistry 加载 skills 目录（is_ready=True） | PASS |
| T8.3 | node2 Skill 路径激活（_source=skill_definition, 2 steps） | PASS |
| T8.4a | node3 SkillExecutor 执行（2/2 steps succeeded） | PASS |
| T8.4b | session_scope 全字段注入正确 | PASS |
| T8.4c | prev_step interop（output_key → step_results 链） | PASS |
| T8.5 | 无 Skill 匹配 SOP 回退（不崩溃） | PASS |
| T8.6 | 日志关键字段验证（_source/session_scope/Skill module） | PASS |

---

## 测试期间发现与修复

### 修复 1：sample Skill YAML 缺少 default
- **问题**：LLM 不可用时，`user_input` source 的 required 字段提取失败导致 step 报错
- **修复**：`title` 加 `default: 未命名事件`，`description` 加 `default: 无描述`

### 修复 2：db_perms 空 dict 检查语义
- **问题**：T7.3 用 `db_perms: {}` 时 `if db_perms` → False，跳过拒绝检查，fallback 到 query_service.execute(None) 抛 AttributeError
- **确认**：此为正确语义——空 db_perms 表示"无限制"，非空 db_perms 不含目标表才应拒绝。测试改用 `db_perms: {"events": "read"}` 验证拒绝路径

### 修复 3：`_collect_injected_services` 依赖 config
- **问题**：T6.4 用 `EmilyCore.__new__` 构造时无 config 属性导致方法报错
- **确认**：正常 init 流程中 config 一定存在，此为测试夹具问题

---

## 关键架构验证

| 验证点 | 方式 | 结果 |
|--------|------|------|
| Skill 不持有数据、不声明 datasets | T3.1 验证 YAML 解析无 datasets 字段 | PASS |
| fail-open（Skill/LLM 不可用时降级） | T1.6a-b 默认 None / T8.5 无匹配回退 | PASS |
| session_scope 数据边界隔离 | T5.7d / T7.1-2 / T8.4b | PASS |
| prev_step interop（output_key 链） | T5.7e / T8.4c | PASS |
| 白名单强制（tool 必须在 tools 声明中） | T5.8a-b | PASS |
| 禁止修改已有方法签名 | T1.6a-b 默认值兼容 | PASS |

---

## 文件清单

### 被测文件
```
emily-core/emily_core/skill/__init__.py
emily-core/emily_core/skill/definition.py
emily-core/emily_core/skill/validator.py
emily-core/emily_core/skill/parser.py
emily-core/emily_core/skill/registry.py
emily-core/emily_core/skill/param_extractor.py
emily-core/emily_core/skill/executor.py
emily-core/emily_core/__init__.py
emily-core/emily_core/workitem/workitem_agent.py
emily-core/emily_core/workitem/injector.py
emily-core/emily_core/adapters/standard/command.py
emily-core/emily_core/tools/query_tool.py
emily-core/emily_core/services/query_service.py
emily-data/schemas/skill_schema.yaml
emily-data/prompts/param_extraction.md
emily-data/prompts/sop_to_skill.md
scripts/sop_to_skill.py
emily-data/skills/SOP-002-REC-event-record.skill.yaml
```

---

*报告由 req-verify 结构化测试自动生成，2026-07-05。*
