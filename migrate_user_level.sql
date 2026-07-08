-- ========================================
-- 迁移 users.is_admin 到 users.level 枚举字段
-- ========================================

-- Step 1: 创建枚举类型
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_level_enum') THEN
        CREATE TYPE user_level_enum AS ENUM (
            '系统管理员',
            '管理员',
            '建设主管',
            '参建主管',
            '参建人员',
            '访客'
        );
    END IF;
END
$$;

-- Step 2: 添加 level 字段
ALTER TABLE users ADD COLUMN IF NOT EXISTS level user_level_enum;

-- Step 3: 数据迁移
-- is_admin = TRUE -> 管理员
UPDATE users SET level = '管理员' WHERE is_admin = TRUE;

-- is_admin = FALSE 或 NULL -> 访客（默认）
UPDATE users SET level = '访客' 
WHERE (is_admin = FALSE OR is_admin IS NULL) AND level IS NULL;

-- ========================================
-- 验证结果
-- ========================================

-- 统计分布
SELECT level, COUNT(*) AS user_count 
FROM users 
GROUP BY level 
ORDER BY level;

-- 查看前5条示例
SELECT id, username, is_admin, level 
FROM users 
ORDER BY id 
LIMIT 5;
