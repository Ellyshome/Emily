# collect_session_data.py 测试记录

> **日期**: 2026-07-02
> **测试用户**: `a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d` (张建国, L4 建设主管)
> **环境**: 宿主机直连 Docker emily-postgres (localhost:25432)

---

## 1. 测试命令

```powershell
# 前置：确保 Docker 容器运行
docker compose -f docker-compose-napcat.yml ps

# 清除容器内 pycache（bind-mount 不自动刷新）
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +

# 运行脚本（注意：宿主机端口 25432，密码 emily_secret_2026）
export PYTHONIOENCODING=utf-8
export EMILY_DATABASE_URL="postgresql://emily:emily_secret_2026@localhost:25432/emily"
uv run python scripts/collect_session_data.py a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d
```

---

## 2. 修复前结果（real_name 列缺失导致权限静默降级）

```
=== collect_session_data('a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d') ===

--- ERRORS (获取过程中异常) ---
  (无异常)                          ← ⚠ 误导：实际 PermissionService 已 fail-open 降级

--- SessionSnapshot ---
  conversation_id           = debug-conv-001
  user_id                   = a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d
  user_name                 = 张建国                          ✅
  user_position             = Project Manager                 ✅
  created_at                = 2026-07-02T09:22:49.483707+00:00
  project_name              = Tianjin Eco-City 26# Project   ✅
  project_type              = 房屋建筑                         ✅
  project_status            = 进行中                           ✅
  permissions               = {'permission_level': 1, 'company_id': '', 'company_type': '',
                               'company_name': '', 'department': '', ...}   ❌ 全部降级
  user_memory               = 【语言风格要求】...               ✅
  conversation_summary      = 【对话摘要 — 张建国...】         ✅

--- Prompt Variables ---
  {project_name}                      = Tianjin Eco-City 26# Project  ✅
  {project_type}                      = 房屋建筑                       ✅
  {project_status}                    = 进行中                         ✅
  {user_name}                         = 张建国                         ✅
  {user_company}                      =                                ❌ 应为"意景园林景观设计公司"
  {user_company_type}                 =                                ❌ 应为"设计单位"
  {user_department}                   =                                ❌ 应为"设计一部"
  {user_position}                     = Project Manager                ✅
  {user_permission_level}             = 访客(L1)                       ❌ 应为"建设主管(L4)"
  {current_node_ids}                  =                                ✅ (function_scope={} 为正常空值)
  {recent_turns}                      = [2026-06-25T17:51] 用户: ...   ✅
  {user_longterm_memory}              = 【语言风格要求】...             ✅
  {conversation_summary}              = 【对话摘要 — 张建国...】       ✅

全部获取成功（哨兵值: 0）。                  ← ⚠ 误导：实际 4 项数据错误
```

**根因**：ORM 模型 `User.real_name` 列在 DB 中不存在 → `PermissionService.build_permission_snapshot()` 查全量 User 行触发 `UndefinedColumn` → 内部 fail-open 机制静默降级为 L1 访客快照 → 公司/部门/权限等字段全部为空。

---

## 3. 修复内容

从 ORM 模型移除 `real_name` 列，所有 `real_name` 读取转移到 `username` 字段：

| 文件 | 改动 |
|------|------|
| `emily-core/emily_core/infrastructure/database/models.py` | 移除 `real_name = Column(String(100))` |
| `emily-core/emily_core/repositories/user_repo.py` | 移除构造参数 `real_name=`；`find_by_name` 去掉 `real_name` 过滤，仅查 `username` |
| `emily-core/emily_core/application/_user_utils.py` | `getattr(u, "real_name", "") or u.username` → `u.username` |
| `emily-core/emily_core/tools/memory_tool.py` | 同上 |
| `emily-core/emily_core/tools/pending_issue_tool.py` | 同上 |
| `emily-core/emily_core/services/query_service.py` | `u.real_name` → `u.username` |
| `docs/数据库设计.md` | 移除 `real_name` 行，`username` 描述改为"兼真实姓名" |

修复后重启容器：
```powershell
docker compose -f docker-compose-napcat.yml restart emily-core
```

---

## 4. 修复后结果

```
=== collect_session_data('a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d') ===

--- ERRORS (获取过程中异常) ---
  (无异常)

--- SessionSnapshot ---
  conversation_id           = debug-conv-001
  user_id                   = a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d
  user_name                 = 张建国                          ✅
  user_position             = Project Manager                 ✅
  created_at                = 2026-07-02T09:31:56.256335+00:00
  project_name              = Tianjin Eco-City 26# Project   ✅
  project_type              = 房屋建筑                         ✅
  project_status            = 进行中                           ✅
  permissions               = {'permission_level': 4, 'company_id': '46eabd79-...',
                               'company_type': '设计单位', 'company_name': '意景园林景观设计公司',
                               'department': '设计一部', ...}   ✅
  user_memory               = 【语言风格要求】...               ✅
  conversation_summary      = 【对话摘要 — 张建国...】         ✅

--- Prompt Variables ---
  {project_name}                      = Tianjin Eco-City 26# Project  ✅
  {project_type}                      = 房屋建筑                       ✅
  {project_status}                    = 进行中                         ✅
  {user_name}                         = 张建国                         ✅
  {user_company}                      = 意景园林景观设计公司            ✅
  {user_company_type}                 = 设计单位                       ✅
  {user_department}                   = 设计一部                       ✅
  {user_position}                     = Project Manager                ✅
  {user_permission_level}             = 建设主管(L4)                   ✅
  {current_node_ids}                  =                                ✅ (正常空值)
  {recent_turns}                      = [2026-06-25T17:51] 用户: ...   ✅
  {user_longterm_memory}              = 【语言风格要求】...             ✅
  {conversation_summary}              = 【对话摘要 — 张建国...】       ✅

全部获取成功（哨兵值: 0）。
```

---

## 5. 修复前后对比

| 字段 | 修复前 | 修复后 | DB 正确值 |
|------|--------|--------|-----------|
| `permission_level` | 1 (访客) ❌ | **4 (建设主管)** ✅ | 4 |
| `company_name` | (空) ❌ | **意景园林景观设计公司** ✅ | 意景园林景观设计公司 |
| `company_type` | (空) ❌ | **设计单位** ✅ | 设计单位 |
| `department` | (空) ❌ | **设计一部** ✅ | 设计一部 |
| `user_permission_level` | 访客(L1) ❌ | **建设主管(L4)** ✅ | — |
| `user_company` | (空) ❌ | **意景园林景观设计公司** ✅ | — |
| `user_company_type` | (空) ❌ | **设计单位** ✅ | — |
| `user_department` | (空) ❌ | **设计一部** ✅ | — |

**0 哨兵值，0 异常，16/16 prompt 变量正确。**

---

## 6. 遗留事项

| 优先级 | 事项 | 状态 |
|--------|------|------|
| P2 | `doc_visible_set` 未实现（`_sub_fetch_doc_visible_set` 返回空 set） | 已知，需求文档标记 Phase B 后再做 |
| P2 | `User_responsibilities` 未实现（需从权限推导职责描述） | 已知 |
| P2 | `conversation_summary` 当前从 DB 字段读取，非 LLM 压缩生成 | 半实现 |
| P2 | PermissionService fail-open 降级时脚本未报告异常（建议添加降级检测） | 建议改进 |
