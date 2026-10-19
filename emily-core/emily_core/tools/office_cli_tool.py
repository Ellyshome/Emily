"""officeCLI 工具 —— Office 文件读取（Word/Excel/PPT）。

底层调用 iOfficeAI/OfficeCLI 单文件二进制：
  officecli view <file> <mode> --json

SOP-015-QRY（苗木表审核）用它读取用户提交的 xlsx/docx/pptx。
text 模式对 xlsx 返回逐单元格结构（{sheets:[{name, rows:[{row, cells}]}]}），
适合后续做完整性、比例、密度等逐项审核。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("emily.tool.officecli")

_DEFAULT_BIN = "/usr/local/bin/officecli"
_SUPPORTED_SUFFIXES = {".docx", ".xlsx", ".pptx"}
_VALID_MODES = {"text", "outline", "stats", "issues"}
_READ_MIN_LEVEL = 3
_MAX_LINES_DEFAULT = 1000
_MAX_LINES_CAP = 5000
_TIMEOUT_SECONDS = 120
_MAX_OUTPUT_CHARS = 120_000

_OFFICE_CLI_SCHEMA = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "Office 文件路径（.docx / .xlsx / .pptx）",
        },
        "mode": {
            "type": "string",
            "enum": ["text", "outline", "stats", "issues"],
            "description": "读取模式，默认 text；text 返回内容/单元格，outline/stats/issues 返回结构或检查结果",
        },
        "range": {
            "type": "string",
            "description": "可选，xlsx 单元格范围，如 Sheet1!A1:I200；用于大表分段读取",
        },
        "max_lines": {
            "type": "integer",
            "description": "可选，text 模式最大行数/段落数，默认 1000，最大 5000",
        },
    },
    "required": ["file_path"],
}

_OFFICE_CLI_DESCRIPTION = (
    "用 officeCLI 读取 Word/Excel/PPT 文件，返回结构化 JSON。"
    "xlsx 的 text 模式返回逐单元格数据（sheet / row / A1 坐标），适合苗木表等表格审核；"
    "默认最多 1000 行，大表先用 outline 看总行数，再用 range 分段读取。"
)


def _resolve_bin() -> str | None:
    """定位 officecli 可执行文件：环境变量 > PATH > 镜像默认路径。"""
    configured = (os.environ.get("EMILY_OFFICECLI_BIN") or "").strip()
    if configured:
        return configured if os.path.isfile(configured) else None
    found = shutil.which("officecli")
    if found:
        return found
    return _DEFAULT_BIN if os.path.isfile(_DEFAULT_BIN) else None


async def handle_office_cli(params: dict, user_id: str = "") -> dict:
    """读取 Office 文件并返回结构化内容。

    Args:
        params: {file_path, mode?, range?, max_lines?}
        user_id: 会话用户 ID（框架注入；用于 L3 门禁）
    Returns:
        {success, mode, file_name, data, engine, elapsed_ms}
    """
    from .file_tool import check_min_level

    denied = check_min_level(user_id, _READ_MIN_LEVEL, "苗木表审核")
    if denied is not None:
        return denied

    started = time.monotonic()
    file_path = str(params.get("file_path") or "").strip()
    mode = str(params.get("mode") or "text").strip().lower()

    if not file_path:
        return {"success": False, "error": "缺少 file_path", "error_code": "missing_file_path"}
    if mode not in _VALID_MODES:
        return {"success": False, "error": f"不支持的 mode: {mode}",
                "error_code": "invalid_mode"}

    path = Path(file_path).expanduser()
    if not path.is_file():
        return {"success": False, "error": f"文件不存在: {file_path}",
                "error_code": "file_not_found"}
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        return {
            "success": False,
            "error": f"officeCLI 仅支持 {', '.join(sorted(_SUPPORTED_SUFFIXES))}；"
                     f"PDF/图片请使用 ocr_document",
            "error_code": "unsupported_file_type",
        }

    bin_path = _resolve_bin()
    if not bin_path:
        return {
            "success": False,
            "error": "officeCLI 未安装或不可执行；请检查 emily-core 镜像构建和工具注册",
            "error_code": "tool_unavailable",
        }

    cmd = [bin_path, "view", str(path), mode, "--json"]
    if mode == "text":
        raw_max = params.get("max_lines", _MAX_LINES_DEFAULT)
        try:
            max_lines = int(raw_max)
        except (TypeError, ValueError):
            max_lines = _MAX_LINES_DEFAULT
        max_lines = max(1, min(max_lines, _MAX_LINES_CAP))
        cmd += ["--max-lines", str(max_lines)]

        range_value = str(params.get("range") or "").strip()
        if range_value:
            cmd += ["--range", range_value]

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"officeCLI 不可执行: {bin_path}",
            "error_code": "tool_unavailable",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"officeCLI 读取超时（>{_TIMEOUT_SECONDS}s），请拆分文件或缩小 range",
            "error_code": "timeout",
        }

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if proc.returncode != 0:
        detail = (stderr or stdout).strip()
        logger.warning("officeCLI exit=%s file=%s detail=%s",
                       proc.returncode, path.name, detail[:300])
        return {
            "success": False,
            "error": detail or f"officeCLI 退出码 {proc.returncode}",
            "error_code": "officecli_failed",
            "exit_code": proc.returncode,
        }

    if len(stdout) > _MAX_OUTPUT_CHARS:
        return {
            "success": False,
            "error": f"officeCLI 输出过大（{len(stdout)} 字符），请用 range 缩小工作表范围"
                     f"或降低 max_lines 后重试",
            "error_code": "output_too_large",
        }

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        text = stdout.strip()
        return {
            "success": True,
            "mode": mode,
            "file_name": path.name,
            "text": text,
            "engine": "officeCLI",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    if isinstance(payload, dict) and payload.get("success") is False:
        return {
            "success": False,
            "error": payload.get("error") or "officeCLI 返回 success=false",
            "error_code": "officecli_failed",
            "detail": payload,
        }

    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return {
        "success": True,
        "mode": mode,
        "file_name": path.name,
        "data": data,
        "engine": "officeCLI",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
