-- ============================================================
-- 014_seed_rag_visitor.sql —— RAG 验收访客用户（U3）
--
-- 为 RAG 可见范围验收（TC-03/TC-10「访客落点」）新增一名
-- 「无公司 + L1」的外部访客：company=NULL, level=1, org_category=1(访客组)。
-- 不参与任何节点、不绑定 IM 会话，仅用于验证 ①∪②（③ 为空）的可见集语义。
--
-- Precondition: 002_seed_test_data.sql (王建国等用户) 必须已执行
-- Usage: docker exec -i emily-postgres psql -U emily -d emily < 014_seed_rag_visitor.sql
-- ============================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 幂等清理
-- ============================================================
DELETE FROM user_im_bindings WHERE im_user_id IN ('sim_周访客');
DELETE FROM users WHERE username = '周访客';

-- ============================================================
-- 2. 访客用户
--    users 表字段：id, username, phone, email, status, is_admin, gender,
--    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
--    org_category, level, supervisor_id, company, project_id, position,
--    created_at, updated_at
-- ============================================================
INSERT INTO users (id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted, perm_list,
    org_category, level, supervisor_id, company, project_id, position,
    created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    '周访客',
    NULL, NULL,
    'active', false, 0, '', '123456015', 'wx_周访客',
    '外部访客（无公司、L1），用于 RAG 可见范围验收「访客落点」用例',
    '王建国', false, '["public.read"]',
    1, 1,
    NULL,          -- supervisor_id（无直属上级）
    NULL,          -- company（关键：访客无公司，③ 节点集为空）
    NULL,          -- project_id
    '["访客"]', NOW()::text, NOW()::text
FROM users
WHERE username = '王建国' AND is_deleted = false
LIMIT 1;

-- ============================================================
-- 3. IM 绑定（simulator，仅保持一致；不会被 emy-test 使用）
-- ============================================================
INSERT INTO user_im_bindings (id, user_id, im_platform, im_user_id,
    im_display_name, status, created_at, updated_at)
SELECT
    uuid_generate_v4()::text,
    u.id,
    'simulator',
    'sim_' || u.username,
    '周访客',
    'active',
    NOW()::text,
    NOW()::text
FROM users u
WHERE u.username = '周访客' AND u.is_deleted = false;

-- ============================================================
-- 4. 验证
-- ============================================================
SELECT '--- 访客用户校验 ---' AS section;
SELECT u.username, u.level, u.company AS company_id,
       CASE WHEN u.company IS NULL THEN '无公司(正确)' ELSE '有公司(异常)' END AS company_check,
       c.company_name
FROM users u
LEFT JOIN company_info c ON u.company = c.id
WHERE u.username = '周访客' AND u.is_deleted = false;

COMMIT;
