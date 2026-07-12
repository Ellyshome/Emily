"""修复 程序鉴别材料_完整版.md 中缺少代码围栏的问题。

源 md 文件中 220 个源代码段没有用 ```python / ``` 包裹，
导致 Markdown PDF 插件将其作为普通段落渲染，
造成缩进丢失、换行折叠、__dunder__ 变粗体等问题。

本脚本自动在每个"文件编号：N"行之后、"文件路径：..."行之后
插入 ```python，在下个"文件编号："行之前插入 ```。

用法:
    python scripts/fix_code_fences.py
"""

import re
import shutil
from pathlib import Path

MD_FILE = Path(__file__).resolve().parent.parent / "docs" / "程序鉴别材料_完整版.md"
BACKUP_FILE = MD_FILE.with_suffix(".md.bak")

# 正则匹配 "文件编号：数字" 行
FILE_NO_RE = re.compile(r"^文件编号：\d+$")
# 正则匹配 "文件路径：..." 行
FILE_PATH_RE = re.compile(r"^文件路径：.+$")


def fix():
    if not MD_FILE.exists():
        print(f"文件不存在: {MD_FILE}")
        return

    text = MD_FILE.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 找到所有 "文件编号：" 行的索引
    file_no_indices = []
    for i, line in enumerate(lines):
        if FILE_NO_RE.match(line.strip()):
            file_no_indices.append(i)

    print(f"找到 {len(file_no_indices)} 个文件编号标记")

    if not file_no_indices:
        print("未找到文件编号标记，退出")
        return

    # 备份原文件
    shutil.copy2(MD_FILE, BACKUP_FILE)
    print(f"已备份原文件到: {BACKUP_FILE}")

    # 从后往前插入，避免索引偏移
    # 对每个文件编号区域：
    #   1. 在 "文件路径：..." 行之后插入 ```python
    #   2. 在下个 "文件编号：" 行之前插入 ```（即上一个代码块结束）
    #   3. 最后一个文件需要在末尾追加 ```

    insertions = []  # (index, text_to_insert_after)

    for idx_pos, no_idx in enumerate(file_no_indices):
        # 找 "文件路径：" 行（应该在文件编号行之后紧接着）
        path_line_idx = no_idx + 1
        while path_line_idx < len(lines) and not FILE_PATH_RE.match(lines[path_line_idx].strip()):
            path_line_idx += 1

        if path_line_idx >= len(lines):
            print(f"警告: 文件编号行 {no_idx} 后未找到文件路径行，跳过")
            continue

        # 在文件路径行之后插入 ```python
        insertions.append((path_line_idx, "```python"))

        # 找下一个文件编号行（或文件末尾）之前的空行位置，插入 ```
        if idx_pos + 1 < len(file_no_indices):
            next_no_idx = file_no_indices[idx_pos + 1]
        else:
            next_no_idx = len(lines)

        # 从 next_no_idx 往前找第一个非空行，在其后插入 ```
        # 代码块结束标记应放在代码最后一行之后、下个文件编号之前
        end_idx = next_no_idx - 1
        while end_idx > path_line_idx and lines[end_idx].strip() == "":
            end_idx -= 1

        insertions.append((end_idx, "```"))

    # 去重并按索引从大到小排序（从后往前插入）
    insertions.sort(key=lambda x: x[0], reverse=True)

    # 执行插入
    for idx, fence in insertions:
        lines.insert(idx + 1, fence)

    # 写入修复后的文件
    result = "\n".join(lines)
    MD_FILE.write_text(result, encoding="utf-8")
    print(f"已修复代码围栏，共插入 {len(insertions)} 个围栏标记")
    print(f"修复后文件: {MD_FILE}")
    print(f"原文件备份: {BACKUP_FILE}")


if __name__ == "__main__":
    fix()
