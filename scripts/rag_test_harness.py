"""rag_test_harness.py — RAG 系统化改造验收测试执行器。

支持两种模式：

1) 独立模式（默认 / --standalone）
   按《RAG系统化改造_测试计划_V1.md》在自建隔离数据上执行 TC-01~TC-17：
     1. 生成 18 个测试文件（md/txt/pdf/docx）
     2. 写入种子数据（2 企业 / 3 用户 / 2 项目 / 3 节点 / 关系表）
     3. 解析 + 结构分块 + 去重 + TEI 向量化入库（doc_id=files.id）
     4. 执行 TC-01~TC-17 并输出结构化结果
     5. 清理本次测试写入的记录（不污染数据）

2) env 模式（--env）
   复用 env-test 搭建的 EMERALD-01 模拟环境（真实用户/公司/项目），把
   RAG 验收的 18 个测试文件作为当前模拟项目的一部分建库并保留：
     - 主项目 EMERALD-01（公司A=翠湖地产/建设单位 视角）：大部分文件 + N1(specific) + N2(all_project_files)
     - 供应商隔离项目（公司B=鑫达建材供应商 视角）：N3(specific) + #11/#12
     - 角色映射：U1=罗永强(L5 内部)、U2=周文斌(L1 供应商 公开)、U3=周访客(无公司 L1 访客)
     - 默认保留数据（重建由 env-test setup_test_env.ps1 触发）
   子开关：
     --setup    仅建库（无 TC），供 env-test 阶段调用
     --rebuild  强制删除既有 RAG 库后重建（默认幂等：已存在则跳过建库）

用法：
    uv run python scripts/rag_test_harness.py                       # 独立模式：执行并清理
    uv run python scripts/rag_test_harness.py --keep                # 独立模式：执行后保留
    uv run python scripts/rag_test_harness.py --env                 # env 模式：库缺失则建，跑 TC，保留
    uv run python scripts/rag_test_harness.py --env --setup         # env 模式：仅建库（供 setup_test_env.ps1）
    uv run python scripts/rag_test_harness.py --env --rebuild       # env 模式：强制重建库并跑 TC
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

DB_URL = os.environ.get(
    "EMILY_DATABASE_URL",
    "postgresql://emily:emily_secret_2026@localhost:25432/emily",
)
TEI_URL = os.environ.get("EMILY_TEI_URL", "http://localhost:8082")

# 测试数据唯一标记（用于清理与识别）
MARK = "RAGTEST"

# ── env 模式（复用 env-test 模拟环境）──
ENV_MARK = "RAGENV"                    # env 库文件 file_no 前缀，如 RAGENV-F01
ENV_PROJECT_MAIN = "EMERALD-01"        # 主项目（公司A/翠湖地产 视角）
ENV_PROJECT_ISO = "EMERALD-RAG-B"      # 供应商隔离容器项目（公司B/鑫达 视角）
ENV_NODES = {"N1": "EMR-RAG-N1", "N2": "EMR-RAG-N2", "N3": "EMR-RAG-N3"}
ENV_ROLE_U1 = "罗永强"                  # 内部级（公司A / 建设单位 L5）
ENV_ROLE_U2 = "周文斌"                  # 公开级（公司B / 供应商 L1）
ENV_ROLE_U3 = "周访客"                  # 无公司 L1 访客（env-test 人员池新增）


def _load_env() -> None:
    env_path = _HERE.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


# ══════════════════════════════════════════════════════════════════════════
# 测试文件定义（对齐测试计划第四节）
# ══════════════════════════════════════════════════════════════════════════

# 每条：filename, format, confidentiality, uploader(U1/U2), node(N1/N2/N3/""), explicit(U2/""), project(PA/PB), content
_FILE_SPECS = [
    ("公开制度A.md", "md", 0, "U1", "", "", "PA",
     "# 公司考勤管理制度（公开）\n\n[[RAGTEST-01-PUB-NONE]]\n\n本文档为公司全员公开的考勤管理制度，适用于所有在职员工。\n\n1. 工作日上下班时间为 9:00 - 18:00。\n2. 迟到、早退按次记录，每月累计超过 3 次将影响绩效。\n3. 请假需提前 1 个工作日通过 OA 系统提交审批。\n"),
    ("公开流程B.txt", "txt", 0, "U2", "", "", "PB",
     "公司差旅报销流程（公开）\n[[RAGTEST-02-PUB-NONE]]\n\n1. 出差前填写出差申请单并经部门负责人审批。\n2. 保留票据原件，出差结束后 5 个工作日内提交报销。\n3. 住宿标准按职级执行，超支部分自理。\n"),
    ("机密图纸C.pdf", "pdf", 2, "U1", "", "", "PA",
     "主楼结构设计图纸说明（机密）\n[[RAGTEST-03-CON-U1OWN]]\n\n本图纸为在建项目核心结构设计，密级为机密。\n基础采用桩筏基础，主楼抗震设防烈度为 8 度。\n该文件仅上传者本人与系统管理员可见，未经授权严禁外传。\n"),
    ("机密方案D.docx", "docx", 2, "U2", "", "", "PB",
     "专项施工方案（机密）\n[[RAGTEST-04-CON-U2OWN]]\n\n本方案为基坑支护专项施工方案，密级为机密。\n采用地下连续墙 + 内支撑体系，开挖深度 12 米。\n"),
    ("节点A公开E.md", "md", 0, "U1", "N1", "", "PA",
     "# 节点A 施工进度说明（公开）\n\n[[RAGTEST-05-PUB-N1]]\n\n样板段主体结构已封顶，进入二次结构施工。\n当前进度正常，无重大质量安全隐患。\n"),
    ("节点A内部F.md", "md", 1, "U1", "N1", "", "PA",
     "# 节点A 技术交底（内部）\n\n[[RAGTEST-06-INT-N1]]\n\n本次交底内容：防水工程施工工艺与验收标准。\n防水层采用两道 SBS 卷材，搭接宽度不小于 100mm。\n"),
    ("节点A机密G.md", "md", 2, "U1", "N1", "", "PA",
     "# 节点A 成本测算数据（机密）\n\n[[RAGTEST-07-CON-N1]]\n\n样板段综合成本测算：人工费 120 万，材料费 340 万。\n目标成本偏差控制在 3% 以内。\n"),
    ("节点A机密H.md", "md", 2, "U1", "N1", "", "PA",
     "# 节点A 投标报价（机密）\n\n[[RAGTEST-08-CON-N1]]\n\n本节点最终投标报价为 8,860 万元，密级机密。\n报价构成与下浮空间属机密信息，仅上传者/系统管理员/授权人查看。\n"),
    ("全项目文件I.md", "md", 1, "U2", "", "", "PA",
     "# 项目周报（内部）\n\n[[RAGTEST-09-INT-N2]]\n\n本周完成：样板段砌体完成 80%，机电管线预埋 60%。\n下周计划：完成样板段全部砌体并开始抹灰。\n"),
    ("全项目文件J.md", "md", 2, "U1", "N2", "", "PA",
     "# 项目合同摘要（机密）\n\n[[RAGTEST-10-CON-N2]]\n\n总承包合同金额 2.4 亿元，工期 720 日历天。\n付款节点与质保金比例属机密信息。\n"),
    ("节点B公开K.md", "md", 0, "U2", "N3", "", "PB",
     "# 节点B 安全须知（公开）\n\n[[RAGTEST-11-PUB-N3]]\n\n进入施工现场必须佩戴安全帽，禁止酒后作业。\n高处作业需系挂安全带。\n"),
    ("节点B机密L.md", "md", 2, "U2", "N3", "", "PB",
     "# 节点B 供应商名单（机密）\n\n[[RAGTEST-12-CON-N3]]\n\n本项目主要供应商：钢筋供应商 3 家、混凝土供应商 2 家。\n供应商报价与结算价格属机密。\n"),
    ("显式授权M.md", "md", 2, "U1", "", "U2", "PA",
     "# 专家评审会议纪要（机密，显式授权）\n\n[[RAGTEST-13-CON-EXP]]\n\n评审结论：方案总体可行，建议优化地下室防水节点。\n本纪要密级机密，已单独授权特定人员查看。\n"),
    ("格式测试N.pdf", "pdf", 0, "U1", "", "", "PA",
     "PDF 格式解析测试文档（公开）\n[[RAGTEST-14-PUB-PDF]]\n\n本文件用于验证 PDF 文档解析与向量化入库能力。\n包含一个可检索的锚点词以确认检索命中。\n"),
    ("格式测试O.docx", "docx", 0, "U1", "", "", "PA",
     "DOCX 格式解析测试文档（公开）\n[[RAGTEST-15-PUB-DOCX]]\n\n本文件用于验证 DOCX 文档解析与向量化入库能力。\n包含一个可检索的锚点词以确认检索命中。\n"),
    ("重复内容P.md", "md", 0, "U1", "", "", "PA",
     "# 去重测试文档（公开）\n\n[[RAGTEST-16-PUB-DUP]]\n\n本文件内容与另一份文件完全相同，用于验证入库去重能力。\n重复内容应只保留一份入库。\n"),
    ("重复内容P_copy.md", "md", 0, "U1", "", "", "PA",
     "# 去重测试文档（公开）\n\n[[RAGTEST-16-PUB-DUP]]\n\n本文件内容与另一份文件完全相同，用于验证入库去重能力。\n重复内容应只保留一份入库。\n"),
    ("长文档Q.md", "md", 0, "U1", "", "", "PA",
     "# 施工现场管理手册（公开）\n\n[[RAGTEST-18-PUB-LONG]]\n\n## 第一章 总则\n本手册用于规范施工现场各项管理工作。\n\n## 第二章 安全管理\n### 2.1 安全教育\n新进场人员必须接受三级安全教育。\n\n### 2.2 安全检查\n项目部每周组织一次安全大检查。\n\n## 第三章 质量管理\n### 3.1 材料验收\n进场材料需提供合格证并抽样送检。\n\n### 3.2 隐蔽工程验收\n隐蔽工程须经监理验收合格后方可覆盖。\n\n## 第四章 进度管理\n编制施工总进度计划并按月滚动更新。\n"),
]


def _make_pdf(path: Path, text: str) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontname="china-s", fontsize=11)
    doc.save(str(path))
    doc.close()


def _make_docx(path: Path, text: str) -> None:
    import docx
    d = docx.Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    d.save(str(path))


# ══════════════════════════════════════════════════════════════════════════
# 主体
# ══════════════════════════════════════════════════════════════════════════

class Harness:
    def __init__(self, keep: bool = False, env_mode: bool = False,
                 setup_only: bool = False, rebuild: bool = False):
        self.keep = keep or env_mode       # env 模式默认保留数据
        self.env_mode = env_mode
        self.setup_only = setup_only        # 仅建库，不跑 TC
        self.rebuild = rebuild              # 强制清库重建
        self.created = {
            "companies": [], "users": [], "projects": [], "nodes": [],
            "files": [], "chunk_ids": [],
        }
        self.file_ids: dict[int, str] = {}
        self.tmp_dir = _HERE / "_ragtest_tmp"
        self.results: dict = {}
        self.logs: list[str] = []
        self.ingest_meta: dict = {}

    def log(self, msg: str):
        self.logs.append(msg)
        print(msg, flush=True)

    # ── DB helpers ──
    def _session(self):
        from emily_core.infrastructure.database.session import get_session
        return get_session()

    def run(self) -> dict:
        _load_env()
        from emily_core.infrastructure.database.session import init_db
        init_db(DB_URL)

        try:
            self._cleanup_stale()
            self._seed()
            self._generate_files()
            self._ingest()
            self.results["tc"] = self._run_tcs()
            self.results["summary"] = self._summarize(self.results["tc"])
        finally:
            if not self.keep:
                self._cleanup()

        return self.results

    # ── 清理历史残留测试数据 ──
    def _cleanup_stale(self):
        from emily_core.infrastructure.database.models import (
            User, CompanyInfo, Project, ProjectNode,
        )
        with self._session() as s:
            for u in s.query(User).filter(User.username.like(f"{MARK.lower()}-%")).all():
                s.delete(u)
            for c in s.query(CompanyInfo).filter(CompanyInfo.unified_code.like(f"{MARK}-%")).all():
                s.delete(c)
            for p in s.query(Project).filter(Project.code.like(f"{MARK}-%")).all():
                s.delete(p)
            for n in s.query(ProjectNode).filter(ProjectNode.node_id.like(f"{MARK}-%")).all():
                s.delete(n)

    # ── 种子数据 ──
    def _seed(self):
        from emily_core.infrastructure.database.models import (
            User, CompanyInfo, Project, ProjectNode,
            NodeAccessibleFile, NodeParticipantCompany, SessionAccessibleFile,
            _utc_now,
        )

        with self._session() as s:
            # 项目
            pa = Project(code=f"{MARK}-PA", name="RAG测试项目A", status="active")
            pb = Project(code=f"{MARK}-PB", name="RAG测试项目B", status="active")
            s.add_all([pa, pb])
            s.flush()
            self.created["projects"] = [pa.id, pb.id]

            # 企业
            ca = CompanyInfo(
                company_name="RAG测试-公司A(管理单位)", unified_code=f"{MARK}-COMPANY-A",
                project_leader_id=f"{MARK.lower()}-u1", creator_id=f"{MARK.lower()}-u1",
                type="建设单位", is_admin=True,
            )
            cb = CompanyInfo(
                company_name="RAG测试-公司B", unified_code=f"{MARK}-COMPANY-B",
                project_leader_id=f"{MARK.lower()}-u2", creator_id=f"{MARK.lower()}-u2",
                type="施工单位", is_admin=False,
            )
            s.add_all([ca, cb])
            s.flush()
            self.created["companies"] = [ca.id, cb.id]

            # 用户
            u1 = User(username=f"{MARK.lower()}-u1", creator_id="ragtest-system",
                      level=5, company=ca.id, project_id=pa.id)
            u2 = User(username=f"{MARK.lower()}-u2", creator_id="ragtest-system",
                      level=1, company=cb.id, project_id=pb.id)
            u3 = User(username=f"{MARK.lower()}-u3", creator_id="ragtest-system",
                      level=1, company=None, project_id=None)
            s.add_all([u1, u2, u3])
            s.flush()
            self.created["users"] = [u1.id, u2.id, u3.id]
            self.uid = {"U1": u1.id, "U2": u2.id, "U3": u3.id}
            self.cid = {"A": ca.id, "B": cb.id}
            self.pid = {"PA": pa.id, "PB": pb.id}

            # 节点
            def mk_node(node_id, name, project_id, visibility_mode):
                return ProjectNode(
                    project_id=project_id, node_id=node_id, node_name=name,
                    deadline="2026-12-31T00:00:00", creator_id=u1.id,
                    visibility_mode=visibility_mode, status="IN_PROGRESS",
                )

            n1 = mk_node(f"{MARK}-N1", "节点A", pa.id, "specific")
            n2 = mk_node(f"{MARK}-N2", "节点全项目", pa.id, "specific")
            n3 = mk_node(f"{MARK}-N3", "节点B", pb.id, "specific")
            s.add_all([n1, n2, n3])
            s.flush()
            self.created["nodes"] = [n1.id, n2.id, n3.id]
            self.nid = {"N1": n1.node_id, "N2": n2.node_id, "N3": n3.node_id}

            # 节点参与企业
            s.add_all([
                NodeParticipantCompany(node_id=n1.node_id, company_id=ca.id, added_by=u1.id),
                NodeParticipantCompany(node_id=n2.node_id, company_id=ca.id, added_by=u1.id),
                NodeParticipantCompany(node_id=n3.node_id, company_id=cb.id, added_by=u2.id),
            ])

        self._npc_ids_added = True

    # ── 生成测试文件 ──
    def _generate_files(self):
        import shutil
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        for idx, (fname, fmt, _conf, _u, _n, _e, _p, content) in enumerate(_FILE_SPECS, start=1):
            path = self.tmp_dir / fname
            if fmt == "md":
                path.write_text(content, encoding="utf-8")
            elif fmt == "txt":
                path.write_text(content, encoding="utf-8")
            elif fmt == "pdf":
                _make_pdf(path, content)
            elif fmt == "docx":
                _make_docx(path, content)
        self.log(f"生成 {len(_FILE_SPECS)} 个测试文件 → {self.tmp_dir}")

    # ── 入库 ──
    def _ingest(self):
        from emily_core.infrastructure.database.models import File, _utc_now
        from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
        from emily_core.services.document_parser import DocumentParser
        from emily_core.services.structural_chunker import StructuralChunker
        from emily_core.services.dedup_checker import DedupChecker
        from emily_core.infrastructure.embedding.tei_client import TeiClient

        parser = DocumentParser()
        chunker = StructuralChunker()
        repo = KnowledgeChunkRepo()
        tei = TeiClient(TEI_URL)

        # 1) 建 files 记录（doc_id 锚点）
        with self._session() as s:
            for idx, (fname, fmt, conf, u, n, e, p, content) in enumerate(_FILE_SPECS, start=1):
                f = File(
                    file_no=f"{MARK}-F{idx:03d}",
                    filename=fname,
                    uploaded_by=self.uid[u],
                    confidentiality=conf,
                    project_id=self.pid[p],
                    storage_path=str(self.tmp_dir / fname),
                )
                s.add(f)
                s.flush()
                self.file_ids[idx] = f.id
                self.created["files"].append(f.id)

        # 2) 节点可见文件绑定
        from emily_core.infrastructure.database.models import NodeAccessibleFile
        with self._session() as s:
            for idx in (5, 6, 7, 8):
                s.add(NodeAccessibleFile(node_id=self.nid["N1"], file_id=self.file_ids[idx],
                                         added_by=self.uid["U1"]))
            for idx in (11, 12):
                s.add(NodeAccessibleFile(node_id=self.nid["N3"], file_id=self.file_ids[idx],
                                         added_by=self.uid["U2"]))

        # 3) 显式授权 U2 → #13
        from emily_core.infrastructure.database.models import SessionAccessibleFile
        with self._session() as s:
            s.add(SessionAccessibleFile(
                user_id=self.uid["U2"], file_id=self.file_ids[13],
                access_type="explicit", granted_by=self.uid["U1"], granted_at=_utc_now(),
            ))

        # 4) 解析 + 分块 + 去重 + 向量化入库
        seen_hashes: set[str] = set()
        dup_skipped = 0
        per_file_chunks: dict[int, int] = {}

        for idx, (fname, fmt, conf, u, n, e, p, content) in enumerate(_FILE_SPECS, start=1):
            path = self.tmp_dir / fname
            text = parser.parse(path)
            chunks = []
            for c in chunker.chunk(text):
                h = DedupChecker.content_hash(c["text"])
                if h in seen_hashes:
                    dup_skipped += 1
                    continue
                seen_hashes.add(h)
                chunks.append({"text": c["text"], "index": c["index"], "content_hash": h})
            per_file_chunks[idx] = len(chunks)

            if not chunks:
                self.log(f"入库 #{idx} {fname}: 0 chunks (全部重复跳过)")
                continue

            embeddings = asyncio.run(tei.embed([c["text"] for c in chunks]))
            doc_meta = {
                "doc_id": self.file_ids[idx],
                "doc_name": fname,
                "file_no": f"{MARK}-F{idx:03d}",
                "collection": "ragtest",
            }
            ids = repo.batch_insert(chunks, embeddings, doc_meta)
            self.created["chunk_ids"].extend(ids)
            self.log(f"入库 #{idx} {fname}: {len(ids)} chunks (conf={conf}, doc_id={self.file_ids[idx][:8]}…)")

        self.ingest_meta = {"dup_skipped": dup_skipped, "per_file_chunks": per_file_chunks}

    # ═══════════════════════════════════════════════════════════════════════
    # env 模式：复用 env-test 模拟环境（EMERALD-01），库保留
    # ═══════════════════════════════════════════════════════════════════════

    def run_env(self) -> dict:
        """env 模式主流程：复用真实用户/项目，18 文件作为模拟项目一部分建库并保留。"""
        _load_env()
        from emily_core.infrastructure.database.session import init_db
        init_db(DB_URL)

        try:
            if self.rebuild:
                self._env_teardown()
            self._env_prepare()

            if self._env_library_exists() and not self.rebuild:
                self.log(f"[env] RAG 库已存在（{ENV_MARK}-F*），跳过建库")
                self._env_load_file_ids()
                self._env_load_ingest_meta()
            else:
                self._env_generate_files()
                self._env_ingest()
                self.log(f"[env] RAG 库构建完成（18 个文件已入库并保留）")

            if not self.setup_only:
                self.results["tc"] = self._run_tcs()
                self.results["summary"] = self._summarize(self.results["tc"])
                self._env_purge_tc16_probe()
        finally:
            # env 模式默认保留数据（重建由 setup_test_env.ps1 或 --rebuild 触发）
            pass

        return self.results

    # ── 清理 TC-16 注入的状态机探针 chunk（避免污染保留的知识库）──
    def _env_purge_tc16_probe(self):
        from emily_core.infrastructure.database.models import KnowledgeChunk
        with self._session() as s:
            s.query(KnowledgeChunk).filter(
                KnowledgeChunk.doc_id == self.file_ids.get(1, ""),
                KnowledgeChunk.doc_name == "状态机测试",
            ).delete(synchronize_session=False)

    # ── 角色/项目/节点 解析与兜底创建（幂等）──
    def _env_prepare(self):
        from emily_core.infrastructure.database.models import (
            User, Project, ProjectNode, NodeParticipantCompany,
        )
        with self._session() as s:
            def _user(username: str) -> User:
                u = s.query(User).filter(
                    User.username == username, User.is_deleted == False,
                ).first()
                if u is None:
                    raise RuntimeError(
                        f"[env] 用户不存在: {username}（请先运行 env-test 种子）"
                    )
                return u

            u1 = _user(ENV_ROLE_U1)
            u2 = _user(ENV_ROLE_U2)

            # U3 访客：env-test 人员池应已提供（014_seed_rag_visitor.sql），缺失则兜底创建
            u3 = s.query(User).filter(
                User.username == ENV_ROLE_U3, User.is_deleted == False,
            ).first()
            if u3 is None:
                self.log(f"[env] 访客用户 {ENV_ROLE_U3} 不存在，兜底创建（无公司 L1）")
                u3 = User(
                    username=ENV_ROLE_U3, creator_id=u1.id,
                    level=1, company=None, project_id=None, status="active",
                    is_deleted=False,
                )
                s.add(u3)
                s.flush()

            self.uid = {"U1": u1.id, "U2": u2.id, "U3": u3.id}
            # 公司A/B = 真实角色所在公司
            self.cid = {"A": u1.company or "", "B": u2.company or ""}

            # 主项目（必须已存在：EMERALD-01）
            main = s.query(Project).filter(
                Project.code == ENV_PROJECT_MAIN, Project.is_deleted == False,
            ).first()
            if main is None:
                raise RuntimeError(
                    f"[env] 主项目不存在: {ENV_PROJECT_MAIN}（请先运行 env-test 007 种子）"
                )
            # 供应商隔离项目（不存在则创建）
            iso = s.query(Project).filter(
                Project.code == ENV_PROJECT_ISO, Project.is_deleted == False,
            ).first()
            if iso is None:
                iso = Project(
                    code=ENV_PROJECT_ISO,
                    name="翠湖庭院—供应商侧知识隔离（RAG 验收）",
                    description="RAG 验收用 B 侧隔离容器，承载 N3 及其机密文件，避免被主项目 all_project_files 覆盖。",
                    status="active", creator_id=u1.id, is_deleted=False,
                )
                s.add(iso)
                s.flush()
            self.pid = {"PA": main.id, "PB": iso.id}

            # 节点创建（幂等）
            node_ids = {
                "N1": ENV_NODES["N1"],
                "N2": ENV_NODES["N2"],
                "N3": ENV_NODES["N3"],
            }
            specs = [
                # (键, 项目键, 名称, 可见模式)
                ("N1", "PA", "节点A（specific，参与公司A）", "specific"),
                ("N2", "PA", "节点全项目（specific，参与公司A）", "specific"),
                ("N3", "PB", "节点B（specific，参与公司B）", "specific"),
            ]
            for key, proj_key, name, mode in specs:
                nid = node_ids[key]
                node = s.query(ProjectNode).filter(ProjectNode.node_id == nid).first()
                if node is None:
                    node = ProjectNode(
                        project_id=self.pid[proj_key], node_id=nid, node_name=name,
                        owner_dept_id="", related_company_id="建设单位",
                        deadline="2026-12-31T00:00:00", creator_id=u1.id,
                        visibility_mode=mode, status="IN_PROGRESS",
                        node_type="WORK_PACKAGE", responsible_user_id=u1.id,
                    )
                    s.add(node)
                    s.flush()
            self.nid = node_ids

            # 节点参与企业（幂等）：N1/N2→公司A，N3→公司B
            binds = [
                ("N1", "A"), ("N2", "A"), ("N3", "B"),
            ]
            for nk, ck in binds:
                exists = s.query(NodeParticipantCompany).filter(
                    NodeParticipantCompany.node_id == node_ids[nk],
                    NodeParticipantCompany.company_id == self.cid[ck],
                ).first()
                if exists is None:
                    s.add(NodeParticipantCompany(
                        node_id=node_ids[nk], company_id=self.cid[ck],
                        added_by=u1.id,
                    ))
            s.flush()

    # ── 判定库是否已建 ──
    def _env_library_exists(self) -> bool:
        from emily_core.infrastructure.database.models import File
        with self._session() as s:
            row = s.query(File.id).filter(
                File.file_no.like(f"{ENV_MARK}-F%"),
            ).first()
        return row is not None

    # ── 删除旧库（仅限本库自建记录，不碰真实业务数据）──
    def _env_teardown(self):
        from emily_core.infrastructure.database.models import (
            File, KnowledgeChunk, NodeAccessibleFile, SessionAccessibleFile,
            NodeParticipantCompany, ProjectNode,
        )
        with self._session() as s:
            frows = s.query(File).filter(File.file_no.like(f"{ENV_MARK}-F%")).all()
            fids = [f.id for f in frows]
            node_ids = list(ENV_NODES.values())
            if fids:
                s.query(KnowledgeChunk).filter(
                    KnowledgeChunk.doc_id.in_(fids),
                ).delete(synchronize_session=False)
                s.query(NodeAccessibleFile).filter(
                    NodeAccessibleFile.file_id.in_(fids),
                ).delete(synchronize_session=False)
                s.query(SessionAccessibleFile).filter(
                    SessionAccessibleFile.file_id.in_(fids),
                ).delete(synchronize_session=False)
                s.query(File).filter(File.id.in_(fids)).delete(
                    synchronize_session=False,
                )
            s.query(NodeAccessibleFile).filter(
                NodeAccessibleFile.node_id.in_(node_ids),
            ).delete(synchronize_session=False)
            s.query(NodeParticipantCompany).filter(
                NodeParticipantCompany.node_id.in_(node_ids),
            ).delete(synchronize_session=False)
            s.query(ProjectNode).filter(
                ProjectNode.node_id.in_(node_ids),
            ).delete(synchronize_session=False)
        # 清理物理内容文件
        if self.tmp_dir.exists():
            import shutil
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.log(f"[env] 旧 RAG 库已删除（{ENV_MARK}-F* 文件/chunk/节点）")

    # ── 生成内容文件到 emily-data/attachments/mock/<项目>/ragtest/ ──
    def _env_attach_root(self) -> Path:
        return _HERE.parent / "emily-data" / "attachments"

    def _env_proj_code(self, p: str) -> str:
        # _FILE_SPECS 第7字段：PA→主项目，PB→供应商隔离项目
        return ENV_PROJECT_MAIN if p == "PA" else ENV_PROJECT_ISO

    def _env_storage_rel(self, p: str, fname: str) -> str:
        return f"mock/{self._env_proj_code(p)}/ragtest/{fname}"

    def _env_abs_path(self, rel: str) -> Path:
        return self._env_attach_root() / rel

    def _env_generate_files(self):
        import shutil
        # 清理两个项目的旧 ragtest 目录
        for proj in (ENV_PROJECT_MAIN, ENV_PROJECT_ISO):
            d = self._env_attach_root() / "mock" / proj / "ragtest"
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        for _idx, (fname, fmt, _conf, _u, _n, _e, p, content) in enumerate(_FILE_SPECS, start=1):
            path = self._env_abs_path(self._env_storage_rel(p, fname))
            path.parent.mkdir(parents=True, exist_ok=True)
            if fmt in ("md", "txt"):
                path.write_text(content, encoding="utf-8")
            elif fmt == "pdf":
                _make_pdf(path, content)
            elif fmt == "docx":
                _make_docx(path, content)
        self.tmp_dir = self._env_attach_root() / "mock" / ENV_PROJECT_MAIN / "ragtest"
        self.log(f"[env] 生成 {len(_FILE_SPECS)} 个知识文件（PA→{ENV_PROJECT_MAIN}/ragtest, PB→{ENV_PROJECT_ISO}/ragtest）")

    # ── 建 files 记录 + 节点绑定 + 显式授权 + 解析/分块/去重/向量化入库 ──
    def _env_ingest(self):
        from emily_core.infrastructure.database.models import (
            File, NodeAccessibleFile, SessionAccessibleFile, _utc_now,
        )
        from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
        from emily_core.services.document_parser import DocumentParser
        from emily_core.services.structural_chunker import StructuralChunker
        from emily_core.services.dedup_checker import DedupChecker
        from emily_core.infrastructure.embedding.tei_client import TeiClient

        parser = DocumentParser()
        chunker = StructuralChunker()
        repo = KnowledgeChunkRepo()
        tei = TeiClient(TEI_URL)

        # 1) files 记录（doc_id 锚点）
        #    storage_path 用容器相对路径（emily-data/attachments 为根），与 env-test mock 约定一致
        with self._session() as s:
            for idx, (fname, fmt, conf, u, _n, _e, p, _content) in enumerate(_FILE_SPECS, start=1):
                f = File(
                    file_no=f"{ENV_MARK}-F{idx:02d}",
                    filename=fname,
                    uploaded_by=self.uid[u],
                    confidentiality=conf,
                    project_id=self.pid[p],
                    storage_path=self._env_storage_rel(p, fname),
                    file_ext=Path(fname).suffix,
                    rag_indexed=True,
                )
                s.add(f)
                s.flush()
                self.file_ids[idx] = f.id

        # 2) 节点可见绑定（N1→#5-8，N3→#11-12）
        with self._session() as s:
            for idx in (5, 6, 7, 8):
                s.add(NodeAccessibleFile(node_id=self.nid["N1"], file_id=self.file_ids[idx],
                                         added_by=self.uid["U1"]))
            for idx in (11, 12):
                s.add(NodeAccessibleFile(node_id=self.nid["N3"], file_id=self.file_ids[idx],
                                         added_by=self.uid["U2"]))

        # 3) 显式授权 U2 → #13
        with self._session() as s:
            s.add(SessionAccessibleFile(
                user_id=self.uid["U2"], file_id=self.file_ids[13],
                access_type="explicit", granted_by=self.uid["U1"], granted_at=_utc_now(),
            ))

        # 4) 解析 + 分块 + 去重 + 向量化
        seen_hashes: set[str] = set()
        dup_skipped = 0
        per_file_chunks: dict[int, int] = {}

        for idx, (fname, _fmt, conf, _u, _n, _e, p, _content) in enumerate(_FILE_SPECS, start=1):
            path = self._env_abs_path(self._env_storage_rel(p, fname))
            text = parser.parse(path)
            chunks = []
            for c in chunker.chunk(text):
                h = DedupChecker.content_hash(c["text"])
                if h in seen_hashes:
                    dup_skipped += 1
                    continue
                seen_hashes.add(h)
                chunks.append({"text": c["text"], "index": c["index"], "content_hash": h})
            per_file_chunks[idx] = len(chunks)

            if not chunks:
                self.log(f"入库 #{idx} {fname}: 0 chunks (全部重复跳过)")
                continue

            embeddings = asyncio.run(tei.embed([c["text"] for c in chunks]))
            doc_meta = {
                "doc_id": self.file_ids[idx],
                "doc_name": fname,
                "file_no": f"{ENV_MARK}-F{idx:02d}",
                "collection": f"project_{ENV_PROJECT_MAIN}",
            }
            ids = repo.batch_insert(chunks, embeddings, doc_meta)
            self.log(f"入库 #{idx} {fname}: {len(ids)} chunks (conf={conf}, doc_id={self.file_ids[idx][:8]}…)")

        self.ingest_meta = {"dup_skipped": dup_skipped, "per_file_chunks": per_file_chunks}

    # ── 库已存在时：回填内存 id/元数据映射 ──
    def _env_load_file_ids(self):
        from emily_core.infrastructure.database.models import File
        with self._session() as s:
            rows = s.query(File).filter(
                File.file_no.like(f"{ENV_MARK}-F%"),
            ).all()
        self.file_ids = {}
        for f in rows:
            seq = int(f.file_no.rsplit("-F", 1)[1])
            self.file_ids[seq] = f.id
        if len(self.file_ids) != len(_FILE_SPECS):
            raise RuntimeError(
                f"[env] RAG 库文件数不完整（期望 {len(_FILE_SPECS)}，实际 {len(self.file_ids)}），请 --rebuild"
            )

    def _env_load_ingest_meta(self):
        from emily_core.infrastructure.database.models import KnowledgeChunk
        per = {}
        with self._session() as s:
            for idx, fid in self.file_ids.items():
                per[idx] = s.query(KnowledgeChunk).filter(
                    KnowledgeChunk.doc_id == fid,
                ).count()
        self.ingest_meta = {"dup_skipped": 0, "per_file_chunks": per}

    # ── 权限/可见集 ──
    def _perm(self, user_id):
        from emily_core.services.permission_service import PermissionService
        return PermissionService().build_permission_dict(user_id)

    def _visible(self, user_id, company_id, info_level):
        from emily_core.services.visible_file_set_resolver import VisibleFileSetResolver
        return set(VisibleFileSetResolver().resolve_visible_file_list(
            user_id, company_id=company_id, info_level=info_level))

    def _search(self, user_id, query, top_k=5, rerank=False):
        from emily_core.services.permission_service import PermissionService
        from emily_core.services.visible_file_set_resolver import VisibleFileSetResolver
        from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
        from emily_core.infrastructure.embedding.tei_client import TeiClient
        from emily_core.providers.rag.pgvector_provider import PgVectorRagProvider

        perm = PermissionService().build_permission_dict(user_id)
        company_id = perm.get("company_id", "")
        info_level = perm.get("info_level", "public")
        resolver = VisibleFileSetResolver()
        scoped = resolver.resolve_visible_file_ids(user_id, company_id=company_id, info_level=info_level)
        provider = PgVectorRagProvider(tei=TeiClient(TEI_URL), repo=KnowledgeChunkRepo(),
                                       similarity=0.3)

        async def _do():
            return await provider.search(query, top_k=top_k, scoped_doc_ids=scoped, rerank=rerank)

        resp = asyncio.run(_do())
        return {
            "company_id": company_id,
            "info_level": info_level,
            "hits": [{"doc_id": r.source_file_id, "doc_name": r.source_document,
                      "score": r.score} for r in resp.results],
            "total": resp.total,
        }

    # ── TC 执行 ──
    def _run_tcs(self) -> dict:
        tcs = {}
        tcs["TC-01"] = self._tc01()
        tcs["TC-02"] = self._tc02()
        tcs["TC-03"] = self._tc03()
        tcs["TC-04"] = self._tc04()
        tcs["TC-05"] = self._tc05()
        tcs["TC-06"] = self._tc06()
        tcs["TC-07"] = self._tc07()
        tcs["TC-08"] = self._tc08()
        tcs["TC-09"] = self._tc09()
        tcs["TC-10"] = self._tc10()
        tcs["TC-11"] = self._tc11()
        tcs["TC-12"] = self._tc12()
        tcs["TC-13"] = self._tc13()
        tcs["TC-14"] = self._tc14()
        tcs["TC-15"] = self._tc15()
        tcs["TC-16"] = self._tc16()
        tcs["TC-17"] = self._tc17()
        return tcs

    # TC-01: doc_id 归一
    def _tc01(self):
        from sqlalchemy import text
        with self._session() as s:
            rows = s.execute(text(
                "SELECT k.doc_id FROM knowledge_chunks k "
                "LEFT JOIN files f ON k.doc_id = f.id WHERE f.id IS NULL"
            )).fetchall()
        orphans = [r[0] for r in rows]
        passed = len(orphans) == 0
        return {"passed": passed, "orphan_count": len(orphans),
                "detail": f"断链孤儿 {len(orphans)} 条"}

    # TC-02: U1 自传高密级 #3 可见
    def _tc02(self):
        p = self._perm(self.uid["U1"])
        vis = self._visible(self.uid["U1"], p["company_id"], p["info_level"])
        has3 = self.file_ids[3] in vis
        sr = self._search(self.uid["U1"], "RAGTEST-03-CON-U1OWN")
        hit3 = any(h["doc_id"] == self.file_ids[3] for h in sr["hits"])
        return {"passed": has3 and hit3, "file3_in_visible": has3, "search_hit_file3": hit3,
                "detail": "U1 可见 #3(密级2自传)"}

    # TC-03（固定项）: 访客文件可见范围锚点 —— 固定拿「访客(U3/周访客)」ID 做测试
    #   断言（双向）：
    #     a) 能看到「所有」公开文件（confidentiality=0）
    #     b) 看不到「任何」内部(1)/机密(2)文件
    def _tc03(self):
        p = self._perm(self.uid["U3"])
        vis = self._visible(self.uid["U3"], p["company_id"], p["info_level"])
        from emily_core.infrastructure.database.models import File
        with self._session() as s:
            rows = s.query(File).filter(File.is_deleted == False).all()
        public_ids = {f.id for f in rows if f.confidentiality == 0}
        private_ids = {f.id for f in rows if f.confidentiality in (1, 2)}
        all_public_visible = public_ids <= vis          # 所有公开文件均可见
        no_private_visible = not (vis & private_ids)     # 无任何内部/机密文件泄露
        private_leaked = len(vis & private_ids)
        passed = all_public_visible and no_private_visible and len(public_ids) > 0
        return {"passed": passed,
                "visible_count": len(vis),
                "public_count": len(public_ids),
                "all_public_visible": all_public_visible,
                "private_leaked": private_leaked,
                "detail": f"访客可见 {len(vis)} 文件；公开全见={all_public_visible}；内部/机密泄露 {private_leaked} 条"}

    # TC-04: U1 见 #7，U2 不见 #7
    def _tc04(self):
        p1 = self._perm(self.uid["U1"]); v1 = self._visible(self.uid["U1"], p1["company_id"], p1["info_level"])
        p2 = self._perm(self.uid["U2"]); v2 = self._visible(self.uid["U2"], p2["company_id"], p2["info_level"])
        u1_see = self.file_ids[7] in v1
        u2_see = self.file_ids[7] in v2
        passed = u1_see and not u2_see
        return {"passed": passed, "U1_sees_7": u1_see, "U2_sees_7": u2_see,
                "detail": "U1 见 #7 / U2 不见 #7"}

    # TC-05: 密级上界 —— 机密(2)为白名单制，节点参与不自动放行；U1 为上传者，①自传可见
    def _tc05(self):
        p1 = self._perm(self.uid["U1"]); v1 = self._visible(self.uid["U1"], p1["company_id"], p1["info_level"])
        u1_see_8 = self.file_ids[8] in v1
        # 按设计模型（①自传不受密级约束），U1 应可见 #8（机密2）
        return {"passed": u1_see_8, "U1_sees_8": u1_see_8,
                "note": "#8 为机密(2)白名单制；U1 以自传身份可见，节点参与不自动放行",
                "detail": f"U1 可见 #8(密级2自传)={u1_see_8}"}

    # TC-06: 企业隔离 —— 计划用 #11(密级0公开)，按模型②公开应可见；改用 #12 验证
    def _tc06(self):
        p1 = self._perm(self.uid["U1"]); v1 = self._visible(self.uid["U1"], p1["company_id"], p1["info_level"])
        u1_see_11 = self.file_ids[11] in v1
        u1_see_12 = self.file_ids[12] in v1
        # 真正企业隔离：#12(密级2，公司B节点)，U1 不应见
        passed = (not u1_see_12) and u1_see_11
        return {"passed": passed, "U1_sees_11": u1_see_11, "U1_sees_12": u1_see_12,
                "note": "#11 密级0 属②公开（U1 可见）；企业隔离以 #12(密级2) 为准",
                "detail": f"U1 见 #11(公开)={u1_see_11}，U1 不见 #12(公司B机密)={not u1_see_12}"}

    # TC-07: all_project_files 已下线 —— #9(密级1，PA项目，U2上传，未挂节点) 不再因全项目模式放行给 U1
    def _tc07(self):
        p1 = self._perm(self.uid["U1"]); v1 = self._visible(self.uid["U1"], p1["company_id"], p1["info_level"])
        see9 = self.file_ids[9] in v1
        # all_project_files 已移除：U1 参与 PA 但 #9 非自传、未挂节点，不应可见
        return {"passed": not see9, "U1_sees_9": see9,
                "detail": "all_project_files 已下线：U1 不见 #9(非自传/未挂节点)"}

    # TC-08: U2 显式授权见 #13
    def _tc08(self):
        p2 = self._perm(self.uid["U2"]); v2 = self._visible(self.uid["U2"], p2["company_id"], p2["info_level"])
        see13 = self.file_ids[13] in v2
        return {"passed": see13, "U2_sees_13": see13, "detail": "U2 显式授权可见 #13(密级2)"}

    # TC-09: 越权封堵 —— 检索命中落在各自可见集内
    def _tc09(self):
        q = "成本测算"
        s1 = self._search(self.uid["U1"], q)
        s2 = self._search(self.uid["U2"], q)
        p1 = self._perm(self.uid["U1"]); v1 = self._visible(self.uid["U1"], p1["company_id"], p1["info_level"])
        p2 = self._perm(self.uid["U2"]); v2 = self._visible(self.uid["U2"], p2["company_id"], p2["info_level"])
        ok1 = all(h["doc_id"] in v1 for h in s1["hits"])
        ok2 = all(h["doc_id"] in v2 for h in s2["hits"])
        passed = ok1 and ok2
        return {"passed": passed, "U1_all_in_visible": ok1, "U2_all_in_visible": ok2,
                "detail": f"U1 命中 {s1['total']} 条 / U2 命中 {s2['total']} 条，均不越界"}

    # TC-10: U3 可见 = ①∪②（③空）
    def _tc10(self):
        p3 = self._perm(self.uid["U3"]); v3 = self._visible(self.uid["U3"], p3["company_id"], p3["info_level"])
        from emily_core.infrastructure.database.models import File
        with self._session() as s:
            rows = s.query(File).filter(File.id.in_(list(v3))).all()
        # U3 应只见 密级0（公开），无自传（未上传）、无节点
        ok = all(r.confidentiality == 0 for r in rows)
        return {"passed": ok, "visible_count": len(v3), "detail": f"U3 可见 {len(v3)} 文件（①空，③空，仅②公开）"}

    # TC-11: pdf/docx 解析入库
    def _tc11(self):
        c14 = self.ingest_meta["per_file_chunks"].get(14, 0)
        c15 = self.ingest_meta["per_file_chunks"].get(15, 0)
        passed = c14 > 0 and c15 > 0
        return {"passed": passed, "pdf_chunks": c14, "docx_chunks": c15,
                "detail": f"#14 pdf={c14} chunks, #15 docx={c15} chunks"}

    # TC-12: #18 结构分块 >1
    def _tc12(self):
        c18 = self.ingest_meta["per_file_chunks"].get(18, 0)
        passed = c18 > 1
        return {"passed": passed, "chunks": c18, "detail": f"#18 长文档分块 {c18} 段"}

    # TC-13: #16/#17 去重只入1份
    def _tc13(self):
        from emily_core.infrastructure.database.models import KnowledgeChunk
        with self._session() as s:
            # 统计 #16 与 #17 各自的 chunk 数
            c16 = s.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == self.file_ids[16]).count()
            c17 = s.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == self.file_ids[17]).count()
        passed = (c16 > 0 and c17 == 0)
        return {"passed": passed, "file16_chunks": c16, "file17_chunks": c17,
                "detail": f"#16={c16} chunks, #17={c17} chunks（重复被跳过）"}

    # TC-14: 混合检索命中不越界
    def _tc14(self):
        sr = self._search(self.uid["U1"], "施工工艺 防水")
        p1 = self._perm(self.uid["U1"]); v1 = self._visible(self.uid["U1"], p1["company_id"], p1["info_level"])
        ok = all(h["doc_id"] in v1 for h in sr["hits"])
        return {"passed": ok, "hits": len(sr["hits"]),
                "detail": f"混合检索命中 {len(sr['hits'])} 条，均∈U1可见集"}

    # TC-15: rerank 降级不报错
    def _tc15(self):
        try:
            sr = self._search(self.uid["U1"], "施工现场", rerank=True)
            passed = True
        except Exception as e:
            sr = {"hits": []}
            passed = False
        return {"passed": passed, "hits": len(sr["hits"]),
                "detail": f"rerank=True 仍返回 {len(sr['hits'])} 条（降级不阻断）"}

    # TC-16: 状态机 failed→pending→indexed
    def _tc16(self):
        from emily_core.repositories.knowledge_chunk_repo import KnowledgeChunkRepo
        from emily_core.infrastructure.database.models import KnowledgeChunk
        repo = KnowledgeChunkRepo()
        # 造一条 failed chunk
        cid = str(uuid.uuid4())
        with self._session() as s:
            s.add(KnowledgeChunk(id=cid, doc_id=self.file_ids[1], doc_name="状态机测试",
                                 chunk_index=0, chunk_text="状态机测试内容",
                                 content_hash=hashlib.sha256(b"statemachine").hexdigest(),
                                 ingest_status="failed"))
        self.created["chunk_ids"].append(cid)
        n1 = repo.recover_failed()
        with self._session() as s:
            st1 = s.query(KnowledgeChunk.ingest_status).filter(KnowledgeChunk.id == cid).scalar()
        repo.set_embedding(cid, [0.0] * 1024)
        with self._session() as s:
            st2 = s.query(KnowledgeChunk.ingest_status).filter(KnowledgeChunk.id == cid).scalar()
        passed = (st1 == "pending" and st2 == "indexed")
        return {"passed": passed, "after_recover": st1, "after_embed": st2,
                "detail": f"failed→{st1}→{st2}"}

    # TC-17: 引用溯源 cite_id/title
    def _tc17(self):
        sr = self._search(self.uid["U1"], "RAGTEST-01-PUB-NONE")
        hits = sr["hits"]
        if not hits:
            return {"passed": False, "detail": "无命中"}
        ok = all(bool(h["doc_id"]) and bool(h["doc_name"]) for h in hits)
        sample = {"cite_id": hits[0]["doc_id"], "title": hits[0]["doc_name"]}
        return {"passed": ok, "sample": sample,
                "detail": f"命中 {len(hits)} 条，均携带 doc_id/doc_name"}

    # ── 汇总 ──
    def _summarize(self, tcs):
        passed = sum(1 for t in tcs.values() if t.get("passed"))
        failed = [k for k, v in tcs.items() if not v.get("passed")]
        return {"total": len(tcs), "passed": passed, "failed": failed,
                "all_passed": len(failed) == 0}

    # ── 清理 ──
    def _cleanup(self):
        import shutil
        from sqlalchemy import text
        from emily_core.infrastructure.database.models import (
            User, CompanyInfo, Project, ProjectNode, File,
            NodeAccessibleFile, NodeParticipantCompany, SessionAccessibleFile, KnowledgeChunk,
        )
        with self._session() as s:
            if self.created["chunk_ids"]:
                s.query(KnowledgeChunk).filter(KnowledgeChunk.id.in_(self.created["chunk_ids"])).delete(
                    synchronize_session=False)
            if self.file_ids:
                s.query(SessionAccessibleFile).filter(
                    SessionAccessibleFile.file_id.in_(list(self.file_ids.values()))).delete(
                    synchronize_session=False)
                s.query(NodeAccessibleFile).filter(
                    NodeAccessibleFile.file_id.in_(list(self.file_ids.values()))).delete(
                    synchronize_session=False)
                s.query(KnowledgeChunk).filter(
                    KnowledgeChunk.doc_id.in_(list(self.file_ids.values()))).delete(
                    synchronize_session=False)
                s.query(File).filter(File.id.in_(list(self.file_ids.values()))).delete(
                    synchronize_session=False)
            if self.created["nodes"]:
                s.query(NodeAccessibleFile).filter(
                    NodeAccessibleFile.node_id.in_(self.nid.values())).delete(synchronize_session=False)
                s.query(NodeParticipantCompany).filter(
                    NodeParticipantCompany.node_id.in_(self.nid.values())).delete(synchronize_session=False)
                s.query(ProjectNode).filter(ProjectNode.id.in_(self.created["nodes"])).delete(
                    synchronize_session=False)
            if self.created["users"]:
                s.query(User).filter(User.id.in_(self.created["users"])).delete(synchronize_session=False)
            if self.created["companies"]:
                s.query(CompanyInfo).filter(CompanyInfo.id.in_(self.created["companies"])).delete(
                    synchronize_session=False)
            if self.created["projects"]:
                s.query(Project).filter(Project.id.in_(self.created["projects"])).delete(
                    synchronize_session=False)
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.log("测试数据已清理（本次写入记录与临时文件）")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="RAG 验收测试执行器")
    ap.add_argument("--keep", action="store_true", help="执行后保留测试数据（独立模式）")
    ap.add_argument("--env", action="store_true", help="env 模式：复用 env-test 模拟环境建库/测试")
    ap.add_argument("--setup", action="store_true", help="仅建库，不跑 TC（供 env-test setup_test_env.ps1）")
    ap.add_argument("--rebuild", action="store_true", help="强制删除既有 RAG 库后重建")
    args = ap.parse_args()

    h = Harness(keep=args.keep, env_mode=args.env,
                setup_only=args.setup, rebuild=args.rebuild)
    if args.env:
        result = h.run_env()
    else:
        result = h.run()
    print("\n===== RESULT =====\n" + json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
