"""ingest_knowledge_library.py — 资料库（数字资产管理）入库脚本。

把指定目录纳入资料库管理：一次性遍历该目录树，为**每一层目录**生成/更新索引文件
（默认 `.emily.md`，见 emily_core/services/knowledge_library/library_fs.py 的
INDEX_FILENAME 常量），索引格式与口径由入库手册（emily-data/prompts/
knowledge_ingest_playbook.md）规定。

设计原则（C9）：脚本通道与对话工具通道**共享同一服务层**
（KnowledgeLibraryIngestService），两者互不调用；脚本只做参数解析与留痕上下文注入。

用法：
    # 预览（不写任何文件，仅输出将生成/更新的索引清单）
    uv run python scripts/ingest_knowledge_library.py --dir H_Project --dry-run

    # 实际入库（留痕归属到操作人）
    uv run python scripts/ingest_knowledge_library.py --dir H_Project/黄湖 --operator-id <用户 UUID>

    # 指定资料库根（缺省按多级回退解析：配置 → 环境变量 → /app/knowledge_library → 开发路径）
    uv run python scripts/ingest_knowledge_library.py --root /app/knowledge_library --operator-id <UUID>

说明：入库只**写索引**，不改动、不移动、不删除任何原始资料；无周期机制，一律人工触发。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# emily_core 包父目录：宿主机 = <repo>/emily-core；容器内 = /app（Dockerfile 复制 emily_core/ 到 /app/emily_core）。
# 两级探测使脚本在宿主 `uv run` 与容器 `docker exec -w /app` 两种通道下都能直接执行
# （经 ScriptManager 执行时已由它注入 PYTHONPATH，此处为直跑补齐）。
for _cand in (_HERE.parent / "emily-core", _HERE.parent):
    if (_cand / "emily_core" / "__init__.py").is_file():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest_knowledge_library")


def _bind_audit(operator_id: str = "") -> None:
    """留痕上下文注入（入口职责）：脚本入口只注入，不写留痕（留痕在 service 动作上）。

    经 ScriptManager 执行时环境变量已注入 source=ops；直接 CLI 执行时由
    declare_script_source 补上。operator_id 给出时作为动作的 actor 归属。
    """
    try:
        from emily_core.infrastructure.logging.audit import (
            SOURCE_OPS,
            bind_audit_context,
            declare_script_source,
        )

        declare_script_source("ingest_knowledge_library")
        if operator_id:
            bind_audit_context(source=SOURCE_OPS, actor_id=operator_id, override=True)
    except Exception as ex:  # noqa: BLE001 — 留痕上下文失败不阻断入库
        logger.debug("bind audit context failed: %s", ex)


def ingest(dir: str = "", root: str | None = None,
           operator_id: str = "", dry_run: bool = False) -> dict:
    """核心函数（CLI 与系统调用共用）：委派入库引擎（M2）。

    Args:
        dir: 目标目录（资料库内相对路径或绝对路径；留空 = 整个资料库）。
        root: 资料库根目录（留空 = 多级回退解析）。
        operator_id: 操作人 user_id（留痕归属，可空）。
        dry_run: True = 只出清单不写盘。

    Returns:
        {success, ok, dry_run, target, created[], updated[], skipped[], stats{}}
    """
    from emily_core.services.knowledge_library import KnowledgeLibraryFs
    from emily_core.services.knowledge_library.ingest_service import (
        KnowledgeLibraryIngestService,
    )

    fs = KnowledgeLibraryFs(root or None)
    result = KnowledgeLibraryIngestService(fs).ingest(dir, dry_run=dry_run, operator_id=operator_id)
    result["root"] = str(fs.root)
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="资料库入库（逐层生成/更新索引文件）")
    parser.add_argument("--dir", default="",
                        help="目标目录：资料库内相对路径（如 H_Project）或绝对路径；"
                             "留空 = 整个资料库")
    parser.add_argument("--root", default="",
                        help="资料库根目录；留空则按 配置 → 环境变量 → /app/knowledge_library → 开发路径 解析")
    parser.add_argument("--operator-id", default="", help="操作人 user_id（留痕归属）")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览：只输出将生成/更新的索引清单，不写任何文件")
    args = parser.parse_args()

    _bind_audit(args.operator_id)
    result = ingest(args.dir, args.root or None, args.operator_id, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
