# 修复：Agent 未回答用户问题（node4 回复合成失败 → 硬编码兜底）

## Context（问题背景）

用户执行测试 `uv run python .claude/skills/emy-test/cli.py --managed --llm --message "我当前在什么全景节点里？"`，收到回复：

> 操作已完成！共执行 1 个步骤，调用 1 个工具，数据库操作 0 次。
> ⚠️ [reply] 回复未回答用户问题，用户询问当前所在全景节点，但回复仅报告操作统计，未提供节点信息

用户询问"当前在什么全景节点"，但回复仅报告执行统计，未包含节点信息。需要定位根因并修复。

## 根因分析（已通过日志 + 代码确认）

整条链路有 3 层问题，最终导致用户看到的是**硬编码兜底串**而非工具返回的节点信息：

### 根因 1（直接原因）：node4 LLM 回复合成失败 → 退回硬编码兜底

[workitem_agent.py:559](emily-core/emily_core/workitem/workitem_agent.py#L559) `_llm_synthesize_reply()` 的回退链：LLM `chat_messages(json_mode=True)` → 取 `data.get("reply","")` → 若 `reply and len(reply) > 20` 则返回，否则退到硬编码兜底（line 647-651）。

硬编码兜底只报告统计数字，**完全丢弃了 `step_results` 里工具实际返回的节点信息**：
```python
# workitem_agent.py:647-651
elif tool_calls > 0:
    return (
        f"操作已完成！共执行 {steps} 个步骤，"
        f"调用 {tool_calls} 个工具，数据库操作 {summary.get('db_operations', 0)} 次。"
    )
```

合成失败的具体原因有三（均在日志/代码中确认）：

- **Bug A — `_build_tools_text` 调用不存在的方法**：[workitem_agent.py:656-671](emily-core/emily_core/workitem/workitem_agent.py#L656) 是 `@staticmethod`，内部调用 `BusinessFlowToolRegistry.get_instance()`。但 [business_flow_tools.py:36](emily-core/emily_core/tools/business_flow_tools.py#L36) 的 `BusinessFlowToolRegistry` 类**没有 `get_instance()` 方法**（不是单例，只是普通类），实例是通过 `self._business_flow_tools` 注入 `WorkItemAgent.__init__` 的。日志确认：`AttributeError: type object 'BusinessFlowToolRegistry' has no attribute 'get_instance'`。结果：workitem.md prompt 的 `{available_tools}` 永远是"（无可用工具）"，且因为 `wi_vars` 先于 session_vars 替换，session_vars 里正确的 `{available_tools}` 不再生效。

- **Bug B — workitem.md prompt 缺少 JSON 输出 schema**：[workitem.md](emily-data/prompts/workitem.md) 的"回复合成规则"只说"将执行步骤的结果提炼为自然语言摘要"，**没有指定输出 JSON 格式 `{"reply": "..."}`**。但代码用 `json_mode=True` 调用并取 `data.get("reply","")`。LLM 被强制输出 JSON 却不知道该用 "reply" 键，可能返回 `{"summary": "..."}` 等其他键 → `reply` 为空 → 静默退到兜底。

- **Bug C — 短回复静默退回，无日志**：[workitem_agent.py:625](emily-core/emily_core/workitem/workitem_agent.py#L625) `if reply and len(reply) > 20: return reply` —— 当 LLM 返回空/短回复或缺少 "reply" 键时，**不记任何日志**就退到兜底。07-22 10:57 测试日志中既无"LLM synthesized reply"也无"falling back"，正是此路径。07-21 日志则出现过 `node4: LLM reply synthesis failed, falling back: LLM response is not valid JSON: {`，说明 LLM 偶尔也返回非法 JSON。

### 根因 2（次要）：意图识别返回 fallback

日志 `Session[123456006] intent: sop=None conf=none compound=False fallback=True` —— LLM 未将"我当前在什么全景节点里？"匹配到 SOP-005（数据查询）或 SOP-011（节点管理），走了 fallback 路径（`sop_id=None`）。这本身不致命（fallback 仍应通过管道执行并回复），但叠加根因 1 后回复彻底丢失。session.md prompt 规则 #7 将"元认知问题"导向 fallback，LLM 可能把"我在哪个节点"误判为元认知问题。

### 根因 3（设计约束）：Guardian 只标记不修复

[real_guardian.py:98](emily-core/emily_core/workitem/pipeline/real_guardian.py#L98) `review_reply()` 正确识别了"回复未回答问题"并追加 `[reply]` 警告，但设计原则是"只标记不拦截"，不会重新生成回复。所以 Guardian 提醒出现了，但回复本身没变。

## 修复方案

### Fix 1：修复 `_build_tools_text` 的 `get_instance()` bug（必须）

[workitem_agent.py:656-671](emily-core/emily_core/workitem/workitem_agent.py#L656)

将 `@staticmethod` 改为实例方法，用 `self._business_flow_tools` 替代不存在的 `BusinessFlowToolRegistry.get_instance()`：

```python
def _build_tools_text(self) -> str:
    """构建可用工具列表文本（供 prompt 注入）。"""
    try:
        registry = self._business_flow_tools
        if registry:
            entries = []
            for name in sorted(registry.list_names()):
                tool = registry.get(name)
                if tool:
                    entries.append(f"- {name}: {tool.description}")
            return "\n".join(entries) if entries else "（无可用工具）"
    except Exception as e:
        logger.warning("format tool list failed: %s", e, exc_info=True)
    return "（无可用工具）"
```

调用处 [workitem_agent.py:593](emily-core/emily_core/workitem/workitem_agent.py#L593) `self._build_tools_text()` 已是实例调用形式，无需改动。

### Fix 2：硬编码兜底改为优先包含 step_results 输出（必须）

[workitem_agent.py:631-653](emily-core/emily_core/workitem/workitem_agent.py#L631) 的硬编码兜底段，在 `tool_calls > 0` 分支中，优先提取最后一个成功 step 的 `output`（即工具 handler 返回的 `reply` 字段，已在 `_real_execute` line 464 写入 `sr.output`）：

```python
elif tool_calls > 0:
    # 优先使用最后一个成功步骤的输出（工具 handler 的 reply）
    last_output = ""
    for sr in getattr(wi, "step_results", []) or []:
        if getattr(sr, "success", True) and getattr(sr, "output", ""):
            last_output = sr.output
    if last_output:
        return last_output
    return (
        f"操作已完成！共执行 {steps} 个步骤，"
        f"调用 {tool_calls} 个工具，数据库操作 {summary.get('db_operations', 0)} 次。"
    )
```

这样即使 LLM 合成失败，用户也能看到工具实际返回的内容（节点信息），而非空洞的统计。

### Fix 3：workitem.md prompt 补充 JSON 输出 schema（必须）

[emily-data/prompts/workitem.md](emily-data/prompts/workitem.md) 的"回复合成规则"末尾追加一条：

```
7. 必须输出 JSON 格式：{"reply": "你的自然语言回复内容"}，reply 字段为最终给用户的回复文本，不要包含其他字段
```

与 session.md line 113 的"仅输出一个 JSON 对象"约定保持一致，确保 LLM 知道用 "reply" 键。

### Fix 4：LLM 合成短回复/缺键时补日志（辅助）

[workitem_agent.py:622-627](emily-core/emily_core/workitem/workitem_agent.py#L622) 在 `if reply and len(reply) > 20` 判断的 else 分支补 warning 日志，便于后续排查：

```python
result = await self._llm.chat_messages(full_messages, json_mode=True)
data = result.get("data", {})
reply = data.get("reply", "") if isinstance(data, dict) else ""
if reply and len(reply) > 20:
    logger.debug("node4: LLM synthesized reply (%d chars)", len(reply))
    return reply
# 补日志：记录为何退到兜底
logger.warning(
    "node4: LLM reply unusable (reply=%r len=%d keys=%s), falling back to hardcoded",
    reply[:80], len(reply), list(data.keys()) if isinstance(data, dict) else [],
)
```

## 不在本次范围

- **意图识别 fallback 问题**（根因 2）：即使 node4 修复后 fallback 路径也能正确回复，暂不改 session.md 路由规则或 SOP 目录。若修复后仍频繁 fallback，再单独排查 SOP-005/SOP-011 的意图匹配。
- **Guardian 改为可重新生成回复**：设计原则是"只标记不拦截"，不改。
- **scheduler datetime 比较 bug**（日志中 `can't compare offset-naive and offset-aware datetimes`）：与本次问题无关，单独处理。

## 验证方式

1. 清缓存重启：`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +` 然后 `docker compose -f docker-compose-napcat.yml restart emily-core`
2. 确认 `_build_tools_text` 不再报错：`docker logs --tail 50 emily-core 2>&1 | grep -i "get_instance\|format tool list failed"` 应无新日志
3. 复现场景测试：
   ```powershell
   docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 10;"
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "我当前在什么全景节点里？" --sender "孙建国"
   ```
4. 预期结果：回复应包含实际的节点信息（如节点名称/编号），不再是"操作已完成！共执行 N 个步骤..."。若 LLM 合成成功 → 自然语言回复；若 LLM 合成仍失败 → Fix 2 兜底显示工具返回的节点信息。
5. 查日志确认 node4 行为：`docker logs --tail 100 emily-core 2>&1 | grep -iE "node4|synthesized|unusable|falling back"`