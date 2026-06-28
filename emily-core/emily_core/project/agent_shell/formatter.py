# emily-core/emily_core/project/agent_shell/formatter.py
"""终端输出格式化工具 —— ASCII 表格、带边框文本框、状态栏。

完全基于 Python 标准库，零第三方依赖。
"""

from __future__ import annotations

from typing import List


class ShellFormatter:
    """终端输出格式化工具。

    提供静态方法，无需实例化即可使用：
        fmt = ShellFormatter()
        print(fmt.table(headers, rows))
        print(fmt.box("一段文字"))
    """

    @staticmethod
    def box(content: str, width: int = 72) -> str:
        """输出带边框的文本框。

        Args:
            content: 文本内容（可含 \n）
            width: 框体宽度（含边框字符），默认 72

        Returns:
            格式化后的多行字符串
        """
        lines = content.split("\n")
        inner_width = width - 4  # 两侧边框 + 空格
        border = "─" * (width - 2)
        result = [f"┌{border}┐"]
        for line in lines:
            # 处理中文对齐：中文占 2 个字符宽度
            result.append(f"│ {line:<{inner_width}} │")
        result.append(f"└{border}┘")
        return "\n".join(result)

    @staticmethod
    def table(headers: List[str], rows: List[List[str]]) -> str:
        """生成 ASCII 表格。

        Args:
            headers: 表头列表
            rows: 数据行列表，每行长度需与 headers 一致

        Returns:
            格式化后的表格字符串（含表头分隔线）
        """
        if not rows:
            return "  (无数据)"

        # 计算每列宽度
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        # 生成分隔线
        sep = "  +" + "+".join("-" * (w + 2) for w in col_widths) + "+"

        # 生成表头
        header_line = "  |" + "|".join(
            f" {h:<{w}} " for h, w in zip(headers, col_widths)
        ) + "|"

        # 生成数据行
        data_lines = []
        for row in rows:
            line = "  |" + "|".join(
                f" {str(c):<{w}} " for c, w in zip(row, col_widths)
            ) + "|"
            data_lines.append(line)

        # 组装
        parts = ["", sep, header_line, sep]
        parts.extend(data_lines)
        parts.append(sep)
        return "\n".join(parts)

    @staticmethod
    def status_bar(data: dict[str, str]) -> str:
        """生成键值对状态栏。

        Args:
            data: 键值对字典

        Returns:
            格式化后的多行字符串
        """
        max_key_len = max(len(k) for k in data.keys()) if data else 0
        lines = []
        for key, value in data.items():
            lines.append(f"  {key:<{max_key_len}} : {value}")
        return "\n".join(lines)
