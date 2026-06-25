"""为计划任务系统测试准备数据库预设数据"""
import psycopg2

pg = {"host":"localhost","port":25432,"dbname":"emily","user":"emily","password":"emily_secret_2026"}
conn = psycopg2.connect(**pg)
cur = conn.cursor()

print("=== Step 1: Add missing columns to users table ===")
try:
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS permission_level INTEGER DEFAULT 0")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS supervisor_id VARCHAR")
    conn.commit()
    print("  Added permission_level and supervisor_id columns")
except Exception as e:
    conn.rollback()
    print(f"  Error: {e}")

print("\n=== Step 2: Clean up duplicate users (keep one per username) ===")
# Keep the first user for each username, delete the rest
cur.execute("""
    DELETE FROM users 
    WHERE id NOT IN (
        SELECT MIN(id) FROM users WHERE is_deleted=false GROUP BY username
    ) AND is_deleted=false
""")
print(f"  Deleted {cur.rowcount} duplicate users")
conn.commit()

print("\n=== Step 3: Set up test users with permission levels and supervisors ===")
# Get user IDs
cur.execute("SELECT id, username, real_name FROM users WHERE is_deleted=false ORDER BY username")
users = {r[1]: {"id": r[0], "name": r[2]} for r in cur.fetchall()}
print(f"  Remaining users: {list(users.keys())}")

# Get supervisor (王工) ID
supervisor_id = users.get("王工", {}).get("id", "")
if not supervisor_id:
    print("  WARNING: No supervisor (王工) found!")

# Set up users with different permission levels
# Schema: username -> (permission_level, supervisor_id, role_desc)
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
    if uname in users:
        uid = users[uname]["id"]
        if supid:
            cur.execute(
                "UPDATE users SET permission_level=%s, supervisor_id=%s WHERE id=%s",
                (plevel, supid, uid)
            )
        else:
            cur.execute(
                "UPDATE users SET permission_level=%s WHERE id=%s",
                (plevel, uid)
            )
        print(f"  {uname} ({role}): permission_level={plevel}, supervisor={'王工' if supid else 'None'}")

conn.commit()

print("\n=== Step 4: Create test project ===")
# Create project if not exists
cur.execute("SELECT id FROM projects WHERE code='S4-PAVE' AND is_deleted=false")
proj = cur.fetchone()
if not proj:
    cur.execute("""
        INSERT INTO projects (id, code, name, description, status, creator_id, is_deleted)
        VALUES ('proj-s4-pave-001', 'S4-PAVE', 'S4地块铺装工程', 'S4地块景观铺装施工项目', 'active', %s, false)
    """, (users.get("王工", {}).get("id", ""),))
    conn.commit()
    print("  Created project: S4地块铺装工程 (S4-PAVE)")
else:
    print(f"  Project already exists: {proj[0]}")

print("\n=== Step 5: Verify setup ===")
cur.execute("""
    SELECT u.username, u.real_name, u.permission_level, 
           s.username as supervisor_name
    FROM users u 
    LEFT JOIN users s ON u.supervisor_id = s.id
    WHERE u.is_deleted=false
    ORDER BY u.permission_level DESC, u.username
""")
print(f"  {'Username':<12} {'Name':<10} {'Level':<8} {'Supervisor'}")
print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*12}")
for r in cur.fetchall():
    print(f"  {r[0]:<12} {r[1]:<10} {r[2] if r[2] is not None else 'NULL':<8} {r[3] or 'None'}")

cur.execute("SELECT id, code, name FROM projects WHERE is_deleted=false")
print(f"\n  Projects:")
for r in cur.fetchall():
    print(f"    {r[0]} | {r[1]} | {r[2]}")

conn.commit()
conn.close()
print("\n=== Setup complete ===")
