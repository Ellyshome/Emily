-- ============================================================
-- 插入测试数据 - CompanyInfo 和 Users
-- 用于 emy-test 技能的下拉选择和权限测试
-- ============================================================

-- 启用 UUID 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 插入测试公司/单位数据
-- ============================================================

-- 先删除旧测试数据（可选，根据需要取消注释）
-- DELETE FROM company_info WHERE company_name IN (
--     'XX地产建设集团', '上海建筑设计研究院', '中天建设集团', 
--     '恒大监理有限公司', '鑫达建材供应商'
-- );

-- 建设单位（甲方）
INSERT INTO company_info (id, company_name, unified_code, business_desc, project_leader_id, creator_id, type, status, scope, department, created_at, updated_at, is_deleted)
VALUES (
    uuid_generate_v4()::text,
    'XX地产建设集团',
    '91310000MA1K3XXX01',
    '房地产开发与经营，项目投资管理',
    'system_admin',
    'system_admin',
    '建设单位',
    'active',
    '["立项审批","投资控制"]',
    '["总裁办", "工程部", "成本部"]',
    NOW()::text,
    NOW()::text,
    false
) ON CONFLICT (unified_code) DO NOTHING;

-- 设计单位
INSERT INTO company_info (id, company_name, unified_code, business_desc, project_leader_id, creator_id, type, status, scope, department, created_at, updated_at, is_deleted)
VALUES (
    uuid_generate_v4()::text,
    '上海建筑设计研究院',
    '91310000MA1K3XXX02',
    '建筑工程设计、规划设计、景观设计',
    'system_admin',
    'system_admin',
    '设计单位',
    'active',
    '["方案设计","施工图设计"]',
    '["建筑所", "结构所", "设备所"]',
    NOW()::text,
    NOW()::text,
    false
) ON CONFLICT (unified_code) DO NOTHING;

-- 总包单位
INSERT INTO company_info (id, company_name, unified_code, business_desc, project_leader_id, creator_id, type, status, scope, department, created_at, updated_at, is_deleted)
VALUES (
    uuid_generate_v4()::text,
    '中天建设集团',
    '91310000MA1K3XXX03',
    '房屋建筑工程总承包、市政工程',
    'system_admin',
    'system_admin',
    '总包',
    'active',
    '["主体施工","机电安装","装饰装修"]',
    '["项目部", "技术部", "安全部"]',
    NOW()::text,
    NOW()::text,
    false
) ON CONFLICT (unified_code) DO NOTHING;

-- 监理单位
INSERT INTO company_info (id, company_name, unified_code, business_desc, project_leader_id, creator_id, type, status, scope, department, created_at, updated_at, is_deleted)
VALUES (
    uuid_generate_v4()::text,
    '恒大监理有限公司',
    '91310000MA1K3XXX04',
    '工程监理、项目管理、技术咨询',
    'system_admin',
    'system_admin',
    '监理',
    'active',
    '["质量监理","进度监理","安全监理"]',
    '["监理一部", "监理二部"]',
    NOW()::text,
    NOW()::text,
    false
) ON CONFLICT (unified_code) DO NOTHING;

-- 供应商
INSERT INTO company_info (id, company_name, unified_code, business_desc, project_leader_id, creator_id, type, status, scope, department, created_at, updated_at, is_deleted)
VALUES (
    uuid_generate_v4()::text,
    '鑫达建材供应商',
    '91310000MA1K3XXX05',
    '建筑材料供应、设备租赁',
    'system_admin',
    'system_admin',
    '供应商',
    'active',
    '["钢材供应","混凝土供应"]',
    '["销售部", "物流部"]',
    NOW()::text,
    NOW()::text,
    false
) ON CONFLICT (unified_code) DO NOTHING;

-- ============================================================
-- 2. 获取公司 ID 供后续使用
-- ============================================================

-- 临时表存储公司 ID
CREATE TEMP TABLE temp_company_ids AS
SELECT id, company_name, type FROM company_info WHERE is_deleted = false;

-- ============================================================
-- 3. 插入测试用户数据（按权限层级）
-- ============================================================

-- 先删除旧测试用户（可选）
-- DELETE FROM users WHERE username IN (
--     'admin_wang', 'pm_li', 'engineer_zhang', 'supervisor_chen',
--     'designer_zhao', 'worker_sun', 'guest_zhou'
-- );

-- 系统管理员 (level = 6)
INSERT INTO users (
    id, username, real_name, phone, email, status, is_admin,
    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, level, supervisor_id, company, position,
    created_at, updated_at
) VALUES (
    uuid_generate_v4()::text,
    'admin_wang',
    '王总',
    '13800000001',
    'wangzong@xxestate.com',
    'active',
    true,
    1,
    '310101197001010001',
    '123456001',
    'wx_admin_wang',
    '系统管理员，拥有所有权限',
    'system',
    false,
    '["*"]',
    4,  -- 管理组
    6,  -- 系统管理员
    NULL,
    (SELECT id FROM temp_company_ids WHERE type = '建设单位' LIMIT 1),
    '["系统管理员","项目总监"]',
    NOW()::text,
    NOW()::text
);

-- 建设主管 (level = 4) - 甲方工程部经理
INSERT INTO users (
    id, username, real_name, phone, email, status, is_admin,
    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, level, supervisor_id, company, position,
    created_at, updated_at
) VALUES (
    uuid_generate_v4()::text,
    'pm_li',
    '李经理',
    '13800000002',
    'lijingli@xxestate.com',
    'active',
    false,
    1,
    '310101197502020002',
    '123456002',
    'wx_pm_li',
    '甲方工程部经理，负责项目整体协调',
    'admin_wang',
    false,
    '["project.read","project.write","task.assign","review.approve"]',
    4,  -- 管理组
    4,  -- 建设主管
    (SELECT id FROM users WHERE username = 'admin_wang' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '建设单位' LIMIT 1),
    '["工程部经理","甲方代表"]',
    NOW()::text,
    NOW()::text
);

-- 参建管理 (level = 3) - 总包项目经理
INSERT INTO users (
    id, username, real_name, phone, email, status, is_admin,
    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, level, supervisor_id, company, position,
    created_at, updated_at
) VALUES (
    uuid_generate_v4()::text,
    'engineer_zhang',
    '张工',
    '13800000003',
    'zhanggong@zhongtian.com',
    'active',
    false,
    1,
    '310101198003030003',
    '123456003',
    'wx_engineer_zhang',
    '总包项目经理，负责现场施工管理',
    'admin_wang',
    false,
    '["task.read","task.write","progress.update"]',
    2,  -- 工程组
    3,  -- 参建管理
    (SELECT id FROM users WHERE username = 'pm_li' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '总包' LIMIT 1),
    '["项目经理","土建工程师"]',
    NOW()::text,
    NOW()::text
);

-- 监理工程师 (level = 3)
INSERT INTO users (
    id, username, real_name, phone, email, status, is_admin,
    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, level, supervisor_id, company, position,
    created_at, updated_at
) VALUES (
    uuid_generate_v4()::text,
    'supervisor_chen',
    '陈监理',
    '13800000004',
    'chenjianli@hengda.com',
    'active',
    false,
    1,
    '310101197804040004',
    '123456004',
    'wx_supervisor_chen',
    '监理工程师，负责质量验收',
    'admin_wang',
    false,
    '["quality.check","progress.review","issue.report"]',
    2,  -- 工程组
    3,  -- 参建管理
    (SELECT id FROM users WHERE username = 'pm_li' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '监理' LIMIT 1),
    '["监理工程师","质量监督员"]',
    NOW()::text,
    NOW()::text
);

-- 设计师 (level = 3)
INSERT INTO users (
    id, username, real_name, phone, email, status, is_admin,
    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, level, supervisor_id, company, position,
    created_at, updated_at
) VALUES (
    uuid_generate_v4()::text,
    'designer_zhao',
    '赵工',
    '13800000005',
    'zhaogong@shdesign.com',
    'active',
    false,
    2,
    '310101198505050005',
    '123456005',
    'wx_designer_zhao',
    '建筑设计师，负责设计变更',
    'admin_wang',
    false,
    '["design.read","design.upload","change.request"]',
    2,  -- 工程组
    3,  -- 参建管理
    (SELECT id FROM users WHERE username = 'pm_li' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '设计单位' LIMIT 1),
    '["建筑设计师","设计负责人"]',
    NOW()::text,
    NOW()::text
);

-- 参建执行 (level = 2) - 施工员
INSERT INTO users (
    id, username, real_name, phone, email, status, is_admin,
    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, level, supervisor_id, company, position,
    created_at, updated_at
) VALUES (
    uuid_generate_v4()::text,
    'worker_sun',
    '孙师傅',
    '13800000006',
    'sunshifu@zhongtian.com',
    'active',
    false,
    1,
    '310101199006060006',
    '123456006',
    'wx_worker_sun',
    '土建施工员，负责现场作业执行',
    'engineer_zhang',
    false,
    '["task.read","progress.report"]',
    2,  -- 工程组
    2,  -- 参建执行
    (SELECT id FROM users WHERE username = 'engineer_zhang' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '总包' LIMIT 1),
    '["施工员","班组长"]',
    NOW()::text,
    NOW()::text
);

-- 访客 (level = 1) - 供应商联系人
INSERT INTO users (
    id, username, real_name, phone, email, status, is_admin,
    gender, id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, level, supervisor_id, company, position,
    created_at, updated_at
) VALUES (
    uuid_generate_v4()::text,
    'guest_zhou',
    '周业务员',
    '13800000007',
    'zhouwuye@xinda.com',
    'active',
    false,
    1,
    '31010119920809999',
    '123456007',
    'wx_guest_zhou',
    '供应商联系人，仅可查看公开信息',
    'admin_wang',
    false,
    '["public.read"]',
    1,  -- 访客组
    1,  -- 访客
    (SELECT id FROM users WHERE username = 'admin_wang' LIMIT 1),
    (SELECT id FROM temp_company_ids WHERE type = '供应商' LIMIT 1),
    '["业务员","供应商联系人"]',
    NOW()::text,
    NOW()::text
);

-- ============================================================
-- 4. 插入用户 IM 绑定数据（用于消息发送模拟）
-- ============================================================

DELETE FROM user_im_bindings WHERE user_id IN (
    SELECT id FROM users WHERE username IN (
        'admin_wang', 'pm_li', 'engineer_zhang', 'supervisor_chen',
        'designer_zhao', 'worker_sun', 'guest_zhou'
    )
);

INSERT INTO user_im_bindings (id, user_id, im_platform, im_user_id, im_display_name, status, created_at, updated_at)
SELECT 
    uuid_generate_v4()::text,
    u.id,
    'simulator',
    'sim_' || u.username,
    u.real_name,
    'active',
    NOW()::text,
    NOW()::text
FROM users u
WHERE u.username IN (
    'admin_wang', 'pm_li', 'engineer_zhang', 'supervisor_chen',
    'designer_zhao', 'worker_sun', 'guest_zhou'
);

-- ============================================================
-- 5. 验证数据
-- ============================================================

-- 查看所有测试用户及其权限级别
SELECT 
    u.real_name as "姓名",
    u.username as "用户名",
    u.level as "权限级别",
    CASE u.level
        WHEN 1 THEN '访客'
        WHEN 2 THEN '参建执行'
        WHEN 3 THEN '参建管理'
        WHEN 4 THEN '建设主管'
        WHEN 5 THEN '管理员'
        WHEN 6 THEN '系统管理员'
        ELSE '未知'
    END as "权限说明",
    c.company_name as "所属单位",
    u.position as "岗位",
    u.phone as "电话"
FROM users u
LEFT JOIN company_info c ON u.company = c.id
WHERE u.username IN (
    'admin_wang', 'pm_li', 'engineer_zhang', 'supervisor_chen',
    'designer_zhao', 'worker_sun', 'guest_zhou'
)
ORDER BY u.level DESC;

-- 统计各权限级别人数
SELECT 
    level,
    CASE level
        WHEN 1 THEN '访客'
        WHEN 2 THEN '参建执行'
        WHEN 3 THEN '参建管理'
        WHEN 4 THEN '建设主管'
        WHEN 5 THEN '管理员'
        WHEN 6 THEN '系统管理员'
        ELSE '未知'
    END as "权限级别",
    COUNT(*) as "人数"
FROM users
WHERE is_deleted = false
GROUP BY level
ORDER BY level DESC;

-- 清理临时表
DROP TABLE temp_company_ids;
