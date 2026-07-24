-- 工具注册表种子数据
-- 执行: docker exec -i emily-postgres psql -U emily -d emily < seed_tool_registry.sql

INSERT INTO tool_registry (id, signature, display_name, category, permission_flag, handler_module, is_active, registered_at, updated_at) VALUES
-- base（全部用户可用）
('query_data',         '{}', '查询企业数据',     'base', 'all',   '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('knowledge_search',   '{}', '知识库搜索',       'base', 'all',   '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
-- business（level >= 3 可用 write，all 全部可用）
('record_event',       '{}', '记录事件',         'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('record_task',        '{}', '记录任务',         'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('record_meeting',     '{}', '记录会议',         'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('record_file',        '{}', '记录文件',         'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('query_files',        '{}', '查询文件',         'business', 'all',   '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('update_file_category','{}','修改文件分类',     'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('write_user_memory',  '{}', '写入用户记忆',     'business', 'all',   '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('create_task_node',   '{}', '创建任务节点',     'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('submit_node_deliverable','{}','提交节点成果',  'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('confirm_node_deliverable','{}','确认节点成果', 'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('return_node_deliverable', '{}','退回节点成果', 'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('query_my_nodes',     '{}', '查询我的节点',     'business', 'write', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
-- project（仅 L5-L6 管理员）
('create_node',         '{}', '创建节点',         'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('query_node',          '{}', '查询节点详情',     'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('update_node_progress','{}', '更新节点进度',     'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('add_node_dependency', '{}', '添加节点依赖',     'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('mount_child_node',    '{}', '挂载子节点',       'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('update_nodes',        '{}', '批量更新节点',     'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('activate_nodes',      '{}', '激活节点',         'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('discard_nodes',       '{}', '废弃节点',         'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('send_email',          '{}', '发送邮件',         'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('fetch_inbox',         '{}', '获取收件箱',       'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('chat_archive',        '{}', '会话归档',         'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('manage_pending_issues','{}','管理待办议题',     'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00'),
('voice_entry',         '{}', '语音录入',         'project', 'admin', '', true, '2026-07-24T00:00:00', '2026-07-24T00:00:00')
ON CONFLICT (id) DO NOTHING;
