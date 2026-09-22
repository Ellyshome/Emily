#!/usr/bin/env python3
"""全景节点模板索引维护工具。

扫描 emily-data/node_templates/ 下的**模板单元目录**（每个单元 = 固定命名的结构化
清单入口 template.yaml + 同目录附件），生成/更新 index.yaml 索引。

模板单元形态：
    node_templates/{ref_id}/template.yaml     ← 结构化清单入口（机器可读）
    node_templates/{ref_id}/<附件>             ← 任意格式，仅登记不解析

索引登记：编号 / 节点名称 / 节点类型 / 阶段分组 / 摘要 / 清单入口 / 附件清单（名·大小·类型）。

两种运行模式：
  uv run python scripts/maintain_node_template_index.py          # 更新模式：自动补齐并写入
  uv run python scripts/maintain_node_template_index.py --check  # 检查模式：只报告差异，不修改

只读约束：
  模板目录在容器内以 :ro 挂载，索引写入只能在宿主机执行。
  容器内执行写入模式会以退出码 2 + 明确提示失败（不抛异常栈）。

集成点：
  - 宿主机：模板库变更后重建索引（或经 scripts/node_template_release.py 串联）
  - 容器内：--check 只读校验（self_check.py 巡检项）
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))

MANIFEST_NAME = "template.yaml"


# ── 路径解析 ──

def _resolve_templates_dir() -> Path:
    """解析模板目录，多级回退。"""
    env_dir = os.environ.get("EMILY_NODE_TEMPLATE_DIR", "")
    if env_dir:
        return Path(env_dir)

    candidates = [
        Path("/app/data/node_templates"),                       # 容器内绝对路径
        Path(__file__).resolve().parents[1] / "emily-data" / "node_templates",  # 开发环境
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]  # 回退到开发路径


# ── 模板单元扫描 ──

def _load_manifest(manifest: Path) -> dict:
    """读取结构化清单入口 template.yaml。"""
    try:
        import yaml
        return yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[警告] {manifest}: 清单读取失败（{e}），跳过")
        return {}


def _collect_attachments(unit_dir: Path) -> list[dict]:
    """登记单元目录内除清单入口外的全部附件（文件名 / 大小 / 类型）。"""
    out: list[dict] = []
    for f in sorted(unit_dir.iterdir()):
        if not f.is_file() or f.name == MANIFEST_NAME:
            continue
        out.append({
            "name": f.name,
            "size": f.stat().st_size,
            "type": (f.suffix.lstrip(".").lower() or "unknown"),
        })
    return out


def _scan_templates(templates_dir: Path) -> list[dict]:
    """扫描模板单元目录，返回模板信息列表。"""
    templates: list[dict] = []
    seen: set[str] = set()

    for unit_dir in sorted(p for p in templates_dir.iterdir() if p.is_dir()):
        manifest = unit_dir / MANIFEST_NAME
        if not manifest.exists():
            print(f"[警告] {unit_dir.name}/: 缺少 {MANIFEST_NAME}，跳过")
            continue

        data = _load_manifest(manifest)
        ref_id = str(data.get("ref_id", "") or "").strip()
        if not ref_id:
            print(f"[警告] {unit_dir.name}/: 清单缺少 ref_id，跳过")
            continue

        if ref_id in seen:
            print(f"[警告] {unit_dir.name}/: ref_id={ref_id} 重复，跳过")
            continue
        seen.add(ref_id)

        if unit_dir.name != ref_id:
            print(f"[警告] 目录名 {unit_dir.name} 与清单 ref_id={ref_id} 不一致（建议目录名 = 模板编号）")

        templates.append({
            "ref_id": ref_id,
            "node_name": data.get("node_name", ""),
            "node_type": data.get("node_type", "TASK"),
            "stage_id": data.get("stage_id", 0),
            "summary": data.get("summary", ""),
            "entry": f"{ref_id}/{MANIFEST_NAME}",
            "attachments": _collect_attachments(unit_dir),
        })

    return templates


# ── 索引读写 ──

def _read_index(index_path: Path) -> dict | None:
    """读取现有索引文件。"""
    if not index_path.exists():
        return None
    try:
        import yaml
        return yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[警告] 索引文件读取失败: {e}")
        return None


def _render_index(templates: list[dict]) -> str:
    """渲染索引内容（不落盘）。"""
    import yaml

    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M:%S")
    stage_dist: dict = {}
    type_dist: dict = {}
    for t in templates:
        sid = t.get("stage_id", 0)
        stage_dist[sid] = stage_dist.get(sid, 0) + 1
        nt = t.get("node_type", "UNKNOWN")
        type_dist[nt] = type_dist.get(nt, 0) + 1

    data = {
        "updated_at": now,
        "template_count": len(templates),
        "stage_distribution": stage_dist,
        "type_distribution": type_dist,
        "templates": templates,
    }

    header = (
        "# 全景节点模板索引\n"
        "# 自动生成，勿手动编辑。\n"
        "# 维护脚本: uv run python scripts/maintain_node_template_index.py（宿主机执行，模板目录只读）\n"
        "#\n"
        "# 用途：模板库发现入口——列出全部模板单元的编号/名称/类型/摘要与附件清单。\n"
        "# 读取方：NodeTemplateLoader（只读检索能力）、控制台模板面板。\n"
        "# 对象声明（declarations）不进索引，随模板内容按需读取。\n"
        "\n"
    )
    return header + yaml.dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120
    )


def _write_index(index_path: Path, templates: list[dict]) -> str:
    """写入索引文件。返回 YAML 字符串供 diff。"""
    text = _render_index(templates)
    try:
        index_path.write_text(text, encoding="utf-8")
    except OSError as e:
        raise ReadOnlyTemplateDir(
            f"模板目录只读，无法写入索引：{index_path}（{e}）\n"
            f"→ 索引写入须在宿主机执行；容器内请用 --check 做只读校验。"
        ) from e
    return text


class ReadOnlyTemplateDir(RuntimeError):
    """模板目录不可写（容器内 :ro 挂载）。"""


# ── 差异比较 ──

def _diff_index(current: list[dict], previous: dict | None) -> tuple[list[str], list[str], list[str]]:
    """比较当前模板与索引的差异。

    Returns:
        (added, removed, changed) — 列表为 ref_id；changed 为条目字段有变（含附件清单变化）
    """
    current_map = {t["ref_id"]: t for t in current}
    prev_map = {}
    if previous and "templates" in previous:
        prev_map = {t.get("ref_id", ""): t for t in previous["templates"] if t.get("ref_id")}

    added = sorted(set(current_map) - set(prev_map))
    removed = sorted(set(prev_map) - set(current_map))
    changed = []
    for ref in sorted(set(current_map) & set(prev_map)):
        cur, prev = dict(current_map[ref]), dict(prev_map[ref])
        # updated_at 类噪声字段不在条目内，逐字段比对（附件清单含 size，改动即视为变更）
        if cur != prev:
            changed.append(ref)
    return added, removed, changed


# ══════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="全景节点模板索引维护工具")
    parser.add_argument("--check", action="store_true",
                        help="检查模式：只报告差异，不修改索引文件（容器内可执行）")
    args = parser.parse_args()

    templates_dir = _resolve_templates_dir()
    if not templates_dir.exists():
        print(f"[错误] 模板目录不存在: {templates_dir}")
        return 1

    index_path = templates_dir / "index.yaml"

    # ── 1. 扫描当前模板单元 ──
    current = _scan_templates(templates_dir)
    if not current:
        print(f"[警告] 未找到任何有效模板单元（需 {MANIFEST_NAME} 且含 ref_id）")
        return 1

    # ── 2. 读取旧索引 ──
    previous = _read_index(index_path)

    # ── 3. 差异分析 ──
    added, removed, changed = _diff_index(current, previous)

    # ── 4. 检查模式 ──
    if args.check:
        if added or removed or changed:
            print(f"[检查] 索引不同步 —— 新增 {len(added)} / 删除 {len(removed)} / 变更 {len(changed)}")
            for a in added:
                t = next(t for t in current if t["ref_id"] == a)
                print(f"  + {t['ref_id']} {t['node_name']}")
            for c in changed:
                print(f"  ~ {c}")
            for r in removed:
                print(f"  - {r}")
            return 1
        print(f"[检查] 索引同步（{len(current)} 个模板）")
        return 0

    # ── 5. 写入模式 ──
    if added or removed or changed or not previous:
        try:
            _write_index(index_path, current)
        except ReadOnlyTemplateDir as e:
            print(f"[错误] {e}")
            return 2
        msg = f"[更新] 索引已更新 —— {len(current)} 个模板"
        if added:
            msg += f"（新增 {len(added)}: {', '.join(added)}）"
        if changed:
            msg += f"（变更 {len(changed)}: {', '.join(changed)}）"
        if removed:
            msg += f"（删除 {len(removed)}: {', '.join(removed)}）"
        print(msg)
    else:
        print(f"[跳过] 索引已最新（{len(current)} 个模板，无变化）")

    # ── 6. 打印模板清单 ──
    print()
    for t in current:
        atts = t.get("attachments", [])
        print(f"  {t['ref_id']} [{t['node_type']}] {t['node_name']}  附件 {len(atts)} 个")
        if t["summary"]:
            print(f"    {t['summary']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
