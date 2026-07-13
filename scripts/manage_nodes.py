"""manage_nodes.py — 全景节点图管理 CLI 脚本（创建 + 更新 + 查询）。

═══════════════════════════════════════════════════════════════════════════════
用途：
  全景节点图的运维管理工具，支持：
    - create:      从 YAML 文件批量创建节点树
    - update:      从 YAML 文件批量更新节点字段
    - activate:    批量激活（审批通过）节点
    - discard:     批量废弃节点
    - progress:    批量更新成果进度
    - link-files:  批量管理节点文件关联（共享文件/条件文件/成果文件）
    - query:       查询项目节点概览

核心逻辑在 emily_core.services.node_batch / node_batch_update，
本脚本仅负责 CLI 参数解析 + DB 初始化 + 报告输出。

用法：
    # 查 users 表获取 UUID（所有操作都需要 operator_id）
    docker exec emily-postgres psql -U emily -d emily \\
        -c "SELECT id, username, permission_level FROM users WHERE status='active' ORDER BY permission_level DESC LIMIT 5;"

    # ── 创建 ──
    uv run python scripts/manage_nodes.py create --file nodes.yaml --dry-run
    uv run python scripts/manage_nodes.py create --file nodes.yaml

    # ── 更新节点字段 ──
    uv run python scripts/manage_nodes.py update --file updates.yaml --dry-run
    uv run python scripts/manage_nodes.py update --file updates.yaml

    # ── 批量激活 ──
    uv run python scripts/manage_nodes.py activate --node-ids SG-001,SG-002 \\
        --operator-id <UUID>

    # ── 批量废弃 ──
    uv run python scripts/manage_nodes.py discard --node-ids SG-001,SG-002 \\
        --operator-id <UUID>

    # ── 批量更新成果进度 ──
    uv run python scripts/manage_nodes.py progress --file progress.yaml --dry-run
    uv run python scripts/manage_nodes.py progress --file progress.yaml

    # ── 管理节点文件关联 ──
    uv run python scripts/manage_nodes.py link-files --file links.yaml --dry-run
    uv run python scripts/manage_nodes.py link-files --file links.yaml

    # ── 查询 ──
    uv run python scripts/manage_nodes.py query --project-id ECOCITY-26

YAML 格式 — update：
    updates:
      - node_id: "SG-001"
        deadline: "2026-12-31"        # 只填要改的字段
        owner_dept_id: "设计部"
      - node_id: "SG-002"
        stage_id: 2

YAML 格式 — progress：
    operator_id: "<UUID>"
    progress:
      - node_id: "SG-001"
        deliverable_name: "主体结构验收报告"   # 按名称匹配（或用 deliverable_id）
        current_amount: 1
      - deliverable_id: "SG-002-DELV-001"     # 直接指定成果编号
        current_amount: 0.5

YAML 格式 — link-files：
    operator_id: "<UUID>"
    links:
      # 增加共享文件
      - node_id: "ECOC-001"
        action: "add_shared_file"
        file_no: "FIL-20260704-0001"
      # 移除共享文件
      - node_id: "ECOC-001"
        action: "remove_shared_file"
        file_no: "FIL-20260704-0002"
      # 设置条件文件（启动文档）
      - node_id: "ECOC-002"
        action: "set_startup_doc"
        file_no: "FIL-20260704-0003"
      # 清除条件文件
      - node_id: "ECOC-002"
        action: "clear_startup_doc"
      # 关联成果文件
      - node_id: "ECOC-003"
        action: "link_deliverable_file"
        deliverable_id: "ECOC-003-DELV-001"
        file_no: "FIL-20260704-0004"
      # 取消成果文件关联
      - node_id: "ECOC-003"
        action: "unlink_deliverable_file"
        deliverable_id: "ECOC-003-DELV-001"
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml

# ── 项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("manage_nodes")


# ══════════════════════════════════════════════════════════════════════════════
# DB 初始化
# ══════════════════════════════════════════════════════════════════════════════

def _init_db(db_url: str) -> None:
    """初始化数据库连接（复用 emily_core 的 session 模块）。"""
    from emily_core.infrastructure.database.session import init_db
    init_db(db_url)


# ══════════════════════════════════════════════════════════════════════════════
# YAML 解析
# ══════════════════════════════════════════════════════════════════════════════

def load_yaml(filepath: str | Path) -> dict:
    """加载 YAML 文件，返回解析后的字典。"""
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"YAML 文件不存在: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("YAML 文件顶层必须是字典")
    return data


# ══════════════════════════════════════════════════════════════════════════════
# node_id 自动编号（创建模式专用）
# ══════════════════════════════════════════════════════════════════════════════

def _assign_auto_node_ids(
    nodes: list[dict],
    project_prefix: str,
) -> list[dict]:
    """为缺少 node_id 的节点自动分配编号。"""
    used_nums = set()
    _collect_used_nums(nodes, project_prefix, used_nums)
    counter = [0]
    _assign_recursive(nodes, project_prefix, used_nums, counter)
    return nodes


def _collect_used_nums(nodes: list[dict], prefix: str, used_nums: set[int]) -> None:
    for n in nodes:
        nid = n.get("node_id", "")
        if nid and nid.startswith(f"{prefix}-"):
            suffix = nid[len(prefix) + 1:]
            try:
                used_nums.add(int(suffix))
            except ValueError:
                pass
        children = n.get("children", [])
        if children:
            _collect_used_nums(children, prefix, used_nums)


def _assign_recursive(
    nodes: list[dict], prefix: str, used_nums: set[int], counter: list[int],
) -> None:
    for n in nodes:
        if not n.get("node_id"):
            while True:
                counter[0] += 1
                if counter[0] not in used_nums:
                    break
            n["node_id"] = f"{prefix}-{counter[0]:03d}"
            logger.debug("自动编号: %s → %s", n.get("node_name", "?"), n["node_id"])
        children = n.get("children", [])
        if children:
            _assign_recursive(children, prefix, used_nums, counter)


# ══════════════════════════════════════════════════════════════════════════════
# 报告输出
# ══════════════════════════════════════════════════════════════════════════════

def _print_report(results: list[dict], dry_run: bool = False) -> None:
    """输出操作报告。"""
    phase_labels = {
        "create_node": "创建节点",
        "create_deliverable": "添加成果",
        "mount_child": "挂载子节点",
        "add_dependency": "添加依赖",
        "update_node": "更新节点",
        "activate_node": "激活节点",
        "discard_node": "废弃节点",
        "update_progress": "更新进度",
        "add_shared_file": "增加共享文件",
        "remove_shared_file": "移除共享文件",
        "set_startup_doc": "设置条件文件",
        "clear_startup_doc": "清除条件文件",
        "link_deliverable_file": "关联成果文件",
        "unlink_deliverable_file": "取消成果文件关联",
    }

    print("\n" + "=" * 60)
    print(f"全景节点操作报告{' (DRY-RUN)' if dry_run else ''}")
    print("=" * 60)

    for phase, label in phase_labels.items():
        phase_results = [r for r in results if r.get("phase") == phase]
        if not phase_results:
            continue

        success_count = sum(1 for r in phase_results if r.get("success"))
        skip_count = sum(1 for r in phase_results if r.get("skipped"))
        fail_count = sum(1 for r in phase_results if not r.get("success"))

        print(f"\n── {label} ──")
        print(f"  成功: {success_count}  跳过: {skip_count}  失败: {fail_count}")

        for r in phase_results:
            icon = "✓" if r.get("success") else "✗"
            if r.get("skipped"):
                icon = "○"
            if r.get("dry_run"):
                icon = "▹"
            node_id = r.get("node_id", "?")
            msg = r.get("message", "")
            print(f"  {icon} [{node_id}] {msg}")

    total_success = sum(1 for r in results if r.get("success"))
    total_fail = sum(1 for r in results if not r.get("success"))
    print("\n" + "-" * 60)
    print(f"总计: 成功 {total_success}, 失败 {total_fail}")
    print("=" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="全景节点图管理脚本",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── 共用 DB 参数 ──
    db_url_default = "postgresql://emily:emily_secret_2026@localhost:25432/emily"

    # ── create 子命令 ──
    p = sub.add_parser("create", help="从 YAML 文件批量创建节点")
    p.add_argument("--file", "-f", required=True, help="YAML 定义文件路径")
    p.add_argument("--db-url", default=db_url_default, help="PostgreSQL 连接 URL")
    p.add_argument("--dry-run", action="store_true", help="预览模式")

    # ── update 子命令 ──
    p = sub.add_parser("update", help="从 YAML 文件批量更新节点字段")
    p.add_argument("--file", "-f", required=True, help="YAML 更新文件路径")
    p.add_argument("--db-url", default=db_url_default, help="PostgreSQL 连接 URL")
    p.add_argument("--dry-run", action="store_true", help="预览模式")

    # ── activate 子命令 ──
    p = sub.add_parser("activate", help="批量激活（审批通过）节点")
    p.add_argument("--node-ids", required=True, help="节点编号，逗号分隔")
    p.add_argument("--operator-id", required=True, help="审批人 UUID")
    p.add_argument("--reject", action="store_true", help="拒绝而非通过")
    p.add_argument("--remark", default="", help="审批备注")
    p.add_argument("--db-url", default=db_url_default, help="PostgreSQL 连接 URL")
    p.add_argument("--dry-run", action="store_true", help="预览模式")

    # ── discard 子命令 ──
    p = sub.add_parser("discard", help="批量废弃节点")
    p.add_argument("--node-ids", required=True, help="节点编号，逗号分隔")
    p.add_argument("--operator-id", required=True, help="操作人 UUID")
    p.add_argument("--db-url", default=db_url_default, help="PostgreSQL 连接 URL")
    p.add_argument("--dry-run", action="store_true", help="预览模式")

    # ── progress 子命令 ──
    p = sub.add_parser("progress", help="批量更新成果进度")
    p.add_argument("--file", "-f", required=True, help="YAML 进度文件路径")
    p.add_argument("--db-url", default=db_url_default, help="PostgreSQL 连接 URL")
    p.add_argument("--dry-run", action="store_true", help="预览模式")

    # ── link-files 子命令 ──
    p = sub.add_parser("link-files", help="批量管理节点文件关联（共享文件/条件文件/成果文件）")
    p.add_argument("--file", "-f", required=True, help="YAML 文件关联定义路径")
    p.add_argument("--db-url", default=db_url_default, help="PostgreSQL 连接 URL")
    p.add_argument("--dry-run", action="store_true", help="预览模式")

    # ── query 子命令 ──
    p = sub.add_parser("query", help="查询项目节点概览")
    p.add_argument("--project-id", required=True, help="项目 ID")
    p.add_argument("--db-url", default=db_url_default, help="PostgreSQL 连接 URL")

    args = parser.parse_args()

    handlers = {
        "create": _run_create,
        "update": _run_update,
        "activate": _run_activate,
        "discard": _run_discard,
        "progress": _run_progress,
        "link-files": _run_link_files,
        "query": _run_query,
    }
    handlers[args.command](args)


# ══════════════════════════════════════════════════════════════════════════════
# create 子命令
# ══════════════════════════════════════════════════════════════════════════════

def _run_create(args) -> None:
    yaml_data = load_yaml(args.file)

    project_id = yaml_data.get("project_id", "")
    creator_id = yaml_data.get("creator_id", "")
    nodes = yaml_data.get("nodes", [])

    if not project_id:
        print("错误：YAML 文件缺少 project_id")
        sys.exit(1)
    if not creator_id:
        print(
            "错误：YAML 文件缺少 creator_id。"
            "请查 users 表填入管理员 UUID：\n"
            "  docker exec emily-postgres psql -U emily -d emily "
            "-c \"SELECT id, username, permission_level FROM users WHERE status='active' ORDER BY permission_level DESC LIMIT 5;\""
        )
        sys.exit(1)
    if not nodes:
        print("错误：YAML 文件 nodes 列表为空")
        sys.exit(1)

    _init_db(args.db_url)

    # 验证 creator_id 是否存在
    from emily_core.repositories.user_repo import UserRepository
    creator_user = UserRepository.get_by_id(creator_id)
    if creator_user is None:
        # Maybe it's a username? Try find_by_name
        creator_user = UserRepository.find_by_name(creator_id)
        if creator_user is not None:
            creator_id = creator_user.id

    logger.info("项目: %s | 创建人: %s (%s) | 节点数: %d",
                project_id, creator_user.username, creator_id, len(nodes))

    # node_id 自动编号
    project_prefix = yaml_data.get("project_prefix", project_id[:4].upper())
    _assign_auto_node_ids(nodes, project_prefix)

    from emily_core.services.node_batch import create_node_tree
    results = asyncio.run(create_node_tree(
        project_id=project_id,
        creator_id=creator_id,
        nodes=nodes,
        dry_run=args.dry_run,
    ))

    _print_report(results, dry_run=args.dry_run)
    if any(not r.get("success") for r in results):
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# update 子命令
# ══════════════════════════════════════════════════════════════════════════════

def _run_update(args) -> None:
    yaml_data = load_yaml(args.file)

    operator_id = yaml_data.get("operator_id", "")
    updates = yaml_data.get("updates", [])

    if not operator_id:
        print("错误：YAML 文件缺少 operator_id")
        sys.exit(1)
    if not updates:
        print("错误：YAML 文件 updates 列表为空")
        sys.exit(1)

    _init_db(args.db_url)
    logger.info("更新节点: %d 条 | 操作人: %s", len(updates), operator_id)

    from emily_core.services.node_batch_update import batch_update_nodes
    results = asyncio.run(batch_update_nodes(
        updates=updates,
        operator_id=operator_id,
        dry_run=args.dry_run,
    ))

    _print_report(results, dry_run=args.dry_run)
    if any(not r.get("success") for r in results):
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# activate 子命令
# ══════════════════════════════════════════════════════════════════════════════

def _run_activate(args) -> None:
    node_ids = [n.strip() for n in args.node_ids.split(",") if n.strip()]
    if not node_ids:
        print("错误：--node-ids 不能为空")
        sys.exit(1)

    _init_db(args.db_url)
    approved = not args.reject
    action = "激活" if approved else "拒绝"
    logger.info("批量%s: %s | 审批人: %s", action, node_ids, args.operator_id)

    from emily_core.services.node_batch_update import batch_activate_nodes
    results = asyncio.run(batch_activate_nodes(
        node_ids=node_ids,
        approver_id=args.operator_id,
        approved=approved,
        remark=args.remark,
        dry_run=args.dry_run,
    ))

    _print_report(results, dry_run=args.dry_run)
    if any(not r.get("success") for r in results):
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# discard 子命令
# ══════════════════════════════════════════════════════════════════════════════

def _run_discard(args) -> None:
    node_ids = [n.strip() for n in args.node_ids.split(",") if n.strip()]
    if not node_ids:
        print("错误：--node-ids 不能为空")
        sys.exit(1)

    _init_db(args.db_url)
    logger.info("批量废弃: %s | 操作人: %s", node_ids, args.operator_id)

    from emily_core.services.node_batch_update import batch_discard_nodes
    results = asyncio.run(batch_discard_nodes(
        node_ids=node_ids,
        operator_id=args.operator_id,
        dry_run=args.dry_run,
    ))

    _print_report(results, dry_run=args.dry_run)
    if any(not r.get("success") for r in results):
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# progress 子命令
# ══════════════════════════════════════════════════════════════════════════════

def _run_progress(args) -> None:
    yaml_data = load_yaml(args.file)

    operator_id = yaml_data.get("operator_id", "")
    progress = yaml_data.get("progress", [])

    if not operator_id:
        print("错误：YAML 文件缺少 operator_id")
        sys.exit(1)
    if not progress:
        print("错误：YAML 文件 progress 列表为空")
        sys.exit(1)

    _init_db(args.db_url)
    logger.info("更新进度: %d 条 | 操作人: %s", len(progress), operator_id)

    from emily_core.services.node_batch_update import batch_update_progress
    results = asyncio.run(batch_update_progress(
        progress_updates=progress,
        operator_id=operator_id,
        dry_run=args.dry_run,
    ))

    _print_report(results, dry_run=args.dry_run)
    if any(not r.get("success") for r in results):
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# link-files 子命令
# ══════════════════════════════════════════════════════════════════════════════

def _run_link_files(args) -> None:
    yaml_data = load_yaml(args.file)

    operator_id = yaml_data.get("operator_id", "")
    links = yaml_data.get("links", [])

    if not operator_id:
        print("错误：YAML 文件缺少 operator_id")
        sys.exit(1)
    if not links:
        print("错误：YAML 文件 links 列表为空")
        sys.exit(1)

    _init_db(args.db_url)
    logger.info("文件关联: %d 条 | 操作人: %s", len(links), operator_id)

    from emily_core.services.node_batch_update import batch_link_files
    results = asyncio.run(batch_link_files(
        links=links,
        operator_id=operator_id,
        dry_run=args.dry_run,
    ))

    _print_report(results, dry_run=args.dry_run)
    if any(not r.get("success") for r in results):
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# query 子命令
# ══════════════════════════════════════════════════════════════════════════════

def _run_query(args) -> None:
    from emily_core.repositories.node_repo import ProjectNodeRepo

    _init_db(args.db_url)

    node_list = asyncio.run(
        asyncio.to_thread(ProjectNodeRepo.find_by_project, args.project_id),
    )

    if not node_list:
        print(f"项目 {args.project_id} 下没有节点")
        return

    print(f"\n项目 {args.project_id} 的全景节点 ({len(node_list)} 个)")
    print("=" * 80)
    print(f"{'节点编号':<20} {'节点名称':<20} {'状态':<20} {'进度':>6} {'主责':<10}")
    print("-" * 80)
    for n in node_list:
        progress = float(n.progress) if n.progress else 0.0
        print(f"{n.node_id:<20} {n.node_name:<20} {n.status:<20} {progress:>5.1f}% {n.owner_dept_id or '':<10}")


if __name__ == "__main__":
    main()
