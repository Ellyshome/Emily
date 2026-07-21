-- ============================================================
-- 003_seed_project_data.sql —— 项目 + 指标 + 文件种子数据
--
-- Precondition: 002_seed_test_data.sql must be run first (users + companies)
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 003_seed_project_data.sql
-- ============================================================

BEGIN;

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. Create temp table for UUID lookups (same pattern as 002)
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _seed_users AS
SELECT id, username, level FROM users WHERE is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _seed_companies AS
SELECT id, company_name, type FROM company_info WHERE is_deleted = false;

-- ============================================================
-- 2. Create test project: ECOCITY-26 生态城一期
-- ============================================================
INSERT INTO projects (id, code, name, description, status, address, city,
    lifecycle_stage, stage_updated_at, creator_id, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text,
    'ECOCITY-26',
    '生态城一期项目',
    '生态城一期建设工程，总建筑面积约15.6万㎡，含6栋高层住宅、配套商业及地下车库。结构形式为框架-剪力墙，设计使用年限50年，抗震设防烈度7度。',
    'active',
    'XX市生态城区核心板块A-01地块',
    'XX市',
    2,  -- lifecycle_stage=2 (工程施工阶段)
    NOW()::text,
    u.id,
    NOW()::text,
    NOW()::text,
    false
FROM _seed_users u WHERE u.username = '王建国';

-- Capture project UUID for later use
CREATE TEMP TABLE _seed_project AS
SELECT id, code FROM projects WHERE code = 'ECOCITY-26' AND is_deleted = false;

-- ============================================================
-- 3. Project indicators (3-5 items)
-- ============================================================
INSERT INTO project_indicator_details (id, project_id, indicator_name, indicator_value, unit,
    source_file_id, description, is_constraint, creator_id, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, p.id, '总建筑面积', '156000', '㎡',
    NULL, '地上建筑面积约12.8万㎡，地下建筑面积约2.8万㎡', true,
    u.id, NOW()::text, NOW()::text, false
FROM _seed_project p, _seed_users u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '容积率', '2.5', '-',
    NULL, '规划条件要求容积率≤2.8', false,
    u.id, NOW()::text, NOW()::text, false
FROM _seed_project p, _seed_users u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '绿化率', '35', '%',
    NULL, '规划条件要求绿化率≥30%', false,
    u.id, NOW()::text, NOW()::text, false
FROM _seed_project p, _seed_users u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '合同总工期', '730', '天',
    NULL, '自开工令签发之日起计算，含不可抗力宽限期30天', true,
    u.id, NOW()::text, NOW()::text, false
FROM _seed_project p, _seed_users u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '总投资额', '485000000', '元',
    NULL, '建安工程费约3.2亿元，其他费用约1.65亿元', true,
    u.id, NOW()::text, NOW()::text, false
FROM _seed_project p, _seed_users u WHERE u.username = '王建国';

-- ============================================================
-- 4. Seed files — 15 mock project documents
-- ============================================================

-- Helper: auto-generate file_no as FIL-YYYYMMDD-NNNN
CREATE OR REPLACE FUNCTION _seed_file_no(seq int) RETURNS text AS $$
BEGIN
    RETURN 'FIL-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || LPAD(seq::text, 4, '0');
END;
$$ LANGUAGE plpgsql;

-- 4.1 Project Licenses (PROJECT_LICENSE) — 5 files
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(1), p.id, '国有建设用地使用权出让合同.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/licenses/land_contract.pdf',
    '/mock/project-ecocity/licenses/land_contract.pdf',
    2048000, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'ECOC-LX-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(2), p.id, '建设用地规划许可证.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/licenses/land_use_permit.pdf',
    '/mock/project-ecocity/licenses/land_use_permit.pdf',
    1024000, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'ECOC-GH-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(3), p.id, '建设工程规划许可证.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/licenses/construction_planning_permit.pdf',
    '/mock/project-ecocity/licenses/construction_planning_permit.pdf',
    1536000, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'ECOC-GH-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(4), p.id, '建筑工程施工许可证.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/licenses/construction_permit.pdf',
    '/mock/project-ecocity/licenses/construction_permit.pdf',
    1280000, u1.id, '.pdf', 'PROJECT_LICENSE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'ECOC-SG-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(5), p.id, '不动产权证（土地证）.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/licenses/property_right_cert.pdf',
    '/mock/project-ecocity/licenses/property_right_cert.pdf',
    896000, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'ECOC-LX-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- 4.2 Contracts (CONTRACT) — 3 files
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(6), p.id, '建设工程施工总承包合同.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/contracts/general_contract.pdf',
    '/mock/project-ecocity/contracts/general_contract.pdf',
    5120000, u1.id, '.pdf', 'CONTRACT', 3, 'V1.0', true,
    u1.id, NOW()::text, NOW()::text, false,
    'NODE_WORKLOAD_DOC', 'ECOC-SG-01'
FROM _seed_project p, _seed_users u1
WHERE u1.username = '王建国';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(7), p.id, '建设工程委托监理合同.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/contracts/supervision_contract.pdf',
    '/mock/project-ecocity/contracts/supervision_contract.pdf',
    2048000, u1.id, '.pdf', 'CONTRACT', 3, 'V1.0', true,
    u1.id, NOW()::text, NOW()::text, false,
    'NODE_WORKLOAD_DOC', 'ECOC-SG-01'
FROM _seed_project p, _seed_users u1
WHERE u1.username = '王建国';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(8), p.id, '建设工程设计合同.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/contracts/design_contract.pdf',
    '/mock/project-ecocity/contracts/design_contract.pdf',
    1536000, u1.id, '.pdf', 'CONTRACT', 2, 'V1.0', true,
    u1.id, NOW()::text, NOW()::text, false,
    'NODE_WORKLOAD_DOC', 'ECOC-GH-01'
FROM _seed_project p, _seed_users u1
WHERE u1.username = '王建国';

-- 4.3 Phase Deliverables (PHASE_DELIVERABLE) — 4 files
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(9), p.id, '方案设计文本.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/deliverables/schematic_design.pdf',
    '/mock/project-ecocity/deliverables/schematic_design.pdf',
    10240000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_DELIVERABLE_DOC', 'ECOC-GH-01-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '赵明远' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(10), p.id, '施工图设计文件.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/deliverables/construction_drawings.pdf',
    '/mock/project-ecocity/deliverables/construction_drawings.pdf',
    25600000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 2, 'V2.1', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_DELIVERABLE_DOC', 'ECOC-GH-01-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '赵明远' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(11), p.id, '地基与基础分部验收报告.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/deliverables/foundation_acceptance.pdf',
    '/mock/project-ecocity/deliverables/foundation_acceptance.pdf',
    1536000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_DELIVERABLE_DOC', 'ECOC-SG-01-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '陈建华' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(12), p.id, '主体结构分部验收报告.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/deliverables/main_structure_acceptance.pdf',
    '/mock/project-ecocity/deliverables/main_structure_acceptance.pdf',
    0, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_DELIVERABLE_DOC', 'ECOC-SG-01-02'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '陈建华' AND u2.username = '李景利';

-- 4.4 Process Documents (PROCESS_DOC) — 2 files
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(13), p.id, '图纸会审记录.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/process/drawing_review_record.pdf',
    '/mock/project-ecocity/process/drawing_review_record.pdf',
    768000, u1.id, '.pdf', 'PROCESS_DOC', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_WORKLOAD_DOC', 'ECOC-SG-01-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '张正宏' AND u2.username = '李景利';

INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(14), p.id, '设计变更通知单（结构）.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/process/design_change_notice.pdf',
    '/mock/project-ecocity/process/design_change_notice.pdf',
    512000, u1.id, '.pdf', 'PROCESS_DOC', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_WORKLOAD_DOC', 'ECOC-SG-01-02'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '赵明远' AND u2.username = '李景利';

-- 4.5 Management Specs (MANAGEMENT_SPEC) — 1 file
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket, object_key, storage_path,
    file_size, uploaded_by, file_ext, file_category, confidentiality, version, is_latest,
    creator_id, created_at, updated_at, is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(15), p.id, '项目质量管理制度.pdf',
    'application/pdf', 'mock-bucket', 'project-ecocity/management/quality_management_system.pdf',
    '/mock/project-ecocity/management/quality_management_system.pdf',
    640000, u1.id, '.pdf', 'MANAGEMENT_SPEC', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'ECOC-SG-01'
FROM _seed_project p, _seed_users u1, _seed_users u2
WHERE u1.username = '李景利' AND u2.username = '王建国';

-- ============================================================
-- 5. Verify
-- ============================================================
SELECT '--- Project ---' AS section;
SELECT code, name, lifecycle_stage,
    CASE lifecycle_stage WHEN 0 THEN '立项' WHEN 1 THEN '规划设计' WHEN 2 THEN '工程施工' WHEN 3 THEN '交付结算' ELSE '未知' END AS stage_name,
    status FROM projects WHERE code = 'ECOCITY-26';

SELECT '--- Project Indicators ---' AS section;
SELECT indicator_name, indicator_value, unit, is_constraint FROM project_indicator_details WHERE project_id = (SELECT id FROM _seed_project LIMIT 1);

SELECT '--- Files ---' AS section;
SELECT file_no, filename, file_category,
    CASE file_category
        WHEN 'PROJECT_LICENSE' THEN '项目证照'
        WHEN 'CONTRACT' THEN '承包合同'
        WHEN 'PHASE_DELIVERABLE' THEN '阶段成果'
        WHEN 'PROCESS_DOC' THEN '过程文件'
        WHEN 'MANAGEMENT_SPEC' THEN '管理规程'
        ELSE file_category
    END AS category_name,
    confidentiality FROM files WHERE project_id = (SELECT id FROM _seed_project LIMIT 1) ORDER BY file_no;

-- Cleanup function
DROP FUNCTION IF EXISTS _seed_file_no;

COMMIT;
