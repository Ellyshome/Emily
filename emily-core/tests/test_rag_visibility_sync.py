"""RAG 检索可见范围与文件可见范围「同步性」测试。

核心不变量（C13/C14 + M4）：

    RAG 检索能命中的文件集合 == 该人员按文件可见范围能看到的文件集合。

也就是说，RAG 的作用域 `scoped_doc_ids` 必须完全来自
`VisibleFileSetResolver`（文件可见性的唯一权威来源），不同人员得到各自的范围，
不允许「全库裸查」或「另写一套可见规则」。

本测试不依赖真实数据库：通过伪造 `PermissionService` / `VisibleFileSetResolver`
/ `RagProvider`，验证两条 RAG 入口（生产工具 handler 与 console 控制台）
都把 resolver 的输出原样作为 `scoped_doc_ids` 传给 provider。
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

# 不同人员画像：(user_id, company_id, info_level, 预期可见文件 id 集合)
PERSONNEL = [
    ("u_uploader", "c_alpha", "public", {"f1", "f2"}),
    ("u_member", "c_beta", "internal", {"f3", "f4"}),
    ("u_admin", "c_gamma", "confidential", {"f1", "f2", "f3", "f4", "f5"}),
    ("u_guest", "", "public", set()),
]


class _FakeResolver:
    """模拟 VisibleFileSetResolver：记录调用并返回预设可见集。"""

    def __init__(self, visible_ids):
        self.visible_ids = set(visible_ids)
        self.calls = []

    def resolve_visible_file_ids(self, user_id, *, company_id, info_level, explicit_ok=True):
        self.calls.append(dict(user_id=user_id, company_id=company_id, info_level=info_level))
        return sorted(self.visible_ids)

    def resolve_visible_file_list(self, user_id, **ctx):
        self.calls.append(dict(user_id=user_id, **ctx))
        return sorted(self.visible_ids)


class _FakeRagProvider:
    """模拟 RAG provider：捕获传入的 scoped_doc_ids。"""

    def __init__(self):
        self.captured_scoped_doc_ids = None

    async def is_available(self):
        return True

    async def search(self, query, top_k=5, stage=None, role=None, scoped_doc_ids=None, rerank=False):
        self.captured_scoped_doc_ids = scoped_doc_ids
        return SimpleNamespace(
            query=query, total=0, provider_name="fake",
            context_text="", results=[],
        )


def _make_perm(company_id: str, info_level: str) -> dict:
    return {
        "company_id": company_id,
        "info_level": info_level,
        "authorized_node_ids": [],
        "sop_allow": [],
    }


def _patch_permission(monkeypatch, company_id: str, info_level: str) -> None:
    from emily_core.services import permission_service as ps

    monkeypatch.setattr(
        ps.PermissionService,
        "build_permission_dict",
        lambda self, user_id, _c=company_id, _i=info_level: _make_perm(_c, _i),
    )


async def _async_noop(*args, **kwargs):
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 生产入口：knowledge_search 工具
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("user_id,company_id,info_level,expected", PERSONNEL)
def test_resolve_scoped_doc_ids_equals_resolver_scope(
    monkeypatch, user_id, company_id, info_level, expected
):
    """RAG 作用域解析必须原样返回 resolver 的可见集，并用对上下文。"""
    from emily_core.tools import knowledge_search_tool as kst

    _patch_permission(monkeypatch, company_id, info_level)
    resolver = _FakeResolver(expected)

    scoped = asyncio.run(kst._resolve_scoped_doc_ids(user_id, resolver))

    assert set(scoped) == expected
    assert resolver.calls[-1] == dict(user_id=user_id, company_id=company_id, info_level=info_level)


@pytest.mark.parametrize("user_id,company_id,info_level,expected", PERSONNEL)
def test_handle_knowledge_search_forwards_resolver_scope(
    monkeypatch, user_id, company_id, info_level, expected
):
    """生产 handler 必须把 resolver 的可见集作为 scoped_doc_ids 传给 provider。"""
    from emily_core.tools import knowledge_search_tool as kst
    from emily_core.infrastructure.logging.rag_logger import RAGRetrievalLogger

    _patch_permission(monkeypatch, company_id, info_level)
    monkeypatch.setattr(RAGRetrievalLogger, "log", _async_noop)

    provider = _FakeRagProvider()
    resolver = _FakeResolver(expected)

    result = asyncio.run(
        kst.handle_knowledge_search(
            {"query": "测试查询", "top_k": 5},
            provider,
            user_id=user_id,
            resolver=resolver,
        )
    )

    assert result.get("success") is True
    assert set(provider.captured_scoped_doc_ids) == expected


# ══════════════════════════════════════════════════════════════════════════════
# console 入口：emy-console 的 /rag-search（经 _build_visible_sets）
# ══════════════════════════════════════════════════════════════════════════════

def _install_fake_api_server(monkeypatch) -> None:
    """让 _build_visible_sets 内部的 `from api.server import get_core` 走轻量假模块，
    避免在单测中拉起整个 FastAPI 路由注册。"""
    fake = types.ModuleType("api.server")

    def get_core():
        raise RuntimeError("EmilyCore not initialized (test)")

    fake.get_core = get_core
    monkeypatch.setitem(sys.modules, "api.server", fake)


@pytest.mark.parametrize("user_id,company_id,info_level,expected", PERSONNEL)
def test_console_rag_scope_equals_file_scope(
    monkeypatch, user_id, company_id, info_level, expected
):
    """console 的文件可见集（rag-search 复用同一来源）必须等于 resolver 输出。"""
    from emily_core.services import permission_service as ps
    from emily_core.services.visible_file_set_resolver import VisibleFileSetResolver

    _install_fake_api_server(monkeypatch)

    import api.routes.console_resources as cr

    monkeypatch.setattr(
        ps.PermissionService,
        "build_permission_dict",
        lambda self, uid, _c=company_id, _i=info_level: _make_perm(_c, _i),
    )
    resolver = _FakeResolver(expected)
    monkeypatch.setattr(
        VisibleFileSetResolver,
        "resolve_visible_file_list",
        lambda self, uid, **ctx: resolver.resolve_visible_file_list(uid, **ctx),
    )

    visible_file_ids, _, _ = cr._build_visible_sets(user_id)

    assert visible_file_ids == expected


# ══════════════════════════════════════════════════════════════════════════════
# resolver 本身：不同人员得到不同范围（DB 无关，编译 SQL 校验关键通道）
# ══════════════════════════════════════════════════════════════════════════════

def _compile_visible_select(user_id: str, company_id: str, info_level: str) -> str:
    from sqlalchemy.dialects import postgresql

    from emily_core.services.visible_file_set_resolver import _build_visible_select

    stmt = _build_visible_select(user_id, company_id, info_level, True)
    return str(stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))


def test_resolver_distinguishes_personnel():
    """有企业 → 走节点可见通道；机密级别 → 开启管理员兜底放行机密。"""
    with_company = _compile_visible_select("u", "c", "internal")
    without_company = _compile_visible_select("u", "", "internal")
    assert "node_accessible_files" in with_company
    assert "node_accessible_files" not in without_company

    admin = _compile_visible_select("u", "c", "confidential")
    internal = _compile_visible_select("u", "c", "internal")
    assert "<= 2" in admin
    assert "<= 2" not in internal
