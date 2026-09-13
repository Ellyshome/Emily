-- ============================================================
-- 015_seed_subcontract_users.sql —— 补充监理团队 + 专业分包单位与人员
--
-- 目的：
--   1) 补齐监理团队：在现有总监理工程师陈建华之外，增加 2 名专业监理工程师，
--      实现「多人分责任覆盖」施工节点（土建监理 + 安装监理）。
--   2) 补齐专业分包：幕墙 / 门窗 / 结构(钢结构) / 二次结构 / 外檐 五类专业分包
--      单位及其项目经理/施工员，用于在施工阶段挂载对应分包节点。
--
-- Precondition: 002 + 002_patch 已创建建设单位/总包/监理/设计/供应商公司
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 015_seed_subcontract_users.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 删除旧数据（幂等）
-- ============================================================
DELETE FROM user_im_bindings WHERE im_user_id IN (
    'sim_钱卫东', 'sim_何丽娟', 'sim_吴志强', 'sim_郑海峰',
    'sim_高翔', 'sim_王伟', 'sim_冯建平'
);
DELETE FROM users WHERE username IN (
    '钱卫东', '何丽娟', '吴志强', '郑海峰',
    '高翔', '王伟', '冯建平'
);
DELETE FROM company_info WHERE unified_code IN (
    '91310000MA1K3XXX06', '91310000MA1K3XXX07', '91310000MA1K3XXX08',
    '91310000MA1K3XXX09', '91310000MA1K3XXX10'
);

-- ============================================================
-- 2. 插入 5 家专业分包公司（type='分包'，上级=总包 中天建设集团）
-- ============================================================
INSERT INTO company_info (id, company_name, unified_code, business_desc,
    project_leader_id, creator_id, type, status, scope, partners, parent_id,
    department, function_scope, is_admin, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, '苏州幕墙工程有限公司', '91310000MA1K3XXX06',
    '建筑幕墙工程专业承包', 'system_admin', 'system_admin', '分包', '履约中',
    '["幕墙工程"]', '[]', zt.id,
    '["幕墙项目部"]', '{}', false, NOW()::text, NOW()::text, false
FROM (SELECT id FROM company_info WHERE type = '总包' AND is_deleted = false LIMIT 1) zt;

INSERT INTO company_info (id, company_name, unified_code, business_desc,
    project_leader_id, creator_id, type, status, scope, partners, parent_id,
    department, function_scope, is_admin, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, '苏州门窗工程有限公司', '91310000MA1K3XXX07',
    '金属门窗工程专业承包', 'system_admin', 'system_admin', '分包', '履约中',
    '["门窗工程"]', '[]', zt.id,
    '["门窗项目部"]', '{}', false, NOW()::text, NOW()::text, false
FROM (SELECT id FROM company_info WHERE type = '总包' AND is_deleted = false LIMIT 1) zt;

INSERT INTO company_info (id, company_name, unified_code, business_desc,
    project_leader_id, creator_id, type, status, scope, partners, parent_id,
    department, function_scope, is_admin, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, '苏州钢结构工程有限公司', '91310000MA1K3XXX08',
    '钢结构工程专业承包', 'system_admin', 'system_admin', '分包', '履约中',
    '["钢结构工程"]', '[]', zt.id,
    '["钢结构项目部"]', '{}', false, NOW()::text, NOW()::text, false
FROM (SELECT id FROM company_info WHERE type = '总包' AND is_deleted = false LIMIT 1) zt;

INSERT INTO company_info (id, company_name, unified_code, business_desc,
    project_leader_id, creator_id, type, status, scope, partners, parent_id,
    department, function_scope, is_admin, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, '苏州二次结构工程有限公司', '91310000MA1K3XXX09',
    '砌体与二次结构工程专业分包', 'system_admin', 'system_admin', '分包', '履约中',
    '["二次结构工程"]', '[]', zt.id,
    '["二次结构项目部"]', '{}', false, NOW()::text, NOW()::text, false
FROM (SELECT id FROM company_info WHERE type = '总包' AND is_deleted = false LIMIT 1) zt;

INSERT INTO company_info (id, company_name, unified_code, business_desc,
    project_leader_id, creator_id, type, status, scope, partners, parent_id,
    department, function_scope, is_admin, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, '苏州外檐装饰工程有限公司', '91310000MA1K3XXX10',
    '外墙保温与装饰工程专业分包', 'system_admin', 'system_admin', '分包', '履约中',
    '["外檐装饰工程"]', '[]', zt.id,
    '["外檐装饰项目部"]', '{}', false, NOW()::text, NOW()::text, false
FROM (SELECT id FROM company_info WHERE type = '总包' AND is_deleted = false LIMIT 1) zt;

-- ============================================================
-- 3. 插入 2 名专业监理工程师（隶属恒大监理，直属上级陈建华）
-- ============================================================

-- 3.1 土建专业监理工程师
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '钱卫东',
    '13800000015', 'qianweidong@hengda.com',
    'active', false, 1, '310101198006060015', '123456015', 'wx_钱卫东',
    '专业监理工程师（土建），负责地基基础、主体结构、二次结构、外檐等土建节点监督',
    '王建国', false, '["quality.check","progress.review","issue.report"]',
    2, 3,
    (SELECT id FROM users WHERE username = '陈建华' LIMIT 1),
    (SELECT id FROM company_info WHERE type = '监理' AND is_deleted = false LIMIT 1),
    '["专业监理工程师","土建监理工程师"]', NOW()::text, NOW()::text
FROM (SELECT 1) t;

-- 3.2 安装专业监理工程师
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '何丽娟',
    '13800000016', 'helijuan@hengda.com',
    'active', false, 2, '310101198308080016', '123456016', 'wx_何丽娟',
    '专业监理工程师（安装），负责机电安装、幕墙、门窗等专业节点监督',
    '王建国', false, '["quality.check","progress.review","issue.report"]',
    2, 3,
    (SELECT id FROM users WHERE username = '陈建华' LIMIT 1),
    (SELECT id FROM company_info WHERE type = '监理' AND is_deleted = false LIMIT 1),
    '["专业监理工程师","安装监理工程师"]', NOW()::text, NOW()::text
FROM (SELECT 1) t;

-- ============================================================
-- 4. 插入 5 名专业分包人员（各分包公司项目经理/施工员）
-- ============================================================

-- 4.1 幕墙项目经理
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '吴志强',
    '13800000017', 'wuzhiqiang@szmq.com',
    'active', false, 1, '310101198505050017', '123456017', 'wx_吴志强',
    '幕墙项目经理，负责幕墙龙骨与面板安装',
    '王建国', false, '["task.read","task.write","progress.update"]',
    2, 3,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM company_info WHERE unified_code = '91310000MA1K3XXX06' LIMIT 1),
    '["幕墙项目经理"]', NOW()::text, NOW()::text
FROM (SELECT 1) t;

-- 4.2 门窗项目经理
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '郑海峰',
    '13800000018', 'zhenghaifeng@szmc.com',
    'active', false, 1, '310101198607070018', '123456018', 'wx_郑海峰',
    '门窗项目经理，负责铝合金门窗及防火门安装',
    '王建国', false, '["task.read","task.write","progress.update"]',
    2, 3,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM company_info WHERE unified_code = '91310000MA1K3XXX07' LIMIT 1),
    '["门窗项目经理"]', NOW()::text, NOW()::text
FROM (SELECT 1) t;

-- 4.3 钢结构施工员
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '高翔',
    '13800000019', 'gaoxiang@szgj.com',
    'active', false, 1, '310101198809090019', '123456019', 'wx_高翔',
    '钢结构施工员，负责钢结构雨棚、金属屋面及结构加固',
    '王建国', false, '["task.read","progress.report"]',
    2, 2,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM company_info WHERE unified_code = '91310000MA1K3XXX08' LIMIT 1),
    '["钢结构施工员"]', NOW()::text, NOW()::text
FROM (SELECT 1) t;

-- 4.4 二次结构施工员
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '王伟',
    '13800000020', 'wangwei@szec.com',
    'active', false, 1, '310101199010100020', '123456020', 'wx_王伟',
    '二次结构施工员，负责填充墙砌筑、构造柱、圈梁施工',
    '王建国', false, '["task.read","progress.report"]',
    2, 2,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM company_info WHERE unified_code = '91310000MA1K3XXX09' LIMIT 1),
    '["二次结构施工员"]', NOW()::text, NOW()::text
FROM (SELECT 1) t;

-- 4.5 外檐项目经理
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, position, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '冯建平',
    '13800000021', 'fengjianping@szwy.com',
    'active', false, 1, '310101199105050021', '123456021', 'wx_冯建平',
    '外檐项目经理，负责外墙保温、真石漆及檐口装饰',
    '王建国', false, '["task.read","task.write","progress.update"]',
    2, 3,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM company_info WHERE unified_code = '91310000MA1K3XXX10' LIMIT 1),
    '["外檐项目经理"]', NOW()::text, NOW()::text
FROM (SELECT 1) t;

-- ============================================================
-- 5. 插入 IM 绑定
-- ============================================================
INSERT INTO user_im_bindings (id, user_id, im_platform, im_user_id,
    im_display_name, status, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    u.id,
    'simulator',
    'sim_' || u.username,
    CASE u.username
        WHEN '钱卫东' THEN '钱监理'
        WHEN '何丽娟' THEN '何监理'
        WHEN '吴志强' THEN '吴经理'
        WHEN '郑海峰' THEN '郑经理'
        WHEN '高翔'   THEN '高工'
        WHEN '王伟'   THEN '王工'
        WHEN '冯建平' THEN '冯经理'
        ELSE u.username
    END,
    'active',
    NOW()::text,
    NOW()::text
FROM users u
WHERE u.username IN (
    '钱卫东', '何丽娟', '吴志强', '郑海峰',
    '高翔', '王伟', '冯建平'
);

-- ============================================================
-- 6. 验证
-- ============================================================
SELECT '--- 分包单位一览 ---' AS section;
SELECT c.company_name, c.type, p.company_name AS "上级单位"
FROM company_info c
LEFT JOIN company_info p ON c.parent_id = p.id
WHERE c.type = '分包' AND c.is_deleted = false
ORDER BY c.company_name;

SELECT '--- 新增监理/分包人员一览 ---' AS section;
SELECT u.username, u.level, c.company_name, u.position
FROM users u
LEFT JOIN company_info c ON u.company = c.id
WHERE u.username IN (
    '钱卫东', '何丽娟', '吴志强', '郑海峰',
    '高翔', '王伟', '冯建平'
)
ORDER BY u.level DESC, u.username;

COMMIT;
