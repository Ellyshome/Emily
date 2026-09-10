"""脚本执行控制台 API —— ScriptManager 的 HTTP 通道。

挂在 18080 业务端口（127.0.0.1 绑定 + AuthMiddleware 覆盖），不挂 18081 监控端口
——后者对局域网开放且无鉴权，暴露脚本执行等同于开放任意远程执行。

安全约束（三层）：
  1. 白名单参数：只接受 scripts_registry.yaml 中声明过的参数（见 params.build_cli_args）
  2. 写库两段式：writes_db=true 的脚本，未显式 confirm 时强制降级为 check_arg 预览
  3. 无 schema 不可调：未声明 params 的脚本一律拒绝，避免裸 args 通道绕过前两层
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("emily.api.scripts")

router = APIRouter(prefix="/scripts", tags=["scripts"])


def _get_manager():
    """取 ScriptManager 实例；未就绪返回 None。"""
    from api.server import get_core
    core = get_core()
    core._ensure_initialized()
    return getattr(core, "_script_manager", None)


def _err(message: str, code: int = 1) -> dict:
    return {"code": code, "message": message, "data": None}


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


# ══════════════════════════════════════════════════════════════════════════════
# 请求体
# ══════════════════════════════════════════════════════════════════════════════

class RunRequest(BaseModel):
    """表单执行请求。"""
    values: dict = Field(default_factory=dict, description="{参数名: 值}，须在 schema 内")
    confirm_write: bool = Field(default=False, description="写库脚本的二次确认")


# ══════════════════════════════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/list")
async def list_scripts():
    """列出全部脚本（含 has_params 标记，前端据此区分可否表单调用）。"""
    sm = _get_manager()
    if sm is None:
        return _err("ScriptManager 未初始化")
    scripts = await asyncio.to_thread(sm.list)
    return _ok({"scripts": scripts, "count": len(scripts)})


@router.get("/schema/{name}")
async def get_schema(name: str):
    """取单个脚本的表单渲染 schema。"""
    sm = _get_manager()
    if sm is None:
        return _err("ScriptManager 未初始化")
    result = await asyncio.to_thread(sm.form_schema, name)
    if "error" in result:
        return _err(result["error"], code=result.get("code", 2))
    return _ok(result)


@router.post("/run/{name}")
async def run_script(name: str, req: RunRequest):
    """按 schema 执行脚本。

    writes_db 且 confirm_write=false 时不会真跑，只回 check_arg 预览结果，
    响应中 forced_preview=true 供前端提示"这只是预览"。
    """
    sm = _get_manager()
    if sm is None:
        return _err("ScriptManager 未初始化")

    schema = await asyncio.to_thread(sm.form_schema, name)
    if "error" in schema:
        return _err(schema["error"], code=schema.get("code", 2))
    if not schema.get("params"):
        return _err(f"脚本 '{name}' 未声明参数 schema，暂不支持 Web 调用")

    logger.info("scripts.run name=%s confirm_write=%s keys=%s",
                name, req.confirm_write, sorted(req.values.keys()))

    result = await asyncio.to_thread(
        sm.run_with_params, name, req.values, req.confirm_write
    )
    # 执行失败也回 code=0——失败详情在 data 里，前端统一渲染 stdout/stderr
    return _ok(result)
