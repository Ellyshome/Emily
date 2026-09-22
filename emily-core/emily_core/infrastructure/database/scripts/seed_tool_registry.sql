-- 工具注册表种子数据
-- 执行: docker exec -i emily-postgres psql -U emily -d emily < seed_tool_registry.sql

INSERT INTO tool_registry (id, signature, display_name, category, permission_flag, exposure_mode, handler_module, is_active, registered_at, updated_at) VALUES
-- base（全部用户可用，permission_flag=all → exposure_mode=meta）
('query_data',         '{}', '查询企业数据',     'base', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('knowledge_search',   '{}', '知识库搜索',       'base', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('meta_cognition_read','{}', '三书全文按需检索', 'base', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
-- business — permission_flag=all → exposure_mode=meta（只读可直调）
('query_files',        '{}', '查询文件',         'business', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('send_file',          '{}', '发送已有文件',     'business', 'all',   'meta', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('list_file_versions', '{}', '列出文件版本',     'business', 'all',   'meta', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('list_attachments',  '{}', '列出主文件附件',   'business', 'all',   'meta', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('write_user_memory',  '{}', '写入用户记忆',     'business', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
-- business — permission_flag=write → exposure_mode=sop_only（写操作默认走专属SOP）
('record_event',       '{}', '记录事件',         'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('record_task',        '{}', '记录任务',         'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('record_meeting',     '{}', '记录会议',         'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('record_file',        '{}', '记录文件',         'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('update_file_category','{}','修改文件分类',     'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('link_file',          '{}', '关联文件到业务对象','business', 'write', 'sop_only', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('new_file_version',   '{}', '创建文件新版本',   'business', 'write', 'sop_only', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('delete_file',        '{}', '软删除文件',        'business', 'write', 'sop_only', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('link_to_master',    '{}', '挂载附件到主文件', 'business', 'write', 'sop_only', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('unlink_attachment', '{}', '卸载附件为独立文件','business', 'write', 'sop_only', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('update_file_purpose','{}','校正文件业务意图', 'business', 'write', 'sop_only', '', true, '2026-07-26T00:00:00', '2026-07-26T00:00:00'),
('create_task_node',   '{}', '创建任务节点',     'business', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-09-17T00:00:00'),
-- 批次①~④ 新增工具（2026-09-17）：写语义与口径见 tools_consistency.TOOL_META_MAP
('update_file_confidentiality','{}','调整文件密级','business','write','sop_only','', true, '2026-09-17T00:00:00', '2026-09-17T00:00:00'),
('manage_node_file',   '{}', '维护节点共享文件', 'business', 'write', 'sop_only', '', true, '2026-09-17T00:00:00', '2026-09-17T00:00:00'),
('embed_and_index',    '{}', '知识库入库',        'business', 'write', 'sop_only', '', true, '2026-09-17T00:00:00', '2026-09-17T00:00:00'),
('rag_remove_document','{}', '知识库出库',        'business', 'admin', 'sop_only', '', true, '2026-09-17T00:00:00', '2026-09-17T00:00:00'),
-- 人员/企业维护（Q11 选项 a）：授权判定在 PersonnelService 服务层，工具层 write（L3+）粗筛；
-- 承载 SOP-012-SYS（准入 L3）；manage_company 含删除，标 delete 高危
('update_user_level',  '{}', '调整人员权限等级', 'business', 'write', 'sop_only', '', true, '2026-09-18T00:00:00', '2026-09-18T00:00:00'),
('update_user_company','{}', '调整人员所属企业', 'business', 'write', 'sop_only', '', true, '2026-09-18T00:00:00', '2026-09-18T00:00:00'),
('manage_company',     '{}', '新增或删除企业',   'business', 'write', 'sop_only', '', true, '2026-09-18T00:00:00', '2026-09-18T00:00:00'),
-- 上传类放开到 L2（授权由 node_service 按「责任人 / L5+ / 节点参与单位人员」二次校验）
('submit_node_deliverable','{}','提交节点成果',  'business', 'write_l2', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('confirm_node_deliverable','{}','确认节点成果', 'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('return_node_deliverable', '{}','退回节点成果', 'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('query_my_nodes',     '{}', '查询我的节点',     'business', 'write', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
-- project（仅 L5-L6 管理员，permission_flag=admin → exposure_mode=sop_only）
('create_node',         '{}', '创建节点',         'project', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('query_node',          '{}', '查询节点详情',     'project', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('update_node_progress','{}', '更新节点进度',     'project', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('add_node_dependency', '{}', '添加节点依赖',     'project', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('mount_child_node',    '{}', '挂载子节点',       'project', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('update_nodes',        '{}', '批量更新节点',     'project', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('discard_nodes',       '{}', '废弃节点',         'project', 'admin', 'sop_only', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('manage_node_participant','{}','维护节点参与单位/参与人','project','admin','sop_only','', true, '2026-09-17T00:00:00', '2026-09-17T00:00:00'),
-- business（L4 条线负责人及以上可读，permission_flag=read_l4 → exposure_mode=sop_only）
('read_node_template',  '{}', '读取参考模板库',   'business', 'read_l4', 'sop_only', '', true, '2026-09-23T00:00:00', '2026-09-23T00:00:00'),
('build_node_draft',    '{}', '按模板装配草稿',   'business', 'read_l4', 'sop_only', '', true, '2026-09-23T00:00:00', '2026-09-23T00:00:00'),
('send_email',          '{}', '发送邮件',         'base', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('fetch_inbox',         '{}', '获取收件箱',       'base', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('chat_archive',        '{}', '会话归档',         'base', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('manage_pending_issues','{}','管理待办议题',     'base', 'all',   'meta', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00')
ON CONFLICT (id) DO NOTHING;
