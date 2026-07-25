"""ocr_document 工具 —— VLM 视觉大模型 OCR。

参考需求/silicon-ocr。M14 handler 风格，注册到 BusinessFlowToolRegistry。
"""

from __future__ import annotations
import logging
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..infrastructure.vlm.client import VlmOcrClient

logger = logging.getLogger("emily.tool.ocr")

_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "图片路径（jpg/png/bmp/tiff）或 PDF 文件"},
        "prompt": {"type": "string", "description": "可选，定制识别要求"},
    },
    "required": ["file_path"],
}

_OCR_DESCRIPTION = (
    "对图片或 PDF 做 OCR 识别，返回 Markdown 格式文本（表格用 | 分隔，保留标题层级）。"
    "适用：施工图纸、扫描件、规范文档图片。支持多页并发。"
)


async def handle_ocr_document(params: dict, vlm_client: "VlmOcrClient") -> dict:
    """M14 handler：VLM OCR。

    Args:
        params: {file_path, prompt?}
        vlm_client: VlmOcrClient 实例（由 registry 注入）
    Returns:
        {success, text/markdown, pages?, model, elapsed_ms}
    """
    started = time.monotonic()
    file_path = params.get("file_path", "")
    prompt = params.get("prompt")

    if not file_path or not os.path.exists(file_path):
        return {"success": False, "error": f"file not found: {file_path}"}

    ext = os.path.splitext(file_path)[1].lower()

    # ── PDF：先转图片再 OCR ──
    if ext == ".pdf":
        return await _ocr_pdf(file_path, prompt, vlm_client, started)

    # ── 图片：直接 OCR ──
    result = await vlm_client.ocr(file_path, prompt=prompt)
    total_ms = int((time.monotonic() - started) * 1000)
    if result["success"]:
        return {
            "success": True,
            "markdown": result["text"],
            "model": result.get("model", ""),
            "page_count": 1,
            "elapsed_ms": total_ms,
        }
    return {
        "success": False,
        "error": result.get("error", "OCR failed"),
        "model": result.get("model", ""),
        "elapsed_ms": total_ms,
    }


async def _ocr_pdf(file_path: str, prompt: str | None,
                   vlm_client: "VlmOcrClient", started: float) -> dict:
    """PDF 逐页转图片 → OCR。"""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return {"success": False,
                "error": "pdf2image not installed (pip install pdf2image; apt install poppler-utils)"}

    try:
        images = convert_from_path(file_path, dpi=200)
    except Exception as e:
        return {"success": False, "error": f"PDF conversion failed: {e}"}

    if not images:
        return {"success": False, "error": "PDF has no pages"}

    # 逐页保存临时图片
    import tempfile
    temp_dir = tempfile.mkdtemp(prefix="ocr_pdf_")
    image_paths = []
    try:
        for i, img in enumerate(images):
            tmp_path = os.path.join(temp_dir, f"page_{i + 1:04d}.png")
            img.save(tmp_path, "PNG")
            image_paths.append(tmp_path)

        # 并发 OCR
        results = await vlm_client.ocr_batch(image_paths)
    finally:
        # 清理临时文件
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    # 聚合结果
    pages = []
    all_text_parts = []
    for i, r in enumerate(results):
        page_no = i + 1
        if r["success"]:
            pages.append({"page_no": page_no, "text": r["text"]})
            all_text_parts.append(r["text"])
        else:
            pages.append({"page_no": page_no, "text": "",
                          "error": r.get("error", "")})

    total_ms = int((time.monotonic() - started) * 1000)
    success_count = sum(1 for p in pages if not p.get("error"))
    return {
        "success": success_count > 0,
        "markdown": "\n\n".join(all_text_parts),
        "pages": pages,
        "page_count": len(pages),
        "success_pages": success_count,
        "model": results[0].get("model", "") if results else "",
        "elapsed_ms": total_ms,
    }
