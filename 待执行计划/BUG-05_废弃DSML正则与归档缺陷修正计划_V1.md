# BUG-05 修正计划 V1：废弃 DSML 正则 + 切 v4-pro + 归档缺陷修复

> **创建日期**：2026-07-30
> **状态**：待实施
> **优先级**：P0
> **依据**：`需求/BUG-05_DSML工具调用解析失败导致WorkItem取数失败及归档缺陷报告.md`（报告2）
> **决策来源**：用户指示——DSML 正则不可靠，废弃；以标准 function calling 为主，agent_loop_model 切 v4-pro；text fallback 精准反馈；error_analysis 节点归档 agent_loop 日志

---

## 0. 与已有计划的关系

本目录已有 `Agent_Loop_DSML文本泄漏修复_计划_V1.md`（V2，文件内标记"待实施"）。经代码核对，该计划**大部分内容已实施到代码中**：

- ✅ agent_loop_model 配置链路（config.py L51 / client.py L61,66 / __init__.py L157 / loop.py L113-116）
- ✅ text fallback 三级策略 + route_after_agent 修复 + graph.py 条件边含 agent_node
- ✅ quality_gate 节点（graph.py L60、nodes.py L401）

但该计划的 **DSML 正则部分存在偏差**：V2 计划写的正则是新格式 `[<＜](\||｜)DSML(\||｜)\s*tool_calls`，而代码里实际实施的是**旧的单竖线版本** `<\｜tool_calls\｜?\s*>`（client.py L26-L36）——这正是报告2 发现的正则失配根因（模型实际返回双竖线 `<｜｜DSML｜｜tool_calls>`，正则只认单竖线）。

本计划与旧计划的关系：
- **不重复**旧计划已实施的出口管控 / quality_gate / 配置链路
- **P0-1 删除**旧计划 §2.1 添加的 DSML 正则（因不可靠，用户决定废弃）
- **新增**报告2 发现的归档三层缺陷修复（旧计划未覆盖）
- **新增** text fallback 精准反馈改进（旧计划 correction 文案泛泛，需改进）
- **新增** v4-pro 默认值 + max_tokens 调整（旧计划只加了配置项，未设默认值）

---

## 1. Context

报告2 定位 BUG-05 主因为 **DSML 工具调用格式正则失配** + **归档三层缺陷**。原报告2 的 P0 是"修复正则兼容新格式"，但正则方案不可靠（模型格式再变一次就又失效）。按用户指示改为**根治路线**：

- **废弃 DSML 正则**，以 LLM 标准 function calling（通过 `tools` 参数输出 `tool_calls`）为主路径
- **agent_loop_model 切 v4-pro**——v4-pro 支持稳定 function calling，从源头规避 v4-flash 的 DSML 文本泄漏
- **text fallback 改为精准反馈**——执行遇挫时告知 agent 具体错在哪、怎么调整输出
- **在 error_analysis 排错节点归档 agent_loop 日志**——弥补 agent_node/tool_node 不挂 ArchiveHook 导致的日志缺失

---

## 2. P0-1 废弃 DSML 正则解析

**文件**：`emily-core/emily_core/infrastructure/llm/client.py`

- 删除三条正则常量 L26-L36（`_DSML_TOOL_CALLS_RE` / `_DSML_INVOKE_RE` / `_DSML_PARAM_RE`）
- 删除 `_try_parse_dsml_tool_call` 静态方法 L289-L330（codegraph 确认仅 1 caller、无测试覆盖，删除安全）
- 删除 `chat_messages` 中的 DSML 检测分支 L226-L233（`if tools and content: dsml_result = ...`）
- 删除仅 DSML 用到的 `import hashlib`（L24）
- 更新顶部注释 L16-L22：移除"DSML 解析作为降级兜底"，改为说明"依赖 agent_loop_model(v4-pro) 标准 function calling，text fallback 精准纠错"

---

## 3. P0-2 agent_loop_model 切 v4-pro + max_tokens 调整

**文件**：`emily-core/emily_core/config.py` + `emily-data/config/core_config.json` + `emily-core/emily_core/workitem/langgraph_engine/agent/loop.py`

### 3.1 默认模型切 v4-pro

- config.py L51 `llm_agent_loop_model: str = ""` → `"deepseek-v4-pro"`
- core_config.json 显式加 `"llm_agent_loop_model": "deepseek-v4-pro"`（运行时可调，不依赖默认值）
- 链路已验证通畅：__init__.py L157 传入 → client.py L66 落到 `self.agent_loop_model` → loop.py L113 读取 → client.py L151 已处理 v4-pro 不传 temperature

### 3.2 max_tokens 调整（推荐 8192）

v4-pro 是 reasoner 模型，`reasoning_content` 占 token，当前 `llm_max_tokens=1024` 会触发 `finish_reason=length` 截断。推荐 **8192**——留足推理+输出余量，reasoner 按实际用量计费，设大不等于花得多。

- config.py 新增字段 `llm_agent_loop_max_tokens: int = 8192`
- loop.py L116 调 chat_messages 时传 `max_tokens=getattr(config, "llm_agent_loop_max_tokens", 8192)`
- core_config.json 可选加 `"llm_agent_loop_max_tokens": 8192`

> 注意字段名统一，避免重蹈 `agent_max_iterations` vs `agent_loop_max_iterations` 不匹配的覆辙（见 §8 附带发现）。

---

## 4. P0-3 text fallback 精准反馈

**文件**：`emily-core/emily_core/workitem/langgraph_engine/agent/loop.py` L165-L205

现状的 correction（L174-L186）泛泛说"你返回了纯文本，请调用工具"，agent 不知道自己错在哪。改为**先诊断 content 特征，再给针对性反馈**：

```python
# 诊断文本特征，给出具体纠错方向
if "<｜" in content or "DSML" in content:
    diagnosis = ("你返回了 DSML/XML 文本标签格式（如 <｜tool_calls>），"
                 "这不是有效的工具调用。请直接通过 function calling 接口调用工具，"
                 "不要在回复内容里写任何 XML/DSML 标签。")
elif content.strip().startswith("{") or content.strip().startswith("["):
    diagnosis = ("你返回了 JSON 文本，但工具调用必须通过 function calling 接口输出，"
                 "不能在 content 里写 JSON。请直接调用对应工具。")
else:
    diagnosis = ("你返回了纯文本回复，但当前阶段必须调用工具才能执行操作。"
                 f"可用工具：{', '.join(t['function']['name'] for t in tool_specs)}。")
```

把 `diagnosis` 拼进 correction 消息，让 agent 知道**错在哪 + 怎么调**。第 2 次仍附工具调用示例。

---

## 5. P1-1 error_analysis 节点归档 agent_loop 日志（用户建议的"预设钩子截取"）

**文件**：`emily-core/emily_core/workitem/pipeline/hook.py` L369-L375 + `emily-core/emily_core/services/session_archive_writer.py` L700-L706

error_analysis 节点已有 `fire_after` 钩子（nodes.py L392），只是 `category_map` 把 `agent_loop` / `error_analysis` 两类日志过滤掉了。改白名单即可让排错节点成为失败现场的日志记录点。

`hook.py` category_map：

```python
category_map = {
    "created": set(),
    "routing": set(),
    "executing": {"planning", "execution", "guardian"},
    "summarizing": {"execution", "guardian", "agent_loop"},          # +agent_loop（成功路径归档）
    "error_analysis": {"execution", "agent_loop", "error_analysis"}, # +agent_loop +error_analysis（失败现场）
}
```

`session_archive_writer.py`：
- `phase_labels` 加 `"agent_loop": "Agent 循环"`、`"error_analysis": "错误分析"`
- 循环列表 L706 加 `"agent_loop"`、`"error_analysis"`

**效果**：失败时 error_analysis 节点 fire_after 归档全部 agent_loop LLM 日志（含 3 次 text fallback 的请求/响应）；成功时 summarizing 归档 agent_loop 日志。`archived_ids` 跨节点去重（hook.py L377）保证不重复。

---

## 6. P1-2 error_analysis 段读到空修复

**文件**：`emily-core/emily_core/workitem/workitem.py` + `emily-core/emily_core/workitem/langgraph_engine/nodes.py` L321-L328 + `emily-core/emily_core/services/session_archive_writer.py` L681-L691

报告2 §3.3 指出：`result` 里其实存了正确的 `transient_failure` + root_cause，但归档读到空。三处一起改（根治 + 兜底）：

1. **workitem.py** WorkItem dataclass 显式加字段：`error_analysis: dict = field(default_factory=dict)`（根治动态属性赋值隐患）
2. **nodes.py L327-L328** 去掉 `try/except: pass`，让赋值异常能暴露（CLAUDE.md 约束 #0 根治而非迁就）
3. **session_archive_writer.py L683-L689** error_analysis 分支增加从 `prompt_info` 读 `error_type` / `root_cause` / `should_abort` 作为兜底（`prompt_info_error_analysis` 数据已确认正确，见 nodes.py L384-L388）

---

## 7. P2 executing 段空标题处理

**文件**：`emily-core/emily_core/services/session_archive_writer.py` render_node_section

报告2 §3.2 缺陷 B：executing 段在 agent_node 之前跑，天然无 agent_loop 日志，归档只剩空标题 `### 🔧 Agent 执行循环`。

处理方式：executing 段不再渲染"Agent 执行循环"空标题。"Agent 循环"内容由 summarizing / error_analysis 段承载（已在 §5 通过 phase_labels 落地）。

> 报告2 §3.3 末尾的"should_abort=False 与 WI DONE 矛盾"（多轮 error_analysis 只归档一段）本次不做，后续加轮次标记。

---

## 8. 附带发现（非 BUG-05 范围，提请后续处理）

`__init__.py` L487 和 `loop.py` L95 读 `agent_loop_max_iterations`，但 config.py L71 和 core_config.json 里字段名是 `agent_max_iterations`——配置名不匹配，永远拿到默认值 12。建议后续统一字段名。

---

## 9. 涉及文件清单

| # | 文件 | 改动 | 对应章节 |
|---|------|------|---------|
| 1 | emily-core/emily_core/infrastructure/llm/client.py | 删 DSML 正则+函数+检测分支+hashlib import+更新注释 | §2 |
| 2 | emily-core/emily_core/config.py | llm_agent_loop_model 默认 v4-pro + 新增 llm_agent_loop_max_tokens=8192 | §3 |
| 3 | emily-data/config/core_config.json | 加 llm_agent_loop_model + llm_agent_loop_max_tokens | §3 |
| 4 | emily-core/emily_core/workitem/langgraph_engine/agent/loop.py | text fallback 精准反馈 + chat_messages 传 max_tokens | §3.2 §4 |
| 5 | emily-core/emily_core/workitem/pipeline/hook.py | category_map 加 agent_loop/error_analysis | §5 |
| 6 | emily-core/emily_core/services/session_archive_writer.py | phase_labels+循环加 agent_loop/error_analysis + executing 空标题 + error_analysis 分支兜底读 prompt_info | §5 §6 §7 |
| 7 | emily-core/emily_core/workitem/workitem.py | WorkItem 加 error_analysis 字段 | §6 |
| 8 | emily-core/emily_core/workitem/langgraph_engine/nodes.py | 去 try/except: pass | §6 |

---

## 10. 实施顺序

1. §2 client.py 废弃 DSML 正则（P0，先去掉不可靠兜底）
2. §3 config.py + core_config.json 切 v4-pro + max_tokens（P0，源头规避）
3. §4 loop.py text fallback 精准反馈 + chat_messages 传 max_tokens（P0）
4. §5 hook.py + session_archive_writer.py 归档白名单（P1，失败现场可见）
5. §6 workitem.py + nodes.py + session_archive_writer.py error_analysis 段修复（P1）
6. §7 session_archive_writer.py executing 空标题（P2）

---

## 11. 验证步骤

1. 重启 emily-core：`docker compose -f docker-compose-napcat.yml restart emily-core`
2. 清 pycache：`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`
3. emy-test 复测：
   ```powershell
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下翠湖庭院项目有哪些全景节点" --sender "李景利"
   ```
4. 预期：agent_loop 用 v4-pro 标准 function calling 调 query_data → 返回真实节点数据
5. 查 docker 日志确认 `tool_node business query_data result: {...total: N...}`、`agent_node LLM result: type=tool_call`
6. 查 LLM trace（`emily-data/logs/llm_trace.jsonl`）确认 agent_loop 调用 model=`deepseek-v4-pro`、无 DSML 文本泄漏
7. 查归档（`emily-data/session_archives/`）确认"Agent 循环"段出现 LLM/工具调用记录；若触发 error_analysis，"错误分析"段显示正确的 `transient_failure` + root_cause 而非 `unknown/无`

---

## 12. 回滚方案

所有改动为删除/替换/新增字段，无 DB 迁移、无配置文件格式变更：

- 回滚 DSML 废弃：git 恢复 client.py 正则+函数+检测分支
- 回滚 v4-pro：`llm_agent_loop_model` 默认值改回空字符串
- 回滚 max_tokens：删除 `llm_agent_loop_max_tokens` 字段 + loop.py 的 max_tokens 参数
- 回滚归档白名单：`category_map` / `phase_labels` 恢复原集合
- 回滚 error_analysis 字段：删除 WorkItem.error_analysis 字段（dataclass 加字段不影响序列化兼容）
