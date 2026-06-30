#!/usr/bin/env python3
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
        # 复用 Emily 的 LLM client
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
