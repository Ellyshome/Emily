"""QueryService —— 结构化查询服务。

M5: 支持 9 种 query_type 的跨库查询 + 统计聚合 + 回复格式化。
覆盖全部 9 张业务表：events / tasks / meetings / files / messages /
conversations / users / projects / 以及跨领域 summary。

M8c: 新增 query_type="journal" 支持项目事件日志查询。
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from ..repositories.event_repo import EventRepository
from ..repositories.task_repo import TaskRepository
from ..repositories.meeting_repo import MeetingRepository
from ..repositories.file_repo import FileRepository
from ..repositories.message_repo import MessageRepository
from ..repositories.user_repo import UserRepository
from ..adapters.standard.command import QueryCommand

logger = logging.getLogger("emily.service.query")

# ── 时间范围解析 ──


def _resolve_time_range(time_range: str) -> tuple[str | None, str | None]:
    """将语义时间范围转换为 ISO 日期范围 (start, end)。

    Args:
        time_range: today / this_week / this_month / all

    Returns:
        (start_iso_str | None, end_iso_str | None)
        None 表示不限
    """
    if time_range == "all":
        return None, None

    now = datetime.now(timezone.utc)

    if time_range == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif time_range == "this_week":
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif time_range == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    else:
        return None, None

    return start.isoformat(), end.isoformat()


# ── 查询结果类型 ──


class QueryService:
    """结构化查询服务。

    按 query_type 分发到对应 repo，支持按项目、时间范围、
    状态、负责人、发送者、关键词等多维度过滤。
    """

    def __init__(self):
        self.event_repo = EventRepository()
        self.task_repo = TaskRepository()
        self.meeting_repo = MeetingRepository()
        self.file_repo = FileRepository()
        self.message_repo = MessageRepository()
        self.user_repo = UserRepository()
        self._journal = None  # M8c: EventJournal 引用

    def set_journal(self, journal) -> None:
        """注入事件日志服务（M8c）。"""
        self._journal = journal

    # ── 按 query_type 分发 ──

    def execute(self, cmd: QueryCommand) -> dict[str, Any]:
        """按 query_type 分发给对应的查询方法。

        Returns:
            dict 包含查询结果和元数据：
            - query_type: str
            - items: list | None（查询结果列表）
            - counts: dict | None（统计数据）
            - summary: dict | None（summary 类型返回）
            - total: int
        """
        qt = cmd.query_type

        if qt == "event":
            items = self.query_events(
                project_id=cmd.project_id,
                project_ids=cmd.project_ids,
                time_range=cmd.time_range,
                status=cmd.status_filter,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "task":
            items = self.query_tasks(
                project_id=cmd.project_id,
                project_ids=cmd.project_ids,
                time_range=cmd.time_range,
                status=cmd.status_filter,
                assignee=cmd.assignee,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "meeting":
            items = self.query_meetings(
                project_id=cmd.project_id,
                project_ids=cmd.project_ids,
                time_range=cmd.time_range,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "file":
            items = self.query_files(
                project_id=cmd.project_id,
                project_ids=cmd.project_ids,
                file_type=cmd.file_type,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "message":
            items = self.query_messages(
                project_id=cmd.project_id,
                time_range=cmd.time_range,
                conversation_id=cmd.conversation_id,
                sender_name=cmd.sender_name,
                keyword=cmd.keyword,
                intent=cmd.intent,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "conversation":
            items = self.query_conversations(
                time_range=cmd.time_range,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "user":
            items = self.query_users(
                time_range=cmd.time_range,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "project":
            items = self.query_projects()
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "journal":
            items = self.query_journal(
                time_range=cmd.time_range,
                keyword=cmd.keyword,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        elif qt == "summary":
            summary = self.get_summary(project_id=cmd.project_id)
            return {
                "query_type": qt,
                "summary": summary,
                "items": None,
                "total": 0,
            }

        elif qt == "my_nodes":
            items = self.query_my_nodes(
                user_id=cmd.sender_name or "",
                project_id=cmd.project_id,
                limit=cmd.limit,
            )
            return {
                "query_type": qt,
                "items": items,
                "total": len(items),
            }

        else:
            logger.warning("Unknown query_type: %s", qt)
            return {
                "query_type": qt,
                "items": [],
                "total": 0,
            }

    # ── 溯源到人 ──

    def build_trace(self, query_type: str, items: list) -> list[dict]:
        """构建查询结果的「信息溯源到人」结构化数据。

        供 handle_query_data 附带在返回结果中，默认不渲染进回复文本；
        LLM 在用户追问「谁记录的 / 谁确认的 / 谁负责的」时据此回答。

        溯源字段（三层，对应人员关联纽带基础）：
        - uploader：信息上传/记录人（谁提供的信息）
        - confirmed_by：认证人（谁确认该信息生效）
        - responsible：责任人（任务 owner）
        - host：主持人（会议 host_id）

        每个溯源人含 id / name / position，使「人→人关联」不仅返回姓名，
        还附带其岗位，让查询者知道可向谁、以什么身份进一步核实。

        Returns:
            list[dict]，每条对应一个业务对象，含 no/title + 各溯源人的身份。
            解析不到时字段值为 ""（fail-open，不阻断查询）。
        """
        if query_type not in ("event", "task", "meeting"):
            return []

        # 收集所有需解析的用户 id，一次性解析为身份信息（避免 N+1 逐条查询）
        user_ids: set[str] = set()
        for it in items or []:
            for attr in ("user_id", "confirmed_by", "owner_id", "created_by", "host_id"):
                uid = getattr(it, attr, None) or ""
                if uid:
                    user_ids.add(uid)

        info_map = self._resolve_user_info(user_ids)

        traces: list[dict] = []
        for it in items or []:
            def _p(attr):
                uid = getattr(it, attr, None) or ""
                info = info_map.get(uid, {})
                return {"id": uid, "name": info.get("name", ""),
                        "position": info.get("position", "")} if uid else \
                       {"id": "", "name": "", "position": ""}

            if query_type == "event":
                traces.append({
                    "no": getattr(it, "event_no", "") or "",
                    "title": getattr(it, "title", "") or "",
                    "uploader": _p("user_id"),
                    "confirmed_by": _p("confirmed_by"),
                })
            elif query_type == "task":
                traces.append({
                    "no": getattr(it, "task_no", "") or "",
                    "title": getattr(it, "title", "") or "",
                    "uploader": _p("created_by"),
                    "responsible": _p("owner_id"),
                })
            elif query_type == "meeting":
                traces.append({
                    "no": getattr(it, "meeting_no", "") or "",
                    "title": getattr(it, "title", "") or "",
                    "uploader": _p("created_by"),
                    "host": _p("host_id"),
                })
        return traces

    def _resolve_user_info(self, user_ids: set[str]) -> dict[str, dict]:
        """批量解析 user_id → {name, position}。fail-open：单个失败返回空。"""
        result: dict[str, dict] = {}
        for uid in user_ids:
            try:
                u = self.user_repo.get(uid)
                if u:
                    result[uid] = {
                        "name": u.username or "",
                        "position": self._format_position(getattr(u, "position", "") or ""),
                    }
            except Exception as e:
                logger.debug("resolve_user_info failed for %s: %s", uid, e)
        return result

    @staticmethod
    def _format_position(position_raw: str) -> str:
        """解析 users.position JSON 数组字符串，拼接为顿号分隔的岗位名。"""
        if not position_raw or position_raw == "[]":
            return ""
        try:
            import json as _json
            positions = _json.loads(position_raw)
            if isinstance(positions, list):
                return "、".join(str(p) for p in positions if p)
        except (ValueError, TypeError):
            return ""
        return ""

    # ── 业务对象查询 ──

    def query_events(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        time_range: str = "all",
        status: str | None = None,
        limit: int = 50,
    ):
        """查询事件。支持 session_scope 的 project_ids 范围过滤。"""
        return self.event_repo.query_events(
            project_id=project_id,
            project_ids=project_ids,
            time_range=time_range,
            status=status,
            limit=limit,
        )

    def query_tasks(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        time_range: str = "all",
        status: str | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ):
        """查询任务。支持 session_scope 的 project_ids 范围过滤。"""
        return self.task_repo.query_tasks(
            project_id=project_id,
            project_ids=project_ids,
            time_range=time_range,
            status=status,
            assignee=assignee,
            limit=limit,
        )

    def query_meetings(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        time_range: str = "all",
        limit: int = 50,
    ):
        """查询会议。支持 session_scope 的 project_ids 范围过滤。"""
        return self.meeting_repo.query_meetings(
            project_id=project_id,
            project_ids=project_ids,
            time_range=time_range,
            limit=limit,
        )

    def query_files(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        file_type: str | None = None,
        limit: int = 50,
    ):
        """查询文件。支持 session_scope 的 project_ids 范围过滤。"""
        return self.file_repo.query_files(
            project_id=project_id,
            project_ids=project_ids,
            file_type=file_type,
            limit=limit,
        )

    # ── 通讯记录查询 ──

    def query_messages(
        self,
        project_id: str | None = None,
        time_range: str = "all",
        conversation_id: str | None = None,
        sender_name: str | None = None,
        keyword: str | None = None,
        intent: str | None = None,
        limit: int = 50,
    ):
        """多维度查询消息。"""
        return self.message_repo.query_messages(
            project_id=project_id,
            time_range=time_range,
            conversation_id=conversation_id,
            sender_name=sender_name,
            keyword=keyword,
            intent=intent,
            limit=limit,
        )

    def query_conversations(
        self,
        time_range: str = "all",
        limit: int = 20,
    ) -> list[dict]:
        """活跃会话排行。"""
        return self.message_repo.get_active_conversations(
            time_range=time_range,
            limit=limit,
        )

    def query_users(
        self,
        time_range: str = "all",
        limit: int = 50,
    ) -> list[dict]:
        """用户列表（含消息计数 + 节点归属）。"""
        from ..repositories.node_repo import ProjectNodeRepo

        users = self.user_repo.list_users(limit=limit)
        results = []
        for u in users:
            # 查该用户的节点参与信息
            node_names: list[str] = []
            try:
                resp_nodes = ProjectNodeRepo.find_by_responsible_user(
                    u.id, status="IN_PROGRESS", limit=50,
                )
                part_nodes = ProjectNodeRepo.find_by_participant_user(
                    u.id, status="IN_PROGRESS", limit=50,
                )
                seen = set()
                for n in resp_nodes + part_nodes:
                    if n.node_id not in seen:
                        seen.add(n.node_id)
                        role = "负责人" if n.node_id in {r.node_id for r in resp_nodes} else "参与"
                        node_names.append(f"{n.node_name}({role})")
            except Exception:
                pass

            results.append({
                "user_id": u.id,
                "username": u.username,
                "display_name": u.username,
                "status": u.status or "active",
                "created_at": u.created_at,
                "node_names": node_names,
            })
        return results

    def query_my_nodes(
        self,
        user_id: str = "",
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """查询用户负责或参与的全景节点。"""
        from ..repositories.node_repo import ProjectNodeRepo

        resp_nodes = ProjectNodeRepo.find_by_responsible_user(
            user_id, project_id=project_id, status="IN_PROGRESS", limit=limit,
        )
        part_nodes = ProjectNodeRepo.find_by_participant_user(
            user_id, project_id=project_id, status="IN_PROGRESS", limit=limit,
        )

        seen: set[str] = set()
        results = []
        resp_ids = {n.node_id for n in resp_nodes}
        for n in resp_nodes + part_nodes:
            if n.node_id in seen:
                continue
            seen.add(n.node_id)
            results.append({
                "node_id": n.node_id,
                "node_name": n.node_name,
                "node_type": getattr(n, "node_type", ""),
                "status": n.status,
                "deadline": n.deadline,
                "project_id": n.project_id,
                "role": "responsible" if n.node_id in resp_ids else "participant",
            })
        return results

    def query_projects(self) -> list[dict]:
        """项目列表。"""
        projects = self.event_repo.list_projects(status="active")
        archived = self.event_repo.list_projects(status="archived")
        results = []
        for p in projects + archived:
            results.append({
                "project_id": p.id,
                "code": p.code,
                "name": p.name,
                "description": p.description,
                "status": p.status,
            })
        return results

    def query_journal(
        self,
        time_range: str = "all",
        keyword: str = "",
        limit: int = 50,
    ) -> list[str]:
        """查询项目事件日志（M8c）。

        Args:
            time_range: 时间范围（暂在返回结果中标注）
            keyword: 搜索关键词
            limit: 最大返回条目数

        Returns:
            日志条目行列表
        """
        if self._journal is None:
            return ["项目日志服务未启用"]
        return self._journal.search(keyword=keyword, limit=limit)

    # ── 跨领域聚合 ──

    def get_summary(self, project_id: str | None = None) -> dict:
        """跨领域统计摘要。

        Returns:
            包含各表计数的字典。
        """
        event_counts = self.event_repo.count_by_status(project_id=project_id)
        task_counts = self.task_repo.count_by_status(project_id=project_id)

        # 消息总数（不分状态）
        messages = self.message_repo.query_messages(
            project_id=project_id, limit=10000,
        )
        message_count = len(messages)

        # 活跃会话数
        conversations = self.message_repo.get_active_conversations(limit=100)
        conversation_count = len(conversations)

        # 用户数
        user_count = self.user_repo.count_users(status="active")

        # 项目数
        projects = self.event_repo.list_projects(status="active")
        project_count_total = len(projects)

        # 文件数
        files = self.file_repo.query_files(project_id=project_id, limit=10000)
        file_count = len(files)

        # 会议数
        meetings = self.meeting_repo.query_meetings(
            project_id=project_id, limit=10000,
        )
        meeting_count = len(meetings)

        return {
            "events": event_counts,
            "events_total": sum(event_counts.values()),
            "tasks": task_counts,
            "tasks_total": sum(task_counts.values()),
            "messages_total": message_count,
            "conversations_total": conversation_count,
            "users_total": user_count,
            "projects_total": project_count_total,
            "files_total": file_count,
            "meetings_total": meeting_count,
        }

    # ── 回复格式化 ──

    @staticmethod
    def format_reply(query_type: str, results: dict) -> str:
        """将查询结果格式化为用户可读的回复文本。

        Args:
            query_type: 查询类型
            results: execute() 返回的结果字典

        Returns:
            格式化的回复文本
        """
        total = results.get("total", 0)
        items = results.get("items")

        # ── event ──
        if query_type == "event":
            if total == 0:
                return "暂无事件记录。"
            lines = [f"共找到 {total} 条事件记录："]
            for i, ev in enumerate(items, 1):
                if i > 10 and total > 10:
                    lines.append(f"... 还有 {total - 10} 条")
                    break
                title = getattr(ev, "title", "") or ""
                event_no = getattr(ev, "event_no", "") or ""
                status = getattr(ev, "status", "") or ""
                event_date = getattr(ev, "event_date", "") or getattr(ev, "created_at", "") or ""
                desc = getattr(ev, "description", "") or ""
                # 格式化日期为 YYYY-MM-DD
                date_str = ""
                if event_date:
                    date_str = event_date[:10] if len(event_date) >= 10 else event_date
                line = f"  {event_no} [{status}] {title}"
                if date_str:
                    line += f" ({date_str})"
                if desc and len(desc) <= 60:
                    line += f" — {desc}"
                lines.append(line)
            return "\n".join(lines)

        # ── task ──
        elif query_type == "task":
            if total == 0:
                return "暂无任务记录。"
            lines = [f"共找到 {total} 条任务："]
            for i, tk in enumerate(items, 1):
                if i > 10 and total > 10:
                    lines.append(f"... 还有 {total - 10} 条")
                    break
                title = getattr(tk, "title", "") or ""
                task_no = getattr(tk, "task_no", "") or ""
                status = getattr(tk, "status", "") or "todo"
                owner = getattr(tk, "owner_text", "") or ""
                line = f"  {task_no} [{status}] {title}"
                if owner:
                    line += f" - {owner}"
                lines.append(line)
            return "\n".join(lines)

        # ── meeting ──
        elif query_type == "meeting":
            if total == 0:
                return "暂无会议记录。"
            lines = [f"共找到 {total} 场会议："]
            for i, mt in enumerate(items, 1):
                if i > 10 and total > 10:
                    lines.append(f"... 还有 {total - 10} 条")
                    break
                title = getattr(mt, "title", "") or "未命名会议"
                meeting_no = getattr(mt, "meeting_no", "") or ""
                lines.append(f"  {meeting_no} {title}")
            return "\n".join(lines)

        # ── file ──
        elif query_type == "file":
            if total == 0:
                return "暂无文件记录。"
            lines = [f"共找到 {total} 个文件："]
            for i, f in enumerate(items, 1):
                if i > 10 and total > 10:
                    lines.append(f"... 还有 {total - 10} 个")
                    break
                filename = getattr(f, "filename", "") or "未知文件"
                file_no = getattr(f, "file_no", "") or ""
                file_type = getattr(f, "file_type", "") or ""
                line = f"  {file_no} {filename}"
                if file_type:
                    line += f" ({file_type})"
                lines.append(line)
            return "\n".join(lines)

        # ── message ──
        elif query_type == "message":
            if total == 0:
                return "暂无消息记录。"
            lines = [f"共找到 {total} 条消息："]
            for i, msg in enumerate(items, 1):
                if i > 10 and total > 10:
                    lines.append(f"... 还有 {total - 10} 条")
                    break
                sender = getattr(msg, "sender_name", "") or "未知"
                content = getattr(msg, "content", "") or ""
                # 截断长消息
                if len(content) > 80:
                    content = content[:80] + "..."
                created = getattr(msg, "created_at", "") or ""
                time_str = ""
                if created:
                    try:
                        dt = datetime.fromisoformat(created)
                        time_str = dt.strftime("%m-%d %H:%M")
                    except (ValueError, TypeError):
                        pass
                line = f"  [{time_str}] {sender}: {content}"
                lines.append(line)
            return "\n".join(lines)

        # ── conversation ──
        elif query_type == "conversation":
            if total == 0:
                return "暂无活跃会话。"
            lines = [f"活跃会话 Top {min(total, 10)}："]
            for i, conv in enumerate(items[:10], 1):
                title = conv.get("title", "未知会话")
                count = conv.get("count", 0)
                last = conv.get("last_active", "")
                time_str = ""
                if last:
                    try:
                        dt = datetime.fromisoformat(last)
                        time_str = dt.strftime("%m-%d %H:%M")
                    except (ValueError, TypeError):
                        pass
                lines.append(f"  {i}. {title} ({count} 条消息, 最后活跃: {time_str})")
            return "\n".join(lines)

        # ── user ──
        elif query_type == "user":
            if total == 0:
                return "暂无注册用户。"
            lines = [f"共有 {total} 位用户："]
            for i, u in enumerate(items[:10], 1):
                name = u.get("display_name") or u.get("username") or "未知"
                status = u.get("status", "active")
                line = f"  {name}"
                if status != "active":
                    line += f" [{status}]"
                # 附带节点信息
                node_names = u.get("node_names") or []
                if node_names:
                    line += f" → 节点: {', '.join(node_names[:3])}"
                    if len(node_names) > 3:
                        line += f" ...等{len(node_names)}个"
                lines.append(line)
            if total > 10:
                lines.append(f"... 还有 {total - 10} 位")
            return "\n".join(lines)

        # ── my_nodes ──
        elif query_type == "my_nodes":
            if total == 0:
                return "未找到您负责或参与的全景节点。"
            lines = [f"您共参与 {total} 个全景节点："]
            for i, n in enumerate(items[:15], 1):
                name = n.get("node_name", "")
                status = n.get("status", "")
                role = n.get("role", "participant")
                role_label = "责任人" if role == "responsible" else "参与人"
                lines.append(f"  {i}. {name} [{status}]（{role_label}）")
            if total > 15:
                lines.append(f"... 还有 {total - 15} 个节点")
            return "\n".join(lines)

        # ── project ──
        elif query_type == "project":
            if total == 0:
                return "暂无项目。"
            lines = [f"共有 {total} 个项目："]
            for i, p in enumerate(items[:10], 1):
                name = p.get("name", "未知")
                code = p.get("code", "")
                status = p.get("status", "active")
                line = f"  {name}"
                if code:
                    line += f" ({code})"
                if status != "active":
                    line += f" [{status}]"
                lines.append(line)
            return "\n".join(lines)

        # ── journal (M8c) ──
        elif query_type == "journal":
            items = results.get("items") or []
            if not items:
                return "暂无项目事件日志。"
            lines = [f"项目事件日志（最近 {len(items)} 条）："]
            for entry in items:
                lines.append(f"  {entry}")
            return "\n".join(lines)

        # ── summary ──
        elif query_type == "summary":
            s = results.get("summary") or {}
            lines = ["项目概览："]
            lines.append(f"  事件: {s.get('events_total', 0)} 条 (pending: {s.get('events', {}).get('pending', 0)})")
            lines.append(f"  任务: {s.get('tasks_total', 0)} 条 (todo: {s.get('tasks', {}).get('todo', 0)})")
            lines.append(f"  会议: {s.get('meetings_total', 0)} 场")
            lines.append(f"  文件: {s.get('files_total', 0)} 个")
            lines.append(f"  消息: {s.get('messages_total', 0)} 条")
            lines.append(f"  活跃会话: {s.get('conversations_total', 0)} 个")
            lines.append(f"  用户: {s.get('users_total', 0)} 人")
            lines.append(f"  活跃项目: {s.get('projects_total', 0)} 个")
            return "\n".join(lines)

        # ── fallback ──
        logger.warning("Unknown query_type in format_reply: %s", query_type)
        return "查询结果暂不支持显示。"
