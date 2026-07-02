-- ============================================================
-- 天津生态城26#地项目 - 测试数据SQL脚本（修复版）
-- ============================================================

BEGIN;

-- ============================================================
-- 1. 插入公司信息
-- ============================================================
INSERT INTO company_info (
    id,
    company_name,
    unified_code,
    business_desc,
    project_leader_id,
    creator_id,
    type,
    status,
    scope,
    partners,
    created_at,
    updated_at,
    is_deleted
) VALUES (
    'company_ecocity_tianjin_001',
    'Tianjin Eco-City Investment',
    '911201166688376677',
    'Eco-City Development',
    '',
    'system',
    'Developer',
    'active',
    '[]',
    '[]',
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    false
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 2. 插入项目总用户 - 王建国
-- ============================================================
INSERT INTO users (
    id,
    username,
    real_name,
    phone,
    email,
    status,
    is_admin,
    gender,
    id_card,
    qq,
    wechat,
    remark,
    creator_id,
    is_deleted,
    perm_list,
    org_category,
    permission_level,
    supervisor_id,
    company,
    position,
    created_at,
    updated_at
) VALUES (
    'user_wangjianguo_001',
    'wangjianguo',
    'Wang Jianguo',
    '13802168899',
    'wangjianguo@test.com',
    'active',
    false,
    1,
    '120104198006156677',
    '88552211',
    'wangjg_ecocity',
    'Project Manager',
    'system',
    false,
    '[]',
    4,
    4,
    NULL,
    'company_ecocity_tianjin_001',
    '["Project Manager"]',
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 3. 更新公司的项目负责人ID
-- ============================================================
UPDATE company_info 
SET project_leader_id = 'user_wangjianguo_001'
WHERE id = 'company_ecocity_tianjin_001'
AND project_leader_id = '';

-- ============================================================
-- 4. 插入生态城26#地项目
-- ============================================================
INSERT INTO projects (
    id,
    code,
    name,
    description,
    status,
    created_at,
    updated_at,
    address,
    city,
    lifecycle_stage,
    stage_updated_at,
    creator_id,
    is_deleted
) VALUES (
    'project_ecocity_26_001',
    'ECO-CITY-26-2024',
    'Tianjin Eco-City 26# Project',
    'Landscape construction phase',
    'active',
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'Tianjin Binhai Eco-City',
    'Tianjin',
    2,
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'user_wangjianguo_001',
    false
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 5. 更新用户的项目关联字段
-- ============================================================
UPDATE users 
SET project_id = 'project_ecocity_26_001'
WHERE id = 'user_wangjianguo_001'
AND project_id IS NULL;

COMMIT;
