# 静默收集 Monitor 模式 — 测试报告

> **测试日期**：2026-07-26
> **测试环境**：emily-core Docker 容器（重启后），PostgreSQL emily 库
> **测试工具**：emy-test CLI（已更新群聊模拟参数）
> **测试发送者**：李景利（users 表 UUID: c0d515d8-f3af-4b3f-8f1b-dffcb489ce47）

---

## 测试结果总览

| # | 场景 | 命令 | CLI 输出 | 预期 | 结论 |
|---|------|------|---------|------|------|
| 1 | 私聊 | `--message "你好..."` | LLM 自我介绍回复 | 正常响应 | ✅ |
| 2 | 群聊 @bot | `--conversation-type group --message "@Emily..."` | LLM 待办事项回复 | 正常响应 | ✅ |
| 3 | 群聊 非@ | `--conversation-type group --no-at --message "今天天气..."` | `[不接管，无回复]` | 静默收集 | ✅ |

---

## 测试 1：私聊向后兼容

**命令**：
```powershell
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "你好，请介绍一下你自己" --sender "李景利"
```

**CLI 输出**（摘要）：
> 你好李景利！我是 Emily，你的企业工程项目管理助手，专门协助你处理翠湖庭院住宅小区的各项工作。我目前支持这些流程：系统管理类的有 SOP-000-SYS...

**结论**：私聊行为不变，LLM 正常响应。向后兼容性确认。

---

## 测试 2：群聊 @机器人 → 正常响应

**命令**：
```powershell
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --conversation-type group --message "@Emily 帮我查下今天的待办事项" --sender "李景利"
```

**CLI 输出**（摘要）：
> 今天翠湖庭院住宅小区这边共有10个待办任务，我目前能看到的有：审核景观绿化施工方案正在由李经理处理，还有三项待办分别是苗木采购进场验收、硬质铺装与园路施工、景观绿化种植施工，都指派给了黄工...

**数据库验证**（messages 表，group_001）：

| content | direction | takeover |
|---------|-----------|----------|
| `@Emily 帮我查下今天的待办事项` | `user_to_agent` | `t` |
| `今天翠湖...共有10个待办任务...` | `agent_to_user` | `t` |

**结论**：群聊中 @机器人 触发完整流水线（DomainTakeover → LLM → 回复），入站+出站消息均持久化。

---

## 测试 3：群聊 非@ → 静默收集（核心场景）

**命令**：
```powershell
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --conversation-type group --no-at --message "今天天气不错，适合户外施工" --sender "李景利"
```

**CLI 输出**：
```
[不接管，无回复]
```

**Docker 日志证据**：
```
2026-07-26 16:40:15 [INFO] emily.domain_takeover: takeover=true, reason=monitor_silent_collect
2026-07-26 16:40:15 [INFO] emily.core: Silent collect: msg persisted conv=group_001 sender=李景利
```

**数据库验证**（messages 表，group_001）：

| content | takeover | direction | group_id |
|---------|----------|-----------|----------|
| `今天天气不错，适合户外施工` | `t` | `user_to_agent` | `group_001` |

**结论**：
1. ✅ `DomainTakeoverService` 正确返回 `takeover=True, should_reply=False`
2. ✅ 日志输出了 `monitor_silent_collect` 原因和 `Silent collect` 进入标记
3. ✅ 消息持久化到 `messages` 表（`takeover=t`, `direction=user_to_agent`）
4. ✅ 没有触发 SessionPool 路由（无 LLM 调用，无 `agent_to_user` 出站记录）
5. ✅ CLI 端收到 204 No Content，显示 `[不接管，无回复]`

---

## 重点对比：@bot vs 非@ 的消息持久化差异

同一群聊 `group_001` 的三条消息：

| 时间 | 内容 | direction | 说明 |
|------|------|-----------|------|
| 16:39:44 | `@Emily 帮我查下...` | `user_to_agent` | @消息入站 |
| 16:40:02 | `今天翠湖...共有10个待办...` | `agent_to_user` | @消息的 LLM 回复出站 |
| 16:40:15 | `今天天气不错，适合户外施工` | `user_to_agent` | 静默收集：仅入站持久化，**无出站回复** |

静默收集的消息只有 `user_to_agent` 方向，没有对应的 `agent_to_user` 记录——证明 LLM 流水线未被触发。

---

## emy-test 工具更新验证

CLI 新增的三个参数均正常工作：

| 参数 | 测试状态 |
|------|---------|
| `--conversation-type group` | ✅ 正确设置 `conversation_type="group"`，`group_id` 和 `group_name` 字段出现在 StandardMessage 中 |
| `--group-id` | ✅ 未指定时默认 `group_001`，数据库 `group_id` 字段正确写入 |
| `--no-at` | ✅ 传入时 `is_at_bot=False`，DomainTakeoverService 走入 `monitor_silent_collect` 分支 |

`--help` 输出三个新参数正确显示。

---

## 未覆盖场景

| 场景 | 状态 | 说明 |
|------|------|------|
| 群聊文件静默收集 | 未测 | emy-test 支持 `--file` 传附件，可与 `--conversation-type group --no-at` 配合测试附件静默下载 |
| observe 模式回归 | 未测 | observe 模式未改动，但未做回归确认 |
| collaborate 模式回归 | 未测 | collaborate 模式未改动，但未做回归确认 |
| 多用户群聊 | 无法测 | emy-test 一次仅模拟一个发送者，无法模拟多人在群内交替发言 |

---

## 结论

**静默收集 monitor 模式实现正确，通过全部 3 项设计场景测试。** 核心证据链完整：

1. DomainTakeoverService 决策正确（`takeover=True, should_reply=False`）
2. handle_message 静默返回（不触发 LLM，不发回复）
3. 消息正确持久化到 messages 表（`takeover=t`, `direction=user_to_agent`）
4. 无 agent_to_user 出站记录（确认 LLM 未被调用）
5. emy-test CLI 群聊参数更新可用

---

*本报告由 emy-test 生产实战测试生成。*
