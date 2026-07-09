"""数据迁移脚本：PlanTask → Node 扩展字段回填。

三步迁移：
  1. 加 responsible_user_id（nullable）→ 回填 → 加约束
  2. 加 submission_status → 智能回填
  3. 将活跃 PlanTask 实例转为 TASK 节点（可选，Phase 3 用）

用法：
  uv run python scripts/migrate_plantask_to_node.py --step backfill
  uv run python scripts/migrate_plantask_to_node.py --step constraints
  uv run python scripts/migrate_plantask_to_node.py --step convert-tasks
"""

import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emily-core"))

import argparse


def backfill_responsible_user_id():
    """Step 1: 回填 responsible_user_id。"""
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        from sqlalchemy import text

        # 大部分情况：从 creator_id 回填
        result = session.execute(text("""
            UPDATE project_nodes
            SET responsible_user_id = creator_id
            WHERE responsible_user_id IS NULL OR responsible_user_id = ''
        """))
        print(f"  从 creator_id 回填: {result.rowcount} 行")

        # 兜底：creator_id 无效时设为系统管理员
        ADMIN_UUID = os.environ.get("EMILY_ADMIN_UUID", "")
        if ADMIN_UUID:
            result2 = session.execute(text("""
                UPDATE project_nodes
                SET responsible_user_id = :admin_id
                WHERE responsible_user_id IS NULL
                   OR responsible_user_id = ''
                   OR responsible_user_id NOT IN (SELECT id FROM users)
            """), {"admin_id": ADMIN_UUID})
            print(f"  兜底设为管理员: {result2.rowcount} 行")

        session.commit()
    print("Step 1 完成: responsible_user_id 回填")


def add_constraints():
    """Step 1b: 加 NOT NULL + FK 约束。"""
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        from sqlalchemy import text

        # NOT NULL
        session.execute(text("""
            ALTER TABLE project_nodes
            ALTER COLUMN responsible_user_id SET NOT NULL
        """))
        print("  NOT NULL 约束已添加")

        # FK（如果不存在）
        try:
            session.execute(text("""
                ALTER TABLE project_nodes
                ADD CONSTRAINT fk_nodes_responsible_user
                FOREIGN KEY (responsible_user_id) REFERENCES users(id)
            """))
            print("  FK 约束已添加")
        except Exception as e:
            if "already exists" in str(e).lower() or "重复" in str(e):
                print("  FK 约束已存在，跳过")
            else:
                raise

        session.commit()
    print("Step 1b 完成: 约束添加")


def backfill_submission_status():
    """Step 2: 智能回填 submission_status。"""
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        from sqlalchemy import text

        # COMPLETED 节点 → 成果 CONFIRMED
        result1 = session.execute(text("""
            UPDATE node_deliverables d
            SET submission_status = 'CONFIRMED',
                confirmed_at = (SELECT completed_at FROM project_nodes n WHERE n.node_id = d.node_id)
            WHERE d.submission_status = 'PENDING'
              AND EXISTS (SELECT 1 FROM project_nodes n WHERE n.node_id = d.node_id AND n.status = 'COMPLETED')
        """))
        print(f"  COMPLETED 节点 → CONFIRMED: {result1.rowcount} 行")

        # current_amount >= target_amount → CONFIRMED
        result2 = session.execute(text("""
            UPDATE node_deliverables
            SET submission_status = 'CONFIRMED'
            WHERE submission_status = 'PENDING'
              AND CAST(current_amount AS DECIMAL) >= CAST(target_amount AS DECIMAL)
        """))
        print(f"  进度达标 → CONFIRMED: {result2.rowcount} 行")

        # current_amount > 0 且 < target → SUBMITTED
        result3 = session.execute(text("""
            UPDATE node_deliverables
            SET submission_status = 'SUBMITTED'
            WHERE submission_status = 'PENDING'
              AND CAST(current_amount AS DECIMAL) > 0
              AND CAST(current_amount AS DECIMAL) < CAST(target_amount AS DECIMAL)
        """))
        print(f"  部分进度 → SUBMITTED: {result3.rowcount} 行")

        session.commit()
    print("Step 2 完成: submission_status 回填")


def main():
    parser = argparse.ArgumentParser(description="PlanTask → Node 迁移脚本")
    parser.add_argument("--step", required=True,
                        choices=["backfill", "constraints", "submission", "convert-tasks"],
                        help="执行步骤")
    args = parser.parse_args()

    print(f"=== 迁移步骤: {args.step} ===")

    if args.step == "backfill":
        backfill_responsible_user_id()
    elif args.step == "constraints":
        add_constraints()
    elif args.step == "submission":
        backfill_submission_status()
    elif args.step == "convert-tasks":
        print("convert-tasks 步骤暂未实现（Phase 3 使用）")


if __name__ == "__main__":
    main()
