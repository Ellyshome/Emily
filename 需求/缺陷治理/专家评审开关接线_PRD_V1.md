# PRD：接通专家评审死开关 `expert_review_enabled`

> **来源**：`需求/Pi_Agent对比分析报告_业务设计篇.md` §6-2（340 行）
> **级别**：🔴 死代码/断线（配置误导运维）｜**优先级**：P2（成本低，随缺陷治理批次落地）
> **日期**：2026-09-11 ｜ **状态**：待评审

---

## 1. 问题核实（当前代码复验）

**结论：缺陷存在。** 证据如下（2026-09-11 在当前工作树核实）：

| 项 | 证据 |
|---|---|
| 开关仅定义、无读取方 | `emily-core/emily_core/config.py:204` `expert_review_enabled: bool = True`；全仓 grep `expert_review_enabled` 仅此 1 处命中 |
| 实际生效的评审门控 | `workitem/langgraph_engine/graph.py:124-138` `route_after_routing`：仅依据 `wi.expert_required and wi.expert_id` 决定进 `expert_review` 还是 `executing` |
| `expert_required` 的真实来源 | `session/session_agent.py:711-719`：SOP 绑定了 ACTIVE 专家时置 `wi.expert_required = True` |
| 评审节点自身也不看开关 | `workitem/langgraph_engine/nodes.py:555` `make_expert_review` 接收 `config` 但只用于 LLM 参数，无 enabled 判断 |

## 2. 问题分析（原因）

1. **配置与门控两条线各写各的**：`expert_review_enabled` 是设计评审节点时加进 Config 的；而路由逻辑（`route_after_routing`）后来按"WorkItem 是否绑定专家"实现，没有回头消费这个配置项。
2. **默认 True 掩盖了断线**：默认值恰好等于当前实际行为（绑了专家就评审），所以功能表现无异常，grep 不 grep 都"看起来没问题"——典型的"默认值巧合性正确"。
3. **危害**：运维若把 `expert_review_enabled` 置 False（如专家模型故障时降级），配置"读取成功、实际无效"，评审照走，故障得不到降级；反之排查问题时该开关也不提供任何信息。这是报告 §4.4"接上死开关"项的直接对应。

## 3. Bug 复现方法（兼作修复后自验证）

### 3.1 静态证据（30 秒）

```bash
cd emily-core/emily_core && grep -rn "expert_review_enabled" --include=*.py .
```

当前：仅 `config.py:204` 一处命中（定义），无任何读取方 → 开关必然无效。修复后：至少 2 处命中（定义 + 读取）。

### 3.2 运行时级复现（脚本，秒级，无 LLM/DB 依赖）

新建 `scripts/repro_expert_review_switch.py`——直接调用缺陷所在的真函数 `route_after_routing`，构造"开关关、但 WI 绑定了专家"的最小场景：

```python
"""repro_expert_review_switch.py — 复现 expert_review_enabled 死开关缺陷。

用法: python scripts/repro_expert_review_switch.py
退出码: 0 = 开关生效（修复后预期）; 1 = 缺陷存在（开关被忽略）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "emily-core"))

from emily_core.config import Config
from emily_core.workitem.workitem import WorkItem
from emily_core.workitem.pipeline.context import BusContext
from emily_core.workitem.langgraph_engine.state import set_bus_context
from emily_core.workitem.langgraph_engine.graph import route_after_routing


def route(config: Config) -> str:
    wi = WorkItem(id="repro-switch-1")   # 若构造签名不符，改为无参构造后逐属性赋值
    wi.expert_required = True            # 复现 session_agent.py:719 的绑定结果
    wi.expert_id = "expert-x"
    set_bus_context(BusContext(work_item=wi))
    return route_after_routing({})       # 修复后改为: make_route_after_routing(config)({})


def main() -> int:
    route_off = route(Config(expert_review_enabled=False))
    if route_off == "expert_review":
        print("REPRO OK: 缺陷复现 — expert_review_enabled=False 被忽略，路由仍返回 expert_review")
        return 1
    if route_off == "executing":
        print("PASS: 开关生效 — False 时跳过专家评审，直达 executing")
    else:
        print(f"UNEXPECTED: route={route_off}")
        return 2

    route_on = route(Config(expert_review_enabled=True))
    print(f"对照组: True 时 route={route_on}（预期 expert_review，保持原行为）")
    return 0 if route_on == "expert_review" else 2


if __name__ == "__main__":
    sys.exit(main())
```

**当前（缺陷在）预期结果**：输出 `REPRO OK: 缺陷复现 …`，退出码 1——`Config(expert_review_enabled=False)` 已构造成功，但路由函数根本不消费它。

**修复后预期结果（自验证）**：

- 按修复方案改为闭包工厂后，把脚本中的 `route_after_routing({})` 换成 `make_route_after_routing(config)({})`（其余不动）；
- `False` 分支输出 `PASS: 开关生效 …`，`True` 对照组保持 `expert_review`，退出码 0。

### 3.3 生产级复现（真实链路，可选）

1. 配置中把 `expert_review_enabled` 置 `False`；
2. 触发一个 SOP 绑定了 ACTIVE 专家的 WorkItem（`session_agent.py:719` 会置 `expert_required=True`）;
3. 观察 emily-core 日志——**当前**：仍出现 `route_after_routing: WI … → expert_review (expert=…)`，评审照常执行（开关被无视）；**修复后**：记录一条跳过说明并直接进 `executing`。

## 4. 修复方案

### 4.1 语义定义

- `expert_review_enabled = True`（现状默认）：维持现有行为——`wi.expert_required && expert_id` 命中即进 `expert_review` 节点。
- `expert_review_enabled = False`：**全局跳过专家评审**，绑定了专家的 WorkItem 直接走 `executing`（agent loop），并在日志中记录一次性说明（避免"绑了专家却没评审"被当成 bug 上报）。

### 4.2 实施步骤

1. **路由处接线**（唯一权威门控点）：`graph.py` 的 `route_after_routing` 增加开关判断。该函数目前通过 `get_bus_context()` 取 WI，拿不到 config；采用**闭包工厂**改造——`build_workitem_graph` 已接收 `config`（`graph.py:31`），把 `route_after_routing` 改为 `make_route_after_routing(config)` 返回的闭包，条件边注册处同步替换。判断顺序：`config.expert_review_enabled` 为 False → 直接 return `"executing"`（其余逻辑不变）。
2. **节点内防御性判断（可选加固）**：`make_expert_review`（`nodes.py:565`）入口处若 `not getattr(config, "expert_review_enabled", True)`，按现有 fallback 模式（`:576-579` 的 `fallback to executing`）直接降级返回。作用：即使未来出现第二条进入路径，开关仍然兜底。
3. **配置注释与暴露**：`config.py:204` 的 docstring 补写语义（"False = 全局跳过专家评审，即使 SOP 已绑定专家"）；确认该字段可通过环境变量覆盖（沿用 Config 既有 env 加载机制）。
4. **日志可观测**：开关为 False 且发生跳过时打一条 INFO（含 wi id），便于运维确认配置已生效。

### 4.3 不做什么

- 不改变"专家绑定 → expert_required"的判定逻辑本身（那是业务规则，不是开关职责）；
- 不引入第三种取值（如 per-SOP 覆盖），如有分层需求另立需求单。

## 5. 验收标准

1. `expert_review_enabled = False` + SOP 绑定 ACTIVE 专家的 WorkItem：路由日志显示直接进 `executing`，`expert_review` 节点零调用。
2. `expert_review_enabled = True`（默认）：行为与修复前逐字节一致（回归：评审节点照常进入、`expert_review_result` 照常被 summarizing 消费）。
3. grep `expert_review_enabled` ≥ 2 处命中（定义 + 至少一处读取），测试覆盖两个分支。
4. 配置通过环境变量可改，无需改代码。

## 6. 风险与回滚

- **风险**：极低——只增一个短路判断。唯一注意点是闭包工厂改造时不要误改条件边其他分支。
- **回滚**：开关置 True 即恢复原行为；代码回滚为单函数还原即可。

---

## 7. 落地与验证记录（2026-09-11）

### 7.1 实现
- `graph.py`：新增 `make_route_after_routing(config)` 闭包工厂（False→短路返回 `executing` + INFO 日志），`route_after_routing`/`_route_expert_or_executing` 保留为向后兼容入口；条件边改用闭包。
- `nodes.py`：`make_expert_review` 入口增加开关防御性兜底。
- `bootstrap.py`：env_map 增加 `EMILY_EXPERT_REVIEW_ENABLED`，`bool_fields` 增加 `expert_review_enabled`（false/0/no/off → False）。
- `config.py`：docstring 补语义与环境变量说明。

### 7.2 实测结果
| 场景 | 命令 | 结果 |
|---|---|---|
| 运行时（修复后） | `python scripts/repro_expert_review_switch.py` | `PASS: 开关生效 — False 时直达 executing`；对照组 True → `expert_review`，退出码 0 |
| 静态 | `grep -rn expert_review_enabled emily-core/emily_core` | ≥2 处命中（定义 + 路由/节点读取） |
| 环境变量 | `pytest tests/test_defect_fixes.py -k env_bool` | `EMILY_EXPERT_REVIEW_ENABLED=false` → False，通过 |
| 回归 | `pytest tests/` | 21 passed |

> 脚本含修复前兼容分支：若 `make_route_after_routing` 不存在（旧代码），回退旧 `route_after_routing(state)`，该分支必然忽略开关 → 退出码 1，可复现缺陷。
