"""pdfread —— AI 友好的 PDF 文本提取 CLI。

用法:
  python scripts/pdfread.py <file.pdf> [page_start] [page_end]   # 提取指定页文本
  python scripts/pdfread.py <file.pdf> all                        # 全量提取
  python scripts/pdfread.py <file.pdf> meta                       # 只看页数与每页首行

示例:
  uv run python scripts/pdfread.py "docs/论文类/参考文献类/xxx.pdf" 1 3
  uv run python scripts/pdfread.py "docs/论文类/参考文献类/xxx.pdf" meta

依赖: pymupdf（已装入项目 venv）
"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

import fitz  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"[ERROR] file not found: {path}")
        sys.exit(1)

    doc = fitz.open(path)

    if len(sys.argv) >= 3 and sys.argv[2] == "meta":
        print(f"file: {path}\npages: {doc.page_count}")
        for i in range(doc.page_count):
            first_line = (doc[i].get_text().strip().split("\n") or [""])[0][:60]
            print(f"  p{i+1}: {first_line}")
        sys.exit(0)

    if len(sys.argv) >= 3 and sys.argv[2] == "all":
        start, end = 1, doc.page_count
    else:
        start = int(sys.argv[2]) if len(sys.argv) >= 3 else 1
        end = int(sys.argv[3]) if len(sys.argv) >= 4 else start

    end = min(end, doc.page_count)
    for i in range(start - 1, end):
        print(f"\n===== page {i+1} =====")
        print(doc[i].get_text())


if __name__ == "__main__":
    main()
