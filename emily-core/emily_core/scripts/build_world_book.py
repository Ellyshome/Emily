"""手动触发世界书构建。
用法: uv run python -m emily_core.scripts.build_world_book [project_code]
默认 project_code = EMERALD-01
环境变量 EMILY_DATABASE_URL 或默认 localhost:25432
"""
import os
import sys
import json

sys.path.insert(0, ".")

from sqlalchemy import text

from emily_core.infrastructure.database.session import init_db, get_session
from emily_core.services.world_book_builder import ProjectWorldBookBuilder


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "EMERALD-01"

    db_url = os.environ.get(
        "EMILY_DATABASE_URL",
        "postgresql://emily:emily_secret_2026@localhost:25432/emily",
    )
    init_db(db_url=db_url)

    with get_session() as s:
        row = s.execute(
            text("SELECT id FROM projects WHERE code = :c AND is_deleted = false"),
            {"c": code},
        ).fetchone()
        if not row:
            print(f"[FAIL] 项目 {code} 不存在")
            return
        project_id = row[0]

    print(f"[INFO] 项目 ID: {project_id}")

    builder = ProjectWorldBookBuilder()
    result = builder.build(project_id, generated_by="manual_cli")

    status = result.get("status", "unknown") if isinstance(result, dict) else "error"
    print(f"[INFO] 构建状态: {status}")

    if isinstance(result, dict):
        content_raw = result.get("content_json", "")
        if content_raw:
            if isinstance(content_raw, str):
                content = json.loads(content_raw)
            else:
                content = content_raw
            print(f"[INFO] 层级: {list(content.keys())}")
            for key, val in content.items():
                if isinstance(val, list):
                    print(f"  - {key}: {len(val)} 条")
                elif isinstance(val, dict):
                    print(f"  - {key}: {len(val)} 个键")
                elif isinstance(val, str):
                    print(f"  - {key}: {len(val)} 字符")
        else:
            print("[WARN] content_json 为空")

    print("[DONE] 世界书构建完成")


if __name__ == "__main__":
    main()
