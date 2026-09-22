#!/usr/bin/env python3
"""全景节点模板库上线聚合薄壳（宿主机执行）。

串联三步（薄壳只串联，不含业务逻辑）：
  1. migrate_node_templates --verify   存量模板形态迁移 + 四项一致性断言
  2. maintain_node_template_index      递归重建 index.yaml（含附件清单）
  3. maintain_node_template_index --check  索引与模板单元一致性校验

用法：
  uv run python scripts/node_template_release.py            # 完整上线
  uv run python scripts/node_template_release.py --dry-run  # 只预览迁移与索引差异
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE.parent / "emily-data" / "node_templates"


def _run(step: str, cmd: list[str]) -> tuple[int, str]:
    print(f"\n── {step} ──")
    print("$ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(_HERE.parent), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out.rstrip())
    return r.returncode, out


def main() -> int:
    parser = argparse.ArgumentParser(description="全景节点模板库上线（迁移 → 索引 → 校验）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不落盘")
    args = parser.parse_args()

    py = sys.executable
    migrate = str(_HERE / "migrate_node_templates.py")
    index = str(_HERE / "maintain_node_template_index.py")

    summary: list[str] = []

    # 1) 存量迁移
    rc, out = _run("① 存量模板迁移", [py, migrate] + (["--dry-run"] if args.dry_run else []))
    if rc != 0:
        summary.append("① 迁移 FAIL")
    else:
        summary.append("① 迁移 " + ("预览完成" if args.dry_run else "完成"))

    # 2) 四项一致性断言（dry-run 下基线未更新，跳过）
    if args.dry_run:
        summary.append("② 四项断言 跳过（dry-run）")
    else:
        rc, _ = _run("② 四项一致性断言（AC-US-01.5）", [py, migrate, "--verify"])
        summary.append("② 四项断言 " + ("PASS" if rc == 0 else "FAIL"))
        if rc != 0:
            print("\n[中止] 四项断言不通过——模板内容在迁移中被改变，请核对后重试。")
            return 1

    # 3) 索引重建
    rc, _ = _run("③ 索引重建", [py, index] + ([] if not args.dry_run else []))
    if args.dry_run:
        summary.append("③ 索引重建 跳过（dry-run）")
    else:
        summary.append("③ 索引重建 " + ("完成" if rc == 0 else f"FAIL(rc={rc})"))

    # 4) 索引一致性校验
    rc, _ = _run("④ 索引一致性校验", [py, index, "--check"])
    ok = rc == 0
    summary.append("④ 索引校验 " + ("同步" if ok else "不同步"))
    if not ok:
        print("\n[中止] 索引与模板单元不一致。")
        return 1

    print("\n════ 上线汇总 ════")
    for line in summary:
        print(f"  {line}")
    print(f"  模板目录: {_TEMPLATES_DIR}")
    print("  结果: 模板库已就绪" + ("（预览模式，未落盘）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
