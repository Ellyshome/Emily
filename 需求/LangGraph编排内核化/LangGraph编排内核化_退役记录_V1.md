# LangGraph 编排内核化 — 退役记录（V1）

> **执行日期**：2026-09-12
> **目标**：全量废弃被新内核取代的旧编排实现，使会话编排图为唯一渠道；未实现的原功能退役休眠但不删除，留作后续实现的参考。
> **依据**：[复盘 V1](LangGraph编排内核化_复盘_V1.md)、[功能复现测试用例 V1](LangGraph编排内核化_功能复现测试用例_V1.md)

## 一、已删除（本轮）

| 对象 | 内容 | 规模 |
|------|------|------|
| `session/loop.py` | 10 个退役方法：`handle`、`_handle_impl`、`_run_loop`、`_run_plan`、`_run_plan_step`、`_settle_capability_result`、`_register_suspend`、`_render_plan_results`、`_enforce_capability_progress`、`_publish_progress` | 文件 880 → 614 行（**-266**） |
| `session/suspend_registry.py` | 内存挂起登记（已由检查点中断取代） | 删除（**-111**） |
| `workitem/pipeline/real_guardian.py` | 空实现守护者（全仓零引用） | 删除（**-168**） |
| 合计 | | **约 -545 行** |

删除方式：脚本按方法名精确切除（先做"被删方法是否仍被保留代码调用"的残留校验，命中即中止不写回）。首轮校验**拦下两处真实依赖**——`_execute_tool` 与 `_execute_business_tool` 中仍会写旧的挂起登记，随后连同该分支一并拆除（挂起由检查点承担）。

## 二、唯一渠道改造

| 改动 | 位置 | 说明 |
|------|------|------|
| 分派改为无条件 | `session/loop.py` 池 `route()` | 删除 `_use_graph()` 开关判定与旧分支，一律走 `handle_via_graph` |
| 删除旧链路回退 | `emily_core/__init__.py` 入站分派 | 去掉"N7 回退到旧 `session_pool`"与 `else` 分支；池未就绪改为**报错不静默** |
| 访客兜底（替代 N7 回退） | 池 `route()` | 发送者未解析为系统用户时以 `GUEST_USER_ID` 建会话，权限快照为空并按 fail-closed 收窄——**既保证唯一渠道，又不丢消息** |

## 三、冷代码保留（未实现的原功能，休眠不删）

| 对象 | 保留理由 | 对应复现目标 |
|------|---------|-------------|
| `session/confirm_dialog.py` | `CONTROL_TOOL_SPECS` 仍活（能力目录引用）；`ConfirmDialog.handle` 为确认执行体 | FR-E4 |
| `session/confirm_queue.py` | 待确认队列（旧链路专用） | FR-E4、FR-E5 |
| `SessionLoop._pending_event_injection` | 待确认事件注入（含 `_dialog.prompt_injection`） | FR-E5、FR-H5 |
| `SessionLoop._group_injections` | 群聊注入 | FR-H4 |
| `expert_*`（工具与节点） | 经 D10 决策休眠 | FR-C9 |

> 冷代码在文件中保持原位（不做搬移，避免改变导入路径），但**已无调用方**；后续实现确认/注入时以其为参考。

## 四、验证证据

| 项 | 结果 |
|----|------|
| 语法编译 | 通过（仅 `/app` 只读导致的 `__pycache__` 写入提示，无语法错误） |
| 已删模块残留引用 | `suspend_registry` / `real_guardian` / `_use_graph` / `loop.handle(` 全仓 **0 命中** |
| 内核回放 | **11/11 通过** |
| 端到端·闲聊 | HTTP 200，直答「你好呀，林建辉！有什么需要帮忙的吗？」（零模型调用） |
| 端到端·业务 | HTTP 200，回复列出 5 条真实事件（含标题、日期、状态） |
| 端到端·访客兜底 | HTTP 200，未授权发送者仍获答「你好呀，陌生人！…」——消息不再丢弃 |
| 运行形态 | 日志 `session graph built: fast→understand→gate⇄execute→summarize, gate=True, checkpointer=True` |
| 服务状态 | 容器 running、重启 0 次 |

> 过程中的两次"空响应"均为**探针载荷问题**（字段名 `sender` 应为 `sender_name`、缺 `platform`/`conversation_type`，被判为 `mode=monitor` 静默收集），非系统回归。

## 五、影响与取舍

1. **灰度回退能力随之消失**：`EMILY_SESSION_GRAPH_ENABLED` 关闭后回退旧链路的路径已不存在（US-09 的 AC9.1/AC9.2 不再成立）。回退手段改为代码回滚（git）与镜像回滚。同类变化需在 PRD 下一次修订中登记为"被本记录替代"。
2. **旧链路文件仍在盘上**：`session_pool.py` / `session_agent.py` / `orchestrator.py` 仍被 `EmilyCore` 实例化，用于 `POST /session/terminate`、会话统计与监控——它们**已不是消息渠道**，但仍是服务依赖。

## 六、残留待办（第二轮，需你确认后执行）

| # | 项目 | 依赖链 | 预估 |
|---|------|-------|------|
| 1 | 删除 `session_pool.py` / `session_agent.py` / `orchestrator.py` | 需先把 `terminate` 与会话统计迁到新池；`SessionAgent._try_fast_reply` 需抽为独立小模块（新内核与归档器在用） | 中 |
| 2 | 清理其配置与模型引用 | `config.py:198-204`（orchestrator 字段）、`models.py:573`、`monitor.py:79`、`session_factory.py`、`session_archive_writer.py:627` | 中 |
| 3 | 删除 `workitem/pipeline/bus.py`（255 行）与 `node.py` | 当前由 `session_pool.py:32`、`session_agent.py:35` 引用，须与第 1 项同批 | 低 |
| 4 | 清理两个失效符号 | `session_graph_enabled`（开关已无读取方，仍存在于 config / bootstrap / compose）、`SessionLoopPool.last_build_failed`（只写不读） | 低 |
| 5 | 冷代码标注 | 在第五节所列冷文件与冷方法处加显式标注，避免被误判为可删死代码 | 低 |

---

*本记录的所有删除均有残留校验与运行验证支撑；冷代码保留项与残留待办已逐条列明。*
