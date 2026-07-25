# LLM 调用链路优化需求

> 基于 2026-07-25 llm_trace 抓包分析，一次用户问答产生 5 次 LLM API 调用 / 12,759 tokens / ~1500 行通讯记录。

---

## 一、现状

### 当前流水线

```
用户提问 → ① 意图路由(router) → ② 执行规划(planner) → ③ 步骤审核(auditor) → ④ 回复合成(composer) → ⑤ 最终审核(auditor)
            deepseek-v4-pro        deepseek-v4-pro        deepseek-v4-flash      deepseek-v4-pro         deepseek-v4-flash
            4669 tokens            1410 tokens            2231 tokens            2095 tokens             459 tokens
```

### 问题场景

用户提问"9米的道路转弯半径是否符合消防要求？"（纯知识问答），走完了完整 5 步流水线。最终回复引用的规范条文（GB50016）来自模型自身知识，流水线中间步骤的 RAG 检索结果完全不相关且未被使用。

---

## 二、改进项

### P0-1：知识问答快速通道

**现状**：[session_agent.py](file:///d:/app/Emily/emily-core/emily_core/session/session_agent.py#L204-L239) 中，所有非短路消息走 `_split_into_workitems → PipelineBUS → M4` 全流程。router 返回 `fallback=true, intent=knowledge_qa` 时仍进入 4 节点 BUS。

**方案**：在 `_handle_impl` 中增加快速通道判断。当意图识别返回 `fallback=true` 且 intent 为纯知识问答类型、无需工具调用时，跳过 PipelineBUS，直接走轻量回复。

**关键文件**：
- `emily-core/emily_core/session/session_agent.py` — `_handle_impl()` 方法

**预期效果**：知识问答从 5 次调用降为 2 次（路由 + 回复），省掉 planner、auditor×2 共 3 次调用，tokens 减少约 60%。

---

### P0-2：RAG 检索结果 score 阈值过滤

**现状**：[knowledge_search_tool.py](file:///d:/app/Emily/emily-core/emily_core/tools/knowledge_search_tool.py#L93-L123) 中，`handle_knowledge_search` 直接返回 pgvector 的 top_k 结果，不检查相关性。本次 trace 中查询"消防车道转弯半径要求 9米"，返回了"生命周期节点台账""消防验收办理时限"等无关片段（最高 score 0.66）。

**方案**：在返回前增加 score 阈值过滤，过滤后 chunks 为空时标记 `total=0`，让后续步骤知道"知识库无相关结果"。

**关键文件**：
- `emily-core/emily_core/tools/knowledge_search_tool.py` — `handle_knowledge_search()` 函数

**预期效果**：无关 RAG 结果不再污染后续步骤的 prompt，每步骤节省约 200-500 tokens。

---

### P1-1：历史压缩阈值降低

**现状**：[session_context.py](file:///d:/app/Emily/emily-core/emily_core/session/session_context.py#L319-L323) 中，`message_history` 超过 40 条（20 轮）才触发 `compress_overflow`。本次 trace 中 10 轮对话（20 条）未达阈值，**全部 5 次 LLM 调用都原样携带了与当前问题无关的 10 轮历史**。

**方案 A（低风险）**：阈值从 40 降到 20，并在每轮开始时检查压缩。

**方案 B（更优）**：引入语义相关性过滤。用轻量方式判断每轮历史与当前问题的相关性，不相关的轮次合并为摘要。

**关键文件**：
- `emily-core/emily_core/session/session_context.py` — `compress_overflow()` 方法
- `emily-core/emily_core/session/session_agent.py` — `handle()` 方法（调用点）

**预期效果**：每次 API 调用 prompt tokens 减少约 50-70%。

---

### P1-2：最终审核按风险等级条件化

**现状**：`_review_final_reply` 在每次 M4 合成后无条件调用 LLM 审核。L1 低风险的简单知识问答也触发审核，结果为空列表。

**方案**：最终审核只在 L2/L3 风险等级时执行。同理 node3 的 step auditor 对 L1 风险且仅有 RAG 返回（无实际工具调用）的步骤可跳过。

**关键文件**：
- `emily-core/emily_core/session/session_agent.py` — `_synthesize_final_reply()` 方法
- `emily-core/emily_core/workitem/workitem_agent.py` — `node3_execute()` 方法

**预期效果**：L1 风险场景省去 1-2 次审核调用。

---

### P2：Router 系统 Prompt 分层

**现状**：router（`_recognize_intent`）的系统 prompt 包含完整"三书体系"（项目世界书 + 规则书 + 认知书），约 290 行。但"规则书"中"删除需二次确认""错误处理降级"等内容与意图路由完全无关。

**方案**：为 router 构建精简版 prompt，只保留意图路由必需的信息：
- 角色定义
- 业务类型树（SYS/REC/FILE/QRY）
- 当前用户身份 + 项目名称（各一行）
- 输出格式要求

完整规则书、认知书、权限分级细节等在 planner/executor 阶段需要时再注入。

**关键文件**：
- `emily-core/emily_core/session/session_agent.py` — `_recognize_intent()` 方法
- `emily-data/prompts/session.md` — 路由 prompt 模板

**预期效果**：router prompt 从 ~290 行压缩到 ~80 行，prompt_tokens 从 4669 降到约 1500-2000。

---

## 三、优先级与收益汇总

| 优先级 | 改进项 | 开发量 | 预期减少调用 | 预期减少 tokens |
|--------|--------|--------|-------------|----------------|
| **P0** | 知识问答快速通道 | 中 | 3 次 | ~60% |
| **P0** | RAG 结果 score 过滤 | 小 | 0 | ~30%（涉及步骤） |
| **P1** | 历史压缩阈值降低 | 小 | 0 | ~50%（每调用） |
| **P1** | 最终审核条件化 | 小 | 1 次 | ~5% |
| **P2** | Router prompt 分层 | 中 | 0 | ~40%（路由调用） |

全部落地后，知识问答类场景预期从 **5 次调用 / 12,759 tokens** 降至 **2 次调用 / ~3,500 tokens**。
