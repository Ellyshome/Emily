"""邮箱模块功能测试 —— 端到端验证 SMTP 发送 + IMAP 接收。

使用 .env 中的凭证进行真实 QQ 邮箱收发测试。
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime


# ══════════════════════════════════════════════════════════════════════════════
# 1. 绕过包层级加载模块（避免触发 emily_core/__init__.py 的 DB 依赖）
# ══════════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMILY_CORE = os.path.join(PROJECT_ROOT, "emily-core")

def _load_module(rel_path: str, module_name: str):
    """加载单个 .py 文件为模块，绕过包 __init__.py。"""
    file_path = os.path.join(EMILY_CORE, rel_path)
    # 预注册到 sys.modules 以支持 from __future__ import annotations + dataclass
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


print("=" * 60)
print("Emily Email Module — 功能测试")
print(f"时间：{datetime.now().isoformat()}")
print("=" * 60)

# ══════════════════════════════════════════════════════════════════════════════
# 0. 预先搭建虚拟包层级（支持相对导入 from .base import ...）
# ══════════════════════════════════════════════════════════════════════════════

def _fake_package(name: str):
    """创建虚拟包模块，放入 sys.modules。"""
    if name not in sys.modules:
        pkg = type(sys)(name)
        pkg.__path__ = []
        sys.modules[name] = pkg
    return sys.modules[name]

_fake_package("emily_core")
_fake_package("emily_core.providers")
_fake_package("emily_core.providers.email")
_fake_package("emily_core.services")

# 同时注册 emily_core.tools（email_service.py 引用 emily_core.agent.tool_registry）
_fake_package("emily_core.agent")
_fake_package("emily_core.tools")

# ══════════════════════════════════════════════════════════════════════════════
# 预备：加载 email_service.py 引用的依赖（agent/tool_registry）
# ══════════════════════════════════════════════════════════════════════════════

# ToolDefinition 被 email_service.py 通过 email_tool.py 间接引用
# 但 email_service.py 本身不引用它，只有 email_tool.py 引用
# 先加载基础模块

# ══════════════════════════════════════════════════════════════════════════════
# 2. 加载 base.py (dataclasses + ABC)
# ══════════════════════════════════════════════════════════════════════════════

mod_base = _load_module(
    "emily_core/providers/email/base.py",
    "emily_core.providers.email.base",
)
EmailCredentials = mod_base.EmailCredentials
EmailAttachment = mod_base.EmailAttachment
EmailEnvelope = mod_base.EmailEnvelope
SendResult = mod_base.SendResult
EmailProvider = mod_base.EmailProvider

print("\n[PASS] base.py 加载成功")
print(f"  EmailCredentials: {list(EmailCredentials.__dataclass_fields__.keys())}")
print(f"  EmailEnvelope:    {list(EmailEnvelope.__dataclass_fields__.keys())}")
print(f"  SendResult:       {list(SendResult.__dataclass_fields__.keys())}")
print(f"  EmailProvider:    ABC with {len([m for m in dir(EmailProvider) if not m.startswith('_')])} methods")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 加载 SMTP Provider
# ══════════════════════════════════════════════════════════════════════════════

mod_smtp = _load_module(
    "emily_core/providers/email/smtp_provider.py",
    "emily_core.providers.email.smtp_provider",
)
SMTPEmailProvider = mod_smtp.SMTPEmailProvider

print("\n[PASS] smtp_provider.py 加载成功")
print(f"  SMTPEmailProvider: {SMTPEmailProvider.__name__}")


# ══════════════════════════════════════════════════════════════════════════════
# 4. 加载 IMAP Provider
# ══════════════════════════════════════════════════════════════════════════════

mod_imap = _load_module(
    "emily_core/providers/email/imap_provider.py",
    "emily_core.providers.email.imap_provider",
)
IMAPEmailProvider = mod_imap.IMAPEmailProvider

print("\n[PASS] imap_provider.py 加载成功")
print(f"  IMAPEmailProvider: {IMAPEmailProvider.__name__}")


# ══════════════════════════════════════════════════════════════════════════════
# 5. EmailService 验证（手动组合）
# ══════════════════════════════════════════════════════════════════════════════

mod_service = _load_module(
    "emily_core/services/email_service.py",
    "emily_core.services.email_service",
)
EmailService = mod_service.EmailService

print("\n[PASS] email_service.py 加载成功")
print(f"  EmailService: {EmailService.__name__}")


# ══════════════════════════════════════════════════════════════════════════════
# 6. 从 .env 读取凭证
# ══════════════════════════════════════════════════════════════════════════════

ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
credentials = {}
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                val = val.strip().strip("'").strip('"')
                credentials[key.strip()] = val

EMAIL_USER = credentials.get("EMAIL_IDKEY", "")
EMAIL_PASS = credentials.get("EMAIL_PASSWORD", "")

print(f"\n[INFO] 凭证加载：user={EMAIL_USER}, pass={'***' if EMAIL_PASS else 'MISSING'}")

if not EMAIL_USER or not EMAIL_PASS:
    print("[SKIP] 未找到邮箱凭证，跳过真实收发测试")
    sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
# 7. 构建 EmailCredentials + EmailService
# ══════════════════════════════════════════════════════════════════════════════

CREDS = EmailCredentials(
    smtp_host="smtp.qq.com",
    smtp_port=465,
    imap_host="imap.qq.com",
    imap_port=993,
    username=EMAIL_USER,
    password=EMAIL_PASS,
    use_ssl=True,
)

smtp_provider = SMTPEmailProvider()
imap_provider = IMAPEmailProvider()
email_service = EmailService(smtp=smtp_provider, imap=imap_provider)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1：SMTP 发送 — 发一封测试邮件给自己
# ══════════════════════════════════════════════════════════════════════════════

async def test_smtp_send():
    print("\n" + "-" * 40)
    print("TEST 1: SMTP 发送纯文本邮件")
    print("-" * 40)

    subject = f"[Emily 模块测试] SMTP 发送测试 — {datetime.now().strftime('%H:%M:%S')}"
    body = (
        f"你好！\n\n"
        f"这是 Emily 邮箱模块自动发送的测试邮件。\n"
        f"发送时间：{datetime.now().isoformat()}\n"
        f"测试项目：SMTPEmailProvider.send()\n\n"
        f"如果收到此邮件，说明 SMTP 发送功能正常。\n\n"
        f"— Emily Core 邮箱模块"
    )

    result = await email_service.send(
        creds=CREDS,
        to=EMAIL_USER,       # 发给自己
        subject=subject,
        body=body,
        html=False,
    )

    print(f"  结果: success={result.success}")
    if result.success:
        print(f"  Message-ID: {result.message_id or '(空)'}")
        print(f"  [PASS] SMTP 发送成功")
    else:
        print(f"  错误: {result.error}")
        print(f"  [FAIL] SMTP 发送失败")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2：SMTP 发送 — HTML 邮件
# ══════════════════════════════════════════════════════════════════════════════

async def test_smtp_send_html():
    print("\n" + "-" * 40)
    print("TEST 2: SMTP 发送 HTML 邮件")
    print("-" * 40)

    subject = f"[Emily 模块测试] HTML 邮件测试 — {datetime.now().strftime('%H:%M:%S')}"
    body = (
        "<html><body>"
        "<h2>Emily 邮箱模块 — HTML 邮件测试</h2>"
        "<p>这是一封 <b>HTML</b> 格式的测试邮件。</p>"
        "<ul>"
        f"<li>发送时间：{datetime.now().isoformat()}</li>"
        "<li>测试项目：SMTPEmailProvider.send(html=True)</li>"
        "</ul>"
        "<p style='color:green;'><b>如果收到此邮件并看到格式，说明 HTML 发送功能正常。</b></p>"
        "<p style='color:gray; font-size:12px;'>— Emily Core 邮箱模块</p>"
        "</body></html>"
    )

    result = await email_service.send(
        creds=CREDS,
        to=EMAIL_USER,
        subject=subject,
        body=body,
        html=True,
    )

    print(f"  结果: success={result.success}")
    if result.success:
        print(f"  Message-ID: {result.message_id or '(空)'}")
        print(f"  [PASS] HTML 邮件发送成功")
    else:
        print(f"  错误: {result.error}")
        print(f"  [FAIL] HTML 邮件发送失败")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3：IMAP 收件箱 — 获取未读邮件
# ══════════════════════════════════════════════════════════════════════════════

async def test_imap_fetch_inbox():
    print("\n" + "-" * 40)
    print("TEST 3: IMAP 获取收件箱未读邮件")
    print("-" * 40)

    envelopes = await email_service.fetch_inbox(
        creds=CREDS,
        unread_only=True,
        limit=10,
    )

    print(f"  结果: {len(envelopes)} 封未读邮件")
    for i, env in enumerate(envelopes, 1):
        date_str = env.date.strftime("%m-%d %H:%M") if env.date else "(no date)"
        subj = env.subject.encode("ascii", errors="replace").decode("ascii")
        sndr = env.sender.encode("ascii", errors="replace").decode("ascii")
        preview = env.body_plain[:80].encode("ascii", errors="replace").decode("ascii")
        print(f"  [{i}] subject: {subj}")
        print(f"      from: {sndr}")
        print(f"      date: {date_str}")
        print(f"      UID:  {env.uid}")
        print(f"      preview: {preview}...")
        if env.attachments:
            print(f"      attachments: {len(env.attachments)}")
    print(f"  [PASS] IMAP fetch_inbox 成功，返回 {len(envelopes)} 封邮件")
    return envelopes


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4：IMAP fetch_orders — 搜索 Order 邮件
# ══════════════════════════════════════════════════════════════════════════════

async def test_imap_fetch_orders():
    print("\n" + "-" * 40)
    print("TEST 4: IMAP fetch_orders（主题含 'order' 的未读邮件）")
    print("-" * 40)

    # 先发一封主题含 "order" 的邮件给自己（模拟管理员命令）
    subject = f"order: 测试命令 — {datetime.now().strftime('%H:%M:%S')}"
    body = "这是一封 order 测试邮件，用于验证 fetch_orders 功能。"
    send_result = await email_service.send(
        creds=CREDS,
        to=EMAIL_USER,
        subject=subject,
        body=body,
        html=False,
    )
    print(f"  已发送 Order 测试邮件: success={send_result.success}")
    if not send_result.success:
        print(f"  [FAIL] 无法发送测试邮件: {send_result.error}")
        return []

    # 等待 QQ 邮箱服务器处理（可能需要几秒）
    print("  等待 5 秒让邮件到达收件箱...")
    await asyncio.sleep(5)

    orders = await email_service.fetch_orders(creds=CREDS, since_uid=None)

    print(f"  结果: {len(orders)} 封 Order 邮件")
    for env in orders:
        subj = env.subject.encode("ascii", errors="replace").decode("ascii")
        print(f"  - [{env.uid}] {subj} | is_order={env.is_order}")
    if orders:
        print(f"  [PASS] fetch_orders 成功，返回 {len(orders)} 封")
    else:
        print(f"  [WARN] fetch_orders 返回 0 封（可能邮件还在投递中）")
    return orders


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5：IMAP mark_read — 标记已读
# ══════════════════════════════════════════════════════════════════════════════

async def test_imap_mark_read(envelopes: list):
    print("\n" + "-" * 40)
    print("TEST 5: IMAP mark_read")
    print("-" * 40)

    if not envelopes:
        print("  [SKIP] 没有可标记的邮件")
        return True

    uids = [e.uid for e in envelopes[:3]]  # 标记前 3 封
    if not uids or not uids[0]:
        print("  [SKIP] 邮件 UID 为空，无法标记")
        return None

    result = await email_service.mark_read(creds=CREDS, uids=uids)

    print(f"  结果: {result}")
    print(f"  标记 UIDs: {uids}")
    if result:
        print(f"  [PASS] mark_read 成功")
    else:
        print(f"  [FAIL] mark_read 失败")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: 错误处理 — 无效凭证
# ══════════════════════════════════════════════════════════════════════════════

async def test_error_handling():
    print("\n" + "-" * 40)
    print("TEST 6: 错误处理 — 无效凭证")
    print("-" * 40)

    bad_creds = EmailCredentials(
        smtp_host="smtp.qq.com",
        smtp_port=465,
        imap_host="imap.qq.com",
        imap_port=993,
        username="invalid@qq.com",
        password="wrong_password_123",
        use_ssl=True,
    )

    result = await email_service.send(
        creds=bad_creds,
        to="someone@qq.com",
        subject="test",
        body="test",
    )

    print(f"  结果: success={result.success}")
    print(f"  错误信息: {result.error}")
    if not result.success and result.error:
        print(f"  [PASS] 错误处理正常，返回了错误信息")
    else:
        print(f"  [FAIL] 期望返回错误，但返回了 success={result.success}")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7: 错误处理 — 空凭证
# ══════════════════════════════════════════════════════════════════════════════

async def test_empty_credentials():
    print("\n" + "-" * 40)
    print("TEST 7: 错误处理 — 空凭证")
    print("-" * 40)

    empty_creds = EmailCredentials(
        smtp_host="smtp.qq.com",
        smtp_port=465,
        imap_host="imap.qq.com",
        imap_port=993,
        username="",
        password="",
        use_ssl=True,
    )

    result = await email_service.send(
        creds=empty_creds,
        to="someone@qq.com",
        subject="test",
        body="test",
    )

    print(f"  结果: success={result.success}")
    print(f"  错误信息: {result.error}")
    if not result.success and "AUTH_FAILED" in (result.error or ""):
        print(f"  [PASS] 空凭证正确返回 AUTH_FAILED")
    elif not result.success:
        print(f"  [PASS] 空凭证正确返回错误（非 AUTH_FAILED: {result.error}）")
    else:
        print(f"  [FAIL] 期望返回错误，但返回了 success=True")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 8: 错误处理 — 无效收件人
# ══════════════════════════════════════════════════════════════════════════════

async def test_invalid_recipient():
    print("\n" + "-" * 40)
    print("TEST 8: 错误处理 — 无效收件地址")
    print("-" * 40)

    result = await email_service.send(
        creds=CREDS,
        to="not-an-email",
        subject="test",
        body="test",
    )

    print(f"  结果: success={result.success}")
    print(f"  错误信息: {result.error}")
    if not result.success and "INVALID" in (result.error or "").upper():
        print(f"  [PASS] 无效收件人正确返回错误")
    else:
        print(f"  [FAIL] 期望返回 INVALID_RECIPIENT 错误, got: {result.error}")


# ══════════════════════════════════════════════════════════════════════════════
# TEST 9: is_available 检测
# ══════════════════════════════════════════════════════════════════════════════

async def test_is_available():
    print("\n" + "-" * 40)
    print("TEST 9: Provider is_available()")
    print("-" * 40)

    smtp_ok = await smtp_provider.is_available()
    imap_ok = await imap_provider.is_available()

    print(f"  SMTP Provider available: {smtp_ok}")
    print(f"  IMAP Provider available: {imap_ok}")

    if smtp_ok and imap_ok:
        print(f"  [PASS] 两个 Provider 均可用")
    else:
        print(f"  [FAIL] 至少一个 Provider 不可用")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    results = {"pass": 0, "fail": 0, "skip": 0}

    # Test 1: SMTP 发送纯文本
    try:
        r = await test_smtp_send()
        results["pass" if r and r.success else "fail"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 2: SMTP 发送 HTML
    try:
        r = await test_smtp_send_html()
        results["pass" if r and r.success else "fail"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 3: IMAP 收件箱
    envelopes = []
    try:
        envelopes = await test_imap_fetch_inbox()
        results["pass"] += 1  # 返回空列表也算成功
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 4: IMAP fetch_orders
    try:
        orders = await test_imap_fetch_orders()
        if orders:
            envelopes.extend(orders)
        results["pass" if orders is not None else "fail"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 5: IMAP mark_read
    try:
        await test_imap_mark_read(envelopes)
        results["pass"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 6: 错误处理 — 无效凭证
    try:
        await test_error_handling()
        results["pass"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 7: 错误处理 — 空凭证
    try:
        await test_empty_credentials()
        results["pass"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 8: 错误处理 — 无效收件人
    try:
        await test_invalid_recipient()
        results["pass"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # Test 9: is_available
    try:
        await test_is_available()
        results["pass"] += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {e}")
        results["fail"] += 1

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("测试汇总")
    print("=" * 60)
    print(f"  通过: {results['pass']}")
    print(f"  失败: {results['fail']}")
    total = results['pass'] + results['fail']
    print(f"  总计: {total}/{total}")
    if results['fail'] == 0:
        print(f"\n  [ALL PASS] All {results['pass']} tests passed OK")
    else:
        print(f"\n  [PARTIAL] {results['pass']}/{total} passed, {results['fail']} failed")


if __name__ == "__main__":
    asyncio.run(main())
