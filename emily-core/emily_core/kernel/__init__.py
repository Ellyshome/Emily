# emily-core/emily_core/kernel/__init__.py
"""内核共享组件包。

本包承载**编排内核的机制实现**（非业务能力），供会话编排图与工单编排图共同消费。
"""
from . import context, policies, react_kernel  # noqa: F401

__all__ = ["react_kernel", "policies", "context"]
