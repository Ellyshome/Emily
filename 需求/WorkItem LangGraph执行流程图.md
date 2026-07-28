# WorkItem LangGraph 执行流程图

```mermaid
graph TD
    START([START]) --> N1

    N1["① wi_node1<br/>意图校验/注入<br/>agent.node1_intent"]

    N1 --> N2

    N2["② wi_node2<br/>制定执行方案<br/>agent.node2_plan<br/><br/>若来自 error_analysis：<br/>replan_count++<br/>注入 replan_hint 重新规划"]

    N2 --> C_ABORT1{"flow_control<br/>.should_abort ?"}

    C_ABORT1 -->|"是"| END_D([END])
    C_ABORT1 -->|"否"| N3

    N3["③ wi_node3<br/>执行/工具调用循环<br/>agent.node3_execute<br/><br/>⚠️ 无重试策略<br/>（L2 数据写入非幂等）"]

    N3 --> C_ABORT2{"flow_control<br/>.should_abort ?"}

    C_ABORT2 -->|"是"| END_D

    C_ABORT2 -->|"否"| C_FAILED{"has_failed_step<br/>且 replan_count<br/>&lt; max_replan ?"}

    C_FAILED -->|"否（全部通过<br/>或 replan已耗尽）"| N4

    C_FAILED -->|"是"| EA

    EA["⚠ error_analysis<br/>自反省错误分析<br/><br/>① 代码预分类<br/>（权限关键词/L3 工具）<br/>↓ 无法判定 ↓<br/>② LLM 归因分析"]

    EA --> EA_ROUTE{"error_type 分类"}

    EA_ROUTE -->|"param_error<br/>tool_mismatch"| N2
    EA_ROUTE -->|"transient_failure"| N3
    EA_ROUTE -->|"missing_info<br/>permission_denied<br/>permanent_failure<br/>should_abort"| END_D

    N4["④ wi_node4<br/>结果汇总<br/>agent.node4_summary"]

    N4 --> END_D
```

## 流程说明

| 节点 | 职责 | 失败处理 |
|------|------|----------|
| ① wi_node1 | 意图校验与注入 | 无分支，直通 node2 |
| ② wi_node2 | 制定执行方案（支持 re-plan） | should_abort → 终止 |
| ③ wi_node3 | 执行工具调用（非幂等，不重试） | 失败 + 未耗尽 replan → error_analysis |
| ⚠ error_analysis | 自反省：代码预分类 → LLM 归因 | 参数/工具错误 → 回 node2 重新规划；瞬态失败 → 回 node3 直接重试；其余 → 终止 |
| ④ wi_node4 | 结果汇总 | 结束 |

## 关键约束

- **max_replan** 默认为 1，防止 error_analysis → node2 的死循环
- 一旦 replan_count 耗尽，node3 失败后直接进入 node4（不再进 error_analysis）
- error_analysis 两级分析：代码预分类优先（零 LLM 调用），无法判定时才调 LLM
- node3 不做自动重试（L2 数据写入非幂等，重试可能导致重复写入）
