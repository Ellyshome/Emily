import psycopg2

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

# 查看 company_info 所有字段
print("=== company_info 表字段（含 is_admin） ===")
cur.execute(
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns "
    "WHERE table_name = 'company_info' "
    "ORDER BY ordinal_position"
)
for col in cur.fetchall():
    default = " 默认: {}".format(col[3]) if col[3] else ""
    marker = " <-- IS_ADMIN" if col[0] == 'is_admin' else ""
    print("  {0:25} {1:20} nullable={2}{3}{4}".format(col[0], col[1], col[2], default, marker))

# 验证数据
print()
print("=== 验证数据 ===")
cur.execute("SELECT company_name, is_admin FROM company_info ORDER BY is_admin DESC;")
for row in cur.fetchall():
    admin_flag = " (主单位)" if row[1] else " (普通)"
    print("  {0:15} is_admin={1}{2}".format(row[0], row[1], admin_flag))

conn.close()
print()
print("Done!")
