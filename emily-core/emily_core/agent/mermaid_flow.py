"""MermaidFlow —— Mermaid 决策图解析器。

M7.1: 删除了 SkillRegistry 相关类（FlowToSkillGenerator, MermaidFlowRegistry）。
现在 MermaidFlowParser 解析 Mermaid 图为 FlowDefinition，
FlowMapManager（在 flow_renderer.py）管理图文件系统，
MasterAgent 通过 {SUB_FLOWS} 占位符全部注入 system prompt（无需工具调用）。

核心思路：
1. 启动时 FlowMapManager 扫描 prompts/flows/ 目录
2. 根图 main.md 注入 system prompt ({ROOT_FLOW})
3. 子图全部注入 system prompt ({SUB_FLOWS})，无需 LLM 工具调用
4. 无图覆盖时原子工具自由推理兜底
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("emily.agent.flow")


# ==============================================================================
# 核心数据结构（增强）
# ==============================================================================


@dataclass
class FlowNode:
    """Mermaid 图中的一个节点。

    Attributes:
        node_id: 图中的节点标识（如 "A", "B"）
        label: 节点显示文本
        node_type: 节点类型 — "action" (方角[]) 或 "decision" (菱形{})
        tool_name: 映射到 ToolRegistry 的工具名
        params: 工具参数
    """

    node_id: str
    label: str
    node_type: str = "action"  # "action" | "decision"
    tool_name: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class FlowDefinition:
    """从 Mermaid 图文件解析出的完整决策图定义。

    Attributes:
        name: 文件名（不含 .md 后缀，唯一标识）
        display_name: 一级标题文本
        description: 「适用场景」内容
        trigger_keywords: %% trigger 指令（可选，保留兼容）
        nodes: 节点列表
        edges: 边列表 [(source_id, target_id, condition_label), ...]
        branches: 可跳转的子图列表 [(filename, description), ...]
        guidance: 「分支指引」全文（markdown）
        raw_mermaid: 原始 Mermaid 代码块
        raw_text: 文件全文
    """

    name: str
    display_name: str = ""
    description: str = ""
    trigger_keywords: list = field(default_factory=list)
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list = field(default_factory=list)  # [(src, tgt, condition), ...]
    branches: list = field(default_factory=list)  # [(filename, description), ...]
    guidance: str = ""  # 「分支指引」全文
    raw_mermaid: str = ""  # 原始 Mermaid 代码块
    raw_text: str = ""  # 文件全文


# ==============================================================================
# Mermaid 解析器（增强）
# ==============================================================================


class MermaidFlowParser:
    """将 Mermaid 流程图解析为 FlowDefinition。

    支持从 Mermaid 字符串直接解析（parse），以及从完整 .md 文件解析（parse_file）。

    Mermaid 语法支持：
    - 方角节点 A[label] → action
    - 菱形节点 A{label} → decision
    - 简单边 A --> B
    - 条件边 A -->|条件| B
    - %% name: xxx — 图名称
    - %% description: xxx — 描述
    - %% trigger: a, b — 触发关键词
    - %% goto: filename.md -- 描述 — 子图引用
    - %% map A -> tool_name(key=val) — 显式工具映射
    """

    # 节点识别规则（关键词 → 工具名）
    _KEYWORD_TO_TOOL = [
        (["记录", "录入", "记录事件", "事件"], "record_event"),
        (["任务", "创建任务"], "record_task"),
        (["会议"], "record_meeting"),
        (["文件", "归档", "存档"], "record_file"),
        (["查询", "搜索", "列出", "统计", "汇总", "查找"], "query_data"),
        (["回复", "回复消息", "通知", "告知"], "chat_reply"),
        (["路由", "分类", "判断意图"], "route_message"),
    ]

    # 正则
    _RE_NODE_ACTION = re.compile(r'\b([A-Za-z0-9_]+)\[([^\]]+)\]')  # 方角节点
    _RE_NODE_DECISION = re.compile(r'\b([A-Za-z0-9_]+)\{([^}]+)\}')  # 菱形节点（新增）
    _RE_EDGE_COND = re.compile(r'([A-Za-z0-9_]+)\s*-+>\s*\|([^|]+)\|\s*([A-Za-z0-9_]+)')  # 条件边（新增）
    _RE_EDGE_SIMPLE = re.compile(r'([A-Za-z0-9_]+)\s*-+>\s*([A-Za-z0-9_]+)')  # 简单边（新增）
    _RE_GOTO = re.compile(r'%%\s*goto\s*:\s*(\S+\.md)\s*[-—]+\s*(.+)')  # 子图引用（新增）
    _RE_MAP_DIRECTIVE = re.compile(
        r'%%\s*map\s+([A-Za-z0-9_]+)\s*->\s*([a-z_]+)\s*\(([^)]*)\)', re.I
    )
    _RE_TRIGGER_DIRECTIVE = re.compile(r'%%\s*trigger\s*:\s*(.+)', re.I)
    _RE_NAME_DIRECTIVE = re.compile(r'%%\s*name\s*:\s*(.+)', re.I)
    _RE_DESCRIPTION_DIRECTIVE = re.compile(r'%%\s*description\s*:\s*(.+)', re.I)

    def parse(self, mermaid_text: str) -> FlowDefinition:
        """解析 Mermaid 文本为 FlowDefinition。

        Args:
            mermaid_text: Mermaid 源码（可带或不带 ```mermaid 标记）

        Returns:
            FlowDefinition: 解析后的决策图定义（含节点、边、goto 指令）
        """
        raw = self._strip_markers(mermaid_text)

        # 1. 解析显式指令
        direct_mapping = self._parse_map_directives(raw)
        trigger_keywords = self._parse_trigger_directive(raw)
        name = self._parse_name_directive(raw)
        description = self._parse_description_directive(raw)

        # 2. 解析节点 — 一次性扫描所有节点定义，保持原文出现顺序
        nodes: list[FlowNode] = []
        node_ids_seen: set[str] = set()

        # 合并两者为一个扫描: [(node_id, label, is_decision), ...]
        all_node_defs: list[tuple[str, str, bool]] = []
        for m in self._RE_NODE_ACTION.finditer(raw):
            pos = m.start()
            node_id = m.group(1).strip()
            label = m.group(2).strip()
            if node_id not in [n[0] for n in all_node_defs]:
                all_node_defs.append((node_id, label, False, pos))
        for m in self._RE_NODE_DECISION.finditer(raw):
            pos = m.start()
            node_id = m.group(1).strip()
            label = m.group(2).strip()
            if node_id not in [n[0] for n in all_node_defs]:
                all_node_defs.append((node_id, label, True, pos))

        # 按文本出现位置排序
        all_node_defs.sort(key=lambda x: x[3])

        for node_id, label, is_decision, _pos in all_node_defs:
            if node_id in node_ids_seen:
                continue
            node_ids_seen.add(node_id)

            if is_decision:
                nodes.append(FlowNode(
                    node_id=node_id,
                    label=label,
                    node_type="decision",
                ))
            elif node_id in direct_mapping:
                tool_name, params = direct_mapping[node_id]
                nodes.append(FlowNode(
                    node_id=node_id,
                    label=label,
                    node_type="action",
                    tool_name=tool_name,
                    params=params,
                ))
            else:
                tool_name = self._infer_tool(label)
                params = self._default_params_for_tool(tool_name, label)
                nodes.append(FlowNode(
                    node_id=node_id,
                    label=label,
                    node_type="action",
                    tool_name=tool_name,
                    params=params,
                ))

        # 3. 解析边（新增）
        edges: list[tuple] = []

        # 条件边: B -->|完整| C
        for src, cond, tgt in self._RE_EDGE_COND.findall(raw):
            edges.append((src.strip(), tgt.strip(), cond.strip()))

        # 收集已有边的节点对（避免简单边覆盖条件边）
        condition_edge_pairs: set[tuple[str, str]] = {
            (src, tgt) for src, tgt, _ in edges
        }

        # 简单边: A --> B（排除已有条件边的 src-tgt 对）
        for src, tgt in self._RE_EDGE_SIMPLE.findall(raw):
            pair = (src.strip(), tgt.strip())
            if pair not in condition_edge_pairs:
                edges.append((pair[0], pair[1], ""))

        # 4. 解析 goto 指令（新增）
        branches: list[tuple[str, str]] = []
        for m in self._RE_GOTO.finditer(raw):
            filename = m.group(1).strip()
            desc = m.group(2).strip()
            branches.append((filename, desc))

        # 5. 自动生成缺失的 name / description
        if not name and nodes:
            name = "flow_" + "_".join(n.node_id.lower() for n in nodes[:3])[:40] or "custom_flow"
        if not description:
            description = ""

        logger.info(
            "MermaidFlow parsed: name=%s, nodes=%d (action=%d, decision=%d), edges=%d, branches=%d",
            name, len(nodes),
            sum(1 for n in nodes if n.node_type == "action"),
            sum(1 for n in nodes if n.node_type == "decision"),
            len(edges), len(branches),
        )

        return FlowDefinition(
            name=name,
            display_name="",
            description=description,
            trigger_keywords=trigger_keywords,
            nodes=nodes,
            edges=edges,
            branches=branches,
            guidance="",
            raw_mermaid=raw,
            raw_text=raw,
        )

    def parse_file(self, file_path: str | Path) -> FlowDefinition:
        """解析一个完整的 .md 文件为 FlowDefinition。

        从文件中提取：
        - 一级标题 → display_name
        - 「适用场景」段落 → description
        - Mermaid 代码块 → parse()
        - 「分支指引」段落 → guidance
        - %% goto: 指令 → branches

        Args:
            file_path: .md 文件路径

        Returns:
            FlowDefinition: 完整解析结果
        """
        path = Path(file_path)
        raw_text = path.read_text(encoding="utf-8")

        name = path.stem  # 文件名（不含 .md）

        # 提取一级标题
        display_name = ""
        title_match = re.match(r'^#\s+(.+)', raw_text.strip())
        if title_match:
            display_name = title_match.group(1).strip()

        # 提取「适用场景」
        description = ""
        desc_match = re.search(
            r'##\s*适用场景\s*\n+(.+?)(?=\n##|\n```|\Z)',
            raw_text, re.DOTALL,
        )
        if desc_match:
            description = desc_match.group(1).strip()

        # 提取 Mermaid 代码块
        mermaid_block = ""
        mermaid_match = re.search(
            r'```mermaid\s*\n(.*?)```',
            raw_text, re.DOTALL,
        )
        if mermaid_match:
            mermaid_block = mermaid_match.group(1).strip()

        # 解析 Mermaid 块
        if mermaid_block:
            flow = self.parse(mermaid_block)
        else:
            flow = FlowDefinition(name=name)

        # 覆盖 name / display_name / description（文件级信息优先于注释指令）
        flow.name = name
        flow.display_name = display_name
        flow.description = description
        flow.raw_text = raw_text

        # 提取「分支指引」
        guidance_match = re.search(
            r'##\s*分支指引\s*\n(.+)',
            raw_text, re.DOTALL,
        )
        if guidance_match:
            flow.guidance = guidance_match.group(1).strip()

        # 提取 goto 指令（从原始文本中，parse() 已经做了但用 Mermaid 块）
        # 这里从全文再扫一次，确保文件级的 goto 也被捕获
        for m in self._RE_GOTO.finditer(raw_text):
            filename = m.group(1).strip()
            desc = m.group(2).strip()
            if (filename, desc) not in flow.branches:
                flow.branches.append((filename, desc))

        # 提取 trigger 指令
        trigger_match = self._RE_TRIGGER_DIRECTIVE.search(raw_text)
        if trigger_match and not flow.trigger_keywords:
            flow.trigger_keywords = [
                kw.strip()
                for kw in trigger_match.group(1).split(",")
                if kw.strip()
            ]

        logger.info(
            "MermaidFlow parse_file: %s → name=%s, nodes=%d, edges=%d, branches=%d, guidance=%d chars",
            path.name, flow.name, len(flow.nodes), len(flow.edges),
            len(flow.branches), len(flow.guidance),
        )

        return flow

    # -- 辅助方法 --

    @staticmethod
    def _strip_markers(text: str) -> str:
        """去除 ```mermaid / ``` 标记。"""
        lines = text.strip().splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def _parse_map_directives(self, text: str) -> dict:
        """解析 %% map A -> record_event(title="$input", project="X") 指令。

        Returns:
            {node_id: (tool_name, params_dict)}
        """
        result = {}
        for m in self._RE_MAP_DIRECTIVE.finditer(text):
            node_id = m.group(1)
            tool_name = m.group(2).strip().lower()
            args_text = m.group(3).strip()
            params = self._parse_params(args_text)
            result[node_id] = (tool_name, params)
            logger.debug("MermaidFlow: map %s -> %s(%s)", node_id, tool_name, params)
        return result

    @staticmethod
    def _parse_params(args_text: str) -> dict:
        """解析形如 "title=\"$input\", project=\"my-project\"" 的参数。"""
        params = {}
        if not args_text:
            return params

        parts = []
        current = ""
        for ch in args_text:
            if ch == "," and not current.count('"') % 2:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())

        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                params[key] = value
        return params

    def _parse_trigger_directive(self, text: str) -> list:
        m = self._RE_TRIGGER_DIRECTIVE.search(text)
        if not m:
            return []
        return [kw.strip() for kw in m.group(1).split(",") if kw.strip()]

    def _parse_name_directive(self, text: str) -> str:
        m = self._RE_NAME_DIRECTIVE.search(text)
        return m.group(1).strip() if m else ""

    def _parse_description_directive(self, text: str) -> str:
        m = self._RE_DESCRIPTION_DIRECTIVE.search(text)
        return m.group(1).strip() if m else ""

    def _infer_tool(self, label: str) -> str:
        """根据节点标签中的关键词推断工具名。"""
        for keywords, tool_name in self._KEYWORD_TO_TOOL:
            if any(kw in label for kw in keywords):
                return tool_name
        return "chat_reply"

    @staticmethod
    def _default_params_for_tool(tool_name: str, label: str) -> dict:
        """为不同工具生成默认参数模板。"""
        if tool_name == "record_event":
            return {
                "title": "$input",
                "event_type": "general",
                "description": label,
            }
        if tool_name == "record_task":
            return {
                "title": "$input",
                "description": label,
                "owner_text": "待分配",
            }
        if tool_name == "record_meeting":
            return {
                "title": "$input",
                "summary": "$prev.summary",
                "attendees": "未指定",
            }
        if tool_name == "record_file":
            return {
                "filename": "$input",
                "file_type": "general",
            }
        if tool_name == "query_data":
            return {
                "query_type": "events",
                "project_name": "",
                "keyword": "$input",
            }
        if tool_name == "chat_reply":
            return {"message": "$input"}
        if tool_name == "route_message":
            return {"message": "$input"}
        return {"input": "$input"}


# ==============================================================================
# NL2Flow: 自然语言 → Mermaid 图（LLM 辅助）
# ==============================================================================


class NL2Flow:
    """用 LLM 将自然语言描述转为 Mermaid 流程图。

    用法：
        nl2flow = NL2Flow(llm_client)
        mermaid_text = await nl2flow.generate("我想记录项目的日报流程...")
        flow_def = parser.parse(mermaid_text)

    注意：此功能需要 LLM client 可用。如果 llm_client 为 None，
    generate() 返回模板占位符。
    """

    _SYSTEM_PROMPT = """你是 Mermaid 流程图生成专家。根据用户的自然语言业务流程描述，
生成一个符合以下模板的 Mermaid graph TD 流程图。

## 文件模板要求

生成的 .md 文件必须包含：

```markdown
# <流程名称，如：事件录入决策流程>

## 适用场景
<一句话描述什么情况下使用此决策图>

## 决策图

```mermaid
graph TD
    A[收到事件汇报] --> B{汇报内容是否完整?}
    B -->|信息完整| C[展示拟录入单 · 事件]
    B -->|缺少字段| D[追问缺失信息]
    C --> E[用户确认后调用 record_event]
    D --> F{追问后用户是否补充?}
    F -->|已补充| C
    F -->|未回复| G[保持待确认状态]
```

## 分支指引

### 完整 → 拟录入单
- 按 MasterAgent 内置「拟录入单流程」组装
- 展示后等待用户确认/修改/放弃

### 缺少字段 → 追问
- 优先追问关键字段
- 最多追问两轮

### 子流程引用
%% goto: example_confirm.md   — 用户确认后的处理
%% goto: example_cancel.md    — 用户取消后的处理
```

## 格式规则

1. **Mermaid 图**: 使用 graph TD 方向。菱形 {} 表示决策节点，方角 [] 表示动作节点。
2. **分支指引**: 必需。文字描述每个分支的处理逻辑。
3. **适用场景**: 必需。帮助 LLM 判断是否应查阅此文件。
4. **%% goto: 指令**: 声明可跳转的子图。格式 %% goto: <文件名>.md  — <简短说明>。

只输出完整的 .md 文件内容，不要额外说明。"""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def generate(self, natural_language_desc: str) -> str:
        """用 LLM 从自然语言生成 .md 文件内容。

        Args:
            natural_language_desc: 用户描述的业务流程

        Returns:
            完整的 .md 文件内容（包含 Mermaid 代码块、分支指引等）
        """
        if self.llm is None:
            logger.warning("NL2Flow: no LLM client, returning placeholder template")
            return self._placeholder_template(natural_language_desc[:40])

        messages = [
            {"role": "system", "content": self._SYSTEM_PROMPT},
            {"role": "user", "content": natural_language_desc},
        ]
        try:
            response = await self.llm.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=2000,
            )
            result = response.get("content", "") if isinstance(response, dict) else str(response)
            logger.info("NL2Flow: generated %d chars", len(result))
            return result
        except Exception as e:
            logger.error("NL2Flow LLM call failed: %s", e)
            return self._placeholder_template(natural_language_desc[:40])

    @staticmethod
    def _placeholder_template(topic: str) -> str:
        """无 LLM 时生成一个占位模板。"""
        safe_name = re.sub(r'[^\w]+', '_', topic).strip('_')[:30] or "custom_flow"
        return (
            f"# 自定义流程\n\n"
            f"## 适用场景\n{topic}\n\n"
            f"## 决策图\n\n"
            f"```mermaid\n"
            f"graph TD\n"
            f"    A[接收请求] --> B{{判断需求}}\n"
            f"    B -->|已知场景| C[按现有流程处理]\n"
            f"    B -->|新场景| D[原子工具自由推理]\n"
            f"    C --> E[回复用户]\n"
            f"    D --> E\n"
            f"```\n\n"
            f"## 分支指引\n\n"
            f"### 已知场景\n查阅对应子图或按既有规则处理。\n\n"
            f"### 新场景\n无对应子图时，用原子工具自由推理兜底。\n\n"
            f"### 子流程引用\n"
            f"%% goto: main.md -- 返回根图\n"
        )
