import sqlite3

conn = sqlite3.connect(r"d:\app\Emily\data\knowledge_base\kb.db")
cur = conn.cursor()

# 列出所有表
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)

for t in tables:
    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
    count = cur.fetchone()[0]
    print(f"\n--- {t} ({count} rows) ---")
    cur.execute(f'PRAGMA table_info("{t}")')
    cols = [(r[1], r[2]) for r in cur.fetchall()]
    print("  Columns:", cols)
    
    cur.execute(f'SELECT * FROM "{t}" LIMIT 5')
    rows = cur.fetchall()
    for row in rows:
        print(" ", row)

conn.close()
