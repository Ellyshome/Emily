import psycopg2
pg = {"host":"localhost","port":25432,"dbname":"emily","user":"emily","password":"emily_secret_2026"}
conn = psycopg2.connect(**pg)
cur = conn.cursor()

cur.execute("SELECT instance_no, title, status, deadline_at, executor_id, initiator_id, project_id, created_at FROM plan_task_instances ORDER BY created_at DESC LIMIT 5")
cols = [desc[0] for desc in cur.description]
print(cols)
for r in cur.fetchall():
    print(r)

cur.execute("SELECT template_no, name, status, task_type, deadline_rule FROM plan_task_templates ORDER BY created_at DESC LIMIT 5")
cols = [desc[0] for desc in cur.description]
print("\n" + str(cols))
for r in cur.fetchall():
    print(r)

conn.close()
