"""PersonnelService — 人员/企业读写编排（能力层核心）。

承载需求基线 §五 B/E 的写能力与 §五 A/D 的读能力：
  - 只读：人员台账五列、企业清单（含在职人数）
  - 写：等级调整、企业归属调整、企业新增/删除

授权口径（缺口清单 D6 / D8，PRD R1/R2/R3）在**本服务层**判定：
  - 调整等级：op>=6 可调[1,6]；op∈[3,5] 只能调到低于自身（new<op）；op∈[1,2] 不可
  - 调整归属：op>=4 任意；op==3 仅本企业人员；op∈[1,2] 不可
  - 企业增删：op>=4 可；op∈[1,3] 不可
  fail-closed：取不到操作人等级一律拒绝；被拒也留痕。

只做编排，不碰 SQL（数据访问在 Repository）。
"""

from __future__ import annotations

import json
import logging

from ..infrastructure.logging.audit import (
    RESULT_FAILED,
    RESULT_REJECTED,
    RESULT_SUCCEEDED,
    record_action,
)
from ..repositories.company_repo import CompanyRepository
from ..repositories.user_repo import UserRepository

logger = logging.getLogger("emily.service.personnel")

_AUDIT_CATEGORY = "personnel"


class PersonnelService:
    """人员/企业读写能力。sync 方法，端点/工具侧经 asyncio.to_thread 包裹。"""

    # ── 授权判定 ──

    @staticmethod
    def _can_adjust_level(op_level: int, new_level: int) -> bool:
        if op_level >= 6:
            return 1 <= new_level <= 6
        if op_level >= 3:
            return 1 <= new_level < op_level
        return False

    @staticmethod
    def _can_adjust_company(op_level: int, op_company: str, target_company: str) -> bool:
        if op_level >= 4:
            return True
        if op_level == 3:
            return bool(op_company) and op_company == target_company
        return False

    # ── 留痕（挂载点唯一，非阻断）──

    @staticmethod
    def _audit(action, target_type, target_id, operator_id, result,
               reason="", detail=None) -> None:
        try:
            record_action(
                category=_AUDIT_CATEGORY,
                action=action,
                target_type=target_type,
                target_id=str(target_id or ""),
                actor_id=str(operator_id or ""),
                result=result,
                error_reason=str(reason or ""),
                detail_json=json.dumps(detail or {}, ensure_ascii=False),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("personnel audit failed action=%s: %s", action, e)

    # ── 只读 ──

    @staticmethod
    def list_personnel(limit: int = 2000) -> list[dict]:
        return UserRepository.list_personnel(limit=limit)

    @staticmethod
    def list_companies() -> list[dict]:
        return CompanyRepository.list_companies()

    @staticmethod
    def get_username(user_id: str) -> str | None:
        """解析人员用户名（只读，供记忆读取等展示用，避免路由层直调 repo）。"""
        user = UserRepository.get_by_id(user_id)
        if user is None:
            return None
        return (getattr(user, "username", "") or "").strip() or None

    # ── 写：等级调整 ──

    @staticmethod
    def adjust_user_level(operator_id: str, target_user_id: str,
                          new_level: int, reason: str = "") -> dict:
        if not isinstance(new_level, int) or isinstance(new_level, bool):
            return PersonnelService._reject("invalid_level", "目标等级非法")
        if not 1 <= new_level <= 6:
            return PersonnelService._reject("invalid_level", "目标等级须在 1-6 之间")

        operator = UserRepository.get_by_id(operator_id)
        if operator is None:
            return PersonnelService._reject("permission_denied", "操作人不存在")
        target = UserRepository.get_by_id(target_user_id)
        if target is None:
            return PersonnelService._reject("user_not_found", "目标人员不存在")

        op_level = int(getattr(operator, "level", None) or 0)
        if not PersonnelService._can_adjust_level(op_level, new_level):
            PersonnelService._audit(
                "user_level_updated", "user", target_user_id, operator_id,
                RESULT_REJECTED, "permission_denied",
                {"old_level": getattr(target, "level", None),
                 "new_level": new_level, "reason": reason},
            )
            return PersonnelService._reject("permission_denied", "权限不足：不可将该人员调整至目标等级")

        old_level = getattr(target, "level", None)
        try:
            UserRepository.set_level(target_user_id, new_level)
        except Exception as e:  # noqa: BLE001
            logger.exception("adjust_user_level failed: %s", e)
            PersonnelService._audit(
                "user_level_updated", "user", target_user_id, operator_id,
                RESULT_FAILED, str(e),
                {"old_level": old_level, "new_level": new_level, "reason": reason},
            )
            return PersonnelService._reject("update_failed", "等级调整失败，请稍后重试")

        PersonnelService._audit(
            "user_level_updated", "user", target_user_id, operator_id,
            RESULT_SUCCEEDED, "",
            {"old_level": old_level, "new_level": new_level, "reason": reason},
        )
        return PersonnelService._ok({"target_user_id": target_user_id,
                                     "old_level": old_level, "new_level": new_level})

    # ── 写：企业归属调整 ──

    @staticmethod
    def adjust_user_company(operator_id: str, target_user_id: str,
                            company_id: str, reason: str = "") -> dict:
        operator = UserRepository.get_by_id(operator_id)
        if operator is None:
            return PersonnelService._reject("permission_denied", "操作人不存在")
        target = UserRepository.get_by_id(target_user_id)
        if target is None:
            return PersonnelService._reject("user_not_found", "目标人员不存在")

        op_level = int(getattr(operator, "level", None) or 0)
        op_company = getattr(operator, "company", None) or ""
        target_company = getattr(target, "company", None) or ""
        if not PersonnelService._can_adjust_company(op_level, op_company, target_company):
            PersonnelService._audit(
                "user_company_updated", "user", target_user_id, operator_id,
                RESULT_REJECTED, "permission_denied",
                {"old_company": target_company, "new_company": company_id, "reason": reason},
            )
            return PersonnelService._reject("permission_denied", "权限不足：不可调整该人员所属企业")

        # 非空目标企业须存在且有效；空串表清空归属
        if company_id:
            if CompanyRepository.get_by_id(company_id) is None:
                return PersonnelService._reject("company_not_found", "目标企业不存在或已删除")

        old_company = target_company
        try:
            UserRepository.set_company(target_user_id, company_id or None)
        except Exception as e:  # noqa: BLE001
            logger.exception("adjust_user_company failed: %s", e)
            PersonnelService._audit(
                "user_company_updated", "user", target_user_id, operator_id,
                RESULT_FAILED, str(e),
                {"old_company": old_company, "new_company": company_id, "reason": reason},
            )
            return PersonnelService._reject("update_failed", "企业归属调整失败，请稍后重试")

        PersonnelService._audit(
            "user_company_updated", "user", target_user_id, operator_id,
            RESULT_SUCCEEDED, "",
            {"old_company": old_company, "new_company": company_id, "reason": reason},
        )
        return PersonnelService._ok({"target_user_id": target_user_id,
                                     "old_company": old_company, "new_company": company_id})

    # ── 写：企业新增 ──

    @staticmethod
    def create_company(operator_id: str, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            return PersonnelService._reject("invalid_company_name", "企业名称不能为空")

        operator = UserRepository.get_by_id(operator_id)
        if operator is None:
            return PersonnelService._reject("permission_denied", "操作人不存在")
        op_level = int(getattr(operator, "level", None) or 0)
        if op_level < 4:
            PersonnelService._audit(
                "company_created", "company", "", operator_id,
                RESULT_REJECTED, "permission_denied", {"company_name": name},
            )
            return PersonnelService._reject("permission_denied", "权限不足：仅 L4 及以上可新增企业")

        if CompanyRepository.get_by_name(name) is not None:
            return PersonnelService._reject("duplicate_company_name", "同名有效企业已存在")

        try:
            company = CompanyRepository.create(
                name=name,
                unified_code="",
                project_leader_id=operator_id,
                creator_id=operator_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("create_company failed: %s", e)
            PersonnelService._audit(
                "company_created", "company", "", operator_id,
                RESULT_FAILED, str(e), {"company_name": name},
            )
            return PersonnelService._reject("update_failed", "企业新增失败，请稍后重试")

        PersonnelService._audit(
            "company_created", "company", company.id, operator_id,
            RESULT_SUCCEEDED, "", {"company_name": name},
        )
        return PersonnelService._ok({"company_id": company.id, "company_name": name})

    # ── 写：企业删除 ──

    @staticmethod
    def delete_company(operator_id: str, company_id: str) -> dict:
        operator = UserRepository.get_by_id(operator_id)
        if operator is None:
            return PersonnelService._reject("permission_denied", "操作人不存在")
        op_level = int(getattr(operator, "level", None) or 0)
        if op_level < 4:
            PersonnelService._audit(
                "company_deleted", "company", company_id, operator_id,
                RESULT_REJECTED, "permission_denied", {},
            )
            return PersonnelService._reject("permission_denied", "权限不足：仅 L4 及以上可删除企业")

        company = CompanyRepository.get_by_id(company_id)
        if company is None:
            return PersonnelService._reject("company_not_found", "企业不存在或已删除")

        member_count = CompanyRepository.count_active_members(company_id)
        if member_count > 0:
            PersonnelService._audit(
                "company_deleted", "company", company_id, operator_id,
                RESULT_REJECTED, "company_has_members", {"member_count": member_count},
            )
            return PersonnelService._reject(
                "company_has_members", f"该企业仍有 {member_count} 名在职归属人员，不可删除")

        try:
            CompanyRepository.soft_delete(company_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("delete_company failed: %s", e)
            PersonnelService._audit(
                "company_deleted", "company", company_id, operator_id,
                RESULT_FAILED, str(e), {},
            )
            return PersonnelService._reject("update_failed", "企业删除失败，请稍后重试")

        PersonnelService._audit(
            "company_deleted", "company", company_id, operator_id,
            RESULT_SUCCEEDED, "", {"company_name": getattr(company, "company_name", "")},
        )
        return PersonnelService._ok({"company_id": company_id})

    # ── 返回结构 ──

    @staticmethod
    def _ok(data=None, message="ok") -> dict:
        return {"success": True, "reason_code": "", "message": message, "data": data or {}}

    @staticmethod
    def _reject(reason_code: str, message: str) -> dict:
        return {"success": False, "reason_code": reason_code, "message": message, "data": None}
