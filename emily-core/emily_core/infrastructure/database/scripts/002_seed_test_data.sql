-- ============================================================
-- 002_seed_test_data.sql —— 测试种子数据：公司 + 用户 + IM 绑定
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 002_seed_test_data.sql
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 删除旧测试数据（幂等）
-- ============================================================
DELETE FROM user_im_bindings WHERE im_platform = 'simulator';
DELETE FROM users WHERE username IN (
    '王建国', '李景利', '张正宏', '陈建华',
    '赵明远', '孙建国', '周文斌'
);
DELETE FROM company_info WHERE unified_code IN (
    '91310000MA1K3XXX01', '91310000MA1K3XXX02', '91310000MA1K3XXX03',
    '91310000MA1K3XXX04', '91310000MA1K3XXX05'
);

-- ============================================================
-- 2. 插入测试公司/单位数据
-- ============================================================
INSERT INTO company_info (id, company_name, unified_code, business_desc, project_leader_id, creator_id, type, status, scope, department, created_at, updated_at, is_deleted)
VALUES
(uuid_generate_v4()::text, 'XX地产建设集团',   '91310000MA1K3XXX01', '房地产开发与经营，项目投资管理', 'system_admin', 'system_admin', '建设单位', 'active', '["立项审批","投资控制"]',            '["总裁办", "工程部", "成本部"]', NOW()::text, NOW()::text, false),
(uuid_generate_v4()::text, '上海建筑设计研究院', '91310000MA1K3XXX02', '建筑工程设计、规划设计、景观设计', 'system_admin', 'system_admin', '设计单位', 'active', '["方案设计","施工图设计"]',          '["建筑所", "结构所", "设备所"]', NOW()::text, NOW()::text, false),
(uuid_generate_v4()::text, '中天建设集团',     '91310000MA1K3XXX03', '房屋建筑工程总承包、市政工程',     'system_admin', 'system_admin', '总包',     'active', '["主体施工","机电安装","装饰装修"]', '["项目部", "技术部", "安全部"]', NOW()::text, NOW()::text, false),
(uuid_generate_v4()::text, '恒大监理有限公司',  '91310000MA1K3XXX04', '工程监理、项目管理、技术咨询',     'system_admin', 'system_admin', '监理',     'active', '["质量监理","进度监理","安全监理"]', '["监理一部", "监理二部"]',         NOW()::text, NOW()::text, false),
(uuid_generate_v4()::text, '鑫达建材供应商',    '91310000MA1K3XXX05', '建筑材料供应、设备租赁',           'system_admin', 'system_admin', '供应商',   'active', '["钢材供应","混凝土供应"]',          '["销售部", "物流部"]',             NOW()::text, NOW()::text, false);

-- ============================================================
-- 3. 临时表存储公司 ID
-- ============================================================
CREATE TEMP TABLE temp_company_ids AS
SELECT id, company_name, type FROM company_info WHERE is_deleted = false;

-- ============================================================
-- 4. 插入测试用户（7 个，覆盖 6 个权限层级）
--    users 表字段: id, username, phone, email, status, is_admin, gender, id_card,
--    qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level,
--    supervisor_id, company, project_id, position, long_term_memory,
--    conversation_summary, created_at, updated_at
-- ============================================================

-- 4.1 系统管理员 (level = 6)
INSERT INTO users (id, username, phone, email, status, is_admin, gender, id_card, qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '王建国',
    '13800000001', 'wangzong@xxestate.com',
    'active', true, 1, '310101197001010001', '123456001', 'wx_王建国',
    '系统管理员，拥有所有权限',
    'system', false, '["*"]', 4, 6, NULL,
    (SELECT id FROM temp_company_ids WHERE type = '建设单位' LIMIT 1),
    '["系统管理员","项目总监"]', NOW()::text, NOW()::text
);

-- 4.2 建设主管 (level = 4) - 甲方工程部经理
INSERT INTO users (id, username, phone, email, status, is_admin, gender, id_card, qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '李景利',
    '13800000002', 'lijingli@xxestate.com',
    'active', false, 1, '310101197502020002', '123456002', 'wx_李景利',
    '甲方工程部经理，负责项目整体协调',
    '王建国', false, '["project.read","project.write","task.assign","review.approve"]',
    4, 4,
    (SELECT id FROM users WHERE username = '王建国' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '建设单位' LIMIT 1),
    '["工程部经理","甲方代表"]', NOW()::text, NOW()::text
);

-- 4.3 参建管理 (level = 3) - 总包项目经理
INSERT INTO users (id, username, phone, email, status, is_admin, gender, id_card, qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '张正宏',
    '13800000003', 'zhanggong@zhongtian.com',
    'active', false, 1, '310101198003030003', '123456003', 'wx_张正宏',
    '总包项目经理，负责现场施工管理',
    '王建国', false, '["task.read","task.write","progress.update"]',
    2, 3,
    (SELECT id FROM users WHERE username = '李景利' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '总包' LIMIT 1),
    '["项目经理","土建工程师"]', NOW()::text, NOW()::text
);

-- 4.4 监理工程师 (level = 3)
INSERT INTO users (id, username, phone, email, status, is_admin, gender, id_card, qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '陈建华',
    '13800000004', 'chenjianli@hengda.com',
    'active', false, 1, '310101197804040004', '123456004', 'wx_陈建华',
    '监理工程师，负责质量验收',
    '王建国', false, '["quality.check","progress.review","issue.report"]',
    2, 3,
    (SELECT id FROM users WHERE username = '李景利' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '监理' LIMIT 1),
    '["监理工程师","质量监督员"]', NOW()::text, NOW()::text
);

-- 4.5 设计师 (level = 3)
INSERT INTO users (id, username, phone, email, status, is_admin, gender, id_card, qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '赵明远',
    '13800000005', 'zhaogong@shdesign.com',
    'active', false, 2, '310101198505050005', '123456005', 'wx_赵明远',
    '建筑设计师，负责设计变更',
    '王建国', false, '["design.read","design.upload","change.request"]',
    2, 3,
    (SELECT id FROM users WHERE username = '李景利' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '设计单位' LIMIT 1),
    '["建筑设计师","设计负责人"]', NOW()::text, NOW()::text
);

-- 4.6 参建执行 (level = 2) - 施工员
INSERT INTO users (id, username, phone, email, status, is_admin, gender, id_card, qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '孙建国',
    '13800000006', 'sunshifu@zhongtian.com',
    'active', false, 1, '310101199006060006', '123456006', 'wx_孙建国',
    '土建施工员，负责现场作业执行',
    '张正宏', false, '["task.read","progress.report"]',
    2, 2,
    (SELECT id FROM users WHERE username = '张正宏' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '总包' LIMIT 1),
    '["施工员","班组长"]', NOW()::text, NOW()::text
);

-- 4.7 访客 (level = 1) - 供应商联系人
INSERT INTO users (id, username, phone, email, status, is_admin, gender, id_card, qq, wechat, remark, creator_id, is_deleted, perm_list, org_category, level, supervisor_id, company, position, created_at, updated_at)
VALUES (
    uuid_generate_v4()::text,
    '周文斌',
    '13800000007', 'zhouwuye@xinda.com',
    'active', false, 1, '31010119920809999', '123456007', 'wx_周文斌',
    '供应商联系人，仅可查看公开信息',
    '王建国', false, '["public.read"]',
    1, 1,
    (SELECT id FROM users WHERE username = '王建国' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '供应商' LIMIT 1),
    '["业务员","供应商联系人"]', NOW()::text, NOW()::text
);

-- ============================================================
-- 5. 插入用户 IM 绑定数据（用于消息发送模拟）
-- ============================================================

INSERT INTO user_im_bindings (id, user_id, im_platform, im_user_id, im_display_name, status, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    u.id,
    'simulator',
    'sim_' || u.username,
    -- display name from position field (JSON array, first element)
    CASE u.username
        WHEN '王建国' THEN '王总'
        WHEN '李景利' THEN '李经理'
        WHEN '张正宏' THEN '张工'
        WHEN '陈建华' THEN '陈监理'
        WHEN '赵明远' THEN '赵工'
        WHEN '孙建国' THEN '孙师傅'
        WHEN '周文斌' THEN '周业务员'
        ELSE u.username
    END,
    'active',
    NOW()::text,
    NOW()::text
FROM users u
WHERE u.username IN (
    '王建国', '李景利', '张正宏', '陈建华',
    '赵明远', '孙建国', '周文斌'
);

-- ============================================================
-- 6. 验证
-- ============================================================

-- 6.1 用户 + 权限 + 公司
SELECT
    u.username AS "用户名",
    u.level AS "权限级别",
    CASE u.level
        WHEN 1 THEN '访客'      WHEN 2 THEN '参建执行'
        WHEN 3 THEN '参建管理'  WHEN 4 THEN '建设主管'
        WHEN 5 THEN '管理员'    WHEN 6 THEN '系统管理员'
        ELSE '未知'
    END AS "权限说明",
    c.company_name AS "所属单位",
    u.position AS "岗位"
FROM users u
LEFT JOIN company_info c ON u.company = c.id
WHERE u.username IN (
    '王建国', '李景利', '张正宏', '陈建华',
    '赵明远', '孙建国', '周文斌'
)
ORDER BY u.level DESC;

-- 6.2 各权限级别统计
SELECT level, COUNT(*) AS "人数"
FROM users WHERE is_deleted = false
GROUP BY level ORDER BY level DESC;

-- 6.3 IM 绑定验证
SELECT u.username, b.im_user_id, b.im_display_name, b.im_platform
FROM user_im_bindings b
JOIN users u ON b.user_id = u.id
WHERE b.im_platform = 'simulator';

-- 清理
DROP TABLE temp_company_ids;
