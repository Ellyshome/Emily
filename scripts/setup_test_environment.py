#!/usr/bin/env python3
"""测试环境初始化脚本 —— 布置陈哲的精装设计测试环境。

创建内容：
  1. 精装设计公司（company_info）
  2. 用户：陈哲，精装设计单位主管（users）
  3. 15轮往期对话（conversations + messages）
  4. 用户长期记忆（users.long_term_memory）
  5. 测试项目（projects）
  6. 全景节点：精装设计（project_nodes）
  7. 计划任务：精装施工图出图（plan_task_templates + plan_task_instances）
  8. 测试文件（files + node_accessible_files）

使用方式：
  cd emily-core
  python -m scripts.setup_test_environment
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "emily-core"))

from datetime import datetime, timezone, timedelta
from emily_core.infrastructure.database import init_db, get_session
from emily_core.infrastructure.database.models import (
    User, CompanyInfo, Conversation, Message,
    Project, ProjectNode, PlanTaskTemplate, PlanTaskInstance,
    File, NodeAccessibleFile,
)


BEIJING_TZ = timezone(timedelta(hours=8))


def _beijing_now() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def setup_test_environment():
    """创建完整测试环境。"""

    # 先初始化数据库连接
    init_db()

    with get_session() as session:
        # ── 1. 创建公司 ──
        print("📦 创建测试公司...")
        company = CompanyInfo(
            company_name="精装设计研究院",
            unified_code="91110108MA00123456",  # 18位统一社会信用代码
            business_desc="专注于精装设计、景观设计、室内外装饰设计",
            project_leader_id="chenzhe-uuid-001",  # 后面补充
            creator_id="system",
            type="设计单位",
            status="active",
            scope=json.dumps(["景观设计", "精装设计", "施工图设计"]),
            partners=json.dumps([]),
            department=json.dumps(["设计部", "施工图部", "景观部"]),
            function_scope=json.dumps({
                "精装设计": ["设计图纸交付", "材料样板确认", "现场技术支持"],
                "景观设计": ["方案设计", "施工图设计", "现场配合"],
            }),
        )
        session.add(company)
        session.flush()
        print(f"   ✓ 公司已创建: {company.id}")

        # ── 2. 创建用户：陈哲 ──
        print("👤 创建用户：陈哲")
        company_id = company.id
        user = User(
            id="chenzhe-jyzx-2026-0001",  # 使用 notebook 里的 ID
            username="陈哲",
            phone="13800138000",
            email="chenzhe@jingzhuang-design.com",
            status="active",
            is_admin=False,
            gender=1,  # 男
            id_card="110101198808081234",
            qq="123456789",
            wechat="chenzhe_2026",
            remark="精装设计研究院设计主管，负责精装设计，景观施工图，有8年精装设计经验",
            creator_id="system",
            org_category=2,  # 工程组
            permission_level=3,  # 参建管理（主管级别）
            company=company_id,
            position=json.dumps(["精装设计主管", "景观施工图负责人"]),
        )
        session.add(user)
        session.flush()
        print(f"   ✓ 陈哲（公司ID: {company_id}）")

        # 更新公司的 project_leader_id
        company.project_leader_id = user.id

        # ── 3. 设置用户长期记忆 ──
        print("🧠 设置用户长期记忆与风格偏好")
        user.long_term_memory = """
# 陈哲 个人长期记忆（2026-07-06 更新）

## 基本信息
- 姓名：陈哲
- 职位：精装设计单位主管 / 景观施工图负责人
- 经验：8年精装设计经验，5年景观施工图经验

## 个人工作风格偏好
1. **图纸要求**：非常注重图纸细节与规范，要求施工图必须符合国家建筑标准设计图集
2. **材料偏好**：优先考虑环保材料，推崇木纹石、金属收边条、艺术涂料
3. **色彩偏好**：现代简约风格，偏爱中性色调，避免过于鲜艳的颜色
4. **沟通习惯**：喜欢结构化汇报，要点清晰，避免长篇大论；喜欢用思维导图梳理设计逻辑
5. **交付要求**：文件命名规范（项目-日期-版本-内容），PDF+CAD双版本交付

## 往期项目经验
- 阳光城·滨江悦府 精装设计 2024
- 龙湖·天璞 景观施工图 2025
- 万科·中央公园 精装+景观一体化设计 2025-2026

## 常见问题偏好回答
- 材料选型：优先考虑可施工性、成本控制、后期维护
- 节点大样：注重防水处理、收口美观、结构安全
- 变更处理：先评估影响再决策，保留图纸变更追溯记录

## 记忆更新时间
2026-07-06 初始化
"""

        # 对话摘要
        user.conversation_summary = """
【历史对话摘要】
- 累计对话：15轮
- 主要议题：精装施工图出图标准、景观材料选型、设计变更流程
- 用户风格：专业严谨、注重细节、喜欢结构化信息呈现
- 偏好工具：搜索图纸规范、查询材料库、生成施工图检查清单
"""

        # ── 4. 创建会话 ──
        print("💬 创建会话与15轮历史对话...")
        conv = Conversation(
            id="conv-chenzhe-test-001",
            im_platform="test",
            conversation_type="private",
            conversation_id="test-conv-123",
            title="精装设计项目沟通群",
            takeover_mode="collaborate",
        )
        session.add(conv)
        session.flush()

        # ── 5. 创建15轮历史对话 ──
        conversation_history = [
            # 第1-2轮：项目启动
            ("我是陈哲，负责这个项目的精装设计", "你好陈哲，很高兴认识你！我是你的AI项目助手。请告诉我项目目前的进展情况。"),
            ("项目目前处于方案设计阶段，下周要出第一版施工图", "好的，我记录一下：精装设计项目，方案设计阶段，下周出第一版施工图。需要我帮你准备施工图出图检查清单吗？"),

            # 第3-4轮：图纸规范
            ("帮我整理一下精装施工图出图标准", "好的，为你整理精装施工图出图标准：\n\n【图纸目录】\n1. 封面、目录、设计说明\n2. 平面布置图、地面铺装图、天花布置图\n3. 立面图（主要空间4个方向）\n4. 节点大样图（收口、防水、特殊造型）\n5. 材料表、灯具表、洁具表\n\n【出图规范】\n- 图层规范：按专业分层（墙体、家具、灯具等）\n- 线型规范：粗线=轮廓，中线=家具，细线=标注\n- 标注规范：尺寸标注、标高标注、材质标注"),
            ("还需要补充景观施工图的规范", "【景观施工图补充规范】\n\n【图纸目录】\n1. 总平面图、分区索引图\n2. 竖向设计图、排水设计图\n3. 种植设计图（乔木、灌木、地被分层）\n4. 园建施工图（道路、广场、水景、廊架）\n5. 通用节点大样图\n\n【注意事项】\n- 坐标系统：与建筑总平统一\n- 标高系统：绝对标高+相对标高双标注\n- 种植设计：标注胸径/冠幅/高度，注明苗木来源"),

            # 第5-6轮：材料选型
            ("帮我查一下木纹石的技术参数", "木纹石技术参数查询结果：\n\n【基本信息】\n- 石材类型：大理石（天然石材）\n- 产地：意大利、土耳其、中国云南\n- 常规厚度：20mm（墙面），30mm（地面）\n\n【物理性能】\n- 抗压强度：120-150 MPa\n- 抗弯强度：10-12 MPa\n- 吸水率：0.1-0.3%\n- 莫氏硬度：3-4\n\n【适用场景】\n- ✅ 室内地面、墙面\n- ❌ 室外地面（硬度不足，易磨损）\n- ⚠️ 浴室需做防水处理\n\n【价格参考】\n- 国产：300-500元/㎡\n- 进口：800-1500元/㎡"),
            ("木纹石做地面需要注意什么", "木纹石做地面注意事项：\n\n【施工前】\n1. 石材六面防护处理（防污、防水）\n2. 排版预铺，避免色差集中\n3. 检查石材厚度均匀性\n\n【施工中】\n1. 采用专用石材粘结剂，避免用普通水泥（易返碱）\n2. 留缝2-3mm，用同色填缝剂\n3. 地面完成后72小时禁止上人\n\n【施工后】\n1. 结晶处理，提高表面硬度和光泽度\n2. 定期（每3-6个月）做养护\n3. 避免尖锐物体直接刮擦"),

            # 第7-8轮：节点设计
            ("墙地收口节点怎么处理比较好", "墙地收口节点设计方案：\n\n【方案一：金属收边条（推荐）】\n```\n墙面完成面\n    ↓\n┌───────┐ 不锈钢收边条（5mm宽）\n│  5mm  │ ← 突出墙面\n└───────┘\n    ↓\n地面完成面\n```\n优点：收口干净，可遮挡施工误差\n\n【方案二：密缝处理】\n- 墙地砖对缝，留1mm美缝\n- 要求施工精度高，适合现代简约风格\n\n【方案三：踢脚线遮挡】\n- 80-100mm高踢脚线\n- 可隐藏缝隙和管线出口"),
            ("防水节点呢，淋浴房的", "淋浴房防水节点设计：\n\n【标准做法】\n1. 墙面防水高度：淋浴区1800mm，其他区域300mm\n2. 地面防水：JS防水涂料，涂刷3遍（1.5mm厚）\n3. 门槛石：先装门槛石，再做防水反边\n\n【节点大样】\n```\n淋浴区地面（找坡1%）\n    ↓\n┌────────────────────┐\n│  防水涂料上翻300mm │ ← 墙上\n│  门槛石（湿贴）     │\n│  防水反边包裹门槛石 │\n└────────────────────┘\n    ↓\n卫生间外地面\n```\n\n【重点检查】\n- 管道根部防水加强层\n- 地漏周围防水处理\n- 淋浴屏固定处密封"),

            # 第9-10轮：设计变更
            ("设计变更流程怎么走", "设计变更流程：\n\n【标准流程】\n1. 变更发起：填写《设计变更申请单》，说明变更原因和内容\n2. 技术评估：评估变更对造价、工期、其他专业的影响\n3. 甲方确认：甲方签字确认变更（涉及费用需商务确认）\n4. 设计出图：出具正式变更图纸，版本号+1\n5. 图纸下发：下发施工单位，做技术交底\n6. 现场实施：跟踪实施情况，验收闭环\n\n【变更分类】\n- 小变更：不涉及造价，仅图纸优化 → 设计主管确认\n- 中变更：造价影响<5万 → 项目经理确认\n- 大变更：造价影响≥5万 → 公司级审批"),
            ("变更单怎么归档", "设计变更归档规范：\n\n【归档内容】\n1. 《设计变更申请单》原件\n2. 变更前后图纸对比（PDF格式）\n3. 技术评估说明\n4. 甲方确认函/签字扫描件\n5. 现场实施照片（前后对比）\n6. 验收记录\n\n【文件命名】\n`项目名称-变更类别-日期-版本号.pdf`\n例：`滨江悦府-精装-20260701-V1.0.pdf`\n\n【版本管理】\n- 每版变更独立编号：BG-YYYYMMDD-001\n- 图纸上标注变更云线+变更编号\n- 维护变更台账，可追溯查询"),

            # 第11-12轮：项目进度
            ("帮我列一下下周的工作计划", "陈哲下周工作计划（2026-07-07至07-11）：\n\n【周一】施工图内审\n- 精装施工图自审（平面+立面）\n- 景观施工图核对（竖向+种植）\n- 准备内审会议材料\n\n【周二】设计交底\n- 上午：精装施工图内审会议\n- 下午：景观施工图内审会议\n- 整理内审意见清单\n\n【周三】图纸修改\n- 根据内审意见修改图纸\n- 重点修改：节点大样、材料标注\n\n【周四】甲方汇报\n- 准备汇报PPT（方案+施工图）\n- 材料样板准备\n\n【周五】出图准备\n- 图纸目录核对\n- 打印装订：精装6套，景观4套\n- 电子文件刻盘/上传"),
            ("需要准备哪些材料样板", "精装项目材料样板清单：\n\n【硬装材料】\n1. 地面砖/石材（3-5款）\n   - 客厅：木纹石（主选）、仿木纹砖（备选）\n   - 卫生间：防滑砖（600x600）\n\n2. 墙面材料（2-3款）\n   - 艺术涂料（3种肌理）\n   - 木饰面（2种木色）\n\n3. 石材（2-3款）\n   - 台面：人造石/天然大理石\n   - 门槛石/窗台石\n\n【五金洁具】\n4. 五金件（1套）\n   - 门锁、合页、拉手\n   - 水龙头、淋浴花洒\n\n5. 灯具样板（2-3款）\n   - 筒灯、射灯、灯带\n\n【展示要求】\n- 统一A4展板，标注材料名称、规格、品牌\n- 标注使用部位（客厅/卧室/卫生间）"),

            # 第13-14轮：质量检查
            ("施工图出图前要检查什么", "施工图出图前检查清单：\n\n【图纸完整性】\n□ 封面、目录、设计说明齐全\n□ 平面图、立面图、节点图完整\n□ 所有变更已更新到图纸\n\n【标注检查】\n□ 尺寸标注齐全、无遗漏\n□ 标高标注正确（±0.000清晰）\n□ 材质标注完整（含型号规格）\n\n【规范合规】\n□ 符合《建筑内部装修设计防火规范》\n□ 符合《民用建筑工程室内环境污染控制标准》\n□ 无障碍设计符合规范\n\n【专业协调】\n□ 与建筑图核对：墙体、门窗位置一致\n□ 与机电图核对：插座、灯具、设备位置\n□ 与结构图核对：梁位、板厚\n\n【出图设置】\n□ 图层关闭正确（仅显示必要图层）\n□ 打印样式设置正确\n□ 图纸比例标注正确"),
            ("还要检查什么容易遗漏的", "容易遗漏的施工图检查项：\n\n【细节类】\n□ 开关插座高度标注（常规300mm，床头700mm）\n□ 检修口预留（吊顶内、管井内）\n□ 窗帘盒尺寸（200mm宽，150mm高）\n□ 门套线与墙面收口\n\n【专业配合类】\n□ 浴霸/排气扇开孔尺寸\n□ 热水器排烟管走向\n□ 空调内机位置与吊顶关系\n□ 智能设备预埋管线\n\n【施工可实施类】\n□ 砖排版：避免出现小于1/3砖宽\n□ 石材排版：纹理方向一致\n□ 木饰面分块：考虑板材规格，减少拼接\n□ 灯具定位：与吊顶造型关系协调"),

            # 第15轮：总结
            ("好的，下周按这个计划来推进", "好的陈哲！我已经完整记录了项目计划和所有讨论要点。\n\n📋 【本周工作要点总结】\n1. 精装+景观施工图内审（周一二）\n2. 根据内审意见修改图纸（周三）\n3. 甲方汇报准备（周四）\n4. 正式出图（周五）\n\n🔔 我会在关键节点提醒你：\n- 周一 09:00 内审会议提醒\n- 周三 17:00 图纸修改进度确认\n- 周四 10:00 汇报材料检查\n\n需要我随时帮你查询规范、整理清单、生成文档，随时告诉我！祝你工作顺利！"),
        ]

        # 创建消息
        for i, (user_msg, agent_msg) in enumerate(conversation_history, 1):
            msg_time = datetime.now(timezone.utc) - timedelta(days=30-i)

            # 用户消息
            msg_user = Message(
                id=f"msg-chenzhe-{i*2-1:03d}",
                event_id=f"evt-chenzhe-{i*2-1:03d}",
                conversation_id=conv.id,
                sender_user_id=user.id,
                sender_im_id="chenzhe-im-id",
                sender_name="陈哲",
                message_type="text",
                direction="user_to_agent",
                content=user_msg,
                attachments=json.dumps([]),
                is_at_bot=True,
                status="processed",
                created_at=msg_time.isoformat(),
                processed_at=(msg_time + timedelta(seconds=10)).isoformat(),
            )
            session.add(msg_user)

            # Agent回复
            msg_agent = Message(
                id=f"msg-chenzhe-{i*2:03d}",
                event_id=f"evt-chenzhe-{i*2:03d}",
                conversation_id=conv.id,
                sender_user_id=None,
                sender_im_id="agent",
                sender_name="AI助手",
                message_type="text",
                direction="agent_to_user",
                content=agent_msg,
                attachments=json.dumps([]),
                is_at_bot=False,
                status="processed",
                created_at=(msg_time + timedelta(seconds=30)).isoformat(),
                processed_at=(msg_time + timedelta(seconds=45)).isoformat(),
            )
            session.add(msg_agent)

        session.flush()
        print(f"   ✓ 创建了15轮对话（30条消息）")

        # ── 6. 创建项目 ──
        print("🏗️ 创建测试项目")
        project = Project(
            id="project-jingzhuang-2026-001",
            code="JZ-2026-001",
            name="滨江悦府精装设计项目",
            description="滨江悦府项目精装设计，含住宅公共区域、样板间、景观设计",
            status="active",
            address="上海市浦东新区滨江大道888号",
            city="上海",
            lifecycle_stage=1,  # 规划设计阶段
            creator_id=user.id,
        )
        session.add(project)
        session.flush()
        print(f"   ✓ 项目已创建: {project.name}")

        # 更新用户的项目关联
        user.project_id = project.id

        # ── 7. 创建全景节点：精装设计 ──
        print("📍 创建全景节点：精装设计")
        node = ProjectNode(
            project_id=project.id,
            node_id="NODE-JZ-001",
            node_name="精装设计",
            creator_id=user.id,
            deadline=(datetime.now(BEIJING_TZ) + timedelta(days=30)).strftime("%Y-%m-%d"),
            owner_dept_id=company.id,
            related_company_id=company.id,
            parent_node_id="",  # 根节点
            stage_id=2,  # 设计阶段
            child_weight="1.0",
            remark="精装设计主节点，含方案设计、施工图设计、材料选型",
            status="IN_PROGRESS",
            sort_order=1,
        )
        session.add(node)
        session.flush()
        print(f"   ✓ 节点已创建: {node.node_id}")

        # ── 8. 创建计划任务模板 ──
        print("📋 创建计划任务：精装施工图出图")
        task_template = PlanTaskTemplate(
            template_no="TPL-20260706-0001",
            name="精装施工图出图",
            description="完成精装设计全套施工图（平面+立面+节点+材料表）",
            initiator_id=user.id,
            executor_id=user.id,
            project_id=project.id,
            task_type="ONCE",
            deadline_rule="2026年7月31日前完成",
            verification_standard=json.dumps({
                "图纸完整性": "平面、立面、节点、材料表齐全",
                "标注规范性": "尺寸、标高、材质标注完整",
                "专业协调性": "与建筑、机电专业核对无误",
            }),
            status="ACTIVE",
            creator_id=user.id,
        )
        session.add(task_template)
        session.flush()

        # 创建任务实例
        task_instance = PlanTaskInstance(
            instance_no="INST-20260706-0001",
            template_id=task_template.id,
            period_key="2026-07-31",
            name="精装施工图出图",
            description="完成滨江悦府精装设计全套施工图",
            initiator_id=user.id,
            executor_id=user.id,
            project_id=project.id,
            deadline_at=(datetime.now(BEIJING_TZ) + timedelta(days=25)).isoformat(),
            status="SUBMITTED",
            related_node_id=node.id,  # 关联到精装设计节点
            deliverables=json.dumps([
                {"name": "精装施工图全套", "type": "CAD", "required": True},
                {"name": "材料表", "type": "Excel", "required": True},
                {"name": "图纸说明", "type": "PDF", "required": True},
            ]),
        )
        session.add(task_instance)
        session.flush()
        print(f"   ✓ 计划任务已创建: {task_template.template_no}")

        # ── 9. 创建测试文件 ──
        print("📄 创建测试文件并关联到节点")

        test_files_data = [
            {
                "filename": "精装施工图-平面布置图.dwg",
                "file_type": "CAD",
                "file_ext": ".dwg",
                "description": "滨江悦府精装设计平面布置图",
            },
            {
                "filename": "精装施工图-立面图.dwg",
                "file_type": "CAD",
                "file_ext": ".dwg",
                "description": "客厅、卧室、卫生间立面图",
            },
            {
                "filename": "精装施工图-节点大样图.dwg",
                "file_type": "CAD",
                "file_ext": ".dwg",
                "description": "墙地收口、防水节点、门套节点等",
            },
            {
                "filename": "材料清单-精装.xlsx",
                "file_type": "Excel",
                "file_ext": ".xlsx",
                "description": "精装材料清单，含品牌、规格、用量",
            },
            {
                "filename": "景观施工图-总平面图.dwg",
                "file_type": "CAD",
                "file_ext": ".dwg",
                "description": "小区景观总平面布置图",
            },
        ]

        created_files = []
        for i, file_data in enumerate(test_files_data, 1):
            file = File(
                file_no=f"FIL-2026070{i}-000{i}",
                project_id=project.id,
                filename=file_data["filename"],
                file_type=file_data["file_type"],
                file_ext=file_data["file_ext"],
                file_url=f"/app/storage/files/{file_data['filename']}",
                file_hash=f"hash_{i:04d}",
                file_size=1024 * 100 * i,  # 模拟文件大小
                uploaded_by=user.id,
                confidentiality=1,  # 内部
                creator_id=user.id,
                responsible_id=user.id,
                version=f"V1.{i}",
                is_latest=True,
                parse_status="pending",
            )
            session.add(file)
            session.flush()
            created_files.append(file)

            # 关联文件到节点
            node_file = NodeAccessibleFile(
                node_id=node.id,
                file_id=file.id,
                access_type="READ_WRITE",
                creator_id=user.id,
            )
            session.add(node_file)

        session.flush()
        print(f"   ✓ 创建了{len(created_files)}个测试文件并关联到节点")

        # ── 提交所有变更 ──
        session.commit()
        print("\n" + "="*60)
        print("✅ 测试环境布置完成！")
        print("="*60)
        print(f"""
创建的数据汇总：
  📦 公司：精装设计研究院（ID: {company_id}）
  👤 用户：陈哲（ID: {user.id}）
     - 权限级别：3（参建管理 / 主管）
     - 职位：精装设计主管、景观施工图负责人
  💬 对话：15轮历史对话（30条消息）
  🧠 记忆：用户长期风格偏好记忆已设置
  🏗️ 项目：滨江悦府精装设计项目
  📍 全景节点：精装设计（NODE-JZ-001）
  📋 计划任务：精装施工图出图（关联到精装设计节点）
  📄 测试文件：5个施工图文件（关联到节点）

测试用户ID：
  chenzhe-jyzx-2026-0001

可以使用以下命令测试：
  cd emily-core
  python -m emily_core.session.fetchers.fetch_available_tools --user-id chenzhe-jyzx-2026-0001
""")


if __name__ == "__main__":
    setup_test_environment()
