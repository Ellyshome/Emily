"""配置清单（emy-config）—— 只读汇总「代码默认值 · 宿主机 .env 声明 · 容器生效值」。

定位：**观察窗口**，不写盘、不自动重启、不做热重载（PRD：Issues/emy-config/emy-config_PRD_V1.md）。
消费方：`api/routes/config_inventory.py`（GET /api/v1/config/{inventory,file}），页面 `/config`。

三条硬约束（对应 PRD C-04 ~ C-08）：
  1. env → Config 映射只反查 `bootstrap.ENV_CONFIG_MAP`，不另建副本；
  2. 字段说明从 `config.py` 源码解析，不在清单里手抄；
  3. 「文件是否被运行时代码读取」由源码 AST 扫描判定（跳过注释与 docstring），
     不硬编码结论 —— 代码漂移时页面自动跟着变，不做静态断言。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import logging
import os
import platform
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from ..bootstrap import (
    ENV_BOOL_FIELDS,
    ENV_CONFIG_MAP,
    ENV_FLOAT_FIELDS,
    ENV_INT_FIELDS,
    ENV_LIST_FIELDS,
)
from ..config import Config

logger = logging.getLogger("emily.config_inventory")

BEIJING_TZ = timezone(timedelta(hours=8))

# 项目根：services/ → emily_core/ → emily-core/ → Emily/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 宿主机 .env 在容器内的只读挂载点（不参与容器启动，仅用于读「声明值」）
_HOST_ENV_MOUNT = Path("/app/host/.env")


# ── 配置文件登记表 ────────────────────────────────────────────────
# semantics:
#   config-overrides —— 文件内容按「Config 字段覆盖」理解（可做键值冲突校验）
#   declarative      —— 声明式配置文件（键不是 Config 字段，不参与字段级比对）
# not_loaded_hint: 不被运行时代码读取时，页面给出的「应改哪里」指引
_CONFIG_FILES: dict[str, dict] = {
    "core_config.json": {
        "semantics": "config-overrides",
        "not_loaded_hint": "容器内运行时不读取本文件（配置只走「环境变量 → Config」）；"
                           "应改 docker-compose-*.yml 的 environment 段或宿主机 .env",
    },
    "hook_config.json": {
        "semantics": "declarative",
        "not_loaded_hint": "Hook 声明式挂载配置；预期由 emily_core 在启动时读取",
    },
    "mcp_servers.json": {"semantics": "declarative", "not_loaded_hint": "MCP 服务器声明；预期启动时读取"},
    "retrieval_channels.json": {"semantics": "declarative", "not_loaded_hint": "检索通道元数据；预期启动时读取"},
    "retrieval_strategy.json": {"semantics": "declarative", "not_loaded_hint": "检索策略表；预期启动时读取"},
    "scripts_registry.yaml": {"semantics": "declarative", "not_loaded_hint": "脚本注册表；预期启动时读取"},
}

# 宿主机侧声明文件（容器内不可读，仅供指引）
_HOST_DECLARATIONS = (
    (".env", "环境变量声明（密钥 / 渠道参数）—— 经 compose environment 段注入容器"),
    ("docker-compose-napcat.yml", "QQ 渠道部署编排（含 emily-core 的 environment 段）"),
    ("docker-compose-minimal.yml", "最小部署编排（core + postgres）"),
    ("docker-compose-wecom.yml", "企业微信渠道部署编排"),
)

# 密钥类键名（命中即掩码，任何接口都不返回明文）
_SENSITIVE_RE = re.compile(r"(api_key|apikey|password|passwd|secret|token|idkey)", re.IGNORECASE)
# 例外：以 _tokens 结尾的是配额数字（max_tokens / reserve_tokens），不是密钥
_SENSITIVE_EXEMPT_RE = re.compile(r"_tokens$", re.IGNORECASE)

# 源码扫描时跳过的目录（vendored / 非运行时）
_SCAN_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "tests"}

# 单文件内容返回上限（PRD NFR：200k 字符）
_FILE_CONTENT_LIMIT = 200_000


# ── 路径与读取基础 ────────────────────────────────────────────────

def _config_dir() -> Path:
    """容器内 /app/config 优先，开发环境回退 emily-data/config。"""
    container = Path("/app/config")
    if container.is_dir():
        return container
    return _PROJECT_ROOT / "emily-data" / "config"


def _host_env_path() -> Path:
    """宿主机 .env：容器内只读挂载点优先，开发环境回退项目根 .env。"""
    if _HOST_ENV_MOUNT.exists():
        return _HOST_ENV_MOUNT
    return _PROJECT_ROOT / ".env"


def _source_roots() -> list[Path]:
    """运行时代码根目录（用于 AST 扫描「谁读了这份配置」）。"""
    roots = [p for p in (Path("/app/emily_core"), Path("/app/api")) if p.is_dir()]
    if not roots:
        roots = [
            _PROJECT_ROOT / "emily-core" / "emily_core",
            _PROJECT_ROOT / "emily-core" / "api",
        ]
    return [p for p in roots if p.is_dir()]


def _parse_dotenv(text: str) -> dict[str, str]:
    """宽容解析 .env（忽略注释与空行，剥离成对引号）。"""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _is_sensitive(name: str) -> bool:
    name = name or ""
    if _SENSITIVE_EXEMPT_RE.search(name):
        return False
    return bool(_SENSITIVE_RE.search(name))


def _mask(text: str) -> str:
    """密钥展示形式：只暴露「已配置（N 字符）」，用于判断是否配置。"""
    return f"已配置（{len(text)} 字符）" if text else ""


# 匹配「键 = 值」的行（JSON / YAML / .env 三种写法）
_LINE_VALUE_RE = re.compile(
    r'^(?P<prefix>\s*[-#]?\s*["\']?(?P<key>[\w.\-]+)["\']?\s*[:=]\s*)(?P<value>.+?)\s*,?\s*$'
)


def _mask_line(line: str) -> str:
    """把配置文件中密钥类键的值替换为长度提示（防接口回传明文）。"""
    m = _LINE_VALUE_RE.match(line)
    if not m or not _is_sensitive(m.group("key")):
        return line
    raw = m.group("value").strip().strip('"').strip("'")
    return f"{m.group('prefix')}{_mask(raw)}"


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _cell(value, sensitive: bool) -> dict:
    """统一值单元：{present, display, length}。密钥只给长度。"""
    text = _to_text(value)
    if sensitive:
        return {"present": bool(text), "display": _mask(text), "length": len(text)}
    return {"present": text != "", "display": text, "length": len(text)}


def _normalize(value) -> str:
    """比对前归一化：'true'/'True'/'1' 视为同值，数字去尾零。"""
    text = _to_text(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return "true"
    if text in ("false", "0", "no", "off"):
        return "false"
    try:
        return f"{float(text):g}"
    except ValueError:
        return text


# ── 源码证据扫描（AST，跳过注释与 docstring）─────────────────────

@lru_cache(maxsize=1)
def _code_string_index() -> tuple[tuple[str, str, int], ...]:
    """索引运行时代码里的字符串常量 → ((字面量, 相对文件, 行号), ...)。

    用 AST 而非正则：注释天然不在 AST 内，docstring 单独剔除，
    避免把「文档里提到某配置文件」误判为「代码读了它」。
    进程内只算一次（代码为只读挂载，热更新即容器重启）。
    """
    entries: list[tuple[str, str, int]] = []
    self_path = Path(__file__).resolve()
    for root in _source_roots():
        base = root.parent
        for path in sorted(root.rglob("*.py")):
            if any(part in _SCAN_SKIP_DIRS for part in path.parts):
                continue
            # 跳过本模块：登记表里就写着这些文件名，扫到自己会得出「被读取」的假证据
            if path.resolve() == self_path:
                continue
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError, UnicodeDecodeError) as e:  # noqa: BLE001
                logger.debug("config scan skipped %s: %s", path, e)
                continue
            docstrings = set()
            for node in ast.walk(tree):
                # 语句级裸字符串（模块/函数 docstring，及 config.py 那种「字段说明」行）
                # 不构成任何读取，全部剔除
                if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    docstrings.add(id(node.value))
            try:
                rel = str(path.relative_to(base)).replace("\\", "/")
            except ValueError:
                rel = path.name
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value and id(node) not in docstrings):
                    entries.append((node.value, rel, node.lineno))
    return tuple(entries)


def _references(needle: str, limit: int = 5) -> list[dict]:
    """返回源码中引用该字面量的位置（文件:行号），最多 limit 条。"""
    hits = [
        {"file": rel, "line": line}
        for value, rel, line in _code_string_index() if needle in value
    ]
    hits.sort(key=lambda h: (h["file"], h["line"]))
    return hits[:limit]


# ── 字段分组（页面左导航；未归类字段落「其他」）──────────────────

# 有序：(key, title, 显式字段集, 前缀集) —— 显式字段优先命中，前缀兜底
_SECTION_RULES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("llm", "LLM 模型与采样", (
        "llm_api_key", "llm_base_url", "llm_model", "llm_temperature", "llm_max_tokens",
        "llm_router_model", "llm_guardian_model",
    ), ()),
    ("agent_loop", "Agent Loop 与编排", (
        "llm_agent_loop_model", "llm_agent_loop_max_tokens", "llm_context_window_override",
        "llm_compact_reserve_tokens", "llm_compact_keep_recent_tokens", "llm_dynamic_output",
        "agent_loop_max_iterations", "node_retry_max_attempts", "node_retry_initial_interval",
        "node_retry_backoff_factor", "node_timeout_seconds", "node_timeout_overrides",
        "langgraph_max_replan", "langgraph_checkpointer", "orchestrator_enabled",
        "orchestrator_max_depth", "orchestrator_max_dynamic_wis", "session_loop_sop_allowlist",
        "capability_call_timeout_seconds", "checkpoint_resume_window_seconds",
    ), ()),
    ("session", "会话与上下文", (
        "bot_name", "takeover_mode", "session_ttl_seconds", "session_max_concurrent",
        "workitem_max_per_session", "progress_message_template", "default_interaction_channel",
        "test_conv_prefixes",
    ), ()),
    ("kb", "知识库与 RAG", (
        "tei_url", "embedding_mode", "embedding_api_url", "embedding_api_key", "embedding_model",
    ), ("kb_", "rag_")),
    ("vlm", "VLM 视觉", (), ("vlm_",)),
    ("storage_db", "数据库与文件存储", (
        "database_url", "storage_root", "prompts_dir", "pending_issues_path", "hook_config_path",
    ), ()),
    ("log_archive", "日志与归档与记忆", (
        "journal_enabled", "journal_path", "user_memory_enabled", "user_memory_dir",
        "user_memory_max_entries", "session_archive_enabled", "session_archive_dir",
    ), ("log_",)),
    ("email", "邮箱渠道", (), ("email_",)),
    ("permission", "权限与准入", (
        "permission_cache_ttl_seconds", "permission_fail_open", "auto_create_user",
        "auto_create_whitelist", "fallback_basic_tools", "fallback_advanced_write_tools",
        "fallback_admin_min_level",
    ), ()),
)
_SECTION_OTHER = ("other", "其他（仅代码默认值）")


def _classify(field_name: str) -> str:
    for key, _title, explicit, prefixes in _SECTION_RULES:
        if field_name in explicit:
            return key
    for key, _title, _explicit, prefixes in _SECTION_RULES:
        if prefixes and field_name.startswith(prefixes):
            return key
    return _SECTION_OTHER[0]


# ── 源码解析：字段说明（防第二份副本）────────────────────────────

@lru_cache(maxsize=1)
def _field_notes() -> dict[str, str]:
    """从 config.py 源码解析字段说明（紧随字段声明之后的 docstring）。

    约定：字段缩进 4 空格 + `name: type = value`，说明为其后的三引号字符串。
    解析失败即降级为空（说明列留空），不阻断清单生成。
    """
    path = Path(__file__).resolve().parents[1] / "config.py"
    notes: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning("config.py unreadable for notes: %s", e)
        return notes

    header = re.compile(r"^ {4}([a-zA-Z_]\w*)\s*:")
    quote = re.compile(r'^ {4}"""(.*)$')
    idx = 0
    while idx < len(lines):
        m = header.match(lines[idx])
        if not m:
            idx += 1
            continue
        name = m.group(1)
        idx += 1
        q = quote.match(lines[idx]) if idx < len(lines) else None
        if not q:
            continue
        parts = [q.group(1)]
        while not parts[-1].rstrip().endswith('"""') and idx + 1 < len(lines):
            idx += 1
            parts.append(lines[idx].strip())
        text = "\n".join(parts).replace('"""', "").strip()
        notes[name] = re.sub(r"\s*\n\s*", " ", text)
        idx += 1
    return notes


def _field_default(f) -> object:
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        try:
            return f.default_factory()  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return None
    return None


def _type_name(f) -> str:
    annotation = f.type
    return annotation if isinstance(annotation, str) else getattr(annotation, "__name__", str(annotation))


# ── 主流程 ────────────────────────────────────────────────────────

def build_inventory(config: Config) -> dict:
    """生成配置总清单（纯读取，无副作用）。"""
    notes = _field_notes()
    config_dir = _config_dir()
    dotenv_path = _host_env_path()
    dotenv_readable = dotenv_path.is_file()
    dotenv: dict[str, str] = {}
    if dotenv_readable:
        try:
            dotenv = _parse_dotenv(dotenv_path.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            logger.warning("host .env unreadable: %s", e)
            dotenv_readable = False

    container_env = dict(os.environ)
    field_to_env: dict[str, str] = {}
    for env_key, cfg_key in ENV_CONFIG_MAP.items():
        field_to_env.setdefault(cfg_key, env_key)

    # ── 三方对照字段表 ──
    fields: list[dict] = []
    for f in dataclasses.fields(Config):
        name = f.name
        env_key = field_to_env.get(name, "")
        sensitive = _is_sensitive(name) or _is_sensitive(env_key)
        default_value = _field_default(f)
        effective_value = getattr(config, name, default_value)
        declared_raw = dotenv.get(env_key) if env_key else None
        # 空值注入（compose 以 ${VAR:-} 列出但 .env 未设置）等价于「未配置」：
        # 内核 _config_from_env 对空串跳过（列表字段除外，空串 = 显式置空）
        raw_env = container_env.get(env_key) if env_key else None
        injected = bool(env_key) and raw_env is not None and (
            str(raw_env).strip() != "" or name in ENV_LIST_FIELDS
        )

        if injected:
            source = "env"
        elif env_key and env_key in dotenv:
            source = "dotenv-uninjected"
        else:
            source = "default"

        fields.append({
            "field": name,
            "section": _classify(name),
            "type": _type_name(f),
            "note": notes.get(name, ""),
            "env": env_key,
            "has_env": bool(env_key),
            "sensitive": sensitive,
            "restart_required": bool(env_key),
            "default": _cell(default_value, sensitive),
            "declared": _cell(declared_raw, sensitive),
            "effective": _cell(effective_value, sensitive),
            "source": source,
        })

    sections: list[dict] = []
    for key, title, *_ in _SECTION_RULES:
        count = sum(1 for item in fields if item["section"] == key)
        if count:
            sections.append({"key": key, "title": title, "count": count})
    other_count = sum(1 for item in fields if item["section"] == _SECTION_OTHER[0])
    if other_count:
        sections.append({"key": _SECTION_OTHER[0], "title": _SECTION_OTHER[1], "count": other_count})

    # ── 配置文件状态 ──
    files: list[dict] = []
    for name, meta in _CONFIG_FILES.items():
        path = config_dir / name
        exists = path.is_file()
        refs = _references(name) if exists else []
        stat = None
        if exists:
            try:
                stat = path.stat()
            except OSError:
                stat = None
        files.append({
            "name": name,
            "path": str(path),
            "exists": exists,
            "size": stat.st_size if stat else 0,
            "mtime": (datetime.fromtimestamp(stat.st_mtime, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
                      if stat else ""),
            "semantics": meta["semantics"],
            "read_by_runtime": bool(refs),
            "evidence": refs,
            "not_loaded_hint": meta["not_loaded_hint"],
        })

    host_files = [
        {
            "name": name,
            "note": note,
            "readable": dotenv_readable if name == ".env" else False,
            "path": str(dotenv_path) if name == ".env" else name,
        }
        for name, note in _HOST_DECLARATIONS
    ]

    # ── 容器内环境变量：未映射 / 未声明 ──
    mapped_env = set(ENV_CONFIG_MAP)
    unmapped_env = []
    for key in sorted(container_env):
        if not key.startswith("EMILY_") or key in mapped_env:
            continue
        unmapped_env.append({
            "key": key,
            "sensitive": _is_sensitive(key),
            "display": _mask(container_env[key]) if _is_sensitive(key) else container_env[key],
            "consumers": _references(key),
        })

    dotenv_declared = [
        {
            "key": key,
            "mapped": key in mapped_env,
            "mapped_field": ENV_CONFIG_MAP.get(key, ""),
            "injected": key in container_env,
            "sensitive": _is_sensitive(key),
            "display": _mask(dotenv.get(key, "")) if _is_sensitive(key) else dotenv.get(key, ""),
        }
        for key in sorted(dotenv)
    ]

    findings = _build_findings(
        config=config,
        fields=fields,
        files=files,
        dotenv=dotenv,
        container_env=container_env,
        unmapped_env=unmapped_env,
        config_dir=config_dir,
    )

    runtime = {
        "config_source": "环境变量 → Config（emily_core/bootstrap.py:_config_from_env）",
        "config_file_dir": str(config_dir),
        "dotenv_path": str(dotenv_path),
        "dotenv_readable": dotenv_readable,
        "dotenv_mount_hint": "" if dotenv_readable else
            "宿主机 .env 未挂载进容器：在 emily-core 服务 volumes 增加 "
            "'- ./.env:/app/host/.env:ro' 后 docker compose up -d emily-core",
        "python_version": platform.python_version(),
        "field_count": len(fields),
        "env_entry_count": len(ENV_CONFIG_MAP),
        "container_env_count": len(container_env),
        "dotenv_key_count": len(dotenv),
        "generated_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }

    return {
        "runtime": runtime,
        "sections": sections,
        "fields": fields,
        "files": files,
        "host_files": host_files,
        "unmapped_env": unmapped_env,
        "dotenv_declared": dotenv_declared,
        "findings": findings,
        "restart_hint": _restart_hints(),
    }


def _build_findings(*, config: Config, fields: list[dict], files: list[dict],
                    dotenv: dict[str, str], container_env: dict[str, str],
                    unmapped_env: list[dict], config_dir: Path) -> list[dict]:
    """五类差异告警（PRD AC-US-03.1）：warn 优先，逐条给出可执行的下一步。"""
    findings: list[dict] = []
    by_field = {item["field"]: item for item in fields}

    # ① dotenv-uninjected：.env 声明了内核要读的变量，但容器环境里没有
    for env_key, cfg_key in ENV_CONFIG_MAP.items():
        if env_key not in dotenv or env_key in container_env:
            continue
        cell = by_field[cfg_key]["effective"]
        findings.append({
            "level": "warn",
            "kind": "dotenv-uninjected",
            "title": f"{env_key} 已声明但未注入容器",
            "detail": f"宿主机 .env 声明了 {env_key}（映射到 Config.{cfg_key}），"
                      f"但容器环境无此变量，当前生效值为：{cell['display'] or '（空）'}",
            "action": "在三份 docker-compose-*.yml 的 emily-core.environment 段补 "
                      f"'- {env_key}=${{{env_key}:-}}'，再 docker compose up -d emily-core（重建容器）",
        })

    # ② 类型转换登记缺失：设了 env 但类型没登记 → 字符串直接落库（PRD R2）
    type_sets = {
        "bool": ENV_BOOL_FIELDS, "int": ENV_INT_FIELDS,
        "float": ENV_FLOAT_FIELDS, "list": ENV_LIST_FIELDS,
    }
    missing_type_reg = []
    for item in fields:
        if not item["has_env"] or item["type"] not in ("bool", "int", "float", "list"):
            continue
        if not any(item["field"] in names for names in type_sets.values()):
            missing_type_reg.append(f"{item['env']} → {item['field']}（{item['type']}）")
    if missing_type_reg:
        findings.append({
            "level": "warn",
            "kind": "env-type-unregistered",
            "title": f"{len(missing_type_reg)} 个环境变量入口缺少类型转换登记",
            "detail": "、".join(missing_type_reg),
            "action": "在 emily_core/bootstrap.py 的 ENV_BOOL/INT/FLOAT/LIST_FIELDS 中登记对应字段，"
                      "否则环境变量字符串会原样落到 Config（bool(\"false\") 为真）",
        })

    # ③④⑤ file-conflict / file-invalid-key / file-not-loaded
    for item in files:
        if not item["exists"]:
            continue
        if not item["read_by_runtime"]:
            findings.append({
                "level": "warn",
                "kind": "file-not-loaded",
                "title": f"{item['name']} 不被任何运行时代码读取",
                "detail": f"文件存在于 {item['path']}，但源码扫描（跳过注释与 docstring）"
                          f"未发现任何引用，改动它不会生效。",
                "action": item["not_loaded_hint"],
            })
        if item["semantics"] != "config-overrides":
            continue
        payload = _load_structured(config_dir / item["name"])
        if not isinstance(payload, dict):
            continue
        invalid = [k for k in payload if not k.startswith("_") and k not in by_field]
        if invalid:
            findings.append({
                "level": "info",
                "kind": "file-invalid-key",
                "title": f"{item['name']} 有 {len(invalid)} 个非 Config 字段",
                "detail": "、".join(invalid),
                "action": "这些键不会映射到任何 Config 字段（该文件本就不参与运行），可清理",
            })
        conflicts = []
        for key, value in payload.items():
            if key.startswith("_") or key not in by_field:
                continue
            cell = by_field[key]
            if _normalize(value) == _normalize(getattr(config, key, None)):
                continue
            conflicts.append({
                "key": key,
                "file_value": _mask(_to_text(value)) if cell["sensitive"] else _to_text(value),
                "effective_value": cell["effective"]["display"] or "（空）",
            })
        if conflicts:
            findings.append({
                "level": "warn",
                "kind": "file-conflict",
                "title": f"{item['name']} 有 {len(conflicts)} 项与生效值冲突",
                "detail": "；".join(f"{c['key']}: 文件={c['file_value']} ≠ 生效={c['effective_value']}"
                                    for c in conflicts),
                "action": f"该文件不参与运行，冲突仅说明两边不同步；如需改这些项，"
                          f"改 emily-core 的 environment 段或 .env 后重建容器，"
                          f"或删除该文件中已废弃的键以免误导",
                "items": conflicts,
            })

    # ④ env-without-dotenv：容器有值但 .env 未声明（compose / 镜像提供）
    without_dotenv = sorted(k for k in container_env if k.startswith("EMILY_") and k not in dotenv)
    if without_dotenv:
        empty = [k for k in without_dotenv if not container_env[k].strip()]
        detail = "、".join(without_dotenv)
        if empty:
            detail += (f"；其中 {len(empty)} 个为空值（compose 列了入口但 .env 未设置，"
                       f"等同未配置）：{'、'.join(empty)}")
        findings.append({
            "level": "info",
            "kind": "env-without-dotenv",
            "title": f"{len(without_dotenv)} 个 EMILY_* 由 compose / 镜像提供",
            "detail": detail,
            "action": "这些键写死在 docker-compose-*.yml 的 environment 段或镜像内；"
                      "要调整须改对应编排文件并重建容器；空值项在 .env 中赋值即可启用",
        })

    # ⑤ env-unmapped：容器内 EMILY_* 未映射到 Config 字段
    if unmapped_env:
        with_consumer = [item for item in unmapped_env if item["consumers"]]
        detail_parts = []
        for item in sorted(unmapped_env, key=lambda x: x["key"]):
            if item["consumers"]:
                where = "、".join(f"{c['file']}:{c['line']}" for c in item["consumers"])
                detail_parts.append(f"{item['key']}（代码引用：{where}）")
            else:
                detail_parts.append(f"{item['key']}（本次扫描未发现代码引用）")
        findings.append({
            "level": "info",
            "kind": "env-unmapped",
            "title": f"{len(unmapped_env)} 个环境变量未映射到 Config 字段",
            "detail": "；".join(detail_parts),
            "action": "「未发现代码引用」不等于「没人读」——可能由其它服务、"
                      "脚本或运行期 os.environ 读取；如需在 .env 里管理，"
                      "请先确认消费方，再按需补 ENV_CONFIG_MAP 入口",
            "consumed_count": len(with_consumer),
        })

    level_order = {"warn": 0, "info": 1}
    findings.sort(key=lambda f: (level_order.get(f["level"], 9), f["kind"]))
    return findings


def _load_structured(path: Path):
    """读取 JSON / YAML（失败返回 None，不阻断清单）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text)
        except Exception:  # noqa: BLE001
            return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _restart_hints() -> list[dict]:
    """三类改动的生效方式（PRD C-01 / C-02：重建 vs 重启必须区分）。"""
    return [
        {
            "change": "宿主机 .env（声明值）",
            "action": "docker compose -f docker-compose-napcat.yml up -d emily-core",
            "note": "环境变量在容器创建时固化；docker restart 不会重读 .env",
        },
        {
            "change": "emily-data/config/*（配置文件）",
            "action": "docker restart emily-core",
            "note": "配置目录以只读方式挂载进容器，重启即重新读取；"
                    "标注「不被读取」的文件改了无效",
        },
        {
            "change": "emily-core/emily_core/config.py（代码默认值）",
            "action": "docker restart emily-core",
            "note": "代码为只读挂载，重启即生效（bind-mount 下如需清 __pycache__ 另行处理）",
        },
    ]


# ── 单文件内容（只读、白名单、掩码）──────────────────────────────

def read_config_file(name: str) -> dict:
    """读取已登记配置文件内容（只读）。

    文件名白名单限定（拒绝路径穿越）；密钥类键值掩码；超长截断。
    """
    if name not in _CONFIG_FILES:
        raise KeyError(f"配置文件未登记：{name}")
    if "/" in name or "\\" in name or name.startswith("."):
        raise KeyError(f"非法文件名：{name}")

    path = _config_dir() / name
    if not path.is_file():
        return {"name": name, "exists": False, "path": str(path), "content": "", "truncated": False}

    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > _FILE_CONTENT_LIMIT
    if truncated:
        text = text[:_FILE_CONTENT_LIMIT]
    masked = "\n".join(_mask_line(line) for line in text.splitlines())
    return {
        "name": name,
        "exists": True,
        "path": str(path),
        "content": masked,
        "truncated": truncated,
    }


__all__ = ["build_inventory", "read_config_file"]
