-- ============================================================
-- 清理 users 表的无用字段
-- 执行前请先备份！
-- ============================================================

-- 检查当前 users 表结构（PostgreSQL）
-- SELECT column_name, data_type, is_nullable 
-- FROM information_schema.columns 
-- WHERE table_name = 'users' 
-- ORDER BY ordinal_position;

-- 删除测试沉淀的无用字段（根据实际情况调整）
-- 下面列出可能的无用字段，请根据实际表结构取消注释

-- ALTER TABLE users DROP COLUMN IF EXISTS test_field_1;
-- ALTER TABLE users DROP COLUMN IF EXISTS temp_column;
-- ALTER TABLE users DROP COLUMN IF EXISTS old_data;
-- ALTER TABLE users DROP COLUMN IF EXISTS migration_backup;
-- ALTER TABLE users DROP COLUMN IF EXISTS debug_info;
-- ALTER TABLE users DROP COLUMN IF EXISTS experimental_feature;

-- 清理完成后，重建索引（如果需要）
-- REINDEX TABLE users;

-- ============================================================
-- 验证：查看清理后的表结构
-- ============================================================

SELECT 
    column_name, 
    data_type, 
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'users' 
ORDER BY ordinal_position;

-- 统计当前用户数
SELECT COUNT(*) as total_users FROM users WHERE is_deleted = false;
