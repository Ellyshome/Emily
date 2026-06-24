"""PipelineNode —— 公共 Pipeline BUS 的单个节点（蓝图 §5.4）。

每个节点是一个执行单元，携带 3 个 Hook 挂载点：
  · before:{node}    —— 节点执行前（鉴权/资源检查/审核），可 BLOCK
  · after:{node}     —— 节点执行后（审计/追踪），fire-and-forget
  · on_error:{node}  —— 节点执行异常（错误记录/恢复/降级）

节点的实际业务逻辑由 handler 提供：async fn(BusContext) -> None。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import BusContext

# 节点 handler 签名：async fn(context) -> None
NodeHandler = Callable[["BusContext"], Awaitable[None]]


@dataclass
class PipelineNode:
    """公共 Pipeline BUS 的单个节点。

    Attributes:
        name: 节点名（如 "wi_node1"），用于 hook 挂载点拼接。
        title: 人类可读标题（如 "意图+拆分"）。
        handler: 节点业务逻辑，async fn(context) -> None。
        required: 是否必经节点。required 节点异常 → WorkItem FAILED；
                  非 required 节点异常 → 触发 on_error hook 后继续下一节点。
    """

    name: str
    title: str
    handler: "NodeHandler"
    required: bool = True
