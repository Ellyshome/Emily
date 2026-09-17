"""SessionPromptService — 会话拉起提示词生成（只读、无副作用）。

口径约束（PRD US-07 / R11）：生成结果与 Emily 实际装配使用的那份
**同模板 + 同变量 + 同能力目录口径**。故本模块不复制内核
`session/loop.py:_build_system_prompt` 的字面量，而是复用其依赖的三源：
  - 模板：`load_prompt("session_loop")`
  - 变量：`SessionContext.get_prompt_variables(actor)`
  - 能力目录：`CapabilityCatalog.list_capabilities(actor, context)`

零副作用：`SessionContext.create` / `SessionDataFetcher.fetch` 均为只读采集，
本模块不写库、不写文件、不留痕、不调用 LLM。

注意：与内核内既有装配形成"两份实现"——这是 C12 约束下的遗留收敛项，
收敛须走内核机制专项立项（见 PRD US-09.3），本模块不改内核。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("emily.service.session_prompt")


def _beijing_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _strip_unfilled(template: str) -> str:
    """移除未渲染的模板占位符（与内核 `loop.py:_strip_unfilled` 同口径）。"""
    return re.sub(r"\{[a-z_]+\}", "", template or "")


class SessionPromptService:
    """一键生成指定人员的会话拉起 system prompt。"""

    @staticmethod
    def generate(user_id: str, core=None) -> dict:
        """生成指定人员的会话拉起提示词全文。

        Args:
            user_id: 目标人员 UUID。
            core: EmilyCore 实例（供 SessionContext / CapabilityCatalog 复用）。

        Returns:
            {"success": bool, "reason_code": str, "prompt": str, "meta": dict}
        """
        from ..infrastructure.llm.prompt_loader import load_prompt
        from ..repositories.user_repo import UserRepository
        from .capability_catalog import CapabilityCatalog
        from .session_context import SessionContext
        from .session_data_fetcher import SessionDataFetcher

        if not user_id:
            return {"success": False, "reason_code": "user_not_found", "prompt": "", "meta": {}}

        user = UserRepository.get_by_id(user_id)
        if user is None:
            return {"success": False, "reason_code": "user_not_found", "prompt": "", "meta": {}}

        template = load_prompt("session_loop") or ""
        if not template:
            return {"success": False, "reason_code": "template_missing", "prompt": "", "meta": {}}

        entries = []
        try:
            context = SessionContext.create(
                user_id=user_id,
                conversation_id="",
                sender_name=getattr(user, "username", "") or "",
                core=core,
            )
            actor = SessionDataFetcher.fetch_actor_snapshot(user_id, core) or {}
            actor["user_id"] = user_id

            catalog = CapabilityCatalog(core=core)
            entries = list(catalog.list_capabilities(actor, context) or [])
            caps = "\n".join(
                f"- {e.name}（{e.kind}）: {e.description}"
                for e in entries
                if getattr(e, "name", "")
            ) or "（暂无可用能力）"

            prompt = template.replace("{capability_catalog}", caps)
            for key, value in (context.get_prompt_variables(actor) or {}).items():
                prompt = prompt.replace(key, str(value) if value else "（无）")
            prompt = prompt.replace("{current_datetime}", _beijing_now_str())
            prompt = _strip_unfilled(prompt)
        except Exception as e:  # noqa: BLE001
            logger.exception("generate session prompt failed: %s", e)
            return {"success": False, "reason_code": "context_error", "prompt": "", "meta": {}}

        return {
            "success": True,
            "reason_code": "",
            "prompt": prompt,
            "meta": {
                "user_id": user_id,
                "username": getattr(user, "username", "") or "",
                "generated_at": _beijing_now_str(),
                "char_count": len(prompt),
                "capability_count": len(entries),
            },
        }
