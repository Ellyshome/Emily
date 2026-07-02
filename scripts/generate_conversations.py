#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为5个用户每人生成50轮对话记录
"""

import sys
import os
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'emily-core'))

from sqlalchemy import create_engine, text

DB_URL = "postgresql://emily:emily_secret_2026@localhost:25432/emily"

BEIJING_TZ = timezone(timedelta(hours=8))

def beijing_now():
    return datetime.now(BEIJING_TZ)

def beijing_time_str(dt):
    return dt.isoformat()

def generate_conversation_data():
    """生成每个用户的对话内容"""
    
    user_conversations = {
        # 张建国 - 项目总经理 - 关注进度、质量、协调
        "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d": [
            ("user", "今天12号楼的施工进度怎么样了？"),
            ("agent", "张总，12号楼主体结构今天完成第15层浇筑，比计划提前1天。目前整体进度符合节点要求。"),
            ("user", "质量检查情况如何？有没有发现什么问题？"),
            ("agent", "今天上午监理进行了14层墙体垂直度检查，合格率98.5%，发现2处轻微偏差已安排整改。混凝土试块强度检测正常。"),
            ("user", "下周的材料进场计划安排好了吗？"),
            ("agent", "已安排：周一钢筋120吨，周三防水卷材，周五管材。物资部已经确认供应商备货情况。"),
            ("user", "提醒李明华，明天上午9点召开进度协调会"),
            ("agent", "好的张总，已设置提醒：明天上午9点进度协调会，将通知李工参加。"),
            ("user", "这个月的安全文明施工检查结果出来了吗？"),
            ("agent", "本月安全检查评分88分，比上月提升3分。主要问题集中在临边防护和临时用电，已下达整改通知。"),
            ("user", "景观工程的进场时间确定了吗？"),
            ("agent", "景观单位计划7月15日进场，目前正在进行场地移交准备工作。"),
            ("user", "上次监理提出的整改项闭环了吗？"),
            ("agent", "12项整改已完成10项，剩余2项预计明天完成。监理已安排复查。"),
            ("user", "工程款支付进度如何？"),
            ("agent", "本月进度款已提交业主审批，预计周五前可到账。"),
            ("user", "周末安排人员值班了吗？"),
            ("agent", "已安排：周六土建和安全各1人，周日水电和资料各1人，值班表已发布。"),
            ("user", "把这周的进度报表发给我看看"),
            ("agent", "本周进度完成率96%：主体结构15层完成，二次结构开始8层，机电预埋同步跟进。详细报表已生成。"),
            ("user", "和设计院沟通的图纸变更有消息了吗？"),
            ("agent", "设计院已回复，变更图纸预计下周二提供，已通知技术部做好交底准备。"),
            ("user", "现场的扬尘治理怎么样？"),
            ("agent", "围挡喷淋正常运行，PM10平均值65μg/m³，符合环保要求。已安排专人定期维护。"),
            ("user", "下个月的进度计划调整好了吗？"),
            ("agent", "已初步完成：主体结构封顶8月15日，二次结构完成9月30日，砌体施工10月20日。正在与各单位确认。"),
            ("user", "工人住宿情况如何？有没有什么问题？"),
            ("agent", "目前在场工人320人，宿舍区运行正常。上周消防安全检查合格。"),
            ("user", "天气预报说下周有暴雨，做好准备"),
            ("agent", "已收到预警，已安排：基坑排水检查，材料覆盖，临时设施加固。应急队伍24小时待命。"),
            ("user", "材料送检情况怎么样？"),
            ("agent", "本月送检钢筋6批次、混凝土12批次、防水材料2批次，全部合格。"),
            ("user", "每周的监理例会正常召开吗？"),
            ("agent", "是的，每周三下午2点准时召开，会议纪要已及时整理分发。"),
        ],
        
        # 李明华 - 土建工程师 - 关注技术、施工、材料
        "b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e": [
            ("user", "15层的墙柱钢筋验收了吗？"),
            ("agent", "李工，15层墙柱钢筋已于今天上午10点验收通过，监理已签字确认，可以进行下道工序。"),
            ("user", "今天混凝土浇筑预计到几点完成？"),
            ("agent", "根据浇筑速度，预计晚上8点可以完成15层梁板。已安排值班人员全程旁站。"),
            ("user", "下午的技术交底准备好了吗？"),
            ("agent", "已准备好：二次结构砌筑技术交底PPT、节点详图、质量控制要点。下午2点会议室进行。"),
            ("user", "钢筋进场了吗？量够不够？"),
            ("agent", "已进场HRB400钢筋120吨，经送检合格。可满足16-18层施工需求。"),
            ("user", "14层的模板什么时候可以拆？"),
            ("agent", "根据同条件试块强度报告，已达到设计强度85%，明天可以拆模。已通知施工班组。"),
            ("user", "混凝土坍落度测试结果怎么样？"),
            ("agent", "今天共测试6车，坍落度160-180mm，符合要求。已按要求留置试块。"),
            ("user", "砌体材料进场了吗？"),
            ("agent", "加气块已进场200立方，质量证明文件齐全。正在进行取样送检。"),
            ("user", "悬挑脚手架的验收通过了吗？"),
            ("agent", "12-15层悬挑架已于昨天通过验收，同意使用。安全资料已归档。"),
            ("user", "后浇带的处理方案确定了吗？"),
            ("agent", "已确定采用微膨胀混凝土，强度等级提高一级。技术交底已下发施工班组。"),
            ("user", "测量放线的复核结果出来了吗？"),
            ("agent", "15层轴线标高复核完成，偏差在允许范围内。已做好标识保护。"),
            ("user", "今天的旁站记录写了吗？"),
            ("agent", "已完成混凝土浇筑旁站记录，包括进场时间、坍落度、浇筑部位、异常情况等。"),
            ("user", "外墙保温的样板验收了吗？"),
            ("agent", "样板已验收通过，监理确认符合要求。可以开始大面积施工。"),
            ("user", "施工电梯的维护保养做了吗？"),
            ("agent", "已按计划完成月度维保，检测合格。维保记录已存档。"),
            ("user", "雨季施工措施落实了吗？"),
            ("agent", "已落实：基坑排水沟清理、材料覆盖、临时用电防护、应急物资准备到位。"),
            ("user", "今天的隐蔽工程资料签了吗？"),
            ("agent", "15层水电预埋隐蔽资料已由监理签字，资料已归档。"),
            ("user", "下周的材料计划报上去了吗？"),
            ("agent", "已上报：钢筋80吨、水泥50吨、砂200立方。预计周三前可以进场。"),
        ],
        
        # 王晓芳 - 质量监理 - 关注验收、规范、整改
        "c3d4e5f6-a7b8-4c7d-8e1f-2a3b4c5d6e7f": [
            ("user", "今天15层钢筋验收情况怎么样？"),
            ("agent", "王工，15层墙柱钢筋验收基本合格，发现3处箍筋间距偏差，已要求施工方立即整改。整改完成后再进行复查。"),
            ("user", "上次的整改项复查了吗？"),
            ("agent", "14层模板整改项已全部复查合格，同意进行下道工序。整改复查记录已签字确认。"),
            ("user", "混凝土试块的强度报告出来了吗？"),
            ("agent", "13层顶板同条件试块强度达到设计值的92%，符合拆模条件。报告已归档。"),
            ("user", "砌体工程的质量怎么样？有没有发现什么问题？"),
            ("agent", "8层砌体抽检：灰缝厚度基本符合要求，但有2处通缝，已要求返工。拉结筋设置符合规范。"),
            ("user", "防水工程的闭水试验做了吗？"),
            ("agent", "地下室外墙防水闭水试验48小时完成，无渗漏现象。已签署验收记录。"),
            ("user", "进场材料的质量证明文件都齐全吗？"),
            ("agent", "今天进场的防水卷材资料齐全，合格证、检测报告都有。已按要求取样送检。"),
            ("user", "实测实量的数据统计了吗？"),
            ("agent", "本周实测实量：截面尺寸合格率96%，垂直度94%，平整度95%。已形成报表。"),
            ("user", "有没有发现质量通病？"),
            ("agent", "发现3处墙面烂根、2处蜂窝麻面，已下达监理通知单要求整改。"),
            ("user", "节能工程的资料齐全吗？"),
            ("agent", "外墙保温资料基本齐全，缺少一组导热系数检测报告，已要求施工方尽快补充。"),
            ("user", "桩基的检测报告都出来了吗？"),
            ("agent", "200根桩基检测全部完成，Ⅰ类桩196根，Ⅱ类桩4根，符合设计要求。"),
            ("user", "今天的监理日志写好了吗？"),
            ("agent", "已完成：天气晴，温度26-33℃，施工内容15层钢筋绑扎、8层砌体，质量验收情况记录完整。"),
            ("user", "钢筋保护层厚度检测了吗？"),
            ("agent", "14层顶板保护层抽检20点，合格18点，合格率90%。不符合要求的部位已标注处理。"),
            ("user", "钢结构的焊缝检测合格吗？"),
            ("agent", "超声波检测100条焊缝，发现3条不合格，已要求返修重新检测。"),
            ("user", "本月的质量评估报告写了吗？"),
            ("agent", "已完成7月质量评估报告：主体结构质量总体可控，一次验收合格率95%。已提交业主。"),
            ("user", "后浇带的施工质量符合要求吗？"),
            ("agent", "后浇带清理基本干净，但钢筋除锈不够彻底，已要求处理后再进行浇筑。"),
        ],
        
        # 赵伟 - 安全员 - 关注安全、隐患、培训、应急
        "d4e5f6a7-b8c9-4d8e-9f2a-3b4c5d6e7f8a": [
            ("user", "今天的安全巡查有没有发现什么问题？"),
            ("agent", "赵工，今日巡查发现3项问题：1)临边防护2处缺失，2)1个配电箱未上锁，3)2人未系安全带。已下达整改通知。"),
            ("user", "三级安全教育都做了吗？"),
            ("agent", "本周新进场15人，已全部完成三级安全教育并考试合格。教育记录已存档。"),
            ("user", "特种作业人员的证件都在有效期内吗？"),
            ("agent", "塔吊司机2人、信号工3人、电工2人、焊工4人，证件全部有效，已更新人员台账。"),
            ("user", "消防器材的检查做了吗？"),
            ("agent", "月度消防检查已完成：40具灭火器全部有效，消防栓供水正常，消防通道畅通。"),
            ("user", "基坑的监测数据正常吗？"),
            ("agent", "今日基坑监测：水平位移最大2.3mm，沉降1.8mm，均在报警值以内。监测数据正常。"),
            ("user", "昨天的安全隐患整改完了吗？"),
            ("agent", "5项隐患已整改4项，剩余1项临电整改预计今天下午完成。已安排复查。"),
            ("user", "今天的早班会开了吗？"),
            ("agent", "已召开6个班组早班会，内容：高温作业注意事项、临边防护、临时用电安全。签到齐全。"),
            ("user", "塔吊的限位检查了吗？"),
            ("agent", "1#塔吊力矩限位、起重量限制器检查正常，2#塔吊行程限位需调整，已通知维保单位。"),
            ("user", "安全帽的佩戴情况怎么样？"),
            ("agent", "现场抽查30人，28人正确佩戴，2人未系帽带，已现场教育并要求整改。"),
            ("user", "应急物资都齐全吗？"),
            ("agent", "应急物资盘点：急救箱药品齐全，担架2副，防毒面具4套，抽水泵4台，沙袋200袋。已补充过期药品。"),
            ("user", "高处作业的防护措施到位吗？"),
            ("agent", "15层外架防护基本到位，发现1处安全网破损，已立即更换。"),
            ("user", "临时用电的巡检记录做了吗？"),
            ("agent", "已完成每日巡检：接地电阻测试合格，漏电保护器试跳正常，线路绝缘良好。"),
            ("user", "这个月的安全例会开了吗？"),
            ("agent", "已召开7月安全例会，通报本月隐患情况，布置下月安全重点。会议纪要已分发各单位。"),
            ("user", "防暑降温的措施落实了吗？"),
            ("agent", "已落实：现场提供凉茶、防暑药品，调整作业时间（11:30-14:30休息），宿舍空调运行正常。"),
            ("user", "安全资料都更新了吗？"),
            ("agent", "安全资料已更新至最新：安全教育、技术交底、隐患整改、检查记录、特种作业台账全部齐全。"),
        ],
        
        # 陈思雨 - 资料员 - 关注资料、归档、报验、送检
        "e5f6a7b8-c9d0-4e9f-0a3b-4c5d6e7f8a9b": [
            ("user", "15层的钢筋资料报验了吗？"),
            ("agent", "陈姐，15层钢筋报验资料已准备好：钢筋合格证、检测报告、隐蔽记录、检验批。已上报监理待签字。"),
            ("user", "今天的送检计划安排好了吗？"),
            ("agent", "今日送检：钢筋原材1组、钢筋连接3组、混凝土试块6组。已联系检测中心，车辆9点到。"),
            ("user", "监理签字的资料都拿回来了吗？"),
            ("agent", "已取回：14层模板检验批、13层混凝土报验、砌体检验批。已整理归档。"),
            ("user", "这个月的资料都齐了吗？"),
            ("agent", "7月资料：主体结构检验批12份、隐蔽记录8份、材料报验15份、旁站记录6份。基本齐全，还差2份试验报告。"),
            ("user", "竣工资料的目录整理好了吗？"),
            ("agent", "已按档案馆要求整理目录：综合文件28项、质量控制资料126项、质量验收资料45项。正在逐步完善。"),
            ("user", "材料的合格证都收集齐了吗？"),
            ("agent", "已收集：钢筋合格证12份、水泥合格证3份、防水卷材合格证2份、砌块合格证2份。全部齐全。"),
            ("user", "试验报告的台账更新了吗？"),
            ("agent", "已更新：钢筋试验36组、混凝土试验120组、砂浆试验18组、防水材料试验4组。可随时查询。"),
            ("user", "技术交底的资料都签字了吗？"),
            ("agent", "二次结构砌筑、内墙抹灰、外墙保温技术交底已全部签字，交底人和接交人签字齐全。"),
            ("user", "图纸会审的记录整理好了吗？"),
            ("agent", "景观工程图纸会审记录已整理，共提出问题23条，设计院已全部回复。已分发各单位。"),
            ("user", "设计变更的资料都下发了吗？"),
            ("agent", "第003号设计变更（门窗尺寸调整）已复印5份，下发施工、监理、预算、业主。签收记录齐全。"),
            ("user", "今天的施工日志写了吗？"),
            ("agent", "已完成：天气晴，施工内容15层钢筋绑扎、8层砌体、水电预埋。人员、机械、材料情况详细记录。"),
            ("user", "影像资料都归档了吗？"),
            ("agent", "已归档本周照片120张：钢筋验收、混凝土浇筑、安全检查、质量问题及整改。按日期分类存储。"),
            ("user", "分部工程的验收资料准备好了吗？"),
            ("agent", "主体结构分部验收资料已基本就绪：质量控制资料完整、安全和功能检测齐全、观感质量验收记录完成。"),
            ("user", "物资进场的台账更新了吗？"),
            ("agent", "已更新本周进场材料：钢筋120吨、水泥50吨、砂石300立方。进场日期、数量、使用部位全部记录。"),
            ("user", "监理通知单的回复都交了吗？"),
            ("agent", "第021号监理通知单（质量问题）已回复并附整改照片，监理已确认。第022号（安全）正在整改中。"),
        ],
    }
    
    return user_conversations

def generate_50_messages(user_id, base_messages, start_date, user_name):
    """生成50条消息记录"""
    messages = []
    
    # 基础消息扩展到50条
    extended_messages = base_messages.copy()
    
    # 补充消息达到50条
    additional_messages = {
        "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d": [  # 张建国
            ("user", "现场的文明施工情况怎么样？"),
            ("agent", "整体情况良好，材料堆放整齐，施工道路畅通。正在加强扬尘控制。"),
            ("user", "各分包单位的配合情况如何？"),
            ("agent", "土建、水电、消防配合顺畅，每周协调会及时解决交叉作业问题。"),
            ("user", "项目的成本控制情况怎么样？"),
            ("agent", "本月成本在预算范围内，钢筋节约率2.3%，混凝土节约率1.8%。"),
            ("user", "有没有业主提出的新要求？"),
            ("agent", "业主要求加快景观工程进度，争取提前1个月完工。已调整施工计划。"),
            ("user", "下周的工作计划重点是什么？"),
            ("agent", "下周重点：15层主体结构完成，景观单位进场，监理例会准备。"),
        ],
        "b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e": [  # 李明华
            ("user", "楼梯间的施工缝处理好了吗？"),
            ("agent", "已按要求凿毛、清理，刷水泥浆。监理验收合格。"),
            ("user", "施工电梯的附墙安装了吗？"),
            ("agent", "1#施工电梯12层附墙已安装，检测合格，可以正常使用。"),
            ("user", "夜间施工的照明足够吗？"),
            ("agent", "已增加3盏投光灯，现场照明良好，满足施工要求。"),
            ("user", "钢筋的下料单都审核了吗？"),
            ("agent", "16-18层钢筋下料单已审核，无误后下发班组。"),
            ("user", "模板的周转材料还够用吗？"),
            ("agent", "目前木模板周转4次，还可以用2次，计划下周补充新模板。"),
        ],
        "c3d4e5f6-a7b8-4c7d-8e1f-2a3b4c5d6e7f": [  # 王晓芳
            ("user", "抹灰工程的质量怎么样？"),
            ("agent", "8层内墙抹灰抽检，垂直度、平整度合格率93%，个别部位需要修补。"),
            ("user", "门窗的安装质量检查了吗？"),
            ("agent", "外窗框安装已完成，垂直度、对角线偏差在允许范围内。打发泡剂前已清理干净。"),
            ("user", "屋面防水的基层处理合格吗？"),
            ("agent", "屋面基层平整度良好，阴阳角已做圆弧处理。可以进行防水卷材施工。"),
            ("user", "预留洞的封堵质量怎么样？"),
            ("agent", "检查10个预留洞，8个封堵密实，2个有缝隙，已要求返工。"),
            ("user", "预埋件的位置准确吗？"),
            ("agent", "幕墙预埋件抽检20个，19个位置偏差在5mm以内，1个偏差15mm需调整。"),
        ],
        "d4e5f6a7-b8c9-4d8e-9f2a-3b4c5d6e7f8a": [  # 赵伟
            ("user", "施工电梯的防坠试验做了吗？"),
            ("agent", "已完成3个月一次的防坠试验，试验合格，记录已存档。"),
            ("user", "危险品仓库的管理规范吗？"),
            ("agent", "氧气瓶、乙炔瓶分库存放，间距符合要求，消防器材齐全。管理制度已上墙。"),
            ("user", "民工学校的培训开展了吗？"),
            ("agent", "本周开展安全培训2次，参加人数68人，内容：高处作业、临时用电安全。"),
            ("user", "基坑周边的防护栏杆牢固吗？"),
            ("agent", "检查发现2处栏杆松动，已加固。其余防护牢固稳定。"),
            ("user", "工人的宿舍用电安全吗？"),
            ("agent", "宿舍区检查未发现违规用电，私拉乱接现象已杜绝。空调运行正常。"),
        ],
        "e5f6a7b8-c9d0-4e9f-0a3b-4c5d6e7f8a9b": [  # 陈思雨
            ("user", "工程联系单的编号连续吗？"),
            ("agent", "已核对，编号从001到045连续，无跳号、重号。"),
            ("user", "测量资料都签字归档了吗？"),
            ("agent", "基准点复核、楼层放线、沉降观测资料全部齐全，测量人和复核人都已签字。"),
            ("user", "竣工图的绘制进度怎么样？"),
            ("agent", "主体结构竣工图已完成80%，建筑施工图正在同步绘制。"),
            ("user", "资料的电子版都备份了吗？"),
            ("agent", "已备份至云端和移动硬盘，双备份确保安全。每周同步更新一次。"),
            ("user", "监理例会的纪要整理好了吗？"),
            ("agent", "第36期监理例会纪要已整理，共5页。已发微信群和邮箱给各参会单位。"),
        ],
    }
    
    extended_messages.extend(additional_messages.get(user_id, []))
    
    # 如果还不够，继续补充通用对话
    if len(extended_messages) < 50:
        generic_conversations = [
            ("user", "好的，知道了"),
            ("agent", "好的，如有其他问题随时联系。"),
            ("user", "收到"),
            ("agent", "明白，马上处理。"),
            ("user", "谢谢"),
            ("agent", "不客气，这是应该的。"),
            ("user", "好"),
            ("agent", "收到，已记录。"),
            ("user", "行"),
            ("agent", "好的，按计划执行。"),
        ]
        extended_messages.extend(generic_conversations)
    
    # 只取前50条
    final_messages = extended_messages[:50]
    
    # 为每条消息生成时间戳（间隔约10分钟）
    current_time = start_date
    result = []
    
    for i, (direction, content) in enumerate(final_messages):
        message_time = current_time + timedelta(minutes=i * 10)
        result.append({
            "direction": direction,
            "content": content,
            "time": message_time,
        })
    
    return result

def main():
    engine = create_engine(DB_URL)
    
    user_conversations = generate_conversation_data()
    
    users_info = {
        "a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d": {"name": "Zhang Jianguo", "im_id": "zhangjg_001"},
        "b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e": {"name": "Li Minghua", "im_id": "limh_002"},
        "c3d4e5f6-a7b8-4c7d-8e1f-2a3b4c5d6e7f": {"name": "Wang Xiaofang", "im_id": "wangxf_003"},
        "d4e5f6a7-b8c9-4d8e-9f2a-3b4c5d6e7f8a": {"name": "Zhao Wei", "im_id": "zhaow_004"},
        "e5f6a7b8-c9d0-4e9f-0a3b-4c5d6e7f8a9b": {"name": "Chen Siyu", "im_id": "chensy_005"},
    }
    
    project_id = "project_ecocity_26_001"
    start_date = beijing_now() - timedelta(days=7)  # 从7天前开始
    
    with engine.connect() as conn:
        for user_id, user_info in users_info.items():
            print(f"\nGenerating conversation for: {user_info['name']}")
            
            # 1. 创建会话
            conv_id = str(uuid.uuid4())
            conv_sql = text("""
                INSERT INTO conversations (
                    id, im_platform, conversation_type, conversation_id,
                    group_id, title, project_id, takeover_mode, created_at, updated_at
                ) VALUES (
                    :id, 'wechat', 'single', :conv_id,
                    NULL, :title, :project_id, 'collaborate', :created_at, :updated_at
                )
            """)
            conn.execute(conv_sql, {
                "id": conv_id,
                "conv_id": f"single_{user_id}",
                "title": f"{user_info['name']} - Emily Assistant",
                "project_id": project_id,
                "created_at": beijing_time_str(start_date),
                "updated_at": beijing_time_str(start_date),
            })
            
            # 2. 生成消息
            base_messages = user_conversations.get(user_id, [])
            messages = generate_50_messages(user_id, base_messages, start_date, user_info['name'])
            
            for i, msg in enumerate(messages):
                msg_id = str(uuid.uuid4())
                msg_sql = text("""
                    INSERT INTO messages (
                        id, event_id, message_uid, conversation_id, project_id,
                        sender_user_id, sender_im_id, sender_name, message_type,
                        direction, content, attachments, is_at_bot, takeover,
                        status, created_at, processed_at, msg_type
                    ) VALUES (
                        :id, :event_id, :message_uid, :conversation_id, :project_id,
                        :sender_user_id, :sender_im_id, :sender_name, 'text',
                        :direction, :content, '[]', :is_at_bot, false,
                        'processed', :created_at, :processed_at, 1
                    )
                """)
                
                is_user = msg["direction"] == "user"
                conn.execute(msg_sql, {
                    "id": msg_id,
                    "event_id": f"evt_{uuid.uuid4().hex[:12]}",
                    "message_uid": f"msg_{i:04d}",
                    "conversation_id": conv_id,
                    "project_id": project_id,
                    "sender_user_id": user_id if is_user else None,
                    "sender_im_id": user_info['im_id'] if is_user else "emily_bot",
                    "sender_name": user_info['name'] if is_user else "Emily",
                    "direction": "user_to_agent" if is_user else "agent_to_user",
                    "content": msg["content"],
                    "is_at_bot": is_user,
                    "created_at": beijing_time_str(msg["time"]),
                    "processed_at": beijing_time_str(msg["time"] + timedelta(seconds=30)),
                })
            
            print(f"  Created {len(messages)} messages")
        
        conn.commit()
    
    print("\n🎉 All conversations created successfully!")
    print(f"   Total: 5 users × 50 messages = 250 messages")

if __name__ == "__main__":
    main()
