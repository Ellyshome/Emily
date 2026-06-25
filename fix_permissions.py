import sys
sys.path.insert(0, 'emily-core')
from emily_core.infrastructure.database.session import init_db, get_session

init_db(db_url='postgresql://emily:emily_secret_2026@localhost:25432/emily')

with get_session() as session:
    from emily_core.infrastructure.database.models import User

    # Update ALL users with matching real_name
    wang = session.query(User).filter(User.real_name == '王工').all()
    zhang = session.query(User).filter(User.real_name == '张工').all()
    li = session.query(User).filter(User.real_name == '李工').all()
    zhao = session.query(User).filter(User.real_name == '赵工').all()

    wang_ids = [u.id for u in wang]
    zhang_count = len(zhang)
    for u in zhang:
        u.permission_level = 1
        u.supervisor_id = wang_ids[0] if wang_ids else ''
    for u in li:
        u.permission_level = 2
        u.supervisor_id = ''
    for u in zhao:
        u.permission_level = 1
        u.supervisor_id = wang_ids[0] if wang_ids else ''
    for u in wang:
        u.permission_level = 2
        u.supervisor_id = ''

    session.commit()
    print(f"Updated: zhang({zhang_count}), li({len(li)}), wang({len(wang)}), zhao({len(zhao)})")
    print("Done!")
