"""Docker Engine API 客户端（通过 Unix Socket 查询容器状态）。"""

import asyncio
import logging
import os
import re
import struct
from datetime import datetime, timezone, timedelta

import aiohttp

logger = logging.getLogger(__name__)

# 需要监控的容器名（与 docker-compose 中 container_name 一致）
MONITORED_CONTAINERS = [
    "napcat",
    "astrbot",
    "emily-core",
    "maxkb",   # 已废弃，保留占位避免索引错位
    "tei",     # TEI embedding 服务
    "emily-postgres",
]

# 容器对外展示名称（不暴露底层工具名）
CONTAINER_DISPLAY_NAMES = {
    "napcat": "通讯接口",
    "astrbot": "消息平台",
    "emily-core": "业务内核",
    "maxkb": "知识库（已废弃）",
    "tei": "TEI embedding",
    "emily-postgres": "数据库",
}

# IM 账号占位（需求 V2 §4.1）
IM_ACCOUNTS = [
    {"platform": "qq", "label": "QQ", "status": "active", "webui_url": "/napcat-webui"},
    {"platform": "wechat", "label": "微信", "status": "no_account"},
    {"platform": "dingtalk", "label": "钉钉", "status": "no_account"},
    {"platform": "feishu", "label": "飞书", "status": "no_account"},
]


def _docker_socket_available() -> bool:
    """检查 Docker Unix Socket 是否可用。"""
    return os.path.exists("/var/run/docker.sock")


async def get_container_status() -> list[dict]:
    """通过 Docker Engine API 获取受监控容器的运行状态。

    Returns:
        [
            {
                "name": "napcat",
                "status": "running" | "stopped",
                "image": "mlikiowa/napcat-docker:latest",
            },
            ...
        ]
    """
    if not _docker_socket_available():
        logger.debug("Docker socket not available — returning all containers as unknown")
        return [
            {"name": name, "display_name": CONTAINER_DISPLAY_NAMES.get(name, name), "status": "unknown", "image": ""}
            for name in MONITORED_CONTAINERS
        ]

    try:
        connector = aiohttp.UnixConnector("/var/run/docker.sock")
        async with aiohttp.ClientSession(connector=connector) as client:
            async with client.get(
                "http://localhost/containers/json?all=true"
            ) as resp:
                if resp.status != 200:
                    logger.warning("Docker API returned status %d", resp.status)
                    return _fallback_status()
                containers = await resp.json()

        # 构建 name → info 映射
        container_map = {}
        for c in containers:
            names = c.get("Names", [])
            name = names[0].lstrip("/") if names else ""
            container_map[name] = {
                "name": name,
                "display_name": CONTAINER_DISPLAY_NAMES.get(name, name),
                "status": "running" if c.get("State") == "running" else "stopped",
                "image": c.get("Image", ""),
            }

        # 按监控列表顺序返回
        result = []
        for name in MONITORED_CONTAINERS:
            if name in container_map:
                result.append(container_map[name])
            else:
                result.append({"name": name, "display_name": CONTAINER_DISPLAY_NAMES.get(name, name), "status": "stopped", "image": ""})
        return result

    except Exception as e:
        logger.warning("Docker API call failed: %s", e)
        return _fallback_status()


def _fallback_status() -> list[dict]:
    """Docker API 不可用时的降级返回。"""
    return [
        {"name": name, "display_name": CONTAINER_DISPLAY_NAMES.get(name, name), "status": "unknown", "image": ""}
        for name in MONITORED_CONTAINERS
    ]


async def _get_napcat_container_id() -> str | None:
    """获取 napcat 容器的 Docker ID。"""
    if not _docker_socket_available():
        return None
    try:
        connector = aiohttp.UnixConnector("/var/run/docker.sock")
        async with aiohttp.ClientSession(connector=connector) as client:
            async with client.get(
                "http://localhost/containers/json?all=true"
            ) as resp:
                if resp.status != 200:
                    return None
                containers = await resp.json()
                for c in containers:
                    names = c.get("Names", [])
                    for n in names:
                        if n.lstrip("/") == "napcat":
                            return c.get("Id", "")
    except Exception as e:
        logger.debug("fetch napcat container id failed: %s", e, exc_info=True)
        return None
    return None


async def _fetch_napcat_webui_token() -> str | None:
    """从 NapCat 容器内 webui.json 读取真实 token。

    优先通过 Docker API exec 读取 /app/napcat/config/webui.json，
    回退到从日志中解析 WebUi Token 行。
    """
    if not _docker_socket_available():
        return None
    try:
        container_id = await _get_napcat_container_id()
        if not container_id:
            return None
        connector = aiohttp.UnixConnector("/var/run/docker.sock")
        async with aiohttp.ClientSession(connector=connector) as client:
            # 创建 exec 实例
            async with client.post(
                f"http://localhost/containers/{container_id}/exec",
                json={
                    "Cmd": ["cat", "/app/napcat/config/webui.json"],
                    "AttachStdout": True,
                    "AttachStderr": True,
                },
            ) as resp:
                if resp.status not in (200, 201):
                    return None
                exec_id = (await resp.json())["Id"]
            # 启动 exec
            async with client.post(
                f"http://localhost/exec/{exec_id}/start",
                json={"Detach": False, "Tty": False},
            ) as resp:
                if resp.status != 200:
                    return None
                raw = await resp.read()
                # Docker multiplex 格式: 8字节头 + payload
                text = ""
                pos = 0
                while pos + 8 <= len(raw):
                    size = struct.unpack(">I", raw[pos + 4 : pos + 8])[0]
                    pos += 8
                    if pos + size > len(raw):
                        break
                    text += raw[pos : pos + size].decode("utf-8", errors="replace")
                    pos += size
                import json as _json
                data = _json.loads(text)
                return data.get("token")
    except Exception as e:
        logger.debug("Failed to fetch napcat webui token: %s", e)
        return None


async def _fetch_napcat_logs(container_id: str, tail: int = 200) -> str:
    """通过 Docker Engine API 获取 napcat 容器日志。"""
    try:
        connector = aiohttp.UnixConnector("/var/run/docker.sock")
        async with aiohttp.ClientSession(connector=connector) as client:
            url = f"http://localhost/containers/{container_id}/logs?stdout=true&stderr=true&tail={tail}&timestamps=false"
            async with client.get(url) as resp:
                if resp.status != 200:
                    return ""
                raw = await resp.read()
                # Docker 日志格式: 8字节头 + payload
                # header: [stream(1)][0x00][0x00][0x00][size_big_endian(4)]
                text_parts = []
                pos = 0
                while pos + 8 <= len(raw):
                    # stream_type = raw[pos]  # 1=stdout, 2=stderr
                    size = struct.unpack(">I", raw[pos + 4 : pos + 8])[0]
                    pos += 8
                    if pos + size > len(raw):
                        break
                    text_parts.append(raw[pos : pos + size].decode("utf-8", errors="replace"))
                    pos += size
                return "\n".join(text_parts)
    except Exception as e:
        logger.debug("Failed to fetch napcat logs: %s", e)
        return ""


# 正则：从日志中提取 QQ 信息（匹配 [Name(Number)] 格式，Name 仅含英文/数字/下划线）
_QQ_INFO_RE = re.compile(r"\[(\w+)\((\d{5,15})\)\]")


def _parse_qq_info_from_logs(logs: str) -> dict:
    """从 NapCat 日志中解析 QQ 登录信息。

    Returns:
        {"logged_in": bool, "qq_number": str, "qq_nickname": str, "is_recent": bool}
    """
    if not logs:
        return {"logged_in": False, "qq_number": "", "qq_nickname": "", "is_recent": False}

    matches = _QQ_INFO_RE.findall(logs)

    if not matches:
        return {"logged_in": False, "qq_number": "", "qq_nickname": "", "is_recent": False}

    # 按 QQ 号统计频率
    qq_counts = {}
    qq_names = {}
    for name, qq in matches:
        qq_counts[qq] = qq_counts.get(qq, 0) + 1
        if qq not in qq_names:
            qq_names[qq] = name
    qq_number = max(qq_counts, key=qq_counts.get)
    qq_nickname = qq_names.get(qq_number, "")

    # 检查最近 30 分钟是否有活动（日志格式 "MM-DD HH:MM:SS"）
    now = datetime.now()
    recent = False
    for line in logs.splitlines():
        m = re.match(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                ts_str = f"{now.year}-{m.group(1)}"
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                if (now - ts) < timedelta(minutes=30):
                    recent = True
                    break
            except ValueError:
                pass

    return {
        "logged_in": True,
        "qq_number": qq_number,
        "qq_nickname": qq_nickname,
        "is_recent": recent,
    }


async def get_im_accounts() -> list[dict]:
    """返回 IM 账号状态列表。

    当前仅 QQ 已实现，其余预留"无账号、无连接"占位。
    QQ 状态: 通过解析 NapCat 容器日志判断是否实际有 QQ 号登录。
    NapCat WebUI 链接由前端根据当前页面 hostname + token 动态拼接。
    """
    containers = await get_container_status()
    napcat_running = any(
        c["name"] == "napcat" and c["status"] == "running"
        for c in containers
    )

    # 动态从 NapCat 容器内读取真实 webui token（不依赖环境变量）
    webui_token = await _fetch_napcat_webui_token()
    if not webui_token:
        # 回退到环境变量（兼容旧配置）
        webui_token = os.environ.get("NAPCAT_WEBUI_TOKEN", "")

    # 通过日志解析 QQ 登录状态
    qq_logged_in = False
    qq_number = ""
    qq_nickname = ""
    is_recent = False
    if napcat_running:
        container_id = await _get_napcat_container_id()
        if container_id:
            logs = await _fetch_napcat_logs(container_id, tail=200)
            qq_info = _parse_qq_info_from_logs(logs)
            qq_logged_in = qq_info["logged_in"]
            qq_number = qq_info["qq_number"]
            qq_nickname = qq_info["qq_nickname"]
            is_recent = qq_info["is_recent"]

    accounts = []
    for acc in IM_ACCOUNTS:
        entry = {"platform": acc["platform"], "label": acc["label"]}
        if acc["platform"] == "qq":
            if qq_logged_in:
                entry["status"] = "connected"
            elif napcat_running:
                entry["status"] = "disconnected"  # 容器在跑但没登录
            else:
                entry["status"] = "disconnected"
            entry["webui_available"] = napcat_running
            entry["webui_token"] = webui_token
            entry["qq_number"] = qq_number
            entry["qq_nickname"] = qq_nickname
        else:
            entry["status"] = acc["status"]
            entry["webui_available"] = False
            entry["webui_token"] = ""
            entry["qq_number"] = ""
            entry["qq_nickname"] = ""
        accounts.append(entry)
    return accounts
