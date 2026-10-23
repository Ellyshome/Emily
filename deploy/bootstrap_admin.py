#!/usr/bin/env python3
"""bootstrap_admin.py —— 创建/更新 Emily 的首个管理员账号。

为什么需要：
    生产新库里 users 表为空，而 IM 绑定服务在 auto_create_user=False 时不会自动建号，
    没有账号就没人能用 Emily。本脚本提供最小引导：建一个高等级用户，并把微信标识
    （users.wechat）预先写进去——绑定服务对「档案已登记 wechat 但未建绑定行」的用户
    会自动补建绑定（user_binding_service 的通道账号兜底），因此无需手工插绑定表。

用法（在仓库根目录，容器内执行）：

    # 查看现有账号
    docker compose -f docker-compose.server.yml exec -T emily-core \
        python /app/deploy/bootstrap_admin.py --list

    # 创建/更新管理员（--wechat 填小程序上报的用户标识）
    docker compose -f docker-compose.server.yml exec -T emily-core \
        python /app/deploy/bootstrap_admin.py --username 管理员 --level 6 --wechat wxid_xxx

等级口径（与配置清单一致）：1=访客 2=参建执行 3=参建管理 4=建设主管 5=管理员 6=系统管理员。
level >= 5 时自动置 is_admin=True。
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

# 容器内代码根为 /app（Dockerfile COPY emily_core/ ./emily_core/）
sys.path.insert(0, os.environ.get("EMILY_APP_ROOT", "/app"))


def _list_users() -> int:
    from emily_core.infrastructure.database.models import User
    from emily_core.infrastructure.database.session import get_session

    with get_session() as s:
        rows = (
            s.query(User)
            .filter(User.is_deleted == False)  # noqa: E712
            .order_by(User.level.desc(), User.created_at)
            .all()
        )
        if not rows:
            print("（users 表为空）")
            return 0
        print(f"{'ID':38} {'LV':>3} {'ADMIN':5} {'STATUS':8} USERNAME / WECHAT")
        for u in rows:
            print(f"{u.id:38} {u.level:>3} {str(bool(u.is_admin)):5} {u.status:8} "
                  f"{u.username} / {u.wechat or '-'}")
    return 0


def _upsert(args) -> int:
    from emily_core.infrastructure.database.models import User
    from emily_core.infrastructure.database.session import get_session

    with get_session() as s:
        user = (
            s.query(User)
            .filter(User.username == args.username, User.is_deleted == False)  # noqa: E712
            .first()
        )
        if user is None and args.wechat:
            user = (
                s.query(User)
                .filter(User.wechat == args.wechat, User.is_deleted == False)  # noqa: E712
                .first()
            )

        if user is None:
            uid = str(uuid.uuid4())
            user = User(
                id=uid,
                username=args.username,
                phone=args.phone,
                email=args.email,
                wechat=args.wechat,
                status="active",
                is_admin=args.level >= 5,
                level=args.level,
                # creator_id 为 NOT NULL；首个账号自引用，后续账号可指向它
                creator_id=uid if not args.creator_id else args.creator_id,
                perm_list="[]",
                position="[]",
            )
            s.add(user)
            s.flush()
            print(f"已创建：{user.username}  id={user.id}  level={user.level}  "
                  f"wechat={user.wechat or '-'}")
        else:
            user.level = args.level
            user.is_admin = args.level >= 5
            if args.wechat:
                user.wechat = args.wechat
            if args.phone:
                user.phone = args.phone
            if args.email:
                user.email = args.email
            print(f"已更新：{user.username}  id={user.id}  level={user.level}  "
                  f"wechat={user.wechat or '-'}")

        if not args.wechat:
            print("提示：未指定 --wechat，该账号暂无 IM 身份，小程序消息仍会被拒绝；"
                  "可稍后带 --wechat 重跑本脚本补绑。")
        s.commit()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="创建/更新 Emily 首个管理员")
    p.add_argument("--list", action="store_true", help="列出全部账号后退出")
    p.add_argument("--username", default="", help="账号显示名（唯一匹配依据）")
    p.add_argument("--level", type=int, default=6, choices=range(1, 7),
                   help="权限等级 1-6（默认 6=系统管理员）")
    p.add_argument("--wechat", default="", help="微信侧用户标识（写入 users.wechat，用于自动绑定）")
    p.add_argument("--phone", default="", help="手机号（可选）")
    p.add_argument("--email", default="", help="邮箱（可选）")
    p.add_argument("--creator-id", default="", help="创建人 ID（可选，默认自引用）")
    args = p.parse_args()

    if args.list:
        return _list_users()
    if not args.username:
        print("缺少 --username（或用 --list 查看现有账号）", file=sys.stderr)
        return 2
    return _upsert(args)


if __name__ == "__main__":
    sys.exit(main())
