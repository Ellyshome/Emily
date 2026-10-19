-- ============================================================
-- 011_register_video_download_sop.sql —— 播种「视频下载」SOP 及其准入级别
--
-- 背景：SOP-014-FILE（yt-dlp 公开视频下载）为新增业务流，文件落在
--   emily-data/sops/SOP-014-FILE-video_download.md，但 sop_business_flows 表内无行。
--   权限白名单计算（emily_core/services/permission_service.py::_compute_sop_allow）按
--   该表逐行判定，无行则该 SOP 不进 sop_allow，其声明的工具也不会经 SOP 授权层放行
--   （session/fetchers/fetch_available_tools.py）—— 即「SOP 存在但没人能路由到它」。
--   故本脚本补一行记录并落 min_level。
--
-- 口径（线性「不低于」语义，非树形跨线继承 —— 与 008/010 一致）：
--   视频下载 → L3 参建管理及以上。
--   工具 download_video 另在 handler 内做 check_min_level(3) 兜底（fail-closed）。
--
-- 幂等：ON CONFLICT (sop_id) 走 UPDATE，可重复执行。
--   注意 create_all() 只建表不播种数据行，本行必须显式执行本脚本。
--
-- Precondition: 006（sop_business_flows 已建并播种）+ 008（min_level 口径）先执行
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 011_register_video_download_sop.sql
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
    'SOP-014-FILE',
    'SOP-014-FILE-video_download.md',
    '视频下载',
    '用 yt-dlp 下载公开视频（B站/YouTube/Vimeo）并归档，随后发送给当前会话用户（L3+）',
    'FILE',
    '文件管理',
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
--   FROM sop_business_flows WHERE sop_id = 'SOP-014-FILE';
