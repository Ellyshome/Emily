"""接入渠道状态服务 —— emy-console「模块能力 → 渠道连通性」。

渠道清单与连通判定：

  渠道        实现方式                              连通判定
  QQ          NapCat 容器 + QQ 登录态              容器 running 且日志中解析出已登录 QQ 号
  企业微信    AstrBot wecom 适配器                  容器 running 且适配器 enable 且日志无凭证错误
  微信小程序  独立 wechat-gateway（依赖域名）         EMILY_WXMP_DOMAIN 已配置且 <域名>/health 可达
  邮箱        进程内 SMTP 发信 + IMAP 收信           IMAP 登录成功 且 收到自发「系统启动完成」邮件

每个渠道额外返回 `details`（键值对，供前端详情框架展示），QQ 另带 `qrcode` 字段
（未登录时给出扫码二维码，便于在控制台直接完成登录）。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from ..providers.email.base import EmailCredentials
from ..providers.email.imap_provider import IMAPEmailProvider
from ..infrastructure.docker.client import (
    exec_container_shell,
    fetch_container_logs,
    get_container_id,
    get_container_status,
    parse_qq_info_from_logs,
    read_container_file,
    read_container_file_bytes,
    restart_container,
    write_container_file,
)

logger = logging.getLogger("emily.channels")


# ── 常量 ──

# AstrBot 主配置文件（容器内路径）
_ASTRBOT_CONFIG_PATH = "/AstrBot/data/cmd_config.json"

# NapCat 登录二维码：按生成时间搜索容器内最新的一张
# （NapCat 日志中的「二维码已保存到 /app/napcat/cache/qrcode.png」为回退位置）
_NAPCAT_QR_SEARCH_ROOT = "/app/napcat"
_NAPCAT_QR_SEARCH_DEPTH = 3
_NAPCAT_QRCODE_FALLBACK_PATH = "/app/napcat/cache/qrcode.png"

# 搜索脚本：按 mtime 倒序取时间最近的一个二维码文件，输出 "epoch 路径"
_NAPCAT_QR_SEARCH_SCRIPT = (
    "find {root} -maxdepth {depth} -type f "
    "\\( -iname '*qrcode*' -o -iname 'qr*.png' \\) "
    "-exec stat -c '%Y %n' {{}} + 2>/dev/null | sort -rn | head -n 1"
)

# 小程序依赖域名探测超时（秒）
_WXMP_PROBE_TIMEOUT = 3.0

# 邮箱默认服务器（与 emily_core/config.py 中 Config 的默认值保持一致）
_EMAIL_DEFAULT_SMTP_HOST = "smtp.qq.com"
_EMAIL_DEFAULT_SMTP_PORT = 465
_EMAIL_DEFAULT_IMAP_HOST = "imap.qq.com"
_EMAIL_DEFAULT_IMAP_PORT = 993

# 启动报告邮件主题（bootstrap._send_startup_email 固定使用）
_STARTUP_MAIL_SUBJECT_KEYWORD = "系统启动完成"

# 单次拉取的最近邮件数（启动报告为自发邮件，通常位于最新若干封内）
_EMAIL_FETCH_LIMIT = 20

# NapCat 日志：二维码解码 URL / 二维码保存行（行首带 MM-DD HH:MM:SS 时间戳）
_QR_DECODE_URL_RE = re.compile(r"二维码解码URL:\s*(\S+)")
_QR_SAVED_TIME_RE = re.compile(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*二维码已保存到")


# ══════════════════════════════════════════════════════════════════════
#  通用工具
# ══════════════════════════════════════════════════════════════════════


def _kv(label: str, value) -> dict:
    text = str(value) if value not in (None, "") else "—"
    return {"label": label, "value": text}


def _channel(
    key: str,
    label: str,
    connected: bool,
    account_label: str,
    account: str,
    note: str,
    details: list[dict],
    qrcode: dict | None = None,
    editable: dict | None = None,
) -> dict:
    return {
        "channel": key,
        "label": label,
        "connected": connected,
        "account_label": account_label,
        "account": account or "—",
        "note": note,
        "details": details,
        "qrcode": qrcode,
        "editable": editable,
    }


# ══════════════════════════════════════════════════════════════════════
#  控制台可写的渠道覆盖配置
#
#  /app/config 为只读挂载（人工维护），故运行期可改项单独落在 /app/runtime
#  （compose 中按读写挂载），文件：channel_overrides.json
# ══════════════════════════════════════════════════════════════════════

_OVERRIDE_FILENAME = "channel_overrides.json"


def _override_path():
    from ..infrastructure.paths import resolve_data_path

    return Path(
        resolve_data_path(
            "",
            f"/app/runtime/{_OVERRIDE_FILENAME}",
            f"emily-data/runtime/{_OVERRIDE_FILENAME}",
        )
    )


def _load_overrides() -> dict:
    """读取控制台覆盖配置，失败返回空 dict。"""
    path = _override_path()
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            return json.loads(text.lstrip("\ufeff")) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("load channel overrides failed: %s", e)
    return {}


def _save_overrides(data: dict) -> None:
    """写入控制台覆盖配置（父目录按需创建）。"""
    path = _override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
#  QQ 渠道（NapCat + QQ 登录态）
# ══════════════════════════════════════════════════════════════════════


def _parse_qrcode_meta(logs: str) -> dict:
    """从 NapCat 日志中解析二维码解码 URL 与二维码保存时间。"""
    decode_url = ""
    m = _QR_DECODE_URL_RE.search(logs or "")
    if m:
        decode_url = m.group(1)

    saved_at = ""
    for line in (logs or "").splitlines():
        hit = _QR_SAVED_TIME_RE.match(line.strip())
        if hit:
            saved_at = hit.group(1)
    return {"decode_url": decode_url, "saved_at": saved_at}


async def _find_latest_qrcode(container_id: str) -> tuple[str, str]:
    """搜索容器内时间最近的二维码文件，返回 (容器内路径, 生成时间文本)。"""
    script = _NAPCAT_QR_SEARCH_SCRIPT.format(
        root=_NAPCAT_QR_SEARCH_ROOT, depth=_NAPCAT_QR_SEARCH_DEPTH
    )
    out = await exec_container_shell(container_id, script)
    line = out.strip().splitlines()[0].strip() if out.strip() else ""
    if not line:
        return "", ""
    epoch, _, path = line.partition(" ")
    path = path.strip()
    if not path:
        return "", ""
    try:
        saved_at = datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        saved_at = ""
    return path, saved_at


async def _load_qrcode(container_id: str, meta: dict) -> dict:
    """搜索并读取 NapCat 时间最近的登录二维码，返回可直接渲染的 data URL。"""
    path, saved_at = await _find_latest_qrcode(container_id)
    path = path or _NAPCAT_QRCODE_FALLBACK_PATH
    raw = await read_container_file_bytes(container_id, path)
    if not raw:
        return {
            "available": False,
            "data_url": "",
            "decode_url": meta.get("decode_url", ""),
            "saved_at": saved_at or meta.get("saved_at", ""),
            "path": path,
        }
    return {
        "available": True,
        "data_url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
        "decode_url": meta.get("decode_url", ""),
        "saved_at": saved_at or meta.get("saved_at", ""),
        "path": path,
    }


async def get_qq_qrcode() -> dict:
    """重新搜索并返回 NapCat 容器内时间最近的登录二维码（前端点击二维码时调用）。"""
    containers = await get_container_status()
    if {c["name"]: c["status"] for c in containers}.get("napcat") != "running":
        return {"available": False, "reason": "NapCat 容器未运行"}

    container_id = await get_container_id("napcat")
    if not container_id:
        return {"available": False, "reason": "未找到 NapCat 容器"}

    logs = await fetch_container_logs(container_id, tail=200)
    qr = await _load_qrcode(container_id, _parse_qrcode_meta(logs))
    qr["reason"] = "" if qr.get("available") else "未搜索到二维码文件"
    return qr


async def _qq_channel(napcat_status: str) -> dict:
    """QQ 渠道：NapCat 容器运行 + QQ 登录态，账号展示 QQ 号。"""
    napcat_running = napcat_status == "running"
    qq_number = ""
    qq_nickname = ""
    logs = ""
    container_id = await get_container_id("napcat") if napcat_running else None
    if container_id:
        logs = await fetch_container_logs(container_id, tail=200)
        info = parse_qq_info_from_logs(logs)
        if info["logged_in"]:
            qq_number = info["qq_number"]
            qq_nickname = info["qq_nickname"]

    connected = napcat_running and bool(qq_number)
    if not napcat_running:
        note = "NapCat 容器未运行"
    elif not qq_number:
        note = "NapCat 已运行，但 QQ 未登录"
    else:
        note = "NapCat 已登录" + (f"（{qq_nickname}）" if qq_nickname else "")

    # 未登录且容器在跑：给出扫码二维码，便于直接在控制台完成登录
    qrcode = None
    if napcat_running and not connected and container_id:
        qrcode = await _load_qrcode(container_id, _parse_qrcode_meta(logs))

    details = [
        _kv("实现方式", "NapCat（OneBot11）+ AstrBot aiocqhttp 适配器"),
        _kv("容器状态", napcat_status),
        _kv("登录状态", "已登录" if qq_number else "未登录"),
        _kv("登录账号", f"{qq_number}（{qq_nickname}）" if qq_number else "—"),
    ]
    if qrcode is not None:
        details.append(_kv("登录方式", "用手机 QQ 扫描下方二维码完成登录（点击二维码可按生成时间刷新为最新一张）"))

    return _channel(
        "qq", "QQ", connected, "QQ 号", qq_number or "—", note, details, qrcode
    )


# ══════════════════════════════════════════════════════════════════════
#  企业微信渠道（AstrBot wecom 适配器）
# ══════════════════════════════════════════════════════════════════════


def _find_platform(cfg: dict, ptype: str) -> dict | None:
    """在 AstrBot 配置的 platform 数组中按 type 查找适配器配置。"""
    for p in cfg.get("platform") or []:
        if isinstance(p, dict) and p.get("type") == ptype:
            return p
    return None


async def _fetch_astrbot_config() -> dict:
    """读取 AstrBot 主配置（cmd_config.json），失败返回空 dict。"""
    container_id = await get_container_id("astrbot")
    if not container_id:
        return {}
    text = await read_container_file(container_id, _ASTRBOT_CONFIG_PATH)
    if not text.strip():
        return {}
    try:
        # 该文件可能带 UTF-8 BOM（AstrBot 在 Windows 侧编辑过），需先剥离
        return json.loads(text.lstrip("\ufeff"))
    except Exception as e:
        logger.debug("parse astrbot config failed: %s", e)
        return {}


def _last_wecom_error_line(logs: str) -> str:
    """取**本次适配器加载之后**最后一条企微报错行（截断，仅作诊断线索，不参与连通判定）。

    只看最近一次「加载企微适配器」之后的行：否则上一次运行的旧报错会长期压在详情里，
    与重启后的干净状态自相矛盾。
    """
    lines = (logs or "").splitlines()
    start = 0
    for i, line in enumerate(lines):
        if "platform adapter wecom" in line:
            start = i

    last = ""
    for line in lines[start:]:
        low = line.lower()
        if "wecom" in low and ("erro" in low or "error code" in low or "invalid credential" in low):
            last = line.strip()
    return last[:300]


# ── 企业微信实测探测 ──
#
# 连通性一律以实测为准（凭证 → 模式对应权限 → 回调入口），不再扫日志文本：
# 日志里的报错既可能是"可选增强步骤"失败（如 kf/account/list 生成客服二维码），
# 也可能完全不影响收发，用它判定会误报。

_WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin/"

# 探测超时（秒）
_WECOM_PROBE_TIMEOUT = 8.0

# 企微报错 hint 中的出口 IP（用于提示该把哪个 IP 加入企业可信 IP）
_WECOM_FROM_IP_RE = re.compile(r"from ip:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})")

# AstrBot 回调服务（同网内容器名 + Dashboard/Webhook 端口）
_ASTRBOT_CALLBACK_HOST = "astrbot"
_ASTRBOT_CALLBACK_PORT = 6185


async def _wecom_api_get(path: str, params: dict) -> tuple[dict, str]:
    """GET 企微 API，返回 (响应 JSON, 企微侧看到的出口 IP)。

    注：显式 trust_env=False —— emily-core 配了 HTTPS_PROXY（mitmproxy 抓包），
    探测不应绕道代理，否则会因证书/代理不可用而误判不可达。
    """
    timeout = aiohttp.ClientTimeout(total=_WECOM_PROBE_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
        async with client.get(_WECOM_API_BASE + path, params=params) as resp:
            data = await resp.json(content_type=None)

    from_ip = ""
    if isinstance(data, dict):
        m = _WECOM_FROM_IP_RE.search(str(data.get("errmsg", "")))
        if m:
            from_ip = m.group(1)
    return data if isinstance(data, dict) else {}, from_ip


async def _probe_wecom(entry: dict) -> dict:
    """实测企业微信凭证与权限。

    顺序：gettoken（凭证）→ 按模式校验业务 API（kf_name 非空走微信客服，否则自建应用）。
    返回 ``{"ok", "reason", "steps": [{label, ok, detail}], "from_ip"}``；
    reason 为未通过原因（ok=True 时为空串）。
    """
    entry = entry or {}
    corpid = str(entry.get("corpid", "") or "").strip()
    secret = str(entry.get("secret", "") or "").strip()
    kf_name = str(entry.get("kf_name", "") or "").strip()

    if not corpid or not secret:
        return {"ok": False, "reason": "企业 ID 或 Secret 未配置", "steps": [], "from_ip": ""}

    try:
        token_resp, from_ip = await _wecom_api_get(
            "gettoken", {"corpid": corpid, "corpsecret": secret}
        )
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "reason": f"企微 API 不可达：{type(e).__name__}",
            "steps": [],
            "from_ip": "",
        }

    code = token_resp.get("errcode")
    if code != 0:
        msg = f"{code} {token_resp.get('errmsg', '')}".strip()
        return {
            "ok": False,
            "reason": f"凭证校验未通过（{msg}）",
            "steps": [{"label": "凭证校验", "ok": False, "detail": f"gettoken → {msg}"}],
            "from_ip": from_ip,
        }

    token = token_resp.get("access_token", "")
    steps = [{"label": "凭证校验", "ok": True, "detail": "gettoken → 0 ok"}]

    if kf_name:
        label = "微信客服权限"
        path = "kf/account/list"
    else:
        label = "自建应用权限 / 可信 IP"
        path = "agent/list"

    try:
        biz_resp, biz_ip = await _wecom_api_get(path, {"access_token": token})
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "reason": f"{label}探测失败：{type(e).__name__}",
            "steps": steps,
            "from_ip": from_ip,
        }

    from_ip = biz_ip or from_ip
    bcode = biz_resp.get("errcode")
    detail = f"{path} → {bcode} {biz_resp.get('errmsg', '')}".strip()
    steps.append({"label": label, "ok": bcode == 0, "detail": detail})

    reason = ""
    if bcode != 0:
        if bcode == 48002:
            reason = (
                "该 Secret 无此 API 权限：kf_name 非空表示走微信客服模式，"
                "但当前 Secret 应取自私建应用；请换成微信客服专用 Secret，或清空 kf_name 走自建应用模式"
            )
        elif bcode == 60020:
            reason = f"出口 IP {from_ip or '未知'} 未加入企业可信 IP"
        else:
            reason = f"{label}未通过：{detail}"
    return {"ok": bcode == 0, "reason": reason, "steps": steps, "from_ip": from_ip}


async def _probe_wecom_callback(entry: dict) -> dict:
    """探回调侧就绪度：AstrBot 回调服务（同网内）是否可达，返回 ``{"ok", "detail"}``。

    判定口径：能取到 HTTP 响应（含 401/404）即视为回调服务在跑；连接异常视为不可达。
    不从外部请求 Webhook 路径本身——那会因缺少微信签名而在 AstrBot 日志里产生
    一条假的"签名异常"报错，污染诊断。
    **仅证明本网内回调服务在跑**：入口从公网是否可达，取决于企微后台填的回调 URL
    与本机端口映射，服务端无法自证，须在企微侧确认。
    """
    uuid_part = str((entry or {}).get("webhook_uuid", "") or "").strip()
    if not uuid_part:
        return {"ok": False, "detail": "未启用统一 Webhook（缺 webhook_uuid）"}

    url = f"http://{_ASTRBOT_CALLBACK_HOST}:{_ASTRBOT_CALLBACK_PORT}/"
    path = f"/api/platform/webhook/{uuid_part}"
    try:
        timeout = aiohttp.ClientTimeout(total=_WECOM_PROBE_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
            async with client.get(url) as resp:
                return {
                    "ok": True,
                    "detail": f"回调服务可达（HTTP {resp.status}）：{_ASTRBOT_CALLBACK_HOST}:{_ASTRBOT_CALLBACK_PORT}{path}",
                }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "detail": (
                f"回调服务不可达（{_ASTRBOT_CALLBACK_HOST}:{_ASTRBOT_CALLBACK_PORT}）："
                f"{type(e).__name__}"
            ),
        }


async def _wecom_channel(astrbot_status: str) -> dict:
    """企业微信渠道：连通性以**实测**为准（凭证 → 模式权限 → 回调服务），账号展示企业 ID + 客服账号。

    日志里的报错只作诊断线索展示，不参与判定：适配器可能报着 48002 仍能正常收发。
    """
    astrbot_running = astrbot_status == "running"
    cfg = await _fetch_astrbot_config() if astrbot_running else {}
    entry = _find_platform(cfg, "wecom")
    corpid = str(entry.get("corpid", "") or "").strip() if entry else ""
    kf_name = str(entry.get("kf_name", "") or "").strip() if entry else ""
    enabled = bool(entry.get("enable")) if entry else False

    editable = _wecom_editable(entry)
    probe: dict = {"ok": False, "reason": "", "steps": [], "from_ip": ""}
    webhook: dict = {"ok": False, "detail": "未探测（适配器未启用）"}
    connected = False
    note = ""

    if not astrbot_running:
        note = "AstrBot 容器未运行"
    elif not entry:
        note = "未配置企业微信适配器"
    elif not enabled:
        note = "企业微信适配器未启用"
    else:
        probe = await _probe_wecom(entry)
        webhook = await _probe_wecom_callback(entry)
        connected = bool(probe["ok"]) and bool(webhook["ok"])
        note = (
            "实测通过：凭证 / 权限 / 回调服务均正常"
            if connected
            else (probe["reason"] or webhook["detail"])
        )

    details = [
        _kv("实现方式", "AstrBot wecom 适配器（企业微信/微信客服）"),
        _kv("容器状态", astrbot_status),
        _kv("适配器", f"wecom（{entry.get('id', '—')}）" if entry else "未配置"),
        _kv("适配器启用", "是" if enabled else "否"),
        _kv("运行模式", "微信客服（kf_name 已配置）" if kf_name else "自建应用"),
    ]
    for step in probe["steps"]:
        details.append(_kv(step["label"], step["detail"]))
    if probe["from_ip"]:
        details.append(_kv("企微侧看到的出口 IP", probe["from_ip"]))
    details.append(_kv("回调服务", webhook["detail"]))

    # 上部详情表与下部可编辑项去重：可编辑字段已在下方承载，同名行不再重复展示
    details = _drop_editable_duplicates(details, editable)

    # 日志最近报错仅作诊断线索（不参与连通判定）
    if astrbot_running and enabled:
        container_id = await get_container_id("astrbot")
        if container_id:
            last_err = _last_wecom_error_line(
                await fetch_container_logs(container_id, tail=300)
            )
            if last_err:
                details.append(_kv("最近报错（仅参考）", last_err))

    return _channel(
        "wecom",
        "企业微信",
        connected,
        "企业 ID · 客服账号",
        " · ".join(p for p in (corpid, kf_name) if p) or "—",
        note,
        details,
        editable=editable,
    )


# 企业微信可编辑参数。
# clearable = 允许在弹窗中「清空」该字段（kf_name 清空即从微信客服模式切回自建应用；
#             CorpID 无清空语义，故不给）
_WECOM_FIELDS = (
    {"name": "corpid", "label": "企业 ID",
     "placeholder": "ww 开头，如 ww1994648105a74a7c"},
    {"name": "secret", "label": "应用 / 客服 Secret",
     "placeholder": "从企微后台复制的完整 Secret"},
    {"name": "token", "label": "回调 Token", "placeholder": "3-32 位"},
    {"name": "encoding_aes_key", "label": "消息加密密钥", "placeholder": "43 位"},
    {"name": "kf_name", "label": "客服账号", "clearable": True,
     "placeholder": "微信客服后台的客服账号名称；清空即切换为自建应用模式"},
)

_ALLOWED_WECOM_PARAMS = tuple(f["name"] for f in _WECOM_FIELDS)


def _wecom_editable(entry: dict | None) -> dict:
    """企业微信关键参数的录入项描述（前端渲染为「文字 + 点击弹窗替换」）。"""
    entry = entry or {}
    fields = []
    for spec in _WECOM_FIELDS:
        current = str(entry.get(spec["name"], "") or "").strip()
        fields.append({
            "name": spec["name"],
            "label": spec["label"],
            "value": current,
            "display": current or _EMPTY_DISPLAY,
            "placeholder": spec["placeholder"],
            "clearable": bool(spec.get("clearable")),
        })
    return _editable(
        "wecom-params",
        fields,
        "保存并重启 AstrBot",
        "写入 AstrBot 配置 /AstrBot/data/cmd_config.json 的 wecom 适配器并重启 AstrBot 生效"
        "（原文件备份为 cmd_config.json.bak）；点参数值可替换，写入前会做格式校验。",
    )


def _normalize_wecom_param(name: str, value: str) -> str:
    """校验并归一化单个企业微信参数；返回空串表示「保持原值」。"""
    v = (value or "").strip()
    if not v:
        return ""
    if any(c.isspace() for c in v):
        raise ValueError(f"{name} 不能包含空格等空白字符")
    if name == "corpid":
        if not v.startswith("ww"):
            raise ValueError("企业 ID 应以 ww 开头（企微后台「我的企业」→ 企业信息）")
    elif name == "secret":
        if len(v) < 16:
            raise ValueError("Secret 长度异常（疑似占位符），请从企微后台复制完整值")
    elif name == "token":
        if not 3 <= len(v) <= 32:
            raise ValueError("回调 Token 长度须为 3-32 位")
    elif name == "encoding_aes_key":
        if len(v) != 43:
            raise ValueError(f"消息加密密钥须为 43 位，当前 {len(v)} 位")
    return v


async def set_wecom_params(params: dict | None = None, clear: list[str] | None = None) -> dict:
    """写入企业微信关键参数到 AstrBot 配置，并重启 AstrBot 使其生效。

    流程：读配置 → 校验 → 备份 → 写回 → 回读校验 → 重启 astrbot。

    - ``params``：要替换的键值对，仅接受 ``_WECOM_FIELDS`` 白名单内的键；
      密文字段传空串＝保持原值（不回显，故无法区分"没填"与"要清空"）
    - ``clear``：要清空的字段名，仅接受 ``clearable`` 字段（CorpID / 客服账号）
    """
    container_id = await get_container_id("astrbot")
    if not container_id:
        raise ValueError("未找到 AstrBot 容器，请先启动 astrbot")

    text = await read_container_file(container_id, _ASTRBOT_CONFIG_PATH)
    if not text.strip():
        raise ValueError(f"读取 AstrBot 配置失败：{_ASTRBOT_CONFIG_PATH}")
    try:
        # 该文件可能带 UTF-8 BOM（在 Windows 侧编辑过），需先剥离
        cfg = json.loads(text.lstrip("\ufeff"))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"AstrBot 配置不是合法 JSON，已中止写入：{e}") from e

    entry = _find_platform(cfg, "wecom")
    if entry is None:
        raise ValueError("AstrBot 配置中未找到 wecom 适配器，请先在 AstrBot 面板添加后再录入")

    applied: dict[str, str] = {}
    for name in _ALLOWED_WECOM_PARAMS:
        if name not in (params or {}):
            continue
        value = _normalize_wecom_param(name, params[name])
        if value:
            applied[name] = value
    cleared: list[str] = []
    for name in (clear or []):
        spec = next((f for f in _WECOM_FIELDS if f["name"] == name), None)
        if spec is None:
            raise ValueError(f"不支持清空的字段：{name}")
        if not spec.get("clearable"):
            raise ValueError(f"{spec['label']} 不支持清空（如需更换请直接输入新值）")
        cleared.append(name)

    if not applied and not cleared:
        raise ValueError("没有需要写入的参数")

    entry.update(applied)
    for name in cleared:
        entry[name] = ""

    backup_path = f"{_ASTRBOT_CONFIG_PATH}.bak"
    await exec_container_shell(container_id, f"cp {_ASTRBOT_CONFIG_PATH} {backup_path}")
    if not await write_container_file(
        container_id,
        _ASTRBOT_CONFIG_PATH,
        json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8"),
    ):
        raise ValueError("写入 AstrBot 配置失败（容器内文件不可写？）")

    written = await read_container_file(container_id, _ASTRBOT_CONFIG_PATH)
    try:
        json.loads(written.lstrip("\ufeff"))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"写回后配置校验失败，请用 {backup_path} 还原：{e}") from e

    parts = []
    if applied:
        parts.append(f"写入 {'、'.join(sorted(applied))}")
    if cleared:
        parts.append(f"清空 {'、'.join(sorted(cleared))}")
    saved = "，".join(parts)

    restarted = await restart_container("astrbot")
    logger.info("wecom params saved: %s (restarted=%s)", saved, restarted)

    if restarted:
        message = (
            f"已{saved}，AstrBot 已重启生效；"
            "约 10 秒后自动刷新连通状态（原配置已备份为 cmd_config.json.bak）"
        )
    else:
        message = (
            f"已{saved}，但自动重启 AstrBot 失败，"
            "请手动执行 docker compose restart astrbot 使其生效"
        )
    return {"saved": sorted(applied), "cleared": sorted(cleared), "message": message}


# ══════════════════════════════════════════════════════════════════════
#  微信小程序渠道（独立网关 + 依赖域名）
# ══════════════════════════════════════════════════════════════════════


def _normalize_wxmp_domain(raw: str) -> str:
    """归一化小程序依赖域名：补 https:// 前缀、去掉尾部斜杠。"""
    domain = (raw or "").strip()
    if not domain:
        return ""
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    return domain.rstrip("/")


def _effective_wxmp_domain() -> tuple[str, str, str]:
    """返回 (生效域名, 生效来源, 控制台覆盖的原始值)。覆盖优先，其次环境变量。"""
    override_raw = str(_load_overrides().get("wxmp_domain", "") or "").strip()
    if override_raw:
        return _normalize_wxmp_domain(override_raw), "控制台覆盖", override_raw
    env_raw = (os.environ.get("EMILY_WXMP_DOMAIN", "") or "").strip()
    if env_raw:
        return _normalize_wxmp_domain(env_raw), "环境变量 EMILY_WXMP_DOMAIN", ""
    return "", "未配置", ""


# 空字段在控制台的展示文本
_EMPTY_DISPLAY = "空"


def _editable(endpoint: str, fields: list[dict], submit_label: str, hint: str = "") -> dict:
    """渠道可编辑项描述（前端渲染为「文字 + 点击弹窗替换」，不在详情区平铺 input）。

    - ``endpoint``：提交目标，前端 POST ``/api/v1/console/channels/<endpoint>``
    - ``fields``：``[{name, label, value, display, placeholder, clearable}]``
      · ``display``：详情区展示文本，未给出时按 ``value`` 自动填充，空值显示「空」
      · ``clearable``：是否允许在弹窗中「清空」该字段
    """
    for f in fields:
        f.setdefault("display", f.get("value") or _EMPTY_DISPLAY)
        f.setdefault("clearable", False)
    return {
        "endpoint": endpoint,
        "submit_label": submit_label,
        "hint": hint,
        "fields": fields,
    }


def _drop_editable_duplicates(details: list[dict], editable: dict | None) -> list[dict]:
    """去掉详情表中与可编辑字段同名的行：同名即重复，改由下方可编辑项承载。"""
    labels = {f.get("label") for f in (editable or {}).get("fields", [])}
    if not labels:
        return details
    return [d for d in details if d.get("label") not in labels]


def _wxmp_editable(override_raw: str, source: str) -> dict:
    """小程序渠道的「关联域名」录入项描述。"""
    return _editable(
        "wxmp-domain",
        [{
            "name": "domain",
            "label": "关联域名",
            "value": override_raw,
            "placeholder": "https://your-domain.example.com",
            "clearable": True,
        }],
        "保存",
        f"当前生效来源：{source}；点「清空该字段」后保存＝清除控制台覆盖，回落到环境变量",
    )


async def _probe_wxmp_health(base_url: str) -> tuple[bool, str]:
    """探测小程序依赖域名的 /health（不跟随环境代理）。返回 (是否可达, 结果说明)。"""
    try:
        timeout = aiohttp.ClientTimeout(total=_WXMP_PROBE_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
            async with client.get(base_url + "/health") as resp:
                return resp.status == 200, f"HTTP {resp.status}"
    except Exception as e:
        logger.debug("wxmp health probe failed (%s): %s", base_url, e)
        return False, f"{type(e).__name__}: {e}"


async def _wxmp_channel() -> dict:
    """微信小程序渠道：依赖已备案 HTTPS 域名，账号展示该域名。"""
    domain, source, override_raw = _effective_wxmp_domain()
    editable = _wxmp_editable(override_raw, source)

    if not domain:
        return _channel(
            "wxmp",
            "微信小程序",
            False,
            "依赖域名",
            "未配置",
            "未配置关联域名",
            [
                _kv("实现方式", "独立 wechat-gateway 薄网关（小程序 wx.request → /chat）"),
                _kv("依赖域名", "未配置"),
                _kv("生效来源", source),
                _kv("微信平台要求", "须为 HTTPS 且已完成 ICP 备案，并在公众平台配置「request 合法域名」"),
            ],
            editable=editable,
        )

    reachable, probe_result = await _probe_wxmp_health(domain)
    return _channel(
        "wxmp",
        "微信小程序",
        reachable,
        "依赖域名",
        domain,
        "网关 /health 探测通过" if reachable else "依赖域名 /health 探测失败",
        [
            _kv("实现方式", "独立 wechat-gateway 薄网关（小程序 wx.request → /chat）"),
            _kv("依赖域名", domain),
            _kv("生效来源", source),
            _kv("探测地址", domain + "/health"),
            _kv("探测结果", probe_result),
            _kv("微信平台要求", "须为 HTTPS 且已完成 ICP 备案，并在公众平台配置「request 合法域名」"),
        ],
        editable=editable,
    )


async def set_wxmp_domain(domain: str) -> dict:
    """保存 / 清除微信小程序关联域名的控制台覆盖值，返回更新后的渠道数据。

    - 非空：归一化 + 校验后写入覆盖值
    - 空串：清除覆盖，生效值回落到 EMILY_WXMP_DOMAIN
    """
    raw = (domain or "").strip()
    if raw:
        normalized = _normalize_wxmp_domain(raw)
        if " " in normalized or not urlparse(normalized).netloc:
            raise ValueError("域名格式不正确，示例：https://emily.example.com")
    else:
        normalized = ""

    data = _load_overrides()
    if normalized:
        data["wxmp_domain"] = normalized
    else:
        data.pop("wxmp_domain", None)
    _save_overrides(data)

    logger.info("wxmp domain override saved: %s", normalized or "(cleared)")
    return await _wxmp_channel()


# ══════════════════════════════════════════════════════════════════════
#  邮箱渠道（SMTP 发信 + IMAP 收信，以「收到自发启动报告」为联通证据）
# ══════════════════════════════════════════════════════════════════════


def _email_credentials() -> tuple[EmailCredentials | None, str, str]:
    """构造邮箱凭证。返回 (凭证|None, 失败原因, 邮箱账号)。"""
    idkey = os.environ.get("EMILY_EMAIL_IDKEY", "").strip()
    password = os.environ.get("EMILY_EMAIL_PASSWORD", "").strip()
    if not idkey or not password:
        return None, "未设置 EMILY_EMAIL_IDKEY / EMILY_EMAIL_PASSWORD", idkey

    creds = EmailCredentials(
        smtp_host=os.environ.get("EMILY_EMAIL_SMTP_HOST", "").strip() or _EMAIL_DEFAULT_SMTP_HOST,
        smtp_port=int(os.environ.get("EMILY_EMAIL_SMTP_PORT", "") or _EMAIL_DEFAULT_SMTP_PORT),
        imap_host=os.environ.get("EMILY_EMAIL_IMAP_HOST", "").strip() or _EMAIL_DEFAULT_IMAP_HOST,
        imap_port=int(os.environ.get("EMILY_EMAIL_IMAP_PORT", "") or _EMAIL_DEFAULT_IMAP_PORT),
        username=idkey,
        password=password,
    )
    return creds, "", idkey


async def _imap_probe_login(creds: EmailCredentials) -> tuple[bool, str]:
    """显式 IMAP 登录探测：登录是否成功 / 失败原因（provider 会吞异常，故单独探测）。"""
    import aioimaplib

    imap = None
    try:
        imap = aioimaplib.IMAP4_SSL(
            host=creds.imap_host, port=creds.imap_port, timeout=15
        )
        await imap.wait_hello_from_server()
        resp = await imap.login(creds.username, creds.password)
        if resp.result != "OK":
            return False, f"登录被拒绝（{resp.result}）"
        await imap.select("INBOX")
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if imap is not None:
            try:
                await imap.logout()
            except Exception:  # noqa: BLE001
                pass


def _pick_startup_mail(envelopes: list) -> object | None:
    """从邮件列表中挑出最近一封「系统启动完成」启动报告（按邮件时间取最新）。"""
    found = [
        env for env in envelopes
        if _STARTUP_MAIL_SUBJECT_KEYWORD in (env.subject or "")
    ]
    if not found:
        return None
    dated = [env for env in found if env.date]
    if dated:
        return max(dated, key=lambda env: env.date)
    return found[-1]


async def _email_channel() -> dict:
    """邮箱渠道：IMAP 登录成功 且 收到自发启动报告邮件 → 已联通。"""
    creds, reason, idkey = _email_credentials()
    base_details = [
        _kv("实现方式", "进程内 SMTP 发信 + IMAP 收信（启动时自发启动报告）"),
        _kv("邮箱账号", idkey or "未配置"),
    ]
    if creds is None:
        base_details.append(_kv("SMTP / IMAP", "—"))
        return _channel(
            "email", "邮箱", False, "邮箱账号", "未配置", reason, base_details
        )

    base_details.append(_kv("SMTP", f"{creds.smtp_host}:{creds.smtp_port}"))
    base_details.append(_kv("IMAP", f"{creds.imap_host}:{creds.imap_port}"))

    provider = IMAPEmailProvider()

    # ① 拉取最近邮件：拿到邮件即说明 IMAP 登录成功
    envelopes: list = []
    try:
        envelopes = await provider.fetch_inbox(creds, unread_only=False, limit=_EMAIL_FETCH_LIMIT)
    except Exception as e:  # noqa: BLE001
        logger.debug("email fetch failed: %s", e)

    login_ok = bool(envelopes)
    login_detail = "成功"
    if not login_ok:
        # ② 无邮件时无法区分「登录失败」与「收件箱空/无新邮件」，补一次显式登录探测
        login_ok, err = await _imap_probe_login(creds)
        login_detail = "成功" if login_ok else f"失败（{err}）"

    base_details.append(_kv("IMAP 登录", login_detail))

    if not login_ok:
        return _channel(
            "email", "邮箱", False, "邮箱账号", idkey,
            "IMAP 登录失败，请检查授权码", base_details,
        )

    startup_mail = _pick_startup_mail(envelopes)
    if startup_mail is None:
        base_details.append(
            _kv("启动报告邮件", f"最近 {_EMAIL_FETCH_LIMIT} 封中未找到「{_STARTUP_MAIL_SUBJECT_KEYWORD}」")
        )
        return _channel(
            "email", "邮箱", False, "邮箱账号", idkey,
            "IMAP 登录成功，但未收到自发启动报告邮件", base_details,
        )

    when = startup_mail.date.strftime("%Y-%m-%d %H:%M:%S") if startup_mail.date else "—"
    base_details.append(_kv("启动报告邮件", startup_mail.subject or "—"))
    base_details.append(_kv("邮件时间", when))
    base_details.append(_kv("发件人", startup_mail.sender or "—"))

    return _channel(
        "email", "邮箱", True, "邮箱账号", idkey,
        "IMAP 登录成功，已收到自发启动报告邮件", base_details,
    )


# ══════════════════════════════════════════════════════════════════════
#  聚合入口
# ══════════════════════════════════════════════════════════════════════


async def get_access_channels() -> list[dict]:
    """返回接入渠道（QQ / 企业微信 / 微信小程序 / 邮箱）的连通性、实现账号与细节。"""
    containers = await get_container_status()
    status = {c["name"]: c["status"] for c in containers}

    return [
        await _qq_channel(status.get("napcat", "unknown")),
        await _wecom_channel(status.get("astrbot", "unknown")),
        await _wxmp_channel(),
        await _email_channel(),
    ]
