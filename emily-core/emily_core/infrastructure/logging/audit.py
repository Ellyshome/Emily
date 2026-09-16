"""留痕门面 —— 统一上下文、动作记录与三态判定。

设计依据：`issues/操作留痕治理/操作留痕治理_计划_V1.md` §2

三条纪律：
1. **挂载点唯一**：留痕只在本模块产生（经 `@audited` 挂到动作类 service 的公开方法）。
   入口层（api/routes、application、scripts）只注入上下文，不写留痕。
2. **入口只注入**：`bind_audit_context()` 由四类入口各调一次，下游动作自动携带。
3. **非阻断**：写入失败只告警并计数（`audit_write_stats()` 供体检项读取），不影响业务返回。

三态判定以「统一解释层」实现（`classify_result`），不要求各 service 改返回签名 ——
105 个写方法中仅 35 个返回可机械判定的结构，其余按"未抛异常即成功"处理。
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

logger = logging.getLogger("emily.audit")

# ── 流量性质（source）取值域 —— 语义是"这条留痕属于哪类行为"，不是"从哪个入口进来" ──
SOURCE_USER = "user"        # 真实用户行为（IM 渠道）
SOURCE_OPS = "ops"          # 人发起的运维操作（console 直接操作、脚本 CLI）
SOURCE_AUTO = "auto"        # 无人触发的系统行为（调度、守护、进化、归档）
SOURCE_TEST = "test"        # 测试流量（console 模拟对话）
VALID_SOURCES = (SOURCE_USER, SOURCE_OPS, SOURCE_AUTO, SOURCE_TEST)

# ── 三态 ──
RESULT_SUCCEEDED = "succeeded"
RESULT_FAILED = "failed"
RESULT_REJECTED = "rejected"

# 拒绝码表：命中即判为"被拒"（动作已开始后的规则/权限拒绝），否则为"失败"。
# 口径见需求基线 §四.5；新增条目须按该口径评审。表可扩。
#
# 码值来源（已核实）：
#   file_service:  permission_denied / invalid_confidentiality / file_not_found（规则与权限拒绝）
#                  update_failed 属真失败，故不入表
#   node_service:  40301 仅建设单位人员可创建 / 40302 仅管理员或责任人可变更责任人 /
#                  40303 仅责任人、管理员或同单位可提交成果（权限拒绝）
#                  40001 依赖自己、循环依赖、自挂父子 / 40002 责任人不存在、子节点达上限（规则拒绝）
#   file_manager:  link_to_master / unlink_attachment / update_purpose 的规则拒绝（见各方法 reason_code）
#
# 局限：返回值只带中文文案、无机器码的动作点无法机械判定，一律记 failed ——
# 要精确化需在该动作点补 `reason_code`（见计划 §2.2）。
REJECT_CODES = frozenset({
    # file_service
    "permission_denied",
    "invalid_confidentiality",
    "file_not_found",
    # node_service（权限）
    "40301",
    "40302",
    "40303",
    # node_service（规则）
    "40001",
    "40002",
    # file_manager（附件链规则）
    "invalid_nesting",
    "self_reference",
    "invalid_master",
})

# String(500) 上限：log_writer 的截断规则不覆盖 summary / error_reason，此处自行收口
_MAX_SUMMARY = 500
_MAX_REASON = 500
_MAX_FIELD = 200

_ENV_SOURCE = "EMILY_AUDIT_SOURCE"
_ENV_CHANNEL = "EMILY_AUDIT_CHANNEL"
_ENV_CHANNEL_ACCOUNT = "EMILY_AUDIT_CHANNEL_ACCOUNT"


@dataclass(frozen=True)
class AuditContext:
    """一次请求/动作的留痕上下文。"""

    source: str = SOURCE_AUTO
    channel: str = ""
    channel_account: str = ""
    actor_id: str = ""
    actor_name: str = ""


_audit_ctx: ContextVar[Optional[AuditContext]] = ContextVar("emily_audit_context", default=None)

# 写入失败可观测（FR-12）：计数 + 最后一次错误
_write_stats: dict[str, Any] = {"failures": 0, "last_error": ""}


def _norm_source(source: str) -> str:
    return source if source in VALID_SOURCES else SOURCE_AUTO


def _env_context() -> AuditContext:
    """环境变量回退上下文（脚本 subprocess 通道，见计划 §2.3）。"""
    return AuditContext(
        source=_norm_source(os.environ.get(_ENV_SOURCE, "") or ""),
        channel=os.environ.get(_ENV_CHANNEL, "") or "",
        channel_account=os.environ.get(_ENV_CHANNEL_ACCOUNT, "") or "",
    )


def bind_audit_context(
    *,
    source: str = "",
    channel: str = "",
    channel_account: str = "",
    actor_id: str = "",
    actor_name: str = "",
    override: bool = True,
) -> Optional[Token]:
    """绑定留痕上下文（入口职责，各入口调一次）。

    Args:
        override: True=覆盖已有绑定（console 模拟对话用它把 ops 改成 test）；
                  False=已绑定则不覆盖（IM 入口的兜底语义，避免覆盖 console 先绑的 test）。

    Returns:
        Token，供 `reset_audit_context()` 复位；未发生绑定时返回 None。
    """
    current = _audit_ctx.get()
    if current is not None and not override:
        return None
    base = current if current is not None else _env_context()
    new_ctx = replace(
        base,
        source=_norm_source(source) if source else base.source,
        channel=channel or base.channel,
        channel_account=channel_account or base.channel_account,
        actor_id=actor_id or base.actor_id,
        actor_name=actor_name or base.actor_name,
    )
    return _audit_ctx.set(new_ctx)


def reset_audit_context(token: Optional[Token]) -> None:
    """复位上下文（与 bind 配对，放在 finally）。"""
    if token is None:
        return
    try:
        _audit_ctx.reset(token)
    except Exception:  # 跨 task 复位等边界情况，忽略
        pass


def current_audit_context() -> AuditContext:
    """读取当前上下文；未绑定时回退环境变量（脚本 subprocess 通道），再回退 auto。"""
    return _audit_ctx.get() or _env_context()


def declare_script_source(script_name: str = "") -> None:
    """脚本入口声明（FR-7 的脚本入口注入）。

    经 Web 控制台 / `scriptmgr run` 执行时，`ScriptManager` 已注入环境变量；
    直接 `uv run python scripts/xxx.py` 时无环境变量，故脚本在 `main()` 首行调用本函数声明来源。

    Args:
        script_name: 脚本名（留空则取 `sys.argv[0]` 的文件名）
    """
    import sys
    from pathlib import Path

    name = script_name or Path(sys.argv[0] or "").stem or "script"
    try:
        bind_audit_context(source=SOURCE_OPS, channel_account=f"cli:{name}", override=True)
    except Exception as e:  # 非阻断
        logger.debug("declare_script_source failed: %s", e)


def _from_success(success: bool, code: str = "") -> tuple[str, str]:
    if success:
        return RESULT_SUCCEEDED, ""
    if code and code in REJECT_CODES:
        return RESULT_REJECTED, code
    return RESULT_FAILED, code


def _result_code(ret: dict) -> str:
    """从返回字典中取机器码（优先 reason_code / error_code，最后才用文案型 error）。"""
    for key in ("reason_code", "error_code"):
        val = ret.get(key)
        if val:
            return str(val)
    return str(ret.get("error") or "")


def classify_result(ret: Any) -> tuple[str, str]:
    """把动作方法的返回值归入三态，返回 (result, error_reason)。

    判定优先级（见计划 §2.2）：异常在外层处理 → dict含success / NodeOperationResult
    → list[dict] 逐项 success → bool → 其余视为成功（无失败信号）。
    """
    if isinstance(ret, bool):
        return _from_success(ret)

    if isinstance(ret, dict) and "success" in ret:
        return _from_success(bool(ret.get("success")), _result_code(ret))

    if isinstance(ret, (list, tuple)) and ret and all(
        isinstance(item, dict) and "success" in item for item in ret
    ):
        total = len(ret)
        ok = sum(1 for item in ret if item.get("success"))
        if ok == total:
            return RESULT_SUCCEEDED, ""
        return RESULT_FAILED, f"{total - ok}/{total} items failed"

    success_attr = getattr(ret, "success", None)
    if isinstance(success_attr, bool):  # NodeOperationResult
        return _from_success(success_attr, str(getattr(ret, "error_code", "") or ""))

    return RESULT_SUCCEEDED, ""


def _pipeline_run_id() -> str:
    """沿用既有运行标识（workitem/scheduler.py set_context 写入）。"""
    try:
        from .business_event_logger import BusinessEventLogger

        return BusinessEventLogger._current_context.get("pipeline_run_id", "")
    except Exception:
        return ""


def record_action(
    *,
    category: str,
    action: str,
    target_type: str = "",
    target_id: str = "",
    target_no: str = "",
    summary: str = "",
    result: str = RESULT_SUCCEEDED,
    error_reason: str = "",
    detail_json: str = "",
    project_id: str = "",
    actor_id: str = "",
    actor_name: str = "",
) -> None:
    """写入一条动作留痕（唯一挂载点）。非阻断：失败只告警 + 计数。"""
    ctx = current_audit_context()
    actor_id = str(actor_id or ctx.actor_id or "unknown")
    actor_name = str(actor_name or ctx.actor_name or ("unknown" if actor_id == "unknown" else ""))

    try:
        from ..database.models import BusinessEventLog
        from .log_writer import EvolutionLogWriter

        EvolutionLogWriter.write_sync(
            BusinessEventLog,
            project_id=project_id,
            user_id=actor_id,
            user_name=actor_name[:_MAX_FIELD],
            event_category=category,
            event_action=action,
            target_type=target_type,
            target_id=str(target_id or "")[:_MAX_FIELD],
            target_no=str(target_no or "")[:50],
            summary=str(summary or "")[:_MAX_SUMMARY],
            detail_json=detail_json,
            pipeline_run_id=_pipeline_run_id(),
            source=_norm_source(ctx.source),
            channel=str(ctx.channel or "")[:30],
            channel_account=str(ctx.channel_account or "")[:_MAX_FIELD],
            result=result,
            error_reason=str(error_reason or "")[:_MAX_REASON],
        )
    except Exception as e:  # 非阻断：留痕失败不影响业务
        _write_stats["failures"] = int(_write_stats.get("failures", 0)) + 1
        _write_stats["last_error"] = str(e)[:300]
        logger.warning("audit.record_action failed action=%s: %s", action, e)


def audit_write_stats() -> dict:
    """留痕写入失败的计数与最后一次错误（供 self_check 体检项读取）。"""
    return dict(_write_stats)


def _resolve_path(container: Any, path: str) -> str:
    """按点号路径取值（支持 "cmd.creator_id" 这类嵌套；取不到返回空串）。"""
    current = container
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return ""
    return str(current) if current is not None else ""


def _extract(sig: inspect.Signature, args: tuple, kwargs: dict, names: tuple[str, ...]) -> tuple[str, ...]:
    """从调用入参中取出指定参数值（取不到返回空串）。"""
    if not names:
        return tuple("" for _ in names)
    try:
        bound = sig.bind_partial(*args, **kwargs)
    except Exception:
        return tuple("" for _ in names)
    return tuple(_resolve_path(bound.arguments, name) if name else "" for name in names)


def audited(
    *,
    category: str,
    action: str,
    target_type: str = "",
    actor_arg: str = "",
    target_arg: str = "",
    target_no_arg: str = "",
    target_result_attr: str = "",
    summary_arg: str = "",
    summary: str = "",
) -> Callable:
    """动作留痕装饰器（挂载点唯一）。

    Args:
        category: 事件类别，如 "file" / "event" / "node"
        action: 动作标识，如 "confidentiality_updated"
        target_type: 目标类型，如 "file"
        actor_arg: 取操作人的参数名（如 "operator_id"；支持 "cmd.creator_id" 这类路径）；
                   取不到时回退上下文 actor，仍无则记 actor=unknown
        target_arg: 取目标 ID 的参数名（如 "file_id"）
        target_no_arg: 取目标业务编号的参数名（如 "file_no"）
        target_result_attr: 目标 ID 取自**返回值**的属性名（建类动作用它取新建对象 id）
        summary_arg: 参与摘要的参数名（模板中的 {summary}）
        summary: 摘要模板，支持 {action} / {target} / {summary} 占位；留空用 "{action} {target}"

    同时支持同步与异步方法；被装饰方法抛异常时记 failed 后原样抛出。
    """
    def decorator(fn: Callable) -> Callable:
        try:
            sig: Optional[inspect.Signature] = inspect.signature(fn)
        except (TypeError, ValueError):
            sig = None

        def _audit(args: tuple, kwargs: dict, result: str, reason: str, ret: Any = None) -> None:
            actor = target_id = target_no = extra = ""
            if sig is not None:
                actor, target_id, target_no, extra = _extract(
                    sig, args, kwargs, (actor_arg, target_arg, target_no_arg, summary_arg)
                )
            if not target_id and target_result_attr and ret is not None:
                target_id = _resolve_path({"ret": ret}, f"ret.{target_result_attr}")
            target = target_no or target_id
            try:
                text = (summary or "{action} {target}").format(
                    action=action, target=target, summary=extra,
                )
            except Exception:
                text = f"{action} {target}".strip()
            record_action(
                category=category,
                action=action,
                target_type=target_type,
                target_id=target_id,
                target_no=target_no,
                summary=text.strip(),
                result=result,
                error_reason=reason,
                actor_id=actor,
            )

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                try:
                    ret = await fn(*args, **kwargs)
                except Exception as e:
                    _audit(args, kwargs, RESULT_FAILED, repr(e))
                    raise
                result, reason = classify_result(ret)
                _audit(args, kwargs, result, reason, ret)
                return ret

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            try:
                ret = fn(*args, **kwargs)
            except Exception as e:
                _audit(args, kwargs, RESULT_FAILED, repr(e))
                raise
            result, reason = classify_result(ret)
            _audit(args, kwargs, result, reason, ret)
            return ret

        return sync_wrapper

    return decorator
