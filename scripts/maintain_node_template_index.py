#!/usr/bin/env python3
"""全景节点模板索引维护工具。

扫描 emily-data/node_templates/ 下所有 .md 模板文件（README.md 除外），
解析 frontmatter + 节点说明，生成/更新 index.yaml 索引。

三种运行模式：
  uv run python scripts/maintain_node_template_index.py          # 更新模式：自动补齐
  uv run python scripts/maintain_node_template_index.py --check  # 检查模式：只报告差异，不修改
  uv run python scripts/maintain_node_template_index.py --auto   # 自动模式：静默，无差异不输出

集成点：
  - 手动触发：开发新增模板后运行
  - 启动自检：bootstrap.py 中 --auto 模式调用
  - 自检脚本：system_check 中 --check 模式调用
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))

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


# ── Frontmatter 解析 ──

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    """解析 Markdown 文件的 YAML frontmatter。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        import yaml
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}


def _extract_summary(body: str) -> str:
    """从 ## 节点说明 中提取首句作为摘要。"""
    # 匹配 "## 节点说明" 之后的第一个非空段落的第一个句子（到第一个句号）
    pattern = re.compile(r"##\s+节点说明\s*\n+(.*?)(?=\n##|\n#|\Z)", re.DOTALL)
    m = pattern.search(body)
    if not m:
        return ""

    section = m.group(1).strip()
    # 取第一句（到句号结束，截断在 120 字以内）
    first_sentence = re.split(r"[。.]", section)[0].strip()
    first_sentence = re.sub(r"\n+", " ", first_sentence)  # 去除换行
    first_sentence = re.sub(r"\s{2,}", " ", first_sentence)  # 压缩空格
    if len(first_sentence) > 120:
        first_sentence = first_sentence[:120] + "…"
    return first_sentence


# ── 模板扫描 ──

def _scan_templates(templates_dir: Path) -> list[dict]:
    """扫描目录下所有 .md 模板文件，返回模板信息列表。"""
    templates: list[dict] = []
    seen: set[str] = set()

    for md_file in sorted(templates_dir.glob("*.md")):
        if md_file.name == "README.md":  # 跳过映射说明文档
            continue

        text = md_file.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        ref_id = fm.get("ref_id", "")
        if not ref_id:
            print(f"[警告] {md_file.name}: 缺少 ref_id，跳过")
            continue

        if ref_id in seen:
            print(f"[警告] {md_file.name}: ref_id={ref_id} 重复，跳过")
            continue
        seen.add(ref_id)

        summary = _extract_summary(text)
        templates.append({
            "ref_id": ref_id,
            "node_name": fm.get("node_name", ""),
            "node_type": fm.get("node_type", "WORK_PACKAGE"),
            "stage_id": fm.get("stage_id", 0),
            "summary": summary,
            "file": md_file.name,
        })

    return templates


# ── 索引读写 ──

def _read_index(index_path: Path) -> dict | None:
    """读取现有索引文件。"""
    if not index_path.exists():
        return None
    try:
        import yaml
        with open(index_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[警告] 索引文件读取失败: {e}")
        return None


def _write_index(index_path: Path, templates: list[dict]) -> str:
    """写入索引文件。返回 YAML 字符串供 diff。"""
    import yaml

    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M:%S")

    # 按 stage_id 分组统计
    stage_dist: dict[int, int] = {}
    type_dist: dict[str, int] = {}
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

    yaml_text = (
        "# 全景节点模板索引\n"
        "# 自动生成，勿手动编辑。\n"
        "# 维护脚本: uv run python scripts/maintain_node_template_index.py\n"
        "#\n"
        "# 用途：供 AI 工具快速了解模板库内容，选择匹配的参考节点。\n"
        "# Session 拉起时本索引可能被注入 prompt，告诉 AI 有模板库可用。\n"
        "\n"
    )
    yaml_text += yaml.dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120
    )
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(yaml_text)
    return yaml_text


# ── 差异比较 ──

def _diff_index(
    current: list[dict], previous: dict | None
) -> tuple[list[str], list[str], list[str]]:
    """比较当前模板与索引的差异。
    Returns:
        (added, removed, unchanged) — 列表为 ref_id
    """
    current_ids = {t["ref_id"] for t in current}
    prev_ids = set()
    if previous and "templates" in previous:
        prev_ids = {t.get("ref_id", "") for t in previous["templates"] if t.get("ref_id")}

    added = sorted(current_ids - prev_ids)
    removed = sorted(prev_ids - current_ids)
    unchanged = sorted(current_ids & prev_ids)

    return added, removed, unchanged


# ══════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="全景节点模板索引维护工具")
    parser.add_argument(
        "--check", action="store_true",
        help="检查模式：只报告差异，不修改索引文件"
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="自动模式：静默运行，无差异时不输出（供启动自检使用）"
    )
    args = parser.parse_args()

    templates_dir = _resolve_templates_dir()
    if not templates_dir.exists():
        msg = f"[错误] 模板目录不存在: {templates_dir}"
        if args.auto:
            return 0  # 静默跳过
        print(msg)
        return 1

    index_path = templates_dir / "index.yaml"

    # ── 1. 扫描当前模板 ──
    current = _scan_templates(templates_dir)
    if not current:
        msg = "[警告] 未找到任何有效模板文件（缺少 ref_id 或 frontmatter 格式错误）"
        if args.auto:
            return 0
        print(msg)
        return 1

    # ── 2. 读取旧索引 ──
    previous = _read_index(index_path)

    # ── 3. 差异分析 ──
    added, removed, unchanged = _diff_index(current, previous)

    # ── 4. 检查模式 ──
    if args.check:
        if added or removed:
            print(f"[检查] 索引不同步 —— 新增 {len(added)} / 删除 {len(removed)}")
            for a in added:
                t = next(t for t in current if t["ref_id"] == a)
                print(f"  + {t['ref_id']} {t['node_name']}")
            for r in removed:
                print(f"  - {r}")
            return 1
        print(f"[检查] 索引同步（{len(current)} 个模板）")
        return 0

    # ── 5. 写入模式 ──
    if added or removed or not previous:
        _write_index(index_path, current)
        msg = f"[更新] 索引已更新 —— {len(current)} 个模板"
        if added:
            msg += f"（新增 {len(added)}: {', '.join(added)}）"
        if removed:
            msg += f"（删除 {len(removed)}: {', '.join(removed)}）"
        print(msg)
    else:
        if not args.auto:
            print(f"[跳过] 索引已最新（{len(current)} 个模板，无变化）")

    # ── 6. 打印模板清单 ──
    if not args.auto:
        print()
        for t in current:
            print(f"  {t['ref_id']} [{t['node_type']}] {t['node_name']}")
            if t["summary"]:
                print(f"    {t['summary']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
