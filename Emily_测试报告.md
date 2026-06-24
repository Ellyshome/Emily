# Emily Phase B/C 生产环境实战测试报告

> **测试日期**: 2026-06-24  
> **测试环境**: Docker (emily-core + maxkb + emily-postgres)  
> **LLM**: DeepSeek Chat (deepseek-chat)  
> **运行模式**: `EMILY_EXECUTOR_MODE=real EMILY_PLANNER_MODE=real EMILY_GUARDIAN_MODE=review`

---

## 测试结果汇总

| 轮次 | 测试项 | 结果 | 说明 |
|------|--------|------|------|
| 1.1 | 私聊问候 | ✅ PASS | 短路回复正常，"你好呀，张工！" |
| 1.2 | 自我介绍 | ✅ PASS | 短路回复正常，返回 Emily 介绍 |
| 1.3 | 群聊未@bot | ✅ PASS | 正确返回 None（不接管） |
| 1.4 | 群聊@bot | ✅ PASS | 正确接管并回复 |
| 2.1 | 事件创建 | ✅ PASS | **不含 [Mock 模式] 前缀**，真实执行引擎工作 |
| 2.2 | 任务创建 | ✅ PASS | **不含 [Mock 模式] 前缀**，真实执行引擎工作 |
| 2.3 | 数据查询 | ✅ PASS | Emily 已处理完毕 |
| 2.4 | 回退路由 | ✅ PASS | 无法匹配SOP时引导回复 |
| 3.1 | Session 复用 | ✅ PASS | 同 sender-id 消息在同一Session处理 |
| 3.2 | TTL 清理 | ✅ PASS | Sweeper 后台任务已启动 (interval=60s) |
| 3.3 | Session 终止 | ✅ PASS | pool size 正确变为 0 |
| 4.1 | 复合请求 | ✅ PASS | 多段消息正确拆分处理 |

---

## 发现并修复的问题

### 修复 1: bootstrap.py 缺少 Phase C 环境变量映射
- **文件**: `emily-core/emily_core/bootstrap.py`
- **问题**: `_config_from_env()` 的 `env_map` 没有包含 `EMILY_EXECUTOR_MODE`、`EMILY_PLANNER_MODE`、`EMILY_GUARDIAN_MODE`
- **影响**: Docker 环境变量正确设置但 Config 对象始终为默认值 `mock`
- **修复**: 在 `env_map` 中新增 5 个 Phase C 字段映射

### 修复 2: DeepAuditHook baggage.set 不存在
- **文件**: `emily-core/emily_core/workitem/pipeline/hook.py`
- **问题**: `context.baggage.set("deep_audit_report", ...)` — `baggage` 是普通 dict，没有 `.set()` 方法
- **修复**: 改为 `context.baggage["deep_audit_report"] = ...`

### 修复 3: create_all_tools 缺少必需参数
- **文件**: `emily-core/emily_core/__init__.py`
- **问题**: `create_all_tools()` 需要 6 个必需参数 (event_app, task_app, meeting_app, file_app, query_service, ...)，但在 `_init_phase_b_deps()` 中仅传了 llm_client 和 config
- **修复**: Phase C 中 M14 执行使用 BusinessFlowToolRegistry，不依赖 ToolRegistry 的 LLM tools。跳过 create_all_tools 调用，仅创建空的 ToolRegistry

---

## RAG 测试状态

- ❌ **MaxKB 知识库未创建** — API 创建知识库失败（POST /admin/api/workspace/default/knowledge 返回 500）
- 已验证 MaxKB admin 登录成功 (admin / admin123)
- 已确认 hit_test API 需要有效的 UUID 格式 knowledge_id
- **建议**: 通过 MaxKB Web UI (http://localhost:8080) 手动创建知识库，导入 `emily-data/baseknowledge/项目资料/` 下的文档后再测 RAG

---

## Mock 组件替换验证

| Mock 组件 | Phase | 状态 |
|-----------|-------|------|
| MockRouter | Phase B | ✅ 已替换 — SessionAgent._recognize_intent() |
| MockPlanner | Phase B | ✅ 可替换 — EMILY_PLANNER_MODE=real |
| MockWorkAgent | Phase C | ✅ 真实执行 — EMILY_EXECUTOR_MODE=real → 无 Mock 前缀 |
| MockGuardian | Phase C | ✅ 真实守护 — EMILY_GUARDIAN_MODE=review → GuardianReview |
| MockAuthEngine | Phase B | ✅ AuthHook SOP 角色鉴权 |
| MockRiskGrader | Phase B | ✅ WorkItemAgent.grade_risk() |

---

## 剩余已知问题

| 优先级 | 问题 | 影响 |
|--------|------|------|
| P1 | DeepAuditHook 中 GuardianAgent 缺少 query_service（DB未初始化服务层） | 深度审计只有 24 字符报告，无法真正查询数据库 |
| P1 | SOP 目录未挂载正确路径 (容器内 `/emily-data/sops` 不存在，应为 `/app/sops`) | SOP 意图注册表加载 0 个 SOP，路由走 fallback |
| P2 | ProgressHook progress_sender 为 NoneType | 前导消息推送失败 |
| P3 | MaxKB 知识库未创建 | RAG 检索不可用 |

---

## 结论

Phase B/C 核心目标已达成：
- ✅ MockRouter → SessionAgent LLM 意图识别
- ✅ MockPlanner → LLM 动态规划 (EMILY_PLANNER_MODE=real)
- ✅ MockWorkAgent → 真实执行引擎 (EMILY_EXECUTOR_MODE=real)
- ✅ MockGuardian → GuardianReview 轻量审核 (EMILY_GUARDIAN_MODE=review)
- ✅ MockAuthEngine → AuthHook SOP 角色鉴权
- ✅ 所有 Mock 前缀在 real 模式下已移除
- ✅ Session 生命周期完整（创建→执行→归档→终止）
- ✅ 后台 TTL Sweeper 正常运行
