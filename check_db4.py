import psycopg2

pg = {"host":"localhost","port":25432,"dbname":"emily","user":"emily","password":"emily_secret_2026"}
conn = psycopg2.connect(**pg)
cur = conn.cursor()

print("=== PLAN_TASK tables ===")
for tn in ["plan_task_templates", "plan_task_instances", "plan_task_logs", "plan_task_deliverables"]:
    cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)", (tn,))
    print(f"  {tn}: {'YES' if cur.fetchone()[0] else 'NO'}")

print("\n=== USERS columns (checking for permission_level, supervisor_id) ===")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name IN ('supervisor_id', 'permission_level')")
found = [r[0] for r in cur.fetchall()]
print(f"  Missing columns: {found}")

print("\n=== USERS (distinct usernames) ===")
cur.execute("SELECT username, real_name, count(*) FROM users WHERE is_deleted=false GROUP BY username, real_name ORDER BY username")
for r in cur.fetchall():
    print(f"  {r[0]} ({r[1]}): {r[2]}")

print("\n=== PROJECTS ===")
cur.execute("SELECT id, code, name FROM projects WHERE is_deleted=false")
projs = cur.fetchall()
print(f"  Count: {len(projs)}")
for p in projs:
    print(f"  {p}")

print("\n=== plan_task_instances columns ===")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='plan_task_instances' ORDER BY ordinal_position")
for r in cur.fetchall():
    print(f"  {r[0]}")

conn.close()
