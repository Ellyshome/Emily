"""NodeDeliverableMatcher —— 节点成果匹配器。

将文件解析结果与全景节点图做匹配，高置信度时通知管理员介入确认。
"""

import difflib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6
TIME_WINDOW_DAYS = 7  # 时间窗口加分的 ± 天数

# file_type → 关键词映射（用于推断关联阶段/节点）
FILE_TYPE_KEYWORDS = {
    "证照": ["规划", "许可", "证照", "验收"],
    "合同": ["采购", "分包", "合同", "招标"],
    "图纸": ["设计", "施工图", "方案", "深化"],
    "公文": ["审批", "批复", "通知", "报告"],
    "报告": ["验收", "检测", "评估", "总结"],
}


@dataclass
class MatchCandidate:
    node_id: str
    node_name: str
    confidence: float
    match_reason: str


class NodeDeliverableMatcher:
    """
    节点成果匹配器。
    输入：(文件对象, ParseResult)
    输出：匹配候选列表，按置信度降序。
    """

    def __init__(self, outbound_bus=None):
        self._outbound_bus = outbound_bus

    async def match(self, file_obj, parse_result) -> list[MatchCandidate]:
        """
        :param file_obj: files 表的 ORM 对象
        :param parse_result: ParseResult（有 file_type, summary 字段）
        :return: 匹配候选列表
        """
        candidates: list[MatchCandidate] = []

        # 优先级 1：文件名关键词匹配（权重 0.9-1.0）
        filename = getattr(file_obj, 'filename', '') or ''
        keyword_matches = await self._match_by_filename(filename)
        candidates.extend(keyword_matches)

        # 优先级 2：文件类型 + 阶段推断（权重 0.7-0.85）
        if parse_result:
            type_matches = await self._match_by_file_type(parse_result.file_type)
            for m in type_matches:
                existing_ids = {c.node_id for c in candidates}
                if m.node_id not in existing_ids:
                    candidates.append(m)

        # 优先级 3：摘要关键词匹配（权重 0.6-0.75）
        if parse_result and parse_result.summary:
            summary_matches = await self._match_by_summary(parse_result.summary)
            for m in summary_matches:
                existing_ids = {c.node_id for c in candidates}
                if m.node_id not in existing_ids:
                    candidates.append(m)

        # 优先级 4：时间窗口加分（+0.1）
        candidates = await self._apply_time_bonus(candidates, file_obj)

        # 按置信度降序
        candidates.sort(key=lambda x: x.confidence, reverse=True)
        return candidates

    async def match_and_notify(self, file_obj, parse_result) -> bool:
        """匹配并通知管理员（高置信度才通知）。返回是否产生了匹配。"""
        candidates = await self.match(file_obj, parse_result)
        if not candidates:
            return False

        best = candidates[0]
        if best.confidence >= HIGH_CONFIDENCE:
            await self._notify_admin(file_obj, best, candidates)
            return True
        elif best.confidence >= MEDIUM_CONFIDENCE:
            logger.info(
                "中置信度匹配: 文件 %s -> 节点 %s (置信度 %.0%%)，不主动通知",
                getattr(file_obj, 'filename', '?'), best.node_name, best.confidence,
            )
            return True
        return False

    # ── 各匹配策略具体实现 ──

    async def _match_by_filename(self, filename: str) -> list[MatchCandidate]:
        """用文件名匹配节点成果关键词。从 project_nodes 表加载所有活跃节点，做关键词相似度匹配。"""
        candidates: list[MatchCandidate] = []
        try:
            from emily_core.infrastructure.database.session import get_session
            from emily_core.infrastructure.database.models import ProjectNode

            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.status.in_(["IN_PROGRESS", "CONDITIONS_NOT_MET"]),
                ).all()

            for node in nodes:
                # 用文件名与节点名做相似度匹配
                ratio = difflib.SequenceMatcher(None, filename.lower(), node.node_name.lower()).ratio()
                if ratio > 0.5:
                    confidence = min(ratio + 0.2, 1.0)  # 基础相似度 +0.2 偏移
                    candidates.append(MatchCandidate(
                        node_id=node.node_id,
                        node_name=node.node_name,
                        confidence=round(confidence, 2),
                        match_reason=f"文件名相似度 {ratio:.0%}",
                    ))
        except Exception as e:
            logger.warning("_match_by_filename 失败: %s", e)

        return candidates

    async def _match_by_file_type(self, file_type: str) -> list[MatchCandidate]:
        """用文件分类匹配对应阶段的节点。"""
        candidates: list[MatchCandidate] = []
        keywords = FILE_TYPE_KEYWORDS.get(file_type, [])
        if not keywords:
            return candidates

        try:
            from emily_core.infrastructure.database.session import get_session
            from emily_core.infrastructure.database.models import ProjectNode

            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.status.in_(["IN_PROGRESS", "CONDITIONS_NOT_MET"]),
                ).all()

            for node in nodes:
                name_lower = node.node_name.lower()
                for kw in keywords:
                    if kw.lower() in name_lower:
                        candidates.append(MatchCandidate(
                            node_id=node.node_id,
                            node_name=node.node_name,
                            confidence=0.75,
                            match_reason=f"文件类型「{file_type}」匹配关键词「{kw}」",
                        ))
                        break
        except Exception as e:
            logger.warning("_match_by_file_type 失败: %s", e)

        return candidates

    async def _match_by_summary(self, summary: str) -> list[MatchCandidate]:
        """用摘要关键词匹配节点成果描述。"""
        candidates: list[MatchCandidate] = []
        try:
            from emily_core.infrastructure.database.session import get_session
            from emily_core.infrastructure.database.models import ProjectNode

            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.status.in_(["IN_PROGRESS", "CONDITIONS_NOT_MET"]),
                ).all()

            # 将摘要切为词粒（简单按字 + 空格切）
            summary_words = set(summary) if len(summary) > 5 else set()

            for node in nodes:
                node_name_set = set(node.node_name)
                if not summary_words or not node_name_set:
                    continue
                intersection = summary_words & node_name_set
                if len(intersection) >= 2:
                    ratio = len(intersection) / min(len(summary_words), len(node_name_set))
                    if ratio > 0.3:
                        candidates.append(MatchCandidate(
                            node_id=node.node_id,
                            node_name=node.node_name,
                            confidence=round(min(ratio + 0.3, 0.85), 2),
                            match_reason=f"摘要与节点名关键词交集 {len(intersection)} 字",
                        ))
        except Exception as e:
            logger.warning("_match_by_summary 失败: %s", e)

        return candidates

    async def _apply_time_bonus(
        self, candidates: list[MatchCandidate], file_obj
    ) -> list[MatchCandidate]:
        """如果文件上传时间在节点截止时间窗口内，置信度 +0.1。"""
        try:
            from datetime import datetime, timezone, timedelta
            file_created = getattr(file_obj, 'created_at', None)
            if not file_created:
                return candidates

            # 解析文件创建时间
            if isinstance(file_created, str):
                try:
                    file_dt = datetime.fromisoformat(file_created.replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    return candidates
            elif isinstance(file_created, datetime):
                file_dt = file_created
            else:
                return candidates

            # 确保 file_dt 是 offset-aware
            if file_dt.tzinfo is None:
                file_dt = file_dt.replace(tzinfo=timezone.utc)

            from emily_core.infrastructure.database.session import get_session
            from emily_core.infrastructure.database.models import ProjectNode

            node_ids = {c.node_id for c in candidates}
            if not node_ids:
                return candidates

            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.node_id.in_(node_ids),
                ).all()

            node_deadlines = {}
            for n in nodes:
                if n.deadline:
                    try:
                        dl = datetime.fromisoformat(n.deadline)
                        if dl.tzinfo is None:
                            dl = dl.replace(tzinfo=timezone.utc)
                        node_deadlines[n.node_id] = dl
                    except (ValueError, TypeError):
                        continue

            window = timedelta(days=TIME_WINDOW_DAYS)
            for c in candidates:
                deadline = node_deadlines.get(c.node_id)
                if deadline and abs((file_dt - deadline).days) <= TIME_WINDOW_DAYS:
                    c.confidence = round(min(c.confidence + 0.1, 1.0), 2)
                    c.match_reason += " +时间窗口加分"
        except Exception as e:
            logger.warning("_apply_time_bonus 失败: %s", e)

        return candidates

    async def _notify_admin(
        self, file_obj, best: MatchCandidate, all_candidates: list[MatchCandidate]
    ) -> None:
        """向管理员推送匹配通知（含匹配详情）。"""
        filename = getattr(file_obj, 'filename', '未知文件')
        file_no = getattr(file_obj, 'file_no', '')

        # 构建通知消息
        lines = [
            "🔗 文件自动匹配通知",
            "──────────────",
            f"文件：{filename} ({file_no})",
            f"最佳匹配节点：{best.node_name}",
            f"置信度：{best.confidence:.0%}",
            f"匹配原因：{best.match_reason}",
        ]

        if len(all_candidates) > 1:
            lines.append("──────────────")
            lines.append("其他候选项：")
            for i, c in enumerate(all_candidates[1:4], 1):
                lines.append(f"  {i}. {c.node_name} (置信度 {c.confidence:.0%})")

        notification = "\n".join(lines)

        if self._outbound_bus:
            try:
                self._outbound_bus.publish("reply", {
                    "content": notification,
                    "source": "scheduler:node_deliverable_matcher",
                })
                logger.info("节点匹配通知已发送: %s -> %s", filename, best.node_name)
            except Exception as e:
                logger.warning("节点匹配通知发送失败: %s", e)
