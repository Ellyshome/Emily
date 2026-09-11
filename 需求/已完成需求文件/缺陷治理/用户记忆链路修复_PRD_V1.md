# PRD：修复用户长期记忆链路（"写而不读" + "读有写无"双断线）

> **来源**：`需求/Pi_Agent对比分析报告_业务设计篇.md` §6-3（341 行）
> **级别**：🔴 死代码/断线（功能空转）｜**优先级**：P2
> **日期**：2026-09-11 ｜ **状态**：待评审

---

## 1. 问题核实（当前代码复验）

**结论：缺陷存在，且比报告描述的更完整——是两条互不相通的半截链路。**

### 1.1 文件通道：写了不读

| 环节 | 证据 | 状态 |
|---|---|---|
| 写入工具 | `tools/memory_tool.py:21` `create_memory_tool` 注册为 `write_user_memory`；`tools/registry.py:327-329` 已进注册表；`infrastructure/tools_consistency.py:83` 在 REGISTERED_TOOLS 清单 | ✅ 通 |
| 写入服务 | `services/user_memory_service.py:92` `save_memory` → 追加写 `emily-data/user_memory/<用户名>-长期记忆.md` | ✅ 通 |
| 读取函数 | `user_memory_service.py:171` `load_memory_context`（及 `:142` `load_memory`）**全仓零调用者** | ❌ 断 |
| 实际数据 | `emily-data/user_memory/` 目录仅有 `.gitkeep`，尚无记忆文件（工具虽注册，产出无人消费，也侧面说明从未端到端验证过） | ❌ 空 |

### 1.2 DB 通道：读了没得读

| 环节 | 证据 | 状态 |
|---|---|---|
| DB 字段 | `infrastructure/database/models.py:94` `users.long_term_memory = Column(String, default="")` | ✅ 有 |
| 读取方 | `session/session_data_fetcher.py:176` `long_term_memory = user.long_term_memory or ""` → `session_context.py:161` 装入快照 → `session_context.py:428` 渲染进提示词变量 `{user_memory}` | ✅ 通 |
| 写入方 | **全仓零写入者**（grep `long_term_memory` 仅定义与读取路径） | ❌ 断 |

### 1.3 净效果

`{user_memory}` 恒为空串；Agent 写下的记忆只进文件、永不进提示词。`user_memory_service.py` 模块头自称"每次新对话开始时，加载用户记忆作为 system prompt 上下文"——该承诺当前不成立。

## 2. 问题分析（原因）

1. **M8c 半截交付**：记忆功能分期实现，写入侧（工具 + 服务 + 注册表 + 一致性校验）完整落地；读取侧只建了 `load_memory_context` 方法，从未接到 session 数据装配链上。
2. **两套存储并存且未收敛**：设计上可能先有 DB 列（读取方按列实现），后改文件方案（写入方按文件实现），切换时没有删除旧列读路径，也没有让新文件方案接管 `{user_memory}`——结果两边各剩一半。
3. **无端到端测试**：`write_user_memory` 在工具一致性校验（tools_consistency.py）中只验注册与 schema，不验"写了之后下次会话可见"，断线无法被测试网拦住。

## 3. Bug 复现方法（兼作修复后自验证）

### 3.1 静态证据（30 秒）

```bash
cd emily-core/emily_core
grep -rn "load_memory_context" --include=*.py .        # 当前：仅 user_memory_service.py 定义处，零调用者
grep -rn "long_term_memory" --include=*.py .           # 当前：仅 models.py 定义 + session 读取路径，零写入方
ls ../../emily-data/user_memory/                       # 当前：仅 .gitkeep（功能从未端到端跑通过）
```

### 3.2 运行时级复现（脚本，无 LLM 依赖，需 DB 可达）

新建 `scripts/repro_user_memory_loop.py`——在**一次进程内**先后走"写入通道"与"提示词数据源通道"，两通道本应连通却各走各的：

```python
"""repro_user_memory_loop.py — 复现用户长期记忆"写而不读"缺陷。

用法: python scripts/repro_user_memory_loop.py <user_id>   # user_id 需为 DB 中已存在用户
退出码: 0 = 写后可见（修复后预期）; 1 = 缺陷存在（两通道断开）; 2 = 参数/环境错误。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "emily-core"))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: repro_user_memory_loop.py <user_id>")
        return 2

    from emily_core.services.user_memory_service import UserMemoryService
    from emily_core.session.session_data_fetcher import SessionDataFetcher

    user_id = sys.argv[1]
    # memory_dir 必须与 EmilyCore._init_m8c_services 注入的目录一致（emily-data/user_memory）。
    # 注意：不传 memory_dir 时服务内部默认值是 <仓库根>/memory，与生产目录不同。
    svc = UserMemoryService(memory_dir="emily-data/user_memory", enabled=True)

    title = svc.save_memory(user_id, "复现记忆条目：每周一 9 点提交周报", title="复现条目")
    if not title:
        print("UNEXPECTED: save_memory 写入失败，请检查目录权限")
        return 2
    file_memory = svc.load_memory_context(user_id)
    print(f"[1] 文件通道（load_memory_context，功能本身完好）: {len(file_memory)} 字符")

    result = SessionDataFetcher.fetch(user_id, core=None)
    injected = (result.get("session_snapshot") or {}).get("long_term_memory", "")
    print(f"[2] 提示词数据源（SessionDataFetcher → {{user_memory}}）: {len(injected)} 字符, {injected[:60]!r}")

    if file_memory and not injected:
        print("REPRO OK: 缺陷复现 — 记忆已写入文件，但 {user_memory} 的数据源 users.long_term_memory "
              "无人写入，恒为空；load_memory_context 零调用者。")
        return 1
    if file_memory and injected:
        print("PASS: 写入后 {user_memory} 数据源包含记忆内容，链路连通")
        return 0
    print("UNEXPECTED: 文件内容为空或判定异常，请人工检查")
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

**当前（缺陷在）预期结果**：`[1]` 有内容、`[2]` 为 0 字符，输出 `REPRO OK: 缺陷复现 …`，退出码 1。

**修复后预期结果（自验证）**：按 §4.2 接线后，`[2]` 渲染出"用户长期工作要求：\n- …复现条目…”，输出 `PASS`，退出码 0——同一脚本不改一行即可当回归用例。

> 边界说明：若该用户的 `users.long_term_memory` 列已存在历史数据，`[2]` 当前会显示列值而非空——此时判定标准改为"文件内容应优先于列值出现"（修复后 `[2]` 的内容应等于 `[1]` 而非列值）。

### 3.3 端到端复现（真实链路，可选）

1. 在会话中让 Agent 调用 `write_user_memory` 写入一条可辨识的长期要求；
2. 开启**新会话**，dump 提示词（`scripts/dump_session_prompt_live.py` 或 `generate_session_prompt.py`）；
3. **当前**：`{user_memory}` 渲染为空串；**修复后**：渲染出"用户长期工作要求"条目列表。

## 4. 修复方案

### 4.1 方案选型

| 方案 | 说明 | 判断 |
|---|---|---|
| A. 文件为准，读取接线（**采纳**） | `session_data_fetcher` 增加"从 `UserMemoryService.load_memory_context(user_name)` 取记忆"，作为 `long_term_memory` 的主数据源；DB 列降级为兜底 | 写入链路已完整可用，只补读取一步；文件自带条目裁剪（max_entries=50）；改动集中在一个 fetcher |
| B. 改写入到 DB 列 | `write_user_memory` 改为 UPDATE `users.long_term_memory` | 否决：需处理并发追加、长度上限、迁移既有语义；且 UserMemoryService 整套服务变死代码 |
| C. 双写双读 | 文件 + DB 同步维护 | 否决：两份真相，正是本次缺陷的成因模式 |

### 4.2 实施步骤

1. **接线读取**：`session_data_fetcher.py` 步骤 1 处改造——
   ```python
   long_term_memory = user.long_term_memory or ""
   if core is not None:
       svc = getattr(core, "_user_memory_service", None)   # __init__.py:99 已有实例
       if svc is not None and svc.enabled:
           file_memory = svc.load_memory_context(user_name)  # 需确认 _resolve_user_name 与文件名的用户名口径一致
           if file_memory:
               long_term_memory = file_memory
   ```
   即：文件记忆优先，为空时回退 DB 列（兼容历史数据，也为 B 方案留后路）。
2. **用户名口径核对**：写入用 `_user_id` 解析出的用户名（`memory_tool.py:69` 一带），读取在 fetcher 里用 `_resolve_user_name(user)`。PRD 落地时核对两者生成同一文件名，不一致则统一走 `_resolve_user_name`（必要时把该解析函数提为共用工具）。
3. **注入长度保护**：`load_memory_context` 输出在渲染前截断上限（建议 2000 字符，与 session_context 单条边界口径一致），防止记忆累积撑爆稳定前缀之外的变量区。
4. **提示词确认**：`emily-data/prompts/session.md` 确认 `{user_memory}` 占位符存在且位于消息历史之前的稳定区之外（变量区），不需要改提示词文件。
5. **补端到端校验**：在 tools_consistency 或单测中加一条链路断言：`save_memory("测试用户", ...)` → `fetch()` 返回的 snapshot `long_term_memory` 包含该条目。
6. **文档对齐**：`user_memory_service.py` 模块头注释更新为实际行为（文件优先、DB 兜底）；报告 §6-3 项在本 PRD 落地后标记关闭。

### 4.3 不做什么

- 不删除 `users.long_term_memory` 列（保留兜底与历史数据，避免 DB 迁移）；不在本 PRD 内做记忆的语义压缩/摘要演进（属报告 §1.4 压缩机制范畴）。

## 5. 验收标准

1. Agent 调用 `write_user_memory` 写入一条记忆 → 开启**新会话** → dump session prompt（`scripts/generate_session_prompt.py`）可见 `{user_memory}` 渲染出该条目。
2. 文件记忆为空的用户：`{user_memory}` 等于 DB 列值（兜底路径回归）。
3. 记忆超过 2000 字符时注入内容被截断且有日志。
4. 单测覆盖：写入→读取链路、文件空回退 DB、用户名口径一致性。

## 6. 风险与回滚

- **风险**：记忆内容进入提示词后，若有用户的记忆含过期/错误指令会影响会话质量 → 已有 `max_entries=50` 裁剪 + 2000 字符上限双闸；且 `user_memory_enabled` 配置开关可整体停用。
- **回滚**：接线处包在 `if core is not None` 判断内，回滚即删除该段，零结构变更。

---

## 7. 落地与验证记录（2026-09-11）

### 7.1 实现
- `session_data_fetcher.py`：抽出 `resolve_long_term_memory(user, user_name, core)` —— 文件记忆优先、DB 列兜底、2000 字符截断；`fetch()` 调用它。
- `user_memory_service.py`：模块头补"消费链路（读侧）"说明。
- 新增 `emily-core/tests/test_defect_fixes.py`：覆盖文件优先/DB 兜底/服务禁用/异常回退/超长截断/写读环回。

### 7.2 实际脚本用法（较 PRD 草案增强）
`python scripts/repro_user_memory_loop.py --user-id <uuid>`；`--legacy` 模拟修复前（fetch 不传 core）；`--db-url` / `EMILY_DATABASE_URL` 指定连接串（默认 `127.0.0.1:25432`）。脚本复用 `core._user_memory_service` 实例，不硬拼目录。

### 7.3 实测结果
| 场景 | 命令 | 结果 |
|---|---|---|
| 修复前模拟 | `repro_user_memory_loop.py --user-id <uuid> --legacy` | `[1]` 55 字符、`[2]` 0 字符 → `REPRO OK`，退出码 1 |
| 修复后 | `repro_user_memory_loop.py --user-id <uuid>` | `[1]`=101、`[2]`=101，含标记 → `PASS`，退出码 0 |
| 端到端 | `write_user_memory` → 新会话 `{user_memory}` | `保存条目 101 字符`；修复后 session_snapshot.long_term_memory 含 `复现记忆条目` |
| 单测 | `pytest tests/test_defect_fixes.py` | 记忆相关 7 项通过 |

> 生产口径：记忆目录由 `resolve_data_path` 解析（容器 `/app/user_memory`，开发 `emily-data/user_memory`）；读取必须复用 `core._user_memory_service`，勿硬拼。
