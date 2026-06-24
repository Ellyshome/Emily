"""GuardianReview —— 轻量核验包装器（M8a）。

对 MasterAgent 的回复和待录入数据进行快速核验。
不是完整的 ReAct Agent，而是单次 LLM 调用。
核验失败时默认为 pass（不阻塞主流程）。
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("emily.agent.guardian_review")


@dataclass
class ReviewResult:
    """核验结果。"""
    passed: bool
    """核验是否通过"""
    findings: str = ""
    """核验发现的问题描述（pass 时为空）"""
    raw_response: str = ""
    """LLM 原始响应（调试用）"""
    elapsed_ms: int = 0
    """耗时（毫秒）"""


class GuardianReview:
    """轻量级守护核验。

    与 GuardianAgent 的区别：
    - GuardianAgent 是多轮 ReAct 循环，可调用 query_data 工具做深度调查
    - GuardianReview 是单次 LLM 调用，只基于传入的数据做判断，不查询数据库
    - GuardianReview 超时 5s，失败时默认 pass（不阻塞主流程）

    使用方式：
        review = GuardianReview(llm_client, config)
        result = await review.review_reply(reply_text, user_message)
        if not result.passed:
            reply_text += "\\n\\n⚠ 守护提醒：" + result.findings
    """

    # 核验 Prompt 模板
    REPLY_REVIEW_PROMPT = """你是 Emy 的质量守护员。请快速检查以下 AI 回复是否存在明显问题。

## 检查维度
1. **事实错误**：回复中的数据是否与已知的用户消息明显矛盾
2. **安全隐患**：是否暴露了系统内部信息或敏感操作
3. **逻辑矛盾**：回复是否存在自相矛盾或明显错误的结论

## 用户消息
{user_message}

## AI 回复
{reply_text}

## 输出格式
仅输出一个 JSON 对象，不要输出任何其他内容：
{{"passed": true/false, "findings": "问题描述"}}

如果没问题：{{"passed": true, "findings": ""}}
如果有问题：{{"passed": false, "findings": "用一句话描述问题"}}
注意：这是 IM 群聊回复，轻微的口语化表达、格式简单不是问题。只检查实质性的数据错误或矛盾。"""

    RECORD_REVIEW_PROMPT = """你是 Emy 的质量守护员。请快速检查以下待录入数据是否存在明显不合理之处。

## 当前时间
{CURRENT_DATETIME}
（注意："未来日期"是指晚于上述当前时间发生的事件。如果事件日期是今天且时间已过，则不是未来日期，不应标记为矛盾。）

## 检查维度
1. **数值合理性**：数字是否明显超出合理范围（如单日铺装 99999 ㎡）
2. **字段矛盾**：各字段之间是否存在矛盾（如日期确实是未来、类型与描述不符）
3. **关键字段缺失**：必填字段是否为空或明显错误

## 录入类型
{tool_name}

## 待录入数据
{data_json}

## 输出格式
仅输出一个 JSON 对象，不要输出任何其他内容：
{{"passed": true/false, "findings": "问题描述"}}

如果数据合理：{{"passed": true, "findings": ""}}
如果有问题：{{"passed": false, "findings": "用一句话描述问题"}}
注意：轻微的不精确（如缺少可选项）不视为问题。只标记明显的实质性错误或矛盾。"""

    # 核验超时（秒）
    REVIEW_TIMEOUT = 5.0

    def __init__(self, llm_client, config=None):
        """初始化核验器。

        Args:
            llm_client: LLMClient 实例（可以是 None，此时所有核验自动 pass）
            config: 全局配置（可选）
        """
        self._llm = llm_client
        self._config = config

    @property
    def is_available(self) -> bool:
        """核验是否可用（需要 LLM 客户端）。"""
        return self._llm is not None

    async def review_reply(
        self,
        reply_text: str,
        user_message: str = "",
    ) -> ReviewResult:
        """核验一条 AI 回复。

        Args:
            reply_text: MasterAgent 生成的回复文本
            user_message: 触发回复的用户消息（用于上下文）

        Returns:
            ReviewResult: 核验结果（不可用时自动返回 pass）
        """
        if not self._llm:
            return ReviewResult(passed=True, findings="", raw_response="no LLM client")

        prompt = self.REPLY_REVIEW_PROMPT.format(
            reply_text=reply_text[:2000],  # 截断过长回复
            user_message=user_message[:500],
        )

        return await self._call_llm(prompt)

    async def review_record(
        self,
        tool_name: str,
        data: dict,
    ) -> ReviewResult:
        """核验一条待录入数据。

        Args:
            tool_name: 工具名称（record_event / record_task / record_meeting / record_file）
            data: 待录入数据字典

        Returns:
            ReviewResult: 核验结果（不可用时自动返回 pass）
        """
        if not self._llm:
            return ReviewResult(passed=True, findings="", raw_response="no LLM client")

        prompt = self.RECORD_REVIEW_PROMPT.format(
            CURRENT_DATETIME=_beijing_now_str(),
            tool_name=tool_name,
            data_json=json.dumps(data, ensure_ascii=False, indent=2),
        )

        return await self._call_llm(prompt)

    async def _call_llm(self, prompt: str) -> ReviewResult:
        """调用 LLM 进行核验（带超时和异常保护）。"""
        start = time.time()
        try:
            # 使用 chat() 单轮调用（非 chat_with_tools），不需要工具
            content = await asyncio.wait_for(
                self._llm.chat(
                    system_prompt="你是一个数据质量检查助手。只输出 JSON，不输出其他内容。",
                    user_message=prompt,
                ),
                timeout=self.REVIEW_TIMEOUT,
            )

            elapsed_ms = int((time.time() - start) * 1000)
            content = content.strip() if content else ""

            # 提取 JSON（有时 LLM 会在 JSON 外加说明文字）
            result = self._parse_json(content)

            return ReviewResult(
                passed=result.get("passed", True),
                findings=result.get("findings", ""),
                raw_response=content,
                elapsed_ms=elapsed_ms,
            )

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.warning(
                "GuardianReview timed out after %.1fs (%dms) — defaulting to pass",
                self.REVIEW_TIMEOUT, elapsed_ms,
            )
            return ReviewResult(
                passed=True,
                findings="",
                raw_response="timeout",
                elapsed_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.warning(
                "GuardianReview LLM call failed (%dms): %s — defaulting to pass",
                elapsed_ms, e,
            )
            return ReviewResult(
                passed=True,
                findings="",
                raw_response=f"error: {e}",
                elapsed_ms=elapsed_ms,
            )

    @staticmethod
    def _parse_json(content: str) -> dict:
        """从 LLM 响应中提取 JSON 对象。"""
        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试提取 {...} 块
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass

        logger.debug("Failed to parse GuardianReview JSON: %s", content[:200])
        return {"passed": True, "findings": ""}


def _beijing_now_str() -> str:
    """返回北京时间字符串，用于核验 prompt 注入。"""
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
