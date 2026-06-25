import psycopg2

pg = {"host":"localhost","port":25432,"dbname":"emily","user":"emily","password":"emily_secret_2026"}
conn = psycopg2.connect(**pg)
cur = conn.cursor()

print("=== USERS columns ===")
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='users' ORDER BY ordinal_position")
for r in cur.fetchall():
    print(r)

print("\n=== USERS data ===")
cur.execute("SELECT * FROM users LIMIT 20")
cols = [desc[0] for desc in cur.description]
print(cols)
for r in cur.fetchall():
    print(r)

print("\n=== PROJECTS ===")
cur.execute("SELECT * FROM projects WHERE is_deleted=false LIMIT 10")
cols = [desc[0] for desc in cur.description]
print(cols)
for r in cur.fetchall():
    print(r)

print("\n=== PLAN TASK TEMPLATES ===")
try:
    cur.execute("SELECT * FROM plan_task_templates LIMIT 10")
    cols = [desc[0] for desc in cur.description]
    print(cols)
    for r in cur.fetchall():
        print(r)
except Exception as e:
    print(f"Table error: {e}")

print("\n=== PLAN TASK INSTANCES ===")
try:
    cur.execute("SELECT * FROM plan_task_instances LIMIT 20")
    cols = [desc[0] for desc in cur.description]
    print(cols)
    for r in cur.fetchall():
        print(r)
except Exception as e:
    print(f"Table error: {e}")

print("\n=== PLAN TASK LOGS ===")
try:
    cur.execute("SELECT * FROM plan_task_logs LIMIT 10")
    cols = [desc[0] for desc in cur.description]
    print(cols)
    for r in cur.fetchall():
        print(r)
except Exception as e:
    print(f"Table error: {e}")

print("\n=== PLAN TASK DELIVERABLES ===")
try:
    cur.execute("SELECT * FROM plan_task_deliverables LIMIT 10")
    cols = [desc[0] for desc in cur.description]
    print(cols)
    for r in cur.fetchall():
        print(r)
except Exception as e:
    print(f"Table error: {e}")

print("\n=== ALL TABLES ===")
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
for r in cur.fetchall():
    print(r[0])

conn.close()
