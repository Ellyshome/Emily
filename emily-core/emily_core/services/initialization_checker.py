"""InitializationChecker —— 项目初始化四层模型检查。

23 项必备项：T1(7) + T2(6) + T3(5) + T4(5)。
每项有明确的数据源和判定条件，纯数据驱动无需 LLM。

参照模式：emily_core/services/evolution/insight_generator.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..infrastructure.database.session import get_session
from sqlalchemy import or_
from ..infrastructure.database.models import (
    Project, User, CompanyInfo, ProjectNode, NodeDependency,
)

logger = logging.getLogger("emily.initialization_checker")

BEIJING_TZ = timezone(timedelta(hours=8))


class InitializationChecker:
    """项目初始化检查器——四层模型 23 项必备项。"""

    def check(self, project_id: str) -> dict:
        """检查项目初始化层级和缺失项。

        Args:
            project_id: 项目 ID（projects.id UUID）

        Returns:
            {
                "project_id": str,
                "tier": int (0-4),
                "tier_label": str,
                "is_activated": bool,
                "items": {key: bool, ...},   # 23 项各是否完成
                "missing": [str, ...],         # 缺失项描述
                "summary_by_tier": {T1: {...}, T2: {...}, ...},
            }
        """
        with get_session() as session:
            project = session.query(Project).filter(
                Project.id == project_id,
                or_(Project.is_deleted == False, Project.is_deleted == None),
            ).first()

            if project is None:
                return {
                    "project_id": project_id,
                    "tier": 0,
                    "tier_label": "未开始（项目不存在）",
                    "is_activated": False,
                    "items": {},
                    "missing": ["项目不存在"],
                    "summary_by_tier": {},
                }

            # ── T1：可识别（7 项）──
            t1 = {}

            # T1-1: 项目名称非空且非默认值
            t1["T1_project_name"] = bool(project.name and project.name.strip() and project.name != "未命名项目")

            # T1-2: 项目编号非空
            t1["T1_project_code"] = bool(project.code and project.code.strip())

            # T1-3: 项目地址非空
            t1["T1_project_address"] = bool(project.address and project.address.strip())

            # T1-4: 项目类型可区分
            desc = (project.description or "").strip()
            t1["T1_project_type"] = bool(desc) or (project.lifecycle_stage or 0) != 0

            # T1-5: 生命周期阶段非0
            t1["T1_lifecycle_stage"] = (project.lifecycle_stage or 0) != 0

            # T1-6: 项目管理员账户
            admins = session.query(User).filter(
                User.project_id == project_id,
                User.is_deleted == False,
                User.is_admin == True,
            ).all()
            t1["T1_admin_user"] = len(admins) > 0

            # T1-7: 管理员邮箱
            t1["T1_admin_email"] = any(u.email and u.email.strip() for u in admins)

            t1_done = sum(1 for v in t1.values() if v)
            t1_total = len(t1)

            # ── T2：有组织（6 项）──
            t2 = {}

            # 查询关联公司
            users_in_project = session.query(User).filter(
                User.project_id == project_id,
                User.is_deleted == False,
            ).all()
            company_ids = list(set(u.company for u in users_in_project if u.company))
            companies = session.query(CompanyInfo).filter(CompanyInfo.id.in_(company_ids)).all() if company_ids else []
            company_types = [c.type for c in companies if c.type]

            # T2-1: 建设单位
            t2["T2_builder_company"] = "建设单位" in company_types

            # T2-2: 项目管理/代建单位
            t2["T2_management_company"] = any(t in company_types for t in ["代建单位", "项目管理", "建设单位"])

            # T2-3: 施工总承包单位
            t2["T2_general_contractor"] = any(t in company_types for t in ["施工单位", "总包", "施工总承包"])

            # T2-4: 监理单位
            t2["T2_supervisor_company"] = any(t in company_types for t in ["监理单位", "监理"])

            # T2-5: 项目经理
            has_pm = False
            for u in users_in_project:
                try:
                    positions = json.loads(u.position or "[]")
                    if "项目经理" in positions:
                        has_pm = True
                        break
                except (json.JSONDecodeError, TypeError):
                    pass
            t2["T2_project_manager"] = has_pm

            # T2-6: 总监理工程师
            has_cs = False
            for u in users_in_project:
                try:
                    positions = json.loads(u.position or "[]")
                    if any(p in positions for p in ["总监理工程师", "总监理"]):
                        has_cs = True
                        break
                except (json.JSONDecodeError, TypeError):
                    pass
            t2["T2_chief_supervisor"] = has_cs

            t2_done = sum(1 for v in t2.values() if v)
            t2_total = len(t2)

            # ── T3：可运转（5 项）──
            t3 = {}

            nodes = session.query(ProjectNode).filter(
                ProjectNode.project_id == project_id,
                ProjectNode.is_discarded == False,
            ).all()

            # T3-1: 节点树已创建（至少1个 MILESTONE）
            milestones = [n for n in nodes if getattr(n, 'node_type', '') == 'MILESTONE']
            t3["T3_node_tree_created"] = len(milestones) > 0

            # T3-2: 关键里程碑有截止日期
            t3["T3_milestone_deadlines"] = len(milestones) > 0 and all(m.deadline for m in milestones)

            # T3-3: 关键节点有责任人
            wp_and_ms = [n for n in nodes if getattr(n, 'node_type', '') in ('MILESTONE', 'WORK_PACKAGE')]
            t3["T3_node_responsible_persons"] = len(wp_and_ms) > 0 and all(n.responsible_user_id for n in wp_and_ms)

            # T3-4: 至少1个适配 SOP
            sop_count = 0
            try:
                from ..skill.registry import SkillRegistry
                from pathlib import Path
                skill_dir = "/app/skills"
                if not Path(skill_dir).exists():
                    skill_dir = ""
                if not skill_dir:
                    dev_dir = str(Path(__file__).resolve().parents[2] / "emily-data" / "skills")
                    if Path(dev_dir).exists():
                        skill_dir = dev_dir
                if skill_dir:
                    reg = SkillRegistry(skill_directory=skill_dir)
                    reg.load()
                    sop_count = len(reg.list_sop_ids())
            except Exception as e:
                logger.debug("T3 sop check failed: %s", e, exc_info=True)
            t3["T3_sop_adapted"] = sop_count > 0

            # T3-5: 项目经理已绑定 IM
            t3["T3_pm_im_bound"] = False
            if has_pm:
                for u in users_in_project:
                    try:
                        positions = json.loads(u.position or "[]")
                        if "项目经理" in positions and u.im_bindings:
                            t3["T3_pm_im_bound"] = True
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

            t3_done = sum(1 for v in t3.values() if v)
            t3_total = len(t3)

            # ── T4：充分运转（5 项）──
            t4 = {}

            # T4-1: 全部参建单位已录入
            expected_types = {"建设单位", "代建单位", "施工单位", "监理单位", "设计单位"}
            t4["T4_all_companies"] = expected_types.issubset(set(company_types))

            # T4-2: 全部节点有责任人
            active_nodes = [n for n in nodes if n.status not in ("NOT_ACTIVATED",)]
            t4["T4_all_node_responsible"] = len(active_nodes) > 0 and all(n.responsible_user_id for n in active_nodes)

            # T4-3: 节点依赖关系已建立
            wp_nodes = [n for n in nodes if getattr(n, 'node_type', '') == 'WORK_PACKAGE']
            wp_node_ids = [n.node_id for n in wp_nodes]
            dep_count = 0
            if wp_node_ids:
                deps = session.query(NodeDependency).filter(
                    NodeDependency.node_id.in_(wp_node_ids)
                ).count()
                dep_count = deps
            t4["T4_dependency_coverage"] = len(wp_nodes) > 0 and dep_count >= len(wp_nodes) * 0.5

            # T4-4: 知识库已填充
            file_count = 0
            try:
                from ..infrastructure.database.models import File
                file_count = session.query(File).filter(File.project_id == project_id).count()
            except Exception as e:
                logger.debug("T4 file count check failed: %s", e, exc_info=True)
            t4["T4_knowledge_filled"] = file_count >= 5

            # T4-5: 晨报已成功发送至少1次
            t4["T4_morning_report_sent"] = False
            try:
                from ..infrastructure.database.models import SchedulerJobLog
                log = session.query(SchedulerJobLog).filter(
                    SchedulerJobLog.action_type == "morning_report",
                    SchedulerJobLog.status == "success",
                ).first()
                t4["T4_morning_report_sent"] = log is not None
            except Exception as e:
                logger.debug("T4 morning report check failed: %s", e, exc_info=True)

            t4_done = sum(1 for v in t4.values() if v)
            t4_total = len(t4)

        # ── 计算层级 ──
        all_items = {**t1, **t2, **t3, **t4}
        total_items = len(all_items)
        done_items = sum(1 for v in all_items.values() if v)

        tier = 0
        if t1_done >= t1_total:
            tier = 1
        if tier >= 1 and t2_done >= t2_total:
            tier = 2
        if tier >= 2 and t3_done >= t3_total:
            tier = 3
        if tier >= 3 and t4_done >= t4_total:
            tier = 4

        tier_labels = {
            0: "T0 未开始",
            1: "T1 可识别",
            2: "T2 有组织",
            3: "T3 可运转",
            4: "T4 充分运转",
        }

        missing = [k for k, v in all_items.items() if not v]
        missing_descriptions = {
            "T1_project_name": "项目名称未填写",
            "T1_project_code": "项目编号未填写",
            "T1_project_address": "项目地址未填写",
            "T1_project_type": "项目类型无法区分",
            "T1_lifecycle_stage": "生命周期阶段未设定",
            "T1_admin_user": "无项目管理员账户",
            "T1_admin_email": "管理员无可用邮箱",
            "T2_builder_company": "缺少建设单位",
            "T2_management_company": "缺少代建/管理单位",
            "T2_general_contractor": "缺少施工总承包单位",
            "T2_supervisor_company": "缺少监理单位",
            "T2_project_manager": "缺少项目经理",
            "T2_chief_supervisor": "缺少总监理工程师",
            "T3_node_tree_created": "节点树未创建",
            "T3_milestone_deadlines": "里程碑无截止日期",
            "T3_node_responsible_persons": "关键节点无责任人",
            "T3_sop_adapted": "无适配 SOP",
            "T3_pm_im_bound": "项目经理未绑定 IM",
            "T4_all_companies": "参建单位不全",
            "T4_all_node_responsible": "部分节点无责任人",
            "T4_dependency_coverage": "节点依赖关系不足50%",
            "T4_knowledge_filled": "知识库文件不足5个",
            "T4_morning_report_sent": "晨报从未成功发送",
        }
        missing_desc = [missing_descriptions.get(k, k) for k in missing]

        summary_by_tier = {
            "T1": {"done": t1_done, "total": t1_total, "pass": t1_done >= t1_total},
            "T2": {"done": t2_done, "total": t2_total, "pass": t2_done >= t2_total},
            "T3": {"done": t3_done, "total": t3_total, "pass": t3_done >= t3_total},
            "T4": {"done": t4_done, "total": t4_total, "pass": t4_done >= t4_total},
        }

        return {
            "project_id": project_id,
            "tier": tier,
            "tier_label": tier_labels.get(tier, "未知"),
            "is_activated": tier >= 3,
            "items": all_items,
            "missing": missing_desc,
            "summary_by_tier": summary_by_tier,
            "total_done": done_items,
            "total_items": total_items,
        }
