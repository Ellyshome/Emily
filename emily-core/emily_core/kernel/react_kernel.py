# emily-core/emily_core/kernel/react_kernel.py
"""共享对话循环内核（M2 / US-03）—— ReAct 循环机制的唯一实现。

定位：
  - "提示装配 → 模型调用 → 结果归一 → 消息回填 → 工具执行 → 迭代记账 → 文本纠错"
    这一套机制在本模块实现**一份**；会话编排图与工单编排图各以薄适配层消费（AC-US-03.1）。
  - 内核**不感知调用方**：提示、工具集、工具执行、控制工具、纠错策略、异常分类一律经
    `LoopPorts` 注入；内核内不出现调用方字面量，也不解释调用方语义（AC-US-03.2）。

状态契约（中性键，两图 schema 均需声明）：
  - 沿用的共有键：`messages` / `iteration_count` / `_pending_tool_call`
  - 内核裁决写入：`_kernel_outcome` / `_kernel_text` / `_kernel_error` / `_kernel_text_nudge`
  - 调用方按 outcome 自行映射到本图的下一个节点（路由不在内核里）

裁决值（`decide_next`）：
  `tool`   有待执行调用 → 交调用方去执行
  `retry`  需要再叫一次模型（纠错后重试）
  `final`  回复文本已就绪，可以收口
  `reject` 文本不作为回复，交调用方按终态处理（如转纠错/兜底节点）
  `cap`    达到迭代上限
  `error`  模型调用终态异常
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("emily.kernel.react_kernel")

# ── 中性裁决（outcome）──
OUTCOME_TOOL = "tool"
OUTCOME_RETRY = "retry"
OUTCOME_FINAL = "final"
OUTCOME_REJECT = "reject"
OUTCOME_CAP = "cap"
OUTCOME_ERROR = "error"

# ── 工具执行后的裁决 ──
OUTCOME_CONTINUE = "continue"    # 执行完毕，回模型继续
OUTCOME_TERMINAL = "final"       # 控制工具宣告工作完成
OUTCOME_SUSPEND = "suspend"      # 控制工具宣告需要用户补充信息

#: 异常分类返回值
ERROR_TRANSIENT = "transient"    # 上抛 → 交给框架的节点级重试（M3 / US-02）
ERROR_FATAL = "fatal"            # 内核转终态，交调用方兜底


def default_classify_error(exc: BaseException) -> str:
    """默认异常分类：瞬时故障（超时/连接/传输）判 transient，其余判 fatal。

    瞬时故障**上抛**给框架的节点级重试策略处理（US-02）；终态故障由内核转结构化结果，
    避免把不可重试的错误反复打给模型。
    """
    if isinstance(exc, (TimeoutError, ConnectionError, ConnectionResetError, OSError)):
        return ERROR_TRANSIENT
    text = f"{type(exc).__name__}: {exc}".lower()
    transient_markers = (
        "timeout", "timed out", "connection", "connect", "temporarily", "rate limit",
        "429", "500", "502", "503", "504", "server error", "overloaded", "unavailable",
        "read error", "reset by peer", "proxy",
    )
    if any(m in text for m in transient_markers):
        return ERROR_TRANSIENT
    return ERROR_FATAL


@dataclass
class LoopKeys:
    """中性键名（默认与两图现有键名一致，可覆盖）。"""

    messages: str = "messages"
    iteration: str = "iteration_count"
    pending: str = "_pending_tool_call"
    outcome: str = "_kernel_outcome"
    text: str = "_kernel_text"
    error: str = "_kernel_error"
    nudge: str = "_kernel_text_nudge"


@dataclass
class NudgeOutcome:
    """文本纠错裁决。

    Attributes:
        retry_text: 非空 → 作为纠错提示追加进对话并重试
        reject: True → 该文本不作为回复，交调用方按终态处理（与 retry_text 互斥，优先 reject）
    """

    retry_text: str = ""
    reject: bool = False


@dataclass
class LoopPorts:
    """循环的全部差异点（调用方注入）。

    Attributes:
        llm_call: `async (messages, tool_specs) -> dict`，返回
            `{"type": "tool_call"|"text", "content", "tool_name", "tool_arguments",
              "tool_call_id", "reasoning_content"}`
        tool_specs: 当前可见工具集供给
        prompt: 系统提示构建（首轮装配用）
        history: 会话/工单历史消息（首轮装配用）
        user_input: 本轮用户输入（首轮装配用）
        execute: `async (name, arguments) -> dict`，执行非控制类调用
        max_iterations: 迭代上限
        text_nudge: `(result, attempt) -> NudgeOutcome | None`，模型返回文本（或空结果）而非
            工具调用时的纠错策略；返回 None 表示按文本收口，`retry_text` 表示纠错重试，
            `reject` 表示该文本不作为回复
        control_tools: `{name: async (name, args, state) -> dict}`，控制类工具处理
            （返回 `{"outcome": 裁决, "tool_message": str, "extra_messages": list, "patch": dict}`）
        classify_error: 异常分类；返回 transient 时**上抛**交框架重试
        on_llm_result: 结果记账钩子（如调用计数），不参与裁决
        context_recovery: `async (exc) -> list | None`，上下文溢出恢复；
            返回重建后的消息列表则重试一次，返回 None 则按异常处理
    """

    llm_call: "Callable[[list, list], Awaitable[dict]] | None" = None
    tool_specs: Callable[[], list] = lambda: []
    prompt: Callable[[], str] = lambda: ""
    history: Callable[[], list] = lambda: []
    user_input: Callable[[], str] = lambda: ""
    execute: "Callable[[str, dict], Awaitable[dict]] | None" = None
    max_iterations: Callable[[], int] = lambda: 12
    text_nudge: "Callable[[dict, int], NudgeOutcome | None] | None" = None
    control_tools: dict = field(default_factory=dict)
    classify_error: "Callable[[Exception], str] | None" = None
    on_llm_result: "Callable[[dict], None] | None" = None
    context_recovery: "Callable[[Exception], Awaitable[list | None]] | None" = None


# ══════════════════════════════════════════════════════════════════════════════
# 模型步
# ══════════════════════════════════════════════════════════════════════════════


def assemble_messages(*, ports: LoopPorts) -> list:
    """首轮消息装配：系统提示 + 历史 + 本轮用户输入。"""
    messages: list = []
    system_prompt = ports.prompt() or ""
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(ports.history() or [])
    messages.append({"role": "user", "content": ports.user_input()})
    return messages


def _classify(exc: Exception, ports: LoopPorts) -> str:
    if ports.classify_error is None:
        return ERROR_FATAL
    try:
        return str(ports.classify_error(exc) or ERROR_FATAL)
    except Exception:  # noqa: BLE001 — 分类器自身异常按终态处理
        return ERROR_FATAL


async def llm_step(state: dict, *, ports: LoopPorts, keys: LoopKeys = LoopKeys()) -> dict:
    """单轮模型调用：装配 → 调用 → 归一 → 记账，返回中性补丁。"""
    if ports.llm_call is None:
        raise RuntimeError("react kernel: llm_call 端口未注入")
    messages = list(state.get(keys.messages) or [])
    iteration = int(state.get(keys.iteration, 0) or 0)

    if not messages:
        messages = assemble_messages(ports=ports)

    max_iter = int(ports.max_iterations() or 12)
    if iteration >= max_iter:
        logger.warning("react kernel: iteration_count=%d >= cap=%d", iteration, max_iter)
        return {keys.messages: messages, keys.iteration: iteration,
                keys.outcome: OUTCOME_CAP}

    tool_specs = list(ports.tool_specs() or [])
    result: "dict | None" = None
    try:
        result = await ports.llm_call(messages, tool_specs)
    except Exception as exc:  # noqa: BLE001 — 分类后决定上抛或转终态
        failure = exc
        recovered = None
        if ports.context_recovery is not None:
            try:
                recovered = await ports.context_recovery(exc)
            except Exception as rec_exc:  # noqa: BLE001 — 恢复失败按原异常处理
                logger.warning("react kernel: context recovery failed: %s", rec_exc)
                recovered = None
        if recovered is not None:
            # 上下文溢出闭环：重建消息后重试一次
            messages = list(recovered)
            try:
                result = await ports.llm_call(messages, tool_specs)
            except Exception as exc2:  # noqa: BLE001
                failure = exc2
        if result is None:
            if _classify(failure, ports) == ERROR_TRANSIENT:
                logger.warning("react kernel: transient model failure, raised for framework retry: %s",
                               failure)
                raise failure
            logger.error("react kernel: model call failed (fatal): %s", failure, exc_info=True)
            return {keys.messages: [], keys.pending: {},
                    keys.outcome: OUTCOME_ERROR,
                    keys.error: {"root_cause": f"模型调用异常: {failure}", "detail": repr(failure)},
                    keys.iteration: iteration}
    if ports.on_llm_result is not None:
        try:
            ports.on_llm_result(result if isinstance(result, dict) else {})
        except Exception as e:  # noqa: BLE001 — 记账失败不影响循环
            logger.debug("react kernel: on_llm_result failed: %s", e)

    result = result if isinstance(result, dict) else {}
    rtype = str(result.get("type") or "")

    if rtype == "tool_call":
        assistant_msg: dict = {
            "role": "assistant",
            "content": result.get("content") or "",
            "tool_calls": [{
                "id": str(result.get("tool_call_id") or ""),
                "type": "function",
                "function": {
                    "name": str(result.get("tool_name") or ""),
                    "arguments": json.dumps(result.get("tool_arguments") or {}, ensure_ascii=False),
                },
            }],
        }
        # DeepSeek reasoner 要求 reasoning_content 作为独立字段回传（合并进 content 会 400）
        if result.get("reasoning_content"):
            assistant_msg["reasoning_content"] = result["reasoning_content"]
        messages.append(assistant_msg)
        return {
            keys.messages: messages,
            keys.iteration: iteration + 1,
            keys.nudge: 0,
            keys.outcome: OUTCOME_TOOL,
            keys.pending: {
                "id": str(result.get("tool_call_id") or ""),
                "name": str(result.get("tool_name") or ""),
                "arguments": dict(result.get("tool_arguments") or {}),
            },
        }

    # 文本分支（rtype == "text" 或空结果）
    content = str(result.get("content") or "")
    text_msg: dict = {"role": "assistant", "content": content}
    if result.get("reasoning_content"):
        text_msg["reasoning_content"] = result["reasoning_content"]
    messages.append(text_msg)

    attempt = int(state.get(keys.nudge, 0) or 0) + 1
    nudge = None
    if ports.text_nudge is not None:
        nudge = ports.text_nudge(result, attempt)

    if nudge is not None and nudge.reject:
        logger.warning("react kernel: text rejected at attempt %d by policy", attempt)
        return {keys.messages: messages, keys.iteration: iteration + 1,
                keys.nudge: attempt, keys.outcome: OUTCOME_REJECT, keys.text: content}

    if nudge is not None and nudge.retry_text:
        messages.append({"role": "user", "content": nudge.retry_text})
        return {keys.messages: messages, keys.iteration: iteration + 1,
                keys.nudge: attempt, keys.outcome: OUTCOME_RETRY,
                keys.pending: {}}

    return {keys.messages: messages, keys.iteration: iteration + 1,
            keys.nudge: attempt, keys.outcome: OUTCOME_FINAL, keys.text: content}


# ══════════════════════════════════════════════════════════════════════════════
# 工具步
# ══════════════════════════════════════════════════════════════════════════════


async def tool_step(state: dict, *, ports: LoopPorts, keys: LoopKeys = LoopKeys()) -> dict:
    """执行待处理调用：控制类交注入处理器，其余走执行端口；结果回填为 tool 消息。"""
    call = dict(state.get(keys.pending) or {})
    name = str(call.get("name") or "")
    arguments = dict(call.get("arguments") or {})
    call_id = str(call.get("id") or "")
    messages = list(state.get(keys.messages) or [])

    handler = (ports.control_tools or {}).get(name)
    if handler is not None:
        out = await handler(name, arguments, state) or {}
        tool_message = str(out.get("tool_message") or "")
        if tool_message:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_message})
        for extra in out.get("extra_messages") or []:
            if isinstance(extra, dict):
                messages.append(extra)
        patch = dict(out.get("patch") or {})
        patch.update({keys.messages: messages, keys.pending: {},
                      keys.outcome: str(out.get("outcome") or OUTCOME_CONTINUE)})
        return patch

    if ports.execute is None:
        payload: dict = {"success": False, "reply": f"工具 '{name}' 不可用"}
    else:
        raw = await ports.execute(name, arguments)
        payload = raw if isinstance(raw, dict) else {"success": False, "reply": "工具返回了非结构化结果"}

    messages.append({"role": "tool", "tool_call_id": call_id,
                     "content": json.dumps(payload, ensure_ascii=False, default=str)})
    return {keys.messages: messages, keys.pending: {}, keys.outcome: OUTCOME_CONTINUE}


def decide_next(state: dict, *, keys: LoopKeys = LoopKeys()) -> str:
    """取出内核裁决（路由映射留给调用方）。"""
    return str(state.get(keys.outcome) or "")


def build_error_patch(*, keys: LoopKeys = LoopKeys(), root_cause: str,
                      **extra: Any) -> dict:
    """构造终态异常补丁（供调用方在不经模型步时复用同一契约）。"""
    patch = {keys.outcome: OUTCOME_ERROR, keys.error: {"root_cause": root_cause}}
    patch.update(extra)
    return patch
