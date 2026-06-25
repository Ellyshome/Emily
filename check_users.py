import sys
sys.path.insert(0, 'emily-core')
from emily_core.infrastructure.database.session import init_db, get_session
from emily_core.repositories.user_repo import UserRepository

init_db(db_url='postgresql://emily:emily_secret_2026@localhost:25432/emily')

ur = UserRepository()
for name in ['张工', '李工', '王工', '赵工']:
    u = ur.find_by_name(name)
    if u:
        uname = getattr(u, 'username', '?') or getattr(u, 'display_name', '?')
        print(f"{uname}: id={u.id[:8]}, level={getattr(u, 'permission_level', 'N/A')}, supervisor={getattr(u, 'supervisor_id', 'N/A')}")
    else:
        print(f"{name}: NOT FOUND")
