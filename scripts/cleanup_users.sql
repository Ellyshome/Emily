-- ============================================================
-- 用户表清理脚本
-- 保留：wangjianguo（生态城26#地项目总）
-- 删除：其他所有用户（包括测试数据、示例用户等）
-- ============================================================

BEGIN;

-- ============================================================
-- 第一步：查看当前所有用户（执行前先确认）
-- ============================================================
-- 取消下面的注释查看当前用户列表
-- SELECT id, username, real_name, phone, email, status, created_at 
-- FROM users 
-- WHERE is_deleted = false
-- ORDER BY created_at;

-- ============================================================
-- 第二步：逻辑删除其他所有用户（推荐：逻辑删除，保留历史）
-- ============================================================
-- 方式A：逻辑删除（推荐，数据可恢复）
UPDATE users 
SET 
    is_deleted = true,
    status = 'deleted',
    updated_at = to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
WHERE 
    username != 'wangjianguo'
    AND is_deleted = false;

-- 方式B：物理删除（不推荐，会破坏外键关联）
-- DELETE FROM users 
-- WHERE username != 'wangjianguo';

-- ============================================================
-- 第三步：清理关联表数据（如果有外键约束）
-- ============================================================
-- 清理已删除用户的IM绑定
UPDATE user_im_bindings 
SET status = 'deleted'
WHERE user_id IN (
    SELECT id FROM users WHERE is_deleted = true
);

-- ============================================================
-- 第四步：验证清理结果
-- ============================================================
-- 查看保留的用户
-- SELECT id, username, real_name, phone, email, status 
-- FROM users 
-- WHERE is_deleted = false
-- ORDER BY created_at;

-- 查看已删除的用户
-- SELECT COUNT(*) as deleted_count FROM users WHERE is_deleted = true;

COMMIT;

-- ============================================================
-- 回滚（如果需要恢复）
-- ============================================================
-- UPDATE users 
-- SET 
--     is_deleted = false,
--     status = 'active'
-- WHERE username != 'wangjianguo' AND is_deleted = true;
