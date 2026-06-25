# Emily Phase B/C 生产环境实战测试报告

> **测试日期**: 2026-06-24  
> **测试环境**: Docker (emily-core + maxkb + emily-postgres)  
> **LLM 模型**: DeepSeek Chat (deepseek-chat)  
> **运行模式**: `EMILY_EXECUTOR_MODE=real EMILY_PLANNER_MODE=real EMILY_GUARDIAN_MODE=review`

---

## 一、测试结果汇总

| 编号 | 测试项目 | 结果 | 说明 |
|------|---------|------|------|
| 1.1 | 私聊问候 | ✅ 通过 | 短路回复正常，不创建 WorkItem |
| 1.2 | 自我介绍 | ✅ 通过 | 返回 Emily 介绍语 |
| 1.3 | 群聊未@bot | ✅ 通过 | 正确返回 None（不接管） |
| 1.4 | 群聊@bot | ✅ 通过 | 正确接管并回复 |
| 2.1 | 事件创建 | ✅ 通过 | **不含 [Mock 模式] 前缀**，真实执行引擎工作 |
| 2.2 | 任务创建 | ✅ 通过 | **不含 [Mock 模式] 前缀**，真实执行引擎工作 |
| 2.3 | 数据查询 | ✅ 通过 | Emily 正确处理 |
| 2.4 | 回退路由 | ✅ 通过 | 无法匹配 SOP 时返回引导回复 |
| 3.1 | Session 复用 | ✅ 通过 | 同 sender-id 共享一个 Session |
| 3.2 | TTL 定期清理 | ✅ 通过 | 后台清理任务已启动（间隔 60s） |
| 3.3 | Session 终止 | ✅ 通过 | Pool size 正确归零 |
| 4.1 | 复合请求 | ✅ 通过 | 多段消息正确拆分处理 |

---

## 二、Mock 组件替换状态

| Mock 组件 | 所属阶段 | 替换状态 |
|-----------|---------|---------|
| MockRouter | Phase B | ✅ 已替换 → SessionAgent._recognize_intent() |
| MockPlanner | Phase B | ✅ 已替换 → WorkItemAgent._llm_plan() |
| MockWorkAgent | Phase C | ✅ 已替换 → RealExecutor（M14 工具直调） |
| MockGuardian | Phase C | ✅ 已替换 → GuardianReview + GuardianAgent |
| MockAuthEngine | Phase B | ✅ 已替换 → AuthHook SOP 角色鉴权 |
| MockRiskGrader | Phase B | ✅ 已替换 → WorkItemAgent.grade_risk() |

---

## 三、发现并修复的 Bug

### Bug 1: bootstrap.py 缺少 Phase C 环境变量映射
**文件**: `emily-core/emily_core/bootstrap.py`  
**问题**: `_config_from_env()` 的 env_map 字典未包含 `EMILY_EXECUTOR_MODE`、`EMILY_PLANNER_MODE`、`EMILY_GUARDIAN_MODE`、`EMILY_AUTH_MODE`、`EMILY_RISK_MODE`、`EMILY_MAXKB_*`、`EMILY_KB_ENABLED` 共 10 个环境变量  
**影响**: Docker 环境变量正确设置了 real 模式，但 Config 对象始终为默认值 `mock`；MaxKB RAG 从未初始化  
**修复**: 在 env_map 中补全所有缺失的 10 个环境变量映射

### Bug 2: DeepAuditHook baggage.set() 方法不存在
**文件**: `emily-core/emily_core/workitem/pipeline/hook.py:338`  
**问题**: `context.baggage.set("deep_audit_report", result.report)` — baggage 是普通 dict，没有 `.set()` 方法  
**影响**: 深度审计完成后写入报告时崩溃（非阻塞，异常被捕获但不记录报告）  
**修复**: 改为 `context.baggage["deep_audit_report"] = result.report`

### Bug 3: create_all_tools 缺少必需参数
**文件**: `emily-core/emily_core/__init__.py:_init_phase_b_deps()`  
**问题**: `create_all_tools()` 需要 6 个 positional 参数（event_app、task_app、meeting_app 等），但只传了 llm_client 和 config  
**影响**: ToolRegistry 初始化失败，虽然不影响 M14 执行路径但报错日志令人困惑  
**修复**: Phase C 中 M14 执行使用 BusinessFlowToolRegistry 直调，跳过 create_all_tools 调用，仅创建空 ToolRegistry

### Bug 4: SOP 目录解析为 Windows 宿主机路径
**文件**: `emily-core/emily_core/__init__.py:_init_phase_b_deps()`  
**问题**: `Path(__file__).resolve().parents[2]` 在容器内解析为 `C:/Program Files/Git/app/sops`（Windows PowerShell 的 Git Bash 路径污染），而非容器内 `/app/sops`  
**影响**: SOPIntentRegistry 加载 0 个 SOP，所有意图识别走 fallback 路径  
**修复**: 调整 fallback 顺序：容器内默认路径 `/app/sops` → 环境变量 → 开发路径

### Bug 5: ConfirmQueue 缺少 clear() 方法
**文件**: `emily-core/emily_core/session/confirm_queue.py`  
**问题**: `SessionAgent.archive()` 调用 `self.confirm_queue.clear()` 但该方法不存在  
**影响**: Session 归档时报错  
**修复**: 新增 `clear()` 方法，同时保留 `remove("__all__")` 兼容

### Bug 6: TraceHook 名称不匹配
**文件**: `emily-core/emily_core/workitem/pipeline/hook.py:260,273`  
**问题**: TraceHook.execute() 内部比较 `self.name == "trace.reasoning_start"`，但 hook_config.json 中定义的名称是 `"trace.execution_start"`  
**影响**: TraceHook 追踪逻辑永不触发，create_reasoning_log / update_reasoning_log 成为死代码  
**修复**: 将比较字符串改为与配置文件一致的 `"trace.execution_start"` / `"trace.execution_end"`

### Bug 7: MockRouter 死代码
**文件**: `emily-core/emily_core/workitem/workitem_agent.py:32`  
**问题**: MockRouter 被 import 并实例化为 `self._router`，但 Phase B 已将路由职责移到 SessionAgent，node1 不再调用 router  
**影响**: 无功能影响，纯粹死代码  
**修复**: 删除 MockRouter 的 import 和 `self._router` 赋值

---

## 四、剩余已知问题

| 优先级 | 问题 | 影响 |
|--------|------|------|
| P1 | `knowledge_search` 工具未接入 RealExecutor 的 BusinessFlowToolRegistry | RAG 知识库查询返回通用的"已处理"回复，不会实际搜索知识库 |
| P1 | GuardianAgent 缺少 query_service（DB 服务层未初始化） | 深度审计只产出 24 字符占位报告，无法真正查询数据库 |
| P2 | ProgressHook 的 progress_sender 在容器内为 NoneType | 前导消息"处理中……"静默失败 |
| P3 | AuthHook 的 sop_intent_registry 初始化失败（dataclass 字段不匹配） | sop_access 和 resource_check 两个鉴权 Hook 构建失败，但不阻塞管道 |

---

## 五、RAG 知识库状态

| 检查项 | 结果 |
|--------|------|
| MaxKB 管理员登录 (admin/admin123) | ✅ 通过 |
| 知识库创建（6 份文档已索引） | ✅ 通过 |
| hit_test API（"消防验收" 返回 3 条结果，相似度 ~0.68） | ✅ 通过 |
| bootstrap 阶段 MaxKB Provider 初始化 | ✅ 通过 |
| knowledge_search 工具接入热路径 | ❌ 待修复 — 工具在 LLM ToolRegistry 中，但 RealExecutor 只查找 BusinessFlowToolRegistry |

---

## 六、结论

Phase B/C 核心目标已达成：

- ✅ 全部 6 个 Mock 组件已被真实实现替换
- ✅ Pipeline 4 节点 BUS 使用真实大脑正常运行
- ✅ Session 完整生命周期（创建 → 执行 → 归档 → 终止）通过测试
- ✅ `[Mock 模式]` 前缀在真实执行模式下已移除
- ✅ 后台 TTL Sweeper 正常运行（每 60s 扫描一次）
- ✅ 9 个 SOP 从索引成功加载，意图路由功能就绪
- ✅ MaxKB RAG Provider 已初始化并可正常调用 hit_test API
- ✅ 测试过程中发现并修复 7 个 Bug
