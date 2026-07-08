import psycopg2

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

# ========================================
# Step 1: 添加字段
# ========================================
print("=== 1. 添加 is_admin 字段 ===")
cur.execute("""
ALTER TABLE company_info 
ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
""")
conn.commit()
print("  字段添加成功！")

# ========================================
# Step 2: 设置现有记录的默认值
# ========================================
print("\n=== 2. 设置现有记录的默认值 ===")
cur.execute("""
UPDATE company_info SET is_admin = FALSE WHERE is_admin IS NULL;
""")
print(f"  更新了 {cur.rowcount} 条记录")
conn.commit()

# ========================================
# 验证结果
# ========================================
print("\n=== 验证结果 ===")
cur.execute(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns "
    "WHERE table_name = 'company_info' AND column_name = 'is_admin'"
)
r = cur.fetchone()
print(f"  字段: {r[0]}")
print(f"  类型: {r[1]}")
print(f"  nullable: {r[2]}")
print(f"  默认值: {r[3]}")

print("\n=== is_admin 值分布 ===")
cur.execute("SELECT is_admin, COUNT(*) FROM company_info GROUP BY is_admin ORDER BY is_admin;")
for r in cur.fetchall():
    print(f"  is_admin={r[0]}: {r[1]} 条")

conn.close()
print("\nDone!")
