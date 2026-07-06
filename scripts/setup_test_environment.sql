-- 测试环境初始化 SQL 脚本
-- 布置陈哲的精装设计测试环境
--
-- 使用方式：
--   docker cp scripts/setup_test_environment.sql emily-postgres:/tmp/setup.sql
--   docker exec emily-postgres psql -U emily -d emily -f /tmp/setup.sql
--

-- 时区设置
SET TIME ZONE 'Asia/Shanghai';

-- ============================================================
-- 1. 创建测试项目：滨江悦府精装设计项目（先创建，因为user依赖）
-- ============================================================
INSERT INTO projects (
    id, code, name, description, status, address, city,
    lifecycle_stage, is_deleted, created_at, updated_at
) VALUES (
    'project-jingzhuang-2026-001',
    'JZ-2026-001',
    '滨江悦府精装设计项目',
    '滨江悦府项目精装设计，含住宅公共区域、样板间、景观设计',
    'active',
    '上海市浦东新区滨江大道888号',
    '上海',
    1,
    false,
    NOW() AT TIME ZONE 'UTC',
    NOW() AT TIME ZONE 'UTC'
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 2. 创建公司：精装设计研究院
-- ============================================================
INSERT INTO company_info (
    id, company_name, unified_code, business_desc,
    project_leader_id, creator_id, type, status,
    scope, partners, department, function_scope,
    created_at, updated_at, is_deleted
) VALUES (
    'company-jingzhuang-2026-001',
    '精装设计研究院',
    '91110108MA00123456',
    '专注于精装设计、景观设计、室内外装饰设计',
    'chenzhe-jyzx-2026-0001',
    'system',
    '设计单位',
    'active',
    '["景观设计", "精装设计", "施工图设计"]',
    '[]',
    '["设计部", "施工图部", "景观部"]',
    '{"精装设计": ["设计图纸交付", "材料样板确认", "现场技术支持"], "景观设计": ["方案设计", "施工图设计", "现场配合"]}',
    NOW() AT TIME ZONE 'UTC',
    NOW() AT TIME ZONE 'UTC',
    false
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 3. 创建用户：陈哲，精装设计单位主管
-- ============================================================
INSERT INTO users (
    id, username, phone, email, status, is_admin, gender,
    id_card, qq, wechat, remark, creator_id, is_deleted,
    perm_list, org_category, permission_level, company,
    project_id, position, long_term_memory, conversation_summary,
    created_at, updated_at
) VALUES (
    'chenzhe-jyzx-2026-0001',
    '陈哲',
    '13800138000',
    'chenzhe@jingzhuang-design.com',
    'active',
    false,
    1,
    '110101198808081234',
    '123456789',
    'chenzhe_2026',
    '精装设计研究院设计主管，负责精装设计，景观施工图，有8年精装设计经验',
    'system',
    false,
    '[]',
    2,
    3,
    'company-jingzhuang-2026-001',
    'project-jingzhuang-2026-001',
    '["精装设计主管", "景观施工图负责人"]',
    '# 陈哲 个人长期记忆（2026-07-06 更新）

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
- 变更处理：先评估影响再决策，保留图纸变更追溯记录',
    '【历史对话摘要】
- 累计对话：15轮
- 主要议题：精装施工图出图标准、景观材料选型、设计变更流程
- 用户风格：专业严谨、注重细节、喜欢结构化信息呈现
- 偏好工具：搜索图纸规范、查询材料库、生成施工图检查清单',
    NOW() AT TIME ZONE 'UTC',
    NOW() AT TIME ZONE 'UTC'
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 4. 创建全景节点：精装设计
-- ============================================================
INSERT INTO project_nodes (
    id, project_id, node_id, node_name, creator_id, deadline,
    owner_dept_id, related_company_id, parent_node_id, stage_id,
    child_weight, remark, status, sort_order, is_discarded,
    created_at, updated_at
) VALUES (
    'node-jingzhuang-2026-001',
    'project-jingzhuang-2026-001',
    'NODE-JZ-001',
    '精装设计',
    'chenzhe-jyzx-2026-0001',
    TO_CHAR(NOW() + INTERVAL '30 days', 'YYYY-MM-DD'),
    'company-jingzhuang-2026-001',
    'company-jingzhuang-2026-001',
    '',
    2,
    '1.0000',
    '精装设计主节点，含方案设计、施工图设计、材料选型',
    'IN_PROGRESS',
    1,
    false,
    NOW() AT TIME ZONE 'UTC',
    NOW() AT TIME ZONE 'UTC'
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 5. 创建会话
-- ============================================================
INSERT INTO conversations (
    id, im_platform, conversation_type, conversation_id,
    title, project_id, takeover_mode, created_at, updated_at
) VALUES (
    'conv-chenzhe-test-001',
    'test',
    'private',
    'test-conv-123',
    '精装设计项目沟通群',
    'project-jingzhuang-2026-001',
    'collaborate',
    NOW() AT TIME ZONE 'UTC',
    NOW() AT TIME ZONE 'UTC'
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 6. 创建15轮历史对话（30条消息）
-- ============================================================
-- 使用 DO 块创建多条消息
DO $$
DECLARE
    conv_id TEXT := 'conv-chenzhe-test-001';
    user_id TEXT := 'chenzhe-jyzx-2026-0001';
    base_time TIMESTAMP := NOW() AT TIME ZONE 'UTC' - INTERVAL '30 days';

    -- 对话数据
    conv_data TEXT[][] := ARRAY[
        -- 第1-2轮：项目启动
        ARRAY['我是陈哲，负责这个项目的精装设计', '你好陈哲，很高兴认识你！我是你的AI项目助手。请告诉我项目目前的进展情况。'],
        ARRAY['项目目前处于方案设计阶段，下周要出第一版施工图', '好的，我记录一下：精装设计项目，方案设计阶段，下周出第一版施工图。需要我帮你准备施工图出图检查清单吗？'],

        -- 第3-4轮：图纸规范
        ARRAY['帮我整理一下精装施工图出图标准', '【图纸目录】
1. 封面、目录、设计说明
2. 平面布置图、地面铺装图、天花布置图
3. 立面图（主要空间4个方向）
4. 节点大样图（收口、防水、特殊造型）
5. 材料表、灯具表、洁具表'],
        ARRAY['还需要补充景观施工图的规范', '【景观施工图补充规范】

【图纸目录】
1. 总平面图、分区索引图
2. 竖向设计图、排水设计图
3. 种植设计图（乔木、灌木、地被分层）
4. 园建施工图（道路、广场、水景、廊架）
5. 通用节点大样图'],

        -- 第5-6轮：材料选型
        ARRAY['帮我查一下木纹石的技术参数', '木纹石技术参数查询结果：

【基本信息】
- 石材类型：大理石（天然石材）
- 产地：意大利、土耳其、中国云南
- 常规厚度：20mm（墙面），30mm（地面）

【物理性能】
- 抗压强度：120-150 MPa
- 抗弯强度：10-12 MPa
- 吸水率：0.1-0.3%
- 莫氏硬度：3-4'],
        ARRAY['木纹石做地面需要注意什么', '木纹石做地面注意事项：

【施工前】
1. 石材六面防护处理（防污、防水）
2. 排版预铺，避免色差集中
3. 检查石材厚度均匀性

【施工中】
1. 采用专用石材粘结剂，避免用普通水泥（易返碱）
2. 留缝2-3mm，用同色填缝剂
3. 地面完成后72小时禁止上人'],

        -- 第7-8轮：节点设计
        ARRAY['墙地收口节点怎么处理比较好', '墙地收口节点设计方案：

【方案一：金属收边条（推荐）】
优点：收口干净，可遮挡施工误差

【方案二：密缝处理】
- 墙地砖对缝，留1mm美缝
- 要求施工精度高，适合现代简约风格

【方案三：踢脚线遮挡】
- 80-100mm高踢脚线
- 可隐藏缝隙和管线出口'],
        ARRAY['防水节点呢，淋浴房的', '淋浴房防水节点设计：

【标准做法】
1. 墙面防水高度：淋浴区1800mm，其他区域300mm
2. 地面防水：JS防水涂料，涂刷3遍（1.5mm厚）
3. 门槛石：先装门槛石，再做防水反边

【重点检查】
- 管道根部防水加强层
- 地漏周围防水处理
- 淋浴屏固定处密封'],

        -- 第9-10轮：设计变更
        ARRAY['设计变更流程怎么走', '设计变更流程：

【标准流程】
1. 变更发起：填写《设计变更申请单》
2. 技术评估：评估变更对造价、工期影响
3. 甲方确认：甲方签字确认变更
4. 设计出图：出具正式变更图纸
5. 图纸下发：下发施工单位，做技术交底
6. 现场实施：跟踪实施情况，验收闭环'],
        ARRAY['变更单怎么归档', '设计变更归档规范：

【归档内容】
1. 《设计变更申请单》原件
2. 变更前后图纸对比（PDF格式）
3. 技术评估说明
4. 甲方确认函/签字扫描件
5. 现场实施照片（前后对比）
6. 验收记录

【文件命名】
`项目名称-变更类别-日期-版本号.pdf`'],

        -- 第11-12轮：项目进度
        ARRAY['帮我列一下下周的工作计划', '陈哲下周工作计划：

【周一】施工图内审
- 精装施工图自审（平面+立面）
- 景观施工图核对（竖向+种植）

【周二】设计交底
- 上午：精装施工图内审会议
- 下午：景观施工图内审会议

【周三】图纸修改
- 根据内审意见修改图纸

【周四】甲方汇报
- 准备汇报PPT

【周五】出图准备
- 图纸目录核对，打印装订'],
        ARRAY['需要准备哪些材料样板', '精装项目材料样板清单：

【硬装材料】
1. 地面砖/石材（3-5款）
   - 客厅：木纹石（主选）
   - 卫生间：防滑砖

2. 墙面材料（2-3款）
   - 艺术涂料（3种肌理）
   - 木饰面（2种木色）

【五金洁具】
3. 五金件、门锁、合页、拉手
4. 灯具样板（筒灯、射灯、灯带）'],

        -- 第13-14轮：质量检查
        ARRAY['施工图出图前要检查什么', '施工图出图前检查清单：

【图纸完整性】
□ 封面、目录、设计说明齐全
□ 平面图、立面图、节点图完整

【标注检查】
□ 尺寸标注齐全、无遗漏
□ 标高标注正确
□ 材质标注完整

【规范合规】
□ 符合防火规范
□ 符合环保标准
□ 无障碍设计符合规范'],
        ARRAY['还要检查什么容易遗漏的', '容易遗漏的施工图检查项：

【细节类】
□ 开关插座高度标注
□ 检修口预留
□ 窗帘盒尺寸
□ 门套线与墙面收口

【专业配合类】
□ 浴霸/排气扇开孔尺寸
□ 热水器排烟管走向
□ 空调内机位置与吊顶关系

【施工可实施类】
□ 砖排版：避免出现小于1/3砖宽
□ 石材排版：纹理方向一致'],

        -- 第15轮：总结
        ARRAY['好的，下周按这个计划来推进', '好的陈哲！我已经完整记录了项目计划和所有讨论要点。

【本周工作要点总结】
1. 精装+景观施工图内审（周一二）
2. 根据内审意见修改图纸（周三）
3. 甲方汇报准备（周四）
4. 正式出图（周五）

我会在关键节点提醒你！
需要我随时帮你查询规范、整理清单、生成文档，随时告诉我！']
    ];

    i INTEGER;
    msg_time TIMESTAMP;
    pair TEXT[];
BEGIN
    FOR i IN 1..array_length(conv_data, 1) LOOP
        pair := conv_data[i];
        msg_time := base_time + (i * INTERVAL '1 day');

        -- 用户消息
        INSERT INTO messages (
            id, event_id, conversation_id, project_id,
            sender_user_id, sender_im_id, sender_name,
            message_type, direction, content,
            attachments, is_at_bot, takeover, status,
            created_at, processed_at
        ) VALUES (
            'msg-chenzhe-' || LPAD(i::TEXT, 3, '0') || '-u',
            'evt-chenzhe-' || LPAD(i::TEXT, 3, '0') || '-u',
            conv_id,
            'project-jingzhuang-2026-001',
            user_id,
            'chenzhe-im-id',
            '陈哲',
            'text',
            'user_to_agent',
            pair[1],
            '[]',
            true,
            false,
            'processed',
            TO_CHAR(msg_time, 'YYYY-MM-DD HH24:MI:SS'),
            TO_CHAR(msg_time + INTERVAL '10 seconds', 'YYYY-MM-DD HH24:MI:SS')
        ) ON CONFLICT (id) DO NOTHING;

        -- Agent回复
        INSERT INTO messages (
            id, event_id, conversation_id, project_id,
            sender_user_id, sender_im_id, sender_name,
            message_type, direction, content,
            attachments, is_at_bot, takeover, status,
            created_at, processed_at
        ) VALUES (
            'msg-chenzhe-' || LPAD(i::TEXT, 3, '0') || '-a',
            'evt-chenzhe-' || LPAD(i::TEXT, 3, '0') || '-a',
            conv_id,
            'project-jingzhuang-2026-001',
            NULL,
            'agent',
            'AI助手',
            'text',
            'agent_to_user',
            pair[2],
            '[]',
            false,
            false,
            'processed',
            TO_CHAR(msg_time + INTERVAL '30 seconds', 'YYYY-MM-DD HH24:MI:SS'),
            TO_CHAR(msg_time + INTERVAL '45 seconds', 'YYYY-MM-DD HH24:MI:SS')
        ) ON CONFLICT (id) DO NOTHING;
    END LOOP;
END $$;

-- ============================================================
-- 7. 创建计划任务模板
-- ============================================================
INSERT INTO plan_task_templates (
    id, template_no, name, description, initiator_id,
    executor_id, project_id, task_type, deadline_rule,
    verification_standard, status, creator_id,
    llm_calculation_failed, llm_failure_notified,
    created_at, updated_at, is_deleted
) VALUES (
    'tpl-jingzhuang-2026-001',
    'TPL-20260706-0001',
    '精装施工图出图',
    '完成精装设计全套施工图（平面+立面+节点+材料表）',
    'chenzhe-jyzx-2026-0001',
    'chenzhe-jyzx-2026-0001',
    'project-jingzhuang-2026-001',
    'ONCE',
    '2026年7月31日前完成',
    '{"图纸完整性": "平面、立面、节点、材料表齐全", "标注规范性": "尺寸、标高、材质标注完整"}',
    'ACTIVE',
    'chenzhe-jyzx-2026-0001',
    false,
    false,
    NOW() AT TIME ZONE 'UTC',
    NOW() AT TIME ZONE 'UTC',
    false
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 8. 创建计划任务实例（挂在精装设计节点下）
-- ============================================================
INSERT INTO plan_task_instances (
    id, instance_no, template_id, period_key, title, description,
    initiator_id, executor_id, project_id, deadline_at, status,
    created_at, updated_at, is_deleted
) VALUES (
    'inst-jingzhuang-2026-001',
    'INST-20260706-0001',
    'tpl-jingzhuang-2026-001',
    '2026-07-31',
    '精装施工图出图',
    '完成滨江悦府精装设计全套施工图，交付成果为景观施工图',
    'chenzhe-jyzx-2026-0001',
    'chenzhe-jyzx-2026-0001',
    'project-jingzhuang-2026-001',
    TO_CHAR(NOW() + INTERVAL '25 days', 'YYYY-MM-DD HH24:MI:SS'),
    'SUBMITTED',
    NOW() AT TIME ZONE 'UTC',
    NOW() AT TIME ZONE 'UTC',
    false
) ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 9. 创建测试文件（5个）
-- ============================================================
INSERT INTO files (
    id, file_no, project_id, filename, file_type, file_ext, file_url,
    file_hash, file_size, uploaded_by, confidentiality,
    responsible_id, version, is_latest, parse_status,
    created_at, updated_at, is_deleted
) VALUES
    (
        'file-jz-001', 'FIL-20260701-0001', 'project-jingzhuang-2026-001',
        '精装施工图-平面布置图.dwg', 'CAD', '.dwg',
        '/app/storage/files/精装施工图-平面布置图.dwg',
        'hash_0001', 102400, 'chenzhe-jyzx-2026-0001',
        1, 'chenzhe-jyzx-2026-0001',
        'V1.0', true, 'pending',
        NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC', false
    ),
    (
        'file-jz-002', 'FIL-20260701-0002', 'project-jingzhuang-2026-001',
        '精装施工图-立面图.dwg', 'CAD', '.dwg',
        '/app/storage/files/精装施工图-立面图.dwg',
        'hash_0002', 204800, 'chenzhe-jyzx-2026-0001',
        1, 'chenzhe-jyzx-2026-0001',
        'V1.0', true, 'pending',
        NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC', false
    ),
    (
        'file-jz-003', 'FIL-20260701-0003', 'project-jingzhuang-2026-001',
        '精装施工图-节点大样图.dwg', 'CAD', '.dwg',
        '/app/storage/files/精装施工图-节点大样图.dwg',
        'hash_0003', 307200, 'chenzhe-jyzx-2026-0001',
        1, 'chenzhe-jyzx-2026-0001',
        'V1.0', true, 'pending',
        NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC', false
    ),
    (
        'file-jz-004', 'FIL-20260701-0004', 'project-jingzhuang-2026-001',
        '材料清单-精装.xlsx', 'Excel', '.xlsx',
        '/app/storage/files/材料清单-精装.xlsx',
        'hash_0004', 51200, 'chenzhe-jyzx-2026-0001',
        1, 'chenzhe-jyzx-2026-0001',
        'V1.0', true, 'pending',
        NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC', false
    ),
    (
        'file-jz-005', 'FIL-20260701-0005', 'project-jingzhuang-2026-001',
        '景观施工图-总平面图.dwg', 'CAD', '.dwg',
        '/app/storage/files/景观施工图-总平面图.dwg',
        'hash_0005', 256000, 'chenzhe-jyzx-2026-0001',
        1, 'chenzhe-jyzx-2026-0001',
        'V1.0', true, 'pending',
        NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC', false
    )
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 10. 将文件关联到精装设计全景节点
-- ============================================================
INSERT INTO node_accessible_files (
    id, node_id, file_id, added_by, added_at
) VALUES
    (
        'naf-jz-001', 'node-jingzhuang-2026-001', 'file-jz-001',
        'chenzhe-jyzx-2026-0001',
        NOW() AT TIME ZONE 'UTC'
    ),
    (
        'naf-jz-002', 'node-jingzhuang-2026-001', 'file-jz-002',
        'chenzhe-jyzx-2026-0001',
        NOW() AT TIME ZONE 'UTC'
    ),
    (
        'naf-jz-003', 'node-jingzhuang-2026-001', 'file-jz-003',
        'chenzhe-jyzx-2026-0001',
        NOW() AT TIME ZONE 'UTC'
    ),
    (
        'naf-jz-004', 'node-jingzhuang-2026-001', 'file-jz-004',
        'chenzhe-jyzx-2026-0001',
        NOW() AT TIME ZONE 'UTC'
    ),
    (
        'naf-jz-005', 'node-jingzhuang-2026-001', 'file-jz-005',
        'chenzhe-jyzx-2026-0001',
        NOW() AT TIME ZONE 'UTC'
    )
ON CONFLICT (id) DO NOTHING;

-- ============================================================
-- 完成提示
-- ============================================================
SELECT '
=====================================================================
✅ 测试环境布置完成！
=====================================================================

创建的数据汇总：

  📦 公司：精装设计研究院（ID: company-jingzhuang-2026-001）

  👤 用户：陈哲（ID: chenzhe-jyzx-2026-0001）
     - 权限级别：3（参建管理 / 主管）
     - 职位：精装设计主管、景观施工图负责人
     - 隶属公司：精装设计研究院

  💬 对话：15轮历史对话（30条消息）
     - 主题：施工图规范、材料选型、节点设计、设计变更流程

  🧠 记忆：用户长期风格偏好记忆已设置
     - 图纸偏好：注重规范与细节
     - 材料偏好：木纹石、金属收边条、艺术涂料
     - 沟通风格：结构化、要点清晰

  🏗️ 项目：滨江悦府精装设计项目

  📍 全景节点：精装设计（NODE-JZ-001）

  📋 计划任务：精装施工图出图
     - 挂在「精装设计」全景节点下
     - 负责人：陈哲
     - 交付成果：景观施工图

  📄 测试文件：5个施工图文件
     - 全部关联到「精装设计」全景节点

=====================================================================
可以使用以下命令测试：
  docker exec emily-core python -m emily_core.session.fetchers.fetch_available_tools --user-id chenzhe-jyzx-2026-0001
=====================================================================
' AS setup_summary;
