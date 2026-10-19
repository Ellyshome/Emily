-- ============================================================
-- 012_register_planting_schedule_review_sop.sql
--   播种「苗木表审核（设计阶段）」SOP 及其准入级别
--
-- 背景：SOP-015-QRY（设计阶段苗木表预审）为新增业务流，文件落在
--   emily-data/sops/SOP-015-QRY-planting_schedule_review.md，但
--   sop_business_flows 表内无行。权限白名单计算
--   （emily_core/services/permission_service.py::_compute_sop_allow）
--   按该表逐行判定；无行则该 SOP 不进 sop_allow，也不会被路由到。
--
-- 口径（线性「不低于」语义，与 008/010/011 一致）：
--   苗木表审核（设计阶段） → L3 参建管理及以上。
--
-- 部署前置：
--   1. 工具 officeCLI 随 emily-core 镜像构建安装（Dockerfile，版本 v1.0.157），
--      启动时由 TOOL_META_MAP 自动种子为 base / all / meta 并启用；无需手工插 tool_registry。
--   2. 如部署版本用 OCR 兜底扫描件，ocr_document 也需处于启用状态。
--
-- 幂等：ON CONFLICT (sop_id) 走 UPDATE，可重复执行。
--
-- Precondition: 006（sop_business_flows 已建并播种）+ 008（min_level 口径）先执行
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 012_register_planting_schedule_review_sop.sql
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
    'SOP-015-QRY',
    'SOP-015-QRY-planting_schedule_review.md',
    '苗木表审核（设计阶段）',
    '设计阶段苗木表预审：仅审核用户提交的 xlsx/pdf/docx，输出信息分析与逐条审核意见（L3+）',
    'QRY',
    '资料审核',
    'INTERNAL',
    3,                       -- 准入门槛：L3 参建管理及以上（线性"不低于"）
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
--   FROM sop_business_flows WHERE sop_id = 'SOP-015-QRY';
--
-- 校验：officeCLI 应在工具表中处于启用状态
-- SELECT api_id, display_name, category, permission_flag, exposure_mode, is_active
--   FROM tool_registry WHERE api_id IN ('officeCLI', 'ocr_document');
--
-- officeCLI 工具行由启动时的 _ensure_tool_registry_seed() 从 TOOL_META_MAP 自动写入，
-- 无需手工 INSERT；预期字段：category=base、permission_flag=all、exposure_mode=meta、
-- is_active=true。若查不到该行，先检查 emily-core 启动日志中的种子同步结果。
