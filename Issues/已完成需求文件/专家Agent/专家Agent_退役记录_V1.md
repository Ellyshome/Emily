# 专家Agent — 退役记录 V1

> **执行日期**：2026-09-16
> **目标**：将「专家」模块**整线退役**（专家库 + 专家评审），移除工具、仓储、数据表、执行图节点、配置项、SOP 与手册文件，不留半条线。
> **决策**：项目所有者确认「全退」（2026-09-16）；本记录**撤销**前次保留决定（见 §二）。
> **依据**：[专家Agent_PRD_V1.md](专家Agent_PRD_V1.md)、[专家Agent_计划_V1.md](专家Agent_计划_V1.md)、[LangGraph编排内核化_退役记录_V1.md](../LangGraph编排内核化/LangGraph编排内核化_退役记录_V1.md)

## 一、退役对象清单

### 1.1 删除的文件（8 个）

| 文件 | 内容 |
|------|------|
| `emily-core/emily_core/tools/expert_manage_tool.py` | 4 个 LLM 工具（create/approve/toggle/query_expert）+ 4 个 schema |
| `emily-core/emily_core/repositories/expert_repo.py` | `ExpertRepository` / `ExpertApprovalRepository`（全仓唯一读写 experts 表处） |
| `emily-core/emily_core/services/expert_manual_loader.py` | `ExpertManualLoader`（专家手册加载器，仅评审节点消费） |
| `scripts/repro_expert_review_switch.py` | `expert_review_enabled` 死开关复现脚本（未注册进脚本注册表） |
| `emily-data/sops/SOP-012-SYS-expert_review.md` | 专家评审 SOP（会被 SkillRegistry 索引进意图目录） |
| `emily-data/files/Expert Work Manual/EXP-001-苗木使用审核职能手册.md` | 专家职能手册 |
| `emily-data/files/Expert Work Manual/EXP-001-苗木审核任务手册.md` | 专家任务手册 |
| `emily-data/files/Expert Work Manual/待审-星湖湿地公园植物设计说明.md` | 待审样例文件 |

### 1.2 删除的代码块

| 文件 | 内容 | 规模 |
|------|------|------|
| `workitem/langgraph_engine/graph.py` | `expert_review` 节点注册、条件边与路由（`make_route_after_routing` / `route_after_routing` / `_route_expert_or_executing`）；`routing → executing` 改为直连 | −63 行 |
| `workitem/langgraph_engine/nodes.py` | `build_expert_prompt` / `make_expert_review` / `_normalize_expert_result` + `make_summarizing` 内的专家分支 | −207 行 |
| `workitem/workitem.py` | `expert_id` / `expert_required` / `expert_review_result` 三字段 | −10 行 |
| `infrastructure/database/models.py` | `Expert` / `ExpertApproval` 两个 ORM 类（`experts` / `expert_approvals` 两表）+ 表清单注释 | −54 行 |
| `tools/registry.py` | 专家工具注册段（eager import + 4 个 `_reg_biz`） | −20 行 |
| `infrastructure/tools_consistency.py` | `REGISTERED_TOOLS` / `TOOL_META_MAP` / `TOOL_SCHEMA_MAP` 的专家条目；并在 `REMOVED_TOOLS` 登记 4 个工具名（触发 DB 残留行自动停用） | −11 / +5 行 |
| `session/session_agent.py` | `_match_expert()` 与调用点、docstring 措辞 | −20 行 |
| `session/capability_runner.py` | `SYSTEM_INTERNAL_SOPS` 中的 `SOP-012-SYS`、模块 docstring 措辞 | −3 行 |
| `config.py` | `expert_review_enabled` / `expert_model` / `llm_expert_max_tokens` 三配置项 | −11 行 |
| `bootstrap.py` | `EMILY_EXPERT_MODEL` / `EMILY_EXPERT_REVIEW_ENABLED` 环境映射 + `ENV_BOOL_FIELDS` 条目 | −3 行 |
| `services/config_inventory.py` | 配置清单中的 3 个专家字段展示项 | −2 行 |
| `langgraph_engine/agent/fallback_policy.py` | `_ADVANCED_READ_TOOLS` 中的 `query_experts` | −1 行 |
| `tests/test_defect_fixes.py` | B 段（expert_review_enabled 接线测试 5 例 + 夹具） | −57 行 |
| `scripts/kernel_regression.py` | 测试桩 `CfgW.expert_review_enabled` | −1 行 |
| `scripts/wiring_scan.py` | docstring 示例参数名改为真实存在的字段 | ±1 行 |
| `session/kernel_state.py` | 仅注释：说明端口族已无消费方（孤儿），按约束 13 另案处理 | ±3 行 |

### 1.3 删除的配置与数据

| 对象 | 处置 |
|------|------|
| `emily-data/config/retrieval_channels.json` 的「专家名册」通道登记（`enabled:false`，指向 `query_experts`） | 删除条目并修正 JSON 尾逗号 |
| `docker-compose-{napcat,wecom,minimal}.yml` 的 `EMILY_EXPERT_MODEL` 环境变量行 | 各删 1 行 |
| `experts` / `expert_approvals` 两张表 | **数据先归档后 DROP**（见 §五.1） |

## 二、撤销前次保留决定

[LangGraph编排内核化_退役记录_V1.md](../LangGraph编排内核化/LangGraph编排内核化_退役记录_V1.md) §三「冷代码保留（未实现的原功能，休眠不删）」曾按 D10 决策把 `expert_*`（工具与节点）列为**冷代码保留**。

**本次撤销该决定并整线退役**，理由：
1. 专家库的自维护入口（4 个工具）若删除而评审节点保留，会形成"能力无法维护但机制仍可触发"的半条线（约束 12 的孤儿机制）；
2. 专家评审节点、SOP-012、配置开关三者构成完整链路，单独保留任一段都无独立价值；
3. 业务侧确认该功能暂不再需要。

## 三、依赖顺序（为何必须同批）

删除存在 4 处**硬失败点**，故必须同批执行（本轮按要求顺序完成）：

| 位置 | 若不处理会发生的错误 |
|------|---------------------|
| `tools/registry.py` 的 eager import | `ModuleNotFoundError`（在 `register_all` 内、无 try/except）→ 内核启动中断 |
| `graph.py` 的模块级 `from .nodes import make_expert_review` | `ImportError` → 图构建失败，全链路不可用 |
| `tests/test_defect_fixes.py` 引用被删符号/字段 | 测试收集期 `ImportError` / `TypeError` |
| `scripts/repro_expert_review_switch.py` 引用被删符号/字段 | `AttributeError`（该脚本已整体删除） |

## 四、验证证据

| 项 | 方法 | 结果 |
|----|------|------|
| 语法 | `py_compile`（16 个改动文件） | EXIT=0 |
| 配置 | `json.load(retrieval_channels.json)` / 3 个 compose `yaml.safe_load` | 均通过 |
| 冷启动 | 清 `__pycache__` → 重启 emily-core → 检索日志 | **无 ERROR / Traceback / ImportError** |
| 图形态 | 容器内 `build_workitem_graph(...)` | 节点 = `created / routing / executing / agent_node / tool_node / summarizing / quality_gate / error_analysis`，**无 `expert_review`** |
| 工具数 | 启动日志 `audit_capabilities` | **45 → 41 tools**（-4 专家工具）；skills 11 → 10（-SOP-012） |
| DB 残留 | 启动日志 `_ensure_tool_registry_seed` | `deactivated 4 removed tools`（`tool_registry` 表 4 行自动停用） |
| 一致性 | `scripts/check_tools_consistency.py` | 36 注册工具 / 0 fatal；1 处 warning 为既有 `write_user_memory` 缺 schema（与本退役无关） |
| 单测 | `pytest tests/test_defect_fixes.py` | **7 passed**（A 段保留，B 段随退役移除） |
| 残留检查 | emily-core / scripts 全目录 grep `expert` | 仅剩 3 处**有意保留**：`REMOVED_TOOLS` 登记、`models.py` 表清单注释、退役说明注释 |
| 数据表 | `to_regclass('experts' / 'expert_approvals')` | 均为空（表已 DROP） |

## 五、保留与遗留

### 5.1 EXP-001 数据已归档（删表前）

`experts` 表原有 1 行（论文 E2 实验所用）：`EXP-001 苗木使用审核专家 / ACTIVE / SOP-012-SYS-expert_review`。
删表前已导出为 [EXP-001_archive.json](EXP-001_archive.json)（本目录）。

### 5.2 有意保留（不随业务能力退役一并删）

| 对象 | 保留理由 |
|------|---------|
| `kernel_state.py` 的 `current_tool_user_id` / `current_tool_perm_dict` / `_read_graph_context_fallback` | 属**内核端口族**（`ToolContext`）；删专家工具后已无消费方（**孤儿状态已记入代码注释**），按约束 13 内核纪律另案评估退役 |
| `tool_registry` 表中 4 条专家工具行 | 由 `REMOVED_TOOLS` 机制自动置为停用（保留行以便审计） |
| `docs/论文类/**`（第 4/5 章、论文框架、E2 实验计划与记录、drawio 附图） | 论文与实验档案，属交付文档 |
| `docs/Manual/开发记录.md`、`issues/**` 历史文档 | 历史决策与需求档案 |
| `emily-data/prompts/*.md`、`baseknowledge/*`、`company_policies/生命周期.md`、`node_templates/*` | "专家"为业务名词或角色措辞（如"专家论证"），与代码无关 |

### 5.3 影响提示（须业务侧知悉）

1. **论文场景③ / E2 实验不可复现**：`docs/论文类/` 第 4.5 节与 5.3.3 节、`E2实验计划/记录` 均以「特化专家智能体审核」为验证场景之一，其依赖（`SOP-012`、`experts` 表、`Expert Work Manual/` 手册）已随本次退役删除。论文框架 V3 中"专家审核场景是否保留"本就是待拍板项，退役后该决策被事实确定；如仍需该场景，需要按论文口径重新实现或另选场景。
2. **`retrieval_channels.json` 少一个通道登记**：该通道原 `enabled:false`（休眠），删除不影响运行时行为。
3. **配置项移除**：`EMILY_EXPERT_REVIEW_ENABLED` / `EMILY_EXPERT_MODEL` 不再被读取；若外部 `.env` 仍残留这两个变量，会被忽略（不报错）。
