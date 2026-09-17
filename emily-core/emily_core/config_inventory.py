"""emy-config 配置清单 —— 只读聚合（三方对照 + 差异告警）。

回答三个问题：这项配置现在是什么值、值从哪来、改了会不会生效。

设计要点（对应 emy-config PRD 的约束项）：
  · C-04 环境变量入口的单一来源是 bootstrap.ENV_CONFIG_MAP，本模块只反查，不另立副本。
  · C-05 字段说明在运行时解析 config.py 源码取得，不在清单里手抄第二份（防漂移）。
  · C-07/C-08 只读；密钥类一律掩码，任何返回值不含明文密钥（含 database_url 的连接串口令）。
  · 非功能性：源码解析失败、.env 缺失、配置文件损坏均降级展示，不抛异常阻断页面。

写盘边界：本模块只写一处 —— 宿主机 .env（save_env_values，把字段值写回唯一生效通路）。
除此之外不写任何文件：配置文件内容接口只读，core_config.json / scheduler_config.json 等
「不被读取」的文件永远不被本模块改写。
"""

from __future__ import annotations

import ast
import json
import logging
import os
import platform
import re
from dataclasses import MISSING
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .bootstrap import (
    ENV_BOOL_FIELDS,
    ENV_CONFIG_MAP,
    ENV_FLOAT_FIELDS,
    ENV_INT_FIELDS,
    ENV_LIST_FIELDS,
)
from .config import Config

logger = logging.getLogger("emily.config_inventory")

BEIJING_TZ = timezone(timedelta(hours=8))

# 单文件返回内容上限（AC 非功能性要求）
MAX_FILE_CHARS = 200_000

# 容器内路径：emily-core 只读挂载 ./emily-data/config:/app/config:ro
_CONTAINER_CONFIG_DIR = Path("/app/config")
# 宿主机 .env 的容器内只读挂载点（C-03；三份 compose 同步）
_CONTAINER_DOTENV = Path("/app/host/.env")

# 密钥判定关键词：命中即只输出「已配置（N 字符）」
_SENSITIVE_KEYWORDS = (
    "api_key", "apikey", "password", "passwd", "secret", "token", "idkey", "credential",
)

# 形如 scheme://user:pass@host 的连接串，掩掉口令部分
_URL_CRED_RE = re.compile(r"(?<=://)([^:/@\s]+):([^@/\s]+)@")


# ══════════════════════════════════════════════════════════════════════════════
#  登记表：容器内配置文件 + 宿主机侧声明文件
# ══════════════════════════════════════════════════════════════════════════════

# 「是否被运行时代码读取」是静态结论，每条附 文件:行号 证据（PRD 风险 R4）。
# 被读取 → loader 给出加载位置；不被读取 → note 说明改它无效。
_CONFIG_FILES: list[dict] = [
    {
        "name": "hook_config.json",
        "loaded": True,
        "loader": "emily_core/__init__.py:928 _load_hook_config()",
        "note": "",
    },
    {
        "name": "mcp_servers.json",
        "loaded": True,
        "loader": "emily_core/mcp/config.py:79-92",
        "note": "",
    },
    {
        "name": "retrieval_channels.json",
        "loaded": True,
        "loader": "emily_core/retrieval/channel_registry.py:20-22",
        "note": "",
    },
    {
        "name": "retrieval_strategy.json",
        "loaded": True,
        "loader": "emily_core/retrieval/strategy.py:18-20",
        "note": "",
    },
    {
        "name": "scripts_registry.yaml",
        "loaded": True,
        "loader": "emily_core/scripts/registry.py:121-126",
        "note": "",
    },
    {
        "name": "core_config.json",
        "loaded": False,
        "loader": "",
        "note": "bootstrap.init() 只走「环境变量 → Config」，不读取本文件；改它不生效，应改 .env / compose environment",
    },
    {
        "name": "scheduler_config.json",
        "loaded": False,
        "loader": "",
        "note": "全仓无代码读取；真实作业行来自数据库表 scheduler_jobs；改它不生效",
    },
]

# 宿主机侧声明（容器内默认不可读，见 C-03）
_HOST_FILES: list[dict] = [
    {
        "name": ".env",
        "container_path": str(_CONTAINER_DOTENV),
        "mounted": True,
        "note": "只读挂载（C-03）。仅取键名、是否注入、与非敏感值；敏感键只给「是否已配置 + 长度」",
    },
    {
        "name": "docker-compose-minimal.yml",
        "container_path": "",
        "mounted": False,
        "note": "宿主机侧文件，未挂载到容器，容器内不可读；环境变量是否注入以容器环境为准",
    },
    {
        "name": "docker-compose-napcat.yml",
        "container_path": "",
        "mounted": False,
        "note": "宿主机侧文件，未挂载到容器，容器内不可读；环境变量是否注入以容器环境为准",
    },
    {
        "name": "docker-compose-wecom.yml",
        "container_path": "",
        "mounted": False,
        "note": "宿主机侧文件，未挂载到容器，容器内不可读；环境变量是否注入以容器环境为准",
    },
]


# 配置分组（PRD §5.1 的 11 组）。未在任一组登记的字段自动归入 other。
_SECTION_FIELDS: list[tuple[str, str, list[str]]] = [
    ("llm", "LLM 模型与采样", [
        "llm_api_key", "llm_base_url", "llm_model", "llm_temperature", "llm_max_tokens",
        "llm_agent_loop_max_tokens", "llm_context_window_override",
        "llm_compact_reserve_tokens", "llm_compact_keep_recent_tokens", "llm_dynamic_output",
        "llm_router_model", "llm_guardian_model", "llm_agent_loop_model",
        "llm_expert_max_tokens", "expert_model",
    ]),
    ("agent_loop", "Agent Loop 与编排", [
        "expert_review_enabled", "agent_loop_max_iterations",
        "node_retry_max_attempts", "node_retry_initial_interval", "node_retry_backoff_factor",
        "node_timeout_seconds", "node_timeout_overrides",
        "orchestrator_enabled", "orchestrator_max_depth", "orchestrator_max_dynamic_wis",
        "session_loop_sop_allowlist", "capability_call_timeout_seconds",
        "checkpoint_resume_window_seconds", "langgraph_max_replan", "langgraph_checkpointer",
        "fallback_basic_tools", "fallback_advanced_write_tools", "fallback_admin_min_level",
    ]),
    ("session", "会话与上下文", [
        "session_ttl_seconds", "session_max_concurrent", "workitem_max_per_session",
        "progress_message_template", "test_conv_prefixes", "default_interaction_channel",
    ]),
    ("rag", "知识库与 RAG", [
        "kb_enabled", "tei_url", "embedding_mode", "embedding_api_url", "embedding_api_key",
        "embedding_model", "rag_similarity_threshold", "kb_top_k", "kb_local_fallback_dir",
    ]),
    ("vlm", "VLM 视觉", [
        "vlm_api_url", "vlm_api_key", "vlm_model",
    ]),
    ("storage", "数据库与文件存储", [
        "database_url", "storage_root", "prompts_dir", "pending_issues_path",
    ]),
    ("log_archive", "日志与归档与记忆", [
        "log_level", "log_dir", "log_to_file",
        "journal_enabled", "journal_path",
        "user_memory_enabled", "user_memory_dir", "user_memory_max_entries",
        "session_archive_enabled", "session_archive_dir",
    ]),
    ("email", "邮箱渠道", [
        "email_smtp_host", "email_smtp_port", "email_imap_host", "email_imap_port",
    ]),
    ("scheduler", "计划任务", [
        "scheduler_enabled", "scheduler_tick_seconds",
    ]),
    ("access", "权限与准入", [
        "auto_create_user", "auto_create_whitelist",
        "permission_cache_ttl_seconds", "permission_fail_open",
    ]),
    ("other", "其他（仅代码默认值）", [
        "bot_name", "takeover_mode", "hook_config_path", "extra",
    ]),
]


# ══════════════════════════════════════════════════════════════════════════════
#  路径与取值工具
# ══════════════════════════════════════════════════════════════════════════════

def _project_root() -> Path:
    """仓库根目录（容器内即 /app）。"""
    return Path(__file__).resolve().parents[2]


def _config_dir() -> Path:
    """容器内配置文件目录：优先 /app/config，开发态回退 emily-data/config。"""
    if _CONTAINER_CONFIG_DIR.is_dir():
        return _CONTAINER_CONFIG_DIR
    return _project_root() / "emily-data" / "config"


def _dotenv_path() -> Path:
    """宿主机 .env 的只读挂载点；开发态回退仓库根 .env。"""
    if _CONTAINER_DOTENV.is_file():
        return _CONTAINER_DOTENV
    return _project_root() / ".env"


def _is_sensitive_name(name: str) -> bool:
    """是否密钥类（命中即只输出「已配置（N 字符）」）。

    注意 "tokens" 是 token 数量（llm_max_tokens / llm_compact_reserve_tokens 等），
    不是密钥——按关键词粗判会把这类数值字段整列掩掉，故单独排除。
    """
    low = name.lower()
    for kw in _SENSITIVE_KEYWORDS:
        if kw not in low:
            continue
        if kw == "token" and "tokens" in low:
            continue
        return True
    return False


def _redact_credentials(text: str) -> str:
    """掩掉 URL 中的口令段（如 database_url 的连接串）。"""
    return _URL_CRED_RE.sub(r"\1:***@", text)


def _display_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(_display_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _cell(name: str, value: Any) -> dict:
    """统一的值单元：{present, display, length}。密钥只给长度，不给明文。"""
    if value is None:
        return {"present": False, "display": "", "length": 0}
    if isinstance(value, str) and value == "":
        return {"present": False, "display": "", "length": 0}
    if isinstance(value, (list, dict, tuple)) and not value:
        return {"present": False, "display": "", "length": 0}
    if _is_sensitive_name(name):
        n = len(str(value))
        return {"present": True, "display": f"已配置（{n} 字符）", "length": n}
    text = _redact_credentials(_display_text(value))
    return {"present": True, "display": text, "length": len(text)}


def _normalize(value: Any) -> str:
    """比对前归一化：'true'/True、'1'/1 视为同值（避免告警误报）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number_str(float(value))
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in ("true", "false"):
            return s.lower()
        try:
            return _number_str(float(s))
        except ValueError:
            return s
    if isinstance(value, (list, tuple)):
        return ",".join(sorted(_normalize(v) for v in value))
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _number_str(f: float) -> str:
    return str(int(f)) if f == int(f) else str(f)


# ══════════════════════════════════════════════════════════════════════════════
#  config.py 源码解析（C-05：字段说明不手抄副本）
# ══════════════════════════════════════════════════════════════════════════════

class _Missing:
    """字段无默认值（dataclasses.MISSING 的占位）。"""


_NO_DEFAULT = _Missing()


def _literal(node: ast.AST | None) -> Any:
    """尽力求值字段默认值表达式；失败返回 _NO_DEFAULT。"""
    if node is None:
        return _NO_DEFAULT
    if isinstance(node, ast.Call):
        # field(default=...) / field(default_factory=...)
        if isinstance(node.func, ast.Name) and node.func.id == "field":
            for kw in node.keywords:
                if kw.arg == "default":
                    return _literal(kw.value)
            for kw in node.keywords:
                if kw.arg == "default_factory":
                    return _literal(kw.value)
        return _NO_DEFAULT
    if isinstance(node, ast.Lambda):
        return _literal(node.body)
    if isinstance(node, ast.Name):
        return {"list": [], "dict": {}, "tuple": (), "set": set(),
                "str": "", "int": 0, "float": 0.0, "bool": False}.get(node.id, _NO_DEFAULT)
    try:
        return ast.literal_eval(node)
    except Exception:
        return _NO_DEFAULT


def _parse_config_source() -> dict[str, dict]:
    """解析 config.py 源码，取 {字段名: {type, default, note}}。

    依赖 config.py 顶部的格式约定（赋值语句后紧跟字符串字面量说明）。
    解析失败时返回空字典，由调用方降级到 dataclass 元数据。
    """
    src_path = Path(__file__).with_name("config.py")
    try:
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
    except Exception as e:  # 解析失败不阻断页面（R3）
        logger.warning("parse %s failed, field notes degraded: %s", src_path, e)
        return {}

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Config":
            return _parse_config_class(node)
    return {}


def _parse_config_class(node: ast.ClassDef) -> dict[str, dict]:
    body = node.body
    fields: dict[str, dict] = {}
    for idx, stmt in enumerate(body):
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        name = stmt.target.id
        try:
            type_str = ast.unparse(stmt.annotation)
        except Exception:
            type_str = ""
        note = ""
        nxt = body[idx + 1] if idx + 1 < len(body) else None
        if (
            isinstance(nxt, ast.Expr)
            and isinstance(nxt.value, ast.Constant)
            and isinstance(nxt.value.value, str)
        ):
            note = " ".join(nxt.value.value.split())
        fields[name] = {"type": type_str, "default": _literal(stmt.value), "note": note}
    return fields


def _type_name(annotation: Any) -> str:
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", str(annotation))


def _dataclass_meta() -> dict[str, dict]:
    """降级路径：从 dataclass 元数据取字段名/类型/默认值（说明列留空）。"""
    meta: dict[str, dict] = {}
    for f in Config.__dataclass_fields__.values():
        default: Any = _NO_DEFAULT
        if f.default is not MISSING:
            default = f.default
        elif f.default_factory is not MISSING:
            try:
                default = f.default_factory()
            except Exception:
                default = _NO_DEFAULT
        meta[f.name] = {"type": _type_name(f.type), "default": default, "note": ""}
    return meta


# ══════════════════════════════════════════════════════════════════════════════
#  .env 解析
# ══════════════════════════════════════════════════════════════════════════════

def _read_dotenv(path: Path) -> tuple[dict[str, str], bool, str]:
    """读宿主机 .env，返回 (键值对, 是否可读, 错误说明)。

    支持 `KEY=VALUE`、`export KEY=VALUE`、`#` 注释与引号包裹；
    重复键后者覆盖前者（与 shell 语义一致）。
    """
    if not path.is_file():
        return {}, False, f"未找到 {path}（.env 未挂载或不存在）"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {}, False, f"读取 {path} 失败：{e}"

    data: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        data[key] = val
    return data, True, ""


# ══════════════════════════════════════════════════════════════════════════════
#  清单构建
# ══════════════════════════════════════════════════════════════════════════════

def _save_state(
    env_key: str,
    container_val: str | None,
    declared_present: bool,
    dotenv_ok: bool,
) -> tuple[bool, str, str]:
    """判断该字段能否「写回 .env 后生效」，返回 (可保存, 拦截原因, 风险提示)。

    .env 只是声明面，必须经 compose environment 注入容器才生效。因此分三种情况：
      · 无环境变量入口      → 无处可写（只能改 config.py）
      · 变量不在容器环境里  → compose 未注入，写了必然不生效，直接拦下
      · 变量已在容器环境里  → 可写；若当前未在 .env 声明，附风险提示（compose 可能是
        写死的字面量而非 ${VAR:-...} 引用，此时写 .env 不生效）
    """
    if not env_key:
        return False, "该字段没有环境变量入口，只能改 config.py 默认值（docker restart emily-core）", ""
    if not dotenv_ok:
        return False, "宿主机 .env 不可读/未挂载，无法写回；请先补 .env 只读挂载并重建容器", ""
    if container_val is None:
        return (
            False,
            f"{env_key} 未被 compose 注入容器（容器环境里没有该变量），写进 .env 不生效；"
            f"请先在 docker-compose-*.yml 的 emily-core.environment 段补上该变量",
            "",
        )
    if not declared_present:
        return (
            True,
            "",
            f"{env_key} 当前未在 .env 声明；若 compose 里是写死的字面量（而非 ${{VAR:-默认值}}），"
            f"写进 .env 不会生效，需先改 compose",
        )
    return True, "", ""


def _validate_env_value(cfg_key: str, value: str) -> str:
    """按类型集校验待写入值（C-06：环境变量是字符串，类型必须显式可校验）。"""
    if "\n" in value or "\r" in value:
        return "值不能包含换行"
    if cfg_key in ENV_BOOL_FIELDS:
        if value.strip().lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            return f"布尔字段只接受 true/false/1/0/yes/no/on/off，收到 {value!r}"
    elif cfg_key in ENV_INT_FIELDS:
        try:
            int(value.strip())
        except ValueError:
            return f"整数字段只接受整数，收到 {value!r}"
    elif cfg_key in ENV_FLOAT_FIELDS:
        try:
            float(value.strip())
        except ValueError:
            return f"浮点字段只接受数字，收到 {value!r}"
    return ""


def _build_fields(
    config: Config,
    source_meta: dict[str, dict],
    fallback_meta: dict[str, dict],
    dotenv: dict[str, str],
    dotenv_ok: bool,
) -> list[dict]:
    """全部字段的三方对照（代码默认值 / .env 声明值 / 容器生效值）。"""
    env_of: dict[str, str] = {cfg_key: env_key for env_key, cfg_key in ENV_CONFIG_MAP.items()}
    fields: list[dict] = []

    for name in fallback_meta:  # dataclass 字段顺序 = Config 声明顺序
        meta = source_meta.get(name) or fallback_meta[name]
        default = meta.get("default", _NO_DEFAULT)
        if default is _NO_DEFAULT:
            default = None

        env_key = env_of.get(name, "")
        container_val = os.environ.get(env_key) if env_key else None
        declared_present = bool(env_key) and dotenv_ok and env_key in dotenv
        declared_val = dotenv.get(env_key) if declared_present else None

        if env_key and container_val is not None and container_val.strip():
            source = "env"
        elif env_key and declared_present and container_val is None:
            source = "dotenv-uninjected"
        else:
            source = "default"

        saveable, save_reason, save_caveat = _save_state(
            env_key, container_val, declared_present, dotenv_ok
        )

        fields.append({
            "field": name,
            "type": meta.get("type", ""),
            "note": meta.get("note", ""),
            "env": env_key,
            "has_env": bool(env_key),
            "sensitive": _is_sensitive_name(name),
            "default": _cell(name, default),
            "declared": _cell(name, declared_val),
            "effective": _cell(name, getattr(config, name, None)),
            "source": source,
            "restart_required": bool(env_key),
            "saveable": saveable,
            "save_reason": save_reason,
            "save_caveat": save_caveat,
        })
    return fields


def _build_sections(field_names: list[str]) -> tuple[list[dict], dict[str, str]]:
    """分组清单 + 字段→分组映射；未登记的字段自动归入 other。"""
    declared: dict[str, str] = {}
    for key, _title, names in _SECTION_FIELDS:
        for name in names:
            declared[name] = key

    unknown = [n for n in declared if n not in field_names]
    if unknown:
        logger.warning("section map lists unknown config fields: %s", ", ".join(sorted(unknown)))

    section_of = {n: declared.get(n, "other") for n in field_names}
    counts: dict[str, int] = {}
    for sec in section_of.values():
        counts[sec] = counts.get(sec, 0) + 1
    sections = [
        {"key": key, "title": title, "count": counts.get(key, 0)}
        for key, title, _names in _SECTION_FIELDS
    ]
    return sections, section_of


def _read_json(path: Path) -> tuple[dict | None, str]:
    """读 JSON 对象；损坏/非对象时返回 (None, 错误说明)。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"解析失败：{e}"
    if not isinstance(raw, dict):
        return None, "顶层不是对象，跳过键值比对"
    return raw, ""


def _is_flat_map(raw: dict) -> bool:
    """顶层是否「扁平配置表」（全部值为标量）。

    只有扁平配置表才做「键值 vs 生效值」比对；作业清单（jobs 为数组）之类的
    结构文件不参与，避免把业务数据误报成配置差异。
    """
    if not raw:
        return False
    return all(
        isinstance(v, (str, int, float, bool)) or v is None
        for k, v in raw.items()
        if not str(k).startswith("_")
    )


def _build_files(config: Config) -> tuple[list[dict], list[dict]]:
    """容器内配置文件状态 + 由文件派生的告警。"""
    config_dir = _config_dir()
    files: list[dict] = []
    findings: list[dict] = []

    for spec in _CONFIG_FILES:
        path = config_dir / spec["name"]
        item = {
            "name": spec["name"],
            "path": str(path),
            "exists": path.is_file(),
            "size": 0,
            "mtime": "",
            "loaded": spec["loaded"],
            "loader": spec["loader"],
            "note": spec["note"],
        }
        if path.is_file():
            try:
                st = path.stat()
                item["size"] = st.st_size
                item["mtime"] = datetime.fromtimestamp(st.st_mtime, BEIJING_TZ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception as e:
                logger.warning("stat %s failed: %s", path, e)

        # 仅文件实际存在时才报「不被读取」（已删则无意义）
        if item["exists"] and not spec["loaded"]:
            findings.append({
                "level": "warn",
                "kind": "file-not-loaded",
                "title": f"{spec['name']} 不被任何运行时代码读取",
                "detail": spec["note"] or "已登记但无运行时代码读取该文件。",
                "next": "改它无效：应改 .env（环境变量）后 docker compose up -d emily-core 重建容器",
            })

            # 扁平配置表才做键值比对（结构文件不参与）
            raw, err = _read_json(path)
            if raw is None:
                if err:
                    item["note"] = (item["note"] + "；" + err).strip("；")
            elif _is_flat_map(raw):
                conflicts = []
                invalid = []
                for key, value in raw.items():
                    if str(key).startswith("_"):
                        continue  # _comment 之类注释键不算配置字段
                    if key not in Config.__dataclass_fields__:
                        invalid.append(key)
                        continue
                    effective = getattr(config, key, None)
                    if _normalize(value) != _normalize(effective):
                        conflicts.append({
                            "field": key,
                            "file": _cell(key, value),
                            "effective": _cell(key, effective),
                        })
                if conflicts:
                    findings.append({
                        "level": "warn",
                        "kind": "file-conflict",
                        "title": f"{spec['name']} 有 {len(conflicts)} 项与生效值冲突",
                        "detail": "该文件不参与运行，其值不生效。",
                        "items": conflicts,
                        "next": "以 .env / compose environment 为准；是否需要清理该文件由独立需求决定",
                    })
                if invalid:
                    findings.append({
                        "level": "info",
                        "kind": "file-invalid-key",
                        "title": f"{spec['name']} 有 {len(invalid)} 个非 Config 字段",
                        "detail": "、".join(invalid),
                        "next": "确认是否另有消费方；若无，可在后续清理需求中移除",
                    })

        files.append(item)
    return files, findings


def _build_env_views(
    dotenv: dict[str, str],
    dotenv_ok: bool,
) -> tuple[list[dict], list[dict], list[dict]]:
    """容器内未映射变量 / .env 声明总览 / 容器有值但 .env 未声明，+ 派生告警。"""
    findings: list[dict] = []

    # 容器内 EMILY_* 未映射到 Config 字段
    unmapped = sorted(
        k for k in os.environ
        if k.startswith("EMILY_") and k not in ENV_CONFIG_MAP
    )
    unmapped_env = [
        {
            "env": k,
            "sensitive": _is_sensitive_name(k),
            "value": _cell(k, os.environ.get(k)),
            "hint": "未映射到 Config 字段——需先确认消费方（部分变量由代码其它位置直接 os.environ 读取）",
        }
        for k in unmapped
    ]
    if unmapped:
        findings.append({
            "level": "info",
            "kind": "env-unmapped",
            "title": f"{len(unmapped)} 个环境变量未映射到 Config 字段",
            "detail": "、".join(unmapped),
            "next": "需先确认消费方（部分变量由代码其它位置直接 os.environ 读取），确认后再决定是否登记到 bootstrap.ENV_CONFIG_MAP",
        })

    # .env 声明总览
    dotenv_declared = [
        {
            "key": k,
            "sensitive": _is_sensitive_name(k),
            "value": _cell(k, v),
            "injected": k in os.environ,
            "mapped": k in ENV_CONFIG_MAP,
        }
        for k, v in sorted(dotenv.items())
    ]

    # 容器有值但 .env 未声明（compose / 镜像提供）
    env_without_dotenv: list[dict] = []
    if dotenv_ok:
        env_without_dotenv = [
            {
                "env": k,
                "sensitive": _is_sensitive_name(k),
                "value": _cell(k, os.environ.get(k)),
                "mapped": k in ENV_CONFIG_MAP,
            }
            for k in sorted(os.environ)
            if k.startswith("EMILY_") and k not in dotenv
        ]
        if env_without_dotenv:
            findings.append({
                "level": "info",
                "kind": "env-without-dotenv",
                "title": f"{len(env_without_dotenv)} 个变量由 compose / 镜像提供",
                "detail": "、".join(i["env"] for i in env_without_dotenv),
                "next": "如需可配置化：把它提升到 .env，并在 docker-compose-*.yml 的 environment 用 ${VAR:-默认值} 引用",
            })

    return unmapped_env, dotenv_declared, env_without_dotenv, findings


def _build_dotenv_uninjected(dotenv: dict[str, str], dotenv_ok: bool) -> list[dict]:
    """`.env` 声明了内核该读的变量，但容器环境里没有 → 改了不生效。

    只对 ENV_CONFIG_MAP 中的键判定：仅供 nginx / 任务脚本使用的键
    （如 EMILY_DOMAIN / EMILY_SSL_CERT）不属本容器配置，不报警。
    """
    if not dotenv_ok:
        return []
    findings = []
    for key in sorted(dotenv):
        if key in ENV_CONFIG_MAP and key not in os.environ:
            findings.append({
                "level": "warn",
                "kind": "dotenv-uninjected",
                "title": f"{key} 已声明但未注入容器",
                "detail": f"该键映射到 Config 字段 {ENV_CONFIG_MAP[key]}，.env 里写了值，但容器环境里没有——改了不生效。",
                "next": "在 docker-compose-*.yml 的 emily-core.environment 段补上该变量，然后 docker compose up -d emily-core 重建容器（docker restart 不会重读 .env）",
            })
    return findings


def _build_host_files(dotenv_path: Path, dotenv_ok: bool) -> list[dict]:
    items = []
    for spec in _HOST_FILES:
        if spec["mounted"]:
            exists = dotenv_path.is_file()
            size = 0
            if exists:
                try:
                    size = dotenv_path.stat().st_size
                except Exception:
                    size = 0
            items.append({
                "name": spec["name"],
                "container_path": str(dotenv_path),
                "mounted": True,
                "exists": exists,
                "readable": dotenv_ok,
                "size": size,
                "note": spec["note"],
            })
        else:
            items.append({
                "name": spec["name"],
                "container_path": "",
                "mounted": False,
                "exists": False,
                "readable": False,
                "size": 0,
                "note": spec["note"],
            })
    return items


def _restart_hints() -> list[dict]:
    """三类改动的生效方式（C-01 / C-02 是两种不同操作，须分别提示）。"""
    return [
        {
            "kind": "env",
            "title": "改环境变量（.env / compose environment）",
            "action": "docker compose up -d emily-core",
            "detail": "环境变量在容器「创建」时固化，必须重建容器；docker restart 不会重读 .env（C-01）。",
        },
        {
            "kind": "code",
            "title": "改 config.py 默认值",
            "action": "docker restart emily-core",
            "detail": "代码为只读挂载，重启进程即生效（C-02）。",
        },
        {
            "kind": "file",
            "title": "改 /app/config/* 配置文件",
            "action": "docker restart emily-core",
            "detail": "被读取的文件在启动时加载，需重启进程；core_config.json / scheduler_config.json 无代码读取，改它无效（应改 .env）。",
        },
    ]


def build_inventory(config: Config) -> dict:
    """构建配置总清单（只读）。任何单点失败均降级，不抛异常。"""
    fallback_meta = _dataclass_meta()
    source_meta = _parse_config_source()
    dotenv_path = _dotenv_path()
    dotenv, dotenv_ok, dotenv_err = _read_dotenv(dotenv_path)

    field_names = list(fallback_meta.keys())
    fields = _build_fields(config, source_meta, fallback_meta, dotenv, dotenv_ok)
    sections, section_of = _build_sections(field_names)

    for item in fields:
        item["section"] = section_of.get(item["field"], "other")

    files, file_findings = _build_files(config)
    unmapped_env, dotenv_declared, env_without_dotenv, env_findings = _build_env_views(
        dotenv, dotenv_ok
    )

    findings = (
        _build_dotenv_uninjected(dotenv, dotenv_ok)
        + file_findings
        + env_findings
    )

    env_entry_count = sum(1 for f in fields if f["has_env"])
    runtime = {
        "config_source": "环境变量 → Config（bootstrap._config_from_env）",
        "dotenv_path": str(dotenv_path),
        "dotenv_readable": dotenv_ok,
        "dotenv_error": dotenv_err,
        "field_count": len(fields),
        "env_entry_count": env_entry_count,
        "env_map_count": len(ENV_CONFIG_MAP),
        "env_type_sets": {
            "bool": len(ENV_BOOL_FIELDS),
            "int": len(ENV_INT_FIELDS),
            "float": len(ENV_FLOAT_FIELDS),
            "list": len(ENV_LIST_FIELDS),
        },
        "container_env_count": len(os.environ),
        "container_emily_env_count": sum(1 for k in os.environ if k.startswith("EMILY_")),
        "sections_count": len(sections),
        "python_version": platform.python_version(),
        "generated_at": datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
    }

    warn_count = sum(1 for f in findings if f["level"] == "warn")
    info_count = sum(1 for f in findings if f["level"] == "info")

    return {
        "runtime": runtime,
        "sections": sections,
        "fields": fields,
        "files": files,
        "host_files": _build_host_files(dotenv_path, dotenv_ok),
        "unmapped_env": unmapped_env,
        "dotenv_declared": dotenv_declared,
        "env_without_dotenv": env_without_dotenv,
        "findings": findings,
        "finding_summary": {"warn": warn_count, "info": info_count},
        "restart_hint": _restart_hints(),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  单文件内容（只读，按登记名单白名单限名）
# ══════════════════════════════════════════════════════════════════════════════

def allowed_file_names() -> list[str]:
    return [spec["name"] for spec in _CONFIG_FILES]


def read_config_file(name: str) -> tuple[dict | None, str]:
    """读单个已登记配置文件的内容（只读）。

    仅接受登记名单内的文件名，天然拒绝路径穿越；超出上限截断。
    返回 (data, error)。
    """
    spec = next((s for s in _CONFIG_FILES if s["name"] == name), None)
    if spec is None:
        return None, "不在允许的配置文件名单内"

    path = _config_dir() / spec["name"]
    data = {
        "name": spec["name"],
        "path": str(path),
        "exists": path.is_file(),
        "loaded": spec["loaded"],
        "loader": spec["loader"],
        "note": spec["note"],
        "size": 0,
        "mtime": "",
        "content": "",
        "truncated": False,
    }
    if not path.is_file():
        return data, ""

    try:
        st = path.stat()
        data["size"] = st.st_size
        data["mtime"] = datetime.fromtimestamp(st.st_mtime, BEIJING_TZ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return data, f"读取失败：{e}"

    if len(text) > MAX_FILE_CHARS:
        data["content"] = text[:MAX_FILE_CHARS]
        data["truncated"] = True
    else:
        data["content"] = text
    # 兜底：万一文件内出现带口令的连接串，掩掉后再返回
    data["content"] = _redact_credentials(data["content"])
    return data, ""


# ══════════════════════════════════════════════════════════════════════════════
#  写回宿主机 .env（本模块唯一的写盘能力）
#
#  为什么只写 .env：Emily 运行时唯一认的通路是「环境变量 → Config」。
#  emily-data/config/*.json 里有两个文件根本没有代码读取，字段表里的值写到那里
#  必然不生效——那正是本模块要暴露的 P1 问题，不能反过来制造它。
# ══════════════════════════════════════════════════════════════════════════════

def _format_env_value(value: str) -> str:
    """按 .env 语法给值加引号（含空白 / # / 引号 / 反斜杠时需要）。"""
    v = value.strip()
    if v == "":
        return ""
    if re.search(r"""[\s#"'\\]""", v):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return v


def _assign_key(line: str) -> tuple[str, str]:
    """解析一行 .env 赋值，返回 (键, 前缀)。非赋值行返回 ("", "")；前缀保留 export 。"""
    raw = line.strip()
    prefix = ""
    if raw.startswith("export "):
        prefix = "export "
        raw = raw[len("export "):].strip()
    if raw.startswith("#") or "=" not in raw:
        return "", ""
    return raw.split("=", 1)[0].strip(), prefix


def save_env_values(updates: list[dict]) -> dict:
    """把若干环境变量写回宿主机 .env（直接覆写，不做备份）。

    只接受「写回后能生效」的字段，判定与清单里的 saveable 同源；逐项返回结果。
    同名键的所有出现位置都会被改写：.env 里允许重复行，shell 语义取最后一条，
    只改第一条会造成「页面显示已改、实际没变」。
    """
    path = _dotenv_path()
    if not path.is_file():
        return {"ok": False, "written": 0, "results": [],
                "message": f"未找到 {path}，无法写入（.env 是否已挂载？）"}

    try:
        original = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "written": 0, "results": [], "message": f"读取 {path} 失败：{e}"}

    dotenv, dotenv_ok, dotenv_err = _read_dotenv(path)
    if not dotenv_ok:
        return {"ok": False, "written": 0, "results": [], "message": dotenv_err}

    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.split(newline)

    results: list[dict] = []
    accepted: dict[str, str] = {}
    for item in updates:
        field = str(item.get("field", ""))
        value = str(item.get("value", ""))
        env_key = next((k for k, v in ENV_CONFIG_MAP.items() if v == field), "")

        if not env_key:
            results.append({"field": field, "ok": False, "message": "该字段没有环境变量入口，无法写入 .env"})
            continue
        ok, reason, _caveat = _save_state(
            env_key, os.environ.get(env_key), env_key in dotenv, True
        )
        if not ok:
            results.append({"field": field, "env": env_key, "ok": False, "message": reason})
            continue
        err = _validate_env_value(field, value)
        if err:
            results.append({"field": field, "env": env_key, "ok": False, "message": err})
            continue
        accepted[env_key] = _format_env_value(value)
        results.append({"field": field, "env": env_key, "ok": True, "message": "已写入"})

    if not accepted:
        return {"ok": False, "written": 0, "results": results, "message": "没有可写入的项"}

    rewritten: set[str] = set()
    for i, line in enumerate(lines):
        key, prefix = _assign_key(line)
        if key in accepted:
            lines[i] = f"{prefix}{key}={accepted[key]}"
            rewritten.add(key)

    appended = [k for k in accepted if k not in rewritten]
    if appended:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("# ── emy-config 追加（保存后需重建容器：docker compose up -d emily-core）──")
        lines.extend(f"{k}={accepted[k]}" for k in appended)

    try:
        path.write_text(newline.join(lines), encoding="utf-8")
    except Exception as e:
        hint = ""
        if "read-only" in str(e).lower() or "Read-only" in str(e):
            hint = "（.env 当前是只读挂载：把 compose 里的 ./.env:/app/host/.env:ro 去掉 :ro，再 docker compose up -d emily-core 重建容器）"
        for r in results:
            if r.get("ok"):
                r["ok"] = False
                r["message"] = f"写入失败：{e}{hint}"
        return {"ok": False, "written": 0, "results": results, "message": f"写入 {path} 失败：{e}{hint}"}

    written = sum(1 for r in results if r.get("ok"))
    return {
        "ok": True,
        "written": written,
        "path": str(path),
        "updated_keys": sorted(accepted),
        "results": results,
        "message": (
            f"已写入 {written} 项到 {path}。"
            "环境变量在容器创建时固化，必须重建容器才生效：docker compose up -d emily-core"
            "（docker restart 不会重读 .env）。"
        ),
    }
