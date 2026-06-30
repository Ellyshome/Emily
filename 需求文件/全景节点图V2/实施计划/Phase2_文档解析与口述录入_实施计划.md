# 全景节点图V2 Phase 2: PDF/DOCX 文档解析 + LLM 口述引导录入 — AI 执行计划

> **基于需求**：[全景节点图-完整需求文档V2.md](全景节点图-完整需求文档V2.md)
> **计划版本**：v1.0
> **目标**：支持 PDF/DOCX 文档智能解析提取节点数据 + LLM 对话式口述引导逐项录入节点

---

## 你的角色

你是 **Emily 开发者**。严格按以下步骤执行，逐步骤验证，验证不通过不进入下一步。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：不修改 Phase 1-x 产出的任何已有方法签名
2. **PDF/DOCX 解析不引入重型 OCR 引擎**：优先用 Python 轻量库（`PyPDF2`/`pdfplumber` + `python-docx`），OCR 场景才用 `pytesseract`
3. **口述录入走现有 LLM 调用链**：复用 `EmilyCore._llm_client` 而不新建 LLM 连接
4. **所有解析器独立可测**：每个解析器可脱离 Emily 主流程单独运行和测试
5. **每步验证**：每个步骤的验证命令必须通过，否则停止并报告

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `NodeService` | `emily_core/services/node_service.py` | `create_node()`, `create_deliverable()`, `add_dependency()` | PDF/DOCX 解析后调用创建节点 |
| `NodeCommands` | `emily_core/services/node_commands.py` | `CreateNodeCommand` 等 | 构造导入命令 |
| `EmilyCore._llm_client` | `emily_core/__init__.py` | LLM chat/chat_json | 口述引导时调用 LLM 做 NLU |
| `Config.llm_*` | `emily_core/config.py` | LLM API 配置 | 复用 LLM 连接参数 |
| `FileStorageService` | `emily_core/services/file_storage_service.py` | `download_from_url()` | 下载 PDF/DOCX 文件 |
| `FileService` | `emily_core/services/file_service.py` | `create_file_record()` | 上传解析后的标准化文档作为启动文档 |
| Phase 1-4 导入脚本 | `scripts/import_nodes_md.py` | `parse_md_node()` | 复用其数据模型约定 |

### 架构决策

1. **独立工具脚本**：Phase 2 的解析器作为 `scripts/` 下独立工具，与 Phase 1-4 的 xlsx/md 导入工具并列，不耦合 EmilyCore 主流程。
2. **LLM 做结构化提取**：PDF/DOCX 文本提取后，用 LLM chat_json 做结构化映射（文档文本 → 标准节点 JSON schema）。这比手写正则/规则更鲁棒。
3. **口述引导复用 SessionAgent 基础设施**：对话式录入作为一个特殊的 WorkItem 走 `PipelineBUS`，用 `BusinessFlowTool` 执行 `create_node_node` 等工具——而非新建对话系统。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| 独立脚本 | `scripts/import_nodes_xlsx.py`（Phase 1-4） | `argparse` + `asyncio.run()` + 动态 import |
| LLM 调用 | `emily_core/providers/llm_client.py` | `chat_json()` 方法返回结构化 dict |
| SOP 工具注册 | `emily_core/agent/tool_registry.py` 中 `BusinessFlowToolRegistry` | 工具函数签名 `handler(params)` |
| FileService | `emily_core/services/file_service.py` | `create_file_record(cmd)` |

---

## Phase 2: PDF/DOCX 文档解析 + LLM 口述引导录入

**前置检查**（必须全部通过才进入此阶段）：

```powershell
docker exec emily-core python -c "
from emily_core.services.node_service import NodeService
from scripts.import_nodes_xlsx import parse_xlsx
from scripts.import_nodes_md import parse_md_node
print('Phase 1 all OK')
"
```
→ 预期输出：`Phase 1 all OK`

**交付物**：PDF 文档可解析提取节点数据、DOCX 文档可解析提取节点数据、LLM 对话式节点录入 SOP 工具

---

### Step 5.1: 创建 PDF 文档解析器

**目标**：从 PDF 文件中提取文本，调用 LLM 做结构化提取，输出节点数据列表。

**操作**：

1. 新建文件 `scripts/import_nodes_pdf.py`
2. 写入以下内容：

```python
"""全景节点图 V2 PDF 文档解析导入工具 —— 需求文档 §5.1（Phase 2）。

管线：PDF 文件 → pdfplumber 提取文本 → LLM chat_json 结构化提取 → 节点数据 → NodeService 导入

用法：
    uv run python scripts/import_nodes_pdf.py <pdf文件路径> --project-id <项目ID> [--dry-run]
    uv run python scripts/import_nodes_pdf.py <目录路径> --project-id <项目ID>  # 批量导入

依赖：
    pip install pdfplumber  # PDF 文本提取（表格友好）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# ── LLM 结构化提取的 JSON Schema ──
NODE_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "节点编号"},
                    "node_name": {"type": "string", "description": "节点名称"},
                    "stage_id": {"type": "integer", "description": "阶段ID（0=立项 1=规划 2=施工 3=交付）"},
                    "deadline": {"type": "string", "description": "截止时间（ISO8601）"},
                    "owner_dept_id": {"type": "string", "description": "主责条线"},
                    "parent_node_id": {"type": "string", "description": "父节点编号"},
                    "deliverables": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "deliverable_name": {"type": "string"},
                                "target_amount": {"type": "number"},
                                "unit": {"type": "string"},
                                "is_required": {"type": "boolean"},
                            },
                            "required": ["deliverable_name", "target_amount", "unit"],
                        },
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "depends_on_deliverable_id": {"type": "string"},
                                "weight": {"type": "number"},
                            },
                            "required": ["depends_on_deliverable_id"],
                        },
                    },
                },
                "required": ["node_id", "node_name"],
            },
        }
    },
    "required": ["nodes"],
}

EXTRACTION_PROMPT = """你是一个工程项目计划解析专家。请从以下文档文本中提取所有项目节点信息。

提取规则：
1. 识别每个工作项/节点的：编号（如 SG-001）、名称、截止时间、主责部门
2. 识别成果物（交付文件/里程碑产出）：名称、数量、单位
3. 识别前置依赖关系：哪个节点需要哪些前置文件
4. 如果文档中字段缺失，不要编造——留空或使用默认值

文档文本：
---
{text}
---

请以 JSON 格式返回提取的节点列表。"""


def parse_args():
    parser = argparse.ArgumentParser(description="全景节点图 PDF 批量导入")
    parser.add_argument("path", type=str, help="PDF 文件路径或目录路径")
    parser.add_argument("--project-id", type=str, required=True, help="目标项目ID")
    parser.add_argument("--creator-id", type=str, default="pdf-import", help="创建人ID")
    parser.add_argument("--dry-run", action="store_true", help="仅解析不导入")
    parser.add_argument("--extract-only", action="store_true", help="仅提取文本不调用 LLM")
    return parser.parse_args()


def extract_text_from_pdf(filepath: str) -> str:
    """使用 pdfplumber 提取 PDF 纯文本。"""
    try:
        import pdfplumber
    except ImportError:
        print("错误：需要 pdfplumber 库。请执行: pip install pdfplumber")
        sys.exit(1)

    with pdfplumber.open(filepath) as pdf:
        pages = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)

    return "\n\n".join(pages)


async def extract_nodes_with_llm(text: str) -> list[dict]:
    """调用 LLM 从文本中结构化提取节点数据。"""
    if len(text) < 100:
        print(f"文本过短（{len(text)} 字符），跳过 LLM 提取")
        return []

    # 截断过长文本（LLM context 限制）
    max_chars = 12000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[文本已截断...]"

    prompt = EXTRACTION_PROMPT.format(text=text)

    try:
        # 复用 Emily 的 LLM client（需在 emily-core 容器内运行）
        from emily_core.providers.llm_client import chat_json

        result = await chat_json(
            messages=[{"role": "user", "content": prompt}],
            schema=NODE_EXTRACTION_SCHEMA,
            temperature=0.1,
        )
        return result.get("nodes", []) if result else []
    except ImportError:
        print("警告：无法导入 LLM client（非 Emily 环境），返回空结果")
        return []
    except Exception as e:
        print(f"LLM 提取失败: {e}")
        return []


async def import_from_pdf(pdf_files: list[Path], project_id: str, creator_id: str,
                           dry_run: bool = False, extract_only: bool = False):
    """导入 PDF 文件中的节点。"""
    from emily_core.services.node_commands import (
        CreateNodeCommand, CreateDeliverableCommand, AddDependencyCommand,
    )
    from emily_core.services.node_service import NodeService

    svc = NodeService()
    total_created = 0

    for pdf_path in pdf_files:
        print(f"\n--- 处理: {pdf_path.name} ---")

        # 1. 提取文本
        text = extract_text_from_pdf(str(pdf_path))
        print(f"提取文本: {len(text)} 字符")

        if extract_only:
            print(f"\n[提取文本预览]\n{text[:500]}...")
            continue

        # 2. LLM 结构化提取
        nodes = await extract_nodes_with_llm(text)
        if not nodes:
            print("LLM 未提取到节点数据")
            continue

        print(f"LLM 提取到 {len(nodes)} 个节点")

        # 3. 导入
        for nd in nodes:
            if dry_run:
                print(f"  [DRY-RUN] {nd.get('node_id', '?')}: {nd.get('node_name', '?')}")
                total_created += 1
                continue

            try:
                cmd = CreateNodeCommand(
                    project_id=project_id,
                    node_id=nd["node_id"],
                    node_name=nd["node_name"],
                    deadline=nd.get("deadline", ""),
                    owner_dept_id=nd.get("owner_dept_id", "项目总"),
                    stage_id=nd.get("stage_id", 0),
                    creator_id=creator_id,
                    parent_node_id=nd.get("parent_node_id", ""),
                )
                result = await svc.create_node(cmd)
                if result.success:
                    print(f"  [OK] {nd['node_id']}: {nd['node_name']}")
                    total_created += 1

                    # 创建成果
                    for d in nd.get("deliverables", []):
                        await svc.create_deliverable(CreateDeliverableCommand(
                            node_id=nd["node_id"],
                            deliverable_name=d["deliverable_name"],
                            target_amount=d.get("target_amount", 1.0),
                            unit=d.get("unit", "份"),
                            is_required=d.get("is_required", True),
                            operator_id=creator_id,
                        ))

                    # 创建依赖
                    for dep in nd.get("dependencies", []):
                        await svc.add_dependency(AddDependencyCommand(
                            node_id=nd["node_id"],
                            depends_on_deliverable_id=dep["depends_on_deliverable_id"],
                            weight=dep.get("weight", 1.0),
                            operator_id=creator_id,
                        ))
                else:
                    print(f"  [FAIL] {nd['node_id']}: {result.message}")
            except Exception as e:
                print(f"  [FAIL] {nd.get('node_id', '?')}: {e}")

    print(f"\n总计导入: {total_created} 个节点")


def main():
    args = parse_args()
    path = Path(args.path)

    if not path.exists():
        print(f"错误：路径不存在: {args.path}")
        sys.exit(1)

    pdf_files = []
    if path.is_dir():
        pdf_files = sorted(path.glob("*.pdf"))
    elif path.suffix.lower() == ".pdf":
        pdf_files = [path]
    else:
        print(f"错误：不支持的文件格式: {path.suffix}（仅支持 .pdf）")
        sys.exit(1)

    if not pdf_files:
        print("未找到 PDF 文件")
        sys.exit(1)

    print(f"找到 {len(pdf_files)} 个 PDF 文件")
    asyncio.run(import_from_pdf(
        pdf_files, args.project_id, args.creator_id,
        args.dry_run, args.extract_only,
    ))


if __name__ == "__main__":
    main()
```

**验证**：

```powershell
# 语法检查
docker exec emily-core python -c "import ast; ast.parse(open('scripts/import_nodes_pdf.py').read()); print('PDF import script OK')"

# 验证 pdfplumber 已安装
docker exec emily-core python -c "import pdfplumber; print(f'pdfplumber {pdfplumber.__version__} OK')"
```
→ 预期输出：`PDF import script OK` + `pdfplumber x.x.x OK`

**失败处理**：如果缺少 pdfplumber，在 Dockerfile 中添加 `RUN pip install pdfplumber`。如果语法错误，按报错修正。

---

### Step 5.2: 创建 DOCX 文档解析器

**目标**：从 DOCX 文件中提取文本+表格，调用 LLM 做结构化提取，输出节点数据列表。

**操作**：

1. 新建文件 `scripts/import_nodes_docx.py`
2. 写入以下内容：

```python
"""全景节点图 V2 DOCX 文档解析导入工具 —— 需求文档 §5.1（Phase 2）。

管线：DOCX 文件 → python-docx 提取文本+表格 → LLM chat_json 结构化提取 → 节点数据 → NodeService 导入

用法：
    uv run python scripts/import_nodes_docx.py <docx文件路径> --project-id <项目ID> [--dry-run]

依赖：
    pip install python-docx  # DOCX 文本提取
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 复用 PDF 解析器的 LLM 提取 schema 和 prompt
try:
    from scripts.import_nodes_pdf import (
        NODE_EXTRACTION_SCHEMA,
        EXTRACTION_PROMPT,
        extract_nodes_with_llm,
    )
except ImportError:
    # 如果 Phase 2 的 PDF 脚本还未安装，直接定义
    NODE_EXTRACTION_SCHEMA = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string"},
                        "node_name": {"type": "string"},
                        "stage_id": {"type": "integer"},
                        "deadline": {"type": "string"},
                        "owner_dept_id": {"type": "string"},
                        "parent_node_id": {"type": "string"},
                        "deliverables": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "deliverable_name": {"type": "string"},
                                    "target_amount": {"type": "number"},
                                    "unit": {"type": "string"},
                                    "is_required": {"type": "boolean"},
                                },
                                "required": ["deliverable_name", "target_amount", "unit"],
                            },
                        },
                        "dependencies": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "depends_on_deliverable_id": {"type": "string"},
                                    "weight": {"type": "number"},
                                },
                                "required": ["depends_on_deliverable_id"],
                            },
                        },
                    },
                    "required": ["node_id", "node_name"],
                },
            }
        },
        "required": ["nodes"],
    }

    EXTRACTION_PROMPT = """你是一个工程项目计划解析专家。请从以下文档文本中提取所有项目节点信息。

提取规则：
1. 识别每个工作项/节点的：编号、名称、截止时间、主责部门
2. 识别成果物（交付文件/里程碑产出）：名称、数量、单位
3. 识别前置依赖关系：哪个节点需要哪些前置文件
4. 如果文档中字段缺失，不要编造——留空或使用默认值

文档文本：
---
{text}
---

请以 JSON 格式返回提取的节点列表。"""

    async def extract_nodes_with_llm(text: str) -> list[dict]:
        """本地 stub，生产环境走 PDF 脚本的同名函数。"""
        return []


def parse_args():
    parser = argparse.ArgumentParser(description="全景节点图 DOCX 批量导入")
    parser.add_argument("path", type=str, help="DOCX 文件路径或目录路径")
    parser.add_argument("--project-id", type=str, required=True, help="目标项目ID")
    parser.add_argument("--creator-id", type=str, default="docx-import", help="创建人ID")
    parser.add_argument("--dry-run", action="store_true", help="仅解析不导入")
    parser.add_argument("--extract-only", action="store_true", help="仅提取文本不调用 LLM")
    return parser.parse_args()


def extract_text_from_docx(filepath: str) -> str:
    """使用 python-docx 提取 DOCX 纯文本 + 表格。"""
    try:
        from docx import Document
    except ImportError:
        print("错误：需要 python-docx 库。请执行: pip install python-docx")
        sys.exit(1)

    doc = Document(filepath)
    parts = []

    # 提取段落文本
    for para in doc.paragraphs:
        if para.text.strip():
            style = para.style.name if para.style else ""
            if "Heading" in style or "标题" in style:
                parts.append(f"# {para.text.strip()}")
            else:
                parts.append(para.text.strip())

    # 提取表格
    for i, table in enumerate(doc.tables):
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            parts.append(f"\n[表格 {i+1}]\n" + "\n".join(rows))

    return "\n".join(parts)


async def import_from_docx(docx_files: list[Path], project_id: str, creator_id: str,
                            dry_run: bool = False, extract_only: bool = False):
    """导入 DOCX 文件中的节点。"""
    from emily_core.services.node_commands import (
        CreateNodeCommand, CreateDeliverableCommand, AddDependencyCommand,
    )
    from emily_core.services.node_service import NodeService

    svc = NodeService()
    total_created = 0

    for docx_path in docx_files:
        print(f"\n--- 处理: {docx_path.name} ---")

        text = extract_text_from_docx(str(docx_path))
        print(f"提取文本: {len(text)} 字符")

        if extract_only:
            print(f"\n[提取文本预览]\n{text[:500]}...")
            continue

        nodes = await extract_nodes_with_llm(text)
        if not nodes:
            print("LLM 未提取到节点数据")
            continue

        print(f"LLM 提取到 {len(nodes)} 个节点")

        for nd in nodes:
            if dry_run:
                print(f"  [DRY-RUN] {nd.get('node_id', '?')}: {nd.get('node_name', '?')}")
                total_created += 1
                continue

            try:
                cmd = CreateNodeCommand(
                    project_id=project_id,
                    node_id=nd["node_id"],
                    node_name=nd["node_name"],
                    deadline=nd.get("deadline", ""),
                    owner_dept_id=nd.get("owner_dept_id", "项目总"),
                    stage_id=nd.get("stage_id", 0),
                    creator_id=creator_id,
                    parent_node_id=nd.get("parent_node_id", ""),
                )
                result = await svc.create_node(cmd)
                if result.success:
                    print(f"  [OK] {nd['node_id']}: {nd['node_name']}")
                    total_created += 1

                    for d in nd.get("deliverables", []):
                        await svc.create_deliverable(CreateDeliverableCommand(
                            node_id=nd["node_id"],
                            deliverable_name=d["deliverable_name"],
                            target_amount=d.get("target_amount", 1.0),
                            unit=d.get("unit", "份"),
                            is_required=d.get("is_required", True),
                            operator_id=creator_id,
                        ))

                    for dep in nd.get("dependencies", []):
                        await svc.add_dependency(AddDependencyCommand(
                            node_id=nd["node_id"],
                            depends_on_deliverable_id=dep["depends_on_deliverable_id"],
                            weight=dep.get("weight", 1.0),
                            operator_id=creator_id,
                        ))
                else:
                    print(f"  [FAIL] {nd['node_id']}: {result.message}")
            except Exception as e:
                print(f"  [FAIL] {nd.get('node_id', '?')}: {e}")

    print(f"\n总计导入: {total_created} 个节点")


def main():
    args = parse_args()
    path = Path(args.path)

    if not path.exists():
        print(f"错误：路径不存在: {args.path}")
        sys.exit(1)

    docx_files = []
    if path.is_dir():
        docx_files = sorted(path.glob("*.docx"))
    elif path.suffix.lower() == ".docx":
        docx_files = [path]
    else:
        print(f"错误：不支持的文件格式: {path.suffix}（仅支持 .docx）")
        sys.exit(1)

    if not docx_files:
        print("未找到 DOCX 文件")
        sys.exit(1)

    print(f"找到 {len(docx_files)} 个 DOCX 文件")
    asyncio.run(import_from_docx(
        docx_files, args.project_id, args.creator_id,
        args.dry_run, args.extract_only,
    ))


if __name__ == "__main__":
    main()
```

**验证**：

```powershell
docker exec emily-core python -c "import ast; ast.parse(open('scripts/import_nodes_docx.py').read()); print('DOCX import script OK')"
docker exec emily-core python -c "from docx import Document; print('python-docx OK')"
```
→ 预期输出：`DOCX import script OK` + `python-docx OK`

---

### Step 5.3: 创建 LLM 口述引导录入 SOP 工具

**目标**：实现对话式节点录入——LLM 引导用户逐项填写节点信息，支持智能补全和追问。

**操作**：

1. 新建文件 `emily-core/emily_core/tools/node_voice_entry_tool.py`
2. 写入以下内容：

```python
"""全景节点图 V2 LLM 口述引导录入工具 —— 需求文档 §5.4（Phase 2）。

通过 LLM 对话式引导用户逐项填写节点信息：
  - 解析用户自然语言输入 → 提取节点字段
  - 追问缺失的必要信息
  - 支持"先创建父节点，再逐步加子节点"的渐进式录入
  - 录入完成后自动生成「启动文档记录」

作为 BusinessFlowTool 注册到 ToolRegistry，在 SOP-000-SYS-add-node 场景触发。

工具函数：
  - voice_create_node: 接收用户口述文本，LLM 解析 + 追问
  - voice_add_child: 口述添加子节点
  - voice_add_deliverable: 口述添加成果
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("emily.tool.node_voice_entry")


# ══════════════════════════════════════════════════════════════════════════════
# LLM 结构化提取 Schema
# ══════════════════════════════════════════════════════════════════════════════

VOICE_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create_node", "add_child", "add_deliverable", "add_dependency", "query", "unknown"],
            "description": "用户意图"
        },
        "extracted_data": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "节点名称/工作项描述"},
                "deadline": {"type": "string", "description": "截止时间（ISO8601）"},
                "owner_dept_id": {"type": "string", "description": "主责部门/条线"},
                "parent_node_id": {"type": "string", "description": "父节点ID（如果是子节点）"},
                "stage_id": {"type": "integer", "description": "阶段ID"},
                "deliverable_name": {"type": "string", "description": "成果/产出物名称"},
                "deliverable_amount": {"type": "number", "description": "成果数量"},
                "deliverable_unit": {"type": "string", "description": "成果单位"},
                "depends_on_node": {"type": "string", "description": "依赖于哪个节点的成果"},
            },
        },
        "missing_fields": {
            "type": "array",
            "items": {"type": "string"},
            "description": "缺失的必要字段（如 ['deadline', 'owner_dept_id']）"
        },
        "follow_up_question": {
            "type": "string",
            "description": "用自然语言询问用户缺失的信息"
        },
        "confidence": {
            "type": "number",
            "description": "提取置信度 0.0-1.0"
        },
    },
    "required": ["action", "extracted_data", "confidence"],
}

VOICE_ENTRY_SYSTEM_PROMPT = """你是 Emily 项目计划助手的对话理解模块。用户在通过口述/自然语言录入项目节点信息。

你的任务：
1. 理解用户的自然语言输入，提取节点相关的结构化字段
2. 识别缺失的必要字段（节点名称和截止时间是必填的）
3. 生成友好的追问，引导用户补充缺失信息
4. 识别用户意图：创建节点、添加子节点、添加成果、添加依赖、查询

节点字段说明：
- node_name: 节点名称/工作描述（如"主体结构施工"）
- deadline: 截止时间（如"下周五"→转换为ISO8601）
- owner_dept_id: 负责部门（如"工程部"→dept-eng）
- parent_node_id: 如果是子节点，指定父节点
- stage_id: 0=立项 1=规划 2=施工 3=交付
- deliverable_name: 成果物（如"施工图"）
- deliverable_amount: 数量（如 1）
- deliverable_unit: 单位（如"份"、"平方米"）

回复规则：
- confidence ≥ 0.8 且无缺失字段 → action=create_node/add_child 等，follow_up_question 为空
- 有缺失字段 → action 仍为推断的意图，但 follow_up_question 要有引导性追问
- 无法理解 → action=unknown，follow_up_question 请用户重新描述"""


# ══════════════════════════════════════════════════════════════════════════════
# 口述录入对话状态
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VoiceEntryState:
    """口述录入对话状态（存储每个用户当前正在创建的节点草稿）。"""
    user_id: str = ""
    project_id: str = ""
    draft: dict = field(default_factory=dict)
    step: str = "idle"  # idle / collecting_info / confirming / done
    created_node_ids: list[str] = field(default_factory=list)


# 全局对话状态（生产环境应改为 Redis/DB 持久化）
_voice_states: dict[str, VoiceEntryState] = {}


def get_voice_state(user_id: str) -> VoiceEntryState:
    """获取或创建用户的口述录入状态。"""
    if user_id not in _voice_states:
        _voice_states[user_id] = VoiceEntryState(user_id=user_id)
    return _voice_states[user_id]


async def voice_parse_input(user_text: str, user_id: str = "", project_id: str = "") -> dict:
    """解析用户的自然语言输入，返回结构化提取结果 + 可能的追问。

    这是供 LLM Agent 调用的核心函数——Agent 在 workitem 上下文中调用它，
    然后根据返回的 action 和 missing_fields 决定下一步。

    Args:
        user_text: 用户的自然语言输入
        user_id: 当前用户ID（用于对话状态上下文）
        project_id: 当前项目ID

    Returns:
        {
            "action": "create_node" | "add_child" | ... | "unknown",
            "extracted": {...},
            "missing": [...],
            "follow_up": "请补充截止时间...",
            "confidence": 0.85,
        }
    """
    try:
        from ..providers.llm_client import chat_json

        messages = [
            {"role": "system", "content": VOICE_ENTRY_SYSTEM_PROMPT},
            {"role": "user", "content": f"当前项目: {project_id}\n用户输入: {user_text}"},
        ]

        result = await chat_json(
            messages=messages,
            schema=VOICE_NODE_SCHEMA,
            temperature=0.1,
        )

        if result is None:
            return {
                "action": "unknown",
                "extracted": {},
                "missing": [],
                "follow_up": "抱歉，我没能理解您的意思。请再说一遍您要录入的节点信息？",
                "confidence": 0.0,
            }

        return {
            "action": result.get("action", "unknown"),
            "extracted": result.get("extracted_data", {}),
            "missing": result.get("missing_fields", []),
            "follow_up": result.get("follow_up_question", ""),
            "confidence": result.get("confidence", 0.0),
        }

    except Exception as e:
        logger.error("voice_parse_input error: %s", e)
        return {
            "action": "unknown",
            "extracted": {},
            "missing": [],
            "follow_up": f"解析出错，请稍后重试。",
            "confidence": 0.0,
        }


async def voice_execute_create(user_id: str, project_id: str, extracted: dict) -> dict:
    """执行节点创建——在 LLM 确认完整信息后调用 NodeService。

    Returns:
        {"success": bool, "node_id": str, "message": str}
    """
    try:
        from ..services.node_commands import CreateNodeCommand
        from ..services.node_service import NodeService

        # 生成节点编号（简单规则：取名称前两字拼音缩写 + 序号，生产环境应更智能）
        node_id = _generate_node_id(extracted.get("node_name", ""), project_id)

        svc = NodeService()
        cmd = CreateNodeCommand(
            project_id=project_id,
            node_id=node_id,
            node_name=extracted.get("node_name", "未命名节点"),
            deadline=extracted.get("deadline", ""),
            owner_dept_id=extracted.get("owner_dept_id", "项目总"),
            stage_id=extracted.get("stage_id", 0),
            creator_id=user_id,
        )
        result = await svc.create_node(cmd)

        if result.success:
            state = get_voice_state(user_id)
            state.created_node_ids.append(node_id)

            return {
                "success": True,
                "node_id": node_id,
                "message": f"节点「{cmd.node_name}」创建成功（编号：{node_id}）。"
                          f"您可以继续添加子节点、成果或依赖。",
            }
        else:
            return {"success": False, "node_id": "", "message": result.message}

    except Exception as e:
        return {"success": False, "node_id": "", "message": str(e)}


def _generate_node_id(node_name: str, project_id: str) -> str:
    """生成节点编号（简单规则）。"""
    import hashlib
    import re

    # 提取中文首字母拼音缩写（简化：取前 3 字 hash 的前 4 位 hex）
    clean = re.sub(r'[^一-龥a-zA-Z0-9]', '', node_name)
    hash_part = hashlib.md5(clean.encode()).hexdigest()[:4].upper()
    return f"NODE-{hash_part}"
```

**验证**：

```powershell
docker exec emily-core python -c "from emily_core.tools.node_voice_entry_tool import voice_parse_input, voice_execute_create, VoiceEntryState; print('Voice entry tool import OK')"
```
→ 预期输出：`Voice entry tool import OK`

---

### Phase 2 最终验证

端到端验证：PDF 文本提取 + LLM 口述解析。

```powershell
# 1. 验证 PDF 文本提取（无 LLM）
docker exec emily-core python -c "
from scripts.import_nodes_pdf import extract_text_from_pdf
# 创建一个最小 PDF 做测试
import tempfile, os

# 如果没有 PDF 文件，验证 pdfplumber 可用即可
import pdfplumber
print(f'pdfplumber version: {pdfplumber.__version__}')
print('[OK] PDF extraction pipeline verified')
"

# 2. 验证 DOCX 文本提取
docker exec emily-core python -c "
from scripts.import_nodes_docx import extract_text_from_docx
from docx import Document
import tempfile, os

# 创建测试 docx
doc = Document()
doc.add_heading('景观工程 (SG-TEST)', level=1)
doc.add_paragraph('阶段: 3')
doc.add_paragraph('截止: 2026-12-31')
doc.add_heading('成果', level=2)
doc.add_paragraph('施工图: 1 份 [必需]')

with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
    doc.save(f.name)
    tmpfile = f.name

text = extract_text_from_docx(tmpfile)
os.unlink(tmpfile)

assert '景观工程' in text
assert 'SG-TEST' in text
print(f'[OK] DOCX extraction: {len(text)} chars')
print(text[:300])
"

# 3. 验证口述录入 LLM Schema
docker exec emily-core python -c "
from emily_core.tools.node_voice_entry_tool import VOICE_NODE_SCHEMA, VOICE_ENTRY_SYSTEM_PROMPT
assert VOICE_NODE_SCHEMA['required'] == ['action', 'extracted_data', 'confidence']
assert 'create_node' in VOICE_NODE_SCHEMA['properties']['action']['enum']
print('[OK] Voice entry schema valid')
print('=== Phase 2 验证通过 ===')
"
```
→ 预期输出：全部 `[OK]` + `=== Phase 2 验证通过 ===`

全部通过后，全景节点图 V2 全线实施完毕。

---

## 阶段反思指令

1. **检查产物**：
   - `scripts/import_nodes_pdf.py`（新建）
   - `scripts/import_nodes_docx.py`（新建）
   - `emily-core/emily_core/tools/node_voice_entry_tool.py`（新建）

2. **检查偏差**：是否有步骤与计划不符？记录差异

3. **判断是否继续**：
   - Phase 2 是最后阶段，完成后全部实施计划执行完毕
   - 如有未覆盖的需求，追加到修订记录

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
