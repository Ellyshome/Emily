"""RealGuardian — 轻量 LLM 输出审核。只标记不拦截，并发非阻塞。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("emily.guardian")


# ══════════════════════════════════════════════════════════════════════════════
# System Prompts（从 prompt 文件加载，首次调用时缓存）
# ══════════════════════════════════════════════════════════════════════════════

def _load_guardian_prompt(name: str) -> str:
    """加载 Guardian system prompt（带缓存）。"""
    from emily_core.infrastructure.llm.prompt_loader import load_prompt
    return load_prompt(name)


_STEP_REVIEW_PROMPT = None   # 惰性加载
_REPLY_REVIEW_PROMPT = None  # 惰性加载


def _get_step_review_prompt() -> str:
    global _STEP_REVIEW_PROMPT
    if _STEP_REVIEW_PROMPT is None:
        _STEP_REVIEW_PROMPT = _load_guardian_prompt("guardian_step")
    return _STEP_REVIEW_PROMPT


def _get_reply_review_prompt() -> str:
    global _REPLY_REVIEW_PROMPT
    if _REPLY_REVIEW_PROMPT is None:
        _REPLY_REVIEW_PROMPT = _load_guardian_prompt("guardian_reply")
    return _REPLY_REVIEW_PROMPT


# ══════════════════════════════════════════════════════════════════════════════
# Data Class
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class GuardianNote:
    """审核发现的问题标记。"""
    source: str = ""  # "step:step-02" | "reply"
    issues: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# RealGuardian
# ══════════════════════════════════════════════════════════════════════════════

class RealGuardian:
    """轻量输出审核 —— 单次 LLM chat_json，只标记不拦截。

    设计原则：
    - LLM 可用则自动启用；不可用时 review_*() 返回 None（静默跳过）
    - 只返回问题文本，不做 PASS/FLAG/REJECT 三态决策
    - node3 中通过 asyncio.create_task() 并进执行，不阻塞下一步
    """

    def __init__(self, llm_client: Any, config: Any = None) -> None:
        self._llm = llm_client
        self._config = config

    # ── node3 陪跑：审核单个步骤 ──

    async def review_step(self, step_result: Any) -> GuardianNote | None:
        """审核单个 StepResult。返回问题列表或 None。

        作为 asyncio.create_task() 的目标函数使用，
        在后台与下一步执行并行跑。
        """
        if not self._llm:
            return None
        # 无实质数据则跳过——guardian 三维度（虚构数据/错误引用/逻辑矛盾）
        # 全部依赖工具返回或 RAG 引用，无数据时必然返回空列表，徒耗 token。
        # 此兜底与 node3_execute 调用点过滤逻辑一致，双重守门。
        if not (getattr(step_result, "tool_calls", None)
                or getattr(step_result, "rag_results", None)
                or getattr(step_result, "db_results", None)):
            return None
        prompt = self._build_step_prompt(step_result)
        from ...infrastructure.logging.llm_logger import LLMInteractionLogger
        prev_category = LLMInteractionLogger._current_context.get("call_category", "")
        LLMInteractionLogger.set_category("guardian")
        try:
            guardian_model = getattr(self._llm, "guardian_model", None) or getattr(self._llm, "model", None)
            data = await self._llm.chat_json(
                prompt,
                f"审核步骤: {getattr(step_result, 'step_id', '?')}",
                model=guardian_model,
            )
            issues: list[str] = data.get("issues", []) if isinstance(data, dict) else []
            if issues:
                return GuardianNote(
                    source=f"step:{getattr(step_result, 'step_id', '?')}",
                    issues=issues,
                )
            return None
        except Exception as e:
            logger.debug("Guardian review_step failed (silent skip): %s", e)
            return None
        finally:
            LLMInteractionLogger.set_category(prev_category)

    # ── node4 出站：审核最终回复 ──

    async def review_reply(self, draft_reply: str, work_item: Any) -> GuardianNote | None:
        """审核最终回复草稿。返回问题列表或 None。

        node4 是最后一个节点，此处直接 await（无需后台化）。
        """
        if not self._llm or not draft_reply:
            return None
        prompt = self._build_reply_prompt(draft_reply, work_item)
        from ...infrastructure.logging.llm_logger import LLMInteractionLogger
        prev_category = LLMInteractionLogger._current_context.get("call_category", "")
        LLMInteractionLogger.set_category("guardian")
        try:
            guardian_model = getattr(self._llm, "guardian_model", None) or getattr(self._llm, "model", None)
            data = await self._llm.chat_json(
                prompt,
                f"审核回复: {draft_reply[:100]}",
                model=guardian_model,
            )
            issues: list[str] = data.get("issues", []) if isinstance(data, dict) else []
            if issues:
                return GuardianNote(source="reply", issues=issues)
            return None
        except Exception as e:
            logger.debug("Guardian review_reply failed (silent skip): %s", e)
            return None
        finally:
            LLMInteractionLogger.set_category(prev_category)

    # ── prompt 构建 ──

    def _build_step_prompt(self, sr: Any) -> str:
        """构建 step 审核的 system prompt + 内嵌 user message。

        将上下文信息直接 format 进 system prompt，user_message 仅为简短标签。
        """
        output = (getattr(sr, "output", "") or "")[:1500]
        tool_info = ""
        for tc in getattr(sr, "tool_calls", []) or []:
            tool_info += (
                f"工具: {getattr(tc, 'tool_name', '?')}\n"
                f"输入: {json.dumps(getattr(tc, 'tool_input', {}), ensure_ascii=False)}\n"
                f"输出: {json.dumps(getattr(tc, 'tool_output', {}), ensure_ascii=False)}\n"
            )
        rag_info = ""
        for rr in getattr(sr, "rag_results", []) or []:
            for chunk in (getattr(rr, "chunks", []) or [])[:3]:
                rag_info += (
                    f"引用: 《{getattr(chunk, 'doc_name', '?')}》"
                    f"{getattr(chunk, 'content', '')[:200]}\n"
                )

        return _get_step_review_prompt().format(
            step_id=getattr(sr, "step_id", "?"),
            output=output,
            tool_info=tool_info[:2000],
            rag_info=rag_info[:1000],
        )

    def _build_reply_prompt(self, draft: str, wi: Any) -> str:
        """构建 reply 审核的 system prompt + 内嵌 user message。"""
        sop_id = getattr(wi, "sop_id", "") or "无"
        user_input = (getattr(wi, "user_input", "") or "")[:500]
        steps_summary = ""
        for sr in getattr(wi, "step_results", []) or []:
            steps_summary += (
                f"[{getattr(sr, 'step_id', '?')}] "
                f"{'OK' if getattr(sr, 'success', True) else 'FAIL'} "
                f"{(getattr(sr, 'output', '') or '')[:200]}\n"
            )

        return _get_reply_review_prompt().format(
            draft_reply=draft[:2000],
            sop_id=sop_id,
            user_input=user_input,
            steps_summary=steps_summary[:1500],
        )

    # ── prompt 字符数追踪（供归档使用）──

    def step_prompt_chars(self, sr: Any) -> int:
        """返回 step 审核 prompt 渲染后字符数（供归档追踪，不暴露 prompt 内容）。"""
        try:
            return len(self._build_step_prompt(sr))
        except Exception:
            return 0

    def reply_prompt_chars(self, draft: str, wi: Any) -> int:
        """返回 reply 审核 prompt 渲染后字符数。"""
        try:
            return len(self._build_reply_prompt(draft, wi))
        except Exception:
            return 0
