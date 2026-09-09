-- ============================================================
-- 007_seed_emerald_project.sql —— 翠湖庭院项目 EMERALD-01 + 指标 + 18个模拟文件
--
-- Precondition: 002_seed_test_data.sql must be run first
--                002_seed_test_data_patch.sql recommended (adds 刘大勇)
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 007_seed_emerald_project.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 临时表：用户和公司 UUID 查找
-- ============================================================
CREATE TEMP TABLE IF NOT EXISTS _su AS
SELECT id, username, level FROM users WHERE is_deleted = false;

CREATE TEMP TABLE IF NOT EXISTS _sc AS
SELECT id, company_name, type FROM company_info WHERE is_deleted = false;

-- ============================================================
-- 2. 插入项目 EMERALD-01 翠湖庭院住宅小区
-- ============================================================
INSERT INTO projects (id, code, name, description, status, address, city,
    lifecycle_stage, stage_updated_at, creator_id, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text,
    'EMERALD-01',
    '翠湖庭院住宅小区',
    '翠湖庭院住宅小区建设工程，总建筑面积约9,850㎡，5栋5-6层框架-剪力墙结构住宅楼，合计80户。坡屋顶，外墙真石漆，容积率1.59，绿化率38%。',
    'active',
    '苏州市滨湖区翠湖路88号',
    '苏州市',
    2,  -- lifecycle_stage=2 (工程施工阶段)
    NOW()::text,
    u.id,
    NOW()::text,
    NOW()::text,
    false
FROM _su u WHERE u.username = '王建国';

-- 捕获项目 ID 供后续使用
CREATE TEMP TABLE _sp AS
SELECT id, code FROM projects WHERE code = 'EMERALD-01' AND is_deleted = false;

-- ============================================================
-- 3. 插入5条项目指标
-- ============================================================
INSERT INTO project_indicator_details (id, project_id, indicator_name,
    indicator_value, unit, source_file_id, description, is_constraint,
    creator_id, created_at, updated_at, is_deleted)
SELECT
    uuid_generate_v4()::text, p.id, '总建筑面积', '9850', '㎡',
    NULL, '地上建筑面积约7,800㎡，地下建筑面积约2,050㎡', true,
    u.id, NOW()::text, NOW()::text, false
FROM _sp p, _su u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '容积率', '1.59', '-',
    NULL, '规划条件要求容积率≤1.8', true,
    u.id, NOW()::text, NOW()::text, false
FROM _sp p, _su u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '绿化率', '38', '%',
    NULL, '规划条件要求绿化率≥35%', true,
    u.id, NOW()::text, NOW()::text, false
FROM _sp p, _su u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '合同总工期', '450', '天',
    NULL, '自开工令签发之日起计算', true,
    u.id, NOW()::text, NOW()::text, false
FROM _sp p, _su u WHERE u.username = '王建国'
UNION ALL
SELECT
    uuid_generate_v4()::text, p.id, '总投资额', '26000000', '元',
    NULL, '建安工程费约1800万元', true,
    u.id, NOW()::text, NOW()::text, false
FROM _sp p, _su u WHERE u.username = '王建国';

-- ============================================================
-- 4. 辅助函数：自动生成 file_no
-- ============================================================
CREATE OR REPLACE FUNCTION _seed_file_no(seq int) RETURNS text AS $$
BEGIN
    RETURN 'FIL-' || TO_CHAR(NOW(), 'YYYYMMDD') || '-' || LPAD(seq::text, 4, '0');
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 5. 插入18个模拟文件
--    files 表字段: id, file_no, project_id, filename, file_type,
--    bucket, object_key, storage_path, file_size, uploaded_by,
--    file_ext, file_category, confidentiality, version, is_latest,
--    creator_id, created_at, updated_at, is_deleted,
--    source_module_type, source_module_id
-- ============================================================

-- -----------------------------------------------------------
-- 5.1 项目证照 (PROJECT_LICENSE) — 5 files (#1-#5)
-- -----------------------------------------------------------

-- File #1: 国有建设用地使用权出让合同.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(1), p.id, '国有建设用地使用权出让合同.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/licenses/land_contract.pdf',
    '/mock/project-emerald/licenses/land_contract.pdf',
    1843200, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'EMR-LX-01-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- File #2: 建设用地规划许可证.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(2), p.id, '建设用地规划许可证.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/licenses/land_use_permit.pdf',
    '/mock/project-emerald/licenses/land_use_permit.pdf',
    1228800, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-GH-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- File #3: 建设工程规划许可证.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(3), p.id, '建设工程规划许可证.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/licenses/construction_planning_permit.pdf',
    '/mock/project-emerald/licenses/construction_planning_permit.pdf',
    1536000, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-GH-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- File #4: 建筑工程施工许可证.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(4), p.id, '建筑工程施工许可证.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/licenses/construction_permit.pdf',
    '/mock/project-emerald/licenses/construction_permit.pdf',
    1024000, u1.id, '.pdf', 'PROJECT_LICENSE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-SG-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- File #5: 不动产权证（土地证）.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(5), p.id, '不动产权证（土地证）.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/licenses/property_right_cert.pdf',
    '/mock/project-emerald/licenses/property_right_cert.pdf',
    1536000, u1.id, '.pdf', 'PROJECT_LICENSE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-LX-01-02'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- -----------------------------------------------------------
-- 5.2 承包合同 (CONTRACT) — 3 files (#6-#8)
-- -----------------------------------------------------------

-- File #6: 建设工程施工总承包合同.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(6), p.id, '建设工程施工总承包合同.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/contracts/general_contract.pdf',
    '/mock/project-emerald/contracts/general_contract.pdf',
    4608000, u1.id, '.pdf', 'CONTRACT', 3, 'V1.0', true,
    u1.id, NOW()::text, NOW()::text, false,
    'NODE_WORKLOAD_DOC', 'EMR-SG-01'
FROM _sp p, _su u1
WHERE u1.username = '王建国';

-- File #7: 建设工程委托监理合同.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(7), p.id, '建设工程委托监理合同.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/contracts/supervision_contract.pdf',
    '/mock/project-emerald/contracts/supervision_contract.pdf',
    2560000, u1.id, '.pdf', 'CONTRACT', 3, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-SG-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- File #8: 建设工程设计合同.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(8), p.id, '建设工程设计合同.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/contracts/design_contract.pdf',
    '/mock/project-emerald/contracts/design_contract.pdf',
    3072000, u1.id, '.pdf', 'CONTRACT', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-GH-01-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '王建国' AND u2.username = '李景利';

-- -----------------------------------------------------------
-- 5.3 阶段成果 (PHASE_DELIVERABLE) — 6 files (#9-#14)
-- -----------------------------------------------------------

-- File #9: 方案设计文本.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(9), p.id, '方案设计文本.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/deliverables/schematic_design.pdf',
    '/mock/project-emerald/deliverables/schematic_design.pdf',
    15360000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_DELIVERABLE_DOC', 'EMR-GH-01-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '赵明远' AND u2.username = '李景利';

-- File #10: 施工图设计文件.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(10), p.id, '施工图设计文件.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/deliverables/construction_drawings.pdf',
    '/mock/project-emerald/deliverables/construction_drawings.pdf',
    25600000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-GH-01-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '赵明远' AND u2.username = '李景利';

-- File #11: 地基与基础分部验收报告.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(11), p.id, '地基与基础分部验收报告.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/deliverables/foundation_acceptance.pdf',
    '/mock/project-emerald/deliverables/foundation_acceptance.pdf',
    12288000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-SG-01-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '陈建华' AND u2.username = '李景利';

-- File #12: 主体结构分部验收报告.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(12), p.id, '主体结构分部验收报告.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/deliverables/main_structure_acceptance.pdf',
    '/mock/project-emerald/deliverables/main_structure_acceptance.pdf',
    10240000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-SG-01-02'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '陈建华' AND u2.username = '李景利';

-- File #13: 机电安装分部验收报告.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(13), p.id, '机电安装分部验收报告.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/deliverables/mechanical_acceptance.pdf',
    '/mock/project-emerald/deliverables/mechanical_acceptance.pdf',
    10240000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-SG-01-03'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '陈建华' AND u2.username = '李景利';

-- File #14: 场地平整压实验收记录.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(14), p.id, '场地平整压实验收记录.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/deliverables/ground_compaction_record.pdf',
    '/mock/project-emerald/deliverables/ground_compaction_record.pdf',
    12288000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_DELIVERABLE_DOC', 'EMR-SG-01-04-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '刘大勇' AND u2.username = '李景利';

-- -----------------------------------------------------------
-- 5.4 过程文件 (PROCESS_DOC) — 2 files (#15-#16)
-- -----------------------------------------------------------

-- File #15: 图纸会审记录.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(15), p.id, '图纸会审记录.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/process/drawing_review_record.pdf',
    '/mock/project-emerald/process/drawing_review_record.pdf',
    1024000, u1.id, '.pdf', 'PROCESS_DOC', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_WORKLOAD_DOC', 'EMR-SG-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '张正宏' AND u2.username = '李景利';

-- File #16: 设计变更通知单（结构）.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(16), p.id, '设计变更通知单（结构）.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/process/design_change_notice.pdf',
    '/mock/project-emerald/process/design_change_notice.pdf',
    819200, u1.id, '.pdf', 'PROCESS_DOC', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    NULL, 'EMR-SG-01-02'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '赵明远' AND u2.username = '李景利';

-- -----------------------------------------------------------
-- 5.5 阶段成果补充 (PHASE_DELIVERABLE) — 1 file (#17)
-- -----------------------------------------------------------

-- File #17: 绿化景观施工图设计文件.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(17), p.id, '绿化景观施工图设计文件.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/deliverables/landscape_design.pdf',
    '/mock/project-emerald/deliverables/landscape_design.pdf',
    18432000, u1.id, '.pdf', 'PHASE_DELIVERABLE', 2, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_DELIVERABLE_DOC', 'EMR-SG-01-05'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '赵明远' AND u2.username = '李景利';

-- -----------------------------------------------------------
-- 5.6 管理规程 (MANAGEMENT_SPEC) — 1 file (#18)
-- -----------------------------------------------------------

-- File #18: 项目质量管理制度.pdf
INSERT INTO files (id, file_no, project_id, filename, file_type, bucket,
    object_key, storage_path, file_size, uploaded_by, file_ext, file_category,
    confidentiality, version, is_latest, creator_id, created_at, updated_at,
    is_deleted, source_module_type, source_module_id)
SELECT
    uuid_generate_v4()::text, _seed_file_no(18), p.id, '项目质量管理制度.pdf',
    'application/pdf', 'mock-bucket', 'project-emerald/management/quality_management_system.pdf',
    '/mock/project-emerald/management/quality_management_system.pdf',
    640000, u1.id, '.pdf', 'MANAGEMENT_SPEC', 1, 'V1.0', true,
    u2.id, NOW()::text, NOW()::text, false,
    'NODE_STARTUP_DOC', 'EMR-SG-01'
FROM _sp p, _su u1, _su u2
WHERE u1.username = '李景利' AND u2.username = '王建国';

-- ============================================================
-- 6. 验证查询
-- ============================================================

-- 6.1 项目概览
SELECT '--- 项目概览 ---' AS section;
SELECT code, name, lifecycle_stage,
    CASE lifecycle_stage
        WHEN 0 THEN '立项'
        WHEN 1 THEN '规划设计'
        WHEN 2 THEN '工程施工'
        WHEN 3 THEN '交付结算'
        ELSE '未知'
    END AS stage_name,
    status
FROM projects WHERE code = 'EMERALD-01';

-- 6.2 项目指标
SELECT '--- 项目指标 ---' AS section;
SELECT indicator_name, indicator_value, unit, is_constraint, description
FROM project_indicator_details
WHERE project_id = (SELECT id FROM _sp LIMIT 1)
ORDER BY indicator_name;

-- 6.3 文件清单
SELECT '--- 文件清单（共18个） ---' AS section;
SELECT file_no, filename,
    CASE file_category
        WHEN 'PROJECT_LICENSE' THEN '项目证照'
        WHEN 'CONTRACT' THEN '承包合同'
        WHEN 'PHASE_DELIVERABLE' THEN '阶段成果'
        WHEN 'PROCESS_DOC' THEN '过程文件'
        WHEN 'MANAGEMENT_SPEC' THEN '管理规程'
        ELSE file_category
    END AS "文件分类",
    CASE confidentiality
        WHEN 1 THEN '公开'
        WHEN 2 THEN '内部'
        WHEN 3 THEN '机密'
        ELSE confidentiality::text
    END AS "保密级别",
    (file_size / 1024) || ' KB' AS "文件大小"
FROM files
WHERE project_id = (SELECT id FROM _sp LIMIT 1)
ORDER BY file_no;

-- 6.4 文件统计
SELECT '--- 文件统计 ---' AS section;
SELECT
    file_category,
    COUNT(*) AS file_count
FROM files
WHERE project_id = (SELECT id FROM _sp LIMIT 1)
GROUP BY file_category
ORDER BY file_category;

-- ============================================================
-- 6b. 用户项目归属回填
--     002 脚本创建用户时不设 project_id，在项目创建后回填
-- ============================================================
UPDATE users
SET project_id = (SELECT id FROM _sp LIMIT 1)
WHERE (project_id IS NULL OR project_id = '') AND is_deleted = false;

-- ============================================================
-- 7. 清理
-- ============================================================
DROP FUNCTION IF EXISTS _seed_file_no;
DROP TABLE IF EXISTS _sp;
DROP TABLE IF EXISTS _su;
DROP TABLE IF EXISTS _sc;

COMMIT;
