import psycopg2

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

# 要删除的用户列表
to_delete_keywords = ['张工', '测试', '假用户']
deleted_count = 0

# 列出所有用户
print("=== 删除前用户列表 ===")
cur.execute("SELECT id, username FROM users WHERE is_deleted = FALSE ORDER BY username;")
users_before = cur.fetchall()
for row in users_before:
    print(f"  {row[1]:20} ({row[0]})")

# 执行删除（软删除）
for user_id, username in users_before:
    if username == 'admin':
        continue
    for keyword in to_delete_keywords:
        if keyword in username:
            print(f"\n删除: {username}")
            cur.execute(
                "UPDATE users SET is_deleted = TRUE WHERE id = %s",
                (user_id,)
            )
            deleted_count += 1
            break

conn.commit()

# 验证
print("\n=== 删除后用户列表 ===")
cur.execute("SELECT id, username FROM users WHERE is_deleted = FALSE ORDER BY username;")
for row in cur.fetchall():
    print(f"  {row[1]:20} ({row[0]})")

print(f"\n共删除 {deleted_count} 个用户")

conn.close()
print()
print("Done!")
