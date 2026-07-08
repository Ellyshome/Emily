import psycopg2

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

# 先获取要删除的用户ID列表
cur.execute("SELECT id, username FROM users WHERE is_deleted = TRUE;")
to_delete = cur.fetchall()
to_delete_ids = [row[0] for row in to_delete]

print("=== 要硬删除的用户 ===")
for row in to_delete:
    print(f"  {row[1]:20} ({row[0]})")
print(f"\n共 {len(to_delete)} 个用户")

# 删除所有关联记录
print("\n=== 清理关联数据 ===")

# user_im_bindings
cur.execute("DELETE FROM user_im_bindings WHERE user_id = ANY(%s);", (to_delete_ids,))
print(f"  user_im_bindings: {cur.rowcount} 条")

# events
cur.execute("DELETE FROM events WHERE user_id = ANY(%s);", (to_delete_ids,))
print(f"  events: {cur.rowcount} 条")

conn.commit()

# 再删除用户
print("\n=== 删除用户 ===")
cur.execute("DELETE FROM users WHERE is_deleted = TRUE;")
print(f"删除 {cur.rowcount} 个用户")

conn.commit()

# 验证
print("\n=== 删除后用户列表 ===")
cur.execute("SELECT id, username FROM users ORDER BY username;")
for row in cur.fetchall():
    print(f"  {row[1]:20} ({row[0]})")

conn.close()
print()
print("Done!")
