"""VLM 视觉大模型客户端 —— 用于 OCR（参考需求/silicon-ocr/main.py）。

通过 OpenAI 兼容的 chat/completions API 调用视觉大模型，把图片转成 Markdown 文本。
支持 SiliconFlow Qwen3-VL / 百度千帆 qianfan-ocr-fast 等，通过配置切换。
"""

from __future__ import annotations
import asyncio
import base64
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger("emily.vlm")

# Emily 工程文档通用 OCR prompt（基于 silicon-ocr/main.py:69-79 改写）
_DEFAULT_OCR_PROMPT = (
    "请识别并原样抄写图片中所有文字，要求如下：\n"
    "1. 不要用代码块（```）包裹输出内容；\n"
    "2. 按照从上到下、从左到右的顺序输出；\n"
    "3. 不论文字位于何处（正文、标题、表格、图例、页眉页脚等），一律原样收录；\n"
    "4. 表格内容按行输出，单元格之间用 | 分隔；\n"
    "5. 保留标题层级（用 # 标记）、列表（用 - 标记）；\n"
    "6. 不对内容做归类、总结或结构调整，忠实抄写原文。"
)

_SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif", ".webp"}


class VlmOcrClient:
    """VLM 视觉大模型 OCR 客户端。

    通过 OpenAI 兼容 API 调用视觉大模型做 OCR。
    """

    def __init__(self, api_url: str, api_key: str, model: str,
                 timeout: int = 300, max_tokens: int = 4096):
        self._api_url = api_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens

    # ── 公开 API ──────────────────────────────────────

    async def ocr(self, image_path: str, prompt: str | None = None) -> dict:
        """对单张图片做 OCR。

        Args:
            image_path: 图片文件路径（jpg/png/bmp/tiff/gif/webp）。
            prompt: 可选，定制 OCR 提示词。

        Returns:
            {"success": bool, "text": str, "model": str, "elapsed_ms": int, "error": str?}
        """
        started = time.monotonic()
        try:
            img_b64 = self._image_to_base64(image_path)
            text = await self._call_api(img_b64, prompt)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return {"success": True, "text": text, "model": self._model,
                    "elapsed_ms": elapsed_ms}
        except Exception as e:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.warning("VLM OCR failed: %s (%s)", image_path, e)
            return {"success": False, "text": "", "model": self._model,
                    "elapsed_ms": elapsed_ms, "error": str(e)}

    async def ocr_batch(self, image_paths: list[str],
                        concurrency: int = 6) -> list[dict]:
        """并发 OCR 多张图片。

        Args:
            image_paths: 图片文件路径列表。
            concurrency: 最大并发数。

        Returns:
            按输入顺序的结果列表，每个元素同 ocr() 返回值。
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def _ocr_one(path: str) -> dict:
            async with semaphore:
                return await self.ocr(path)

        tasks = [_ocr_one(p) for p in image_paths]
        return list(await asyncio.gather(*tasks))

    # ── 内部 ──────────────────────────────────────────

    @staticmethod
    def _image_to_base64(image_path: str) -> str:
        """读取图片并转为 base64 字符串。"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    async def _call_api(self, img_b64: str, prompt: str | None = None) -> str:
        """调用 VLM API 做 OCR。"""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or _DEFAULT_OCR_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
            "max_tokens": self._max_tokens,
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self._api_url, json=payload,
                                        headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(
                            f"VLM API returned {resp.status}: {body[:500]}")
                    data = await resp.json()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"VLM API request failed: {e}") from e

        if "choices" not in data or not data["choices"]:
            raise RuntimeError(f"VLM API unexpected response: {str(data)[:500]}")

        return data["choices"][0]["message"]["content"]
