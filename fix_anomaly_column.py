import sys
sys.path.insert(0, 'emily-core')
from emily_core.infrastructure.database.session import init_db, get_session

init_db(db_url='postgresql://emily:emily_secret_2026@localhost:25432/emily')

with get_session() as session:
    from sqlalchemy import text
    session.execute(text("ALTER TABLE plan_task_instances ALTER COLUMN anomaly_reason TYPE VARCHAR(500)"))
    session.commit()
    print("Done! anomaly_reason column extended to VARCHAR(500)")
