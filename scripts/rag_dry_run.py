"""rag_dry_run.py — RAG 基座 dry-run 测试脚本。

直接调用生产入口 handle_knowledge_search，保证"手动能跑通 ⟹ 调用能跑通"。
绕过 LLM 和 4 节点管道，直接打 MaxKB hit_test，输出 JSON 结构化的原始检索结果。

设计原则：测试入口 = 生产入口。
  手动 CLI 测试:  scripts/rag_dry_run.py → handle_knowledge_search(params, rag_provider)
  生产 SOP 调用:  RealExecutor        → handle_knowledge_search(params, rag_provider)
  两者调同一个函数，测试有效性最大化。

MaxKB 凭据自动获取（无需手动设环境变量）：
  - maxkb_url:     env > config，宿主机自动修正 maxkb→localhost
  - admin_password: env > config > MaxKB 默认密码 maxkb123
  - knowledge_id:  env > config > 登录 MaxKB API 自动查询知识库列表

RAG 基座调用失败即反馈失败，不做本地回退兜底（与生产行为一致）。

用法：
    uv run python scripts/rag_dry_run.py "放线验收标准"
    uv run python scripts/rag_dry_run.py "施工工艺" --top-k 8
    uv run python scripts/rag_dry_run.py "钢筋间距" --stage 施工建设 --role 工程部经理
    uv run python scripts/rag_dry_run.py --probe
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rag_dry_run")

# MaxKB 官方默认管理员密码（admin 账号）。若用户已修改，需设 EMILY_MAXKB_ADMIN_PASSWORD 环境变量。
_MAXKB_DEFAULT_PASSWORD = "maxkb123"


def _find_core_config(explicit: str = "") -> Path | None:
    """多级回退查找 core_config.json：--config 参数 → 容器 /app/config → 开发 emily-data。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        logger.warning("--config 指定的路径不存在: %s", explicit)

    candidates = [
        Path("/app/config/core_config.json"),  # 容器内
        _HERE.parent / "emily-data" / "config" / "core_config.json",  # 开发环境
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _load_config(config_path: str = ""):
    """加载配置，复用 bootstrap._config_from_env 合并环境变量（与 bootstrap.init() 一致）。"""
    from emily_core.bootstrap import _config_from_env
    from emily_core.config import Config

    cfg_file = _find_core_config(config_path)
    if cfg_file is None:
        logger.warning("未找到 core_config.json，使用纯环境变量 + 默认值")
        data = {}
    else:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        logger.info("加载配置: %s", cfg_file)

    # 合并环境变量（与 bootstrap.init() 路径完全一致，避免逻辑漂移）
    data = _config_from_env(data)
    return Config.from_dict(data)


def _is_in_container() -> bool:
    """检测是否在容器内运行。"""
    return Path("/.dockerenv").exists() or Path("/app/emily_core").exists()


def _resolve_maxkb_url(config) -> str:
    """解析 MaxKB URL。宿主机环境自动将容器服务名 maxkb 改为 localhost。

    docker-compose 将 MaxKB 8080 端口映射到宿主机，但 config 里默认是
    http://maxkb:8080（容器内服务名），宿主机不可达。
    """
    url = os.environ.get("EMILY_MAXKB_URL") or config.maxkb_url
    if not _is_in_container() and "://maxkb:" in url:
        url = url.replace("://maxkb:", "://localhost:")
    return url


async def _discover_knowledge_id(base_url: str, admin_password: str) -> tuple[str | None, str]:
    """登录 MaxKB，查询第一个知识库的 ID 和名称。

    MaxKB Admin API（与 maxkb_provider.py 同路径）:
      - POST /admin/api/user/login  登录获取 token
      - GET  /admin/api/workspace/default/knowledge  知识库列表

    Returns:
        (knowledge_id, knowledge_name)，失败返回 (None, "")
    """
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # 登录
            async with session.post(
                f"{base_url}/admin/api/user/login",
                json={"username": "admin", "password": admin_password},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning("MaxKB 登录失败: HTTP %d", resp.status)
                    return None, ""
                data = await resp.json()
                token = data.get("data", {}).get("token")
                if not token:
                    logger.warning("MaxKB 登录响应缺少 token: %s", str(data)[:200])
                    return None, ""

            # 查询知识库列表
            async with session.get(
                f"{base_url}/admin/api/workspace/default/knowledge",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning("MaxKB 知识库列表查询失败: HTTP %d", resp.status)
                    return None, ""
                data = await resp.json()

            knowledge_list = data.get("data", [])
            if isinstance(knowledge_list, list) and knowledge_list:
                first = knowledge_list[0]
                return first.get("id", ""), first.get("name", "")
            logger.info("MaxKB 知识库列表为空（未创建任何知识库）")
    except Exception as e:
        logger.warning("MaxKB 知识库自动发现异常: %s", e)
    return None, ""


async def _resolve_credentials(config) -> dict:
    """自动解析 MaxKB 凭据。

    优先级：环境变量 > config 文件 > 默认值/API 自动发现。
    """
    maxkb_url = _resolve_maxkb_url(config)

    admin_password = (
        os.environ.get("EMILY_MAXKB_ADMIN_PASSWORD")
        or config.maxkb_admin_password
        or _MAXKB_DEFAULT_PASSWORD
    )

    knowledge_id = (
        os.environ.get("EMILY_MAXKB_KNOWLEDGE_ID")
        or config.maxkb_knowledge_id
    )

    cred_source = {
        "maxkb_url": (
            "env" if os.environ.get("EMILY_MAXKB_URL")
            else "config" if config.maxkb_url else "default"
        ),
        "admin_password": (
            "env" if os.environ.get("EMILY_MAXKB_ADMIN_PASSWORD")
            else "config" if config.maxkb_admin_password
            else "default(maxkb123)"
        ),
        "knowledge_id": (
            "env" if os.environ.get("EMILY_MAXKB_KNOWLEDGE_ID")
            else "config" if config.maxkb_knowledge_id
            else "auto-discover"
        ),
    }

    # knowledge_id 为空时自动从 MaxKB API 发现
    discovered = {"name": "", "found": False}
    if not knowledge_id:
        kid, kname = await _discover_knowledge_id(maxkb_url, admin_password)
        if kid:
            knowledge_id = kid
            discovered = {"name": kname, "found": True}
            cred_source["knowledge_id"] = f"discovered: {kname}"
            logger.info("自动发现 MaxKB 知识库: %s (id=%s...)", kname, kid[:8])
        else:
            cred_source["knowledge_id"] = "auto-discover(failed)"

    return {
        "maxkb_url": maxkb_url,
        "admin_password": admin_password,
        "knowledge_id": knowledge_id,
        "cred_source": cred_source,
        "discovered": discovered,
    }


def _config_diag(config) -> dict:
    """输出 config 原始配置诊断（密钥脱敏）。"""
    kid = config.maxkb_knowledge_id
    return {
        "maxkb_url_in_config": config.maxkb_url,
        "knowledge_id_in_config": (kid[:8] + "...") if kid else "",
        "search_mode": config.maxkb_search_mode,
        "similarity_threshold": config.maxkb_similarity_threshold,
        "kb_enabled": config.kb_enabled,
        "has_admin_password_in_config": bool(config.maxkb_admin_password),
        "kb_top_k": config.kb_top_k,
    }


async def rag_dry_run(
    query: str = "",
    *,
    top_k: int | None = None,
    stage: str | None = None,
    role: str | None = None,
    probe: bool = False,
    config_path: str = "",
) -> dict:
    """RAG 基座 dry-run 检索。调用生产入口 handle_knowledge_search。

    Returns:
        dict: {"dry_run_meta": {...}, "handler_result": {...}}（probe 模式无 handler_result）
    """
    config = _load_config(config_path)

    # ── 自动解析 MaxKB 凭据 ──
    creds = await _resolve_credentials(config)

    from emily_core.providers.rag.maxkb_provider import MaxKBRagProvider
    rag_provider = MaxKBRagProvider(
        base_url=creds["maxkb_url"],
        admin_password=creds["admin_password"],
        knowledge_id=creds["knowledge_id"],
        search_mode=config.maxkb_search_mode,
        similarity=config.maxkb_similarity_threshold,
    )
    available = await rag_provider.is_available()

    meta = {
        "config": _config_diag(config),
        "resolved_maxkb_url": creds["maxkb_url"],
        "credentials_source": creds["cred_source"],
        "discovered_knowledge": creds["discovered"],
        "available": available,
    }

    # ── probe 模式：仅诊断配置 + 连通性，不走 handler ──
    if probe:
        issues: list[str] = []
        if not creds["admin_password"]:
            issues.append("admin_password 未获取到")
        if not creds["knowledge_id"]:
            issues.append("knowledge_id 未获取到（自动发现失败——检查 MaxKB 是否运行/密码是否正确）")
        if not available and not issues:
            issues.append("is_available()=false（MaxKB 服务不可达或凭据错误）")
        if config.kb_enabled is False:
            issues.append("kb_enabled=false（不影响 dry-run，但生产 bootstrap 不会创建 RAG provider）")
        meta["issues"] = issues
        return {"dry_run_meta": meta}

    # ── 检索模式：调生产入口 handle_knowledge_search ──
    from emily_core.tools.knowledge_search_tool import handle_knowledge_search

    params = {"query": query}
    if top_k is not None:
        params["top_k"] = top_k
    if stage:
        params["stage"] = stage
    if role:
        params["role"] = role

    # 与 RealExecutor 框架直调同一个函数
    handler_result = await handle_knowledge_search(params, rag_provider)

    return {
        "dry_run_meta": meta,
        "handler_result": handler_result,
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="RAG 基座 dry-run 测试（调生产入口 handle_knowledge_search，自动获取 MaxKB 凭据）",
    )
    parser.add_argument("query", nargs="?", default="", help="自然语言查询（--probe 模式可省略）")
    parser.add_argument("--top-k", type=int, default=None,
                        help="返回条数，默认 config.kb_top_k（5），handler 内限上限 10")
    parser.add_argument("--stage", default=None, help="按项目阶段过滤（施工建设/竣工验收等）")
    parser.add_argument("--role", default=None, help="按岗位过滤（工程部经理/设计部经理等）")
    parser.add_argument("--probe", action="store_true",
                        help="仅检查配置 + 连通性，不发检索请求")
    parser.add_argument("--config", default="",
                        help="core_config.json 路径（默认多级回退）")
    args = parser.parse_args()

    result = asyncio.run(rag_dry_run(
        query=args.query,
        top_k=args.top_k,
        stage=args.stage,
        role=args.role,
        probe=args.probe,
        config_path=args.config,
    ))

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
