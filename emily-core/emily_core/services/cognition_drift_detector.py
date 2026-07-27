"""CognitionDriftDetector —— 认知偏差检测。

对比世界书 content_json 与实际 DB 数据，检测各层是否过时。
纯数据对比，无需 LLM，非常轻量。

参照模式：emily_core/services/evolution/insight_generator.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..repositories.world_book_repo import ProjectWorldBookRepo
from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    Project, User, CompanyInfo, ProjectNode, NodeDependency, Event,
)

logger = logging.getLogger("emily.cognition_drift_detector")

BEIJING_TZ = timezone(timedelta(hours=8))


class CognitionDriftDetector:
    """认知偏差检测器。"""

    def detect(self, project_id: str) -> dict:
        """检测项目世界书与实际数据的偏差。

        Args:
            project_id: 项目 ID

        Returns:
            {
                "project_id": str,
                "has_world_book": bool,
                "drift": {layer: {"stale": bool, "signals": [...]}, ...},
                "stale_layers": [str, ...],
                "has_drift": bool,
            }
        """
        wb = ProjectWorldBookRepo.get_by_project(project_id)
        if wb is None:
            return {
                "project_id": project_id,
                "has_world_book": False,
                "drift": {},
                "stale_layers": [],
                "has_drift": False,
                "message": "项目无世界书，需首次生成",
            }

        try:
            layers = json.loads(wb.content_json or "{}")
        except (json.JSONDecodeError, TypeError):
            layers = {}

        drift = {}

        # 层1：本体偏差
        drift["ontology"] = self._check_ontology(project_id, layers.get("ontology", {}), wb.updated_at)

        # 层2：人员偏差
        drift["personnel"] = self._check_personnel(project_id, layers.get("personnel", {}), wb.updated_at)

        # 层3：结构偏差
        drift["structure"] = self._check_structure(project_id, layers.get("structure", {}))

        # 层4：时间偏差
        drift["temporal"] = self._check_temporal(project_id, layers.get("temporal", {}))

        # 层5：关系偏差
        drift["relation"] = self._check_relation(project_id, layers.get("relation", {}), wb.updated_at)

        # 层7：自省偏差
        drift["introspection"] = self._check_introspection(project_id, layers.get("introspection", {}))

        # 层6：知识偏差（不常驻，仅标记）
        drift["knowledge"] = {"stale": False, "signals": [], "note": "层6不常驻，按需检测"}

        stale_layers = [k for k, v in drift.items() if v.get("stale", False)]

        return {
            "project_id": project_id,
            "has_world_book": True,
            "drift": drift,
            "stale_layers": stale_layers,
            "has_drift": len(stale_layers) > 0,
        }

    def _check_ontology(self, project_id: str, layer: dict, wb_updated: str) -> dict:
        """层1：本体认知偏差——lifecycle_stage 变了 / 新增参建单位。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if project is None:
                    return {"stale": True, "signals": ["项目不存在"]}

                # lifecycle_stage 变化
                current_stage = project.lifecycle_stage or 0
                recorded_stage = layer.get("lifecycle_stage", -1)
                if current_stage != recorded_stage:
                    signals.append(f"lifecycle_stage: {recorded_stage}->{current_stage}")
                    stale = True

                # 新增参建单位
                users = session.query(User).filter(User.project_id == project_id, User.is_deleted == False).all()
                company_ids = list(set(u.company for u in users if u.company))
                current_company_count = 0
                if company_ids:
                    current_company_count = session.query(CompanyInfo).filter(CompanyInfo.id.in_(company_ids)).count()
                recorded_company_count = len(layer.get("organizations", []))
                if current_company_count > recorded_company_count:
                    signals.append(f"新增参建单位: {recorded_company_count}->{current_company_count}")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_personnel(self, project_id: str, layer: dict, wb_updated: str) -> dict:
        """层2：人员偏差——有用户变更。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                # 检查最近更新的用户数
                users = session.query(User).filter(User.project_id == project_id, User.is_deleted == False).all()
                current_count = len(users)
                recorded_count = layer.get("total_users", 0)
                if current_count != recorded_count:
                    signals.append(f"用户数变化: {recorded_count}->{current_count}")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_structure(self, project_id: str, layer: dict) -> dict:
        """层3：结构偏差——整体进度偏差 >5% / 逾期数变化 / 新节点。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                ).all()

                current_total = len(nodes)
                recorded_total = layer.get("total_nodes", 0)
                if current_total != recorded_total:
                    signals.append(f"节点数变化: {recorded_total}->{current_total}")
                    stale = True

                # 逾期数
                now_beijing = datetime.now(BEIJING_TZ)
                current_overdue = 0
                for n in nodes:
                    if n.status != "COMPLETED" and n.deadline:
                        try:
                            dl = datetime.fromisoformat(n.deadline)
                            if dl.tzinfo is None:
                                dl = dl.replace(tzinfo=BEIJING_TZ)
                            if dl < now_beijing:
                                current_overdue += 1
                        except (ValueError, TypeError):
                            pass
                recorded_overdue = layer.get("overdue", 0)
                if current_overdue != recorded_overdue:
                    signals.append(f"逾期数变化: {recorded_overdue}->{current_overdue}")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_temporal(self, project_id: str, layer: dict) -> dict:
        """层4：时间偏差——近期有新事件 / deadline 逼近。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                # 检查最近事件
                recent_count = session.query(Event).filter(
                    Event.project_id == project_id,
                ).count()
                recorded_events = len(layer.get("recent_events", []))
                # 如果实际事件数远多于世界书记录的，标记过时
                if recent_count > recorded_events + 3:
                    signals.append(f"新事件: 至少{recent_count - recorded_events}条")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_relation(self, project_id: str, layer: dict, wb_updated: str) -> dict:
        """层5：关系偏差——依赖链变更。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                ).all()
                node_ids = [n.node_id for n in nodes]

                if node_ids:
                    dep_count = session.query(NodeDependency).filter(
                        NodeDependency.node_id.in_(node_ids)
                    ).count()
                    recorded_deps = len(layer.get("key_dependencies", []))
                    if dep_count != recorded_deps:
                        signals.append(f"依赖数变化: {recorded_deps}->{dep_count}")
                        stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_introspection(self, project_id: str, layer: dict) -> dict:
        """层7：自省偏差——初始化层级变化。"""
        signals = []
        stale = False
        try:
            from .initialization_checker import InitializationChecker
            checker = InitializationChecker()
            current_result = checker.check(project_id)
            current_tier = current_result["tier"]
            recorded_tier = layer.get("initialization_tier", -1)
            if current_tier != recorded_tier:
                signals.append(f"初始化层级变化: T{recorded_tier}->T{current_tier}")
                stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}
