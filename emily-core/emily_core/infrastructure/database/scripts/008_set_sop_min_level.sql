-- ============================================================
-- 008_set_sop_min_level.sql —— 补齐 sop_business_flows 的准入级别
--
-- 背景：006 播种时未写入 min_level / is_public，两者全为 NULL。而白名单计算
--   （emily_core/permission/cache.py::_compute_user_whitelist）在 min_level 为
--   NULL 时会跳过级别检查并无条件放行，导致 sop_allow 对**所有已登记用户**都等于
--   全部 SOP 清单（L1 也在内），SOP 授权因此不构成权限信号。
--
-- 补齐后 sop_allow 才真正按级别收敛，进而使「已授权 SOP 放行其声明工具」
--   （session/fetchers/fetch_available_tools.py 的 SOP 授权层）成为安全的放行依据。
--
-- 级别判定为线性「不低于」语义（level >= min_level），非树形跨线继承 —— 否则
--   建设线 L4/L5/L6 会因不继承参建线 L2 而失去参建类 SOP。
--
-- 口径：
--   SOP-001/002/003/004/007（会议/事件/任务/文件/长期记忆）→ L2 参建执行及以上
--   SOP-005-QRY（信息查询）                                → L1 访客及以上
--   SOP-000/008/011（SYS 系统类）                          → L5 管理员及以上
--   SOP-012-SYS（人员与企业维护，2026-09-18 新增）          → L3 参建管理及以上
--   SOP-999-SYS（通用兜底）                                → is_public，全员可用
--
-- Precondition: 006 must be run first (sop_business_flows 已播种)
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 008_set_sop_min_level.sql
-- ============================================================

BEGIN;

UPDATE sop_business_flows SET min_level = 2
 WHERE sop_id IN ('SOP-001-REC', 'SOP-002-REC', 'SOP-003-REC', 'SOP-004-FILE', 'SOP-007-REC');

UPDATE sop_business_flows SET min_level = 1
 WHERE sop_id = 'SOP-005-QRY';

UPDATE sop_business_flows SET min_level = 5
 WHERE sop_id IN ('SOP-000-SYS', 'SOP-008-SYS', 'SOP-011-SYS');

-- 人员与企业维护：L3 参建管理及以上（实际分级由服务层 PersonnelService 判定）
UPDATE sop_business_flows SET min_level = 3
 WHERE sop_id = 'SOP-012-SYS';

-- 通用兜底对全员开放（含访客），与 SOP-999-SYS 的定位一致
UPDATE sop_business_flows SET is_public = TRUE
 WHERE sop_id = 'SOP-999-SYS';

COMMIT;

-- 校验：应为 0 行 NULL
-- SELECT count(*) AS null_min_level FROM sop_business_flows WHERE min_level IS NULL AND is_public IS NOT TRUE;
