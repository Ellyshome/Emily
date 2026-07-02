-- ============================================================
-- 填充 users 表必填字段（username、creator_id、created_at、updated_at）
-- 针对现有数据中字段为空的记录进行逻辑自洽的填充
-- ============================================================

-- ============================================================
-- 1. 首先确保有一个系统用户作为 creator 的默认值
-- ============================================================

-- 如果没有系统用户，创建一个作为默认创建者
DO $$
DECLARE
    v_system_user_id TEXT;
    v_system_user_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_system_user_count FROM users WHERE username = 'system';
    
    IF v_system_user_count = 0 THEN
        v_system_user_id := uuid_generate_v4()::TEXT;
        
        INSERT INTO users (
            id, username, phone, email, status, is_admin,
            gender, id_card, qq, wechat, remark, creator_id, is_deleted,
            perm_list, org_category, permission_level,
            created_at, updated_at
        ) VALUES (
            v_system_user_id,
            'system',
            '13800000000',
            'system@emily.local',
            'active',
            true,
            1,
            '',
            '',
            '',
            '系统内置用户，用于数据迁移和自动操作',
            v_system_user_id,  -- 自引用
            false,
            '["*"]',
            4,  -- 管理组
            6,  -- 系统管理员
            NOW()::TEXT,
            NOW()::TEXT
        );
        
        RAISE NOTICE '已创建系统用户 (ID: %)', v_system_user_id;
    ELSE
        RAISE NOTICE '系统用户已存在，无需创建';
    END IF;
END $$;

-- ============================================================
-- 2. 填充 created_at 空值（使用当前时间或历史时间）
-- ============================================================

-- 对于 created_at 为空的记录，使用当前时间作为创建时间
UPDATE users
SET created_at = NOW()::TEXT
WHERE created_at IS NULL 
   OR created_at = '' 
   OR created_at = 'None';

-- 统计更新数量
DO $$
DECLARE
    v_updated_count INTEGER;
BEGIN
    GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    IF v_updated_count > 0 THEN
        RAISE NOTICE '已填充 % 条记录的 created_at 字段', v_updated_count;
    ELSE
        RAISE NOTICE '没有需要填充 created_at 的记录';
    END IF;
END $$;

-- ============================================================
-- 3. 填充 updated_at 空值（与 created_at 保持一致）
-- ============================================================

-- 对于 updated_at 为空的记录，使用 created_at 的值
UPDATE users
SET updated_at = created_at
WHERE updated_at IS NULL 
   OR updated_at = '' 
   OR updated_at = 'None';

-- 统计更新数量
DO $$
DECLARE
    v_updated_count INTEGER;
BEGIN
    GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    IF v_updated_count > 0 THEN
        RAISE NOTICE '已填充 % 条记录的 updated_at 字段', v_updated_count;
    ELSE
        RAISE NOTICE '没有需要填充 updated_at 的记录';
    END IF;
END $$;

-- ============================================================
-- 4. 填充 creator_id 空值
-- ============================================================

-- 对于 creator_id 为空的记录：
-- - 如果是系统用户，自引用
-- - 其他用户使用系统用户 ID 作为创建者
UPDATE users
SET creator_id = (
    CASE 
        WHEN username = 'system' THEN id
        ELSE (SELECT id FROM users WHERE username = 'system' LIMIT 1)
    END
)
WHERE creator_id IS NULL 
   OR creator_id = '' 
   OR creator_id = 'None';

-- 统计更新数量
DO $$
DECLARE
    v_updated_count INTEGER;
BEGIN
    GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    IF v_updated_count > 0 THEN
        RAISE NOTICE '已填充 % 条记录的 creator_id 字段', v_updated_count;
    ELSE
        RAISE NOTICE '没有需要填充 creator_id 的记录';
    END IF;
END $$;

-- ============================================================
-- 5. 填充 username 空值（逻辑自洽的用户名生成策略）
-- ============================================================

-- 策略优先级：
-- 1. 如果有 phone，使用 'user_' + 手机号后4位
-- 2. 如果有 email，使用邮箱前缀
-- 3. 否则使用 'user_' + UUID 前8位
-- 4. 处理重复用户名的情况（添加序号后缀）

DO $$
DECLARE
    v_user_record RECORD;
    v_base_username TEXT;
    v_final_username TEXT;
    v_counter INTEGER;
    v_exists INTEGER;
BEGIN
    FOR v_user_record IN 
        SELECT id, phone, email 
        FROM users 
        WHERE username IS NULL OR username = '' OR username = 'None'
        ORDER BY created_at
    LOOP
        -- 生成基础用户名
        IF v_user_record.phone IS NOT NULL AND v_user_record.phone != '' AND v_user_record.phone != 'None' THEN
            -- 使用手机号后4位
            v_base_username := 'user_' || RIGHT(REGEXP_REPLACE(v_user_record.phone, '[^0-9]', '', 'g'), 4);
        ELSIF v_user_record.email IS NOT NULL AND v_user_record.email != '' AND v_user_record.email != 'None' THEN
            -- 使用邮箱前缀（@之前的部分）
            v_base_username := SPLIT_PART(v_user_record.email, '@', 1);
            -- 清理特殊字符
            v_base_username := REGEXP_REPLACE(v_base_username, '[^a-zA-Z0-9_]', '_', 'g');
            -- 确保长度合适
            IF LENGTH(v_base_username) > 20 THEN
                v_base_username := SUBSTRING(v_base_username, 1, 20);
            END IF;
        ELSE
            -- 使用 UUID 前8位
            v_base_username := 'user_' || SUBSTRING(v_user_record.id, 1, 8);
        END IF;
        
        -- 处理重名
        v_counter := 0;
        v_final_username := v_base_username;
        
        LOOP
            SELECT COUNT(*) INTO v_exists 
            FROM users 
            WHERE username = v_final_username AND id != v_user_record.id;
            
            IF v_exists = 0 THEN
                EXIT;
            END IF;
            
            v_counter := v_counter + 1;
            v_final_username := v_base_username || '_' || v_counter;
        END LOOP;
        
        -- 更新用户名
        UPDATE users 
        SET username = v_final_username 
        WHERE id = v_user_record.id;
        
        RAISE NOTICE '已为用户 % 设置用户名: %', v_user_record.id, v_final_username;
    END LOOP;
END $$;

-- ============================================================
-- 6. 验证填充结果
-- ============================================================

-- 检查是否还有空值
DO $$
DECLARE
    v_null_username INTEGER;
    v_null_creator_id INTEGER;
    v_null_created_at INTEGER;
    v_null_updated_at INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_null_username 
    FROM users 
    WHERE username IS NULL OR username = '' OR username = 'None';
    
    SELECT COUNT(*) INTO v_null_creator_id 
    FROM users 
    WHERE creator_id IS NULL OR creator_id = '' OR creator_id = 'None';
    
    SELECT COUNT(*) INTO v_null_created_at 
    FROM users 
    WHERE created_at IS NULL OR created_at = '' OR created_at = 'None';
    
    SELECT COUNT(*) INTO v_null_updated_at 
    FROM users 
    WHERE updated_at IS NULL OR updated_at = '' OR updated_at = 'None';
    
    IF v_null_username = 0 AND v_null_creator_id = 0 AND 
       v_null_created_at = 0 AND v_null_updated_at = 0 THEN
        RAISE NOTICE '✅ 所有必填字段填充完成！';
    ELSE
        RAISE NOTICE '⚠️  仍存在空值记录：';
        RAISE NOTICE '   - username: % 条', v_null_username;
        RAISE NOTICE '   - creator_id: % 条', v_null_creator_id;
        RAISE NOTICE '   - created_at: % 条', v_null_created_at;
        RAISE NOTICE '   - updated_at: % 条', v_null_updated_at;
    END IF;
END $$;

-- ============================================================
-- 7. 展示填充后的数据摘要
-- ============================================================

SELECT 
    id AS "用户ID",
    username AS "用户名",
    creator_id AS "创建者ID",
    created_at AS "创建时间",
    updated_at AS "更新时间",
    phone AS "手机号",
    email AS "邮箱"
FROM users
ORDER BY created_at DESC
LIMIT 20;

-- 统计总用户数
SELECT COUNT(*) AS "总用户数" FROM users;
