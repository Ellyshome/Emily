-- ============================================================
-- 001_seed_sop_flows.sql —— 生产环境 SOP 业务流登记（11 条基础 SOP）
--
-- 为什么必须执行：
--   permission_service._compute_sop_allow() 在 sop_business_flows **为空**时会 fallback 到
--   SkillRegistry，把磁盘上所有 SOP 无条件放行给所有用户（含 L1 访客可用的 SYS 管理类 SOP）。
--   新库若不播种本表，等于权限矩阵失效。
--
-- 与开发种子的区别：
--   · 数据取自 emily_core/infrastructure/database/scripts/006_seed_permission_data.sql
--     的 sop_business_flows 段，但去掉了对测试用户（王建国）与测试权限组的依赖
--     —— creator_id / default_permission_group_id 置 NULL（两列均可空）。
--   · min_level 口径取自 008_set_sop_min_level.sql，直接写入，无需再跑 008。
--
-- 幂等：ON CONFLICT (sop_id) 走 UPDATE，可重复执行。
-- 说明：SOP-013（资料库）、SOP-014~018 由 010~015 注册脚本另行插入，不在本文件内。
-- ============================================================

BEGIN;

INSERT INTO sop_business_flows (
    id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, required_node_ids,
    default_permission_group_id, min_level, is_public,
    version, is_active, creator_id, created_at, updated_at
) VALUES
    (gen_random_uuid()::text, 'SOP-000-SYS', 'SOP-000-SYS.skill.yaml', '系统管理',
     '系统级操作：配置管理、权限管理、日志查询等',
     'SYS', '系统管理', 'PRIVATE', '[]', NULL, 5, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-001-REC', 'SOP-001-REC.skill.yaml', '事件记录',
     '记录项目事件：里程碑、施工记录、验收记录、安全巡检等',
     'REC', '工程记录', 'INTERNAL', '[]', NULL, 2, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-002-REC', 'SOP-002-REC.skill.yaml', '任务管理',
     '创建、分配、跟踪工作任务的执行状态',
     'REC', '工程记录', 'INTERNAL', '[]', NULL, 2, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-003-REC', 'SOP-003-REC.skill.yaml', '会议纪要',
     '记录会议纪要，含结论、行动项分配、关联文件',
     'REC', '工程记录', 'INTERNAL', '[]', NULL, 2, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-004-FILE', 'SOP-004-FILE.skill.yaml', '文件管理',
     '上传、检索、版本管理项目文件',
     'FILE', '文件管理', 'INTERNAL', '[]', NULL, 2, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-005-QRY', 'SOP-005-QRY.skill.yaml', '信息查询',
     '查询项目信息、节点进度、任务状态、文件目录等',
     'QRY', '信息查询', 'INTERNAL', '[]', NULL, 1, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-007-REC', 'SOP-007-REC.skill.yaml', '流转单管理',
     '业务流转单（设计变更、签证、验收申请、付款申请等）',
     'FLOW', '业务流转', 'INTERNAL', '[]', NULL, 2, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-008-SYS', 'SOP-008-SYS.skill.yaml', '全景节点管理',
     '全景节点图管理：创建、更新、激活、废弃节点，管理交付物',
     'SYS', '系统管理', 'PRIVATE', '[]', NULL, 5, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-011-SYS', 'SOP-011-SYS.skill.yaml', '权限管理',
     '权限组管理、授权审批、权限审计',
     'SYS', '系统管理', 'PRIVATE', '[]', NULL, 5, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-012-SYS', 'SOP-012-SYS-personnel_org.md', '人员与企业维护',
     '人员权限等级调整、企业归属调整、企业档案增删（授权由服务层 PersonnelService 按操作人等级判定）',
     'SYS', '系统管理', 'PRIVATE', '[]', NULL, 3, false, 'V1.0', true, NULL, NOW()::text, NOW()::text),

    (gen_random_uuid()::text, 'SOP-999-SYS', 'SOP-999-SYS.skill.yaml', '通用兜底',
     '未匹配到特定 SOP 时的通用处理入口',
     'SYS', '系统管理', 'PUBLIC', '[]', NULL, 1, true, 'V1.0', true, NULL, NOW()::text, NOW()::text)
ON CONFLICT (sop_id) DO UPDATE SET
    display_name    = EXCLUDED.display_name,
    description     = EXCLUDED.description,
    sop_type        = EXCLUDED.sop_type,
    category        = EXCLUDED.category,
    security_level  = EXCLUDED.security_level,
    min_level       = EXCLUDED.min_level,
    is_public       = EXCLUDED.is_public,
    is_active       = true,
    updated_at      = EXCLUDED.updated_at;

COMMIT;
