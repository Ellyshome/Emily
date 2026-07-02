-- ============================================================
-- 更新5个用户为真实人物特征
-- ============================================================

-- 删除旧的5个用户（因为ID不符合UUID格式
DELETE FROM users WHERE id IN (
    'user_wangjianguo_001',
    'user_lihua_001',
    'user_zhangming_002',
    'user_wangfang_003',
    'user_zhaowei_004'
);

-- 插入5个符合真实人物特征的新用户（标准UUID格式
INSERT INTO users (id, username, phone, email, status, is_admin, gender, remark, creator_id, is_deleted, perm_list, org_category, permission_level, project_id, position, created_at, updated_at) VALUES
('a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d', '张建国', '13801234567', 'zhangjianguo@tjeco.com', 'active', false, 1, '天津生态城26#地项目总经理，高级工程师，15年工程管理经验', 'system', false, '["project:read", "project:write", "event:create", "task:assign"]', 4, 4, 'project_ecocity_26_001', '["项目总经理"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z'),

('b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e', '李明华', '13902345678', 'liminghua@tjeco.com', 'active', false, 1, '土建工程师，负责现场施工管理，8年施工单位经验', 'system', false, '["project:read", "event:create", "task:create"]', 3, 3, 'project_ecocity_26_001', '["土建工程师"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z'),

('c3d4e5f6-a7b8-4c7d-8e1f-2a3b4c5d6e7f', '王晓芳', '13703456789', 'wangxiaofang@tjeco.com', 'active', false, 2, '质量监理工程师，负责工程质量验收，持注册监理工程师', 'system', false, '["project:read", "event:create", "quality:check"]', 2, 2, 'project_ecocity_26_001', '["质量监理"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z'),

('d4e5f6a7-b8c9-4d8e-9f2a-3b4c5d6e7f8a', '赵伟', '13604567890', 'zhaowei@tjeco.com', 'active', false, 1, '安全员，持C证，5年现场安全管理经验', 'system', false, '["project:read", "safety:report"]', 2, 1, 'project_ecocity_26_001', '["安全员"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z'),

('e5f6a7b8-c9d0-4e9f-0a3b-4c5d6e7f8a9b', '陈思雨', '13505678901', 'chensiyu@tjeco.com', 'active', false, 2, '资料员，负责工程资料整理归档，3年经验', 'system', false, '["project:read", "document:upload"]', 1, 1, 'project_ecocity_26_001', '["资料员"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z');
