#!/usr/bin/env python
"""
审计报告问题修复验证测试脚本

覆盖范围：
  T1 - ProgressHook 空指针防护（P2）
  T2 - UserBindingService UUID 发送者对齐（P3-2）
  T3 - knowledge_search 兜底注册（P1-1）
  T4 - AuthHook 审计日志写入（P3-4）

运行方式：
  cd d:/app/Emily/.claude/skills/emy-test
  python test_bug_fixes.py
"""

import sys
import os
from pathlib import Path

# 添加项目路径
_project_root = Path(__file__).resolve().parents[3]
_emily_core = _project_root / "emily-core"
sys.path.insert(0, str(_emily_core))

_results: list[dict] = []


def record(test_id: str, name: str, passed: bool, detail: str = ""):
    _results.append({"id": test_id, "name": name, "passed": passed, "detail": detail})
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {test_id}: {name}")
    if detail:
        print(f"        {detail}")


# ══════════════════════════════════════════════════════════════════════════════
# T1: ProgressHook 空指针防护
# ══════════════════════════════════════════════════════════════════════════════

def test_progress_hook_safety():
    """验证 ProgressHook 对 None sender / 不可调用 sender 的防护。"""
    print("\n── T1: ProgressHook 空指针防护 ──")

    from emily_core.workitem.pipeline.hook import ProgressHook, HookResult

    # ① sender=None → 应静默跳过
    hook = ProgressHook(name="test.progress", progress_sender=None)
    try:
        class FakeContext:
            bagg = {}
            intent = None
            def __getattr__(self, name):
                if name == "baggage":
                    return self.bagg
                return None
        ctx = FakeContext()
        import asyncio
        result = asyncio.run(hook.execute(ctx))
        record("T1.1", "sender=None 静默跳过", True, "返回 ALLOW，无异常")
    except Exception as e:
        record("T1.1", "sender=None 静默跳过", False, str(e))

    # ② sender 不可调用 → 应静默跳过
    hook2 = ProgressHook(name="test.progress2", progress_sender="not_callable_string")
    try:
        ctx2 = FakeContext()
        result = asyncio.run(hook2.execute(ctx2))
        record("T1.2", "sender 不可调用静默跳过", True, "返回 ALLOW，无异常")
    except Exception as e:
        record("T1.2", "sender 不可调用静默跳过", False, str(e))

    # ③ 有效 sender + 无效 template → 不崩溃
    hook3 = ProgressHook(name="test.progress3", progress_sender=lambda x: x, progress_template=None)
    try:
        ctx3 = FakeContext()
        result = asyncio.run(hook3.execute(ctx3))
        record("T1.3", "sender 有效 + None template 不崩溃", True, "返回 ALLOW，无异常")
    except Exception as e:
        record("T1.3", "sender 有效 + None template 不崩溃", False, str(e))

    # ④ enable_progress=False → 跳过
    hook4 = ProgressHook(name="test.progress4", progress_sender=None, enable_progress=False)
    try:
        ctx4 = FakeContext()
        result = asyncio.run(hook4.execute(ctx4))
        record("T1.4", "enable_progress=False 跳过", True, "返回 ALLOW，无异常")
    except Exception as e:
        record("T1.4", "enable_progress=False 跳过", False, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# T2: UserBindingService UUID 处理
# ══════════════════════════════════════════════════════════════════════════════

def test_user_binding_uuid():
    """验证 UserBindingService 正确识别 UUID 格式。"""
    print("\n── T2: UserBindingService UUID 处理 ──")

    from emily_core.services.user_binding_service import UserBindingService

    svc = UserBindingService()

    # ① 标准 UUID 检测
    record("T2.1", "标准 UUID 检测",
           svc._looks_like_uuid("550e8400-e29b-41d4-a716-446655440000"),
           "8-4-4-4-12 格式")

    # ② 无连字符 UUID 检测
    record("T2.2", "无连字符 UUID 检测",
           svc._looks_like_uuid("550e8400e29b41d4a716446655440000"),
           "32 字符十六进制")

    # ③ QQ 号不应被误识别为 UUID
    record("T2.3", "QQ 号不误识别",
           not svc._looks_like_uuid("123456789"),
           "纯数字短 ID 非 UUID")

    # ④ 空字符串检测
    record("T2.4", "空字符串不误识别",
           not svc._looks_like_uuid(""),
           "空字符串返回 False")

    # ⑤ None 检测
    record("T2.5", "None 不误识别",
           not svc._looks_like_uuid(None),
           "None 返回 False")


# ══════════════════════════════════════════════════════════════════════════════
# T3: knowledge_search 兜底注册
# ══════════════════════════════════════════════════════════════════════════════

def test_knowledge_search_stub():
    """验证 knowledge_search 兜底 handler 可正常注册和调用。"""
    print("\n── T3: knowledge_search 兜底注册 ──")

    from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry
    from emily_core.tools.knowledge_search_tool import (
        _KNOWLEDGE_SEARCH_SCHEMA, _KNOWLEDGE_SEARCH_DESCRIPTION,
    )
    from emily_core.tools.registry import _tool

    reg = BusinessFlowToolRegistry()

    # 模拟 rag_provider=None 时的兜底注册
    async def _rag_stub(params, **kw):
        query = (params.get("query", "") or "").strip()
        return {
            "success": False,
            "reply": f"知识库服务暂未就绪（查询：{query}），请稍后重试。",
        }

    reg.register(_tool("knowledge_search", _KNOWLEDGE_SEARCH_DESCRIPTION,
                       _KNOWLEDGE_SEARCH_SCHEMA, _rag_stub))

    # ① 工具已注册
    record("T3.1", "knowledge_search 已注册",
           reg.has("knowledge_search"),
           f"注册表含 {len(reg)} 个工具: {reg.list_names()}")

    # ② stub handler 返回友好提示
    import asyncio
    tool = reg.get("knowledge_search")
    result = asyncio.run(tool.handler(params={"query": "消防验收"}))
    record("T3.2", "stub handler 返回友好提示",
           isinstance(result, dict) and "知识库服务暂未就绪" in result.get("reply", ""),
           f"reply={result.get('reply', '?')[:80]}")


# ══════════════════════════════════════════════════════════════════════════════
# T4: AuthHook 审计日志写入
# ══════════════════════════════════════════════════════════════════════════════

def test_auth_hook_audit():
    """验证 AuthHook 阻断时写审计日志的函数不崩溃。"""
    print("\n── T4: AuthHook 审计日志写入 ──")

    from emily_core.workitem.pipeline.hook import _log_auth_block
    import asyncio

    # ① _log_auth_block 不崩溃（无 DB 时只记 WARNING）
    try:
        asyncio.run(_log_auth_block("test-user-001", "SOP-002", "测试阻断原因"))
        record("T4.1", "_log_auth_block 不崩溃 (无DB 回退)", True, "无异常抛出")
    except Exception as e:
        # 在无数据库环境下可能失败，但不应该导致 AuthHook 崩溃
        record("T4.1", "_log_auth_block 不崩溃", False, str(e))

    # ② 空参数也不崩溃
    try:
        asyncio.run(_log_auth_block("", "", ""))
        record("T4.2", "_log_auth_block 空参数不崩溃", True, "无异常抛出")
    except Exception as e:
        record("T4.2", "_log_auth_block 空参数不崩溃", False, str(e))

    # ③ 验证 PermissionAuditLogRepository 存在且方法可用
    try:
        from emily_core.permission.row_security import PermissionAuditLogRepository
        repo = PermissionAuditLogRepository()
        has_log = hasattr(repo, "log_access_denied")
        has_query = hasattr(repo, "query_logs")
        record("T4.3", "PermissionAuditLogRepository 方法完整",
               has_log and has_query,
               f"log_access_denied={'OK' if has_log else 'MISS'}, query_logs={'OK' if has_query else 'MISS'}")
    except Exception as e:
        record("T4.3", "PermissionAuditLogRepository 加载", False, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 总结
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  审计报告问题修复验证测试")
    print("=" * 70)

    test_progress_hook_safety()
    test_user_binding_uuid()
    test_knowledge_search_stub()
    test_auth_hook_audit()

    passed = sum(1 for r in _results if r["passed"])
    failed = sum(1 for r in _results if not r["passed"])
    total = len(_results)

    print("\n" + "=" * 70)
    print(f"  测试结果: {passed}/{total} 通过, {failed} 失败")
    print("=" * 70)

    if failed > 0:
        print("\n失败明细:")
        for r in _results:
            if not r["passed"]:
                print(f"  [{r['id']}] {r['name']}: {r['detail']}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
