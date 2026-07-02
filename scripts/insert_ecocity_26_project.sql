-- ============================================================
-- 天津生态城26#地项目 - 测试数据SQL脚本
-- 执行方式：在PostgreSQL客户端中直接执行
-- ============================================================

-- 注意：请先确保数据库表结构已创建（通过 alembic 或 Python models）

BEGIN;

-- ============================================================
-- 1. 插入公司信息（天津生态城投资开发有限公司）
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
    '天津生态城投资开发有限公司',
    '91120116668837667E',
    '天津生态城城市开发建设运营',
    '',  -- 后面更新
    'system',
    '建设单位',
    'active',
    '["土地开发", "基础设施建设", "配套商业"]',
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
    project_id,  -- 新添加的项目关联字段
    position,
    created_at,
    updated_at
) VALUES (
    'user_wangjianguo_001',
    'wangjianguo',
    '王建国',
    '13802168899',
    'wangjianguo@tjedc.com',
    'active',
    false,
    1,  -- 1=男
    '120104198006156677',
    '88552211',
    'wangjg_ecocity',
    '天津生态城26#地项目总，高级工程师，15年工程管理经验',
    'system',
    false,
    '["project:read", "project:write", "event:create", "task:assign"]',
    4,  -- 4=管理组
    4,  -- 4=建设主管
    NULL,
    'company_ecocity_tianjin_001',
    'project_ecocity_26_001',  -- 后面更新项目ID
    '["项目总经理", "工程总指挥"]',
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
    '天津生态城26#地块开发项目',
    '天津生态城26#地块项目位于中新天津生态城核心区域，占地面积约8.6万平方米，总建筑面积约25万平方米，包括住宅、商业配套、社区服务中心等。

当前处于景观施工阶段，主要工作内容：
1. 小区园林景观施工（绿化、水景、铺装）
2. 主入口广场及配套设施建设
3. 社区活动中心周边景观
4. 儿童游乐区、健身区设施安装
5. 园区照明及智慧安防系统',
    'active',
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    '天津市滨海新区中新天津生态城26#地块',
    '天津',
    2,  -- 2=工程施工阶段（景观施工）
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
AND (project_id IS NULL OR project_id = '');

COMMIT;

-- ============================================================
-- 数据验证查询
-- ============================================================
-- 查询公司信息
-- SELECT id, company_name, type, status FROM company_info WHERE id = 'company_ecocity_tianjin_001';

-- 查询项目总用户
-- SELECT id, username, real_name, phone, email, permission_level, company, project_id FROM users WHERE id = 'user_wangjianguo_001';

-- 查询项目信息
-- SELECT id, code, name, city, address, lifecycle_stage, status, creator_id FROM projects WHERE id = 'project_ecocity_26_001';

-- ============================================================
-- 预期输出结果：
-- ============================================================
-- 公司信息：
--   名称：天津生态城投资开发有限公司
--   类型：建设单位
--
-- 用户信息（项目总）：
--   用户名：wangjianguo
--   姓名：王建国
--   职位：项目总经理
--   权限层级：4级（建设主管）
--   所属公司：天津生态城投资开发有限公司
--
-- 项目信息：
--   项目名称：天津生态城26#地块开发项目
--   项目编号：ECO-CITY-26-2024
--   所在城市：天津
--   项目地址：天津市滨海新区中新天津生态城26#地块
--   当前阶段：景观施工阶段（lifecycle_stage=2）
--   创建人：王建国
