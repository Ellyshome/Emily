-- ============================================================
-- 014_register_traceability_sop.sql
--   播种「信息追溯与时间线」SOP 及其准入级别
--
-- 背景：SOP-017-FLOW（信息追溯）为新增业务流，文件落在
--   emily-data/sops/SOP-017-FLOW-traceability.md，但 sop_business_flows 表内无行。
--   权限白名单按该表逐行判定；无行则该 SOP 不进 sop_allow，也不会被路由到。
--
-- 口径（线性「不低于」语义，与 008/010/011/012/013 一致）：
--   信息追溯 → L3 参建管理及以上。
--
-- 工具：
--   resolve_project / library_search / read_library_file /
--   read_knowledge_library / query_data 均为只读或既有工具；
--   library_search 需先由 registry/seed_tool_registry 注册。
--
-- 幂等：ON CONFLICT (sop_id) 走 UPDATE，可重复执行。
--
-- Precondition: 006 + 008 + 013 已执行
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 014_register_traceability_sop.sql
-- ============================================================

BEGIN;

INSERT INTO sop_business_flows (
    id, sop_id, sop_file_name, display_name, description,
    sop_type, category, security_level, min_level, is_public,
    required_node_ids, default_permission_group_id,
    version, is_active, creator_id, created_at, updated_at
)
SELECT
    uuid_generate_v4()::text,
    'SOP-017-FLOW',
    'SOP-017-FLOW-traceability.md',
    '信息追溯与时间线',
    '跨评审意见/会议纪要/工作群/事件记录串时间线，指出未闭环项（L3+）',
    'FLOW',
    '信息追溯',
    'INTERNAL',
    3,                       -- 准入门槛：L3 参建管理及以上
    FALSE,                   -- 非公开：按 min_level 收敛
    '[]',
    NULL,
    'V1.0',
    TRUE,
    NULL,
    NOW()::text,
    NOW()::text
ON CONFLICT (sop_id) DO UPDATE SET
    sop_file_name = EXCLUDED.sop_file_name,
    display_name  = EXCLUDED.display_name,
    description   = EXCLUDED.description,
    sop_type      = EXCLUDED.sop_type,
    category      = EXCLUDED.category,
    security_level = EXCLUDED.security_level,
    min_level     = EXCLUDED.min_level,
    is_public     = EXCLUDED.is_public,
    is_active     = TRUE,
    updated_at    = NOW()::text;

COMMIT;

-- 校验：min_level 应为 3
-- SELECT sop_id, display_name, min_level, is_public, is_active
--   FROM sop_business_flows WHERE sop_id = 'SOP-017-FLOW';
