"""临时测试：验证 sop_business_flows 查询"""
import sys
sys.path.insert(0, "emily-core")
from emily_core.infrastructure.database.session import get_session
from emily_core.infrastructure.database.models import SOPBusinessFlow

with get_session() as s:
    flows = s.query(SOPBusinessFlow).filter(
        SOPBusinessFlow.is_active == True,
        SOPBusinessFlow.is_deleted == False,
    ).all()
    print(f"flows={len(flows)}")
    for f in flows[:3]:
        print(f"  {f.sop_id} is_active={f.is_active} is_deleted={f.is_deleted}")
    
    # Also try without filters
    all_flows = s.query(SOPBusinessFlow).all()
    print(f"all flows (no filter)={len(all_flows)}")
