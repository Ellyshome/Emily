"""emy-config 配置清单 API —— 只读汇总「代码默认值 · 宿主机 .env 声明 · 容器生效值」。

定位：与 emy-console 并列的**观察窗口**（emy-console 管运行时操作与观测，
emy-config 管配置的声明面与生效面）。本模块**只读**：

  · 不提供任何写配置文件的接口；
  · 不自动重启容器、不做配置热重载（改完按返回的 restart_hint 人工执行）；
  · 密钥类字段一律掩码，接口返回体中不出现明文。

实现落在 `emily_core/services/config_inventory.py`（本层只做协议适配）。

接口：
  GET /api/v1/config/inventory       配置总清单（字段三方对照 / 文件状态 / 差异告警）
  GET /api/v1/config/file?name=...   单个配置文件内容（文件名白名单限定）
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query

logger = logging.getLogger("emily.api.config")

router = APIRouter(prefix="/config", tags=["config"])


def _err(message: str, code: int = 1) -> dict:
    return {"code": code, "message": message, "data": None}


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


def _live_config():
    """取容器内生效的 Config 实例（未初始化时回退代码默认值）。"""
    from api.server import get_core
    from emily_core.config import Config

    try:
        core = get_core()
    except RuntimeError:
        logger.warning("config inventory: EmilyCore not initialized, falling back to defaults")
        return Config()
    return getattr(core, "config", None) or Config()


@router.get("/inventory")
async def inventory():
    """配置总清单（纯内存 + 少量文件读取；源码扫描结果进程内缓存）。"""
    from emily_core.services.config_inventory import build_inventory

    try:
        config = _live_config()
        data = await asyncio.to_thread(build_inventory, config)
        return _ok(data)
    except Exception as ex:  # noqa: BLE001
        logger.exception("config.inventory failed")
        return _err(f"生成配置清单失败：{ex}")


@router.get("/file")
async def config_file(name: str = Query(..., description="已登记的配置文件名（取值见清单 files[].name）")):
    """读取单个配置文件内容（只读、白名单、密钥掩码）。"""
    from emily_core.services.config_inventory import read_config_file

    try:
        return _ok(await asyncio.to_thread(read_config_file, name))
    except KeyError as ex:
        return _err(str(ex))
    except Exception as ex:  # noqa: BLE001
        logger.exception("config.file failed")
        return _err(f"读取配置文件失败：{ex}")
