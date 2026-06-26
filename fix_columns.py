import sys
sys.path.insert(0, 'emily-core')
from emily_core.infrastructure.database.session import init_db, get_session
from sqlalchemy import text

init_db(db_url='postgresql://emily:emily_secret_2026@localhost:25432/emily')

with get_session() as session:
    # Check current column types
    result = session.execute(text("""
        SELECT column_name, data_type, character_maximum_length 
        FROM information_schema.columns 
        WHERE table_name = 'plan_task_instances' 
        AND column_name IN ('anomaly_reason', 'description', 'escalation_reason', 'verification_standard', 'title')
        ORDER BY column_name
    """))
    for row in result:
        print(f"{row[0]}: {row[1]}({row[2]})")
    
    # Now fix all columns
    session.execute(text("ALTER TABLE plan_task_instances ALTER COLUMN anomaly_reason TYPE VARCHAR(500)"))
    session.execute(text("ALTER TABLE plan_task_instances ALTER COLUMN description TYPE VARCHAR(2000)"))
    session.execute(text("ALTER TABLE plan_task_instances ALTER COLUMN escalation_reason TYPE VARCHAR(500)"))
    session.execute(text("ALTER TABLE plan_task_instances ALTER COLUMN verification_standard TYPE VARCHAR(500)"))
    session.commit()
    
    # Verify again
    result2 = session.execute(text("""
        SELECT column_name, data_type, character_maximum_length 
        FROM information_schema.columns 
        WHERE table_name = 'plan_task_instances' 
        AND column_name IN ('anomaly_reason', 'description', 'escalation_reason', 'verification_standard')
        ORDER BY column_name
    """))
    print("\nAfter fix:")
    for row in result2:
        print(f"{row[0]}: {row[1]}({row[2]})")
