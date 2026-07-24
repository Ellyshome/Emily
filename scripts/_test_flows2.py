"""容器内运行，直接测试 ORM 查询"""
import sys
import os

# 模拟容器内路径
os.chdir("/app")
sys.path.insert(0, "/app")

# 需要先设置 DB URL 环境变量
print("ENV: PYTHONPATH=", os.environ.get("PYTHONPATH", ""))
print("CWD:", os.getcwd())

from emily_core.infrastructure.database.session import get_session
from emily_core.infrastructure.database.models import SOPBusinessFlow

with get_session() as s:
    # 测试1：不过滤
    all_flows = s.query(SOPBusinessFlow).all()
    print(f"All flows: {len(all_flows)}")
    for f in all_flows[:5]:
        print(f"  {f.sop_id} is_active={f.is_active} is_deleted={f.is_deleted} min_level={f.min_level}")
    
    # 测试2：过滤 is_active
    active = s.query(SOPBusinessFlow).filter(SOPBusinessFlow.is_active == True).all()
    print(f"Active (is_active=True): {len(active)}")
    
    # 测试3：过滤两个条件
    filtered = s.query(SOPBusinessFlow).filter(
        SOPBusinessFlow.is_active == True,
        SOPBusinessFlow.is_deleted == False,
    ).all()
    print(f"Active + not deleted: {len(filtered)}")
    
    # 测试4：打印生成SQL
    from sqlalchemy.dialects import postgresql
    q = s.query(SOPBusinessFlow).filter(SOPBusinessFlow.is_active == True, SOPBusinessFlow.is_deleted == False)
    print("SQL:", q.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
