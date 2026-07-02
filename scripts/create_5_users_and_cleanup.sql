-- ============================================================
-- 创建5个测试用户并物理删除其他所有用户
-- ============================================================

-- 先插入4个新用户（加上已有的wangjianguo共5人）
INSERT INTO users (id, username, real_name, phone, email, status, is_admin, gender, org_category, permission_level, project_id, position, created_at, updated_at, is_deleted) VALUES 
('user_lihua_001', 'lihua', 'Li Hua', '13900000001', 'lihua@test.com', 'active', false, 1, 4, 3, 'project_ecocity_26_001', '["Engineer"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, username, real_name, phone, email, status, is_admin, gender, org_category, permission_level, project_id, position, created_at, updated_at, is_deleted) VALUES 
('user_zhangming_002', 'zhangming', 'Zhang Ming', '13900000002', 'zhangming@test.com', 'active', false, 1, 4, 2, 'project_ecocity_26_001', '["Foreman"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, username, real_name, phone, email, status, is_admin, gender, org_category, permission_level, project_id, position, created_at, updated_at, is_deleted) VALUES 
('user_wangfang_003', 'wangfang', 'Wang Fang', '13900000003', 'wangfang@test.com', 'active', false, 2, 4, 2, 'project_ecocity_26_001', '["QA"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, username, real_name, phone, email, status, is_admin, gender, org_category, permission_level, project_id, position, created_at, updated_at, is_deleted) VALUES 
('user_zhaowei_004', 'zhaowei', 'Zhao Wei', '13900000004', 'zhaowei@test.com', 'active', false, 1, 4, 1, 'project_ecocity_26_001', '["Tech"]', '2026-07-02T00:00:00Z', '2026-07-02T00:00:00Z', false)
ON CONFLICT (id) DO NOTHING;

-- 物理删除不在保留列表中的所有用户
DELETE FROM users 
WHERE id NOT IN (
    'user_wangjianguo_001',
    'user_lihua_001',
    'user_zhangming_002',
    'user_wangfang_003',
    'user_zhaowei_004'
);
