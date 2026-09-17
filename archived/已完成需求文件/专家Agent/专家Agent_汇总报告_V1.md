# 专家Agent — 执行与测试汇总报告

> **日期**：2026-08-12
> **依据文档**：[专家Agent_PRD_V1.md](需求/专家Agent_PRD_V1.md) / [专家Agent_计划_V1.md](需求/专家Agent_计划_V1.md)
> **关联报告**：[专家Agent_测试报告_V1.md](需求/专家Agent_测试报告_V1.md)

---

## 一、模块概览

按 `专家Agent_计划_V1.md` 中 M1-M5 五个模块顺序实施，完成数据层、手册加载、意图接入、评审节点、管理工具的全部开发。

---

## 二、交付清单

### 新建文件（4个）

| 文件 | 模块 | 说明 |
|------|------|------|
| `emily_core/infrastructure/database/models.py` (+48行) | M1 | `Expert` + `ExpertApproval` ORM 模型（2张新表） |
| `emily_core/repositories/expert_repo.py` (+180行) | M1 | `ExpertRepository` + `ExpertApprovalRepository`（全 sync 静态方法） |
| `emily_core/services/expert_manual_loader.py` (+120行) | M2 | `ExpertManualLoader` 多级 fallback 加载器 |
| `emily_core/tools/expert_manage_tool.py` (+280行) | M5 | 4 个 handler + 4 个 schema 常量 |

### 修改文件（7个）

| 文件 | 改动 |
|------|------|
| `emily_core/config.py` | `expert_review_enabled` / `expert_model` / `llm_expert_max_tokens` |
| `emily_core/workitem/workitem.py` | `expert_id` / `expert_required` / `expert_review_result` |
| `emily_core/session/session_agent.py` | `_match_expert()` — SOP 绑定专家时自动设置标记 |
| `emily_core/workitem/langgraph_engine/nodes.py` | `make_expert_review` / `build_expert_prompt` / `_normalize_expert_result` |
| `emily_core/workitem/langgraph_engine/graph.py` | `expert_review` 节点、`route_after_routing` 路由、条件边 |
| `emily_core/tools/registry.py` | 注册 4 个专家管理工具（create/approve/toggle/query） |
| `emily_core/infrastructure/tools_consistency.py` | `REGISTERED_TOOLS` / `TOOL_META_MAP` / `TOOL_SCHEMA_MAP` 新增映射 |

### 测试文档（3份，保留为专家正式配置）

| 文件 | 说明 |
|------|------|
| `emily-data/files/Expert Work Manual/EXP-001-苗木使用审核职能手册.md` | 专家职能手册，10章量化审核标准 |
| `emily-data/files/Expert Work Manual/EXP-001-苗木审核任务手册.md` | 评审任务手册，8维度权重评分 |
| `emily-data/files/Expert Work Manual/待审-星湖湿地公园植物设计说明.md` | 待审测试文件，含8个预设桩点 |

---

## 三、验证结果

### 构建验证

| 检查项 | 结果 |
|--------|------|
| 语法检查（11个文件 `py_compile`） | ✅ 全部通过 |
| 工具一致性检查（40 tools, 39 schemas） | ✅ 0 fatal error |
| 容器启动（`docker compose restart`） | ✅ 正常，health ok |

### 功能测试（11/11 全部执行）

| 编号 | 测试用例 | 结果 |
|------|---------|------|
| TC01 | 审批专家（PENDING→ACTIVE） | ✅ PASS |
| TC02 | 查询专家列表 | ✅ PASS |
| TC03 | 停用专家（ACTIVE→DISABLED） | ✅ PASS |
| TC04 | 启用专家（DISABLED→ACTIVE） | ✅ PASS |
| TC05 | 创建专家权限（L4） | ⚠️ PASS（原始Bug，已修复） |
| TC06 | 专家评审全链路（路由+加载+LLM） | ✅ PASS |
| TC07 | 专家评审含文件（8桩点验证） | ⚠️ PASS（原始Bug，已修复） |
| TC08 | 评审无文件 fallback | ✅ PASS |
| TC09 | JSON 截断 fallback | ✅ PASS |
| TC10 | 审批审计日志 | ✅ PASS |
| TC11 | Docker 运行时无 ERROR | ✅ PASS |

### 评审质量验证

专家评审精准命中全部 **8个预设桩点**，评分 48/100：

| # | 桩点 | 维度 | 得分 |
|---|------|------|------|
| 1 | D区乔木密度 12.19棵/100m² 超标 | 乔木密度合规 | 8/20 |
| 2 | B区草灌比 78:13:9 异常 | 草灌面积比 | 8/15 |
| 3 | C区滨水草坪占比 50% 超限 | （同上维度） | — |
| 4 | 乔木换土深度 0.8m 不达标 | 换土与种植穴 | 0/15 |
| 5 | 土壤理化参数完全缺失 | 土壤理化参数 | 0/15 |
| 6 | 银杏株距 3.0m 低于标准 | 种植间距 | 8/15 |
| 7 | 紫薇地径 2cm 低于标准 | 苗木规格 | 8/10 |
| 8 | 成活率保证条款缺失 | 成活率保证 | 0/5 |

---

## 四、发现的 Bug 与修复

| # | 严重程度 | 问题 | 修复 |
|---|---------|------|------|
| B1 | 🟡 中 | `_check_management_unit` 在 tool_node 上下文 perm_dict 缺失 level 字段 | 增加 DB 兜底：`UserRepository.get_by_id()` → `can_access(level, 4)` |
| B2 | 🟢 低 | 复杂评审 JSON 输出截断（8+ 问题时 token 不足） | `llm_expert_max_tokens: 8192 → 16384` |

**修复验证**：
- B1：李景利(L4) 成功创建专家 EXP-002 ✅
- B2：容器内 `Config().llm_expert_max_tokens` = 16384 ✅
- 两个文件语法编译通过 ✅

---

## 五、遗留事项（全部已修复） ✅

| 事项 | 优先级 | 修复方案 | 验证 |
|------|--------|---------|------|
| #1 创建专家评审专用 SOP | 高 | 创建 `SOP-012-SYS-expert_review.md`，含完整 intent 触发语义 + 否定条件 + 示例对话 | intent 路由 `sop=SOP-012-SYS-expert_review conf=high` ✅ |
| #2 `tools_consistency.py` import 错误 | 低 | `infrastructure/logging/` 包遮蔽 stdlib `logging` → 修复 sys.path 顺序 | `--check` exit code 0 ✅ |
| #3 emy-test 跨 OS 文件传递 | 低 | `--file` 参数自动复制到 `emily-data/attachments/` volume 目录 + 添加 `file_path` 容器内路径 | `[附件] x.md → /app/attachments/x.md` ✅ |
| B2 补充修复 max_tokens 未生效 | 高 | `chat_json()` 增加 `max_tokens` 参数 → `make_expert_review` 传入 `llm_expert_max_tokens=16384` | score=48 完整输出 ✅ |

---

## 六、项目状态

**全部模块（M1-M5）实现完成，功能验证通过，2 个原始 Bug + 3 个遗留事项 + 1 个补充修复全部完成。专家评审管线全链路贯通：**

```
用户消息 → intent路由(SOP-012-SYS conf=high) → 专家匹配(EXP-001) 
→ route_after_routing → expert_review → 手册加载 + 文件加载 
→ LLM评审(max_tokens=16384) → score=48 → 自然语言回复
```
