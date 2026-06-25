import psycopg2

pg = {"host":"localhost","port":25432,"dbname":"emily","user":"emily","password":"emily_secret_2026"}
conn = psycopg2.connect(**pg)
cur = conn.cursor()

print("=== ALL TABLES (count) ===")
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
tables = [r[0] for r in cur.fetchall()]
print(f"Count: {len(tables)}")
for t in tables:
    print(f"  {t}")

print("\n=== PLAN_TASK tables exist? ===")
for tn in ["plan_task_templates", "plan_task_instances", "plan_task_logs", "plan_task_deliverables"]:
    cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)", (tn,))
    exists = cur.fetchone()[0]
    print(f"  {tn}: {'YES' if exists else 'NO'}")

print("\n=== User model columns with supervisor_id/permission_level? ===")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name IN ('supervisor_id', 'permission_level')")
found = [r[0] for r in cur.fetchall()]
print(f"  Found: {found}")

# Also check if meetings table exists
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='meetings')")
print(f"\n  meetings table: {'YES' if cur.fetchone()[0] else 'NO'}")

# Check conversations
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='conversations')")
print(f"  conversations table: {'YES' if cur.fetchone()[0] else 'NO'}")

# Check message_attachments
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='message_attachments')")
print(f"  message_attachments table: {'YES' if cur.fetchone()[0] else 'NO'}")

# Check files
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='files')")
print(f"  files table: {'YES' if cur.fetchone()[0] else 'NO'}")

# Check agent_reasoning_logs
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='agent_reasoning_logs')")
print(f"  agent_reasoning_logs table: {'YES' if cur.fetchone()[0] else 'NO'}")

# Check llm_interaction_logs
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='llm_interaction_logs')")
print(f"  llm_interaction_logs table: {'YES' if cur.fetchone()[0] else 'NO'}")

# Check tool_call_logs
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='tool_call_logs')")
print(f"  tool_call_logs table: {'YES' if cur.fetchone()[0] else 'NO'}")

print("\n=== Total ===")
cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
print(f"  Total tables: {cur.fetchone()[0]}")

conn.close()
