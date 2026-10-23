-- ============================================================
-- 015_register_doc_reading_note_sop.sql
--   播种「非友好格式资料加工（阅读笔记）」SOP 及其准入级别
--
-- 背景：SOP-018-FILE（阅读笔记）为新增业务流，文件落在
--   emily-data/sops/SOP-018-FILE-doc_reading_note.md，但 sop_business_flows 表内无行。
--   权限白名单按该表逐行判定；无行则该 SOP 不进 sop_allow，也不会被路由到。
--
-- 口径（线性「不低于」语义，与 008/010/011/012/013/014 一致）：
--   非友好格式加工与入库 → L5 管理员及以上（写操作：新增资料与笔记）。
--   下游查阅仍走 SOP-013-QRY（L3+）。
--
-- 工具：
--   officeCLI / parse_document / ocr_document / read_library_file 为只读；
--   ingest_knowledge_library 为 L5 写索引工具（handler 内 fail-closed）。
--   资料库文件的落盘当前无工具，由管理员在宿主机/容器内完成（SOP-018 §6.1）。
--
-- 幂等：ON CONFLICT (sop_id) 走 UPDATE，可重复执行。
--
-- Precondition: 006 + 008 + 014 已执行
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 015_register_doc_reading_note_sop.sql
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
    'SOP-018-FILE',
    'SOP-018-FILE-doc_reading_note.md',
    '非友好格式资料加工（阅读笔记）',
    'PPT/扫描件/图纸等资料生成可检索的伴生阅读笔记，按「放文件＋索引＋笔记」口径入库（L5）',
    'FILE',
    '资料管理',
    'INTERNAL',
    5,                       -- 准入门槛：L5 管理员及以上
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

-- 校验：min_level 应为 5
-- SELECT sop_id, display_name, min_level, is_public, is_active
--   FROM sop_business_flows WHERE sop_id = 'SOP-018-FILE';
