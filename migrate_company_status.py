import psycopg2

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

# ========================================
# Step 0: 查看当前状态
# ========================================
print("=== 0. 查看当前 company_info 表结构 ===")
cur.execute(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns "
    "WHERE table_name = 'company_info' AND column_name = 'status'"
)
result = cur.fetchone()
if result:
    print(f"  字段: {result[0]}")
    print(f"  类型: {result[1]}")
    print(f"  nullable: {result[2]}")
    print(f"  默认值: {result[3]}")
else:
    print("  status 字段不存在！")

print("\n=== 当前 status 数据分布 ===")
cur.execute("SELECT status, COUNT(*) FROM company_info GROUP BY status ORDER BY status;")
for r in cur.fetchall():
    status_str = str(r[0]) if r[0] is not None else "NULL"
    print(f"  {status_str:12}: {r[1]} 条")

# ========================================
# Step 1: 创建枚举类型
# ========================================
print("\n=== 1. 创建枚举类型 company_status_enum ===")
cur.execute("""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'company_status_enum') THEN
        CREATE TYPE company_status_enum AS ENUM (
            '未签约',
            '履约中',
            '已解约',
            '完工待结算',
            '履约完成'
        );
    END IF;
END
$$;
""")
conn.commit()
print("  枚举类型创建成功！")

# ========================================
# Step 2: 删除现有默认值（如果有）
# ========================================
print("\n=== 2. 删除现有默认值 ===")
cur.execute("""
ALTER TABLE company_info ALTER COLUMN status DROP DEFAULT;
""")
conn.commit()
print("  成功删除默认值")

# ========================================
# Step 3: 转换现有数据为枚举值（active -> 履约中）
# ========================================
print("\n=== 3. 转换现有数据为枚举值 ===")
# 先把 'active' 转为 '履约中'（字符串）
cur.execute("""
UPDATE company_info SET status = '履约中' WHERE status = 'active';
""")
print(f"  转换了 {cur.rowcount} 条 'active' -> '履约中'")
conn.commit()

# ========================================
# Step 4: 修改字段类型
# ========================================
print("\n=== 4. 修改 status 字段类型为枚举 ===")
# 使用 USING 子句进行类型转换
cur.execute("""
ALTER TABLE company_info 
ALTER COLUMN status TYPE company_status_enum 
USING status::text::company_status_enum;
""")
conn.commit()
print("  字段类型修改成功！")

# ========================================
# Step 5: 处理 NULL 值（设置默认值为 '未签约'）
# ========================================
print("\n=== 5. 设置 NULL 值默认值 ===")
cur.execute("""
UPDATE company_info SET status = '未签约' WHERE status IS NULL;
""")
conn.commit()
print(f"  处理了 {cur.rowcount} 条 NULL 记录")

# ========================================
# Step 6: 设置字段默认值
# ========================================
print("\n=== 6. 设置字段默认值 ===")
cur.execute("""
ALTER TABLE company_info ALTER COLUMN status SET DEFAULT '未签约'::company_status_enum;
""")
conn.commit()
print("  默认值设置成功！")

# ========================================
# 验证结果
# ========================================
print("\n=== 验证结果 ===")
cur.execute(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns "
    "WHERE table_name = 'company_info' AND column_name = 'status'"
)
r = cur.fetchone()
print(f"  字段: {r[0]}")
print(f"  类型: {r[1]}")
print(f"  nullable: {r[2]}")
print(f"  默认值: {r[3]}")

print("\n=== 枚举值分布 ===")
cur.execute("SELECT status, COUNT(*) FROM company_info GROUP BY status ORDER BY status;")
for r in cur.fetchall():
    status_str = str(r[0]) if r[0] is not None else "NULL"
    print(f"  {status_str:12}: {r[1]} 条")

conn.close()
print("\nDone!")
