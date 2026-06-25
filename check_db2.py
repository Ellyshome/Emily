import psycopg2

pg = {"host":"localhost","port":25432,"dbname":"emily","user":"emily","password":"emily_secret_2026"}
conn = psycopg2.connect(**pg)
cur = conn.cursor()

# Only check users columns and tables
print("=== USERS columns ===")
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='users' ORDER BY ordinal_position")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

print("\n=== ALL TABLES ===")
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
for r in cur.fetchall():
    print(f"  {r[0]}")

print("\n=== PROJECTS count ===")
cur.execute("SELECT count(*) FROM projects WHERE is_deleted=false")
print(f"  {cur.fetchone()[0]}")

print("\n=== USERS sample (first 5, key fields) ===")
cur.execute("SELECT id, username, real_name, status, created_at FROM users LIMIT 5")
cols = [desc[0] for desc in cur.description]
print(f"  Columns: {cols}")
for r in cur.fetchall():
    print(f"  {r}")

conn.close()
