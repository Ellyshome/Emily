# Guardian 架构审视：功能定位、替代可行性与纵深防御

> **生成日期**: 2026-06-30  
> **讨论触发**: 评估认为项目处于 Phase B/C 未完成状态，Guardian 是 Phase C 最大缺口  
> **核心问题**:  
> 1. Guardian 的功能能否由 SessionAgent 替代？  
> 2. 全景节点图模式下，防越权检查是否不再必要？

---

## 一、Guardian 功能定位

### 1.1 两种审核模式

| 模式 | 时机 | 接口方法 | 触发节点 | 三态决策 |
|------|------|----------|----------|----------|
| **陪跑模式**（in-stream） | Node 3 执行中，**每步** StepResult 产出后即时审核 | `review_step(step_result, plan_step, criteria)` | `node3_execute` | `PASS` / `FLAG` / `REJECT` |
| **出站模式**（outbound） | Node 4 组装完最终回复后，**发送前**审核 | `review_reply(draft_reply, context)` | `node4_summary` | `PASS` / `FLAG` / `REJECT` |

### 1.2 三态决策语义

```
PASS   → 直接放行
FLAG   → 放行但追加 "⚠️ Emily 提醒：以上回复建议复核" 警告
REJECT → 拦截，替换为兜底回复
```

### 1.3 在 Pipeline 中的嵌入位置

```
node3_execute:                      node4_summary:
  for each StepResult:                draft = 组装回复文本
    _real_execute(step)               │
    │                                 ├─ guardian.review_reply(draft)
    └─ guardian.review_step(sr)  ←──       │
       ├─ PASS → 继续                    ├─ PASS → 发送 draft
       ├─ FLAG → 继续（标记）           ├─ FLAG → draft + 警告后缀  
       └─ REJECT → 理论拦截              └─ REJECT → 替换为兜底回复
```

### 1.4 功能定位总结

Guardian 的角色不是"执行者"，而是**最后的看门人**：

1. **防 LLM 幻觉输出**：检查回复中是否有虚构的事实、错误的编号、不存在的项目名
2. **防越权操作**：检查执行步骤是否超出了用户的权限范围
3. **防逻辑矛盾**：检查回复是否与上下文矛盾（如对已取消的任务说"已完成"）
4. **敏感信息过滤**：是否泄露了不该暴露的数据

### 1.5 当前状态：完全 Mock

```python
# workitem_agent.py:264
if guardian_mode == "real":
    logger.warning(
        "guardian_mode=real 暂未实现 RealGuardian，回退到 MockGuardian"
    )
```

- 配置默认值 `guardian_mode = "mock"`
- 仓库中不存在 `RealGuardian` 类
- `MockGuardian` 两个方法都直接 `return GuardianVerdict.PASS`，不做任何实际检查
- 旧版储备：`agent/guardian_agent.py`（~500行深度审计 ReAct）和 `agent/guardian_review.py`（~200行轻量单次 LLM 验证），均已冷备

---

## 二、问题一：Guardian 能否由 SessionAgent 替代？

### 2.1 SessionAgent 当前职责

```
SessionAgent
├── ① 快回检测 (_try_fast_reply)
├── ② LLM 意图识别 (_recognize_intent)
├── ③ WorkItem 拆分 (_split_into_workitems)
├── ④ 入队执行 (scheduler.run_all)
├── ⑤ 待确认队列 (_collect_pending_confirms)
├── ⑥ 回复聚合 (_reply: "\n\n".join)
└── ⑦ 会话归档 (archive)
```

### 2.2 逐函数分析

| Guardian 函数 | 时机 | 当前嵌入位置 | 能否并入 SessionAgent？ |
|:--|:--|:--|:--|
| `review_step` | Node 3 执行中，每步产出后即时审核 | `WorkItemAgent.node3_execute` | **不应并入**——这是执行层内部的质量检查，SessionAgent 根本看不到 StepResult 的中间态。硬塞进去会破坏分层 |
| `review_reply` | Node 4 组装最终回复后，发送前审核 | `WorkItemAgent.node4_summary` | **可以讨论**——SessionAgent 已经在做回复聚合，出站审核本质是对聚合后的文本做最终检查，这个位置 SessionAgent 天然可达 |

### 2.3 架构判断：`review_reply` 并入 SessionAgent 看似自然，但是设计退步

当前链路：

```
SessionAgent._reply()
  ↑
  └── WorkItemAgent.node4_summary()
        ├── 组装 draft
        ├── guardian.review_reply(draft)  ← 独立审核
        └── wi.result_text = 审核后文本
```

如果并入 SessionAgent：

```
SessionAgent._reply()
  ├── 聚合 replies
  ├── review_reply(merged)  ← 自我审核
  └── 返回
```

**这破坏了独立的"看门人"模型**。Guardian 的设计意图是：**审核者不能是产出者**。SessionAgent 负责编排和聚合，如果让它同时审核自己的聚合结果，就失去了独立校验的意义——编排者对自己产出的文本天然有盲区。

### 2.4 替代方案结论

| 函数 | 建议 |
|:--|:--|
| `review_step`（陪跑） | **不应动**——属于 WorkItemAgent 执行层内部，SessionAgent 够不到 |
| `review_reply`（出站） | **技术上可行，架构上不推荐**——SessionAgent 已经在聚合回复，加一层自检看似减组件，实则让"裁判"和"运动员"合体。更好的路径是**实现 RealGuardian**，而非消解 Guardian |

---

## 三、问题二：全景节点图模式下，防越权检查是否不再必要？

### 3.1 当前节点图的可见性现状

`node_repo.py` 的查询方法没有任何用户级过滤：

```python
# 实际情况 —— 只有项目级隔离，无用户级过滤
def find_by_project(project_id, status=None, limit=200):
    q = session.query(ProjectNode).filter(
        ProjectNode.project_id == project_id,   # ← 只有项目级隔离
        ProjectNode.is_discarded == False       # ← 只过滤已废弃
    )

def get_by_node_id(node_id, project_id=None):
    q = session.query(ProjectNode).filter(
        ProjectNode.node_id == node_id,          # ← 无用户/权限过滤
        ProjectNode.is_discarded == False
    )

def find_by_owner(owner_dept_id, project_id=None):
    q = session.query(ProjectNode).filter(
        ProjectNode.owner_dept_id == owner_dept_id  # ← 按部门筛选，但调用方可传任意值
    )
```

**结论**：节点图有 `project_id` 多项目隔离，但没有任何"用户 X 只能看到自己公司/部门的节点"机制。`related_company_id` 和 `owner_dept_id` 是节点属性（用于业务标注），不是访问控制字段。

### 3.2 当前 AuthHook 的实际工作

```python
# hook.py AuthHook.execute() —— 挂在 before:wi_node1 和 before:wi_node2
① system.execute → 要求 permission_level >= 5（L5+管理员）
② SOP 有绑定时 → 检查 sop_id 是否在 perms.sop_allow 白名单中
③ 无用户 ID 时 → 仅允许 read 操作
```

这检查的是**操作权限**（能不能执行某个 SOP / 写某个资源），不是节点可见性。和全景节点图是两个独立维度。

### 3.3 全景节点图能否替代防越权？

| 维度 | 节点图能做吗？ | 现状 |
|:--|:--|:--|
| 项目级隔离 | ✅ `project_id` 过滤 | 已实现 |
| 公司/部门级隔离 | ⚠️ 节点有 `related_company_id` / `owner_dept_id` 字段但**未用于权限过滤** | 字段存在，逻辑缺失 |
| SOP 操作权限 | ❌ 不是节点图该管的 | AuthHook 负责 |
| 资源级行级安全 | ❌ repo 查询无 user_id 过滤 | 未实现 |
| 写操作鉴权 | ❌ `create_node` 接收 `creator_id` 但不校验 | 信任调用方 |

### 3.4 结论

**防越权检查仍然必要，且全景节点图目前无法替代它。** 原因：

1. **节点图管的是"节点之间的依赖关系"，不管"谁能看/谁能改"**——这是两个正交的领域模型
2. 节点 repo 查询**完全没有用户维度的过滤**，`project_id` 只是多租户最粗粒度的隔离
3. AuthHook 检查的是 SOP 操作权限（能否调 `record_event` / `record_task` 等），这些操作的对象（事件、任务）不在节点图中
4. 即便未来在 repo 层加上行级安全（基于 `grouping` / `company` / `permission_level`），那也是**数据访问层的过滤**，和 Guardian 的**输出审核**是两个不同的防护层

---

## 四、综合判断：纵深防御三层模型

```
防越权 ≠ 节点可见性 ≠ 输出审核

三者是纵深防御的三个独立层：

L1: AuthHook (before node1/node2)  → "你能不能做这个操作"
L2: 节点图可见性 (repo 层)         → "你能看到哪些节点" (目前缺失)
L3: Guardian 输出审核 (node3/node4) → "你的输出有没有幻觉/泄密/矛盾" (目前 Mock)
```

| 层 | 当前状态 | 优先级 |
|:--|:--|:--|
| L1 AuthHook | ✅ 代码就绪，`auth_mode=mock` | 可随时切换 real（需 SOP 白名单数据完备） |
| L2 节点可见性 | ❌ repo 层无用户级过滤 | P2——需在 repo 查询中注入 `grouping`/`company` 过滤条件 |
| L3 Guardian | ❌ 完全 Mock，无 RealGuardian 实现 | **P0**——当前安全体系最大缺口 |

---

## 五、建议

1. **保留 Guardian 的独立架构定位**——不应消解到 SessionAgent 或 AuthHook 中
2. **优先实现 RealGuardian**（至少 `review_reply` 出站模式）——基于旧版 `GuardianReview`（~200行轻量单次 LLM 验证）接入，而非从零重写
3. **节点图可见性作为独立任务**——在 repo 查询方法中加入用户级过滤，不应与 Guardian 混为一谈
4. **AuthHook 切换 real 的前提**——SOP 白名单数据（`sop_allow` 字段）需要在用户数据中实际填充
