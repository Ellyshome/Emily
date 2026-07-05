"""fetch_rag_info —— 获取 RAG 知识库可用性信息。

被 SessionDataFetcher._sub_fetch_rag_info() 调用。
也可独立运行：python -m emily_core.session.fetchers.fetch_rag_info

检查 RAG 提供者是否可用，返回可用性状态和知识库名称列表。
"""

from __future__ import annotations

import json
import logging
import argparse
from typing import Any

logger = logging.getLogger("emily.session.fetchers.fetch_rag_info")


def fetch(core: Any = None) -> dict:
    """获取 RAG 知识库可用性信息。

    Args:
        core: EmilyCore 实例（可选，用于获取 rag_provider）

    Returns:
        {"available": True, "collections": ["default", "project_docs"]}
    """
    try:
        if core is None:
            return {"available": False, "collections": []}

        rag_provider = getattr(core, "_rag_provider", None)
        if rag_provider is None:
            return {"available": False, "collections": []}

        # 同步检查可用性
        is_available_fn = getattr(rag_provider, "is_available", None)
        if is_available_fn is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                available = True
            except RuntimeError:
                available = asyncio.run(is_available_fn())
        else:
            available = True

        collections = getattr(rag_provider, "collections", [])
        return {
            "available": available,
            "collections": list(collections) if collections else [],
        }
    except Exception as e:
        logger.error("fetch_rag_info failed: %s", e)
        return {"available": False, "collections": []}


def main():
    """独立运行入口。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="获取 RAG 知识库可用性信息")
    parser.add_argument("--check", action="store_true", help="实际调用 RAG 检查可用性")
    args = parser.parse_args()

    if args.check:
        print("需要 EmilyCore 实例，请在 emily-core 容器内运行或使用 emy-test 工具")
        return

    # 不带 core 时只能报告默认值
    result = fetch(core=None)
    result["note"] = "无 EmilyCore 实例，available 为默认值 False"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
