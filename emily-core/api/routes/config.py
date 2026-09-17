"""emy-config 配置中心 API —— 只读配置清单 + 字段值写回 .env（PRD §5.6）。

接口契约与 emy-console 一致，统一返回 {code, message, data}：
  · GET  /api/v1/config/inventory        —— 配置总清单（三方对照 + 差异告警）
  · GET  /api/v1/config/file?name=<登记名> —— 查看单个配置文件内容（只读）
  · POST /api/v1/config/save             —— 把字段值写回宿主机 .env

边界：
  · 写盘面单一（C-07 收窄后）：只允许写宿主机 .env —— Emily 运行时唯一认的通路。
    配置文件内容接口只读；config.py 是源码，页面不改。
  · 密钥（C-08）：返回值不含明文密钥（掩码逻辑见 emily_core/config_inventory.py）。
  · 白名单（AC-US-05.2）：文件内容接口只接受登记名单内的文件名，天然拒绝路径穿越。
  · 拦截：无环境变量入口、未被 compose 注入、类型不符的项一律拒写并说明原因，
    避免在页面上制造「改了不生效」。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("emily.api.config")

router = APIRouter(prefix="/config", tags=["config"])


class SaveItem(BaseModel):
    field: str = Field(..., description="Config 字段名")
    value: str = Field(..., description="目标值（字符串；后端按类型集校验）")


class SaveRequest(BaseModel):
    items: list[SaveItem] = Field(..., description="待写入的字段值列表")


def _err(message: str, code: int = 1) -> dict:
    return {"code": code, "message": message, "data": None}


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


@router.get("/inventory")
async def get_inventory():
    """配置总清单：runtime / sections / fields / files / findings / restart_hint。"""
    from api.server import get_core
    from emily_core.config_inventory import build_inventory

    core = get_core()
    config = getattr(core, "config", None)
    if config is None:
        return _err("EmilyCore 配置实例不可用")

    try:
        data = await asyncio.to_thread(build_inventory, config)
    except Exception as e:  # 清单构建失败给出可读提示，不抛 500
        logger.exception("build config inventory failed")
        return _err(f"构建配置清单失败：{e}")
    return _ok(data)


@router.get("/file")
async def get_config_file(name: str = Query(..., description="配置文件登记名")):
    """查看单个配置文件内容（只读）；仅限登记名单。"""
    from emily_core.config_inventory import read_config_file

    try:
        data, error = await asyncio.to_thread(read_config_file, name)
    except Exception as e:
        logger.exception("read config file failed: %s", name)
        return _err(f"读取配置文件失败：{e}")

    if data is None:
        return _err(error or "不允许读取该文件")
    if error:
        data["error"] = error
    return _ok(data)


@router.post("/save")
async def save_config(body: SaveRequest):
    """把字段值写回宿主机 .env（只覆写声明行，不改其它内容）。

    保存本身不重启容器：环境变量在容器创建时固化，需自行 docker compose up -d emily-core。
    """
    from emily_core.config_inventory import save_env_values

    if not body.items:
        return _err("没有待保存的项")

    updates = [{"field": it.field, "value": it.value} for it in body.items]
    try:
        data = await asyncio.to_thread(save_env_values, updates)
    except Exception as e:
        logger.exception("save env values failed")
        return _err(f"写入 .env 失败：{e}")
    return _ok(data)
