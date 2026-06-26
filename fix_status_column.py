import sys
sys.path.insert(0, 'emily-core')
from emily_core.infrastructure.database.session import init_db, get_session
from sqlalchemy import text

init_db(db_url='postgresql://emily:emily_secret_2026@localhost:25432/emily')

with get_session() as session:
    # Just fix plan_task_instances.status
    session.execute(text("ALTER TABLE plan_task_instances ALTER COLUMN status TYPE VARCHAR(30)"))
    session.commit()
    
    r = session.execute(text("""
        SELECT column_name, character_maximum_length 
        FROM information_schema.columns 
        WHERE table_name='plan_task_instances' AND column_name='status'
    """))
    for row in r:
        print(f"plan_task_instances.{row[0]}: VARCHAR({row[1]})")
    
    print("Done!")
