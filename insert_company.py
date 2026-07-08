import psycopg2
import uuid
import datetime

conn = psycopg2.connect(
    "dbname=emily user=emily password=emily_secret_2026 host=localhost port=25432"
)
cur = conn.cursor()

# 先获取一个用户 ID 作为 project_leader_id 和 creator_id
cur.execute("SELECT id, username FROM users WHERE is_deleted = FALSE LIMIT 1;")
user = cur.fetchone()
user_id = user[0]
print(f"使用用户: {user[1]} ({user[0]})")

# 生成 ID
company_id = str(uuid.uuid4())
now = datetime.datetime.now().isoformat()

# 插入数据
print("\n=== 插入数据 ===")
insert_sql = """
INSERT INTO company_info (
    id,
    company_name,
    type,
    is_admin,
    unified_code,
    business_desc,
    project_leader_id,
    creator_id,
    status,
    created_at,
    updated_at,
    is_deleted
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
"""

cur.execute(insert_sql, (
    company_id,
    "蓝城伟业",  # company_name
    "代建单位",  # type
    True,  # is_admin
    "91110000**********",  # unified_code（统一社会信用代码，虚拟）
    "专业从事房地产项目代建管理服务，涵盖住宅、商业、文旅等多业态",  # business_desc
    user_id,  # project_leader_id
    user_id,  # creator_id
    "履约中",  # status
    now,
    now,
    False
))

conn.commit()
print(f"  插入成功！ID: {company_id}")

# 验证
print("\n=== 验证结果 ===")
cur.execute("SELECT id, company_name, type, is_admin, status FROM company_info ORDER BY created_at DESC LIMIT 1;")
row = cur.fetchone()
print(f"  ID: {row[0]}")
print(f"  单位名称: {row[1]}")
print(f"  类型: {row[2]}")
print(f"  is_admin: {row[3]}")
print(f"  status: {row[4]}")

print("\n=== 所有单位列表 ===")
cur.execute("SELECT company_name, type, is_admin, status FROM company_info ORDER BY is_admin DESC;")
for row in cur.fetchall():
    admin_flag = "（主单位）" if row[2] else ""
    print(f"  {row[0]:15} {row[1]:10} {row[3]:10} {admin_flag}")

conn.close()
print("\nDone!")
