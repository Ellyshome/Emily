-- ========================================
-- 迁移 users.permission_level 到 users.level 字段
-- 说明：字段名统一，类型保持 INTEGER (1-6)
-- ========================================

BEGIN;

-- Step 1: 新增 level 字段（若不存在）
ALTER TABLE users ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 1;

-- Step 2: 数据迁移（从 permission_level 复制到 level）
UPDATE users 
SET level = permission_level 
WHERE permission_level IS NOT NULL AND level IS NULL;

-- Step 3: 兼容处理（若 permission_level 不存在，则设置默认值）
UPDATE users 
SET level = 1 
WHERE level IS NULL;

-- Step 4: 添加注释
COMMENT ON COLUMN users.level IS '权限层级（6级树形）：1=访客 2=参建执行 3=参建管理 4=建设主管 5=管理员 6=系统管理员';

COMMIT;

-- ========================================
-- 验证结果
-- ========================================

-- 统计分布
SELECT level, COUNT(*) AS user_count 
FROM users 
GROUP BY level 
ORDER BY level;

-- 查看前5条示例
SELECT id, username, level
FROM users 
ORDER BY id 
LIMIT 5;
