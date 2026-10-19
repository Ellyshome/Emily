-- ============================================================
-- 013_register_site_report_sop.sql
--   播种「一线完工上报」SOP 及其准入级别
--
-- 背景：SOP-016-FLOW（一线完工上报）为新增业务流，文件落在
--   emily-data/sops/SOP-016-FLOW-site_report.md，但 sop_business_flows 表内无行。
--   权限白名单计算按该表逐行判定；无行则该 SOP 不进 sop_allow，也不会被路由到。
--
-- 口径（线性「不低于」语义，与 008/010/011/012 一致）：
--   一线完工上报 → L2 参建执行及以上。
--   L1 访客只做咨询，不进入上报。
--
-- 工具：
--   site_report 为 business/write_l2/sop_only，需本 SOP 声明后放行；
--   启动时由 TOOL_META_MAP 自动种子到 tool_registry（无需手工 INSERT）。
--   临时收容容器由 NodeContainerService 系统内部创建，不要求 L2 用户具备节点创建权限。
--
-- 幂等：ON CONFLICT (sop_id) 走 UPDATE，可重复执行。
--
-- Precondition: 006（sop_business_flows 已建并播种）+ 008（min_level 口径）先执行
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 013_register_site_report_sop.sql
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
    'SOP-016-FLOW',
    'SOP-016-FLOW-site_report.md',
    '一线完工上报',
    '一线员工上报完工部位/工程量/照片，记录事件并按全景节点归属归档（L2+）',
    'FLOW',
    '现场记录',
    'INTERNAL',
    2,                       -- 准入门槛：L2 参建执行及以上
    FALSE,                   -- 非公开：按 min_level 收敛，L1 不可用
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

-- 校验：min_level 应为 2
-- SELECT sop_id, display_name, min_level, is_public, is_active
--   FROM sop_business_flows WHERE sop_id = 'SOP-016-FLOW';
--
-- 校验：site_report 应在工具表中启用
-- SELECT api_id, category, permission_flag, exposure_mode, is_active
--   FROM tool_registry WHERE api_id = 'site_report';
