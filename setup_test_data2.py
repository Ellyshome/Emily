"""为计划任务系统测试准备数据库预设数据 (v2 - 不删用户)"""
import psycopg2

pg = {"host":"localhost","port":25432,"dbname":"emily","user":"emily","password":"emily_secret_2026"}
conn = psycopg2.connect(**pg)
cur = conn.cursor()

print("=== Step 1: Add missing columns ===")
cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS permission_level INTEGER DEFAULT 0")
cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS supervisor_id VARCHAR")
conn.commit()
print("  Done")

print("\n=== Step 2: Get first user per username ===")
cur.execute("""
    SELECT username, MIN(id) as first_id FROM users 
    WHERE is_deleted=false GROUP BY username ORDER BY username
""")
first_users = {r[0]: r[1] for r in cur.fetchall()}
for uname, uid in first_users.items():
    print(f"  {uname}: {uid}")

# Get supervisor (王工) ID
supervisor_id = first_users.get("王工", "")
print(f"\n  Supervisor (王工): {supervisor_id}")

print("\n=== Step 3: Set permission levels on first users ===")
user_setup = {
    "张工": (1, supervisor_id, "施工员"),
    "李工": (2, supervisor_id, "主管"),
    "王工": (2, None, "项目经理(上级)"),
    "赵工": (1, supervisor_id, "质量员"),
    "陈工": (1, supervisor_id, "安全员"),
    "Alice": (1, supervisor_id, "测试用户A"),
    "Bob": (2, supervisor_id, "测试用户B"),
    "Charlie": (1, supervisor_id, "测试用户C"),
}

for uname, (plevel, supid, role) in user_setup.items():
    uid = first_users.get(uname)
    if not uid:
        print(f"  SKIP {uname}: not found")
        continue
    if supid:
        cur.execute("UPDATE users SET permission_level=%s, supervisor_id=%s WHERE id=%s", (plevel, supid, uid))
    else:
        cur.execute("UPDATE users SET permission_level=%s WHERE id=%s", (plevel, uid))
    print(f"  {uname} ({role}): level={plevel}")

conn.commit()

print("\n=== Step 4: Create test project ===")
cur.execute("SELECT id FROM projects WHERE code='S4-PAVE' AND is_deleted=false")
proj = cur.fetchone()
if not proj:
    cur.execute("""
        INSERT INTO projects (id, code, name, description, status, creator_id, is_deleted)
        VALUES ('proj-s4-pave-001', 'S4-PAVE', 'S4地块铺装工程', 'S4地块景观铺装施工项目', 'active', %s, false)
    """, (first_users.get("王工", ""),))
    conn.commit()
    print("  Created: S4地块铺装工程 (S4-PAVE, id=proj-s4-pave-001)")
else:
    print(f"  Already exists: {proj[0]}")

print("\n=== Step 5: Verify ===")
cur.execute("""
    SELECT u.id, u.username, u.permission_level, s.username as supervisor
    FROM users u LEFT JOIN users s ON u.supervisor_id = s.id
    WHERE u.id IN %s AND u.is_deleted=false
    ORDER BY u.permission_level DESC, u.username
""", (tuple(first_users.values()),))
print(f"  {'ID':<38} {'Name':<12} {'Level':<8} {'Supervisor'}")
for r in cur.fetchall():
    print(f"  {r[0]:<38} {r[1]:<12} {r[2]:<8} {r[3] or 'None'}")

cur.execute("SELECT id, code, name FROM projects WHERE is_deleted=false")
print("\n  Projects:")
for r in cur.fetchall():
    print(f"    {r[0]} | {r[1]} | {r[2]}")

print(f"\n  Total users: ")
cur.execute("SELECT count(*) FROM users WHERE is_deleted=false")
print(f"    {cur.fetchone()[0]}")

conn.commit()
conn.close()
print("\n=== Setup complete ===")
