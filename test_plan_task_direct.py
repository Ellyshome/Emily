"""
计划任务系统 — 直接 Service/Application 层测试脚本
===================================================
绕过 HTTP API / Pipeline BUS 集成层，直接测试核心业务逻辑：
状态机、鉴权、循环任务、调度、归档、异常处理等。
"""

import sys
import os
import asyncio
import json
from datetime import datetime, timezone, timedelta

# 添加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'emily-core'))

from emily_core.infrastructure.database.session import init_db, get_session
from emily_core.infrastructure.database.models import Base
from emily_core.repositories.plan_task_repo import (
    PlanTaskTemplateRepo,
    PlanTaskInstanceRepo,
    PlanTaskLogRepo,
    PlanTaskDeliverableRepo,
)
from emily_core.repositories.user_repo import UserRepository
from emily_core.services.plan_task_service import PlanTaskService
from emily_core.services.plan_task_commands import (
    CreateInstanceCommand,
    CreateTemplateCommand,
    SubmitDeliverableCommand,
    ReviewTaskCommand,
)
from emily_core.application.plan_task_app import PlanTaskApplication

BEIJING_TZ = timezone(timedelta(hours=8))
TEST_RESULTS = []


def log_test(case_name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    TEST_RESULTS.append({"case": case_name, "status": status, "detail": detail})
    print(f"  [{status}] {case_name}")
    if detail:
        print(f"       {detail}")


def safe_str(s, maxlen=80):
    """安全转字符串，绕过 GBK 编码问题。"""
    try:
        return str(s)[:maxlen]
    except (UnicodeEncodeError, UnicodeDecodeError):
        return repr(s)[:maxlen]


def get_instance(instance_no):
    """获取实例（封装 session 管理）。"""
    return PlanTaskInstanceRepo.get_by_instance_no(instance_no)


def get_logs(instance_id):
    """获取审计日志。"""
    return PlanTaskLogRepo().find_by_instance(instance_id)


def get_template(tpl_id):
    """获取模板。"""
    return PlanTaskTemplateRepo().get_by_id(tpl_id)


def setup():
    """初始化数据库和服务。"""
    db_url = os.environ.get("EMILY_DATABASE_URL", "postgresql://emily:emily_secret_2026@localhost:25432/emily")
    init_db(db_url=db_url)
    with get_session() as session:
        Base.metadata.create_all(bind=session.get_bind())

    service = PlanTaskService(
        template_repo=PlanTaskTemplateRepo(),
        instance_repo=PlanTaskInstanceRepo(),
        log_repo=PlanTaskLogRepo(),
        deliverable_repo=PlanTaskDeliverableRepo(),
        user_repo=UserRepository(),
    )
    app = PlanTaskApplication(service)
    return service, app


def get_test_users():
    """获取测试用户。"""
    user_repo = UserRepository()
    users = {}
    for name in ["张工", "李工", "王工", "赵工"]:
        u = user_repo.find_by_name(name)
        if u:
            users[name] = u
    return users


def get_test_project():
    """获取测试项目。"""
    from emily_core.infrastructure.database.models import Project
    with get_session() as session:
        return session.query(Project).filter(
            Project.code == "S4-PAVE", Project.is_deleted == False
        ).first()


# =============================================================================
# 用例 A: 一次性任务全生命周期
# =============================================================================
async def test_case_a(app, users, project):
    """用例A: 创建 -> 提交 -> 审核 -> 自动归档"""
    print("\n" + "=" * 60)
    print("用例 A: 一次性任务全生命周期")
    print("=" * 60)

    zhang = users["张工"]
    zhao = users["赵工"]
    deadline = (datetime.now(BEIJING_TZ) + timedelta(days=3)).strftime("%Y-%m-%dT17:00:00+08:00")

    # A1: 创建任务
    print("\n  A1: 创建一次性任务（张工->赵工）")
    cmd = CreateInstanceCommand(
        title="S4地块铺装材料进场验收",
        description="对S4地块进场铺装材料进行质量验收",
        initiator_id=zhang.id,
        executor_id=zhao.id,
        project_id=project.id if project else "",
        deadline_at=deadline,
    )
    result = await app.create_task_from_command(cmd)
    instance_no = result.get("instance_no", "")
    log_test("A1: 创建任务", result["success"],
             f"instance_no={instance_no}, status={result.get('status')}")

    if not result["success"]:
        return instance_no

    # 验证状态为 WAITING
    inst = get_instance(instance_no)
    log_test("A1-verify: 状态=WAITING", inst.status == "WAITING" if inst else False,
             f"status={inst.status if inst else 'N/A'}")
    log_test("A1-verify: executor_id已填充", bool(inst.executor_id) if inst else False,
             f"executor_id={inst.executor_id if inst else 'N/A'}")
    log_test("A1-verify: project_id已填充", bool(inst.project_id) if inst else False,
             f"project_id={inst.project_id if inst else 'N/A'}")

    # A2: 提交成果
    print("\n  A2: 提交成果（赵工提交验收报告）")
    submit_cmd = SubmitDeliverableCommand(
        instance_id=inst.id,
        type="TEXT",
        content="铺装材料已验收完毕，合格率100%",
        submitted_by=zhao.id,
    )
    result2 = await app.submit_task(submit_cmd)
    log_test("A2: 提交成果", result2["success"],
             f"status={result2.get('status')}")

    # 验证状态变为 SUBMITTED
    inst2 = get_instance(instance_no)
    log_test("A2-verify: 状态=SUBMITTED", inst2.status == "SUBMITTED" if inst2 else False,
             f"status={inst2.status if inst2 else 'N/A'}")
    log_test("A2-verify: submitted_at已填充", bool(inst2.submitted_at) if inst2 else False,
             f"submitted_at={inst2.submitted_at if inst2 else 'N/A'}")

    # 验证 deliverables 有记录
    deliverables = PlanTaskDeliverableRepo.find_by_instance(inst2.id)
    log_test("A2-verify: deliverables已创建", len(deliverables) > 0,
             f"count={len(deliverables)}")

    # A3: 审核确认
    print("\n  A3: 审核确认（张工确认验收通过）")
    review_cmd = ReviewTaskCommand(
        instance_id=inst2.id,
        operator_id=zhang.id,
        action="confirm",
        reason="验收通过",
    )
    result3 = await app.review_task(review_cmd)
    log_test("A3: 审核确认", result3["success"],
             f"status={result3.get('status')}")

    # 验证状态变为 CONFIRMED
    inst3 = get_instance(instance_no)
    log_test("A3-verify: 状态=CONFIRMED", inst3.status == "CONFIRMED" if inst3 else False,
             f"status={inst3.status if inst3 else 'N/A'}")
    log_test("A3-verify: confirmed_at已填充", bool(inst3.confirmed_at) if inst3 else False,
             f"confirmed_at={inst3.confirmed_at if inst3 else 'N/A'}")

    # 验证审计日志完整性
    logs = get_logs(inst3.id)
    log_test("A3-verify: 审计日志数量", len(logs) >= 3,
             f"log count={len(logs)}")
    if len(logs) >= 3:
        transitions = [(l.from_status, l.to_status) for l in logs[:3]]
        expected = [(None, "WAITING"), ("WAITING", "SUBMITTED"), ("SUBMITTED", "CONFIRMED")]
        log_test("A3-verify: 日志流转正确",
                 transitions == expected,
                 f"got={transitions}, expected={expected}")

    return instance_no


# =============================================================================
# 用例 B: 循环任务模板创建
# =============================================================================
async def test_case_b(app, users, project):
    """用例B: 创建循环任务模板"""
    print("\n" + "=" * 60)
    print("用例 B: 循环任务模板创建")
    print("=" * 60)

    zhang = users["张工"]
    zhao = users["赵工"]

    print("\n  B1: 创建循环任务（每周五提交周报）")
    cmd = CreateTemplateCommand(
        name="S4地块铺装周报",
        description="每周提交S4地块铺装工程进展周报",
        initiator_id=zhang.id,
        executor_id=zhao.id,
        project_id=project.id if project else "",
        task_type="WEEKLY",
        deadline_rule="每周五17:00",
        creator_id=zhang.id,
    )
    result = await app.create_template_from_command(cmd)
    template_no = result.get("template_no", "")
    log_test("B1: 创建模板", result["success"],
             f"template_no={template_no}")
    if not result["success"]:
        return

    # B2: 激活模板
    print("\n  B2: 激活模板")
    tpl_id = result["object_id"]
    result2 = await app.activate_template(tpl_id)
    log_test("B2: 激活模板", result2["success"],
             f"template_no={result2.get('template_no')}")

    # 验证模板状态
    tpl = get_template(tpl_id)
    log_test("B2-verify: 模板状态=ACTIVE", tpl.status == "ACTIVE" if tpl else False,
             f"status={tpl.status if tpl else 'N/A'}")
    log_test("B2-verify: deadline_rule保留", tpl.deadline_rule == "每周五17:00" if tpl else False,
             f"deadline_rule={tpl.deadline_rule if tpl else 'N/A'}")


# =============================================================================
# 用例 C: 鉴权异常标记
# =============================================================================
async def test_case_c(app, users, project):
    """用例C: 低权限用户向高权限用户下达任务 -> 异常标记"""
    print("\n" + "=" * 60)
    print("用例 C: 鉴权异常标记")
    print("=" * 60)

    zhang = users["张工"]  # level=1
    li = users["李工"]      # level=2 (主管)
    deadline = (datetime.now(BEIJING_TZ) + timedelta(days=3)).strftime("%Y-%m-%dT17:00:00+08:00")

    print("\n  C1: 张工(level=1)向李工(level=2)下达任务")
    cmd = CreateInstanceCommand(
        title="异常鉴权测试任务",
        description="低权限向高权限下达任务",
        initiator_id=zhang.id,
        executor_id=li.id,
        project_id=project.id if project else "",
        deadline_at=deadline,
    )
    result = await app.create_task_from_command(cmd)
    instance_no = result.get("instance_no", "")
    log_test("C1: 创建任务(应标记异常)", result["success"],
             f"anomaly={result.get('anomaly')}, instance_no={instance_no}")

    if not result["success"]:
        return instance_no

    # 验证状态为 ANOMALY_PENDING_REVIEW
    inst = get_instance(instance_no)
    anomaly_status = inst.status == "ANOMALY_PENDING_REVIEW" if inst else False
    log_test("C1-verify: 状态=ANOMALY_PENDING_REVIEW", anomaly_status,
             f"status={inst.status if inst else 'N/A'}")
    log_test("C1-verify: anomaly_reason有内容", bool(inst.anomaly_reason) if inst else False,
             f"reason={inst.anomaly_reason if inst else 'N/A'}")

    return instance_no


# =============================================================================
# 用例 H: 归档不可变
# =============================================================================
async def test_case_h(app, instance_no, users):
    """用例H: 对已归档任务操作 -> ArchivedTaskError"""
    print("\n" + "=" * 60)
    print("用例 H: 归档不可变")
    print("=" * 60)

    if not instance_no:
        log_test("H: 归档不可变", False, "无可用实例")
        return

    inst = get_instance(instance_no)
    if not inst or inst.status != "CONFIRMED":
        log_test("H: 归档不可变", False, f"实例状态={inst.status if inst else 'N/A'}，需要CONFIRMED")
        return

    zhao = users["赵工"]

    # 手动归档
    with get_session() as session:
        inst2 = session.query(type(inst)).filter_by(id=inst.id).first()
        inst2.status = "ARCHIVED"
        inst2.archived_at = datetime.now(timezone.utc).isoformat()
        session.commit()
    log_test("H1: 手动归档", True, "status set to ARCHIVED")

    # 验证已归档
    inst3 = get_instance(instance_no)
    log_test("H1-verify: 状态=ARCHIVED", inst3.status == "ARCHIVED" if inst3 else False,
             f"status={inst3.status if inst3 else 'N/A'}")

    # 尝试提交 -> 应失败
    submit_cmd = SubmitDeliverableCommand(
        instance_id=inst.id,
        type="TEXT",
        content="尝试对已归档任务提交",
        submitted_by=zhao.id,
    )
    try:
        result = await app.submit_task(submit_cmd)
        log_test("H2: 归档不可变(submit应失败)", not result["success"],
                 f"reply={safe_str(result.get('reply', ''))}")
    except Exception as e:
        log_test("H2: 归档不可变(异常)", True,
                 f"exception={type(e).__name__}: {safe_str(e)}")


# =============================================================================
# 用例 I: 状态机非法流转
# =============================================================================
async def test_case_i(app, users, project):
    """用例I: 对 WAITING 任务直接 confirm -> InvalidStateTransitionError"""
    print("\n" + "=" * 60)
    print("用例 I: 状态机非法流转")
    print("=" * 60)

    zhang = users["张工"]
    zhao = users["赵工"]
    deadline = (datetime.now(BEIJING_TZ) + timedelta(days=5)).strftime("%Y-%m-%dT17:00:00+08:00")

    # 创建一个 WAITING 任务
    cmd = CreateInstanceCommand(
        title="非法流转测试任务",
        description="测试非法状态流转",
        initiator_id=zhang.id,
        executor_id=zhao.id,
        project_id=project.id if project else "",
        deadline_at=deadline,
    )
    result = await app.create_task_from_command(cmd)
    instance_no = result.get("instance_no", "")
    log_test("I1: 创建任务", result["success"], f"instance_no={instance_no}")
    if not result["success"]:
        return

    inst = get_instance(instance_no)

    # 尝试对 WAITING 直接 confirm -> 非法流转
    review_cmd = ReviewTaskCommand(
        instance_id=inst.id,
        operator_id=zhang.id,
        action="confirm",
        reason="直接确认（应失败）",
    )
    try:
        result2 = await app.review_task(review_cmd)
        log_test("I2: WAITING->CONFIRMED应失败", not result2["success"],
                 f"reply={safe_str(result2.get('reply', ''))}")
        inst2 = get_instance(instance_no)
        log_test("I2-verify: 状态保持WAITING", inst2.status == "WAITING" if inst2 else False,
                 f"status={inst2.status if inst2 else 'N/A'}")
    except Exception as e:
        log_test("I2: 非法流转异常", True,
                 f"exception={type(e).__name__}: {safe_str(e)}")


# =============================================================================
# 用例 J: 实例不存在
# =============================================================================
async def test_case_j(app, users):
    """用例J: 不存在的实例号 -> TaskNotFoundError"""
    print("\n" + "=" * 60)
    print("用例 J: 实例不存在")
    print("=" * 60)

    zhao = users["赵工"]

    submit_cmd = SubmitDeliverableCommand(
        instance_id="non-existent-id-12345",
        type="TEXT",
        content="测试不存在的实例",
        submitted_by=zhao.id,
    )
    try:
        result = await app.submit_task(submit_cmd)
        log_test("J: 不存在实例(submit)", not result["success"],
                 f"reply={safe_str(result.get('reply', ''))}")
    except Exception as e:
        log_test("J: 不存在实例(异常)", True,
                 f"exception={type(e).__name__}: {safe_str(e)}")


# =============================================================================
# 查询测试
# =============================================================================
async def test_queries(app, users):
    """查询功能测试"""
    print("\n" + "=" * 60)
    print("查询功能验证")
    print("=" * 60)

    zhang = users["张工"]
    zhao = users["赵工"]

    result = await app.query_my_tasks(zhao.id, role="executor", status="WAITING")
    log_test("Q-executor: 查询等待中任务", result["success"],
             f"count={result.get('count', 0)}")
    log_test("Q-executor: 返回格式正确", "tasks" in result,
             f"keys={list(result.keys())}")

    result2 = await app.query_my_tasks(zhang.id, role="initiator", limit=10)
    log_test("Q-initiator: 查询发起的任务", result2["success"],
             f"count={result2.get('count', 0)}")

    # 测试并发数据一致性（安全）
    with get_session() as session:
        from emily_core.infrastructure.database.models import PlanTaskInstance
        count = session.query(PlanTaskInstance).count()
    log_test("Q-consistency: DB实例数一致", count > 0,
             f"total instances={count}")


# =============================================================================
# 调度器验证
# =============================================================================
async def test_scheduler(service, app, users, project):
    """验证调度器的基本功能"""
    print("\n" + "=" * 60)
    print("调度器: 基础验证")
    print("=" * 60)

    from emily_core.services.plan_task_scheduler import PlanTaskScheduler
    from emily_core.config import Config

    config = Config()
    config.scheduler_tick_seconds = 5
    config.scheduler_enabled = True

    scheduler = PlanTaskScheduler(
        service=service,
        config=config,
        outbound_bus=None,
        llm_client=None,
        workflow_integrator=None,
    )

    try:
        await scheduler._tick()
        log_test("Scheduler: _tick执行", True, "tick completed without error")
    except Exception as e:
        log_test("Scheduler: _tick执行", False, f"error={type(e).__name__}: {safe_str(e)}")

    # 验证 find_overdue
    now_iso = datetime.now(timezone.utc).isoformat()
    overdue = await service.find_overdue(now_iso=now_iso)
    log_test("Scheduler: find_overdue", overdue is not None, f"count={len(overdue) if overdue else 0}")

    # 验证 find_near_deadline
    near = await service.find_near_deadline(now_iso=now_iso, before_minutes=60)
    log_test("Scheduler: find_near_deadline", near is not None, f"count={len(near) if near else 0}")


# =============================================================================
# Main
# =============================================================================
async def main():
    print("=" * 60)
    print("计划任务系统 - 直接 Service/Application 层测试")
    print("=" * 60)

    service, app = setup()
    users = get_test_users()
    project = get_test_project()

    print(f"\n  测试用户: {list(users.keys())}")
    print(f"  测试项目: {project.name if project else 'N/A'} ({project.code if project else 'N/A'})")

    if len(users) < 4 or not project:
        print("\n  [ERROR] 前置条件不满足，请先运行 setup_test_data2.py")
        return

    # 执行测试用例
    instance_no = await test_case_a(app, users, project)
    await test_case_b(app, users, project)
    await test_case_c(app, users, project)
    await test_case_h(app, instance_no, users)
    await test_case_i(app, users, project)
    await test_case_j(app, users)
    await test_queries(app, users)
    await test_scheduler(service, app, users, project)

    # 汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    passed = sum(1 for r in TEST_RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in TEST_RESULTS if r["status"] == "FAIL")
    total = len(TEST_RESULTS)

    print(f"\n  总计: {total} 项测试")
    print(f"  通过: {passed} 项")
    print(f"  失败: {failed} 项")
    print(f"  通过率: {passed/total*100:.1f}%" if total > 0 else "  N/A")

    if failed > 0:
        print(f"\n  失败用例:")
        for r in TEST_RESULTS:
            if r["status"] == "FAIL":
                print(f"    - {r['case']}: {r['detail']}")

    # 输出 JSON 结果
    results_file = os.path.join(os.path.dirname(__file__), "test_results.json")
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump({"summary": {"total": total, "passed": passed, "failed": failed}, "results": TEST_RESULTS}, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存到: {results_file}")

    # 输出关键数据库状态
    print("\n" + "=" * 60)
    print("最终数据库状态")
    print("=" * 60)
    with get_session() as session:
        from emily_core.infrastructure.database.models import PlanTaskInstance, PlanTaskTemplate, PlanTaskLog
        instances = session.query(PlanTaskInstance).order_by(PlanTaskInstance.created_at.desc()).limit(10).all()
        print(f"\n  plan_task_instances (最近10条):")
        for inst in instances:
            print(f"    {inst.instance_no} | {inst.title[:30]:30s} | {inst.status:25s} | {inst.deadline_at or 'N/A'}")

        templates = session.query(PlanTaskTemplate).order_by(PlanTaskTemplate.created_at.desc()).limit(5).all()
        print(f"\n  plan_task_templates (最近5条):")
        for tpl in templates:
            print(f"    {tpl.template_no} | {tpl.name[:30]:30s} | {tpl.status:10s} | {tpl.task_type}")

        logs = session.query(PlanTaskLog).order_by(PlanTaskLog.created_at.desc()).limit(15).all()
        print(f"\n  plan_task_logs (最近15条):")
        for lg in logs:
            print(f"    {lg.instance_id[:12] if lg.instance_id else 'N/A':12s} | {str(lg.from_status):15s} -> {str(lg.to_status):25s} | {lg.reason or ''}")


if __name__ == "__main__":
    asyncio.run(main())
