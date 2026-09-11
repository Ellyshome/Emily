"""脚本执行控制台 API —— ScriptManager 的 HTTP 通道。

挂在 18080 业务端口（127.0.0.1 绑定 + AuthMiddleware 覆盖），不挂 18081 监控端口
——后者对局域网开放且无鉴权，暴露脚本执行等同于开放任意远程执行。

安全约束（三层）：
  1. 白名单参数：声明了 params 的脚本，只接受 scripts_registry.yaml 中声明过的参数
     （见 params.build_cli_args）
  2. 写库两段式：writes_db=true 的脚本，未显式 confirm 时强制降级为 check_arg 预览
  3. 无 schema 不传参：未声明 params 的脚本不接受任何自定义参数，只按注册表 run_args
     声明的默认参数执行，避免裸 args 通道绕过第 1、2 层
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
    subcommand: str | None = Field(default=None, description="带子命令脚本选中的动作名")


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
    """执行脚本。

    两类通道：
      - 声明了 params：按 schema 白名单拼 CLI 参数
      - 未声明 params：不接受自定义参数，按注册表 run_args 默认参数执行

    writes_db 且 confirm_write=false 时不会真跑，只回 check_arg 预览结果，
    响应中 forced_preview=true 供前端提示"这只是预览"。
    """
    sm = _get_manager()
    if sm is None:
        return _err("ScriptManager 未初始化")

    schema = await asyncio.to_thread(sm.form_schema, name)
    if "error" in schema:
        return _err(schema["error"], code=schema.get("code", 2))

    values = req.values or {}
    if not schema.get("params") and not schema.get("subcommands"):
        # 无 schema 通道：拒绝任何自定义参数，只按注册表默认参数执行
        if values:
            return _err(f"脚本 '{name}' 未声明参数 schema，不接受自定义参数")
        logger.info("scripts.run name=%s confirm_write=%s (no schema, default args)",
                    name, req.confirm_write)
        result = await asyncio.to_thread(
            sm.run_with_defaults, name, req.confirm_write
        )
    else:
        logger.info("scripts.run name=%s subcommand=%s confirm_write=%s keys=%s",
                    name, req.subcommand, req.confirm_write, sorted(values.keys()))
        result = await asyncio.to_thread(
            sm.run_with_params, name, values, req.confirm_write, req.subcommand
        )
    # 执行失败也回 code=0——失败详情在 data 里，前端统一渲染 stdout/stderr
    return _ok(result)


# ══════════════════════════════════════════════════════════════════════════════
# 动态候选值 + 环境信息
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/options/{source}")
async def list_param_options(source: str, q: str = ""):
    """某类动态参数的候选值（用户 / 项目 / 节点，取自真实运行环境）。"""
    from emily_core.scripts.options import list_options

    try:
        options = await asyncio.to_thread(list_options, source, q)
    except ValueError as ex:
        return _err(str(ex))
    except Exception as ex:
        logger.warning("scripts.options failed source=%s: %s", source, ex)
        return _err(f"读取候选值失败：{ex}")
    return _ok({"source": source, "options": options, "count": len(options)})


@router.get("/env")
async def get_env():
    """控制台环境信息：容器清单 / 数据库来源 / 实体计数 / Core 状态。"""
    from emily_core.scripts.env_probe import collect_env

    core = None
    try:
        from api.server import get_core
        core = get_core()
    except Exception:
        logger.info("scripts.env: EmilyCore 未就绪，仅返回基础设施信息")

    env = await asyncio.to_thread(collect_env, core)
    return _ok(env)
