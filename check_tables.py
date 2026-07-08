import psycopg2

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

# 列出所有表
cur.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' ORDER BY table_name"
)
print("=== 数据库中的表 ===")
for table in cur.fetchall():
    print(f"  {table[0]}")

# 找含 company 的表
cur.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_name LIKE '%company%'"
)
print("\n=== 含 company 的表 ===")
for table in cur.fetchall():
    print(f"  {table[0]}")

conn.close()
print("\nDone!")
