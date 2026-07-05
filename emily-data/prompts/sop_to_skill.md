你是一个 SOP 到 Skill 的转换器。请将以下 SOP 文档转换为 Skill 定义 YAML。

## 输出格式要求

必须输出完整的 YAML 文档，包含以下五个顶层字段（缺一不可）：

```yaml
skill_id: <与文件名一致，如 SOP-002-REC-event-record>
sop_id: <SOP 编号，如 SOP-002-REC>
version: "1.0"
display_name: <中文业务名称，如 事件记录>
instructions: |
  <给 AI 的执行指引，从 SOP §3.3 和 §5 提取>
tools:
  - name: <工具名>
    description: <工具功能描述>
steps:
  - id: step-01
    description: <步骤描述>
    tool_name: <工具名或 null>
    tool_params: {}
    output_key: <输出键名>
```

## 关键规则

### tool_params 格式（必须是 dict，不是 list！）

```yaml
# ✅ 正确格式：参数名作为 key
tool_params:
  title:
    source: user_input
    extraction: 事件标题
    required: true
  event_date:
    source: fixed
    value: today

# ❌ 错误格式：不要用列表！
tool_params:
  - name: title
    source: user_input
```

### source 四种取值

| source | 用途 | 必填字段 |
|--------|------|----------|
| user_input | LLM 从用户消息提取 | extraction |
| prev_step | 从前步结果取值 | path（dot-path） |
| fixed | 固定值（today=今天） | value |
| context | 从 session-context 取值 | path（如 project_name） |

### 其他规则

- 不输出 datasets 段
- 步骤间有依赖时，用 output_key + prev_step 链接
- source=context 用于从 session-context 获取运行时数据（如 project_name, user_id）
- 纯逻辑步骤（意图判定、展示确认单等）tool_name 设为 null
- 每个步骤必须完整输出，不要省略
- 必须输出 steps 段，至少包含一个最终调用工具的步骤

## SOP 文档内容

```
{sop_text}
```
