📊 数据库资源（你有权访问的表）
· 事件表: 记录项目现场发生的事件
  核心字段：event_type(文本,必填) / project_id(FK→projects) / title(文本,必填) / event_date(文本)
· 会议表: 记录会议安排和纪要
  核心字段：project_id(FK→projects) / title(文本) / created_by(FK→users) / meeting_type(整数) / meeting_date(文本)
· 文件表: 项目文件归档
  核心字段：project_id(FK→projects) / filename(文本,必填) / file_type(文本) / uploaded_by(FK→users) / confidentiality(整数)
· 任务表: 跟踪工作任务的执行情况
  核心字段：project_id(FK→projects) / title(文本,必填) / status(文本)
（另有 56 张系统表，共 60 张）
🔗 表间关系
events.project_id → projects.id / tasks.node_id → project_nodes.node_id / files.project_id → projects.id / files.uploaded_by → users.id / users.company → companies.id

📁 文件分类体系
项目证照 / 承包合同 / 工作记录 / 阶段成果 / 过程文件 / 管理规程 / 其他文件
文件访问规则：confidentiality=0 公开文件所有用户可见；confidentiality≥1 内部/机密/绝密文件按权限级别控制；上传人始终可查看自己上传的文件

🔐 权限分级体系
参建线: L1 访客 → L2 参建执行 → L3 参建管理
建设线: L1 访客 → L4 建设主管 → L5 管理员 → L6 系统管理员
⚠️ L4 不继承 L2/L3，两线在 L1 后分叉；跨线访问需临时/永久授权