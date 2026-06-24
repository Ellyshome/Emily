"""FlowMapManager + MermaidFlowRenderer —— Mermaid 决策树文件管理器。

M7.1: 替代旧的 SkillRegistry，以 Mermaid 决策树文件系统为真相源。

架构角色：
- FlowMapManager: 启动时扫描 prompts/flows/ 目录，解析所有 .md 文件
- MermaidFlowRenderer: 将 FlowDefinition 渲染为 LLM 可读文本
- 根图+子图全部注入 system prompt（{ROOT_FLOW} + {SUB_FLOWS}），无需工具调用
- 无图覆盖时原子工具自由推理兜底

注意：FlowMapManager 本身不追踪访问状态（_visited），
访问追踪由 MasterAgent 实例内部维护，避免多用户并发干扰。
"""

import logging
from pathlib import Path
from typing import Optional

from .mermaid_flow import MermaidFlowParser, FlowDefinition, NL2Flow

logger = logging.getLogger("emily.agent.flow")


# ==============================================================================
# MermaidFlowRenderer
# ==============================================================================


class MermaidFlowRenderer:
    """将 FlowDefinition 渲染为 LLM prompt 可读文本。

    用途：
    - 根图在启动时渲染为 prompt 片段注入 {ROOT_FLOW}
    - 子图被 prompt injection 加载时渲染为结构化文本
    """

    @staticmethod
    def render_for_prompt(flow: FlowDefinition) -> str:
        """渲染图为 prompt 文本。

        返回格式：
            ## 业务决策树根图
            <flow.description>（适用场景）

            ```mermaid
            <flow.raw_mermaid>
            ```

            <flow.guidance>（分支指引）

            可查阅的子图文件：
            - event_flow.md — 事件录入详细流程
            ...

        Args:
            flow: 解析后的决策图定义

        Returns:
            渲染后的 markdown 文本
        """
        lines: list[str] = []

        # 标题
        title = flow.display_name or flow.name or "业务决策树"
        lines.append(f"## {title}")

        # 适用场景
        if flow.description:
            lines.append("")
            lines.append(flow.description)

        # Mermaid 图
        if flow.raw_mermaid:
            lines.append("")
            lines.append("```mermaid")
            lines.append(flow.raw_mermaid)
            lines.append("```")

        # 分支指引
        if flow.guidance:
            lines.append("")
            lines.append(flow.guidance)

        # 可查阅的子图文件
        if flow.branches:
            lines.append("")
            lines.append("**可查阅的子图文件：**")
            for filename, desc in flow.branches:
                name = filename.replace(".md", "")
                lines.append(f"- `{name}`  — {desc}")

        return "\n".join(lines)

    @staticmethod
    def render_brief(flow: FlowDefinition) -> dict:
        """渲染子图的简要信息（用于 list_flows 返回）。

        Returns:
            {"name": str, "display_name": str, "description": str}
        """
        return {
            "name": flow.name,
            "display_name": flow.display_name or flow.name,
            "description": flow.description or "",
        }


# ==============================================================================
# FlowMapManager
# ==============================================================================


class FlowMapManager:
    """Mermaid 决策树文件管理器。

    职责：
    1. 启动时扫描 flow_dir，解析所有 .md 文件
    2. 提供根图文本供 prompt 注入
    3. 提供 load_flow(name) 供 prompt injection 工具调用
    4. 可选：NL→LLM→Mermaid→写入文件

    Args:
        flow_dir: 图文件目录路径。None 或空字符串 → 默认 prompts/flows/
        llm_client: LLM 客户端（可选，用于 NL2Flow 功能）
    """

    def __init__(self, flow_dir: str | None = None, llm_client=None):
        # 解析 flow_dir
        if flow_dir:
            self._flow_dir = Path(flow_dir)
        else:
            # 默认路径：prompts/flows/
            self._flow_dir = (
                Path(__file__).parent.parent / "prompts" / "flows"
            ).resolve()

        # 创建目录（如不存在）
        self._flow_dir.mkdir(parents=True, exist_ok=True)

        self._root_name = "main"
        self._llm_client = llm_client
        self._parser = MermaidFlowParser()

        # 扫描所有 .md 文件
        self._flows: dict[str, FlowDefinition] = {}
        count = self._scan()
        logger.info(
            "FlowMapManager initialized: %d flow(s) loaded from %s",
            count, self._flow_dir,
        )

    # ── 核心方法 ──

    def get_root_flow_text(self) -> str:
        """返回根图文件全文（渲染后），用于注入 system prompt 的 {ROOT_FLOW}。

        如果根图不存在，返回空字符串（系统正常运行，只是无决策树引导）。
        """
        root_flow = self._flows.get(self._root_name)
        if root_flow is None:
            logger.debug("Root flow '%s.md' not found, {ROOT_FLOW} will be empty", self._root_name)
            return ""
        return MermaidFlowRenderer.render_for_prompt(root_flow)

    def get_all_sub_flows_text(self) -> str:
        """返回所有非根图 flow 文件的渲染文本，用于 prompt 注入 {SUB_FLOWS}。

        如果只有根图（无子图），返回空字符串。
        """
        parts = []
        for name, flow in self._flows.items():
            if name == self._root_name:
                continue
            parts.append(MermaidFlowRenderer.render_for_prompt(flow))
            parts.append("")
        return "\n".join(parts)

    def load_flow(self, name: str) -> dict | None:
        """加载指定子图文件。

        Args:
            name: 文件名（不含 .md 后缀，如 "event_flow"）

        Returns:
            {
                "name": str,
                "display_name": str,
                "description": str,
                "diagram": str,
                "guidance": str,
                "branches": [{"file": "xxx.md", "description": "..."}, ...],
                "full_text": str,
            }
            文件不存在返回 None。
        """
        name = name.strip()
        if name.endswith(".md"):
            name = name[:-3]

        flow = self._flows.get(name)
        if flow is None:
            return None

        return {
            "name": flow.name,
            "display_name": flow.display_name or flow.name,
            "description": flow.description,
            "diagram": flow.raw_mermaid,
            "guidance": flow.guidance,
            "branches": [
                {"file": filename, "description": desc}
                for filename, desc in flow.branches
            ],
            "full_text": flow.raw_text,
        }

    def list_flows(self) -> list[dict]:
        """列出所有已加载的流程图。

        Returns:
            [{"name": str, "display_name": str, "description": str}, ...]
        """
        return [MermaidFlowRenderer.render_brief(flow)
                for flow in self._flows.values()]

    def reload(self) -> int:
        """重新扫描目录，返回加载的文件数量。"""
        self._flows.clear()
        return self._scan()

    # ── 可选方法 ──

    async def from_natural_language(
        self, description: str, file_name: str = None,
    ) -> dict | None:
        """NL → LLM → Mermaid → 写入 flow_dir → 重新加载 → 返回 load_flow 结果。

        Args:
            description: 业务流程的自然语言描述
            file_name: 文件名（不含 .md，蛇形命名如 "my_flow"）

        Returns:
            load_flow() 的结果字典，失败返回 None。
        """
        if self._llm_client is None:
            logger.warning("FlowMapManager.from_natural_language: no LLM client")
            return None

        nl2flow = NL2Flow(self._llm_client)
        try:
            md_content = await nl2flow.generate(description)
        except Exception as e:
            logger.error("NL2Flow generation failed: %s", e)
            return None

        # 自动生成文件名
        if not file_name:
            import re
            safe = re.sub(r'[^\w]+', '_', description[:30]).strip('_')[:30]
            file_name = safe or "custom_flow"

        # 写入文件
        file_path = self._flow_dir / f"{file_name}.md"
        try:
            file_path.write_text(md_content, encoding="utf-8")
            logger.info("FlowMapManager: wrote %s (%d chars)", file_path, len(md_content))
        except Exception as e:
            logger.error("Failed to write flow file %s: %s", file_path, e)
            return None

        # 重新加载
        self.reload()

        return self.load_flow(file_name)

    # ── 内部方法 ──

    def _scan(self) -> int:
        """扫描 flow_dir 下所有 .md 文件，用 MermaidFlowParser 解析。

        Returns:
            加载的文件数量
        """
        if not self._flow_dir.exists():
            logger.warning("Flow dir does not exist: %s", self._flow_dir)
            return 0

        count = 0
        for md_file in sorted(self._flow_dir.glob("*.md")):
            try:
                flow = self._parser.parse_file(md_file)
                self._flows[flow.name] = flow
                count += 1
            except Exception as e:
                logger.error("Failed to parse flow file %s: %s", md_file, e, exc_info=True)

        # 如果根图 "main" 不存在但目录不为空，使用第一个文件作为根图
        if self._root_name not in self._flows and self._flows:
            first_name = sorted(self._flows.keys())[0]
            logger.info(
                "No 'main.md' found, using '%s' as root flow", first_name,
            )
            self._root_name = first_name

        logger.info("FlowMapManager scanned: %d .md file(s) loaded", count)
        return count

    def _resolve_path(self, name: str) -> Path | None:
        """将文件名解析为完整文件路径。

        Args:
            name: 文件名（不含 .md）

        Returns:
            Path 对象，或 None
        """
        path = self._flow_dir / f"{name}.md"
        if path.exists():
            return path
        return None
