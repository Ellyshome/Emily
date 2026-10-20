"""演示链路纯函数/离线测试：附件 manifest、路径解析、节点查询、完工上报。"""

import asyncio
import types

from emily_core.services.attachment_downloader import (
    AttachmentDownloader,
    format_attachment_manifest,
)
from emily_core.tools.attachment_tool import handle_resolve_file_path
from emily_core.tools.library_file_tool import handle_send_library_file
from emily_core.tools.node_query_tool import _parse_dt, _status
from emily_core.tools.site_report_tool import (
    _proposal_reply,
    _recorded_reply,
    handle_site_report,
)


def test_manifest_format_ready_and_skipped():
    manifest = [
        {"file_name": "苗木表.xlsx", "type": 3, "file_no": "FIL-1",
         "local_path": "/app/attachments/FIL-1.xlsx", "file_size": 10, "status": "ready"},
        {"file_name": "表情.gif", "type": 2, "status": "skipped_chat"},
    ]
    text = format_attachment_manifest(manifest)
    assert "苗木表.xlsx" in text
    assert "FIL-1" in text
    assert "/app/attachments/FIL-1.xlsx" in text
    assert "表情.gif" in text
    assert "未入库" in text


def test_attachment_downloader_returns_manifest():
    class FM:
        async def store_attachment(self, message_id, url, attachment_type, **kw):
            return {"file_id": "f1", "file_no": "FIL-1",
                    "local_path": "/app/attachments/FIL-1.xlsx", "file_size": 10}

    dl = AttachmentDownloader(FM())
    dl._is_already_downloaded = lambda *a: False
    manifest = asyncio.run(dl.download_for_message("m1", [
        {"url": "http://x/a.xlsx", "type": 3, "file_name": "苗木表.xlsx"},
        {"url": "", "type": 2, "file_name": "空图.jpg"},
    ]))
    assert len(manifest) == 2
    assert manifest[0]["status"] == "ready"
    assert manifest[0]["file_no"] == "FIL-1"
    assert manifest[1]["status"] == "missing_url"


def test_resolve_file_path_allows_current_message():
    class Rec:
        id = "f1"
        file_no = "FIL-1"
        filename = "苗木表.xlsx"
        file_size = 10
        message_id = "m1"

    class FM:
        def get_by_file_no(self, no):
            return Rec() if no == "FIL-1" else None

        def can_access(self, uid, fid):
            return False

        def resolve_local_path(self, no):
            return "/app/attachments/FIL-1.xlsx"

    allowed = asyncio.run(handle_resolve_file_path(
        {"file_no": "FIL-1"}, file_manager=FM(), user_id="u1", message_id="m1"))
    denied = asyncio.run(handle_resolve_file_path(
        {"file_no": "FIL-1"}, file_manager=FM(), user_id="u1", message_id="m2"))
    assert allowed["success"] is True
    assert allowed["file_path"].endswith("FIL-1.xlsx")
    assert denied["success"] is False
    assert denied["error_code"] == "permission_denied"


def test_query_nodes_helpers():
    dt = _parse_dt("2026-09-15")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 9
    assert _parse_dt("not-a-date") is None
    assert _status("进行中") == "IN_PROGRESS"
    assert _status("已超时") == "已超时"  # 未知状态原样返回，由 DB 过滤


def test_site_report_replies():
    base = {"tier": "A", "target_node_name": "花岗岩铺装",
            "target_node_id": "N1", "deliverable_name": "花岗岩铺装"}
    proposal = _proposal_reply(base, "2#、4#楼间道路花岗岩铺装", 10, "㎡")
    recorded = _recorded_reply(base, "2#、4#楼间道路花岗岩铺装", 10, "㎡", ["F1", "F2"])
    assert "将记入已有任务" in proposal
    assert "花岗岩铺装" in proposal
    assert "已登记" in recorded
    assert "照片 2 张" in recorded


def test_site_report_dry_run_tier_a(monkeypatch):
    import emily_core.tools.file_tool as ft
    import emily_core.tools.site_report_tool as srt

    monkeypatch.setattr(ft, "check_min_level", lambda *a, **k: None)
    monkeypatch.setattr(srt, "_judge", lambda *a, **k: types.SimpleNamespace(
        tier="A", target_node_id="N1", target_node_name="花岗岩铺装",
        milestone_id="", milestone_name="", reason="命中",
    ))
    monkeypatch.setattr(srt, "_match_deliverable", lambda *a, **k: {
        "deliverable_id": "D1", "deliverable_name": "花岗岩铺装",
    })

    r = asyncio.run(handle_site_report({
        "project_id": "P1",
        "location": "2#、4#楼间道路花岗岩铺装",
        "quantity": 10,
        "unit": "㎡",
        "report_text": "花岗岩铺装今天干了10平米",
        "photo_file_nos": ["FIL-1", "FIL-2"],
        "dry_run": True,
    }, event_app=object(), user_id="u5"))
    assert r["success"] is True
    assert r["tier"] == "A"
    assert r["deliverable_name"] == "花岗岩铺装"
    assert r["photo_count"] == 2
    assert "将记入已有任务" in r["reply"]


def test_send_library_file_uses_runtime_conversation_id(tmp_path, monkeypatch):
    import emily_core.tools.file_tool as ft
    import emily_core.tools.library_file_tool as lft

    monkeypatch.setattr(ft, "check_min_level", lambda *a, **k: None)
    sample = tmp_path / "导水槽效果.jpg"
    sample.write_bytes(b"jpg")
    monkeypatch.setattr(lft, "_safe_path", lambda path: sample)

    published = []

    class Bus:
        def publish(self, event_type, payload):
            published.append((event_type, payload))

    result = asyncio.run(handle_send_library_file(
        {"path": "节点做法/上虞江南里-巷道雨水排水/导水槽效果-巷道.jpg",
         "_conversation_id": "conv-demo-1"},
        outbound_bus=Bus(), user_id="u5"))
    assert result["success"] is True
    assert published[0][0] == "file_send"
    assert published[0][1]["conversation_id"] == "conv-demo-1"
    assert published[0][1]["file_paths"][0]["name"] == "导水槽效果.jpg"


def test_extract_interrupt_question_handles_tuple_tasks():
    """LangGraph 1.x snap.tasks 为 tuple 时也要能取到 ask_user 的真实问题。"""
    from emily_core.workitem.scheduler import _extract_interrupt_question

    class _Task:
        interrupts = [types.SimpleNamespace(value="铺装是哪一段？")]

    snap = types.SimpleNamespace(tasks=(_Task(),), values={})
    assert _extract_interrupt_question(snap) == "铺装是哪一段？"

    snap_dict = types.SimpleNamespace(tasks={"t1": _Task()}, values={})
    assert _extract_interrupt_question(snap_dict) == "铺装是哪一段？"


def test_site_report_requires_two_photos(monkeypatch):
    import emily_core.tools.file_tool as ft

    monkeypatch.setattr(ft, "check_min_level", lambda *a, **k: None)
    r = asyncio.run(handle_site_report({
        "project_id": "P1",
        "location": "2#、4#楼间道路花岗岩铺装",
        "quantity": 10,
        "unit": "㎡",
        "report_text": "花岗岩铺装今天干了10平米",
        "photo_file_nos": ["FIL-1"],
        "dry_run": True,
    }, event_app=object(), user_id="u5"))
    assert r["success"] is False
    assert r["error_code"] == "photos_required"


def test_video_wait_seconds_by_duration():
    from emily_core.tools.video_download_tool import compute_wait_seconds

    assert compute_wait_seconds(212) == 514      # 2×212 + 90
    assert compute_wait_seconds(10) == 120       # 下限
    assert compute_wait_seconds(3000) == 600     # 上限
    assert compute_wait_seconds(0) == 240        # 时长未知
