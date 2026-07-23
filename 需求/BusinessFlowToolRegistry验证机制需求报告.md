# BusinessFlowToolRegistry 验证机制需求报告

> 本文档定义"为什么需要验证机制"以及"验证机制应该起到什么作用"，是需求规格说明，不含实施细节。实施计划待需求审核通过后另行编写。

---

## 1. 为什么要做这件事

### 1.1 BusinessFlowToolRegistry 的关键角色

BusinessFlowToolRegistry（内存工具注册表）是 Emily 工具调用的**权威数据源**。两条执行路径都依赖它：

- **兜底路径**（当前实际运行的主路径）：`_llm_plan` 从 `BusinessFlowToolRegistry.list_names()` 生成工具列表注入 LLM 规划 prompt → `_real_execute` 按 `plan.steps` 调 `BusinessFlowToolRegistry.get(tool_name).handler`
- **Skill 路径**（设计中的主路径，待启用）：SkillExecutor 从 `BusinessFlowToolRegistry.get(tool_name)` 获取 handler 执行

也就是说，**LLM 能看到什么工具、能调什么工具，完全由 BusinessFlowToolRegistry 决定**。它是工具调用的单一真相源。

### 1.2 发现的问题

通过近期排查，发现 BusinessFlowToolRegistry 周围存在多层断层和不一致，且**没有任何机制能在运行前发现它们**：

| 问题 | 现状 | 后果 |
|------|------|------|
| **注册静默失败** | `register_all` 的 `_reg_biz` 是 fail-safe——依赖注入失败（如 `event_app=None`）时只记 WARNING，工具静默缺失 | 工具可能悄悄没注册，运行时才发现"工具未注册" |
| **parameters 大多为空 schema** | `register_all` 注册 business 工具时传 `{"type":"object","properties":{}}` 空 schema，只有 knowledge_search 等少数传了真实 schema | LLM 规划时拿不到参数定义，参数提取靠猜 |
| **description 过时** | 曾发现"SOP §3.2 工具名与此注册表一一对应"等过时注释（已清理），description 本身也可能与 handler 实际行为脱节 | LLM 依据错误 description 误选工具 |
| **Skill YAML 不一致** | 诊断发现 10 个 Skill 中 7 个有问题，24 处不一致（工具名不存在 + 参数 schema 不匹配） | Skill 路径一旦启用，工具调用大面积失败 |
| **tool_registry 表空** | `register_api.py --all` 从未执行，表 0 行 | SkillExecutor 的 `session_api_ids` 检查阻断所有工具（被 /app/skills 没挂载掩盖） |
| **Skill 机制未接线** | /app/skills 没挂载 → SkillRegistry 空 → sop=None → 兜底路径承压 | 设计主路径（Skill）从未生效，全靠 LLM 兜底 |

### 1.3 核心风险

BusinessFlowToolRegistry 作为权威数据源，却**没有任何验证机制**保障其一致性。当前能"运行"纯属侥幸——靠兜底路径的容错（LLM 凭工具名+description 猜）和多个断层互相掩盖（/app/skills 没挂载掩盖了 Skill YAML 错误和 tool_registry 表空）。

即将进行的工作会让 Skill 机制真正启用（挂载 /app/skills、修 Skill YAML、填 tool_registry 表）。**如果没有验证机制，这些修复完成后仍可能残留不一致**，且后续每次工具变更（新增工具、改 schema、改 Skill YAML）都可能引入新问题，无法及时发现。

### 1.4 时机

现在是建立验证机制的**最佳时机**，因为：

1. **修复前**：验证机制能量化当前问题规模（诊断脚本已证明有效——100 行发现 24 处不一致）
2. **修复中**：验证机制提供即时反馈，修一处验一处
3. **修复后**：验证机制成为回归保障，防止后续变更引入新不一致
4. **运行时**：启动自检能在容器启动时发现致命问题，避免带病运行

如果先修复再建验证机制，修复过程中无法量化问题、无法验证修复完整性，且修复后没有回归保障。

---

## 2. 验证机制应该起到的作用

### 2.1 核心目标

**确保 BusinessFlowToolRegistry 作为工具调用权威数据源的一致性和可用性，在问题影响运行时之前发现并报告。**

### 2.2 应起到的作用

#### 作用一：注册完整性与可用性验证

验证 `register_all` 注册的结果是否符合预期：

- **工具未缺失**：所有应该注册的工具都注册成功（没有被 fail-safe 静默吞掉）
- **handler 可调用**：每个注册的工具 handler 是 callable，依赖注入完整
- **元数据完整**：name / description / parameters / category / permission_flag 都非空且合法
- **无重复注册**：register 的 fail-fast（重名抛 ValueError）没有被 try-except 静默

**预期效果**：启动时就能发现"工具没注册上"或"handler 依赖为 None"的问题，而不是运行时才报"工具未注册"。

#### 作用二：schema 准确性验证

验证 `BusinessFlowTool.parameters` 是否真实反映 handler 接受的参数：

- **非空检查**：business 类工具的 parameters 不应是 `{"type":"object","properties":{}}` 空 schema（当前通病）
- **参数对齐**：schema 定义的参数与 handler 函数签名的参数一致（或 schema 是 handler 参数的子集）
- **required 合理性**：required 字段确实在 handler 里被强校验

**预期效果**：LLM 规划和 Skill 参数提取时能拿到准确的参数定义，而非空 schema 让 LLM 靠猜。

#### 作用三：description 准确性验证

验证 `BusinessFlowTool.description` 是否与 handler 实际行为一致：

- **非空**：description 不能为空
- **不过时**：description 不应引用已废弃的概念（如已清理的"SOP §3.2"）
- **与 Skill YAML 声明不矛盾**：同一工具在 BusinessFlowTool.description 和 Skill YAML tools[].description 两处的描述不应冲突

**预期效果**：LLM 依据准确的 description 选择工具，避免"描述说做 A，实际做 B"的误选。

#### 作用四：跨层一致性验证

验证 BusinessFlowToolRegistry 与其消费方/声明方的一致性：

- **Skill YAML 工具名存在性**：Skill YAML 的 `tools[].name` 和 `steps[].tool_name` 必须在 BusinessFlowToolRegistry 里
- **Skill YAML 参数匹配**：Skill YAML 的 `steps[].tool_params` 参数名必须在对应工具的 parameters schema 里
- **tool_registry 表同步**：DB 的 tool_registry 表记录与 BusinessFlowToolRegistry 内存注册的工具集合一致（哪些注册了没入库、哪些入库了没注册）

**预期效果**：Skill 路径启用前能发现所有 Skill YAML 不一致；tool_registry 表空或与内存不一致能被发现。

#### 作用五：启动时 fail-fast 信号

在容器启动时（`register_all` + `SkillRegistry.load` 之后）自动运行验证，输出诊断信号：

- **致命问题**（如工具 handler 为 None、必需工具未注册）：记 ERROR 日志 + 写入启动邮件快照
- **警告问题**（如空 schema、Skill YAML 参数不匹配）：记 WARNING 日志
- **不阻断启动**：验证机制本身 fail-open，不因为发现问题而阻止容器启动（避免验证机制故障导致服务不可用），但问题必须可见

**预期效果**：每次启动都能看到工具一致性健康度，启动邮件的 SOP 统计等指标能反映真实状态，而不是"0"被掩盖。

### 2.3 验证时机

| 时机 | 形态 | 目的 |
|------|------|------|
| **启动时** | `register_all` 和 `SkillRegistry.load` 后自动自检 | fail-fast 信号，写入启动日志/邮件 |
| **手动诊断** | `scripts/check_tools_consistency.py` 独立脚本 | 开发/运维主动全量检查，输出详细报告 |
| **变更后** | 修改工具/Skill YAML 后运行诊断脚本 | 回归验证 |

### 2.4 预期输出

验证机制应输出结构化报告，包含：

1. **健康度摘要**：注册工具数 / 有 schema 数 / 一致性通过率
2. **问题清单**：按严重级别（致命/警告）分组，每条含：
   - 问题类型（注册缺失 / 空 schema / 参数不匹配 / 工具名不存在 / ...）
   - 涉及的工具/Skill/step
   - 具体描述
   - 修复建议
3. **退出码**：致命问题 → 非零（供脚本/CI 判断）；仅警告 → 零

### 2.5 不应做的事（边界）

- **不阻断启动**：验证机制 fail-open，发现问题只报告不阻断（避免验证机制本身故障导致服务不可用）
- **不替代权限层**：工具调用的权限检查仍由 PermissionAuthEngine / `ToolRegistryRepo.get_available` 负责，验证机制只管"一致性"不管"权限"
- **不自动修复**：只诊断报告，不自动改 schema/Skill YAML（自动修复风险高，应由人工判断）
- **不引入运行时开销**：启动自检只在启动时跑一次，不在每条消息处理时跑

---

## 3. 验证项概览（需求规格）

以下为验证机制应覆盖的检查项清单，具体实现方式由实施计划决定：

### 3.1 注册完整性
- V1：register_all 注册的工具数 ≥ 预期（base 2 + business 12 + project 13 = 27）
- V2：每个工具 handler 是 callable
- V3：每个工具 name/description 非空
- V4：无重名注册被静默吞掉

### 3.2 schema 准确性
- V5：business 类工具 parameters 非空 schema（不是 `{"properties":{}}`）
- V6：schema 的 properties 参数名与 handler 签名参数对齐
- V7：required 字段在 handler 里有对应处理

### 3.3 description 准确性
- V8：description 不含已废弃概念（SOP §3.2 等）
- V9：description 与 Skill YAML tools[].description 不矛盾

### 3.4 跨层一致性
- V10：Skill YAML tools[].name 在 BusinessFlowToolRegistry 里
- V11：Skill YAML steps[].tool_name（非 null）在 BusinessFlowToolRegistry 里
- V12：Skill YAML steps[].tool_params 参数名在对应工具 schema 里
- V13：tool_registry 表的工具集合与 BusinessFlowToolRegistry 一致

### 3.5 启动信号
- V14：启动邮件快照含工具一致性健康度（注册数 / 有 schema 数 / 一致性通过率）
- V15：致命问题记 ERROR 日志

---

## 4. 成功标准

验证机制建成后的成功标准：

1. **能发现已知问题**：跑诊断脚本能复现当前 24 处 Skill YAML 不一致 + tool_registry 表空
2. **启动可见**：容器启动日志/邮件能反映工具一致性健康度（不再是"0 skills"被静默掩盖）
3. **回归保障**：后续修改工具或 Skill YAML 后，跑诊断脚本能立即发现新引入的不一致
4. **不阻断**：验证机制故障不影响容器启动和消息处理
5. **可独立运行**：`scripts/check_tools_consistency.py` 能脱离容器独立运行（读 emily-core 代码 + Skill YAML + tool_registry 表）

---

## 5. 与其他工作的关系

| 相关工作 | 关系 |
|---------|------|
| Skill YAML 修复（见 `需求/Skill_YAML一致性修复计划.md`） | 验证机制是修复的**验收工具**——修完后用诊断脚本确认 0 不一致 |
| 挂载 /app/skills（修复断层 A） | 验证机制应在挂载**之前**就位，确保挂载后能立即发现 Skill 路径的问题 |
| 填充 tool_registry 表 | V13 检查能发现表与内存的不一致，指导 register_api.py 的执行 |
| maxkb_provider.py 修复（踩坑 6.3/6.4） | 无直接关系，但同属"基座工具健壮性"主题 |

---

## 6. 附录：现状基线

建立验证机制前的现状基线（供建成后对比）：

| 指标 | 现状 |
|------|------|
| register_all 注册工具数 | 27（base 2 + business 12 + project 13） |
| 有真实 schema 的工具数 | 21/27（6 个无 schema 常量：write_user_memory + node_task 5 个） |
| business 类空 schema 工具数 | 12/12（全部空 schema） |
| Skill 文件数 | 10 |
| Skill YAML 不一致处 | 24（工具名 12 + 参数 12） |
| tool_registry 表记录数 | 0 |
| SkillRegistry 加载 Skill 数 | 0（/app/skills 没挂载） |
| 启动邮件 SOP 统计 | 0（因 SkillRegistry 空） |
