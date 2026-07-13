-- ============================================================
-- 006_seed_permission_data.sql —— 权限体系种子数据
--   permission_groups / sop_business_flows / sop_permission_bindings / permission_grants
--
-- Precondition: 002 + 003 must be run first (users + companies + project)
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 006_seed_permission_data.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 0. Temp lookup tables
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _su AS
SELECT id, username, level, company FROM users WHERE is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sc AS
SELECT id, company_name, type FROM company_info WHERE is_deleted = false;

-- ============================================================
-- 1. Permission Groups (~10 groups)
--    Organization dimension: company_type + department → org_level
-- ============================================================

-- 1.1 建设单位 groups
INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '建设单位-工程部',
    'OWNER-ENG',
    '建设单位工程部权限组——管理全景节点、审批跨单位事项、查看所有文件',
    '建设单位', '工程部',
    2, NULL,
    '["REC","FILE","QRY","FLOW","SYS"]', 4, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '建设单位-成本部',
    'OWNER-COST',
    '建设单位成本部权限组——查看项目文件、流转单，不管理节点',
    '建设单位', '成本部',
    2, NULL,
    '["QRY","FILE","FLOW"]', 3, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '建设单位-总裁办',
    'OWNER-EXEC',
    '建设单位总裁办——全局管理权限',
    '建设单位', '总裁办',
    2, NULL,
    '["REC","FILE","QRY","FLOW","SYS"]', 6, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

-- 1.2 设计单位 groups
INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '设计单位-建筑所',
    'DESIGN-ARCH',
    '设计单位建筑所——设计文件管理、变更申请',
    '设计单位', '建筑所',
    2, NULL,
    '["REC","FILE","QRY","FLOW"]', 3, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '设计单位-结构所',
    'DESIGN-STRUC',
    '设计单位结构所——设计文件管理、变更申请',
    '设计单位', '结构所',
    2, NULL,
    '["REC","FILE","QRY","FLOW"]', 3, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

-- 1.3 总包 groups
INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '总包-项目部',
    'CONTRACTOR-PM',
    '总包项目部——施工记录、进度上报、材料报验',
    '总包', '项目部',
    2, NULL,
    '["REC","QRY","FILE","FLOW"]', 3, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '总包-安全部',
    'CONTRACTOR-SAFE',
    '总包安全部——巡检记录、整改反馈',
    '总包', '安全部',
    2, NULL,
    '["REC","QRY"]', 2, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

-- 1.4 监理 groups
INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '监理-监理一部',
    'SUPERVISOR-1',
    '监理一部——质量验收、材料见证取样、整改通知',
    '监理', '监理一部',
    2, NULL,
    '["REC","QRY","FILE","FLOW"]', 3, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

-- 1.5 供应商 group
INSERT INTO permission_groups (id, name, code, description, company_type, department,
    org_level, parent_group_id, allowed_sop_types, min_level, status, is_system,
    creator_id, created_at, updated_at, is_deleted)
SELECT uuid_generate_v4()::text,
    '供应商-销售部',
    'SUPPLIER-SALES',
    '供应商销售部——材料信息查看、供货记录',
    '供应商', '销售部',
    2, NULL,
    '["QRY","FILE"]', 1, 'active', true,
    u.id, NOW()::text, NOW()::text, false
FROM _su u WHERE u.username = 'admin_wang';

-- ============================================================
-- 2. SOP Business Flows —— Register existing SOPs
-- ============================================================
INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-000-SYS', 'SOP-000-SYS.skill.yaml', '系统管理',
    '系统级操作：配置管理、权限管理、日志查询等',
    'SYS', '系统管理',
    'PRIVATE', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'OWNER-EXEC' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-001-REC', 'SOP-001-REC.skill.yaml', '事件记录',
    '记录项目事件：里程碑、施工记录、验收记录、安全巡检等',
    'REC', '工程记录',
    'INTERNAL', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'CONTRACTOR-PM' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-002-REC', 'SOP-002-REC.skill.yaml', '任务管理',
    '创建、分配、跟踪工作任务的执行状态',
    'REC', '工程记录',
    'INTERNAL', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'CONTRACTOR-PM' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-003-REC', 'SOP-003-REC.skill.yaml', '会议纪要',
    '记录会议纪要，含结论、行动项分配、关联文件',
    'REC', '工程记录',
    'INTERNAL', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'OWNER-ENG' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-004-FILE', 'SOP-004-FILE.skill.yaml', '文件管理',
    '上传、检索、版本管理项目文件',
    'FILE', '文件管理',
    'INTERNAL', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'DESIGN-ARCH' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-005-QRY', 'SOP-005-QRY.skill.yaml', '信息查询',
    '查询项目信息、节点进度、任务状态、文件目录等',
    'QRY', '信息查询',
    'INTERNAL', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'SUPERVISOR-1' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-007-REC', 'SOP-007-REC.skill.yaml', '流转单管理',
    '业务流转单（设计变更、签证、验收申请、付款申请等）',
    'FLOW', '业务流转',
    'INTERNAL', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'OWNER-ENG' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-008-SYS', 'SOP-008-SYS.skill.yaml', '全景节点管理',
    '全景节点图管理：创建、更新、激活、废弃节点，管理交付物',
    'SYS', '系统管理',
    'PRIVATE', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'OWNER-ENG' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-011-SYS', 'SOP-011-SYS.skill.yaml', '权限管理',
    '权限组管理、授权审批、权限审计',
    'SYS', '系统管理',
    'PRIVATE', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'OWNER-EXEC' LIMIT 1;

INSERT INTO sop_business_flows (id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'SOP-999-SYS', 'SOP-999-SYS.skill.yaml', '通用兜底',
    '未匹配到特定 SOP 时的通用处理入口',
    'SYS', '系统管理',
    'PUBLIC', '[]', pg.id,
    'V1.0', true,
    u.id, NOW()::text, NOW()::text
FROM _su u, permission_groups pg WHERE u.username = 'admin_wang' AND pg.code = 'OWNER-EXEC' LIMIT 1;

-- ============================================================
-- 3. SOP-Permission Bindings (~15 bindings)
-- ============================================================

-- 3.1 SOP-001 (事件记录) → 建设/总包/监理可写，其他人只读
INSERT INTO sop_permission_bindings (id, sop_business_flow_id, permission_group_id,
    binding_type, created_at)
SELECT uuid_generate_v4()::text, sop.id, pg.id, 'allow', NOW()::text
FROM sop_business_flows sop, permission_groups pg
WHERE sop.sop_id = 'SOP-001-REC' AND pg.code IN ('OWNER-ENG', 'CONTRACTOR-PM', 'SUPERVISOR-1', 'DESIGN-ARCH');

INSERT INTO sop_permission_bindings (id, sop_business_flow_id, permission_group_id,
    binding_type, created_at)
SELECT uuid_generate_v4()::text, sop.id, pg.id, 'deny', NOW()::text
FROM sop_business_flows sop, permission_groups pg
WHERE sop.sop_id = 'SOP-001-REC' AND pg.code = 'SUPPLIER-SALES';

-- 3.2 SOP-002 (任务管理) → 建设/总包可管理
INSERT INTO sop_permission_bindings (id, sop_business_flow_id, permission_group_id,
    binding_type, created_at)
SELECT uuid_generate_v4()::text, sop.id, pg.id, 'allow', NOW()::text
FROM sop_business_flows sop, permission_groups pg
WHERE sop.sop_id = 'SOP-002-REC' AND pg.code IN ('OWNER-ENG', 'CONTRACTOR-PM', 'OWNER-EXEC');

-- 3.3 SOP-004 (文件管理) → 所有参建单位可读，建设/设计可写
INSERT INTO sop_permission_bindings (id, sop_business_flow_id, permission_group_id,
    binding_type, created_at)
SELECT uuid_generate_v4()::text, sop.id, pg.id, 'allow', NOW()::text
FROM sop_business_flows sop, permission_groups pg
WHERE sop.sop_id = 'SOP-004-FILE' AND pg.code IN ('OWNER-ENG', 'OWNER-COST', 'DESIGN-ARCH', 'DESIGN-STRUC', 'CONTRACTOR-PM', 'SUPERVISOR-1', 'SUPPLIER-SALES');

-- 3.4 SOP-005 (信息查询) → 所有单位可查
INSERT INTO sop_permission_bindings (id, sop_business_flow_id, permission_group_id,
    binding_type, created_at)
SELECT uuid_generate_v4()::text, sop.id, pg.id, 'allow', NOW()::text
FROM sop_business_flows sop, permission_groups pg
WHERE sop.sop_id = 'SOP-005-QRY' AND pg.code IN ('OWNER-ENG', 'OWNER-COST', 'OWNER-EXEC', 'DESIGN-ARCH', 'DESIGN-STRUC', 'CONTRACTOR-PM', 'CONTRACTOR-SAFE', 'SUPERVISOR-1', 'SUPPLIER-SALES');

-- 3.5 SOP-008 (全景节点) → 仅建设单位工程部和管理层
INSERT INTO sop_permission_bindings (id, sop_business_flow_id, permission_group_id,
    binding_type, created_at)
SELECT uuid_generate_v4()::text, sop.id, pg.id, 'allow', NOW()::text
FROM sop_business_flows sop, permission_groups pg
WHERE sop.sop_id = 'SOP-008-SYS' AND pg.code IN ('OWNER-ENG', 'OWNER-EXEC');

-- ============================================================
-- 4. Permission Grants — explicit cross-line access grants
-- ============================================================
-- 4.1 监理获得设计文件的临时查看权限（用于图纸会审）
INSERT INTO permission_grants (id, grant_no, grantee_id, grantor_id, perm_code,
    grant_type, operations, grant_time, expire_time, remark, status, client_ip,
    created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'PGR-20260201-0001',
    u1.id, u2.id,
    'FILE-INTERNAL-*-*-*',
    'TEMP',
    '["read"]',
    NOW()::text,
    '2026-03-31',
    '地基与基础验收前，监理需查阅完整施工图进行质量检查',
    'ACTIVE',
    '',
    NOW()::text, NOW()::text
FROM _su u1, _su u2
WHERE u1.username = 'supervisor_chen' AND u2.username = 'pm_li';

-- 4.2 总包获得成本数据的临时查看权限（进度款审核相关）
INSERT INTO permission_grants (id, grant_no, grantee_id, grantor_id, perm_code,
    grant_type, operations, grant_time, expire_time, remark, status, client_ip,
    created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'PGR-20260710-0001',
    u1.id, u2.id,
    'FILE-CONFIDENTIAL-ECOCITY-*-*',
    'TEMP',
    '["read"]',
    NOW()::text,
    '2026-08-10',
    '7月份进度款支付申请需查阅相关合同及造价数据',
    'ACTIVE',
    '',
    NOW()::text, NOW()::text
FROM _su u1, _su u2
WHERE u1.username = 'engineer_zhang' AND u2.username = 'pm_li';

-- 4.3 设计单位永久拥有设计文件的完全权限
INSERT INTO permission_grants (id, grant_no, grantee_id, grantor_id, perm_code,
    grant_type, operations, grant_time, expire_time, remark, status, client_ip,
    created_at, updated_at)
SELECT uuid_generate_v4()::text,
    'PGR-20260115-0001',
    u1.id, u2.id,
    'FILE-INTERNAL-*-DESIGN-*',
    'PERMANENT',
    '["read"]',
    NOW()::text,
    NULL,
    '设计单位需永久保留对设计文件的查看和更新权限',
    'ACTIVE',
    '',
    NOW()::text, NOW()::text
FROM _su u1, _su u2
WHERE u1.username = 'designer_zhao' AND u2.username = 'admin_wang';

-- ============================================================
-- 5. Permission Definitions — basic permission codes
-- ============================================================
INSERT INTO permission_def (id, perm_code, description, resource_type,
    security_level, node_id, resource_id, created_at, updated_at)
VALUES
    (uuid_generate_v4()::text, 'FILE-PUBLIC-*-*-*', '公开文件读取', 'FIL', 'PUBLIC', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'FILE-INTERNAL-*-*-*', '内部文件读取', 'FIL', 'INTERNAL', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'FILE-CONFIDENTIAL-*-*-*', '机密文件读取', 'FIL', 'CONFIDENTIAL', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'NODE-READ-*-*-*', '全景节点查看', 'NOD', 'PUBLIC', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'NODE-WRITE-*-*-*', '全景节点管理', 'NOD', 'INTERNAL', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'EVENT-READ-*-*-*', '事件查看', 'EVT', 'PUBLIC', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'EVENT-WRITE-*-*-*', '事件创建/编辑', 'EVT', 'INTERNAL', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'TASK-READ-*-*-*', '任务查看', 'TSK', 'PUBLIC', '*', '*', NOW()::text, NOW()::text),
    (uuid_generate_v4()::text, 'TASK-WRITE-*-*-*', '任务创建/分配', 'TSK', 'INTERNAL', '*', '*', NOW()::text, NOW()::text);

-- ============================================================
-- 6. Verify
-- ============================================================
SELECT '--- Permission Groups ---' AS section;
SELECT code, name, company_type, department, org_level, min_level FROM permission_groups WHERE is_deleted = false ORDER BY code;

SELECT '--- SOP Business Flows ---' AS section;
SELECT sop_id, display_name, sop_type, security_level FROM sop_business_flows WHERE is_active = true ORDER BY sop_id;

SELECT '--- SOP Permission Bindings ---' AS section;
SELECT pg.code AS group_code, sbf.sop_id, sb.binding_type
FROM sop_permission_bindings sb
JOIN permission_groups pg ON sb.permission_group_id = pg.id
JOIN sop_business_flows sbf ON sb.sop_business_flow_id = sbf.id
ORDER BY sbf.sop_id, pg.code;

SELECT '--- Permission Grants ---' AS section;
SELECT g.perm_code, u1.username AS grantee, u2.username AS grantor, g.grant_type, g.remark
FROM permission_grants g
JOIN _su u1 ON g.grantee_id = u1.id
JOIN _su u2 ON g.grantor_id = u2.id
WHERE g.status = 'ACTIVE';

COMMIT;
