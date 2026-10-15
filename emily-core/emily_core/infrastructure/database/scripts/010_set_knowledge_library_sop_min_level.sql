-- ============================================================
-- 010_set_knowledge_library_sop_min_level.sql —— 播种「资料库查阅与入库」SOP 及其准入级别
--
-- 背景：SOP-013-QRY（资料库 = 数字资产管理）为 2026-10-15 新增的业务流，文件落在
--   emily-data/sops/SOP-013-QRY-knowledge_library.md，但 sop_business_flows 表内无行。
--   权限白名单计算（emily_core/services/permission_service.py::_compute_sop_allow）按
--   该表逐行判定，无行则该 SOP 不进 sop_allow，其声明的工具也不会经 SOP 授权层放行
--   （session/fetchers/fetch_available_tools.py）—— 即「SOP 存在但没人能路由到它」。
--   故本脚本补一行记录并落 min_level。
--
-- 口径（线性「不低于」语义，非树形跨线继承 —— 与 008 一致）：
--   查阅资料库 → L3 参建管理及以上（US-06.2 / R4）
--   入库动作（ingest_knowledge_library）→ L5 门槛不在此表落值，由工具 handler 判定
--     （US-03.1：入库是工具级门槛，与 SOP 准入门槛分离）
--
-- 幂等：ON CONFLICT (sop_id) 走 UPDATE，可重复执行。
--   注意 create_all() 只建表不播种数据行，本行必须显式执行本脚本。
--
-- Precondition: 006（sop_business_flows 已建并播种）+ 008（min_level 口径）先执行
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 010_set_knowledge_library_sop_min_level.sql
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
    'SOP-013-QRY',
    'SOP-013-QRY-knowledge_library.md',
    '资料库查阅与入库',
    '查阅资料库（数字资产档案库）：列目录 / 读索引 / 读文件逐级下钻；入库另需 L5',
    'QRY',
    '资料管理',
    'INTERNAL',
    3,                       -- 查阅门槛：L3 参建管理及以上（线性"不低于"）
    FALSE,                   -- 非公开：按 min_level 收敛，L1/L2 不可用
    '[]',
    NULL,                    -- 不绑定默认权限组：可见性只由 min_level 决定
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
--   FROM sop_business_flows WHERE sop_id = 'SOP-013-QRY';
