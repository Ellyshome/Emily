# 系统自我描述 — AI 执行计划（Plan File）

## Context

Emily 的 SessionAgent 对自身系统结构（数据库/文件/权限）缺乏认知——它知道自己能访问什么，但不知道为什么、怎么用、有什么关联。当前 `fetch_visible_schema.py` 仅硬编码 10 张表名映射，无字段信息；`fetch_visible_files.py` 仅提供数量统计；权限注入仅一行标签。

需求文档 `完成的需求/元认知/系统自我描述-需求.md` 提出了"系统描述书"概念：作为元认知模块的第三类知识（与规则书、世界书并列），通过 ORM 反射 + PublicFieldRegistry 采集数据库/文件/权限三域知识，生成 ~400 tokens 的结构化描述注入 prompt。

## 深度探查发现（关键修正）

| # | 需求假设 | 实际代码 | 修正方案 |
|---|---------|---------|---------|
| 1 | File 表有 `original_name` 字段 | 实际是 `filename` (String(500)) | 构建器使用 `filename` |
| 2 | File 表有 `access_level` 字段 | 实际是 `confidentiality` (Integer, 0-3) | 权限描述用 `confidentiality` |
| 3 | File 表有 `category` 枚举字段 | 实际是 `file_category` (String(50), 存 FileCategory 值) | 使用 `file_category` |
| 4 | FileCategory 有 5 类（施工图/报告/规范/变更/其他） | 实际 7 类：PROJECT_LICENSE/CONTRACT/WORK_RECORD/PHASE_DELIVERABLE/PROCESS_DOC/MANAGEMENT_SPEC/OTHER | 按 7 类构建 |
| 5 | db_perms 覆盖 10 张表 | 实际只返回 5 张表：project/event/task/meeting/financial | 以 db_perms 实际返回为准 |
| 6 | 文件访问规则是 `access_level ≤ info_level` | 实际用 `confidentiality` 字段 (0=公开,1=内部,2=机密,3=绝密) | 使用 confidentiality 描述 |

## 模块拆分与依赖图

```
SD1(数据模型) ──→ SD2(构建器) ──→ SD4(更新服务) ──→ SD6(调度器集成)
                  │                                  ──→ SD7(Core集成)
                  ──→ SD3(偏差检测) ──→ SD4
                                        ──→ SD8(CLI脚本)
SD1 ──→ SD5(Session集成) ──→ SD7
```

| 模块 | 交付文件 | 核心 |
|------|---------|------|
| SD1 | `models.py` 追加 + `system_description_repo.py` 新建 | SystemDescription ORM + Repo |
| SD2 | `system_description_builder.py` 新建 | D1/D2/D3 三域构建 |
| SD3 | `schema_drift_detector.py` 新建 | 三域 hash 偏差检测 |
| SD4 | `system_description_service.py` 新建 | 检测→重建→存储 |
| SD5 | `fetch_system_description.py` 新建 + `session_data_fetcher.py` / `session_context.py` / `session.md` 修改 | Session 注入 |
| SD6 | `system_description_update.py` 新建 + `__init__.py` 注册 | 周级调度 |
| SD7 | `__init__.py` 修改 + `meta_cognition.py` 路由新建 + `server.py` 修改 | Core + API |
| SD8 | `build_system_description.py` 新建 | CLI 脚本 |

## 可复用组件与参照模式

| 层 | 参照源 | 要模仿的要点 |
|----|--------|-------------|
| ORM 模型 | `models.py` ProjectWorldBook | UUID PK + _new_uuid + _utc_now + Column 风格 + Index |
| Repository | `world_book_repo.py` ProjectWorldBookRepo | @staticmethod + get_session() + create/get/update/list |
| Builder | `world_book_builder.py` ProjectWorldBookBuilder | build() → dict + _build_* 子方法 + _format_content_text() |
| DriftDetector | `cognition_drift_detector.py` | detect() → dict + 分层检测 + stale 信号 |
| Service | `world_book_service.py` | update_stale() + asyncio.to_thread 包裹 sync |
| Scheduler Handler | `world_book_update.py` WorldBookUpdateHandler | SchedulerJobHandler 子类 + execute() → JobResult |
| Fetcher | `fetch_visible_schema.py` | fetch(perms) → str + 独立 CLI main() |
| CLI 脚本 | `build_world_book.py` | sys.path + _init_db + 核心函数 + argparse --dry-run |
| API 路由 | `evolution.py` | APIRouter + prefix + POST/GET endpoints |

## 现有模块改动清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `emily-core/emily_core/infrastructure/database/models.py` | 追加 | SystemDescription ORM 类 |
| `emily-core/emily_core/session/session_data_fetcher.py` | 修改 | 新增 _sub_fetch_system_description() |
| `emily-core/emily_core/session/session_context.py` | 修改 | 新增 system_description 字段 + prompt 变量 + refresh 字段 |
| `emily-data/prompts/session.md` | 修改 | 新增 {system_description} 段 |
| `emily-core/emily_core/__init__.py` | 修改 | _init_meta_cognition() 增加 system_description |
| `emily-core/api/server.py` | 修改 | 注册 meta_cognition 路由 |

## 关键设计决策

1. **Hash 策略**：序列化 ORM 元数据（sorted table/column info）为规范字符串后 SHA-256，不 hash 源文件（避免注释/空格变更误触发）
2. **注入时裁剪（Strategy B）**：DB 存全量 content_json，fetcher 按 db_perms 过滤后格式化文本返回
3. **三层 fallback 采集字段描述**：PublicFieldRegistry → Column.comment → 空字符串（LLM 兜底留作后续优化）
4. **{visible_schema} 保留但降级**：系统描述为空时回退到现有浅层描述

## 验证方案

1. SD1: `docker exec emily-postgres psql -U emily -d emily -c "\d system_descriptions"`
2. SD2: `uv run python scripts/build_system_description.py --dry-run`
3. SD3: 修改 models.py 加测试表 → `--check-only` 检测偏差 → 恢复
4. SD5: emy-test 对话验证 "数据库里有哪些表？" / "权限怎么分级的？"
5. SD7: `docker logs emily-core 2>&1 | grep system_description`

## 产出文件

实施计划将输出到: `完成的需求/元认知/系统自我描述_计划_V1.md`
