"""SOP 业务流工具函数 —— M9 辅助模块。

原 invoke_business_flow 和 list_sop_catalog 工具已移除：
  - invoke_business_flow → MasterAgent._dispatch_specialist()（框架内置方法）
  - list_sop_catalog → 已通过 SOPIntentRegistry.dump_as_text() 注入 system prompt

保留 _extract_allowed_tools_from_sop() 供 MasterAgent 和 BusinessFlowAgent 使用。
"""

import logging

logger = logging.getLogger("emily.tools.business_flow")


# ══════════════════════════════════════════════════════════════════════════════
# 辅助函数：从 SOP 文本中提取允许的工具名
# ══════════════════════════════════════════════════════════════════════════════


def _extract_allowed_tools_from_sop(sop_text: str) -> list[str]:
    """从 SOP §3.2 表格中提取声明的工具名列表。

    使用 mistune 块级解析器提取 §3.2 工具表；
    如果 mistune 不可用或解析失败，降级到旧正则方式。
    """
    try:
        from ..agent.sop_parser import extract_allowed_tools_from_sop

        return extract_allowed_tools_from_sop(sop_text)
    except Exception:
        import re

        tools: list[str] = []

        # 查找 §3.2 章节
        section_match = re.search(
            r"###?\s*3\.2\s+使用的系统工具.*?\n(.*?)(?=###?\s+3\.3|##\s+4\.)",
            sop_text,
            re.DOTALL,
        )
        if not section_match:
            return tools

        section_text = section_match.group(1)

        # 从 Markdown 表格中提取工具名（反引号包裹的）
        tool_names = re.findall(r"`(\w+)`", section_text)
        for name in tool_names:
            if name.startswith("record_") or name.startswith("query_") or name.startswith("invoke_") or name.startswith("manage_") or name.startswith("write_"):
                if name not in tools:
                    tools.append(name)

        return tools
