"""ProjectWorldBookBuilder —— 项目世界书构建服务。

查询数据库聚合七层认知，生成 content_json + content_text。
纯数据驱动，无需 LLM。语义层（项目概述）在增量更新时由 LLM 生成。

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

logger = logging.getLogger("emily.world_book_builder")

BEIJING_TZ = timezone(timedelta(hours=8))

# 生命周期阶段标签
LIFECYCLE_LABELS = {0: "立项", 1: "规划设计", 2: "工程施工", 3: "交付结算"}


class ProjectWorldBookBuilder:
    """项目世界书构建器。"""

    def build(self, project_id: str, *, generated_by: str = "manual", dry_run: bool = False) -> dict:
        """构建/重建单个项目的世界书。

        Args:
            project_id: 项目 ID（projects.id UUID）
            generated_by: 生成来源标记
            dry_run: 预览模式，不写 DB

        Returns:
            构建结果 dict，含 content_json, content_text, initialization_tier 等
        """
        # 采集七层数据
        ontology = self._build_ontology(project_id)
        personnel = self._build_personnel(project_id)
        structure = self._build_structure(project_id)
        temporal = self._build_temporal(project_id)
        relation = self._build_relation(project_id)
        knowledge = self._build_knowledge(project_id)
        introspection = self._build_introspection(project_id)

        content_json = {
            "ontology": ontology,
            "personnel": personnel,
            "structure": structure,
            "temporal": temporal,
            "relation": relation,
            "knowledge": knowledge,
            "introspection": introspection,
        }

        content_text = self._format_content_text(content_json)

        # 估算 token 数（中文约 1.5 字/token）
        token_count = int(len(content_text) / 1.5) if content_text else 0

        # 初始化层级
        init_tier = introspection.get("initialization_tier", 0)
        init_status = introspection.get("initialization_status", {})
        is_activated = introspection.get("is_activated", False)

        # 每层初始版本号
        layer_versions = {k: 1 for k in content_json.keys()}

        result = {
            "project_id": project_id,
            "content_json": json.dumps(content_json, ensure_ascii=False),
            "content_text": content_text,
            "layer_versions": json.dumps(layer_versions),
            "initialization_tier": init_tier,
            "initialization_status": json.dumps(init_status, ensure_ascii=False),
            "is_activated": is_activated,
            "token_count": token_count,
            "generated_by": generated_by,
            "status": "preview" if dry_run else "built",
        }

        if not dry_run:
            # 检查是否已存在 → 更新或创建
            existing = ProjectWorldBookRepo.get_by_project(project_id)
            if existing:
                # 更新：递增版本号
                new_version = existing.version + 1
                # 合并 layer_versions：已有层版本保留，新层版本为1
                old_lv = json.loads(existing.layer_versions or "{}")
                merged_lv = {**old_lv}
                for k in layer_versions:
                    if k in merged_lv:
                        merged_lv[k] += 1
                    else:
                        merged_lv[k] = 1

                ProjectWorldBookRepo.update_content(
                    project_id=project_id,
                    content_json=result["content_json"],
                    content_text=content_text,
                    layer_versions=json.dumps(merged_lv),
                    version=new_version,
                    initialization_tier=init_tier,
                    initialization_status=result["initialization_status"],
                    is_activated=is_activated,
                    token_count=token_count,
                    generated_by=generated_by,
                )
                result["version"] = new_version
            else:
                wb = ProjectWorldBookRepo.create(
                    project_id=project_id,
                    content_json=result["content_json"],
                    content_text=content_text,
                    layer_versions=result["layer_versions"],
                    initialization_tier=init_tier,
                    initialization_status=result["initialization_status"],
                    is_activated=is_activated,
                    token_count=token_count,
                    generated_by=generated_by,
                )
                result["version"] = 1

        return result

    # ── 七层构建方法 ──

    def _build_ontology(self, project_id: str) -> dict:
        """层1：本体认知——项目身份、生命周期、参建方。"""
        try:
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id, Project.is_deleted == False).first()
                if project is None:
                    return {"name": "", "code": "", "error": "项目不存在"}

                # 查询关联公司
                companies = []
                users = session.query(User).filter(User.project_id == project_id, User.is_deleted == False).all()
                company_ids = list(set(u.company for u in users if u.company))
                if company_ids:
                    company_records = session.query(CompanyInfo).filter(CompanyInfo.id.in_(company_ids)).all()
                    for c in company_records:
                        companies.append({
                            "name": c.company_name,
                            "type": c.type or "",
                            "role": c.business_desc or "",
                        })

                stage = project.lifecycle_stage or 0
                return {
                    "name": project.name or "",
                    "code": project.code or "",
                    "address": project.address or "",
                    "lifecycle_stage": stage,
                    "lifecycle_stage_label": LIFECYCLE_LABELS.get(stage, "未知"),
                    "organizations": companies,
                    "project_summary": f"{project.name or '未命名项目'}，当前处于{LIFECYCLE_LABELS.get(stage, '未知')}阶段",
                }
        except Exception as e:
            logger.error("_build_ontology failed: %s", e)
            return {"name": "", "code": "", "error": str(e)}

    def _build_personnel(self, project_id: str) -> dict:
        """层2：人员认知——关键人员、职责边界。"""
        try:
            with get_session() as session:
                users = session.query(User).filter(
                    User.project_id == project_id,
                    User.is_deleted == False,
                    User.status == "active",
                ).all()

                key_personnel = []
                department_leads = []
                for u in users:
                    # 解析职位
                    positions = []
                    try:
                        positions = json.loads(u.position or "[]")
                    except (json.JSONDecodeError, TypeError):
                        positions = []

                    company_name = ""
                    if u.company:
                        company = session.query(CompanyInfo).filter(CompanyInfo.id == u.company).first()
                        if company:
                            company_name = company.company_name

                    person = {
                        "name": u.username or "",
                        "role": ", ".join(positions) if positions else "",
                        "company": company_name,
                        "level": u.level or 1,
                        "is_admin": u.is_admin or False,
                    }

                    # 关键人员：管理员、项目经理、总监理
                    if u.is_admin or any(p in ["项目经理", "总监理工程师", "总监理"] for p in positions):
                        key_personnel.append(person)
                    elif positions:
                        # 部门负责人
                        for p in positions:
                            if p not in ["项目经理", "总监理工程师", "总监理"]:
                                department_leads.append({"department": p, "name": u.username or ""})

                return {
                    "key_personnel": key_personnel,
                    "department_leads": department_leads,
                    "total_users": len(users),
                }
        except Exception as e:
            logger.error("_build_personnel failed: %s", e)
            return {"key_personnel": [], "department_leads": [], "error": str(e)}

    def _build_structure(self, project_id: str) -> dict:
        """层3：结构认知——节点树拓扑、整体进度、里程碑状态。"""
        try:
            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                ).all()

                total = len(nodes)
                completed = sum(1 for n in nodes if n.status == "COMPLETED")
                in_progress = sum(1 for n in nodes if n.status == "IN_PROGRESS")
                conditions_not_met = sum(1 for n in nodes if n.status == "CONDITIONS_NOT_MET")
                not_activated = sum(1 for n in nodes if n.status == "NOT_ACTIVATED")

                now_beijing = datetime.now(BEIJING_TZ)
                overdue = 0
                for n in nodes:
                    if n.status != "COMPLETED" and n.deadline:
                        try:
                            dl = datetime.fromisoformat(n.deadline)
                            if dl.tzinfo is None:
                                dl = dl.replace(tzinfo=BEIJING_TZ)
                            if dl < now_beijing:
                                overdue += 1
                        except (ValueError, TypeError):
                            pass

                # 里程碑
                milestones = []
                for n in nodes:
                    if getattr(n, 'node_type', '') == 'MILESTONE':
                        milestones.append({
                            "name": n.node_name,
                            "status": n.status,
                            "progress": n.progress or "0.00",
                            "deadline": n.deadline or "",
                        })

                # 整体进度加权汇总
                total_progress = 0.0
                if total > 0:
                    total_progress = sum(float(n.progress or "0") for n in nodes) / total

                return {
                    "total_nodes": total,
                    "completed": completed,
                    "in_progress": in_progress,
                    "conditions_not_met": conditions_not_met,
                    "not_activated": not_activated,
                    "overdue": overdue,
                    "overall_progress": f"{total_progress:.1f}%",
                    "milestones": milestones[:10],
                }
        except Exception as e:
            logger.error("_build_structure failed: %s", e)
            return {"total_nodes": 0, "error": str(e)}

    def _build_temporal(self, project_id: str) -> dict:
        """层4：时间认知——近期事件、即将到期、已逾期。"""
        try:
            now_beijing = datetime.now(BEIJING_TZ)
            week_ago = (now_beijing - timedelta(days=7)).strftime("%Y-%m-%d")
            week_later = (now_beijing + timedelta(days=7)).strftime("%Y-%m-%d")

            with get_session() as session:
                # 近期事件
                events = session.query(Event).filter(
                    Event.project_id == project_id,
                ).order_by(Event.created_at.desc()).limit(5).all()

                recent_events = []
                for e in events:
                    recent_events.append({
                        "date": (e.event_date or e.created_at or "")[:10],
                        "summary": e.title or "",
                        "type": e.event_type or "",
                    })

                # 即将到期 + 已逾期节点
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                    ProjectNode.status != "COMPLETED",
                ).all()

                upcoming_deadlines = []
                overdue_items = []
                for n in nodes:
                    if not n.deadline:
                        continue
                    try:
                        dl = datetime.fromisoformat(n.deadline)
                        if dl.tzinfo is None:
                            dl = dl.replace(tzinfo=BEIJING_TZ)
                        dl_str = dl.strftime("%Y-%m-%d")

                        if dl < now_beijing:
                            overdue_items.append({
                                "name": n.node_name,
                                "deadline": dl_str,
                                "status": n.status,
                            })
                        elif dl_str <= week_later:
                            upcoming_deadlines.append({
                                "name": n.node_name,
                                "deadline": dl_str,
                                "status": n.status,
                            })
                    except (ValueError, TypeError):
                        pass

                return {
                    "recent_events": recent_events,
                    "upcoming_deadlines": upcoming_deadlines[:5],
                    "overdue_items": overdue_items[:5],
                }
        except Exception as e:
            logger.error("_build_temporal failed: %s", e)
            return {"recent_events": [], "upcoming_deadlines": [], "overdue_items": [], "error": str(e)}

    def _build_relation(self, project_id: str) -> dict:
        """层5：关系认知——上下游依赖、阻塞。"""
        try:
            with get_session() as session:
                # 查依赖
                deps = session.query(NodeDependency).all()
                # 过滤属于本项目节点的依赖
                project_node_ids = set(
                    n.node_id for n in session.query(ProjectNode).filter(
                        ProjectNode.project_id == project_id,
                        ProjectNode.is_discarded == False,
                    ).all()
                )

                key_dependencies = []
                blocked_nodes = []

                for dep in deps:
                    if dep.node_id not in project_node_ids:
                        continue
                    upstream_node = session.query(ProjectNode).filter(
                        ProjectNode.node_id == dep.depends_on_node_id
                    ).first()
                    downstream_node = session.query(ProjectNode).filter(
                        ProjectNode.node_id == dep.node_id
                    ).first()

                    if upstream_node and downstream_node:
                        key_dependencies.append({
                            "upstream": upstream_node.node_name,
                            "downstream": downstream_node.node_name,
                            "deliverable": dep.depends_on_deliverable_id,
                        })

                        # 上游未完成 → 下游被阻塞
                        if upstream_node.status != "COMPLETED":
                            blocked_nodes.append({
                                "node": downstream_node.node_name,
                                "blocked_by": f"{upstream_node.node_name}未完成",
                                "impact": "",
                            })

                return {
                    "key_dependencies": key_dependencies[:10],
                    "blocked_nodes": blocked_nodes[:5],
                }
        except Exception as e:
            logger.error("_build_relation failed: %s", e)
            return {"key_dependencies": [], "blocked_nodes": [], "error": str(e)}

    def _build_knowledge(self, project_id: str) -> dict:
        """层6：知识认知——SOP 覆盖、知识库地图、认知盲区。"""
        try:
            from ..skill.registry import SkillRegistry

            sop_ids = []
            try:
                skill_dir = "/app/skills"
                if not __import__('pathlib').Path(skill_dir).exists():
                    skill_dir = ""
                if not skill_dir:
                    from pathlib import Path as _P
                    dev_dir = str(_P(__file__).resolve().parents[2] / "emily-data" / "skills")
                    if _P(dev_dir).exists():
                        skill_dir = dev_dir
                if skill_dir:
                    reg = SkillRegistry(skill_directory=skill_dir)
                    reg.load()
                    sop_ids = reg.list_sop_ids()
            except Exception as e:
                logger.warning("SkillRegistry load failed: %s", e, exc_info=True)

            # RAG 信息
            rag_available = False
            rag_collections = []
            # 注意：独立运行时无法获取 core._rag_provider，此处留空
            # 运行时由 M8 集成后从 core 注入

            return {
                "sop_count": len(sop_ids),
                "sop_ids": sop_ids[:15],
                "rag_available": rag_available,
                "rag_collections": rag_collections,
                "coverage_gaps": [],
            }
        except Exception as e:
            logger.error("_build_knowledge failed: %s", e)
            return {"sop_count": 0, "sop_ids": [], "rag_available": False, "rag_collections": [], "error": str(e)}

    def _build_introspection(self, project_id: str) -> dict:
        """层7：自省认知——初始化状态、能力边界。委托 InitializationChecker。

        注意：M3 尚未实现时，此方法返回最小骨架。
        M3 完成后，此处改为调用 InitializationChecker。
        """
        # 先做最小实现：检查项目基本信息是否齐全
        try:
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id, Project.is_deleted == False).first()
                if project is None:
                    return {
                        "initialization_tier": 0,
                        "initialization_status": {},
                        "is_activated": False,
                        "missing_items": ["项目不存在"],
                    }

                # T1 基本检查
                init_status = {}
                init_status["T1_project_name"] = bool(project.name and project.name != "未命名项目")
                init_status["T1_project_code"] = bool(project.code)
                init_status["T1_project_address"] = bool(project.address)
                init_status["T1_lifecycle_stage"] = (project.lifecycle_stage or 0) != 0

                # 管理员检查
                admins = session.query(User).filter(
                    User.project_id == project_id,
                    User.is_deleted == False,
                    User.is_admin == True,
                ).all()
                init_status["T1_admin_user"] = len(admins) > 0
                init_status["T1_admin_email"] = any(u.email for u in admins if u.email)

                # 统计 T1 完成项
                t1_items = [k for k, v in init_status.items() if k.startswith("T1_") and v]
                t1_total = sum(1 for k in init_status if k.startswith("T1_"))
                t1_done = len(t1_items)

                tier = 0
                if t1_done >= t1_total:
                    tier = 1

                missing = [k for k, v in init_status.items() if not v]

                return {
                    "initialization_tier": tier,
                    "initialization_status": init_status,
                    "is_activated": tier >= 3,
                    "missing_items": missing,
                }
        except Exception as e:
            logger.error("_build_introspection failed: %s", e)
            return {
                "initialization_tier": 0,
                "initialization_status": {},
                "is_activated": False,
                "missing_items": [str(e)],
            }

    # ── 文本格式化 ──

    def _format_content_text(self, content_json: dict) -> str:
        """将七层 JSON 格式化为纯文本摘要（注入 prompt）。目标 300-500 字。"""
        lines = []

        # 层1：本体
        o = content_json.get("ontology", {})
        if o.get("name"):
            code = f"（{o['code']}）" if o.get("code") else ""
            lines.append(f"📋 项目：{o['name']}{code}")
            addr = o.get("address", "")
            stage = o.get("lifecycle_stage_label", "")
            if addr or stage:
                lines.append(f"📍 {addr} ｜ 阶段：{stage}")
            orgs = o.get("organizations", [])
            if orgs:
                org_str = " / ".join(f"{c['name']}({c['type']})" for c in orgs[:4])
                lines.append(f"🏗 {org_str}")

        # 层2：人员
        p = content_json.get("personnel", {})
        kp = p.get("key_personnel", [])
        if kp:
            ppl_str = " / ".join(f"{u['name']}({u['role']})" for u in kp[:4])
            lines.append(f"👥 {ppl_str}")

        # 层3：结构
        s = content_json.get("structure", {})
        if s.get("total_nodes", 0) > 0:
            lines.append(
                f"📊 {s['total_nodes']}节点：{s.get('completed', 0)}完成 / "
                f"{s.get('in_progress', 0)}进行中 / {s.get('overdue', 0)}逾期 ｜ "
                f"整体{s.get('overall_progress', '0%')}"
            )
            ms = s.get("milestones", [])
            if ms:
                ms_str = " ｜ ".join(
                    f"{m['name']}{'✓' if m['status']=='COMPLETED' else m.get('progress','')}"
                    for m in ms[:3]
                )
                lines.append(f"🏁 {ms_str}")

        # 层4：时间
        t = content_json.get("temporal", {})
        re = t.get("recent_events", [])
        if re:
            ev_str = " / ".join(f"{e['date'][5:] if len(e.get('date',''))>=5 else e.get('date','')}{e['summary']}" for e in re[:3])
            lines.append(f"📝 近期：{ev_str}")
        ud = t.get("upcoming_deadlines", [])
        if ud:
            ud_str = " / ".join(f"{d['name']}({d['deadline'][5:] if len(d.get('deadline',''))>=5 else d.get('deadline','')})" for d in ud[:3])
            lines.append(f"⏰ 7天内：{ud_str}")
        oi = t.get("overdue_items", [])
        if oi:
            oi_str = " / ".join(f"{i['name']}({i['deadline'][5:] if len(i.get('deadline',''))>=5 else i.get('deadline','')})" for i in oi[:3])
            lines.append(f"🔴 逾期：{oi_str}")

        # 层5：关系（仅在有阻塞时显示）
        r = content_json.get("relation", {})
        bn = r.get("blocked_nodes", [])
        if bn:
            bn_str = " / ".join(f"{b['blocked_by']} → {b['node']}等待" for b in bn[:2])
            lines.append(f"🔗 阻塞：{bn_str}")

        # 层7：初始化状态
        intro = content_json.get("introspection", {})
        tier = intro.get("initialization_tier", 0)
        tier_labels = {0: "未开始", 1: "T1 可识别", 2: "T2 有组织", 3: "T3 可运转", 4: "T4 充分运转"}
        if tier < 3:
            missing = intro.get("missing_items", [])
            missing_str = " / ".join(missing[:3])
            lines.append(f"🟡 {tier_labels.get(tier, '未知')}级 — 缺失：{missing_str}")
        else:
            lines.append(f"🟢 {tier_labels.get(tier, '未知')}级")

        return "\n".join(lines)
