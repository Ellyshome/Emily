"""WorkItemAgent —— 全局单例，异步处理所有 WorkItem（蓝图 §5.3）。

核心设计：不是每个 WorkItem 创建独立 Agent，而是全局唯一 Agent 实例，
异步处理所有 WorkItem。新 WorkItem 进来时，KnowledgeInjector 增量注入
执行该 WorkItem 缺失的知识（SOP/工具/schema），最小化上下文污染。

本期实现：
- WorkItemAgent 是全局单例，持有 KnowledgeInjector 与 4 个 Mock 大脑。
- 提供公共 Pipeline BUS 的 4 个节点 handler（意图+拆分 / 计划+标准 / 执行+验收 / 成果总结）。
- 节点的"大脑"由 Mock 组件提供（MockRouter/MockPlanner/MockWorkAgent/MockGuardian），
  真实 WorkItem-Agent 推理接线属蓝图 §12 Phase B/C。

节点 ↔ Mock 映射（蓝图 §5.4）：
  wi_node1 [意图+拆分]  ← MockRouter.route()
  wi_node2 [计划+标准]  ← MockPlanner.plan()
  wi_node3 [执行+验收]  ← MockWorkAgent.execute() + MockGuardian.review_step()
  wi_node4 [成果总结]    ← 组装 result_text（含 [Mock 模式] 标记）+ MockGuardian.review_reply()
"""

from __future__ import annotations

import logging

from .injector import KnowledgeInjector
from .pipeline.context import BusContext
from .pipeline.mocks import (
    MockRouter,
    MockPlanner,
    MockWorkAgent,
    MockGuardian,
)

logger = logging.getLogger("emily.workitem_agent")


class WorkItemAgent:
    """全局单例 WorkItem-Agent —— 提供公共 BUS 的 4 节点 handler。"""

    def __init__(self, injector: KnowledgeInjector | None = None):
        self.injector = injector or KnowledgeInjector()
        # Mock 大脑（Phase B/C 替换为真实 WorkItem-Agent 推理）
        self._router = MockRouter()
        self._planner = MockPlanner()
        self._work_agent = MockWorkAgent()
        self._guardian = MockGuardian()

    # ── 公共 Pipeline BUS 节点 handler ──

    async def node1_intent(self, context: BusContext) -> None:
        """Node 1 [意图+拆分] —— 确定任务意图，写入 route_decision。"""
        wi = context.work_item
        # 增量灌注（按需加载该 WI 缺失的 SOP/工具/schema）
        self.injector.analyze(wi)

        decision = await self._router.route(wi)
        wi.route_decision = decision
        wi.sop_id = decision.sop_id or wi.sop_id
        wi.llm_call_count += 1
        context.intent = decision
        logger.debug("WI %s node1: sop=%s confidence=%s",
                     wi.id, decision.sop_id, decision.confidence)

    async def node2_plan(self, context: BusContext) -> None:
        """Node 2 [计划+标准] —— 制定执行计划 + 验收标准，写入 execution_plan。"""
        wi = context.work_item
        plan = await self._planner.plan(wi.route_decision, context)
        wi.execution_plan = plan
        wi.risk_level = plan.risk_level
        wi.acceptance_criteria = list(getattr(plan, "acceptance_criteria", []))
        wi.llm_call_count += 1
        logger.debug("WI %s node2: risk=%s steps=%d",
                     wi.id, plan.risk_level, getattr(plan, "estimated_steps", 0))

    async def node3_execute(self, context: BusContext) -> None:
        """Node 3 [执行+验收] —— 执行计划 + 逐步守护验收，写入 step_results。"""
        wi = context.work_item
        if wi.execution_plan is None:
            return
        step_results = await self._work_agent.execute(wi.execution_plan, context)
        criteria = wi.acceptance_criteria
        for sr in step_results:
            # 逐步守护验收（陪跑模式）
            try:
                await self._guardian.review_step(sr, None, criteria)
            except Exception as e:
                logger.warning("WI %s node3 guardian review_step failed: %s", wi.id, e)
            wi.add_step_result(sr)
        wi.llm_call_count += len(step_results)
        if step_results:
            context.agent_result = step_results[-1]
            context.agent_reply = step_results[-1].output
        logger.debug("WI %s node3: %d steps, all_success=%s",
                     wi.id, len(step_results),
                     all(getattr(sr, "success", True) for sr in step_results))

    async def node4_summary(self, context: BusContext) -> None:
        """Node 4 [成果总结] —— 编排人类可读成果 + 出站审核，写入 result_text。"""
        wi = context.work_item
        summary = wi.to_summary()
        steps = summary.get("steps_executed", 0)
        rag_hits = summary.get("rag_hits", 0)
        tool_calls = summary.get("tool_calls", 0)

        if rag_hits > 0:
            rag_texts = []
            for sr in wi.step_results:
                for rag_result in getattr(sr, "rag_results", []):
                    for chunk in getattr(rag_result, "chunks", []):
                        rag_texts.append(f"• {chunk.content}")
            if rag_texts:
                draft = "[Mock 模式] 根据知识库检索，找到以下相关信息：\n\n" + "\n".join(rag_texts[:5])
            else:
                draft = f"[Mock 模式] 已完成知识库查询，共找到 {rag_hits} 条相关信息。"
        elif tool_calls > 0:
            draft = (
                f"[Mock 模式] 操作已完成！共执行 {steps} 个步骤，"
                f"调用 {tool_calls} 个工具，数据库操作 {summary.get('db_operations', 0)} 次。"
            )
        else:
            draft = "[Mock 模式] Emily 已处理完毕。"

        # 出站审核（Mock 始终 PASS）
        try:
            verdict = await self._guardian.review_reply(draft, wi)
            verdict_val = getattr(verdict, "value", "pass")
        except Exception as e:
            logger.warning("WI %s node4 guardian review_reply failed: %s", wi.id, e)
            verdict_val = "pass"

        if verdict_val == "pass":
            wi.result_text = draft
        elif verdict_val == "flag":
            wi.result_text = draft + "\n\n⚠ Mock 守护提醒：建议复核。"
            wi.add_warning("Mock 守护标记")
        else:
            wi.result_text = "[Mock 模式] Emily 已处理完毕。（回复未通过审核，已使用兜底回复）"
            wi.add_warning("Mock 守护拒绝，使用兜底回复")

        wi.llm_call_count += 1
        context.verified_reply = wi.result_text
        logger.debug("WI %s node4: reply_len=%d", wi.id, len(wi.result_text))

    # ── 节点 handler 映射（供 PipelineBUS.build_default 使用）──

    def node_handlers(self) -> dict:
        """返回 4 节点 handler 映射。"""
        return {
            "wi_node1": self.node1_intent,
            "wi_node2": self.node2_plan,
            "wi_node3": self.node3_execute,
            "wi_node4": self.node4_summary,
        }
