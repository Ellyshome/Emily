import psycopg2

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

print("=== 用户等级分布 ===")
cur.execute("SELECT level, COUNT(*) FROM users GROUP BY level ORDER BY level;")
for r in cur.fetchall():
    level_str = str(r[0]) if r[0] else "NULL"
    print(f"  {level_str:12}: {r[1]} 人")

print()
print("=== 前5个用户示例 ===")
cur.execute("SELECT id, username, is_admin, level FROM users LIMIT 5")
for r in cur.fetchall():
    username = str(r[1])
    is_admin = str(r[2])
    level = str(r[3])
    print(f"  {username:15} is_admin={is_admin:5} level={level}")

print()
print("=== 验证字段存在 ===")
cur.execute(
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_name = 'users' AND column_name = 'level'"
)
r = cur.fetchone()
print(f"  字段: {r[0]}, 类型: {r[1]}")

conn.close()
print()
print("✅ Done!")
