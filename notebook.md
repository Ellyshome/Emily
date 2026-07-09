
---

# 日志系统
需要收束



---
灾备：本地AI推理服务：llama.cpp server + 4B QWEN

---
多通道接入，微信、微信小程序、企业微信


----
观察整个emily项目，是否还有断线、不连通、mock类实现的情况：
##  高风险问题（必须关注）
# 问题 位置 当前状态 风险说明
 1 鉴权默认 MOCK config.py#L196 auth_mode="mock" 生产环境默认关闭鉴权！ 需手动配置为 real 
 2 风险分级未实现 config.py#L199 risk_mode="mock" RiskGrader 只有接口定义，无真实逻辑 
 3 三维鉴权未接入主链路 workitem_agent.py#L562 authorize() 方法 无调用者 PermissionAuthEngine 的密级/企业/部门/节点检查 全部未生效

 ---

 project的思考
 1、本质是服务器运维辅助AI工具；
    - 它是个潜伏在服务器里的独立完整的agent，甚至可以是带有claude壳的完整AI对话工具，现在甚至考虑是否独立与emily之外单独成组
    - session是会话级agent权限不宜扩展至全局。管理员应该如何获取全局状态呢？是否还需要拉起一组系统状态的monitor。让session获取信息
 2、用于session的提示词生成；session应该对本项目有基本认知，这个认知不应是一个固定的提示词，希望可以根据项目的推进，自动更新提示词
 3、现有的全局状态机与调度，应该大概可以完成当前的任务了吧，可能需要判断的时候，临时拉起llm帮助决策就可以了。

 ---
# 信息投递，活跃途径投递

 ---

# 关于上传完工确认报告的处理策略：
- 有所属节点，有计划任务归属的；直接入库，只是单独设个‘是否验收确认’字段。有节点管理员负责验收任务
- 计划任务，发起时，需填写节点归属。暂时无对应归属节点，应引导用户提醒所属建设单位对应的部门负责人建立节点。
- 虚拟节点；暂时无对应归属节点，（如节点还在定制申报中，）统一归入虚拟节点，等待二次分配。
   - 虚拟节点中有数据被视为异常数据，作为管理员需处理信息
---

实现 recent_turns 写入 — SessionAgent.handle() 末尾把本轮交互追加进 self.context.recent_turns，到 pipeline 层拼接成 messages 传给 chat_with_tools()
实现会话冷启动灌注 — 你打开的 session灌注.md 里列的：项目元认知世界书、工具列表、数据库 schema，在 SessionFactory._build_context() 里补全
TTL 过期时调 archive() — sweep_expired() 改掉静默丢弃的行为

---
session 的实现
需要有类作为操控抓手
需要有上下文连贯性
需要有归档脚本
---
session
注销脚本、数据库查询脚本、rag查询脚本
---
对脚本emily-core\emily_core\adapters\session\session_factory.py
进行系统化改造
---
message的附件，没有接入自动下载流程，
文件没有统一管理，应该统一下载，只给一个message ID
---
计划任务与节点的关系
节点必备产出成果列表，计划任务必须绑定成果ID
计划任务必须绑定：节点ID+成果项ID
成果项分两种，
   - 一种是定量成果，如铺装 500平米。成果提交需携带定量数据，佐证文件（施工照片，监理验收照片等），且定量数据总和不大于成果预设总量，也就是任务上传需要多一层数据核验的步骤，超过阈值则标注条目异常
   - 一种是非定量成果，如景观方案本册1、景观方案本册讨论稿、仅提交成果文件，不舍总量上限

---
session资源可见范围，
---
sop的结构需要改动，当前都只有一个md文件，它本质是一个具备多轮交互补全信息能力的skill，它应有面向AI工具的‘说明文档’、‘工具脚本’、‘数据yaml表’，需要有一个具备llm推理辅助的解析器，能将现有的sop文件解析出这三个表。
---
session对访问权限的管理策略：

session权限范围的定义：分数据和动作两部分。不用鉴权，用可访问白名单策略管理。
session需要有个脚本，使用用户ID 来 更新 自身权限范围（更新session_context的对应字段。启动时默认自执行一次）。
用户可访问文件是根据自身所处的公司所参与的节点来圈定可见范围的。可执行动作（脚本）是根据sop-skill列表限制的。
---
自成长核心
一个agent智能体。赋予见习项目经理人格，每日阅读新增信息，制作学习笔记。沉淀方法论、行业知识。提出改进建议。
---
当前开发的模块，尽量独立功能脚本能做成：
   - 可被系统调用
   - 可独立执行，作为运维工具
---
查询sops-kill分层
需要提供基本的文件结构、数据库-schema。
查询应通过脚本实现，隐藏查询语句，
---
提供群id灌注
---
Session 原子化能力重构（2026-07-05）
- 新增 tool_registry 表：统一记录系统 API 元数据（ID/签名/一句话说明/分类/权限）
- 新增 session_accessible_files 表：用户→文件可见关系（sync_for_user 自动授权）
- SessionContext 新增 6 个原子化能力字段（available_tools / visible_schema / files / RAG）
- SkillExecutor 白名单软化：from Skill 声明 → Session 可见 API 集合
- 元能力独立执行路径：不在 Skill YAML 的工具，只要 Session 可见即可调用（LLM 动态推导参数）
- 新增 search_files 工具：关键词搜索可见文件（双用途：API + 独立脚本）
- 新增 register_api.py：API 注册器（代码注册 + DB 录入）
- Prompt 模板新增"你的元能力"段落，告知 LLM 可用工具、可查数据库、可访问文件
- 详见 需求文件/重构session工具-技能/实施报告.md

---
uv run python scripts/collect_session_data.py chenzhe-jyzx-2026-0001


# 全量 Session 数据（从项目根目录）
uv run python scripts\fetch_session_data.py chenzhe-jyzx-2026-0001
# 子模块独立运行（从 emily-core 目录）
cd emily-core
uv run python -m emily_core.session.fetchers.fetch_available_tools --user-id chenzhe-jyzx-2026-0001
uv run python -m emily_core.session.fetchers.fetch_visible_schema --user-id chenzhe-jyzx-2026-0001
uv run python -m emily_core.session.fetchers.fetch_rag_info

# available_tools 
docker exec emily-core python -m emily_core.session.fetchers.fetch_available_tools --user-id chenzhe-jyzx-2026-0001
# visible_schema
docker exec emily-core python -m emily_core.session.fetchers.fetch_visible_schema --user-id chenzhe-jyzx-2026-0001
# fetch_rag_info
docker exec emily-core python -m emily_core.session.fetchers.fetch_rag_info --user-id chenzhe-jyzx-2026-0001
---
资料分黑白名单双轨制
非建设单位人员实行白名单制，根据参与节点划定可见范围，
建设单位人员实行黑名单制，划定范围，对非管理员级别隐藏，主要是方便以后财务预算介入。当前黑名单为空，需要有财务再生效

---
# 冷启动自检，都检查什么：
是否可启动：
是否有项目：表projects中是否存在一条字段is_main值为true的数据。
是否有主管理员：表users是否存在一条字段level的值为‘系统管理员’
是否有参见单位：表company_info中是否存在一条字段 is_admin 的值为真。
是否有全景计划节点：节点数大于3
全景计划节点
是否有项目编号：

# 是否有基本项目文件：
土地出让合同 = 拿地约定（债权）
不动产权证 = 地块权属（物权）
规划两证（用地 + 工程）= 允许开发楼盘
立项备案 = 官方登记有这个开发项目




冷启动，拉起项目自我认知，一个类project_context包含：
项目编号
项目名称
项目地址
项目描述

---
调度机启动scheduly

---
# 项目初始化模块。问题


全景节点，至少有一个节点，是项目交付，列出项目交付所需提交的成果，都需所有成果都从这个总节点中领取成果作为子节点延伸。
冷启动时

---
