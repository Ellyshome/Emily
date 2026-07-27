# 工具直调兜底 — 业务流服务手册

> **适用对象**：AI Agent（SessionAgent 路由 + SkillExecutor 执行）  
> **允许用户角色**：`all`（所有用户均可触发兜底流程）  
> **业务目的**：当用户请求无对应专属 SOP 时，由 SOP-999-SYS 从派生工具白名单中选择原子工具直调，完成用户任务  
> **文档风格**：地产企业内业文件管理规范，简洁明确，可执行  
> **特殊标记**：`__FALLBACK_SOP__`

---

## 1. 业务流版本信息

| 项目 | 内容 |
| --- | --- |
| 业务流编号 | SOP-999-SYS |
| 版本 | v2.0 |
| 权限控制 | `all` |
| 特殊机制 | **工具白名单运行时派生**：由 SkillRegistry._derive_sop999_tools() 在启动/重载时计算，公式为"全部 REGISTERED_TOOLS − 被专属 SOP 引用的 − exposure_mode=sop_only" |
| 匹配优先级 | **仅当用户请求明确指向工具能力且无专属 SOP 时路由**；模糊请求走 fallback 对话引导 |
| 参与编辑人 | 系统架构师、AI Agent 训练师 |
| 最后编辑人 | 系统管理员 |
| 最后编辑时间 | 2026-07-26 |
| 关联系统模块 | emily_core → SessionAgent 路由 / SkillRegistry 派生 / SkillExecutor __DYNAMIC__ 分支 / tool_registry exposure_mode |

---

## 2. 意图识别标准

### 2.1 触发此业务流的语义特征

**M4 路由规则（session.md 第 8 条）**：用户请求**明确指向**某个工具能力（如发文件/查文件/写记忆），且无对应专属 SOP → 路由到 sop_id="SOP-999-SYS"。

**两条边界**：
- 仅当请求**明确指向工具能力**时路由 SOP-999（如"帮我发个文件给张三""查一下最新的图纸""帮我记一下偏好"）
- 模糊请求（"帮我处理一下""帮我看看"）→ fallback=true 对话引导，不路由 SOP-999
- 元认知询问（"你能做什么""权限怎么分级"）→ fallback=true，不路由 SOP-999

### 2.2 示例对话

**应触发此业务流**：
> 「帮我发个文件给张三」
> 「查一下最新的图纸」
> 「帮我记一下：我的偏好是优先看施工组的内容」

**不应触发此业务流**：
> 「下午3点的周例会，帮我录进去」→ 有专属 SOP-001-REC，走会议纪要录入
> 「帮我处理一下」→ 模糊请求，fallback 对话引导
> 「你能做什么」→ 元认知询问，fallback 能力树回答

---

## 3. 业务流描述

### 3.1 工具白名单派生机制（M2）

SOP-999 不手写 tools 列表。每次 SkillRegistry.load/reload 时，`_derive_sop999_tools()` 自动计算：

```
白名单 = 全部 REGISTERED_TOOLS
       − 被其他 Skill YAML tools[].name 引用的工具
       − tool_registry 中 exposure_mode == 'sop_only' 的工具
```

**exposure_mode 默认安全分级（M1）**：
- permission_flag=all → exposure_mode=meta（只读工具可被 SOP-999 直调）
- permission_flag=write/admin → exposure_mode=sop_only（写/管理工具必须走专属 SOP）
- 破坏性工具（delete_file/discard_nodes/return_node_deliverable/unlink_attachment）显式标 sop_only

### 3.2 执行流程（__DYNAMIC__ 分支，M3）

```
用户请求 → SessionAgent 路由 → SOP-999-SYS
    ↓
[step-01: LLM 选工具 + 推参]
  - SkillExecutor._extract_sop999_tool_and_params()
  - 把派生白名单 + 各工具 schema 喂给主模型 LLM
  - LLM 输出 {tool_name, params}
  - schema 强校验：缺必填字段不执行，向用户询问
    ↓
[step-02: __DYNAMIC__ 直调]
  - SkillExecutor._execute_dynamic_step()
  - 校验 tool_name 在派生白名单 + Session 可见 API
  - 写类工具（permission_flag != all）走 confirm_callback 拟执行确认
  - 调用 BusinessFlowTool.handler()
  - 审计标记 trigger=sop999
    ↓
[回复用户]
  - 告知工具执行结果、系统编号
```

### 3.3 写操作确认（M3）

当 step-02 检测到所选工具的 permission_flag != all 时：
1. 框架调用 `ctx.confirm_callback(tool_name, params)` 
2. 若 confirm_callback 返回 false（用户取消）→ StepResult(success=False, output="用户取消执行")
3. 若无 confirm_callback → 拒绝执行（安全优先）
4. 仅 permission_flag=all 的只读工具可无需确认直接执行

---

## 4. 异常处理

| 异常场景 | 处理建议 |
| --- | --- |
| **白名单无合适工具** | step-01 LLM 返回 tool_name="" → 告知用户"该需求当前无可用工具，请走专属 SOP 或联系管理员" |
| **LLM 推参缺必填字段** | schema 强校验失败 → 不执行工具，向用户询问缺失参数 |
| **工具不在 Session 可见 API** | 用户权限不足 → StepResult(success=False, "工具不在 Session 可见 API 中") |
| **写操作用户取消** | confirm_callback 返回 false → StepResult(success=False, "用户取消执行") |
| **工具 handler 执行异常** | 捕获异常 → StepResult(success=False, "动态工具执行异常: {e}") |

---

## 5. 与元认知路径的边界

用户询问"你能做什么""权限怎么分级"时：
- session.md 路由规则第 7 条：直接基于能力树回答，fallback=true
- session.md 路由规则第 8 条边界：元认知询问不路由 SOP-999
- session_reply.md 注入 sop_catalog 到回复合成上下文

---

## 6. 变更记录

| 版本 | 日期 | 修改人 | 修改内容 |
| --- | --- | --- | --- |
| v2.0 | 2026-07-26 | 系统管理员 | SOP-999 从"对话引导 SOP"改造为"工具直调兜底"：tools 白名单运行时派生（_derive_sop999_tools）、__DYNAMIC__ 动态工具执行、exposure_mode 默认安全分级、写操作强制确认、路由触发约束 |
| v1.0 | 2026-06-14 | 系统管理员 | 初版发布，作为全面兜底业务流（旧架构：IntentRegistry + match_score=0.65） |
